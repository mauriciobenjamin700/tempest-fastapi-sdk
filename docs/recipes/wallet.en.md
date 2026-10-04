# Wallet (balance and statement)

Every product that pays a user out — a driver, a seller, a creator — ends
up writing a wallet: balance, statement, hold. And the steps that go wrong
go wrong the same way in every product:

- **a credit lost to concurrency**: two sales settled at the same time
  read the same balance, add in Python and write the value — one of them
  vanishes;
- **a debit that pays twice**: two simultaneous withdrawals read the same
  balance and pass the same check;
- **held money spent early**: "available" is computed in Python and the
  debit only checks `balance >= amount`;
- **a webhook delivered twice credits twice.**

The `tempest_fastapi_sdk.wallet` module solves all four with one design:

- the **balance** is an integer column (cents) on the user's row, moved
  **only** by a conditional `UPDATE` with `RETURNING` — no read first;
- the **statement** is an append-only table: each movement records the
  signed amount, the balance it produced and the event that caused it;
- the **held** amount is the sum of the statement lines whose release is
  still in the future, computed **inside** the debit's `WHERE`.

!!! info "No extra"
    Core SDK only: SQLAlchemy already ships with the base install.

## The user model gets a balance

Mix `WalletBalanceMixin` into your user model. It adds
`wallet_cents: int` (`NOT NULL`, default `0`) with a
`CHECK (wallet_cents >= 0)`:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.wallet import WalletBalanceMixin


class UserModel(WalletBalanceMixin, BaseModel):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(64))
```

The `CHECK` lives in the database, not in Python: not even a hand-written
`UPDATE` outside the service can leave the balance negative. The
constraint name follows the SDK convention —
`ck_users_wallet_cents_non_negative` — so a migration can name it.

!!! tip "Need a negative balance?"
    Reversing a credit the user already withdrew (a chargeback, say) has
    only two outcomes: refuse it or record the debt. With
    `WalletBalanceMixin`, `reverse` refuses with
    `WalletInsufficientFundsException`. With `OverdraftWalletBalanceMixin`
    — the same column, without the `CHECK` — the reversal is written and
    the balance goes negative. Ordinary debits still never overdraw in
    either case: `debit` only matches the row when the **available**
    balance covers the amount.

## The statement

The SDK ships the abstract row, `BaseWalletEntryModel`; the project ships
the concrete one, with the FK to the user. For tests and scripts, the
factory builds one:

```python
from tempest_fastapi_sdk.wallet import make_wallet_entry_model

WalletEntryModel = make_wallet_entry_model(
    user_table="users",
    tablename="wallet_entries",
)
```

Each line stores:

| Column | What it is |
| --- | --- |
| `kind` | `"credit"`, `"debit"`, `"reversal"` or an application kind |
| `amount_cents` | **signed** amount: positive adds, negative takes |
| `balance_after_cents` | the balance right after this movement |
| `available_at` | when the movement stops being held |
| `reference_type` / `reference_id` | the causing event (`"order"` + the order id) |
| `description` | free text for the statement |
| `idempotency_key` | optional caller key, unique when present |

The table ships with `UNIQUE (reference_type, reference_id, kind)` and an
index on `(user_id, available_at)` — the exact shape of the held
subquery. The factory's FK is `ON DELETE RESTRICT`: a statement line is a
financial record, so deleting a user who still has one is refused rather
than cascaded.

!!! warning "A hand-written subclass extends `__table_args__`"
    When you write the concrete class yourself and need more
    `__table_args__`, add to the parent's (`super().__table_args__`)
    instead of replacing them. The reference's unique constraint is what
    turns a repeated event into a no-op.

## Putting it together: credit, hold, debit, statement

A complete example that runs as is:

```python
import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseModel, BaseRepository
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletInsufficientFundsException,
    WalletRepository,
    WalletService,
    make_wallet_entry_model,
)


class UserModel(WalletBalanceMixin, BaseModel):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(64))


WalletEntryModel = make_wallet_entry_model(
    user_table="users",
    tablename="wallet_entries",
)


