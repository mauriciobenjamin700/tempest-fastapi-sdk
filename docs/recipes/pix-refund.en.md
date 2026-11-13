# Pix refunds (idempotent)

A passenger cancels a ticket. Your service gives the money back over Pix,
in a job that retries. On the third attempt, the first one finally
answers: it had refunded, only the answer was lost to the timeout.

If the job does not know that, it refunds again. The passenger is paid
twice, and the money leaves your account.

This recipe shows how to refund so that **a retry is safe by
construction** — no lease, no re-reading the order and comparing totals by
hand.

## The contract: `PixRefundProvider`

```python
from tempest_fastapi_sdk.integrations.payment import (
    PixRefund,
    PixRefundProvider,
    PixRefundStatusEnum,
)
```

`PixRefundProvider` is a `PixProvider` with two more methods:

```text
async def refund_pix_charge(
    self,
    charge_id: str,
    amount_cents: int | None = None,
    *,
    refund_reference: str,
) -> PixRefund

async def get_refunded_cents(self, charge_id: str) -> int
```

What each one guarantees, with every provider:

| Situation | Answer |
| --- | --- |
| first call with a `refund_reference` | refunds and returns the `PixRefund` |
| same `refund_reference`, same amount | returns **the same** refund; the money does not leave again |
| same `refund_reference`, another amount | `PixRefundReferenceConflictException` (409) |
| amount above what is left | `PixRefundExceedsBalanceException` (409) |
| `amount_cents=None` | refunds what is left; if nothing is left, `PixRefundExceedsBalanceException` |
| `amount_cents` zero or negative | `ValueError`, before talking to the provider |

`get_refunded_cents` sums the refunds the provider lists for the charge,
except the failed ones. A refund still processing **counts**: treating it as
money still owed is what would let a second refund go out while the first
settles.

!!! tip "One `refund_reference` per refund"
    Use an identifier of yours, stable and unique across the whole account —
    the cancelled ticket's id, for instance (`"ticket-42-refund"`). It is what
    makes the retry idempotent. OpenPix stores it as the refund's
    `correlationID`, so do not reuse one reference on two charges.

## Refunding with retries, no lease

The whole example, running without a network on `FakePixProvider`:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatusEnum,
    PixChargeRequest,
    PixRefund,
    PixRefundProvider,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider


async def refund_ticket(
    provider: PixRefundProvider,
    *,
    charge_id: str,
    ticket_id: str,
    amount_cents: int,
    attempts: int = 3,
) -> PixRefund:
    """Refund a ticket, retrying on a timeout.

    Args:
        provider (PixRefundProvider): Any provider that refunds.
        charge_id (str): The `provider_charge_id` stored at checkout.
        ticket_id (str): The cancelled ticket.
        amount_cents (int): How much to give back.
        attempts (int): How many times to try.

    Returns:
        PixRefund: The refund — the same one on every attempt.

    Raises:
        TimeoutError: If every attempt timed out.
    """
    last_error: TimeoutError | None = None
    for _ in range(attempts):
        try:
            return await provider.refund_pix_charge(
                charge_id,
                amount_cents,
                refund_reference=f"ticket-{ticket_id}-refund",
            )
        except TimeoutError as error:
            last_error = error
    assert last_error is not None
    raise last_error


async def main() -> None:
    """Open a paid charge, lose the refund's answer and try again."""
    provider = FakePixProvider()
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=12000, reference="order-42"),
    )
    provider.advance(charge.provider_charge_id, PaymentStatusEnum.PAID)

    provider.fail_after_next_refund(TimeoutError("the answer was lost"))
    refund = await refund_ticket(
        provider,
        charge_id=charge.provider_charge_id,
        ticket_id="42",
        amount_cents=4500,
    )

    print(refund.amount_cents, refund.refunded_cents)
    print(await provider.get_refunded_cents(charge.provider_charge_id))
    print(len(provider.refunds[charge.provider_charge_id]))


asyncio.run(main())
```

Output:

```text
4500 4500
4500
1
```

The first attempt **refunded** and raised `TimeoutError` — that is what
`fail_after_next_refund` simulates. The second used the same
`refund_reference` and got the refund that already existed. One refund,
R$ 45.00 given back once.

Notice what is **not** in the code: no lock, no reading the charge first, no
comparing totals. The reference does that work.

!!! warning "Two *simultaneous* calls with the same reference"
    The guarantee above is about calls **in sequence** (the retry). Two
    concurrent calls with the same reference can both reach the provider
    before either has finished. At Mercado Pago, the `X-Idempotency-Key`
    should cover that case on their side, but concurrency was not
    measured; at OpenPix, no document says a repeated
    `correlationID` is refused. If your job can fire the same refund in
    parallel (two workers picking up the same task), serialize per
    reference — a queue with one consumer per key, or `claim_once` before
    the call.

## Partial refunds and what was already refunded

One charge for three tickets; two are cancelled at different times:

```python
import asyncio

from tempest_fastapi_sdk.exceptions import PixRefundExceedsBalanceException
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatusEnum,
    PixChargeRequest,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider


async def main() -> None:
    """Refund two of three tickets, then try to refund too much."""
    provider = FakePixProvider()
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=9000, reference="order-7"),
    )
    charge_id = charge.provider_charge_id
    provider.advance(charge_id, PaymentStatusEnum.PAID)

    await provider.refund_pix_charge(charge_id, 3000, refund_reference="ticket-1")
    await provider.refund_pix_charge(charge_id, 3000, refund_reference="ticket-2")
    print(await provider.get_refunded_cents(charge_id))

    try:
        await provider.refund_pix_charge(
            charge_id, 4000, refund_reference="ticket-3"
        )
    except PixRefundExceedsBalanceException as error:
        print(error.requested_cents, error.refundable_cents)


