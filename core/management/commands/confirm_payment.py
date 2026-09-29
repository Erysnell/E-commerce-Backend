"""Confirm a PaymentIntent with a Stripe test card (local testing helper).

Usage:
    python manage.py confirm_payment pi_xxx            # card succeeds (4242...)
    python manage.py confirm_payment pi_xxx --fail      # card is declined (0000...)
"""
from django.core.management.base import BaseCommand, CommandError

from core.gateways.stripe_gateway import StripeGateway
from core.services.exceptions import PaymentError

TEST_PAYMENT_METHODS = {
    "ok": "pm_card_visa",      # → succeeds (same as 4242 4242 4242 4242)
    "fail": "pm_card_chargeDeclined",
}


class Command(BaseCommand):
    help = "Confirm a Stripe PaymentIntent using test payment methods."

    def add_arguments(self, parser):
        parser.add_argument("intent_id", help="PaymentIntent id (pi_...)")
        parser.add_argument(
            "--fail",
            action="store_true",
            help="Simulate a declined card instead of a successful one",
        )
        parser.add_argument(
            "--return-url",
            default="http://localhost:8000/",
            help="return_url required by Stripe when Dashboard payment "
                 "methods with redirect are enabled (default: %(default)s)",
        )

    def handle(self, *args, **options):
        import stripe

        gateway = StripeGateway()
        payment_method = TEST_PAYMENT_METHODS["fail" if options["fail"] else "ok"]

        try:
            intent = stripe.PaymentIntent.confirm(
                options["intent_id"],
                payment_method=payment_method,
                return_url=options["return_url"],
                api_key=gateway.api_key,
            )
        except stripe.error.StripeError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(
            f"Confirmed {intent.id}: status={intent.status}\n"
            "If 'stripe listen' is running, the webhook should hit the API now."
        ))
