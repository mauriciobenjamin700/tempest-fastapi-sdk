# Wallet and Pix payouts

A platform that takes payments by Pix and passes money on to someone — a
driver, an artist, a seller — needs three things: a balance per person, the
history of every cent that came in and went out, and a withdraw button. It
looks like little code, which is exactly why it goes wrong the same way in
every product:

- **lost credit**: reading the balance in Python, adding, and writing it back
  loses one of the credits when two sales of the same driver settle at the
  same time;
- **double payout**: reading the balance, calling Pix and only then zeroing
  it pays twice when the person presses the button twice;
- **drained hold**: computing "what is available" before debiting lets two
  concurrent withdrawals take money that should still be on hold;
- **money as `float`**: `33.30 - 30.13` becomes `3.169999999999998`.

The `tempest_fastapi_sdk.wallet` module solves all four once. Every amount is
an **integer number of cents**.

## The models

The balance lives on your user row, and every movement becomes a ledger
line:

```python
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseUserModel
from tempest_fastapi_sdk.wallet import BaseWalletEntryModel, WalletBalanceMixin


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


class WalletEntryModel(BaseWalletEntryModel):
    __tablename__ = "wallet_entries"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
```

- `WalletBalanceMixin` adds `wallet_cents` (`BIGINT`, default `0`).
- `BaseWalletEntryModel` brings `kind`, `amount_cents` (signed: positive in,
  negative out), `balance_after_cents`, `available_at` (when the credit can
  be withdrawn), `description`, `reference_type` and `reference_id`. You only
  declare the user FK and the table name.

!!! warning "`RESTRICT`, not `CASCADE`"
    With `CASCADE`, deleting the user deletes the money history with it.
    With `RESTRICT`, the database refuses — deactivate or anonymize the user
    instead. `make_wallet_entry_model()` (for tests and scripts) defaults to
    `RESTRICT`.

## Crediting, with and without a hold

```python
import asyncio
from datetime import timedelta
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseRepository, BaseUserModel
from tempest_fastapi_sdk.wallet import (
    BaseWalletEntryModel,
    WalletBalanceMixin,
    WalletService,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


class WalletEntryModel(BaseWalletEntryModel):
    __tablename__ = "wallet_entries"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def wallet_service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="driver@example.com", hashed_password="x")
        session.add(user)
        await session.commit()

        service = wallet_service(session)
        await service.credit(user.id, 9_320, kind="TICKET_SALE")
        await service.credit(
            user.id, 4_500, kind="TICKET_SALE", hold=timedelta(hours=48)
        )

        balance = await service.balance(user.id)
        print(balance.total_cents, balance.held_cents, balance.available_cents)
    await db.disconnect()


asyncio.run(main())
```

Output:

```text
13820 4500 9320
```

- `WalletService` takes **two repositories on the same session**: the balance
  owner's and the ledger's. Different sessions are refused by the
  constructor, because the balance and its ledger line must land in the same
  commit.
- `credit()` runs a single `UPDATE ... SET wallet_cents = wallet_cents + :n
  RETURNING` and writes the ledger line in the same transaction. Nothing is
  read in Python first.
- `hold=timedelta(hours=48)` sets `available_at` 48 hours ahead. The credit
  counts in the total but not in the available amount until then.
- `kind` is your vocabulary (`"TICKET_SALE"`, `"ALO_SALE"`). The kinds the
  service writes on its own are in `WalletEntryKindEnum`.

!!! info "Measured under real concurrency"
    `tests/wallet/test_wallet_live.py` runs 50 concurrent tasks, each on its
    own session, against Postgres in a container. 50 credits of `100` always
    end at `5000`; and the control test, running the naive version (read,
    add, write) in the same setup, ends below `5000`. Measured on
    `postgres:16-alpine`: five consecutive runs of
    `pytest tests/wallet/test_wallet_live.py -m docker`, all 4 tests passing
    in all five.

## Withdrawing

The withdrawal is the part with the most ways to go wrong, so the order is
fixed:

1. **debit first**, only from the available part — the hold is part of the
   `UPDATE`'s `WHERE`, so two concurrent withdrawals cannot both pass;
