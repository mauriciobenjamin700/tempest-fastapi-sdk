# Stripe (cards, subscriptions, Checkout)

Every service of ours that charges a card used to rewrite the same Stripe
layer: a synchronous HTTP client called from inside an async route,
hand-retyped schemas, and a webhook verification each one derived its own
way. `tempest_fastapi_sdk.integrations.payment.stripe` ships that layer.

!!! info "Installation"
    No extra. The integration uses the `HTTPClient` the SDK already has —
    `uv add "tempest-fastapi-sdk[http]"` if you have not pulled `httpx`
    in yet.

!!! info "When to use this"
    International card payments, subscriptions or hosted Checkout. For
    **Pix**, use [OpenPix](openpix.en.md), which is generated from the
    provider's specification.

## The minimal path

```python
import asyncio
from decimal import Decimal

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
    to_minor_units,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    intent = await client.payment_intents.create(
        {
            "amount": to_minor_units(Decimal("199.90"), "brl"),
            "currency": "brl",
            "automatic_payment_methods": {"enabled": True},
            "metadata": {"order_id": "1042"},
        }
    )
    print(intent.id, intent.status, intent.client_secret)


asyncio.run(main())
```

Three things happened without being asked for: the body went out
**form-encoded** in bracket notation (`metadata[order_id]=1042`), the
request carried an `Idempotency-Key`, and the `Stripe-Version` header
pinned the API version.

!!! danger "`client_secret` is a credential"
    It authorizes confirming **that** payment. It belongs in the response
    to the order's own owner and nowhere else — never in a log, never in
    telemetry.

## How it works, piece by piece

### The body is form-encoded, not JSON

Stripe **accepts no JSON** on writes: all 588 write operations in the
specification declare `application/x-www-form-urlencoded`. Nesting is
brackets:

```python
from tempest_fastapi_sdk import form_encode

print(
    form_encode(
        {
            "mode": "payment",
            "line_items": [{"price": "price_123", "quantity": 2}],
            "metadata": {"order_id": "1042"},
        }
    )
)
```

```text
{'mode': 'payment',
 'line_items[0][price]': 'price_123',
 'line_items[0][quantity]': '2',
 'metadata[order_id]': '1042'}
```

`form_encode` is public and serves any form-encoded API — and **the SDK's
integration generator now uses the same function**: an operation whose
`requestBody` declares form comes out with `data=form_encode(payload)`
instead of `json=payload`. Before that, a client generated against Stripe
had 100% of its writes rejected.

What `form_encode` decides for you:

| Value | Goes as | Why |
| --- | --- | --- |
| `True` / `False` | `true` / `false` | `str(True)` would send `"True"` |
| `None` | **not sent** | an empty string is a real value — it *clears* the field |
| `Decimal("10.50")` | `10.50` | going through `float` loses a cent |
| `Enum` | its `.value` | `str()` would send `"Class.MEMBER"` |
| `datetime` | ISO-8601 | |

### Money: zero-decimal currencies exist

Stripe charges in the **smallest unit**. For most currencies that is a
cent, but for JPY, KRW, VND and 13 others the smallest unit **is** the
unit — and dividing everything by 100 is a silent billing bug no BRL-only
test will catch.

```python
from decimal import Decimal

from tempest_fastapi_sdk.integrations.payment.stripe import (
    from_minor_units,
    to_minor_units,
)

print(to_minor_units(Decimal("10.50"), "brl"))    # 1050
print(to_minor_units(Decimal("1050"), "jpy"))     # 1050  <- not 105000
print(from_minor_units(1050, "jpy"))              # Decimal('1050')
print(to_minor_units(Decimal("10.505"), "bhd"))   # 10505 (three decimals)
```

Both tables (`ZERO_DECIMAL_CURRENCIES`, `THREE_DECIMAL_CURRENCIES`) come
from Stripe's documentation and are pinned by a test, so an upstream
change shows up as a failure here — not as a hundredfold charge.

### Webhooks: what Stripe signs is not the body

The header is `t=<timestamp>,v1=<hmac>`, and the HMAC covers
`f"{t}.{body}"`. Signing the body alone is the most common mistake in a
hand-rolled verification — and it fails *silently*, as a 401 on every
delivery.

```python
from typing import Any

from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeEvent,
    StripeWebhookEvent,
    make_stripe_webhook_dependency,
)

from src.core.settings import settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
verified = make_stripe_webhook_dependency(settings.STRIPE_WEBHOOK_SECRET)


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(
    event: StripeWebhookEvent = Depends(verified),
) -> dict[str, Any]:
    """Receive an already verified, typed delivery."""
    if event.event is StripeEvent.PAYMENT_INTENT_SUCCEEDED:
        return {"handled": True, "intent": event.data_object["id"]}
    return {"handled": False, "type": event.event_type}
```

- An invalid, missing or out-of-window (5 minutes) signature is a **401**
  before your handler runs.
- Secret rotation works: Stripe sends one `v1` per active secret and any
  match is accepted.
- An unknown event type does **not** take the route down — `event` is
  `None` and `event_type` keeps the string. A Stripe release does not
  become your incident.
- `event_id` is there to **deduplicate**: deliveries repeat by design.

