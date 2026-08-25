# Pix protocol: one contract, many providers

Your service charges over Pix. Today the provider is OpenPix; tomorrow it
might be Mercado Pago, because the fee changed or because your customer
already has an account there.

If your service speaks the provider's language, that switch rewrites the
service. This recipe shows how not to speak it.

## The problem, concretely

The two providers the SDK already bundles disagree on nearly everything
that matters:

| | OpenPix | Mercado Pago |
| --- | --- | --- |
| amount | cents, in a `float` | reais, in a `float` |
| states | `ACTIVE`, `COMPLETED`, `EXPIRED` | 9 in `Payment`, 5 in `Order`, 4 in `OrderTransactionPayment` |
| copy-and-paste code | `brCode` | `qr_code` |
| QR image | a **URL** | **Base64** |
| your reference | `correlationID` | `external_reference` |

Writing `if charge.status == "COMPLETED"` does not couple your code to Pix.
It couples it to OpenPix.

## The contract

```python
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPaymentEvent,
    PixPayer,
    PixProvider,
)
```

!!! info "No extra for the contract"
    Measured on an install with no extras: the import above and
    `OpenPixPixProvider` both resolve from the core. Extras are each
    provider's HTTP client's business, not the contract's.

`PixProvider` is one attribute and four methods — this is
`integrations/payment/base.py`, unsimplified:

```text
provider_name: str

async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge
async def get_pix_charge(self, charge_id: str) -> PixCharge
async def cancel_pix_charge(self, charge_id: str) -> PixCharge
def parse_webhook(self, event: Any) -> PixPaymentEvent
```

A `Protocol`, not a base class: an adapter satisfies it **by shape**, with
nothing to inherit — the same seam the SDK uses for `RateLimitStore`,
`QuotaStore`, `ModerationBackend` and `PushDispatcher`.

!!! note "Why it is not `runtime_checkable`"
    `isinstance` against a runtime-checkable protocol only checks that the
    **names** exist: an adapter whose `create_pix_charge` takes the wrong
    arguments would pass the check and fail the charge. What actually checks
    is `tests/integrations/payment/test_contract.py`, comparing
    `inspect.signature` — and your type-checker, if you declare the type as
    `PixProvider` (the next section shows where).

### What goes in: `PixChargeRequest`

| field | type | for |
| --- | --- | --- |
| `amount_cents` | `int` | amount in cents — an **integer**, never a `float` |
| `reference` | `str` | your identifier; comes back in `PixCharge.reference` |
| `description` | `str` or `None` | the text the payer sees |
| `expires_in` | `timedelta` or `None` | the payment window |
| `payer` | `PixPayer` or `None` | payer details, where the provider accepts them |

### What comes out: `PixCharge`

| field | type | for |
| --- | --- | --- |
| `provider` | `str` | who issued it (copied from `provider_name`) |
| `provider_charge_id` | `str` | **the id you store** — it is the argument to `get_pix_charge` and `cancel_pix_charge` |
| `reference` | `str` | your own identifier, back again |
| `amount_cents` | `int` | amount, in cents |
| `currency` | `str` | ISO 4217, defaults to `BRL` |
| `status` | `PaymentStatus` | the state you branch on |
| `provider_status` | `str` | the state as the provider names it, raw |
| `br_code` | `str` or `None` | the EMV copy-and-paste string |
| `qr_code_image_url` | `str` or `None` | QR as a URL (what OpenPix returns) |
| `qr_code_base64` | `str` or `None` | QR as Base64 (what Mercado Pago returns) |
| `end_to_end_id` | `str` or `None` | the Pix settlement identifier |
| `expires_at` | `datetime` or `None` | when the window closes |
| `paid_at` | `datetime` or `None` | when it settled |
| `raw` | `dict[str, Any]` | everything the provider said beyond this |

!!! tip "The QR arrives in both formats because the providers disagree"
    The contract carries `qr_code_image_url` **and** `qr_code_base64`, and
    fills in whichever the provider delivers. Your template reads whichever
    is there, instead of knowing which provider is behind it.