2. **call the provider** after the debit commits;
3. **give the money back only if the provider definitively refused**.

```python
import asyncio
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    PayoutRejectedException,
)
from tempest_fastapi_sdk import PixKeyType
from tempest_fastapi_sdk.testing.fakes import FakePayoutProvider
from tempest_fastapi_sdk.wallet import (
    BaseWalletEntryModel,
    WalletBalanceMixin,
    WalletService,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


class WalletEntryModel(BaseWalletEntryModel):
    __tablename__ = "wallet_entries"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def wallet_service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="driver@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        service = wallet_service(session)
        await service.credit(user.id, 9_320, kind="TICKET_SALE")

        payout = FakePayoutProvider()
        payout.fail_next(PayoutRejectedException("unknown Pix key"))
        try:
            await service.withdraw(
                user.id,
                payout=payout,
                pix_key="driver@example.com",
                pix_key_type=PixKeyType.EMAIL,
            )
        except PayoutRejectedException:
            print("refused:", (await service.balance(user.id)).available_cents)

        result = await service.withdraw(
            user.id,
            payout=payout,
            pix_key="driver@example.com",
            pix_key_type=PixKeyType.EMAIL,
        )
        print("paid:", result.entry.amount_cents, result.payout.status)
        print("balance:", (await service.balance(user.id)).total_cents)

        page = await service.statement(user.id)
        print([entry.kind for entry in page["items"]])
    await db.disconnect()


asyncio.run(main())
```

Output:

```text
refused: 9320
paid: -9320 confirmed
balance: 0
['WITHDRAW', 'WITHDRAW_REFUND', 'WITHDRAW', 'TICKET_SALE']
```

The first attempt was refused: the debit went out, the provider refused, and
`WITHDRAW_REFUND` returned the amount. The second one paid. The statement
shows all four lines, newest first.

What happens on each outcome:

| The provider... | Exception | The debit |
| --- | --- | --- |
| accepted | — (returns `WithdrawalSchema`) | stays |
| definitively refused | `PayoutRejectedException` (502) | **comes back** (`WITHDRAW_REFUND`) |
| timed out, dropped the connection, answered something unreadable | `PayoutUncertainException` (502) | **stays**, logged `CRITICAL` |
| — nothing was available | `InsufficientBalanceException` (409) | nothing was debited |

!!! danger "Why the debit stays when the outcome is unknown"
    On a timeout, the Pix may have left. Giving the balance back there lets
    the person withdraw the same money again. The `CRITICAL` log carries the
    `correlation_id`: reconciliation is checking that id at the provider and,
    if the Pix did not leave, crediting it back with
    `credit(..., kind="WITHDRAW_REFUND")`.

!!! tip "The Pix key comes from the profile"
    `withdraw()` takes `pix_key` from the caller. Read it from the user's
    registration, never from the request body — otherwise anyone can pay
    themselves out of someone else's wallet. The router below already does
    it that way.

## Withdrawing through OpenPix

In production, swap the fake for `OpenPixPayoutProvider`:

```python
from tempest_fastapi_sdk import HTTPClient, RetryPolicy
from tempest_fastapi_sdk.integrations.payment.adapters import OpenPixPayoutProvider
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEnvironmentEnum

http: HTTPClient = HTTPClient(
    base_url=OpenPixEnvironmentEnum.SANDBOX.base_url,
    default_headers={"Authorization": "<your AppID>"},
    retry_policy=RetryPolicy(max_attempts=1),
)
payout: OpenPixPayoutProvider = OpenPixPayoutProvider(http)
```

- The adapter sends `autoApprove: true`: it creates and approves the payment
  in one call, with no "created, not yet approved" window.
- HTTP 4xx, or a `DENIED`/`FAILED` payment, become `PayoutRejectedException`.
  `CONFIRMED` becomes `PayoutStatusEnum.CONFIRMED`. `CREATED`, `APPROVED` or a
  state the adapter does not know become `PayoutStatusEnum.PENDING`, with the
  original value in `provider_status` — settlement arrives later through the
  `OPENPIX:MOVEMENT_*` webhooks.

