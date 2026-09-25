from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    CartItemCreateView,
    CartItemDeleteView,
    CartView,
    CategoryViewSet,
    CheckoutView,
    CustomTokenObtainPairView,
    OrderListView,
    ProductViewSet,
    ProfileView,
    RegisterView,
    StripeWebhookView,
)

router = DefaultRouter()
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'products', ProductViewSet, basename='product')

urlpatterns = [
    # Auth
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/profile/', ProfileView.as_view(), name='profile'),

    # Cart
    path('cart/', CartView.as_view(), name='cart'),
    path('cart/add/', CartItemCreateView.as_view(), name='cart_add'),
    path('cart/item/<int:item_id>/delete/', CartItemDeleteView.as_view(), name='cart_item_delete'),

    # Checkout & Payments
    path('checkout/', CheckoutView.as_view(), name='checkout'),
    path('payments/webhook/', StripeWebhookView.as_view(), name='stripe_webhook'),
    path('orders/', OrderListView.as_view(), name='order_list'),

    # Router URLs for Categories and Products
    path('', include(router.urls)),
]