## Charging

A whole program, from the HTTP client to the charge:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixChargeRequest,
    PixProvider,
)
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    OpenPixEnvironment,
)


async def charge_it(provider: PixProvider) -> None:
    """Emit a Pix charge and print what the payer needs to see.

    Args:
        provider (PixProvider): Any provider that implements the contract.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference="order-1042",
            description="Order 1042",
        )
    )

    print(charge.status is PaymentStatus.PENDING)
    print(charge.br_code)
    print(charge.provider)


async def main() -> None:
    """Wire the OpenPix client and charge through the contract."""
    http: HTTPClient = HTTPClient(
        base_url=OpenPixEnvironment.SANDBOX.base_url,
        default_headers={"Authorization": "<your AppID>"},
    )
    await charge_it(OpenPixPixProvider(OpenPixClient(http)))


if __name__ == "__main__":
    asyncio.run(main())
```

Look at the type of `charge_it`: it takes `PixProvider`, not
`OpenPixPixProvider`. That is the whole difference between a portable
service and a coupled one.

!!! tip "The amount is an `int`, always"
    `amount_cents` is an integer number of cents. Both providers type money
    as `number` in their specification — and money that has been through a
    `float` is money that can be wrong. Converting to whatever unit each
    provider expects is the adapter's problem.

## Reading it back, and cancelling

Creating is a quarter of the contract. The rest of the cycle uses the
`provider_charge_id` the charge came back with — store that field next to
your order, because it is the only way back to the provider:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixProvider,
)


async def charge_and_follow(provider: PixProvider) -> PixCharge:
    """Create a charge, read it back, and withdraw it if it is still open.

    Args:
        provider (PixProvider): Any provider that implements the contract.

    Returns:
        PixCharge: The charge in its final observed state.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="order-1042"),
    )
    charge_id: str = charge.provider_charge_id

    current = await provider.get_pix_charge(charge_id)
    if current.status is PaymentStatus.PAID:
        return current

    return await provider.cancel_pix_charge(charge_id)
```

!!! warning "Polling does not replace the webhook, and the webhook does not replace polling"
    `get_pix_charge` is the source you control: it answers when you ask. The
    webhook is the one that arrives first, and may not arrive at all. A
    service that only listens gets stuck when a delivery fails; one that only
    polls pays latency on every order. The [OpenPix »](openpix.md) recipe
    shows both sides wired up — the webhook to react, the read-back to
    reconcile.

## Switching provider

The adapter is the only line that changes. Keep the choice in one place and
return the **contract**, never the adapter:

```python
from tempest_fastapi_sdk.integrations.payment import PixProvider
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient


def build_provider(client: OpenPixClient) -> PixProvider:
    """Choose the Pix provider this deployment charges with.

    Args:
        client (OpenPixClient): The configured OpenPix client.

    Returns:
        PixProvider: The provider, seen through the contract.
    """
    return OpenPixPixProvider(client)
```

Because the return type is the `Protocol`, the type-checker starts holding
you to the contract everywhere else in the service — and it is the one that
tells you, not production. In a FastAPI service this function is the body of
the `Depends`: the router receives a `PixProvider` and never learns which
adapter arrived — that is the point, and the next section builds the whole
service around it.

!!! info "How many adapters exist today: one"
    The SDK ships **one** ready adapter — `OpenPixPixProvider`, in
    `integrations/payment/adapters/openpix.py`. Mercado Pago has a client,
    schemas and `parse_pix_payment` under
    `integrations/payment/mercado_pago/`, but **not yet** a `PixProvider`;
    Stripe comes in through another door, because it
    [does not do Pix](stripe.md). So the one-line switch is the design, and
    it is real the moment the second adapter exists — writing one is the last
    section on this page.

## In your service's architecture

So far the provider has arrived ready-made, as an argument. In a FastAPI
application someone has to **build** it — and that construction is what
decides whether switching provider is one line or a refactor.

This section wires the whole path, bottom up: HTTP client → adapter →
dependency → service → router. The service that comes out of it has **two**
files that know the name "OpenPix"; everything else speaks the contract.

### Where each piece lives

```text
src/
├── core/
│   └── settings.py              # OPENPIX_APP_ID + environment
├── api/
│   ├── app.py                   # create_app() + lifespan
│   ├── dependencies/
│   │   ├── orders.py            # your own order repository
│   │   └── payments.py          # HTTPClient -> OpenPixClient -> adapter
│   └── routers/
│       ├── checkout.py          # POST /api/checkout/{order_id}
│       └── webhooks.py          # POST /webhooks/pix (include_in_schema=False)
├── schemas/
│   └── checkout.py              # what your own client sees
├── services/
│   └── checkout.py              # business rules, written on the contract only
└── db/
    └── repositories/
        └── orders.py            # where provider_charge_id is kept
