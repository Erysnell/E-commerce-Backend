# E-Commerce API

An e-commerce platform backend built with **Django** and **Django Rest Framework**, featuring JWT authentication, a complex data model for products and carts, and Stripe payment gateway integration.

## Features

- **JWT Authentication**: Secure sign-up, login, and token refresh.
- **Product Management**: Full CRUD operations for products and categories (Admin only).
- **Advanced Search**: Search products by name, description, category, and price range.
- **Shopping Cart**: Add, view, and remove items from a persistent user cart.
- **Checkout & Payments**: Integration with Stripe PaymentIntents to process payments.
- **Webhooks**: Automated stock reduction and order status updates via Stripe Webhooks.
- **Admin Panel**: Full-featured Django admin interface for managing the platform.
- **API Documentation**: Interactive Swagger UI powered by `drf-spectacular`.

## Tech Stack

- **Backend**: Django, Django Rest Framework
- **Authentication**: `djangorestframework-simplejwt`
- **Payments**: Pluggable gateway (Stripe by default)
- **Documentation**: `drf-spectacular` (OpenAPI 3)
- **Database**: SQLite (Default) / PostgreSQL (Configurable)

## Project Architecture

The backend is a **layered monolith**: views are thin HTTP adapters, business
rules live in a service layer, and external providers sit behind interfaces.

```
core/
├── views.py            # Presentation: HTTP in/out, domain errors → status codes
├── serializers.py      # DTOs: request validation & response shaping
├── services/           # Service layer: all business rules
│   ├── exceptions.py       # DomainError hierarchy (no HTTP concepts)
│   ├── cart_service.py     # stock rules, quantity merging
│   ├── checkout_service.py # totals, order snapshot, gateway orchestration
│   └── payment_service.py  # webhook → order state transitions (idempotent)
├── gateways/           # Payment ports & adapters (Strategy pattern)
│   ├── base.py             # PaymentGateway ABC + PaymentEvent/Result
│   ├── stripe_gateway.py   # Stripe adapter
│   └── factory.py          # runtime selection via settings.PAYMENT_GATEWAY
└── models.py           # Persistence (Active Record via Django ORM)
```

Key design decisions:

- **Service Layer**: services raise `DomainError` subclasses and never build
  HTTP responses; views translate errors to status codes. This lets you reuse
  the same logic from tasks, the admin, or a future frontend.
- **Strategy (payment gateways)**: `checkout_service` and `payment_service`
  depend on the `PaymentGateway` ABC, not on Stripe. The concrete adapter is
  injected in tests or resolved from `settings.PAYMENT_GATEWAY`.

### Adding a new payment provider

1. Create `core/gateways/paypal_gateway.py` implementing `PaymentGateway`
   (`create_intent` + `parse_webhook_event`).
2. Register it in `core/gateways/factory.py`:
   ```python
   GATEWAYS = {"stripe": StripeGateway, "paypal": PayPalGateway}
   ```
3. Set `PAYMENT_GATEWAY=paypal` in `.env`.

No changes are required in the services or views.

## Setup Instructions

### 1. Clone the repository
```bash
git clone <repository-url>
cd e-commerce
```

### 2. Create and activate a virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the project root (start from the provided template):
```bash
cp .env.example .env
```
Below is an explanation of how to obtain each required key:

```env
SECRET_KEY=your-secret-key-must-be-at-least-32-bytes-long
DEBUG=True
ALLOWED_HOSTS=*

# Stripe
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PUBLISHABLE_KEY=pk_test_...

# Payment gateway strategy (see core/gateways/)
PAYMENT_GATEWAY=stripe

# CORS
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
```

#### **How to obtain your keys:**

