from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from .gateways import (
    PaymentEvent,
    PaymentEventType,
    PaymentGateway,
    PaymentIntentResult,
    get_payment_gateway,
)
from .gateways.stripe_gateway import StripeGateway
from .models import Cart, Category, Order, Product
from .services import cart_service, checkout_service, payment_service
from .services.exceptions import (
    CartNotFoundError,
    EmptyCartError,
    OrderNotFoundError,
    OutOfStockError,
    PaymentError,
    ProductNotFoundError,
)

User = get_user_model()


class FakePaymentGateway(PaymentGateway):
    """Hand-rolled test double: exercises the Strategy contract without Stripe."""

    def __init__(self, *, fail=False, event=None, accept_signature="valid_sig"):
        self.fail = fail
        self.event = event
        self.accept_signature = accept_signature
        self.intents = []

    def create_intent(self, *, amount, currency, metadata=None):
        if self.fail:
            raise PaymentError("Payment gateway unavailable")
        self.intents.append(
            {"amount": amount, "currency": currency, "metadata": metadata}
        )
        return PaymentIntentResult(
            intent_id="pi_fake",
            client_secret="cs_fake",
            amount=amount,
            currency=currency,
        )

    def parse_webhook_event(self, payload, signature):
        if signature != self.accept_signature:
            raise PaymentError("Invalid webhook signature")
        return self.event


class AuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.register_url = reverse('register')
        self.login_url = reverse('token_obtain_pair')

    def test_user_registration(self):
        data = {
            'username': 'testuser',
            'email': 'test@example.com',
            'password': 'securepassword123',
            'first_name': 'Test',
            'last_name': 'User'
        }
        response = self.client.post(self.register_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(email='test@example.com').exists())

    def test_user_login(self):
        User.objects.create_user(username='loginuser', email='login@example.com', password='password123')
        data = {'email': 'login@example.com', 'password': 'password123'}
        response = self.client.post(self.login_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)


class ProductTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name='Electronics', slug='electronics')
        self.product = Product.objects.create(
            name='Laptop',
            description='A powerful laptop',
            price=Decimal('999.99'),
            stock=10,
            category=self.category,
            slug='laptop'
        )
        self.list_url = reverse('product-list')

    def test_product_list_public(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

    def test_product_search(self):
        response = self.client.get(self.list_url + '?search=Laptop')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        
        response = self.client.get(self.list_url + '?search=Phone')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)


class CartTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='cartuser', email='cart@example.com', password='password123')
        self.client.force_authenticate(user=self.user)
        
        self.category = Category.objects.create(name='Books', slug='books')
        self.product = Product.objects.create(
            name='Django Book',
            description='Learn Django',
            price=Decimal('29.99'),
            stock=5,
            category=self.category
        )
        
        self.cart_url = reverse('cart')
        self.add_url = reverse('cart_add')

    def test_add_to_cart(self):
        data = {'product': self.product.id, 'quantity': 1}
        response = self.client.post(self.add_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify cart has the item
        response = self.client.get(self.cart_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['items']), 1)

    def test_add_to_cart_stock_limit(self):
        data = {'product': self.product.id, 'quantity': 10}
        response = self.client.post(self.add_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CartServiceTests(TestCase):
    """Business rules of the cart, tested below the HTTP layer."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='svcuser', email='svc@example.com', password='password123'
        )
        self.category = Category.objects.create(name='Toys', slug='toys')
        self.product = Product.objects.create(
            name='Robot', description='Toy robot', price=Decimal('15.00'),
            stock=3, category=self.category,
        )

    def test_get_or_create_cart_is_idempotent(self):
        cart1 = cart_service.get_or_create_cart(self.user)
        cart2 = cart_service.get_or_create_cart(self.user)
        self.assertEqual(cart1.id, cart2.id)

    def test_add_item_merges_quantity(self):
        cart_service.add_item(user=self.user, product_id=self.product.id, quantity=1)
        item = cart_service.add_item(user=self.user, product_id=self.product.id, quantity=2)
        self.assertEqual(item.quantity, 3)

    def test_add_item_unknown_product_raises(self):
        with self.assertRaises(ProductNotFoundError):
            cart_service.add_item(user=self.user, product_id=99999)

    def test_add_item_beyond_stock_raises(self):
        with self.assertRaises(OutOfStockError):
            cart_service.add_item(user=self.user, product_id=self.product.id, quantity=4)

    def test_remove_item(self):
        item = cart_service.add_item(user=self.user, product_id=self.product.id, quantity=1)
        cart_service.remove_item(user=self.user, item_id=item.id)
        self.assertFalse(Cart.objects.get(user=self.user).items.exists())


class CheckoutServiceTests(TestCase):
    """Checkout orchestration driven by an injected gateway (Strategy)."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='chkuser', email='chk@example.com', password='password123'
        )
        self.category = Category.objects.create(name='Media', slug='media')
        self.product = Product.objects.create(
            name='Vinyl', description='Record', price=Decimal('20.00'),
            stock=5, category=self.category,
        )

    def test_checkout_missing_cart_raises(self):
        gateway = FakePaymentGateway()
        with self.assertRaises(CartNotFoundError):
            checkout_service.checkout(user=self.user, gateway=gateway)
        self.assertEqual(gateway.intents, [])

    def test_checkout_empty_cart_raises(self):
        Cart.objects.create(user=self.user)  # cart exists but has no items
        gateway = FakePaymentGateway()
        with self.assertRaises(EmptyCartError):
            checkout_service.checkout(user=self.user, gateway=gateway)
        self.assertEqual(gateway.intents, [])

    def test_checkout_creates_pending_order_via_gateway(self):
        Cart.objects.create(user=self.user)
        cart_service.add_item(user=self.user, product_id=self.product.id, quantity=2)
        gateway = FakePaymentGateway()

        result = checkout_service.checkout(
            user=self.user, shipping_address='Av. Siempre Viva 742', gateway=gateway
        )

        self.assertEqual(result.order.status, Order.Status.PENDING)
        self.assertEqual(result.order.total_amount, Decimal('40.00'))
        self.assertEqual(result.intent.intent_id, 'pi_fake')
        self.assertEqual(gateway.intents[0]['amount'], Decimal('40.00'))
        self.assertEqual(gateway.intents[0]['metadata']['user_id'], self.user.id)

    def test_checkout_snapshots_order_items(self):
        Cart.objects.create(user=self.user)
        cart_service.add_item(user=self.user, product_id=self.product.id, quantity=2)

        result = checkout_service.checkout(
            user=self.user, shipping_address='Av. Siempre Viva 742',
            gateway=FakePaymentGateway(),
        )

        # Price changes after checkout: the order must not be affected
        Product.objects.filter(pk=self.product.pk).update(price=Decimal('99.99'))
        result.order.refresh_from_db()

        self.assertEqual(result.order.total_amount, Decimal('40.00'))
        item = result.order.items.get()
        self.assertEqual(item.price, Decimal('20.00'))
        self.assertEqual(item.product_name, 'Vinyl')

    def test_checkout_insufficient_stock_never_calls_gateway(self):
        Cart.objects.create(user=self.user)
        cart_service.add_item(user=self.user, product_id=self.product.id, quantity=1)
        Product.objects.filter(pk=self.product.pk).update(stock=0)
        gateway = FakePaymentGateway()

        with self.assertRaises(OutOfStockError):
            checkout_service.checkout(user=self.user, gateway=gateway)
        self.assertEqual(gateway.intents, [])

    def test_gateway_failure_leaves_no_order(self):
        Cart.objects.create(user=self.user)
        cart_service.add_item(user=self.user, product_id=self.product.id, quantity=1)

        with self.assertRaises(PaymentError):
            checkout_service.checkout(user=self.user, gateway=FakePaymentGateway(fail=True))
        self.assertFalse(Order.objects.exists())


