# OpenPix (subscriptions and plans)

This recipe covers **recurring** billing: monthly plans, yearly plans, Pix
Automático. It picks up where the [one-off charge recipe](openpix.md) left off
— the configuration, the layered architecture and the "the webhook notifies,
the API confirms" rule are the same and are not repeated here.

## First thing to understand: the plan is yours, the subscription is theirs

OpenPix **has no plan resource**. There is no `POST /api/v1/plan`, no catalog
on the provider's side. What exists is `subscription`: an agreement with
**one** customer, for **one** amount, at **one** frequency.

That decides your modelling:

| Concept | Where it lives | Why |
| --- | --- | --- |
| **Plan** ("Pro, R$ 49.90/month") | Your database | It is catalog, pricing and business rules — none of which OpenPix knows |
| **Subscription** (Ana on Pro) | Your database **and** OpenPix | You keep the link and the state; they generate the charges |
| **Cycle charge** | OpenPix generates it | Each period becomes an ordinary `Charge`, with the same webhook |

```text
plans (yours)               subscriptions (yours)            OpenPix
┌──────────────┐            ┌───────────────────┐            ┌──────────────┐
│ id           │◄───────────│ plan_id           │            │ subscription │
│ name  "Pro"  │            │ user_id           │            │ correlationID│
│ value 4990   │            │ correlation_id  ──┼───────────►│ globalID     │
│ frequency    │            │ status            │            │ ...          │
└──────────────┘            └───────────────────┘            └──────────────┘
```

The subscription's `correlationID` is your key: use the id of the row in
`subscriptions`, not the plan id — the same plan has thousands of subscribers.

## Creating the subscription

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    Subscription,
    SubscriptionFrequency,
    SubscriptionPayload,
    SubscriptionPayloadCustomer,
    SubscriptionPayloadType,
    reais_to_cents,
)


class SubscriptionService:
    """Recurring subscription rules."""

    def __init__(self, client: OpenPixClient) -> None:
        """Store the generated client.

        Args:
            client (OpenPixClient): The OpenPix client.
        """
        self.client: OpenPixClient = client

    async def subscribe(
        self,
        *,
        reference: str,
        plan_name: str,
        amount_brl: str,
        customer_name: str,
        customer_email: str,
        customer_tax_id: str,
        charge_day: int,
    ) -> Subscription:
        """Subscribe a customer to a plan.

        Args:
            reference (str): Subscription id in your database
                (`correlationID`).
            plan_name (str): Plan name, which the payer sees on the charge.
            amount_brl (str): Cycle amount, in reais.
            customer_name (str): The subscriber's name.
            customer_email (str): The subscriber's email.
            customer_tax_id (str): The subscriber's CPF or CNPJ.
            charge_day (int): Day of the month the charge is generated.

        Returns:
            Subscription: The created subscription.

        Raises:
            ValueError: If the response comes back without the subscription.
        """
        response = await self.client.post_api_v1_subscriptions(
            body=SubscriptionPayload(
                correlation_id=reference,
                name=plan_name,
                value=reais_to_cents(amount_brl),
                type=SubscriptionPayloadType.RECURRENT,
                frequency=SubscriptionFrequency.MONTHLY,
                day_generate_charge=charge_day,
                day_due=5,
                customer=SubscriptionPayloadCustomer(
                    name=customer_name,
                    email=customer_email,
                    tax_id=customer_tax_id,
                ),
            )
        )
        if response.subscription is None:
            raise ValueError(f"OpenPix returned no subscription for {reference}")
        return response.subscription
```

The body that goes on the wire (measured, with `MockTransport`):

```json
{
  "customer": {"name": "Ana", "email": "ana@example.com", "taxID": "11111111111"},
  "value": 4990.0,
  "name": "Plano Pro",
  "dayGenerateCharge": 10.0,
  "frequency": "MONTHLY",
  "type": "RECURRENT",
  "dayDue": 5.0,
  "correlationID": "assinatura-1",
  "additionalInfo": []
}
```

And the response carries `payment_link_url` — the page where the subscriber
pays each cycle — alongside the `global_id` OpenPix uses internally.

!!! note "The numbers go out as floats, and that is the specification"
    `value`, `dayGenerateCharge` and `dayDue` are `type: number` in the spec,
    so the generated model serializes them as `4990.0`, `10.0` and `5.0`. It
    is valid JSON and the same value — but if you compare bodies byte for byte
    in a test, that is what you will see. On the reading side, `to_cents`
    undoes the float back into whole cents.

### The fields that decide the behaviour

| Field | What it does |
| --- | --- |
| `frequency` | Interval between cycles: `WEEKLY`, `MONTHLY`, `BIMONTHLY`, `QUARTERLY`, `SEMIANNUALLY`, `ANNUALLY`. Omitted, it becomes `MONTHLY` |
| `day_generate_charge` | Day of the month the cycle's charge is **born** |
| `day_due` | How many days after that it is **due** |
| `installment_count` | Total number of cycles. Without it the subscription is open-ended |
| `charge_type` | How each charge is issued: `DYNAMIC` (plain Pix), `OVERDUE` (with interest and fine) or `BOLETO` |
| `type` | `RECURRENT` or `PIX_RECURRING` — the difference is right below |

!!! tip "`installment_count` is what separates a subscription from instalments"
    Without it you have a monthly plan: it bills until someone cancels. With
    `installment_count=12` you have a twelve-part instalment plan that ends by
    itself — and `installments_count` in the response comes back `None`
    exactly when the subscription is open-ended.

## `RECURRENT` or `PIX_RECURRING`: the choice that changes the product

```python
from tempest_fastapi_sdk.integrations.payment.openpix import SubscriptionPayloadType

