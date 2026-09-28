"""Payment webhook processing: gateway events → order state transitions.

`process_webhook` verifies and translates the raw payload through the
gateway; `process_payment_event` applies the normalized event to our
domain and can be tested without any provider mocks.
"""
from typing import Optional

from django.db import transaction

from ..gateways import PaymentEvent, PaymentEventType, PaymentGateway, get_payment_gateway
from ..models import Cart, Order
from .exceptions import OrderNotFoundError


def process_webhook(
    payload: bytes,
    signature: str,
    gateway: Optional[PaymentGateway] = None,
) -> str:
    """Verify the webhook with the gateway and apply the event.

    Returns the action taken ('paid', 'failed' or 'ignored').
    Raises PaymentError when the signature or payload is invalid.
    """
    gateway = gateway or get_payment_gateway()

    event = gateway.parse_webhook_event(payload, signature)
    if event is None:
        return "ignored"
    return process_payment_event(event)


def process_payment_event(event: PaymentEvent) -> str:
    """Apply a normalized payment event to the corresponding order."""
    if event.type is PaymentEventType.PAYMENT_SUCCEEDED:
        return _handle_payment_succeeded(event.intent_id)
    if event.type is PaymentEventType.PAYMENT_FAILED:
        return _handle_payment_failed(event.intent_id)
    return "ignored"


def _handle_payment_succeeded(intent_id: Optional[str]) -> str:
    order = _get_order(intent_id)

    # Webhooks can be delivered more than once: make retries idempotent
    if order.status == Order.Status.PAID:
        return "paid"

    order.status = Order.Status.PAID
    order.save(update_fields=["status", "updated_at"])

    _reduce_stock(order)
    _clear_cart(order)
    return "paid"


def _handle_payment_failed(intent_id: Optional[str]) -> str:
    order = _get_order(intent_id)
    if order.status != Order.Status.PAID:
        order.status = Order.Status.FAILED
        order.save(update_fields=["status", "updated_at"])
    return "failed"


def _get_order(intent_id: Optional[str]) -> Order:
    if not intent_id:
        raise OrderNotFoundError(intent_id)
    try:
        return Order.objects.get(stripe_payment_intent_id=intent_id)
    except Order.DoesNotExist as exc:
        raise OrderNotFoundError(intent_id) from exc


def _reduce_stock(order: Order) -> None:
    """Decrement stock for every item in a paid order (atomic)."""
    with transaction.atomic():
        for item in order.items.select_related("product"):
            product = item.product
            if product is None:
                continue  # product was deleted after the order was placed
            product.stock -= item.quantity
            product.save(update_fields=["stock"])


def _clear_cart(order: Order) -> None:
    Cart.objects.filter(user=order.user).delete()
