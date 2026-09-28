"""Stripe adapter: translates Stripe's API into the domain's vocabulary."""
from decimal import Decimal
from typing import Optional

import stripe
from django.conf import settings

from ..services.exceptions import PaymentError
from .base import PaymentEvent, PaymentEventType, PaymentGateway, PaymentIntentResult

# Stripe event type (wire format) → normalized domain event
_STRIPE_EVENT_MAP = {
    "payment_intent.succeeded": PaymentEventType.PAYMENT_SUCCEEDED,
    "payment_intent.payment_failed": PaymentEventType.PAYMENT_FAILED,
}


class StripeGateway(PaymentGateway):
    """Concrete strategy backed by Stripe's PaymentIntents API."""

    def __init__(self, api_key: Optional[str] = None, webhook_secret: Optional[str] = None):
        self._api_key = api_key
        self._webhook_secret = webhook_secret

    @property
    def api_key(self) -> str:
        key = self._api_key or settings.STRIPE_SECRET_KEY
        if not key:
            raise PaymentError("Stripe API key is not configured")
        return key

    @property
    def webhook_secret(self) -> str:
        secret = self._webhook_secret or settings.STRIPE_WEBHOOK_SECRET
        if not secret:
            raise PaymentError("Webhook secret is not configured")
        return secret

    @staticmethod
    def _to_minor_units(amount: Decimal) -> int:
        """Providers charge minor units (cents), the domain uses major units."""
        return int(amount * 100)

    def create_intent(
        self,
        *,
        amount: Decimal,
        currency: str,
        metadata: Optional[dict] = None,
    ) -> PaymentIntentResult:
        try:
            intent = stripe.PaymentIntent.create(
                amount=self._to_minor_units(amount),
                currency=currency,
                metadata=metadata or {},
                automatic_payment_methods={"enabled": True},
                api_key=self.api_key,  # per-request key, no global state
            )
        except stripe.error.StripeError as exc:
            raise PaymentError(str(exc)) from exc

        return PaymentIntentResult(
            intent_id=intent.id,
            client_secret=intent.client_secret,
            amount=amount,
            currency=currency,
        )

    def parse_webhook_event(
        self, payload: bytes, signature: str
    ) -> Optional[PaymentEvent]:
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
        except ValueError as exc:
            raise PaymentError("Invalid webhook payload") from exc
        except stripe.error.SignatureVerificationError as exc:
            raise PaymentError("Invalid webhook signature") from exc

        event_type = _STRIPE_EVENT_MAP.get(event.get("type"))
        if event_type is None:
            return None  # valid event, but nothing we care about

        intent_id = event.get("data", {}).get("object", {}).get("id")
        return PaymentEvent(type=event_type, intent_id=intent_id, raw=event)
