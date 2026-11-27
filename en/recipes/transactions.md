# Transactions (commit and savepoint)

Every `BaseRepository` write commits on its own. For a single write that
is right — and for a business rule with two writes it is exactly the
problem:

```python
from src.db.models import OrderItemModel, OrderModel
from tempest_fastapi_sdk import BaseRepository


async def create_order_unsafe(
    orders: BaseRepository[OrderModel],
    items: BaseRepository[OrderItemModel],
    order: OrderModel,
    rows: list[OrderItemModel],
) -> None:
    """Persist an order and its items without atomicity — the problem.

    Args:
        orders (BaseRepository[OrderModel]): The order repository.
        items (BaseRepository[OrderItemModel]): The item repository.
        order (OrderModel): The order to persist.
        rows (list[OrderItemModel]): The order's items.
    """
    await orders.add(order)      # already durable
    await items.add_all(rows)    # if this fails, the order is orphaned
```

This page closes that hole.

## The `transaction()` block

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderItemModel, OrderModel
from tempest_fastapi_sdk import BaseRepository, transaction


async def create_order(
    session: AsyncSession,
    orders: BaseRepository[OrderModel],
    items: BaseRepository[OrderItemModel],
    order: OrderModel,
    rows: list[OrderItemModel],
) -> OrderModel:
    """Persist an order and its items atomically.

    Args:
        session (AsyncSession): The session both repositories share.
        orders (BaseRepository[OrderModel]): The order repository.
        items (BaseRepository[OrderItemModel]): The item repository.
        order (OrderModel): The order to persist.
        rows (list[OrderItemModel]): The order's items.

    Returns:
        OrderModel: The persisted order.
    """
    async with transaction(session):
        await orders.add(order)
        await items.add_all(rows)
    return order
```

Inside the block each write flushes instead of committing. A clean exit
produces **one** `COMMIT`; any exception produces **one** `ROLLBACK` and
the exception keeps propagating.

!!! tip "The counter lives on the session, not on the repository"
    Notice that `items` honours the block too, without anyone having
    called anything on it. The state is kept in `session.info`, so
    **every** repository bound to the same `AsyncSession` joins the same
    block. That is what makes a service orchestrating several
    repositories work without threading context between them.

The repository exposes the block as sugar:

```python
from src.db.models import OrderItemModel, OrderModel
from tempest_fastapi_sdk import BaseRepository


async def create_order_with_sugar(
    orders: BaseRepository[OrderModel],
    items: BaseRepository[OrderItemModel],
    order: OrderModel,
    rows: list[OrderItemModel],
) -> None:
    """Open the block through the repository instead of the session.

    Args:
        orders (BaseRepository[OrderModel]): The order repository.
        items (BaseRepository[OrderItemModel]): The item repository.
        order (OrderModel): The order to persist.
        rows (list[OrderItemModel]): The order's items.
    """
    async with orders.transaction():
        await orders.add(order)
        await items.add_all(rows)   # same session, same block
```

## Explicit `commit()`

Sometimes the durable point is a business decision, and calling
`update()` purely for its commit side effect is dishonest. Use `commit()`:

```python
from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository


async def persist(
    orders: BaseRepository[OrderModel],
    order: OrderModel,
) -> None:
    """State the durable point instead of inferring it from an update.

    Args:
        orders (BaseRepository[OrderModel]): The order repository.
        order (OrderModel): The order to persist.
    """
    await orders.add(order)
    await orders.commit()
```

`commit()` inside an open `transaction()` block flushes instead of
committing, because committing there would break the block's
all-or-nothing guarantee. That is what makes the call safe to leave in
place when someone later wraps the code in a block — a bare
`session.commit()` has no such property.

`flush()` (make a row visible to later statements in the same
transaction, without committing) and `rollback()` are there too.

!!! warning "`rollback()` inside a block is refused"
    A rollback there would discard the whole block — including writes
    made by other repositories sharing the session — while the caller
    believes it is undoing only its own step. The method raises
    `RuntimeError` saying so. To abort the block, let the exception
    propagate; for a step you intend to recover from, use `savepoint()`.

## An always-explicit repository: `autocommit=False`

When a whole repository belongs to a caller-owned unit of work:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository


async def persist_explicitly(session: AsyncSession, order: OrderModel) -> None:
    """Build a repository whose commit always belongs to the caller.

    Args:
        session (AsyncSession): The session to use.
        order (OrderModel): The order to persist.
    """
    repository: BaseRepository[OrderModel] = BaseRepository(
        session, model=OrderModel, autocommit=False
    )

    await repository.add(order)   # flush only
    await repository.commit()     # you decide when
```

The flag disables only the **implicit** commit inside the write methods.
An explicit `commit()` still commits.

## `savepoint()`: fail without losing the rest

A real `SAVEPOINT`. A failure reverts only the nested part and the
surrounding transaction stays usable:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AccountModel, AuditEntryModel, NicknameModel
from tempest_fastapi_sdk import (
    BaseRepository,
    ConflictException,
    savepoint,
    transaction,
)


async def open_account(
    session: AsyncSession,
    accounts: BaseRepository[AccountModel],
    nicknames: BaseRepository[NicknameModel],
    audit: BaseRepository[AuditEntryModel],
    account: AccountModel,
    nickname: NicknameModel,
    entry: AuditEntryModel,
) -> None:
    """Open the account even when the nickname is already taken.

    Args:
        session (AsyncSession): The shared session.
        accounts (BaseRepository[AccountModel]): The account repository.
        nicknames (BaseRepository[NicknameModel]): The nickname repository.
        audit (BaseRepository[AuditEntryModel]): The audit repository.
        account (AccountModel): The account to create.
        nickname (NicknameModel): The intended nickname.
        entry (AuditEntryModel): The audit record.
    """
    async with transaction(session):
        await accounts.add(account)
        try:
            async with savepoint(session):
                await nicknames.add(nickname)
        except ConflictException:
            pass          # the account survives; only the nickname reverted
        await audit.add(entry)
```

Without the savepoint, catching that `ConflictException` would leave the
session unusable — SQLAlchemy requires a rollback after a failed flush.

!!! info "SQLite needs configuration, and the SDK already applies it"
    The `pysqlite` driver under `aiosqlite` opens transactions implicitly
    and emits no `BEGIN`. SQLite therefore sees the `SAVEPOINT` as the
    outermost transaction and its matching `RELEASE SAVEPOINT` becomes a
    **commit** — a nested block that exits cleanly turns durable even when
    the outer block is rolled back afterwards. `AsyncDatabaseManager`
    applies SQLAlchemy's documented remedy to every SQLite engine it
    builds. If you build the engine yourself, call
    `enable_sqlite_savepoints(engine)`.

## Nesting

`transaction()` blocks are re-entrant: an inner block joins the outer one
and does not commit on its own. Only the outermost exit commits. That lets
one service call another without either knowing who opened the block.

## Recap

- `transaction(session)` groups everything into one `COMMIT`; the counter
  is on the session, so all of its repositories join.
- `commit()` / `flush()` / `rollback()` on the repository keep the service
  away from `session`; `commit()` degrades to a flush inside a block.
- `autocommit=False` makes a whole repository explicit.
- `savepoint()` isolates a step you intend to recover from.
- `rollback()` inside a block raises `RuntimeError`, on purpose.
