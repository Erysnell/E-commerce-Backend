from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from .models import Cart, Category, Order, Product

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

    @patch('core.views.stripe.PaymentIntent.create')
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