```

| Layer | May import | Never imports |
| --- | --- | --- |
| `api/dependencies` | `HTTPClient`, `OpenPixClient`, the adapter, services | — |
| `api/routers` | the dependencies, `schemas` | the adapter, `OpenPixClient` |
| `services` | `PixProvider`, `PixCharge`, repositories | the adapter, `fastapi` |
| `schemas` | `BaseSchema` | the contract and the adapter |
| `db/repositories` | — | anything about payments |

`api/dependencies` is the only layer allowed to know the provider because it
is the only one whose job is **assembly**. It is the composition root: the
place where the concrete becomes the contract, and the only one that changes
on the day of the switch.

### Step 1 — configuration

```python
from tempest_fastapi_sdk import OpenPixSettings


class Settings(OpenPixSettings):
    """The service's settings."""


settings: Settings = Settings()
```

`OpenPixSettings` brings `OPENPIX_APP_ID` and `OPENPIX_ENVIRONMENT`, and
`settings.openpix_kwargs()` returns the resolved `base_url` and the
`Authorization` header — the two arguments `HTTPClient` needs. The
environments are covered in [OpenPix »](openpix.md).

### Step 2 — the HTTP client and the provider, built once

This is the whole file. It is the composition root:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import PixProvider
from tempest_fastapi_sdk.integrations.payment.adapters import OpenPixPixProvider
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    OpenPixWebhookEvent,
    make_openpix_webhook_dependency,
)

from src.api.dependencies.orders import OrderRepositoryDep
from src.core.settings import settings
from src.services.checkout import CheckoutService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build one HTTP client and one provider for the whole process.

    Args:
        app (FastAPI): The application the provider is stored on.

    Yields:
        None: While the application serves requests.
    """
    http: HTTPClient = HTTPClient(**settings.openpix_kwargs(), timeout=15.0)
    app.state.pix_provider = OpenPixPixProvider(OpenPixClient(http))
    try:
        yield
    finally:
        await http.aclose()


def get_pix_provider(request: Request) -> PixProvider:
    """Hand out the process-wide provider, seen through the contract.

    Args:
        request (Request): The request in flight.

    Returns:
        PixProvider: The configured provider.
    """
    provider: PixProvider = request.app.state.pix_provider
    return provider


PixProviderDep = Annotated[PixProvider, Depends(get_pix_provider)]
"""The contract, injected."""

verified_delivery = make_openpix_webhook_dependency()
"""The provider's own verifier, as a dependency tests can override."""

WebhookDeliveryDep = Annotated[OpenPixWebhookEvent, Depends(verified_delivery)]
"""A delivery whose signature the verifier already accepted."""


def get_checkout_service(
    provider: PixProviderDep,
    orders: OrderRepositoryDep,
) -> CheckoutService:
    """Assemble the service the routers call.

    Args:
        provider (PixProviderDep): The provider, through the contract.
        orders (OrderRepositoryDep): The order repository.

    Returns:
        CheckoutService: The service, ready to charge.
    """
    return CheckoutService(provider, orders)


CheckoutServiceDep = Annotated[CheckoutService, Depends(get_checkout_service)]
"""The checkout service, injected."""
```

Four things happen there, and each is worth reading on its own.

