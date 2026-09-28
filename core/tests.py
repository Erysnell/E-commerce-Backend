from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

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


class CheckoutTests(TestCase):
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

    @patch('core.services.checkout_service.stripe.PaymentIntent.create')
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


@patch('core.services.checkout_service.stripe.PaymentIntent.create')
class CheckoutServiceTests(TestCase):
    """Checkout orchestration without the HTTP layer."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='chkuser', email='chk@example.com', password='password123'
        )
        self.category = Category.objects.create(name='Media', slug='media')
        self.product = Product.objects.create(
            name='Vinyl', description='Record', price=Decimal('20.00'),
            stock=5, category=self.category,
        )

    def test_checkout_empty_cart_raises(self, mock_create):
        Cart.objects.create(user=self.user)  # cart exists but has no items
        with self.assertRaises(EmptyCartError):
            checkout_service.checkout(user=self.user)
        mock_create.assert_not_called()

    def test_checkout_missing_cart_raises(self, mock_create):
        with self.assertRaises(CartNotFoundError):
            checkout_service.checkout(user=self.user)
        mock_create.assert_not_called()

    def test_checkout_snapshots_order_items(self, mock_create):
        mock_create.return_value = MagicMock(id='pi_snap', client_secret='cs_snap')
        Cart.objects.create(user=self.user)
        cart_service.add_item(user=self.user, product_id=self.product.id, quantity=2)

        result = checkout_service.checkout(user=self.user, shipping_address='Av. Siempre Viva 742')

        # Price changes after checkout: the order must not be affected
        Product.objects.filter(pk=self.product.pk).update(price=Decimal('99.99'))
        result.order.refresh_from_db()

        self.assertEqual(result.order.total_amount, Decimal('40.00'))
        item = result.order.items.get()
        self.assertEqual(item.price, Decimal('20.00'))
        self.assertEqual(item.product_name, 'Vinyl')

    def test_checkout_insufficient_stock_raises(self, mock_create):
        Cart.objects.create(user=self.user)
        cart_service.add_item(user=self.user, product_id=self.product.id, quantity=1)
        Product.objects.filter(pk=self.product.pk).update(stock=0)

        with self.assertRaises(OutOfStockError):
            checkout_service.checkout(user=self.user)
        mock_create.assert_not_called()


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

    def test_invalid_signature_raises(self):
        with self.assertRaises(PaymentError):
            payment_service.process_webhook(b'{}', 'bad_sig')

    @patch('core.services.payment_service.stripe.Webhook.construct_event')
    def test_payment_succeeded_marks_order_paid_and_reduces_stock(self, mock_construct):
        mock_construct.return_value = {
            'type': 'payment_intent.succeeded',
            'data': {'object': {'id': 'pi_wh_1'}},
        }
        action = payment_service.process_webhook(b'{}', 'sig')

        self.assertEqual(action, 'paid')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 2)
        self.assertFalse(Cart.objects.filter(user=self.user).exists())

    @patch('core.services.payment_service.stripe.Webhook.construct_event')
    def test_webhook_retry_is_idempotent(self, mock_construct):
        mock_construct.return_value = {
            'type': 'payment_intent.succeeded',
            'data': {'object': {'id': 'pi_wh_1'}},
        }
        payment_service.process_webhook(b'{}', 'sig')
        payment_service.process_webhook(b'{}', 'sig')  # Stripe retries deliveries

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 2)  # stock reduced only once

    @patch('core.services.payment_service.stripe.Webhook.construct_event')
    def test_payment_failed_marks_order_failed(self, mock_construct):
        mock_construct.return_value = {
            'type': 'payment_intent.payment_failed',
            'data': {'object': {'id': 'pi_wh_1'}},
        }
        payment_service.process_webhook(b'{}', 'sig')

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FAILED)

    @patch('core.services.payment_service.stripe.Webhook.construct_event')
    def test_unknown_order_raises(self, mock_construct):
        mock_construct.return_value = {
            'type': 'payment_intent.succeeded',
            'data': {'object': {'id': 'pi_unknown'}},
        }
        with self.assertRaises(OrderNotFoundError):
            payment_service.process_webhook(b'{}', 'sig')
