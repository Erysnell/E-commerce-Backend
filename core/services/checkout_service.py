"""Checkout orchestration: stock validation, totals, Stripe, order creation."""
from dataclasses import dataclass
from decimal import Decimal

import stripe
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from ..models import Cart, Order, OrderItem
from .exceptions import CartNotFoundError, EmptyCartError, OutOfStockError, PaymentError

stripe.api_key = settings.STRIPE_SECRET_KEY


@dataclass(frozen=True)
class CheckoutResult:
    """Outcome of a successful checkout: the local order and the payment intent."""

    order: Order
    payment_intent_id: str
    client_secret: str


def _validate_stock(cart: Cart) -> None:
    for item in cart.items.select_related("product"):
        if item.product.stock < item.quantity:
            raise OutOfStockError(
                product_name=item.product.name,
                requested=item.quantity,
                available=item.product.stock,
            )


def _calculate_total(cart: Cart) -> Decimal:
    return sum(
        (item.product.price * item.quantity for item in cart.items.select_related("product")),
        Decimal("0.00"),
    )


def checkout(
    *,
    user: AbstractBaseUser,
    shipping_address: str = "",
    currency: str = "usd",
) -> CheckoutResult:
    """Turn the user's cart into a PENDING order and a Stripe PaymentIntent.

    Order of operations: external payment call first (no orphan rows on
    gateway failure), then an atomic DB write for the order snapshot.
    """
    try:
        cart = Cart.objects.get(user=user)
    except Cart.DoesNotExist:
        raise CartNotFoundError

    if not cart.items.exists():
        raise EmptyCartError

    _validate_stock(cart)
    total_amount = _calculate_total(cart)

    # External service: create the payment intent in Stripe
    try:
        intent = stripe.PaymentIntent.create(
            amount=int(total_amount * 100),  # Stripe uses minor units (cents)
            currency=currency,
            metadata={"user_id": user.id, "cart_id": cart.id},
            automatic_payment_methods={"enabled": True},
        )
    except stripe.error.StripeError as exc:
        raise PaymentError(str(exc)) from exc

    # Atomic snapshot of the order and its items
    with transaction.atomic():
        order = Order.objects.create(
            user=user,
            total_amount=total_amount,
            status=Order.Status.PENDING,
            stripe_payment_intent_id=intent.id,
            shipping_address=shipping_address,
        )
        OrderItem.objects.bulk_create(
            OrderItem(
                order=order,
                product=item.product,
                product_name=item.product.name,
                price=item.product.price,
                quantity=item.quantity,
            )
            for item in cart.items.select_related("product")
        )

    return CheckoutResult(
        order=order,
        payment_intent_id=intent.id,
        client_secret=intent.client_secret,
    )