**The assembly is three layers in one line.**
`OpenPixPixProvider(OpenPixClient(http))` stacks transport → provider client
→ adapter. Each does one job: the `HTTPClient` has retries, timeout and a
per-host circuit breaker; the `OpenPixClient` knows the routes; the adapter
translates into the contract.

**There is one `HTTPClient`, created in the `lifespan`.** It is safe to
share across requests on the same event loop, and the connection pool, the
retries and the breaker are *its* state. Creating one per request throws all
three away and opens a fresh socket on every checkout — and `aclose()` in the
`finally` is what closes the pool at shutdown.

**The return annotation is the seam.** `get_pix_provider` promises
`PixProvider`, not `OpenPixPixProvider`. From there up, the type-checker
refuses any use of something the contract does not carry.

**`verified_delivery` is a named variable on purpose.** It is the callable
`Depends` registers — and it is the key into `app.dependency_overrides` in
the test. Writing `Depends(make_openpix_webhook_dependency())` inline in the
`Annotated` leaves the function anonymous, with no way to replace it.

!!! warning "`app.state` is `Any` — re-annotate on the way out"
    `request.app.state.pix_provider` has no type: `app.state` accepts any
    attribute. Without the `provider: PixProvider = ...` line,
    `mypy --strict` rejects the module:

    ```text
    src/api/dependencies/payments.py:50: error: Returning Any from function
    declared to return "PixProvider"  [no-any-return]
    ```

    The annotation is not decoration: it is the point where the value exists
    again for the type-checker. With it, the whole service passes
    `mypy --strict`.

### Step 3 — the service, which speaks only the contract

```python
from datetime import timedelta

from tempest_fastapi_sdk.integrations.payment import (
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPaymentEvent,
    PixProvider,
)

from src.db.repositories import OrderRepository


class CheckoutService:
    """Open and settle Pix charges for orders."""

    def __init__(self, provider: PixProvider, orders: OrderRepository) -> None:
        """Take the contract and the repository.

        Args:
            provider (PixProvider): Any provider that implements the contract.
            orders (OrderRepository): Where the charge id is persisted.
        """
        self._provider: PixProvider = provider
        self._orders: OrderRepository = orders

    async def open_charge(self, order_id: str, amount_cents: int) -> PixCharge:
        """Charge an order and remember how to address the charge later.

        Args:
            order_id (str): The order's identifier, sent as the reference.
            amount_cents (int): The amount, in cents.

        Returns:
            PixCharge: The created charge, in canonical shape.
        """
        charge = await self._provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=amount_cents,
                reference=order_id,
                description=f"Order {order_id}",
                expires_in=timedelta(minutes=30),
            ),
        )
        await self._orders.attach_charge(order_id, charge.provider_charge_id)
        return charge

    async def settle(self, event: PixPaymentEvent) -> str | None:
        """Act on a canonical event, whichever provider produced it.

        Args:
            event (PixPaymentEvent): The parsed event.

        Returns:
            str | None: The order that was settled, or None when the event
            says something else.
        """
        if event.type is not PixEventType.CHARGE_PAID or event.charge is None:
            return None
        await self._orders.mark_paid(event.charge.reference)
        return event.charge.reference
```

Look at the import block: no `adapters`, no `openpix`. That is a rule you
can check with `grep` instead of with review.

The `reference` is **your** order's id, and `provider_charge_id` is written
in the same transaction the charge is born in. One is how the webhook finds
you; the other is how you get back to the provider to read or cancel. Losing
the second means a charge that exists at the provider and that your service
can no longer address.

### Step 4 — the router returns your schema, not `PixCharge`