manual = SubscriptionPayloadType.RECURRENT
automatic = SubscriptionPayloadType.PIX_RECURRING
```

| | `RECURRENT` | `PIX_RECURRING` (Pix Automático) |
| --- | --- | --- |
| How the money moves | The subscriber pays each charge | Debited from their account, no action needed |
| Authorization | None, it is one charge per cycle | The payer authorizes once, at their bank |
| Default | The charge expires | The bank retries, per the `retryPolicy` |
| Frequencies | All six | No `BIMONTHLY` — the Central Bank does not allow it |
| Webhook events | `OPENPIX:CHARGE_*` | `PIX_AUTOMATIC_*`, **without** the `OPENPIX:` prefix |

Pix Automático carries its own options:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    SubscriptionFrequency,
    SubscriptionPayload,
    SubscriptionPayloadCustomer,
    SubscriptionPayloadPixRecurringOptions,
    SubscriptionPayloadType,
)

payload = SubscriptionPayload(
    correlation_id="subscription-2",
    name="Plano Pro",
    value=4990,
    type=SubscriptionPayloadType.PIX_RECURRING,
    frequency=SubscriptionFrequency.MONTHLY,
    customer=SubscriptionPayloadCustomer(name="Ana", tax_id="11111111111"),
    pix_recurring_options=SubscriptionPayloadPixRecurringOptions(
        minimum_value=1000,
    ),
)
```

!!! warning "Pix Automático events carry no `OPENPIX:` prefix"
    If your handler filters on `event_name.startswith("OPENPIX:")`, it drops
    the whole family silently. Compare against `OpenPixEvent` members, not
    strings: `OpenPixEvent.PIX_AUTOMATIC_APPROVED.value ==
    "PIX_AUTOMATIC_APPROVED"`.

## Collecting each cycle

There is no new API here: **each cycle becomes an ordinary charge**, with the
same `OPENPIX:CHARGE_COMPLETED` as the one-off recipe. What changes is that
the charge carries the subscription.

```python
from tempest_fastapi_sdk.integrations.payment.openpix import Charge, OpenPixEvent
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixWebhookEvent


def subscription_of(event: OpenPixWebhookEvent) -> str | None:
    """Find which subscription a paid charge belongs to.

    Args:
        event (OpenPixWebhookEvent): The verified delivery.

    Returns:
        str | None: The subscription's `correlationID`, or `None` when the
        charge is a one-off.
    """
    if event.event is not OpenPixEvent.CHARGE_COMPLETED:
        return None
    charge = Charge.model_validate(event.payload["charge"])
    if charge.subscription is None:
        return None
    return charge.subscription.correlation_id
```

A charge without `subscription` is a one-off — handle it through the other
recipe's path. With `subscription`, what you are collecting is the monthly
fee: mark the cycle paid and push the renewal date out.

To list a subscription's history:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient


async def charges_of(client: OpenPixClient, reference: str) -> list[str]:
    """List the charges a subscription generated.

    Args:
        client (OpenPixClient): The OpenPix client.
        reference (str): The subscription's `correlationID`.

    Returns:
        The status of each cycle charge, oldest first.
    """
    response = await client.get_api_v1_charge(subscription=reference)
    return [str(charge.status) for charge in response.charges]
```

## Lifecycle

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    Installment,
    OpenPixClient,
    Subscription,
)


async def read(client: OpenPixClient, reference: str) -> Subscription | None:
    """Read a subscription's current state.

    Args:
        client (OpenPixClient): The OpenPix client.
        reference (str): The subscription's `correlationID` or `globalID`.

    Returns:
        Subscription | None: The subscription, or `None` if absent.
    """
    response = await client.get_api_v1_subscriptions_by_id(id=reference)
    return response.subscription


async def installments(client: OpenPixClient, global_id: str) -> list[Installment]:
    """List the instalments generated so far.

    Args:
        client (OpenPixClient): The OpenPix client.
        global_id (str): The subscription's `globalID` — this endpoint does
            **not** take the `correlationID`.

    Returns:
        The instalments, with number, amount, status and generation date.
    """
    response = await client.get_api_v1_subscriptions_by_id_installments(id=global_id)
    return response.installments


async def cancel(client: OpenPixClient, reference: str) -> None:
    """End the subscription.

    Args:
        client (OpenPixClient): The OpenPix client.
        reference (str): The subscription's `correlationID` or `globalID`.
    """
    await client.put_api_v1_subscriptions_by_id_cancel(id=reference)
```

