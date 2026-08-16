"""Stripe, from the API key to the verified webhook.

.. code-block:: python

    from tempest_fastapi_sdk.integrations.payment.stripe import (
        StripeClient,
        stripe_http_client,
        to_minor_units,
    )

    client = StripeClient(stripe_http_client("sk_test_..."))
    intent = await client.payment_intents.create(
        {
            "amount": to_minor_units("199.90", "brl"),
            "currency": "brl",
            "automatic_payment_methods": {"enabled": True},
            "metadata": {"order_id": "1042"},
        }
    )

Four pieces, and each exists because getting it wrong costs money:

- **A typed client** over :class:`~tempest_fastapi_sdk.utils.HTTPClient`,
  with an ``Idempotency-Key`` on every write. Bodies are form-encoded —
  Stripe accepts no JSON on writes — and nested values become bracket
  notation through :func:`~tempest_fastapi_sdk.form_encode`.
- **Money that respects zero-decimal currencies.** ``¥1050`` is ``1050``,
  not ``105000``; dividing everything by 100 is a silent billing bug no
  BRL-only test will catch.
- **Webhook verification** over the ``t.body`` payload Stripe actually
  signs, with a replay window.
- **265 event types as an enum**, generated from the specification rather
  than typed from memory.

!!! note "This client is hand-written, and that is measured"
    The SDK generates integrations from OpenAPI — the OpenPix client is
    generated. Stripe's specification does not survive the trip: the full
    generation is 3.3 MB of schemas that cost **5.8 s and 492 MB of RSS**
    to import, and slicing by resource does not help because
    ``/v1/prices`` alone reaches 864 of the 1440 component schemas. So the
    response models here are thin and keep unknown fields
    (``extra="allow"``), and the request side is a mapping. The numbers,
    and how to re-run them, are in ``scripts/regen_stripe.py``.

!!! warning "A verified webhook is a filter, not an authorization"
    The signature proves the delivery came from Stripe. It does not prove
    the state you are about to act on is current — deliveries retry, and
    arrive out of order. Before shipping goods on
    ``payment_intent.succeeded``, re-read the intent through the API.

The generated half loads lazily (:pep:`562`), so importing this package
for ``to_minor_units`` does not build the event enum.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.integrations.payment.stripe.client import (
    StripeClient as StripeClient,
)
from tempest_fastapi_sdk.integrations.payment.stripe.client import (
    StripeError as StripeError,
)
from tempest_fastapi_sdk.integrations.payment.stripe.client import (
    StripeResource as StripeResource,
)
from tempest_fastapi_sdk.integrations.payment.stripe.environment import (
    STRIPE_API_VERSION as STRIPE_API_VERSION,
)
from tempest_fastapi_sdk.integrations.payment.stripe.environment import (
    STRIPE_BASE_URL as STRIPE_BASE_URL,
)
from tempest_fastapi_sdk.integrations.payment.stripe.environment import (
    STRIPE_IDEMPOTENCY_HEADER as STRIPE_IDEMPOTENCY_HEADER,
)
from tempest_fastapi_sdk.integrations.payment.stripe.environment import (
    STRIPE_VERSION_HEADER as STRIPE_VERSION_HEADER,
)
from tempest_fastapi_sdk.integrations.payment.stripe.environment import (
    stripe_http_client as stripe_http_client,
)
from tempest_fastapi_sdk.integrations.payment.stripe.money import (
    THREE_DECIMAL_CURRENCIES as THREE_DECIMAL_CURRENCIES,
)
from tempest_fastapi_sdk.integrations.payment.stripe.money import (
    ZERO_DECIMAL_CURRENCIES as ZERO_DECIMAL_CURRENCIES,
)
from tempest_fastapi_sdk.integrations.payment.stripe.money import (
    currency_exponent as currency_exponent,
)
from tempest_fastapi_sdk.integrations.payment.stripe.money import (
    format_amount as format_amount,
)
from tempest_fastapi_sdk.integrations.payment.stripe.money import (
    from_minor_units as from_minor_units,
)
from tempest_fastapi_sdk.integrations.payment.stripe.money import (
    to_minor_units as to_minor_units,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeCheckoutSession as StripeCheckoutSession,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeCustomer as StripeCustomer,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeDeleted as StripeDeleted,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeEventObject as StripeEventObject,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeInvoice as StripeInvoice,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeList as StripeList,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeObject as StripeObject,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripePaymentIntent as StripePaymentIntent,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripePrice as StripePrice,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeProduct as StripeProduct,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeRefund as StripeRefund,
)
from tempest_fastapi_sdk.integrations.payment.stripe.schemas import (
    StripeSubscription as StripeSubscription,
)
from tempest_fastapi_sdk.integrations.payment.stripe.webhooks import (
    DEFAULT_TOLERANCE_SECONDS as DEFAULT_TOLERANCE_SECONDS,
)
from tempest_fastapi_sdk.integrations.payment.stripe.webhooks import (
    STRIPE_SIGNATURE_HEADER as STRIPE_SIGNATURE_HEADER,
)
from tempest_fastapi_sdk.integrations.payment.stripe.webhooks import (
    StripeWebhookEvent as StripeWebhookEvent,
)
from tempest_fastapi_sdk.integrations.payment.stripe.webhooks import (
    make_stripe_webhook_dependency as make_stripe_webhook_dependency,
)
from tempest_fastapi_sdk.integrations.payment.stripe.webhooks import (
    parse_event as parse_event,
)
from tempest_fastapi_sdk.integrations.payment.stripe.webhooks import (
    parse_signature_header as parse_signature_header,
)
from tempest_fastapi_sdk.integrations.payment.stripe.webhooks import (
    sign_payload as sign_payload,
)
from tempest_fastapi_sdk.integrations.payment.stripe.webhooks import (
    verify_signature as verify_signature,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.integrations.payment.stripe.events import (
        StripeEvent as StripeEvent,
    )

_LAZY_EXPORTS: frozenset[str] = frozenset({"StripeEvent"})
"""Names resolved on first access rather than at import.