```python
from fastapi import APIRouter, status

from src.api.dependencies import CheckoutServiceDep
from src.schemas import CheckoutCreateSchema, CheckoutResponseSchema

router: APIRouter = APIRouter(prefix="/api/checkout", tags=["checkout"])


@router.post("/{order_id}", status_code=status.HTTP_201_CREATED)
async def open_checkout(
    order_id: str,
    payload: CheckoutCreateSchema,
    service: CheckoutServiceDep,
) -> CheckoutResponseSchema:
    """Open a Pix charge for an order.

    Args:
        order_id (str): The order to charge.
        payload (CheckoutCreateSchema): How much to charge.
        service (CheckoutServiceDep): The checkout service.

    Returns:
        CheckoutResponseSchema: What the payment screen needs.
    """
    charge = await service.open_charge(order_id, payload.amount_cents)
    return CheckoutResponseSchema(
        order_id=charge.reference,
        amount_cents=charge.amount_cents,
        br_code=charge.br_code,
        qr_code_image_url=charge.qr_code_image_url,
        qr_code_base64=charge.qr_code_base64,
    )
```

!!! danger "`PixCharge` is a Pydantic schema — which is exactly why returning it leaks"
    Nothing stops a router from annotating `-> PixCharge`: it serializes.
    The problem is **what** it serializes. A `model_dump(mode="json")` of a
    charge has 14 fields, and two of them are not your client's business:

    ```text
    ['amount_cents', 'br_code', 'currency', 'end_to_end_id', 'expires_at',
     'paid_at', 'provider', 'provider_charge_id', 'provider_status',
     'qr_code_base64', 'qr_code_image_url', 'raw', 'reference', 'status']
    ```

    `raw` is the provider's payload as decoded — on the OpenPix path it is
    the whole `Charge`, `customer` included, with the payer's `name`,
    `email` and `tax_id`. `provider_charge_id` is your write key at the
    provider. A response schema of your own, carrying the fields the screen
    uses, is what separates your API from a third party's payload.

### Step 5 — the webhook: verification at the edge, contract inside

```python
from fastapi import APIRouter

from src.api.dependencies import CheckoutServiceDep, PixProviderDep, WebhookDeliveryDep

router: APIRouter = APIRouter(prefix="/webhooks", include_in_schema=False)


@router.post("/pix")
async def receive_pix(
    delivery: WebhookDeliveryDep,
    provider: PixProviderDep,
    service: CheckoutServiceDep,
) -> dict[str, str | None]:
    """Turn a verified delivery into a settled order.

    Args:
        delivery (WebhookDeliveryDep): The verified delivery.
        provider (PixProviderDep): The provider that parses it.
        service (CheckoutServiceDep): The service that acts on it.

    Returns:
        dict[str, str | None]: The order settled by this delivery, if any.
    """
    event = provider.parse_webhook(delivery)
    return {"settled": await service.settle(event)}
```

The router does not import `OpenPixWebhookEvent`, and does not know there is
RSA anywhere on the path: it receives a `WebhookDeliveryDep`, hands it to the
provider's `parse_webhook` and acts on the `PixPaymentEvent` that comes out.
Signature verification — the part no contract unifies — stayed entirely
inside the composition root's `Annotated`.

!!! note "`include_in_schema=False` is not cosmetic"
    A webhook is not an endpoint of your public API: what authenticates
    there is a signature, not your user's token. With the router out of the
    schema, this service's `app.openapi()` lists exactly one route:

    ```text
    ['/api/checkout/{order_id}']
    ```

### Step 6 — in tests, the fake enters through the dependency

The in-memory adapter from this page's last section is not just for scripts:
it takes the provider's place through `dependency_overrides`, and the whole
suite runs without a network.

```python
from typing import Any

from fastapi.testclient import TestClient

from tempest_fastapi_sdk.integrations.payment import PixProvider

from src.api.app import create_app
from src.api.dependencies import get_pix_provider, verified_delivery
from src.db.repositories import OrderRepository
from tests.fakes import FakePixProvider


def test_checkout_and_webhook() -> None:
    """Charge and settle through the whole stack, on the fake."""
    app = create_app()
    provider: PixProvider = FakePixProvider()
    orders = OrderRepository()
    app.state.orders = orders
    app.dependency_overrides[get_pix_provider] = lambda: provider

    with TestClient(app) as client:
        created = client.post("/api/checkout/order-1042", json={"amount_cents": 1990})
        assert created.status_code == 201
        assert created.json()["br_code"] == "000201fake-1"
        assert orders.charge_ids == {"order-1042": "fake-1"}

        def fake_delivery() -> Any:
            """Stand in for the verified delivery.

            Returns:
                Any: What this provider's parse_webhook reads.
            """
            return {"charge_id": "fake-1"}

        app.dependency_overrides[verified_delivery] = fake_delivery
        settled = client.post("/webhooks/pix")
        assert settled.json() == {"settled": "order-1042"}
        assert orders.paid == {"order-1042"}
```

