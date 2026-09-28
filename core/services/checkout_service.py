"""Checkout orchestration: stock validation, totals, payment, order creation.

Depends on the PaymentGateway interface (Strategy), never on a concrete
provider. The gateway is injected for tests or resolved from settings.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from ..gateways import PaymentGateway, PaymentIntentResult, get_payment_gateway
from ..models import Cart, Order, OrderItem
from .exceptions import CartNotFoundError, EmptyCartError, OutOfStockError


@dataclass(frozen=True)
class CheckoutResult:
    """Outcome of a successful checkout: the local order and the payment handle."""

    order: Order
    intent: PaymentIntentResult


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
    gateway: Optional[PaymentGateway] = None,
) -> CheckoutResult:
    """Turn the user's cart into a PENDING order and a payment intent.

    Order of operations: external payment call first (no orphan rows on
    gateway failure), then an atomic DB write for the order snapshot.
    """
    gateway = gateway or get_payment_gateway()

    try:
        cart = Cart.objects.get(user=user)
    except Cart.DoesNotExist:
        raise CartNotFoundError

    if not cart.items.exists():
        raise EmptyCartError

    _validate_stock(cart)
    total_amount = _calculate_total(cart)

    # External service: ask the gateway to collect the payment
    intent = gateway.create_intent(
        amount=total_amount,
        currency=currency,
        metadata={"user_id": user.id, "cart_id": cart.id},
    )

    # Atomic snapshot of the order and its items
    with transaction.atomic():
        order = Order.objects.create(
            user=user,
            total_amount=total_amount,
            status=Order.Status.PENDING,
            stripe_payment_intent_id=intent.intent_id,
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

    return CheckoutResult(order=order, intent=intent)