1.  **`SECRET_KEY` (Django Secret Key):**
    This is used by Django for cryptographic signing (sessions, CSRF tokens, etc.).
    *   You can generate a new secure secret key by running this command in your terminal:
        ```bash
        python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
        ```
    *   Or use an online generator like [Django Secret Key Generator](https://miniwebtool.com/django-secret-key-generator/).

2.  **`STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` (Stripe API Keys):**
    You need a free Stripe account to obtain these.
    *   Go to the [Stripe Dashboard](https://dashboard.stripe.com/).
    *   Navigate to **Developers > API keys**.
    *   You will see two keys (make sure you are in **Test mode** for development):
        *   **Publishable key** (`pk_test_...`): Use this for client-side (frontend) integration.
        *   **Secret key** (`sk_test_...`): Use this for server-side (backend) integration.

3.  **`STRIPE_WEBHOOK_SECRET` (Webhook Signing Secret):**
    This key is used to verify that incoming webhook events are genuinely from Stripe.
    *   **Option A (Stripe CLI - Recommended for local development):**
        1.  Install the [Stripe CLI](https://docs.stripe.com/stripe-cli).
        2.  Run the following command to start a local listener:
            ```bash
            stripe listen --forward-to localhost:8000/api/payments/webhook/
            ```
        3.  The CLI will output a `whsec_...` secret. Copy this and add it to your `.env`.
    *   **Option B (Stripe Dashboard for Production):**
        1.  In the Stripe Dashboard, go to **Developers > Webhooks**.
        2.  Click **Add endpoint** and point it to your production API URL (`https://yourdomain.com/api/payments/webhook/`).
        3.  Once the endpoint is created, click on it and copy the **Signing secret** (`whsec_...`).

4.  **`DEBUG` and `ALLOWED_HOSTS`:**
    *   Set `DEBUG=True` during development. Set it to `False` in production.
    *   For local development, `ALLOWED_HOSTS=*` is sufficient. In production, set it to your domain (e.g., `yourdomain.com,www.yourdomain.com`).

5.  **`CORS_ALLOWED_ORIGINS`:**
    *   This tells your API which frontend domains are allowed to make requests to it.
    *   For local development, keep it as `http://localhost:3000,http://localhost:5173` (common React/Vue ports).
    *   In production, change this to your frontend's actual URL (e.g., `https://your-frontend-app.com`).

6.  **`PAYMENT_GATEWAY`:**
    *   Selects which payment provider adapter the API uses (see [Project Architecture](#project-architecture)).
    *   Default (and only registered) value: `stripe`. An unknown value fails loudly at runtime instead of falling back silently.

### 5. Apply Migrations
```bash
python manage.py migrate
```

### 6. Create a Superuser (Admin)
```bash
python manage.py createsuperuser
```

### 7. Run the Development Server
```bash
python manage.py runserver
```

## API Endpoints

### Authentication
- `POST /api/auth/register/`: Register a new user.
- `POST /api/auth/login/`: Obtain JWT access and refresh tokens.
- `POST /api/auth/token/refresh/`: Refresh an access token.
- `GET/PUT /api/auth/profile/`: View/Update current user profile.

### Products & Categories
- `GET /api/products/`: List products (supports `search`, `category`, `min_price`, `max_price`).
- `POST /api/products/`: Create product (Admin).
- `GET/PUT/DELETE /api/products/{slug}/`: Product details (Admin for write).
- `GET /api/categories/`: List categories.
- `POST /api/categories/`: Create category (Admin).

### Shopping Cart
- `GET /api/cart/`: View current user's cart and total.
- `POST /api/cart/add/`: Add product to cart.
- `DELETE /api/cart/item/{item_id}/delete/`: Remove item from cart.

### Checkout & Orders
- `POST /api/checkout/`: Initialize payment and get Stripe `client_secret`.
- `GET /api/orders/`: View user's order history.
- `POST /api/payments/webhook/`: Stripe Webhook endpoint.

## API Documentation
Once the server is running, visit:
- **Swagger UI**: [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/)
- **OpenAPI Schema**: [http://localhost:8000/api/schema/](http://localhost:8000/api/schema/)

## Admin Panel
Visit [http://localhost:8000/admin/](http://localhost:8000/admin/) and log in with your superuser credentials.

## Testing
Run the test suite with:
```bash
python manage.py test core
```

## Stripe Integration
To test the payment flow:
1. Use Stripe test cards (e.g., `4242 4242 4242 4242`).
2. Set up a local webhook listener using the Stripe CLI:
   ```bash
   stripe listen --forward-to localhost:8000/api/payments/webhook/
   ```
3. Copy the `whsec_...` from the CLI output into your `.env` file.