Both responses, running:

```text
POST /api/checkout/order-1042 -> 201 {'order_id': 'order-1042', 'amount_cents': 1990, 'br_code': '000201fake-1', 'qr_code_image_url': None, 'qr_code_base64': None}
POST /webhooks/pix -> 200 {'settled': 'order-1042'}
```

There are two overrides, and they differ on purpose. The **provider** one
swaps the entire provider for the fake. The **verified delivery** one swaps
only the verifier — because signing is the part a fake cannot imitate, and
faking verification in a test beats turning it off in production.

!!! tip "What the type-checker holds your fake to"
    The line `provider: PixProvider = FakePixProvider()` is what makes
    `mypy --strict` check the fake against the contract. Change
    `create_pix_charge`'s parameter to `str` and the rejection is immediate,
    naming the member:

    ```text
    tests/fakes.py:39: error: "str" has no attribute "amount_cents"  [attr-defined]
    tests/test_checkout.py:18: error: Incompatible types in assignment (expression has type "FakePixProvider", variable has type "PixProvider")  [assignment]
    tests/test_checkout.py:18: note: Following member(s) of "FakePixProvider" have conflicts:
    tests/test_checkout.py:18: note:     Expected:
    tests/test_checkout.py:18: note:         def create_pix_charge(self, request: PixChargeRequest) -> Coroutine[Any, Any, PixCharge]
    tests/test_checkout.py:18: note:     Got:
    tests/test_checkout.py:18: note:         def create_pix_charge(self, request: str) -> Coroutine[Any, Any, PixCharge]
    ```

    Without the annotation, `dependency_overrides` accepts any callable and
    the defect only shows up on the first charge.

### What switching provider costs in this service

Sweeping the service above for the provider's name finds **two** files:

```text
src/core/settings.py
src/api/dependencies/payments.py
```

The first only because the credentials really are the provider's. The second
is the composition root — and it is the `OpenPixPixProvider(OpenPixClient(http))`
line that changes when the adapter is another one. Neither the service, nor
the routers, nor the schemas show up in that list: that is how you measure
whether the seam is where you think it is.

## States

You branch on `PaymentStatus`, never on the provider's string:

| canonical | means |
| --- | --- |
| `PENDING` | created, waiting for the payer |
| `PAID` | settled |
| `EXPIRED` | the window closed unpaid |
| `CANCELLED` | withdrawn by you or the provider |
| `REFUNDED` | paid and returned |
| `CHARGED_BACK` | reversed by the payer's institution |
| `IN_ANALYSIS` | held for review |
| `FAILED` | refused |

The original string is not lost: it lives in `provider_status`, which is
what you put in the log and show to support.

```python
from tempest_fastapi_sdk.integrations.payment import PaymentStatus, PixCharge


def release_order(charge: PixCharge) -> bool:
    """Decide whether the order can be released.

    Args:
        charge (PixCharge): The charge, in canonical shape.

    Returns:
        bool: Whether the money is in.
    """
    return charge.status is PaymentStatus.PAID
```

!!! warning "`is` works here — and it would not, for free"
    `PixCharge` turns off the `use_enum_values` that `BaseSchema` turns on.
    Without that, `charge.status` would hold the **string** `"paid"` and
    `charge.status is PaymentStatus.PAID` would be `False` on every charge,
    silently, while `==` kept working. That is the kind of defect that
    survives review precisely because the obvious check still passes.

## Webhooks

Signature verification stays with each provider — RSA-1024 at OpenPix, HMAC
at Stripe. What the contract unifies is what comes out of it:

