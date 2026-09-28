"""Domain errors raised by the service layer.

Views translate these into HTTP responses; services never
depend on HTTP concepts (status codes, request/response).
"""


class DomainError(Exception):
    """Base class for all domain/service errors."""


class CartNotFoundError(DomainError):
    """The user has no shopping cart yet."""

    def __init__(self):
        super().__init__("Cart not found")


class EmptyCartError(DomainError):
    """Checkout was requested with an empty cart."""

    def __init__(self):
        super().__init__("Cart is empty")


class ProductNotFoundError(DomainError):
    """The referenced product does not exist (or is inactive)."""

    def __init__(self, product_id=None):
        self.product_id = product_id
        super().__init__("Product not found")


class OutOfStockError(DomainError):
    """Requested quantity exceeds available stock."""

    def __init__(self, product_name, requested, available):
        self.product_name = product_name
        self.requested = requested
        self.available = available
        super().__init__(
            f"Not enough stock for {product_name}: "
            f"requested {requested}, available {available}"
        )


class OrderNotFoundError(DomainError):
    """The referenced order does not exist."""

    def __init__(self, identifier=None):
        self.identifier = identifier
        super().__init__("Order not found")


class PaymentError(DomainError):
    """The payment gateway rejected or failed to process the payment."""

    def __init__(self, detail="Payment processing failed"):
        super().__init__(detail)
