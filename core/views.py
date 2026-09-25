import stripe
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import Cart, CartItem, Category, Order, OrderItem, Product
from .serializers import (
    CartItemSerializer,
    CartSerializer,
    CategorySerializer,
    CheckoutSerializer,
    CustomTokenObtainPairSerializer,
    OrderSerializer,
    ProductSerializer,
    UserRegistrationSerializer,
    UserSerializer,
)

stripe.api_key = settings.STRIPE_SECRET_KEY

# ---------------------------------------------------------
# Authentication
# ---------------------------------------------------------

class RegisterView(generics.CreateAPIView):
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=['Authentication'], summary="Register a new user")
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    @extend_schema(tags=['Authentication'], summary="Obtain JWT access and refresh tokens")
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class CustomTokenRefreshView(TokenRefreshView):
    @extend_schema(tags=['Authentication'], summary="Refresh JWT access token")
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

    @extend_schema(tags=['Authentication'], summary="Get current user profile")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# ---------------------------------------------------------
# Categories & Products
# ---------------------------------------------------------

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    lookup_field = 'slug'

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer
    lookup_field = 'slug'
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        search = params.get('search')
        if search:
            queryset = queryset.filter(name__icontains=search) | queryset.filter(description__icontains=search)

        category = params.get('category')
        if category:
            queryset = queryset.filter(category__slug=category)

        min_price = params.get('min_price')
        if min_price:
            queryset = queryset.filter(price__gte=min_price)

        max_price = params.get('max_price')
        if max_price:
            queryset = queryset.filter(price__lte=max_price)

        # If user is staff, include inactive products in admin views
        if self.request.user.is_staff and params.get('all') == 'true':
            queryset = Product.objects.all()

        return queryset.distinct()

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticatedOrReadOnly()]


# ---------------------------------------------------------
# Shopping Cart
# ---------------------------------------------------------

class CartView(generics.RetrieveAPIView):
    serializer_class = CartSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        cart, _ = Cart.objects.get_or_create(user=self.request.user)
        return cart


class CartItemCreateView(generics.CreateAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=['Cart'], summary="Add a product to the cart")
    def post(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(user=request.user)
        product_id = request.data.get('product')
        quantity = int(request.data.get('quantity', 1))

        if not product_id:
            return Response({"error": "Product ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            product = Product.objects.get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            return Response({"error": "Product not found"}, status=status.HTTP_404_NOT_FOUND)

        if product.stock < quantity:
            return Response({"error": "Not enough stock"}, status=status.HTTP_400_BAD_REQUEST)

        cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product)
        if not created:
            cart_item.quantity += quantity
        else:
            cart_item.quantity = quantity

        if cart_item.quantity > product.stock:
            return Response({"error": "Not enough stock"}, status=status.HTTP_400_BAD_REQUEST)

        cart_item.save()
        serializer = CartItemSerializer(cart_item)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CartItemDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=['Cart'], summary="Remove a product from the cart")
    def delete(self, request, item_id):
        try:
            cart_item = CartItem.objects.get(id=item_id, cart__user=request.user)
        except CartItem.DoesNotExist:
            return Response({"error": "Item not found in your cart"}, status=status.HTTP_404_NOT_FOUND)
        
        cart_item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------
# Checkout & Payments (Stripe)
# ---------------------------------------------------------

class CheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=['Payments'], summary="Initialize checkout and create Stripe PaymentIntent")
    def post(self, request):
        try:
            cart = Cart.objects.get(user=request.user)
        except Cart.DoesNotExist:
            return Response({"error": "Cart not found"}, status=status.HTTP_404_NOT_FOUND)

        if not cart.items.exists():
            return Response({"error": "Cart is empty"}, status=status.HTTP_400_BAD_REQUEST)

        # Check stock availability
        for item in cart.items.all():
            if item.product.stock < item.quantity:
                return Response(
                    {"error": f"Not enough stock for {item.product.name}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shipping_address = serializer.validated_data.get('shipping_address', '')

        # Calculate total
        total_amount = sum(item.product.price * item.quantity for item in cart.items.all())

        # Create Stripe PaymentIntent
        try:
            intent = stripe.PaymentIntent.create(
                amount=int(total_amount * 100),  # Stripe uses cents
                currency='usd',
                metadata={'user_id': request.user.id, 'cart_id': cart.id},
                automatic_payment_methods={'enabled': True},
            )
        except stripe.error.StripeError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Create Order in Database
        with transaction.atomic():
            order = Order.objects.create(
                user=request.user,
                total_amount=total_amount,
                status=Order.Status.PENDING,
                stripe_payment_intent_id=intent.id,
                shipping_address=shipping_address,
            )

            for item in cart.items.all():
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    product_name=item.product.name,
                    price=item.product.price,
                    quantity=item.quantity,
                )

        return Response({
            'order_id': order.id,
            'client_secret': intent.client_secret,
            'total_amount': str(total_amount),
        }, status=status.HTTP_201_CREATED)


class StripeWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=['Payments'], summary="Stripe Webhook listener")
    def post(self, request):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
        event = None

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except ValueError:
            return HttpResponse(status=400)
        except stripe.error.SignatureVerificationError:
            return HttpResponse(status=400)

        if event['type'] == 'payment_intent.succeeded':
            payment_intent = event['data']['object']
            payment_intent_id = payment_intent.get('id')
            
            try:
                order = Order.objects.get(stripe_payment_intent_id=payment_intent_id)
                # Update Order status to PAID
                order.status = Order.Status.PAID
                order.save()

                # Reduce stock
                with transaction.atomic():
                    for item in order.items.all():
                        product = item.product
                        if product:
                            product.stock -= item.quantity
                            product.save()

                # Clear user's cart
                Cart.objects.filter(user=order.user).delete()

            except Order.DoesNotExist:
                return HttpResponse(status=404)

        elif event['type'] == 'payment_intent.payment_failed':
            payment_intent = event['data']['object']
            payment_intent_id = payment_intent.get('id')
            
            try:
                order = Order.objects.get(stripe_payment_intent_id=payment_intent_id)
                order.status = Order.Status.FAILED
                order.save()
            except Order.DoesNotExist:
                return HttpResponse(status=404)

        return HttpResponse(status=200)


# ---------------------------------------------------------
# Orders
# ---------------------------------------------------------

class OrderListView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=['Payments'], summary="List user's order history")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user)