def build_wallet(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=WalletRepository(
            session,
            model=UserModel,
            entry_model=WalletEntryModel,
        ),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        driver = UserModel(name="Ana")
        session.add(driver)
    driver_id: UUID = driver.id

    async with db.get_session_context() as session:
        wallet = build_wallet(session)
        order_id = uuid4()
        await wallet.credit(
            driver_id,
            3_330,
            reference_type="order",
            reference_id=order_id,
            hold=timedelta(hours=48),
            description="Order paid",
        )
        replay = await wallet.credit(
            driver_id,
            3_330,
            reference_type="order",
            reference_id=order_id,
            hold=timedelta(hours=48),
        )
        print("replay balance_after:", replay.balance_after_cents)

        balance = await wallet.balance(driver_id)
        print("total:", balance.total_cents)
        print("held:", balance.held_cents)
        print("available:", balance.available_cents)

        try:
            await wallet.debit(
                driver_id,
                1_000,
                reference_type="withdraw",
                reference_id=uuid4(),
            )
        except WalletInsufficientFundsException as exc:
            print("refused:", exc.code)

        page = await wallet.statement(driver_id)
        print("lines:", page.total)

    await db.disconnect()


asyncio.run(main())
```

Output:

```text
replay balance_after: 3330
total: 3330
held: 3330
available: 0
refused: WALLET_INSUFFICIENT_FUNDS
lines: 1
```

Piece by piece:

- **`credit(..., hold=timedelta(hours=48))`** adds R$ 33.30 to the
  balance and writes the line with `available_at` 48 hours ahead. The
  money is in the wallet, but held.
- **The second `credit` with the same `reference_id`** is the webhook
  delivered again. It returns the line the first call wrote and moves
  nothing — the balance stays 3330 and the statement has **one** line.
- **`balance`** splits the total (3330) from the held part (3330) and the
  available part (0), in one query. `next_release_at` says when the next
  held credit is released.
- **The R$ 10.00 `debit`** is refused: nothing is available.
  `WalletInsufficientFundsException` answers 409 with the code
  `WALLET_INSUFFICIENT_FUNDS`, translated in PT-BR and EN-US by the SDK
  catalog.
- **`statement`** returns a `BasePaginationSchema[WalletEntrySchema]`,
  newest first. A wallet with no movement returns `items=[]`, never an
  error.

!!! note "A replay leaves a `WARNING` in the log"
    The replay path goes through the `INSERT` the unique constraint
    refuses — that is what keeps a replay safe under concurrency too
    (below). `BaseRepository.add` logs each refusal at `WARNING`
    (`IntegrityError on WalletEntryModel.add: unique violation ...`), so a
    redelivered webhook shows up in the log once per redelivery.

## Same reference, another amount: a conflict

A replay is the **same** reference with the **same** owner and the
**same** amount. The same reference with another amount, or for another
wallet, is not a replay — it is two events claiming one identity, and the
service does not pick one on a guess: it raises
`WalletReferenceConflictException` (409, `WALLET_REFERENCE_CONFLICT`) and
moves nothing.

The optional `idempotency_key` is a second replay key, for a movement
with no natural event — a manual adjustment coming from a request with an
`Idempotency-Key` header, for example.

## Reversal

`reverse(entry_id)` undoes a line with an opposite line, of
`kind="reversal"`, that references the original. The statement stays
append-only — the original remains. Reversing the same line twice is a
replay:

- **reversing a debit** (refunding a refused withdrawal) gives the amount
  back, available at once;
- **reversing a credit** is a **forced** debit: it ignores the hold,
  because the money it takes back is exactly what the credit added. A
  credit still held is reversed with its own `available_at`, and leaves
  the held amount with it.

## Together with your domain: `claim_once`

A sale should not credit twice, and the row that knows it is **your**
order. `claim_once` sets a nullable column only if it is still `NULL`, in
a single `UPDATE`:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository, transaction
from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletRepository,
    WalletService,
    make_wallet_entry_model,
)


class SellerModel(WalletBalanceMixin, BaseModel):
    __tablename__ = "sellers"


class OrderModel(BaseModel):
    __tablename__ = "orders"

    seller_id: Mapped[UUID]
    total_cents: Mapped[int]
    credited_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


SellerEntryModel = make_wallet_entry_model(
    user_table="sellers",
    tablename="seller_wallet_entries",
)


async def settle(session: AsyncSession, order: OrderModel) -> bool:
    balances = WalletRepository(
        session,
        model=SellerModel,
        entry_model=SellerEntryModel,
    )
    wallet = WalletService(
        balances=balances,
        entries=BaseRepository(session, model=SellerEntryModel),
    )
    async with transaction(session):
        if not await balances.claim_once(OrderModel, order.id, "credited_at"):
            return False
        await wallet.credit(
            order.seller_id,
            order.total_cents,
            reference_type="order",
            reference_id=order.id,
        )
    return True
```

The first call sets `credited_at` and credits; every later call — a
redelivered webhook, a retried job — gets `False` and does nothing. Both
writes share one `transaction()`, so a failing credit releases the claim
too.

## Why a single `UPDATE`

Every write method of `WalletRepository` is **one**
`UPDATE ... WHERE ... RETURNING`. The debit reaches PostgreSQL 17 in this
form — captured with `before_cursor_execute` over the suite's tables,
with the names swapped for the example's and reindented; `$1` is the
amount already negated:

```sql
UPDATE users
SET wallet_cents = (users.wallet_cents + $1::INTEGER),
    updated_at = $2::TIMESTAMP WITH TIME ZONE
WHERE users.id = $3::UUID
  AND users.wallet_cents - (
      SELECT coalesce(sum(wallet_entries.amount_cents), $4::INTEGER)
      FROM wallet_entries
      WHERE wallet_entries.user_id = users.id
        AND wallet_entries.available_at > $5::TIMESTAMP WITH TIME ZONE
  ) >= $6::INTEGER
RETURNING users.wallet_cents, users.id
```

There is no read first, so there is no window between "check" and
"write" for another request to fall into. No returned row means a
refusal, and only then does the service ask whether the wallet exists,
to answer 404 instead of 409.

Measured with the interleaving **forced** — the first request writes and
holds its transaction open for 20 ms; the second starts only after the
first signalled, so it always runs while the first is uncommitted —, 50
races per scenario, on SQLite and on PostgreSQL 17:

| Shape | SQLite | PostgreSQL |
| --- | --- | --- |
| read the balance, add in Python, write the value | 50/50 credits lost | 50/50 credits lost |
| the same with `SELECT ... FOR UPDATE` | 50/50 credits lost | 0/50 |
| `WalletService.credit` × 2 | 0/50 | 0/50 |
| `WalletService.debit` × 2 against the same balance | 0/50 paid twice | 0/50 paid twice |
| `debit` × 2 of 500 with 1000 in the wallet, 500 held | 0/50 spent held money | 0/50 spent held money |
| the same webhook delivered twice at once | 0/50 credited twice | 0/50 credited twice |

The SQLite rows with losses are from an engine in the driver's default
configuration, where the loss is silent: both requests commit and one
credit vanishes. Under the explicit `BEGIN` that `AsyncDatabaseManager`
turns on, the same race (no lock) fails loudly: in 46 of 50 races one of
the requests got `database is locked` and its credit did not land; in the
other 4 the connection returned to the pool was left unusable for the
next `BEGIN`. Safer than silent, but the credit is still lost.

!!! danger "`FOR UPDATE` does not exist on SQLite"
    SQLAlchemy's SQLite dialect compiles `select(...).with_for_update()`
    **without** the clause, with no error and no warning. A design that
    leans on the lock is right in production and wrong on the test
    database — exactly where nobody looks. The conditional `UPDATE`
    behaves the same on both, which is why the wallet takes no lock.

The numbers come from `tests/test_wallet_concurrency_guard.py`, which
runs on SQLite always and on PostgreSQL under `make test-docker` (or with
`TEST_POSTGRES_URL`). `tests/test_wallet_update_shape_guard.py` pins the
property behind them: every write method emits exactly one `UPDATE` with
a `WHERE`.

## What comes next

This is the first step of issue #400. The next ones, **not shipped yet**,
are the PIX withdrawal (debit the available balance, create and approve
the transfer, give the balance back with a reversal line when PIX
refuses), the fee tiers and the basis-point split, and an optional HTTP
router. Until then, a withdrawal flow is built from `debit` + `reverse`.

## Recap

- The balance is `wallet_cents` on the user's row (`WalletBalanceMixin`,
  or `OverdraftWalletBalanceMixin` to accept a debt on reversal).
- The statement is append-only (`BaseWalletEntryModel` /
  `make_wallet_entry_model`), with `UNIQUE (reference_type, reference_id,
  kind)`.
- `WalletService.credit` / `debit` / `reverse` move the balance and write
  the line in the same transaction; the same event repeated returns the
  line that already exists.
- The held amount is discounted **inside** the debit's `UPDATE`.
- `claim_once` marks your domain's row exactly once.
- Everything in integer cents.
