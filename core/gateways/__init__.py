"""Payment gateway abstraction (Strategy pattern).

`base` defines the interface every gateway must implement; concrete
adapters (Stripe today, others tomorrow) live alongside it and are
selected at runtime by the factory based on settings.
"""
from .base import PaymentEvent, PaymentEventType, PaymentGateway, PaymentIntentResult
from .factory import get_payment_gateway

__all__ = [
    "PaymentEvent",
    "PaymentEventType",
    "PaymentGateway",
    "PaymentIntentResult",
    "get_payment_gateway",
]
