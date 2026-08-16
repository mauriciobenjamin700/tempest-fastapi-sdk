# OpenPix (Pix via Woovi)

This recipe builds a service that **opens a Pix charge, finds out when it was
paid, and releases the order** — with the same layered architecture the rest
of the SDK uses, and without writing a single HTTP call by hand.

By the end you will have:

- a `POST /api/checkout` returning the BR Code for the app to draw the QR;
- a `POST /webhooks/openpix` receiving the payment notification **already
  verified**;
- a read-back through the API before releasing anything;
- a reconciliation job for the charges the webhook never delivered.

Subscriptions and plans (monthly billing, Pix Automático) live in the recipe
next door: [OpenPix (subscriptions and plans)](openpix-subscriptions.md).

## What already ships in the package

Installing the SDK gives you the whole of OpenPix: **373 schemas** and **105
operations** generated from the specification, plus the four things the
specification does not say (environments, webhook events, signature
verification, and cents).

```bash
uv add "tempest-fastapi-sdk[http]"
uv add cryptography
```

`[http]` brings `HTTPClient`, the transport the generated client rides on.
`cryptography` is what verifies the webhook signature — without it the module
imports fine and only fails on the first real delivery, in production.

!!! tip "Need another API the SDK does not bundle?"
    This module exists because OpenPix is common enough that every service was
    generating the same client. For any other API the generator is still the
    right tool: see [Integration client (OpenAPI)](openapi-client.md).

## Configuration

```python
from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEnvironment


class Settings(BaseAppSettings):
    """Service settings."""

    OPENPIX_APP_ID: str = ""
    OPENPIX_ENVIRONMENT: str = "sandbox"

    @property
    def openpix_environment(self) -> OpenPixEnvironment:
        """Resolve the configured environment.

        Returns:
            OpenPixEnvironment: Production when `OPENPIX_ENVIRONMENT` is
            `production`, sandbox otherwise.
        """
        if self.OPENPIX_ENVIRONMENT == "production":
            return OpenPixEnvironment.PRODUCTION
        return OpenPixEnvironment.SANDBOX


settings = Settings()
```

!!! warning "The two environments are different domains"
    Production is `api.openpix.com.br`. Testing is `api.woovi-sandbox.com` — a
    different domain, not a subdomain. Neither spells the other, and an AppID
    from one is worthless in the other. That is why `OpenPixEnvironment`
    exists instead of a string in `.env`.

## The suggested architecture

OpenPix enters through the service layer, behind a dependency. No router
builds a payload, no service opens an `HTTPClient`.

```text
src/
├── core/
│   └── settings.py              # OPENPIX_APP_ID + environment
├── api/
│   ├── dependencies/
│   │   └── payments.py          # builds HTTPClient -> OpenPixClient -> service
│   └── routers/
│       ├── checkout.py          # POST /api/checkout        (JSON, in the schema)
│       └── webhooks.py          # POST /webhooks/openpix    (include_in_schema=False)
├── controllers/
│   └── checkout.py              # orchestrates order + charge
├── services/
│   ├── openpix.py               # charge rules: open, confirm, refund
│   └── orders.py                # your order, which knows nothing about Pix
└── db/
    ├── models/order.py          # order status + correlation_id
    └── repositories/order.py
```

| Layer | May import | Never imports |
| --- | --- | --- |
| `api/routers` | `controllers`, `schemas` | `OpenPixClient`, `db` |
| `controllers` | `services`, `schemas` | `OpenPixClient` |
| `services` | `OpenPixClient`, `db/repositories` | `fastapi` |
| `api/dependencies` | everything above, to wire it | — |

Three decisions worth spelling out:

1. **There is one `HTTPClient`, created in the lifespan.** It carries the
   connection pool, the retry policy and a per-host circuit breaker. Building
   one per request throws all three away and opens a fresh socket on every
   checkout.
