"""Shopping cart business rules.

Functions here are HTTP-agnostic: they receive domain objects
and raise DomainError subclasses instead of returning responses.
"""
from django.contrib.auth.models import AbstractBaseUser

from ..models import Cart, CartItem, Product
from .exceptions import OutOfStockError, ProductNotFoundError


def get_or_create_cart(user: AbstractBaseUser) -> Cart:
    """Return the user's cart, creating it on first access."""
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


def add_item(*, user: AbstractBaseUser, product_id: int, quantity: int = 1) -> CartItem:
    """Add (or merge) a product into the user's cart, enforcing stock limits."""
    if not Product.objects.filter(id=product_id, is_active=True).exists():
        raise ProductNotFoundError(product_id)

    product = Product.objects.get(id=product_id)
    cart = get_or_create_cart(user)

    cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product)
    if not created:
        quantity += cart_item.quantity

    if quantity > product.stock:
        raise OutOfStockError(
            product_name=product.name,
            requested=quantity,
            available=product.stock,
        )

    cart_item.quantity = quantity
    cart_item.save()
    return cart_item


def remove_item(*, user: AbstractBaseUser, item_id: int) -> None:
    """Delete an item from the user's cart.

    Raises CartItem.DoesNotExist when the item does not belong
    to the user (the view maps it to a 404).
    """
    CartItem.objects.get(id=item_id, cart__user=user).delete()