asyncio.run(main())
```

Output:

```text
6000
4000 3000
```

`get_refunded_cents` answers "how much of this charge did I give back?",
asked of the provider — not of your database. Use it to reconcile: if your
record says R$ 30.00 and the provider says R$ 60.00, someone refunded
outside your service.

Both exceptions are `AppException`s: with `register_exception_handlers`, a
route that lets them propagate answers `409` with `code`
`PIX_REFUND_EXCEEDS_BALANCE` or `PIX_REFUND_REFERENCE_CONFLICT` and the
context in `details`.

## With the provider router

If you use `PixProviderRouter` to migrate providers, the refund goes to
the provider that created the charge, like `get` and `cancel`:

```python
from tempest_fastapi_sdk.integrations.payment import PixProviderRouter, PixRefund


async def refund_stored_charge(
    router: PixProviderRouter,
    *,
    provider_name: str,
    charge_id: str,
    ticket_id: str,
) -> PixRefund:
    """Refund what is left of a stored charge.

    Args:
        router (PixProviderRouter): The service's router.
        provider_name (str): The stored `PixCharge.provider`.
        charge_id (str): The stored `PixCharge.provider_charge_id`.
        ticket_id (str): The cancelled ticket.

    Returns:
        PixRefund: The refund.
    """
    return await router.refund_pix_charge(
        provider_name,
        charge_id,
        refund_reference=f"ticket-{ticket_id}-refund",
    )
```

A registered provider that only implements `PixProvider` makes this call
raise `TypeError`, naming the missing method — before any request.

## How each provider guarantees idempotency

| Provider | Mechanism |
| --- | --- |
| **Mercado Pago** | The `X-Idempotency-Key` of `POST /v1/orders/{id}/refund` is derived from `refund_reference` (a UUID5 of the order id and the reference). Same reference, same key: Mercado Pago answers the first refund again. The order is read first to check the balance; when not enough is left, the request **still goes out** under the derived key, so the retry of a refund that already emptied the charge gets the replay instead of the exception — only a refusal from the provider becomes `PixRefundExceedsBalanceException`. `409 idempotency_key_already_used` becomes `PixRefundReferenceConflictException`. |
| **OpenPix** | `refund_reference` becomes the refund's `correlationID`. Before the `POST`, the adapter lists the charge's refunds (`GET /api/v1/charge/{id}/refund`) and, when one already carries the reference, returns it without refunding. When the `POST` fails (timeout, 5xx), the adapter lists again: if the refund is there, it returns it — the answer was lost, not the refund. The amount always goes out explicit, even for a full refund. |
| **`FakePixProvider`** | Keeps refunds per charge and answers a repeated reference with the stored refund. `fail_next` fails **before** refunding; `fail_after_next_refund` refunds and fails **after**. |

!!! info "What was measured and what was not"
    At Mercado Pago, the same key and body answering the same response, and
    the same key with another body answering `409
    idempotency_key_already_used`, were measured on the order **create**
    (2026-10-10), not on the refund route. The refund answer lists every
    refund of the order, oldest first (sandbox fixtures), and the adapter
    reads the last one as the one it just made. What OpenPix answers to a
    repeated refund `correlationID` is not in its specification and was not
    measured — which is why idempotency there is the read before and after
    the `POST`, not the provider's.

!!! note "Empty `end_to_end_id` at Mercado Pago"
    The `e2e_id` field of the sandbox's refunds carries a timestamp
    (`"2026-10-10 09:41:40.107"`), not a Pix end-to-end identifier. The
    adapter leaves `PixRefund.end_to_end_id` as `None` and the raw value
    stays in `raw`.

## If you already call `MercadoPagoPixProvider.refund_pix_charge`

The old form is unchanged:

```python
from tempest_fastapi_sdk.integrations.payment import PixCharge
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoPixProvider,
)


async def refund_old_way(provider: MercadoPagoPixProvider, order_id: str) -> PixCharge:
    """Refund R$ 5.00 without a reference, as before the protocol.

    Args:
        provider (MercadoPagoPixProvider): The adapter.
        order_id (str): The paid order's id.

    Returns:
        PixCharge: The order after the refund.
    """
    return await provider.refund_pix_charge(order_id, 500)
```

Without `refund_reference`, the method returns a `PixCharge`, with a new
key per call — retries of that call are safe, **a second call is a second
refund**. With `refund_reference`, it returns a `PixRefund` and gets
everything in this recipe. Both returns are typed by `@overload`: mypy knows
which one you get.

## Recap

- Depend on `PixRefundProvider` when the service refunds.
- Always pass a `refund_reference` of yours, stable, one per refund.
- Retrying with the same reference is safe; no lease, no re-read.
- `get_refunded_cents` asks the provider how much already went out.
- Above the balance: `PixRefundExceedsBalanceException`. A reference reused
  for another amount: `PixRefundReferenceConflictException`.
- To test without a network: `FakePixProvider` + `fail_after_next_refund`.

See also: [Pix protocol](pix-protocol.md) ·
[Mercado Pago](mercado-pago.md) · [OpenPix](openpix.md) ·
[Fakes](fakes.md) · [Idempotency](idempotency.md)
