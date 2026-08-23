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
    PixProvider,
)
```

`PixProvider` is a `Protocol` with four methods: create, read, cancel and
parse the webhook. Whoever implements it always returns a `PixCharge`, in
the same shape, wherever it came from.

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

## Switching provider

The adapter is the only line that changes:

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
tells you, not production.

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

## Recap

- Your service depends on `PixProvider` and receives `PixCharge`.
- Money crosses the contract as an `int` of cents.
- The state you branch on is `PaymentStatus`; the provider's own sits
  beside it in `provider_status`.
- Webhook signatures stay per provider; the event that comes out is
  canonical.
- Nothing is lost: whatever the provider says beyond the contract is in
  `raw`.