!!! warning "`RetryPolicy(max_attempts=1)` is required, and the adapter checks it"
    `HTTPClient` retries **any** method on 429, 5xx and read timeouts.
    Measured with the default policy: one `POST` answered `500` goes out
    three times. For a payout, the first `POST` may have created the payment,
    and the second would get an answer the adapter reads as a refusal — the
    wallet would give back money that already left. That is why
    `OpenPixPayoutProvider` raises `ValueError` in its constructor when the
    `HTTPClient` can retry.

## Withdrawing by hand (`ManualPayoutProvider`)

With no automated payout — Mercado Pago's is not enabled for the account
(#499) —, `ManualPayoutProvider` implements the same `PayoutProvider` by
e-mailing the administrator the Pix to send: amount in reais, key and type,
`user_id` (from `PayoutRequest.payee_id`, which `withdraw` fills),
`correlation_id` and comment. It returns `PayoutStatusEnum.PENDING` with
`provider_status="manual_review"`, and the debit follows the usual rule:

- the mailer raises `EmailDeliveryException` or
  `IntegrationDisabledException` → `PayoutRejectedException`: no
  administrator got the order, so nobody will pay, and the debit comes back;
- any other error propagates, and the debit stays (`PayoutUncertainException`).

The log carries the key masked (`mask_pix_key`), never whole. With
`records=` (a `BaseRepository` of the `make_manual_payout_model` table),
each withdrawal also becomes a `PENDING` row the administrator completes in
the panel with the PDF receipt. The ready-made route that ties it together —
a withdrawal that empties the wallet, the row, the e-mail and the panel — is
in [Mercado Pago: testing webhooks » Manual withdrawal](mercado-pago-webhooks.md#manual-withdrawal).

## Completing or refusing a withdrawal by hand (`ManualPayoutService`)

The `PENDING` row leaves that state in two ways, and both live in
`ManualPayoutService` — the same class the panel's actions call, so a
service that exposes the queue through its own API
(for example, `PATCH /api/admin/withdrawals`) does not reimplement the rule:

- `mark_paid(row_id, end_to_end_id=None, *, actor=None, require_receipt=False)`
  → `COMPLETED`, with `completed_at`, `completed_by` and the Pix
  `end_to_end_id`;
- `reject(row_id, reason, *, actor=None)` → `REJECTED`, with
  `rejection_reason`, `rejected_at` and `rejected_by`, **and** a
  `WITHDRAW_REFUND` line for the amount in the wallet (`reference_id` is the
  `correlation_id`, the same as the `WITHDRAW` line's), in the same commit.

Both change the status with an `UPDATE ... WHERE id = :id AND status =
'pending'`: two administrators on the same row move it once, and the second
gets `ManualPayoutNotPendingException` (`409
WALLET_MANUAL_PAYOUT_NOT_PENDING`) with nothing changed — not the status,
not the wallet, not a hook. An unknown id is `ManualPayoutNotFoundException`
(`404`); `require_receipt=True` without a receipt is
`ManualPayoutReceiptMissingException` (`409`).

The history the user sees in the app belongs to the service, not the SDK. To
keep it in step with the withdrawal, `ManualPayoutHooks` takes one callback
per transition — `on_requested`, `on_canceled`, `on_paid`, `on_rejected` —,
each called with the session and the row **inside** the transition's
transaction. Pass the same instance to `ManualPayoutProvider` (which runs
`on_requested` and `on_canceled`) and to `ManualPayoutService` (`on_paid` and
`on_rejected`):

```python
import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    PixKeyType,
)
from tempest_fastapi_sdk.exceptions import ManualPayoutNotPendingException
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils
from tempest_fastapi_sdk.wallet import (
    ManualPayoutHooks,
    ManualPayoutProvider,
    ManualPayoutService,
    WalletBalanceMixin,
    WalletService,
    make_manual_payout_model,
    make_wallet_entry_model,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


WalletEntryModel = make_wallet_entry_model(user_table="users")
ManualPayoutModel = make_manual_payout_model(user_table="users")


class WithdrawalHistoryModel(BaseModel):
    """The line the user sees in the app's history."""

    __tablename__ = "withdrawal_history"

    correlation_id: Mapped[str] = mapped_column(String(128), unique=True)
    label: Mapped[str] = mapped_column(String(32))


async def on_requested(session: AsyncSession, row: Any) -> None:
    session.add(
        WithdrawalHistoryModel(
            correlation_id=row.correlation_id, label="SAQUE SOLICITADO"
        )
    )


async def _relabel(session: AsyncSession, row: Any, label: str) -> None:
    line = (
        await session.execute(
            select(WithdrawalHistoryModel).where(
                WithdrawalHistoryModel.correlation_id == row.correlation_id
            )
        )
    ).scalar_one()
    line.label = label


async def on_paid(session: AsyncSession, row: Any) -> None:
    await _relabel(session, row, "SAQUE REALIZADO")


async def on_rejected(session: AsyncSession, row: Any) -> None:
    await _relabel(session, row, "SAQUE RECUSADO")


HOOKS = ManualPayoutHooks(
    on_requested=on_requested, on_paid=on_paid, on_rejected=on_rejected
)


def wallet(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


def payouts(session: AsyncSession) -> ManualPayoutService:
    return ManualPayoutService(
        records=BaseRepository(session, model=ManualPayoutModel),
        ledger=wallet(session).ledger,
        hooks=HOOKS,
    )


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="motorista@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        user_id: UUID = user.id
        await wallet(session).credit(user_id, 12_345, kind="SALE")
        provider = ManualPayoutProvider(
            FakeEmailUtils(),
            admin_emails=["financeiro@example.com"],
            records=BaseRepository(session, model=ManualPayoutModel),
            hooks=HOOKS,
        )
        await wallet(session).withdraw(
            user_id,
            payout=provider,
            pix_key="motorista@example.com",
            pix_key_type=PixKeyType.EMAIL,
            correlation_id="saque-1",
        )
        row = (await session.execute(select(ManualPayoutModel))).scalar_one()
        print("depois do saque  ", (await wallet(session).balance(user_id)).total_cents)

    async with db.get_session_context() as session:
        rejected = await payouts(session).reject(
            row.id, "chave Pix de outra pessoa", actor="admin-1"
        )
        print("status           ", rejected.status)
        print("depois da recusa ", (await wallet(session).balance(user_id)).total_cents)
        history = (await session.execute(select(WithdrawalHistoryModel))).scalar_one()
        print("histórico        ", history.label)

    async with db.get_session_context() as session:
        try:
            await payouts(session).mark_paid(row.id, actor="admin-2")
        except ManualPayoutNotPendingException as error:
            print("segundo admin    ", error.code)

    await db.disconnect()


asyncio.run(main())
```

Running it:

```text
depois do saque   0
status            rejected
depois da recusa  12345
histórico         SAQUE RECUSADO
segundo admin     WALLET_MANUAL_PAYOUT_NOT_PENDING
```

!!! check "A hook that raises undoes the whole transition"
    If `on_rejected` raises, the status stays `PENDING`, the
    `WITHDRAW_REFUND` line does not exist and the balance did not change —
    the tests in `tests/wallet/test_manual_payout_service.py` check all
    three. In `on_requested`, raising refuses the withdrawal: the row is not
    written, no e-mail goes out and the debit comes back. In `on_canceled`
    (the e-mail failed), raising leaves the row `PENDING` and the debit kept
    (`PayoutUncertainException`), the same as an unknown error.

`reject` needs the `ledger` (`WalletService.ledger` will do) on the
repository's session — a different session is a `ValueError` in the
constructor, because the refund and the status would commit apart. Without a
`ledger`, only `mark_paid` works. The panel wires all of this on its own: see
[Admin panel » Preset: manual payout](admin/forms.md#preset-manual-payout-manual_payout_admin).

## Fees and splits

`openpix_fee_cents()` computes the OpenPix fee, and `split_net()` divides the
total between platform, gateway and payees:

```python
from tempest_fastapi_sdk.wallet import openpix_fee_cents, split_net

total = 10_000

driver = split_net(
    total,
    platform_bps=500,
    gateway_fee_cents=openpix_fee_cents(total),
    residual_recipient="driver",
)
print(driver.platform_cents, driver.gateway_fee_cents, dict(driver.shares))

producer = split_net(
    total,
    platform_bps=1_500,
    gateway_fee_cents=openpix_fee_cents(total),
    residual_recipient="producer",
    shares_bps={"interlocutor": 2_000},
)
print(producer.platform_cents, producer.net_cents, dict(producer.shares))
```

Output:

```text
500 180 {'driver': 9320}
1500 8320 {'interlocutor': 1664, 'producer': 6656}
```

- Percentages are **basis points**: `500` is 5 %, `10_000` is 100 %. All
  integer division, rounding down.
- The platform takes its cut from the **gross**; the gateway fee comes off
  next; what remains is the net. Every entry of `shares_bps` takes its share
  **of the net**, and `residual_recipient` keeps the rest — so the cent the
  rounding leaves over lands on a known person instead of disappearing. When
  the fees exceed the total, the net is `0`.
- `OPENPIX_FEE_TIERS` is the schedule Tempest services use today (up to
  R$ 62.50: R$ 0.50; up to R$ 625.00: 0.8 %; above: R$ 5.00; plus a fixed
  R$ 1.00). A different contract? Pass your own `OpenPixFeeTiers` as
  `tiers=`.

??? note "Compared with the services that already existed"
    Measured over every total from R$ 0.01 to R$ 2,000.00 (200,000 values):
    `openpix_fee_cents` and `split_net` (15 % platform, interlocutors over
    the net) give exactly what alofans-api computes. Against
    transport-backend, which rounds in reais with `round()`, the driver gets
    0, 1 or 2 cents more here (87,928, 98,435 and 13,637 of the 200,000
    totals) — the SDK always rounds the platform's cut down.

## Settling once

The charge webhook arrives more than once, and can arrive together with the
manual confirmation. `claim_once()` stamps the row only while the column is
still empty:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.db.transaction import transaction
from tempest_fastapi_sdk.wallet import WalletLedger, claim_once


class OrderModel(BaseModel):
    __tablename__ = "orders"

    seller_id: Mapped[UUID] = mapped_column(nullable=False)
    amount_cents: Mapped[int] = mapped_column(nullable=False)
    credited_at: Mapped[datetime | None] = mapped_column(nullable=True)


async def settle(
    session: AsyncSession, ledger: WalletLedger, order: OrderModel
) -> bool:
    async with transaction(session):
        if not await claim_once(session, OrderModel, order.id, "credited_at"):
            return False
        await ledger.credit(
            order.seller_id,
            order.amount_cents,
            kind="SALE",
            reference_type="order",
            reference_id=str(order.id),
        )
    return True
```

The second call gets `False` and does not credit again. `claim_once()` does
not commit: it joins the same block as the credit, so a failure in the
credit rolls the claim back too.

### The ledger, not the service — `WalletLedger`

Note that `settle` receives a `WalletLedger`, not the `WalletService`. The
ledger is the wallet's data layer: `credit`, `debit_available`, `reverse`,
`balance` and `statement`, each move one `UPDATE ... RETURNING` plus the
statement line. Inside a caller's `transaction()` block it **joins the
block** and does not commit on its own: the credit and the caller's writes
land together, or roll back together when something raises (both cases are
in `tests/wallet/test_ledger.py`, on SQLite and PostgreSQL).

That is why your project's service that has to move a balance in its own
transaction — settling an order, paying out at the end of a trip — receives
the **ledger**, the way it receives any repository:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.wallet import WalletLedger

from src.db.models import UserModel, WalletEntryModel


def build_wallet_ledger(session: AsyncSession) -> WalletLedger:
    """Assemble the wallet ledger over the request session.

    Args:
        session (AsyncSession): The session both repositories share.

    Returns:
        WalletLedger: The ledger.
    """
    return WalletLedger(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )
```

`WalletService` is still what `make_wallet_router` and the **withdrawal**
use: `withdraw` debits, calls the `PayoutProvider` and gives the money back
on refusal. It takes the ledger ready (`WalletService(ledger=...)`) or the
two repositories, as always (it builds the ledger inside), and still exposes
`credit`, `balance` and the rest by delegation.

| Use | When |
| --- | --- |
| `WalletLedger` | a project service credits, debits or reverses inside its own transaction |
| `WalletService` | the Pix withdrawal (`withdraw`) and the wallet router |

A service receiving the `WalletService` is a service inside a service —
`tempest lint-layers` reports it as `DI002`. Receiving the ledger needs no
exception.

## The router

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    HTTPClient,
    RetryPolicy,
)
from tempest_fastapi_sdk import PixKeyType
from tempest_fastapi_sdk.integrations.payment.adapters import OpenPixPayoutProvider
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEnvironmentEnum
from tempest_fastapi_sdk.wallet import (
    BaseWalletEntryModel,
    PixDestinationSchema,
    WalletBalanceMixin,
    WalletService,
    make_wallet_router,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


class WalletEntryModel(BaseWalletEntryModel):
    __tablename__ = "wallet_entries"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
payout = OpenPixPayoutProvider(
    HTTPClient(
        base_url=OpenPixEnvironmentEnum.SANDBOX.base_url,
        default_headers={"Authorization": "<your AppID>"},
        retry_policy=RetryPolicy(max_attempts=1),
    )
)


async def sessions() -> AsyncIterator[AsyncSession]:
    async with db.get_session_context() as session:
        yield session


def wallet_service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


def current_user_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


def pix_destination() -> PixDestinationSchema:
    return PixDestinationSchema(
        pix_key="driver@example.com", pix_key_type=PixKeyType.EMAIL
    )


def payout_provider() -> OpenPixPayoutProvider:
    return payout


app = FastAPI()
app.include_router(
    make_wallet_router(
        service_factory=wallet_service,
        session_factory=sessions,
        current_user_id=current_user_id,
        payout_provider=payout_provider,
        pix_destination=pix_destination,
    )
)
```

| Route | What it does |
| --- | --- |
| `GET /api/wallet/balance` | the logged-in user's `WalletBalanceSchema` |
| `GET /api/wallet/statement?page=1&page_size=20` | paginated ledger, newest first |
| `POST /api/wallet/withdraw` `{"amount_cents": 500}` | withdraws (without `amount_cents`, the whole available balance) |

`current_user_id` and `pix_destination` are the two you replace with yours:
the first comes from your authentication; the second reads the key from the
user's registration and raises when no key is registered. No route accepts a
Pix key or a user id in the body.

## A model that already has a balance column

A service that already keeps the balance in a `wallet` column (in cents)
does not need the mixin. Point the service at it:

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.wallet import WalletService, make_wallet_entry_model


class LegacyUserModel(BaseModel):
    __tablename__ = "legacy_users"

    wallet: Mapped[int] = mapped_column(nullable=False, default=0)


LegacyEntryModel = make_wallet_entry_model(
    user_table="legacy_users",
    tablename="legacy_wallet_entries",
    class_name="LegacyEntryModel",
)


def wallet_service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=LegacyUserModel),
        entries=BaseRepository(session, model=LegacyEntryModel),
        balance_attribute="wallet",
    )
```

Whoever already had a balance before the ledger existed gets a first
`WalletEntryKindEnum.OPENING_BALANCE` line in the migration, so the ledger adds
up to the balance.

## Recap

- The balance is integer cents on the user row; every movement is a ledger
  line in the same transaction.
- Every movement is a single `UPDATE` in the database: credits are not lost
  and withdrawals do not pay twice, measured with 50 concurrent tasks on
  Postgres.
- The hold is part of the debit, not of an earlier read.
- A project service that moves a balance in its own transaction receives
  the `WalletLedger`; `WalletService` is the withdrawal and the router.
- A withdrawal debits first, gives back only on a definitive refusal and
  keeps the debit when the outcome is unknown.
- `split_net` in basis points never loses or invents a cent.
- A manual withdrawal leaves `PENDING` through `ManualPayoutService`:
  `mark_paid` or `reject`, which refunds the wallet in the same commit;
  hooks write the user's history in the same transaction.
- To test without credentials, use `FakePayoutProvider` (see
  [Fakes](fakes.md)).