The event enum has 265 members. Nothing about ``to_minor_units`` needs it,
so importing this package for the money helpers should not build it.
"""


def __getattr__(name: str) -> Any:
    """Resolve the generated event enum on first access.

    Args:
        name (str): The attribute requested.

    Returns:
        Any: :class:`StripeEvent` when asked for it.

    Raises:
        AttributeError: For any other name, matching normal module
            behaviour.
    """
    if name in _LAZY_EXPORTS:
        from tempest_fastapi_sdk.integrations.payment.stripe import events

        value = getattr(events, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the module's attributes, lazy names included.

    Returns:
        list[str]: Sorted attribute names, so autocomplete offers the
        lazily-resolved enum before anything touches it.
    """
    return sorted(set(globals()) | _LAZY_EXPORTS)


__all__: list[str] = [
    "DEFAULT_TOLERANCE_SECONDS",
    "STRIPE_API_VERSION",
    "STRIPE_BASE_URL",
    "STRIPE_IDEMPOTENCY_HEADER",
    "STRIPE_SIGNATURE_HEADER",
    "STRIPE_VERSION_HEADER",
    "THREE_DECIMAL_CURRENCIES",
    "ZERO_DECIMAL_CURRENCIES",
    "StripeCheckoutSession",
    "StripeClient",
    "StripeCustomer",
    "StripeDeleted",
    "StripeError",
    "StripeEvent",
    "StripeEventObject",
    "StripeInvoice",
    "StripeList",
    "StripeObject",
    "StripePaymentIntent",
    "StripePrice",
    "StripeProduct",
    "StripeRefund",
    "StripeResource",
    "StripeSubscription",
    "StripeWebhookEvent",
    "currency_exponent",
    "format_amount",
    "from_minor_units",
    "make_stripe_webhook_dependency",
    "parse_event",
    "parse_signature_header",
    "sign_payload",
    "stripe_http_client",
    "to_minor_units",
    "verify_signature",
]
