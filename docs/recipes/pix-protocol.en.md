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
adapter arrived — that is the point.

!!! info "How many adapters exist today: one"
    The SDK ships **one** ready adapter — `OpenPixPixProvider`, in
    `integrations/payment/adapters/openpix.py`. Mercado Pago has a client,
    schemas and `parse_pix_payment` under
    `integrations/payment/mercado_pago/`, but **not yet** a `PixProvider`;
    Stripe comes in through another door, because it
    [does not do Pix](stripe.md). So the one-line switch is the design, and
    it is real the moment the second adapter exists — writing one is the last
    section on this page.

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