2. **`correlationID` is your primary key on the OpenPix side.** Use the order
   id, not a fresh UUID: it is what ties the webhook, the read-back and the
   refund to a row in your database.
3. **The webhook lives in its own router**, outside the OpenAPI schema. It is
   not part of your public API, and it authenticates with a signature, not
   with your session token.

### The dependency

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient

from src.core.settings import settings
from src.services.openpix import OpenPixService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open one HTTP client for the whole process and close it on shutdown.

    Args:
        app (FastAPI): The application, where the client is kept.

    Yields:
        None: While the application serves requests.
    """
    http: HTTPClient = HTTPClient(
        base_url=settings.openpix_environment.base_url,
        default_headers={"Authorization": settings.OPENPIX_APP_ID},
        timeout=15.0,
    )
    app.state.openpix = OpenPixClient(http)
    try:
        yield
    finally:
        await http.aclose()


def get_openpix_service(request: Request) -> OpenPixService:
    """Hand the route a ready charge service.

    Args:
        request (Request): The current request.

    Returns:
        OpenPixService: The service, over the lifespan's client.
    """
    return OpenPixService(request.app.state.openpix)
```

!!! info "The AppID goes in the raw `Authorization` header"
    No `Bearer`, no `Basic`. It is the string the OpenPix dashboard shows, and
    it is account-wide. `default_headers` puts it on every request the client
    makes.

## Flow 1 — open the charge

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    Charge,
    ChargePayload,
    CustomerPayload,
    OpenPixClient,
    reais_to_cents,
)


class OpenPixService:
    """Pix charge rules."""

    def __init__(self, client: OpenPixClient) -> None:
        """Store the generated client.

        Args:
            client (OpenPixClient): The OpenPix client.
        """
        self.client: OpenPixClient = client

    async def open_charge(
        self,
        *,
        reference: str,
        amount_brl: str,
        customer_name: str,
        customer_email: str,
    ) -> Charge:
        """Open a Pix charge for an order.

        Args:
            reference (str): The order id, used as the `correlationID`.
            amount_brl (str): Amount in reais, as text ("19.90").
            customer_name (str): The payer's name.
            customer_email (str): The payer's email.

        Returns:
            Charge: The created charge, carrying the BR Code for the QR.

        Raises:
            ValueError: If the response comes back without the charge.
        """
        response = await self.client.post_api_v1_charge(
            body=ChargePayload(
                correlation_id=reference,
                value=reais_to_cents(amount_brl),
                comment=f"Order {reference}",
                customer=CustomerPayload(
                    name=customer_name,
                    email=customer_email,
                ),
                expires_in=3600,
            ),
            return_existing=True,
        )
        if response.charge is None:
            raise ValueError(f"OpenPix returned no charge for {reference}")
        return response.charge
```

The router only forwards and picks what the app needs:

```python
from typing import Any

from fastapi import APIRouter, Depends

from src.api.dependencies.payments import get_openpix_service
from src.services.openpix import OpenPixService

router: APIRouter = APIRouter(prefix="/api", tags=["checkout"])


@router.post("/checkout")
async def checkout(
    service: OpenPixService = Depends(get_openpix_service),
) -> dict[str, Any]:
    """Open the order's charge and return what the app draws.

    Args:
        service (OpenPixService): The charge service.

    Returns:
        The BR Code, the QR image and the payment link.
    """
    charge = await service.open_charge(
        reference="order-1",
        amount_brl="19.90",
        customer_name="Ana",
        customer_email="ana@example.com",
    )
    return {
        "br_code": charge.br_code,
        "qr_code_image": charge.qr_code_image,
        "payment_link_url": charge.payment_link_url,
        "status": charge.status,
    }
```

Run against the API, the route answers:

```json
{
  "br_code": "00020101021226830014BR.GOV.BCB.PIX...",
  "qr_code_image": "https://api.openpix.com.br/openpix/charge/brcode/image/x.png",
  "payment_link_url": "https://openpix.com.br/pay/order-1",
  "status": "ACTIVE"
}
```

That is three ways to present the same charge, and the interface picks:

| Field | What it is | When to use it |
| --- | --- | --- |
| `br_code` | The Pix EMV string | Your own app: draw the QR and offer copy-paste |
| `qr_code_image` | URL of a PNG of the QR | Plain page, email, PDF |
| `payment_link_url` | Payment page hosted by OpenPix | When you would rather build no screen at all |

!!! tip "Construct with the Python name — the type-checker agrees"
    Generated fields carry the wire name in `validation_alias` +
    `serialization_alias`, not in `alias`. The difference does not show at
    runtime, and does show in your editor: with `alias`, pyright renames the
    parameter and rejects `ChargePayload(correlation_id=...)`, asking for
    `correlationID`. Measured with basedpyright: with the split form, **0
    errors** for the Python name, while `model_validate` and
    `model_dump(by_alias=True)` keep speaking OpenPix's spelling.

!!! tip "`return_existing=True` makes the call idempotent"
    Without it, a second POST with the same `correlationID` is an error. With
    it, OpenPix returns the charge that already exists — which is what you
    want when the user taps "pay" twice or the app retries.

!!! note "`expires_in` is in seconds, five minutes minimum"
    OpenPix defaults to a long-lived charge. If your order holds stock, close
    that window: `expires_in=3600` expires in an hour and the charge leaves
    "awaiting payment" on its own.

## Flow 2 — find out whether it was paid

There are three paths, and they are not alternatives: they play different
roles.

| Path | What it is | Role |
| --- | --- | --- |
| `CHARGE_COMPLETED` webhook | OpenPix tells you | **Notice.** Fast, but it arrives over the open internet |
| `get_api_v1_charge_by_id` | You ask | **Fact.** This is what authorizes releasing |
| `get_api_v1_charge(status=...)` | You sweep | **Safety net.** Catches what the webhook lost |

The rule that sums it up: **the webhook notifies, the API confirms.**

### The webhook, verified

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.integrations.payment.openpix import (
    Charge,
    OpenPixEvent,
    OpenPixWebhookEvent,
    make_openpix_webhook_dependency,
)

from src.api.dependencies.payments import get_openpix_service
from src.services.openpix import OpenPixService

router: APIRouter = APIRouter(prefix="/webhooks", include_in_schema=False)
verify = make_openpix_webhook_dependency()


@router.post("/openpix")
async def receive_openpix(
    event: OpenPixWebhookEvent = Depends(verify),
    service: OpenPixService = Depends(get_openpix_service),
) -> dict[str, str]:
    """Take a verified delivery and confirm it before releasing.

    Args:
        event (OpenPixWebhookEvent): The verified, decoded delivery.
        service (OpenPixService): The charge service.

    Returns:
        An acknowledgement, so OpenPix stops redelivering.
    """
    if event.event is not OpenPixEvent.CHARGE_COMPLETED:
        return {"status": "ignored", "event": event.event_name}

    charge = Charge.model_validate(event.payload["charge"])
    reference = charge.correlation_id or ""
    if not await service.is_paid(reference):
        return {"status": "not-settled"}

    await service.release(reference)
    return {"status": "released"}
```

The dependency does three things before the route body runs: it checks the RSA
signature in the `x-webhook-signature` header, decodes the JSON, and resolves
the `event` string into an `OpenPixEvent` member. What is left in
`event.payload` is the raw dict — you validate only the branch you care about.

Measured with that router running (test key, signed body):

| Delivery | Response |
| --- | --- |
| No signature header | **401** |
| Valid signature, `OPENPIX:CHARGE_COMPLETED` | 200 `{"status": "released"}` |
| The same delivery again | 200 `{"status": "duplicate"}` |
| An event this SDK does not know | 200 `{"status": "ignored", "event": "..."}` |

!!! danger "OpenPix's public key is RSA-1024"
    Verified by loading it into `cryptography`: 1024 bits, exponent 65537 —
    **below the 2048-bit floor** NIST has recommended since 2013. That caps
    what the signature can prove.

    Treat a valid signature as evidence the delivery came from OpenPix, **not
    as authorization to move money**. What authorizes is the read-back through
    the API — exactly the `service.is_paid` above. Nothing here raises the
    key's strength; the mitigation is not trusting it beyond what it is.

!!! warning "Replay and repeat delivery"
    The signature covers the body and nothing else, so a captured delivery
    stays valid forever — and OpenPix itself redelivers when it does not get a
    200. Treat the handler as **idempotent**: key on `correlationID` and
    ignore what you already processed. See [Idempotency](idempotency.md).

### The confirmation

```python
from tempest_fastapi_sdk.integrations.payment.openpix import ChargeStatus


async def is_paid(self, reference: str) -> bool:
    """Ask the API whether the charge is settled.

    Args:
        reference (str): The charge's `correlationID`.

    Returns:
        bool: `True` only when OpenPix answers `COMPLETED`.
    """
    response = await self.client.get_api_v1_charge_by_id(id=reference)
    charge = response.charge
    return charge is not None and charge.status == ChargeStatus.COMPLETED
```

!!! warning "Compare `status` with `==`, never with `is`"
    The generated models inherit `BaseSchema`, which sets
    `use_enum_values=True`: the field arrives as a `str`, not as an enum
    member. Measured: `charge.status == ChargeStatus.COMPLETED` is `True`,
    `charge.status is ChargeStatus.COMPLETED` is **`False`** — on every
    delivery, silently. `ChargeStatus` is a `str` enum, so `==` works with the
    member **and** with the literal string.

    The three values are `ACTIVE`, `COMPLETED` and `EXPIRED`.

### Reconciliation

A webhook is a network call: one delivery will be lost. A periodic job sweeps
what fell behind — note that it lists what is **still open** on the OpenPix
side, to be crossed against what your database believes is open:

```python
from datetime import UTC, datetime, timedelta

from tempest_fastapi_sdk.integrations.payment.openpix import ChargeStatus, OpenPixClient


async def sweep_pending(client: OpenPixClient) -> list[str]:
    """List the charges still open in the last 24 hours.

    Args:
        client (OpenPixClient): The OpenPix client.

    Returns:
        The `correlationID`s still awaiting payment.
    """
    now = datetime.now(UTC)
    response = await client.get_api_v1_charge(
        start=now - timedelta(days=1),
        end=now,
        status=ChargeStatus.ACTIVE,
    )
    return [charge.correlation_id or "" for charge in response.charges]
```

Every order your database holds as "awaiting" that does **not** appear in that
list ended some other way: it was either paid (and the webhook was lost) or it
expired. Read each one with `get_api_v1_charge_by_id` and close the case.

!!! note "The listing has no pagination in the specification"
    `GET /api/v1/charge` accepts `start`, `end`, `status`, `customer` and
    `subscription` — and nothing else. The response carries `page_info`, but
    the specification declares no `skip`/`limit`, so the generated client does
    not expose them. For wide windows, sweep in smaller time slices.

## Flow 3 — change the deadline, or give up

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargePatchPayload,
    OpenPixClient,
)


async def extend(client: OpenPixClient, reference: str, until: str) -> None:
    """Push out the expiration of an open charge.

    Args:
        client (OpenPixClient): The OpenPix client.
        reference (str): The charge's `correlationID`.
        until (str): New expiration date, ISO 8601.
    """
    await client.patch_api_v1_charge_by_id(
        id=reference,
        body=ChargePatchPayload(expires_date=until),
    )


async def cancel(client: OpenPixClient, reference: str) -> None:
    """Cancel a charge that will not be paid.

    Args:
        client (OpenPixClient): The OpenPix client.
        reference (str): The charge's `correlationID`.
    """
    await client.delete_api_v1_charge_by_id(id=reference)
```

`patch` only touches the expiration — it is the single field
`ChargePatchPayload` has. To change the amount, open another charge.

## Flow 4 — refund

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargeRefundPayload,
    OpenPixClient,
    reais_to_cents,
)


async def refund(
    client: OpenPixClient,
    *,
    reference: str,
    refund_reference: str,
    amount_brl: str,
) -> None:
    """Give the money back for a paid charge.

    Args:
        client (OpenPixClient): The OpenPix client.
        reference (str): The paid charge's `correlationID`.
        refund_reference (str): Your key for this refund.
        amount_brl (str): How much to return, in reais.
    """
    await client.post_api_v1_charge_by_id_refund(
        id=reference,
        body=ChargeRefundPayload(
            correlation_id=refund_reference,
            value=reais_to_cents(amount_brl),
            comment="Order cancelled",
        ),
    )
```

A refund has its own `correlationID` — it is a record of yours, separate from
the charge. `get_api_v1_charge_by_id_refund` lists a charge's refunds, and the
amount is optional: omit it and OpenPix returns the full value.

## Money: whole cents, not floats

The specification says, in those words, *"Value in cents of this charge"* — and
then types the field `number`. The generated model therefore validates `1990`
into the float `1990.0`. Money that has been through a float is money that can
be wrong: add a few of them and you get `0.30000000000000004`.

```python
from decimal import Decimal

from tempest_fastapi_sdk.integrations.payment.openpix import (
    cents_to_reais,
    reais_to_cents,
    to_cents,
)

assert reais_to_cents("19.90") == 1990
assert to_cents(1990.0) == 1990
assert cents_to_reais(1990) == Decimal("19.90")
```

- **`reais_to_cents`** is what you use when *creating*: it takes reais and
  returns cents. It rounds half-up (`0.005` -> `1`), which is what a person
  expects from money and **not** what the built-in `round` does — that rounds
  half to even, and `round(0.005 * 100)` gives `0`.
- **`to_cents`** is what you use when *reading*: it narrows the float the API
  returned into an exact `int`. It **refuses a fraction on purpose** —
  `to_cents(19.9)` raises `ValueError`, because the field is already in cents
  and a fraction means someone is treating reais as cents. Rounding silently
  would hide that behind a plausible number.
- **`cents_to_reais`** returns a `Decimal`, so the value stays exact all the
  way to the formatting call.

## Registering the webhook with OpenPix

You can register it in the dashboard, or through the API — which keeps the
address versioned alongside the deploy:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    PostApiV1WebhookBody,
    WebhookEventEnum,
    WebhookPayload,
)


async def register_webhook(client: OpenPixClient, url: str) -> None:
    """Subscribe the charge-paid event to a URL.

    Args:
        client (OpenPixClient): The OpenPix client.
        url (str): The public address of `POST /webhooks/openpix`.
    """
    await client.post_api_v1_webhook(
        body=PostApiV1WebhookBody(
            webhook=WebhookPayload(
                name="charge-paid",
                event=WebhookEventEnum.OPENPIX_CHARGE_COMPLETED,
                url=url,
                is_active=True,
            )
        )
    )
```

!!! note "The event prefix is not uniform, and that is OpenPix's doing"
    `OpenPixEvent` carries all 28 events verbatim. Charge, transaction,
    movement and dispute carry the `OPENPIX:` namespace
    (`OpenPixEvent.CHARGE_COMPLETED.value == "OPENPIX:CHARGE_COMPLETED"`). The
    Pix-automatic and account-register families do **not**
    (`OpenPixEvent.PIX_AUTOMATIC_APPROVED.value == "PIX_AUTOMATIC_APPROVED"`).
    It looks like a transcription slip; it is not — and that is why a test
    pins both cases.

## Testing without the network

The client takes its transport by injection, so an `httpx.MockTransport`
exercises the whole flow without leaving the machine:

```python
import httpx

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargePayload,
    OpenPixClient,
)

seen: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    """Record the request and answer with a canned charge.

    Args:
        request (httpx.Request): The request that would go out.

    Returns:
        httpx.Response: The canned response.
    """
    seen.append(request)
    return httpx.Response(
        200,
        json={
            "charge": {"status": "ACTIVE", "correlationID": "order-1", "value": 1990},
            "correlationID": "order-1",
            "brCode": "00020101021226830014BR.GOV.BCB.PIX...",
        },
    )


async def test_charge_carries_the_customer() -> None:
    """The customer data reaches the request body."""
    http = HTTPClient(
        base_url="https://api.woovi-sandbox.com",
        transport=httpx.MockTransport(handler),
    )
    client = OpenPixClient(http)

    await client.post_api_v1_charge(
        body=ChargePayload(correlation_id="order-1", value=1990)
    )

    assert seen[-1].url.path == "/api/v1/charge"
```

For the webhook, inject a verifier over a test key pair instead of OpenPix's
key:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    make_openpix_webhook_dependency,
    webhook_verifier,
)

verify = make_openpix_webhook_dependency(
    verifier=webhook_verifier(public_key_pem="-----BEGIN PUBLIC KEY-----\n...")
)
```

## The module's two halves

It is worth knowing which part came from where, because they are maintained
differently:

| Half | What it is | Where it comes from |
| --- | --- | --- |
| **Generated** | `OpenPixClient`, `DEFAULT_BASE_URL`, 373 schema classes | The spec, verbatim |
| **Hand-written** | `OpenPixEnvironment`, `OpenPixEvent`, the webhook, the money helpers | What the spec does **not** say |

!!! info "The generated half is checked in, not written by hand"
    `scripts/regen_openpix.py` produces `schemas.py` and `client.py` from the
    specification pinned in `vendor/openpix-openapi.yaml`, and **a test fails
    if the files on disk drift** from what the script produces. To update when
    OpenPix changes the API: swap the file in `vendor/`, run `make
    openpix-regen`, and the diff shows exactly what the third party changed.

!!! note "The models load on first use, not on import"
    Building 373 Pydantic models costs the better part of a second. Importing
    the package just for `to_cents` should not pay that, so the generated half
    resolves through [PEP 562](https://peps.python.org/pep-0562/).

    Measured on this machine (Python 3.11, with `tempest_fastapi_sdk` already
    imported): **~11 ms** to import the subpackage, **~150 ms** on the first
    access to a generated name, **~0.02 ms** after that. The numbers move with
    the machine; what does not move is the ratio between them — code that only
    calls `to_cents` never pays the 150 ms.

## Recap

1. **One `HTTPClient` per process**, created in the lifespan, with the AppID in
   `default_headers` and the base URL from `OpenPixEnvironment`.
2. **`correlationID` is your order id** — it is what ties creation, webhook,
   read-back and refund together.
3. **Opening the charge** is `post_api_v1_charge` with `return_existing=True`;
   the response carries `br_code`, `qr_code_image` and `payment_link_url`, and
   the interface picks between them.
4. **The webhook notifies, the API confirms.**
   `make_openpix_webhook_dependency()` verifies and hands over the typed
   event; `get_api_v1_charge_by_id` is what authorizes releasing — OpenPix's
   key is RSA-1024.
5. **The handler is idempotent**, because the same delivery arrives more than
   once.
6. **A reconciliation job** sweeps `status=ACTIVE` and closes what the webhook
   lost.
7. **Money in whole cents**: `reais_to_cents` to create, `to_cents` to read,
   `cents_to_reais` to display.
8. **Compare `status` with `==`**, never with `is` — the field arrives as a
   `str`.