class PaymentServiceTests(TestCase):
    """Webhook processing: signature checks and order state transitions."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='payuser', email='pay@example.com', password='password123'
        )
        self.category = Category.objects.create(name='Games', slug='games')
        self.product = Product.objects.create(
            name='Board Game', description='Fun', price=Decimal('30.00'),
            stock=4, category=self.category,
        )
        self.order = Order.objects.create(
            user=self.user, total_amount=Decimal('30.00'),
            stripe_payment_intent_id='pi_wh_1',
        )
        self.order.items.create(
            product=self.product, product_name=self.product.name,
            price=self.product.price, quantity=2,
        )

    @staticmethod
    def _event(event_type, intent_id='pi_wh_1'):
        return PaymentEvent(type=event_type, intent_id=intent_id)

    def test_invalid_signature_raises(self):
        # Goes through the real Stripe adapter: bad signature must be rejected
        with self.assertRaises(PaymentError):
            payment_service.process_webhook(b'{}', 'bad_sig')

    def test_process_webhook_delegates_to_gateway(self):
        gateway = FakePaymentGateway(
            event=self._event(PaymentEventType.PAYMENT_SUCCEEDED)
        )
        action = payment_service.process_webhook(b'{}', 'valid_sig', gateway=gateway)
        self.assertEqual(action, 'paid')

    def test_irrelevant_event_is_ignored(self):
        gateway = FakePaymentGateway(event=None)  # gateway returns None for unknown types
        action = payment_service.process_webhook(b'{}', 'valid_sig', gateway=gateway)
        self.assertEqual(action, 'ignored')

    def test_payment_succeeded_marks_order_paid_and_reduces_stock(self):
        action = payment_service.process_payment_event(
            self._event(PaymentEventType.PAYMENT_SUCCEEDED)
        )

        self.assertEqual(action, 'paid')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 2)
        self.assertFalse(Cart.objects.filter(user=self.user).exists())

    def test_webhook_retry_is_idempotent(self):
        event = self._event(PaymentEventType.PAYMENT_SUCCEEDED)
        payment_service.process_payment_event(event)
        payment_service.process_payment_event(event)  # Stripe retries deliveries

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 2)  # stock reduced only once

    def test_payment_failed_marks_order_failed(self):
        action = payment_service.process_payment_event(
            self._event(PaymentEventType.PAYMENT_FAILED)
        )

        self.assertEqual(action, 'failed')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FAILED)

    def test_unknown_order_raises(self):
        with self.assertRaises(OrderNotFoundError):
            payment_service.process_payment_event(
                self._event(PaymentEventType.PAYMENT_SUCCEEDED, intent_id='pi_unknown')
            )


class GatewayFactoryTests(TestCase):
    """The factory selects the strategy from settings."""

    def test_default_gateway_is_stripe(self):
        self.assertIsInstance(get_payment_gateway(), StripeGateway)

    @override_settings(PAYMENT_GATEWAY='paypal')
    def test_unknown_gateway_raises_loudly(self):
        with self.assertRaises(ValueError) as ctx:
            get_payment_gateway()
        self.assertIn('paypal', str(ctx.exception))

    @override_settings(PAYMENT_GATEWAY='stripe')
    def test_registered_gateway_is_accepted(self):
        self.assertIsInstance(get_payment_gateway(), StripeGateway)


@patch('core.gateways.stripe_gateway.stripe.PaymentIntent.create')
class CheckoutTests(TestCase):
    """End-to-end checkout through the API with the real Stripe adapter mocked."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='checkoutuser', email='checkout@example.com', password='password123')
        self.client.force_authenticate(user=self.user)
        
        self.category = Category.objects.create(name='Gadgets', slug='gadgets')
        self.product = Product.objects.create(
            name='Smartwatch',
            description='A smart watch',
            price=Decimal('199.99'),
            stock=2,
            category=self.category
        )
        
        self.cart = Cart.objects.create(user=self.user)
        self.product.cart_items.create(cart=self.cart, quantity=1)
        
        self.checkout_url = reverse('checkout')

    def test_checkout_success(self, mock_stripe_create):
        # Mock the Stripe response
        mock_stripe_create.return_value = MagicMock(
            id='pi_test_123',
            client_secret='sk_test_secret'
        )
        
        data = {'shipping_address': '123 Test St'}
        response = self.client.post(self.checkout_url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('client_secret', response.data)
        
        # Verify Order was created
        self.assertTrue(Order.objects.filter(user=self.user, stripe_payment_intent_id='pi_test_123').exists())

    def test_checkout_rejects_empty_cart(self, mock_stripe_create):
        self.cart.items.all().delete()  # cart exists, but with no items
        response = self.client.post(self.checkout_url, {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_stripe_create.assert_not_called()
