"""Payment webhook processing: Stripe events → order state transitions."""
import stripe
from django.conf import settings
from django.db import transaction

from ..models import Cart, Order
from .exceptions import OrderNotFoundError, PaymentError


def process_webhook(payload: bytes, signature: str) -> str:
    """Verify the Stripe signature and apply the event to our domain.

    Returns the normalized action taken ('paid', 'failed' or 'ignored').
    Raises PaymentError when the signature or payload is invalid.
    """
    secret = settings.STRIPE_WEBHOOK_SECRET
    if not secret:
        raise PaymentError("Webhook secret is not configured")

    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except ValueError as exc:
        raise PaymentError("Invalid webhook payload") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise PaymentError("Invalid webhook signature") from exc

    event_type = event.get("type")
    intent_id = event.get("data", {}).get("object", {}).get("id")

    if event_type == "payment_intent.succeeded":
        return _handle_payment_succeeded(intent_id)
    if event_type == "payment_intent.payment_failed":
        return _handle_payment_failed(intent_id)
    return "ignored"


def _handle_payment_succeeded(intent_id: str) -> str:
    order = _get_order(intent_id)

    # Webhooks can be delivered more than once: make retries idempotent
    if order.status == Order.Status.PAID:
        return "paid"

    order.status = Order.Status.PAID
    order.save(update_fields=["status", "updated_at"])

    _reduce_stock(order)
    _clear_cart(order)
    return "paid"


def _handle_payment_failed(intent_id: str) -> str:
    order = _get_order(intent_id)
    if order.status != Order.Status.PAID:
        order.status = Order.Status.FAILED
        order.save(update_fields=["status", "updated_at"])
    return "failed"


def _get_order(intent_id: str) -> Order:
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