```python
from tempest_fastapi_sdk.integrations.payment import PixEventType, PixProvider
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixWebhookEvent


def handle(provider: PixProvider, delivery: OpenPixWebhookEvent) -> str | None:
    """Turn a verified delivery into an action.

    Args:
        provider (PixProvider): The provider that verified the delivery.
        delivery (OpenPixWebhookEvent): The verified event.

    Returns:
        str | None: The reference of the order that was paid, if any.
    """
    event = provider.parse_webhook(delivery)
    if event.type is PixEventType.CHARGE_PAID and event.charge is not None:
        return event.charge.reference
    return None
```

An event the SDK does not classify becomes `PixEventType.UNKNOWN` **with the
original name preserved** in `provider_event_name`. It stays visible rather
than swallowed.

There are six canonical types:

| `PixEventType` | fired when |
| --- | --- |
| `CHARGE_CREATED` | the charge was opened |
| `CHARGE_PAID` | the money arrived |
| `CHARGE_EXPIRED` | the window closed unpaid |
| `CHARGE_CANCELLED` | the charge was withdrawn |
| `CHARGE_REFUNDED` | the amount was returned |
| `UNKNOWN` | the provider sent something the SDK does not map |

!!! note "Why `parse_webhook` takes `Any`"
    Each provider delivers a different type: OpenPix delivers an
    `OpenPixWebhookEvent`, already verified; another provider would deliver
    the body's dict, or an object of its own. Typing the parameter as one
    provider's type would tie the contract to that provider — exactly the
    coupling this page avoids. The adapter knows its own type; the contract
    only knows what comes **out**, which is `PixPaymentEvent`.

    Verifying the signature happens **before** this, and stays each
    provider's job: RSA-1024 at OpenPix, HMAC at Stripe. The step-by-step on
    the OpenPix side, including how to register the URL, is in
    [OpenPix »](openpix.md).

## What the provider says beyond the contract

It lives in `raw`:

```python
from tempest_fastapi_sdk.integrations.payment import PixCharge


def payment_link(charge: PixCharge) -> object | None:
    """Read a provider-specific field the contract does not model.

    Args:
        charge (PixCharge): The charge.

    Returns:
        object | None: OpenPix's payment link, when present.
    """
    return charge.raw.get("paymentLinkUrl")
```

!!! info "Why `raw` has to exist"
    The SDK's `BaseSchema` is `extra="ignore"`. Without this field,
    everything a provider sends beyond the contract would vanish during
    validation — no error, no warning. `raw` is what makes going through
    the contract lossless.

    One honest difference: on the API path `raw` is the payload **after**
    the generated schema validated it, so fields the provider's own
    specification does not declare are already gone. On the webhook path
    the body arrives as a dictionary and `raw` is faithful. That is why
    `paid_at` is only filled by a webhook delivery on OpenPix: `paidAt`
    shows up in the specification's examples but not in the `Charge`
    schema.

## Writing an adapter

An adapter is a class with `provider_name` and the four methods. Nothing to
inherit. The example below talks to no provider at all — it keeps charges in
a dict — and that makes it the way to **test your service without a
network**, which is the first adapter worth writing:

