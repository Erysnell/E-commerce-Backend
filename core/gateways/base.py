"""Payment gateway interface: the contract that adapters must fulfill.

The domain speaks in terms of PaymentIntentResult / PaymentEvent;
adapters translate to and from the concrete provider's vocabulary
(Stripe, PayPal, ...). Nothing outside this package should know
which provider is in use.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


class PaymentEventType(Enum):
    """Normalized payment events understood by the domain."""

    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_FAILED = "payment_failed"


@dataclass(frozen=True)
class PaymentIntentResult:
    """Outcome of asking the gateway to collect a payment."""

    intent_id: str
    client_secret: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class PaymentEvent:
    """A normalized webhook event, decoupled from any provider's payload."""

    type: PaymentEventType
    intent_id: Optional[str]
    raw: Any = None


class PaymentGateway(ABC):
    """Strategy for interacting with a payment provider."""

    @abstractmethod
    def create_intent(
        self,
        *,
        amount: Decimal,
        currency: str,
        metadata: Optional[dict] = None,
    ) -> PaymentIntentResult:
        """Start a payment collection and return its handle.

        Raises services.exceptions.PaymentError when the provider
        rejects the request (invalid key, network failure, ...).
        """

    @abstractmethod
    def parse_webhook_event(
        self, payload: bytes, signature: str
    ) -> Optional[PaymentEvent]:
        """Verify a webhook payload and translate it to a PaymentEvent.

        Returns None for events that are valid but irrelevant to us.
        Raises services.exceptions.PaymentError when the payload or
        the signature cannot be trusted.
        """