!!! warning "A verified signature is not authorization"
    It proves the delivery came from Stripe. It does not prove the state
    you are about to change is current — deliveries repeat and arrive out
    of order. Before releasing goods on `payment_intent.succeeded`,
    re-read the intent through the API.

So a test never re-derives the signature wrongly, the module exports the
signer:

```python
import json

from tempest_fastapi_sdk.integrations.payment.stripe import sign_payload

body = json.dumps({"id": "evt_1", "type": "payment_intent.succeeded"}).encode()
header = sign_payload(body, "whsec_test", timestamp=1_770_000_000)
```

### The 265 event types, as an enum

`StripeEvent` is **generated** from the specification — from the
`enabled_events` parameter of `POST /v1/webhook_endpoints`, which is
where Stripe enumerates the types (the `event` object does not). A drift
test fails if the file is edited by hand.

```python
from tempest_fastapi_sdk.integrations.payment.stripe import StripeEvent

print(StripeEvent.INVOICE_PAID.value)               # invoice.paid
print(StripeEvent.has_value("customer.created"))    # True
```

### Idempotency: why every write carries a key

A timeout on `POST /v1/payment_intents` leaves you not knowing whether
money moved. A retry without a key creates a second payment; with one,
Stripe replays the original response for 24 hours. The client puts a
UUID4 on every write — swap in your own when you want the window to cover
your flow:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    order_id = "1042"
    await client.payment_intents.create(
        {"amount": 19990, "currency": "brl"},
        idempotency_key=f"order-{order_id}",
    )


asyncio.run(main())
```

### Cursor pagination

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    filters = {"created": {"gte": 1_770_000_000}}
    async for customer in client.customers.auto_paginate(filters):
        print(customer.id, customer.email)


asyncio.run(main())
```

The cursor is the **last item's id**, not an offset, so the walk stays
correct while objects are created underneath it.

### Errors that say what happened

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    StripeError,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    try:
        await client.payment_intents.create({"amount": 19990, "currency": "brl"})
    except StripeError as error:
        print(error.status_code)    # 402
        print(error.error_type)     # card_error
        print(error.code)           # card_declined
        print(error.decline_code)   # insufficient_funds
        print(error.request_id)     # req_... (what Stripe support asks for)


asyncio.run(main())
```

### Thin models, nothing lost

The models name the fields that carry decisions — `status`, `amount`,
`currency`, the ids linking objects — and set `extra="allow"`. The rest
of the object stays reachable:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    intent = await client.payment_intents.retrieve("pi_123")
    print(intent.status)                                  # typed
    print((intent.model_extra or {}).get("next_action"))  # preserved


asyncio.run(main())
```

An expandable field (`customer`, `payment_intent`, …) is a `str`: without
`expand`, Stripe sends the id. Ask for the object and read it from
`model_extra`:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    intent = await client.payment_intents.retrieve(
        "pi_123", params={"expand": ["customer"]}
    )
    print(intent.customer)


asyncio.run(main())
```

!!! note "Why this client is hand-written rather than generated"
    The SDK generates integrations from OpenAPI — OpenPix is generated.
    Stripe's specification does not survive the trip, and that was
    **measured** on `2026-07-29.dahlia`:

    - generating the full surface yields a `schemas.py` of **3.3 MB / 81k
      lines**, and importing it costs **5.8 s and 492 MB of RSS**;
    - slicing by resource does not help: `/v1/prices` **alone** reaches
      864 of the 1440 schemas, and the ten core resources together reach
      1020. There is no small subset.

    So what comes from the specification still comes from it — API
    version, base URL and the 265 events, via `make stripe-fetch` — and
    the rest is code that fits in your head. The numbers, and how to
    reproduce them, are in `scripts/regen_stripe.py`.

## Configuration

```python
from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


class Settings(BaseAppSettings):
    """Service settings."""

    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""


settings = Settings()
client = StripeClient(stripe_http_client(settings.STRIPE_API_KEY))
```

The webhook secret is **per endpoint**, not per account: a service with a
test and a live endpoint has two.

## Testing

Inject an `httpx.MockTransport` and inspect the request that would go
out:

```python
from urllib.parse import parse_qs

import httpx

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)

requests: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    """Record the request and answer with a fake customer."""
    requests.append(request)
    return httpx.Response(200, json={"id": "cus_1", "object": "customer"})


async def test_create_customer() -> None:
    """The write goes out form-encoded, in bracket notation."""
    client = StripeClient(
        stripe_http_client("sk_test_x", transport=httpx.MockTransport(handler))
    )

    await client.customers.create({"email": "ana@example.com", "metadata": {"o": "1"}})

    assert parse_qs(requests[-1].content.decode()) == {
        "email": ["ana@example.com"],
        "metadata[o]": ["1"],
    }
```

## Recap

- `StripeClient` over the SDK's `HTTPClient`: retry, circuit breaker and
  the pinned `Stripe-Version` come from one place.
- Writes are form-encoded in bracket notation (`form_encode`, public and
  now used by the generator too) and carry an `Idempotency-Key` by
  default.
- Money respects zero-decimal and three-decimal currencies.
- Webhooks verify over `t.body`, with a replay window, secret rotation,
  and an unknown event type that does not take the route down.
- 265 events as an enum, generated from the specification with a drift
  test.
- Thin models with `extra="allow"` — the client is hand-written because
  generating the whole specification costs 3.3 MB and 492 MB of RSS,
  measured.