```python
import asyncio
from typing import Any

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPaymentEvent,
    PixProvider,
)


class FakePixProvider:
    """A provider that keeps charges in a dict instead of calling anyone.

    Attributes:
        provider_name (str): The identifier copied into
            ``PixCharge.provider``.
    """

    provider_name: str = "fake"

    def __init__(self) -> None:
        """Start with no charges."""
        self._charges: dict[str, PixCharge] = {}
        self._next_id: int = 1

    async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge:
        """Create a charge in memory.

        Args:
            request (PixChargeRequest): What the service asked to charge.

        Returns:
            PixCharge: The charge, in canonical shape.
        """
        charge_id = f"fake-{self._next_id}"
        self._next_id += 1
        charge = PixCharge(
            provider=self.provider_name,
            provider_charge_id=charge_id,
            reference=request.reference,
            amount_cents=request.amount_cents,
            status=PaymentStatus.PENDING,
            provider_status="created",
            br_code=f"000201{charge_id}",
        )
        self._charges[charge_id] = charge
        return charge

    async def get_pix_charge(self, charge_id: str) -> PixCharge:
        """Read a charge back.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            PixCharge: The stored charge.

        Raises:
            KeyError: When no charge carries that id.
        """
        return self._charges[charge_id]

    async def cancel_pix_charge(self, charge_id: str) -> PixCharge:
        """Withdraw an unpaid charge.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            PixCharge: The charge in its cancelled shape.
        """
        charge = self._charges[charge_id]
        cancelled = charge.model_copy(
            update={
                "status": PaymentStatus.CANCELLED,
                "provider_status": "cancelled",
            },
        )
        self._charges[charge_id] = cancelled
        return cancelled

    def parse_webhook(self, event: Any) -> PixPaymentEvent:
        """Turn a delivery into a canonical event.

        Args:
            event (Any): Whatever this provider delivers.

        Returns:
            PixPaymentEvent: The canonical event.
        """
        charge = self._charges[str(event["charge_id"])]
        paid = charge.model_copy(
            update={"status": PaymentStatus.PAID, "provider_status": "paid"},
        )
        self._charges[paid.provider_charge_id] = paid
        return PixPaymentEvent(
            provider=self.provider_name,
            type=PixEventType.CHARGE_PAID,
            provider_event_name="fake.paid",
            charge=paid,
            raw=dict(event),
        )


async def main() -> None:
    """Exercise the whole contract against the fake."""
    provider: PixProvider = FakePixProvider()

    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="order-1042"),
    )
    print(charge.provider_charge_id, charge.status.value)

    event = provider.parse_webhook({"charge_id": charge.provider_charge_id})
    print(event.type.value, event.charge is not None)


if __name__ == "__main__":
    asyncio.run(main())
```

Run it and you get:

```text
fake-1 pending
charge_paid True
```

The line doing the checking is `provider: PixProvider = FakePixProvider()`.
It changes nothing at runtime — it changes what the type-checker demands of
you. If a method comes out with the wrong signature, `mypy --strict` fails
there, and not on the day of the first charge.

!!! tip "Three things a real provider's adapter does on top"
    1. **Converts the unit.** The contract is whole cents; the provider may
       want decimal currency. The conversion belongs to the adapter, which is
       why it lives in exactly one place.
    2. **Maps the state.** The provider's string becomes a `PaymentStatus`,
       and the original is copied into `provider_status` — nothing discarded.
    3. **Fills `raw`.** Everything the provider says beyond the contract goes
       there, so no information dies in translation.

    `OpenPixPixProvider` is the reference for how the three sit together:
    `integrations/payment/adapters/openpix.py`.

## Recap

- Your service depends on `PixProvider` and receives `PixCharge`.
- The contract has four methods: create, read, cancel and parse the webhook.
  Store `provider_charge_id` — it is the argument to the middle two.
- Money crosses the contract as an `int` of cents.
- The state you branch on is `PaymentStatus`; the provider's own sits
  beside it in `provider_status`.
- Webhook signatures stay per provider; the event that comes out is
  canonical, and whatever the SDK does not map arrives as `UNKNOWN` with the
  original name.
- Nothing is lost: whatever the provider says beyond the contract is in
  `raw`.
- An adapter is a class with `provider_name` and the four methods, no
  inheritance. The SDK ships one today (OpenPix); the in-memory fake above is
  the one you write first, to test without a network.
- In the architecture: the provider is assembled in `api/dependencies` and
  leaves it as a `PixProvider`. One `HTTPClient` per process, in the
  `lifespan` — and `app.state` is `Any`, so re-annotate the type on the way
  out.
- The router returns your own schema, not `PixCharge`: the canonical charge
  carries `raw` (the provider's payload, `customer` included) and
  `provider_charge_id`.
- In tests, `dependency_overrides` swaps the provider for the fake and the
  verified delivery for a stub — two overrides, and the suite runs without a
  network.
