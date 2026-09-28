"""Gateway factory: picks the configured strategy at runtime."""
from django.conf import settings

from .base import PaymentGateway
from .stripe_gateway import StripeGateway

# Registry of available strategies, keyed by settings value
GATEWAYS = {
    "stripe": StripeGateway,
}


def get_payment_gateway() -> PaymentGateway:
    """Build the payment gateway selected by settings.PAYMENT_GATEWAY.

    Raising ValueError (instead of falling back silently) keeps
    configuration mistakes loud and early.
    """
    name = getattr(settings, "PAYMENT_GATEWAY", "stripe")
    try:
        return GATEWAYS[name]()
    except KeyError:
        available = ", ".join(sorted(GATEWAYS))
        raise ValueError(
            f"Unknown payment gateway {name!r}. Available gateways: {available}"
        ) from None