!!! warning "`installments` wants the `globalID`, the others take either"
    It is in the specification itself: `get_api_v1_subscriptions_by_id` and
    `put_api_v1_subscriptions_by_id_cancel` document *"the globalID or
    correlationID"*, while the instalments one documents *"the globalID"*.
    Store the `global_id` from the creation response — without it you need an
    extra read just to list instalments.

### Changing the amount: the operation the specification left incomplete

`PUT /api/v1/subscriptions/{id}/value` exists, and it re-prices the next
instalments of a Pix Automático subscription with a dynamic amount. But the
specification **declares no body for it** — checked in
`vendor/openpix-openapi.yaml`: the operation has only the path parameter. The
generated client mirrors that faithfully:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient


async def bump(client: OpenPixClient, reference: str) -> None:
    """Call the endpoint exactly as the specification describes it.

    Args:
        client (OpenPixClient): The OpenPix client.
        reference (str): The subscription's `correlationID`.
    """
    await client.put_api_v1_subscriptions_by_id_value(id=reference)
```

If your account needs to send the new amount, send it through the
`HTTPClient` — same transport, same headers, retry and circuit breaker:

```python
from tempest_fastapi_sdk import HTTPClient


async def bump_to(http: HTTPClient, reference: str, cents: int) -> None:
    """Re-price the next instalments.

    Args:
        http (HTTPClient): The already-authenticated transport.
        reference (str): The subscription's `correlationID`.
        cents (int): The new amount, in cents.
    """
    response = await http.request(
        "PUT",
        f"/api/v1/subscriptions/{reference}/value",
        json={"value": cents},
    )
    response.raise_for_status()
```

The generator does not invent what the specification does not say — had it
guessed a body, you would find the mistake in production, not here.

## The state that stays on your side

OpenPix knows whether the cycle's charge was paid. It does not know whether
**your** user has access. That state machine is yours:

```text
                    cycle charge paid
    ┌─────────┐ ─────────────────────────► ┌────────┐
    │ created │                            │ active │
    └─────────┘ ◄───────────────────────── └────────┘
        │          next cycle's charge         │
        │                                      │ cycle expired unpaid
        │                                      ▼
        │                              ┌──────────────┐
        │                              │ past due     │
        │                              └──────────────┘
        │  cancellation                       │
        └──────────────► ┌───────────┐ ◄──────┘
                         │ cancelled │
                         └───────────┘
```

Two rules that avoid the classic subscription bug:

1. **Access expires by date, not by event.** Store `access_until` and push the
   date out when a cycle is paid. If you store only an `is_active` boolean, a
   lost webhook leaves a paying user locked out — or a cancelled one with
   access forever.
2. **Reconcile by instalment.** The periodic job compares OpenPix's
   instalments with the cycles you recorded; anything paid there and open here
   is a lost webhook.

```python
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient


async def unpaid_cycles(client: OpenPixClient, global_id: str) -> list[float]:
    """List the numbers of the instalments not yet paid.

    Args:
        client (OpenPixClient): The OpenPix client.
        global_id (str): The subscription's `globalID`.

    Returns:
        The `installment_number` of every open instalment.
    """
    response = await client.get_api_v1_subscriptions_by_id_installments(id=global_id)
    return [
        parcel.installment_number or 0.0
        for parcel in response.installments
        if parcel.status != "COMPLETED"
    ]
```

## Recap

1. **OpenPix has no plans.** The catalog is yours; the subscription is one
   customer's link to an amount and a frequency.
2. **The subscription's `correlationID` is the row in your database**, not the
   plan. Store the `global_id` that comes back too — the instalments endpoint
   only takes it.
3. **`RECURRENT` bills, `PIX_RECURRING` debits.** The second has frequencies
   restricted by the Central Bank and webhook events **without** the
   `OPENPIX:` prefix.
4. **Every cycle is an ordinary charge** — same webhook, same read-back
   through the API, and `charge.subscription` tells you which subscription it
   came from.
5. **`put_api_v1_subscriptions_by_id_value` has no body in the
   specification.** Send it through the `HTTPClient` if your account needs it.
6. **The user's access expires by date**, pushed out on every paid cycle, and
   reconciled against the instalments list.
