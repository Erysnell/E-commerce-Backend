from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import CartItem, Category, Order, Product
from .serializers import (
    CartItemAddSerializer,
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
from .services import cart_service, checkout_service, payment_service
from .services.exceptions import (
    CartNotFoundError,
    EmptyCartError,
    OrderNotFoundError,
    OutOfStockError,
    PaymentError,
    ProductNotFoundError,
)

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
        return cart_service.get_or_create_cart(self.request.user)


class CartItemCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=['Cart'], summary="Add a product to the cart")
    def post(self, request):
        input_serializer = CartItemAddSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            cart_item = cart_service.add_item(
                user=request.user,
                product_id=input_serializer.validated_data['product'],
                quantity=input_serializer.validated_data['quantity'],
            )
        except ProductNotFoundError:
            return Response({"error": "Product not found"}, status=status.HTTP_404_NOT_FOUND)
        except OutOfStockError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = CartItemSerializer(cart_item)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CartItemDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=['Cart'], summary="Remove a product from the cart")
    def delete(self, request, item_id):
        try:
            cart_service.remove_item(user=request.user, item_id=item_id)
        except CartItem.DoesNotExist:
            return Response(
                {"error": "Item not found in your cart"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------
# Checkout & Payments
# ---------------------------------------------------------

class CheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=['Payments'], summary="Initialize checkout and create Stripe PaymentIntent")
    def post(self, request):
        input_serializer = CheckoutSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            result = checkout_service.checkout(
                user=request.user,
                shipping_address=input_serializer.validated_data.get('shipping_address', ''),
            )
        except CartNotFoundError:
            return Response({"error": "Cart not found"}, status=status.HTTP_404_NOT_FOUND)
        except EmptyCartError:
            return Response({"error": "Cart is empty"}, status=status.HTTP_400_BAD_REQUEST)
        except OutOfStockError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PaymentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'order_id': result.order.id,
            'client_secret': result.client_secret,
            'total_amount': str(result.order.total_amount),
        }, status=status.HTTP_201_CREATED)


class PaymentWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=['Payments'], summary="Payment gateway webhook listener")
    def post(self, request):
        signature = request.META.get('HTTP_STRIPE_SIGNATURE', '')
        try:
            payment_service.process_webhook(request.body, signature)
        except PaymentError:
            return HttpResponse(status=400)
        except OrderNotFoundError:
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
