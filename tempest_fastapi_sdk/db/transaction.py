"""Session-scoped transaction control shared by every repository.

Every :class:`~tempest_fastapi_sdk.db.repository.BaseRepository` write
commits on its own, which is the right default for a single-statement
use case and the wrong one the moment a business rule spans two writes:

```python
await orders.add(order)      # committed here
await items.add_all(rows)    # ...and if this fails, the order is already durable
```

:func:`transaction` closes that hole. The block's depth counter lives on
``session.info`` — **not** on the repository — so every repository bound
to the same :class:`~sqlalchemy.ext.asyncio.AsyncSession` sees the same
open block, which is what makes a service orchestrating several
repositories work:

```python
async with transaction(session):
    await orders.add(order)      # flush only
    await items.add_all(rows)    # flush only
# clean exit -> one COMMIT   |   exception -> one ROLLBACK
```

Nesting is re-entrant: an inner block joins the outer one and only the
outermost exit commits. To let an inner failure be caught and recovered
from without discarding the outer work, use :func:`savepoint`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

TRANSACTION_DEPTH_KEY: Final[str] = "tempest_transaction_depth"
"""Key under which the open-block depth is stored in ``session.info``.

``session.info`` is SQLAlchemy's documented per-session user namespace,
which is why the counter can be shared across repositories without any
of them holding a reference to the others. The key is a named constant
rather than an inline literal so a reader grepping for it finds every
use, and so a typo cannot silently create a second counter.
"""


def transaction_depth(session: AsyncSession) -> int:
    """Return how many :func:`transaction` blocks are open on ``session``.

    Args:
        session (AsyncSession): The session to inspect.

    Returns:
        int: ``0`` when no block is open, otherwise the nesting depth.
    """
    depth = session.info.get(TRANSACTION_DEPTH_KEY, 0)
    return depth if isinstance(depth, int) else 0


def in_transaction(session: AsyncSession) -> bool:
    """Report whether a :func:`transaction` block is open on ``session``.

    Repositories call this to decide between ``flush`` and ``commit``:
    inside a block a write must stay uncommitted so the block owns the
    single commit at the end.

    Args:
        session (AsyncSession): The session to inspect.

    Returns:
        bool: ``True`` when at least one block is open.
    """
    return transaction_depth(session) > 0


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Group every write on ``session`` into a single commit.

    While the block is open, repository writes flush instead of
    committing, so nothing is durable until the outermost block exits
    cleanly. An exception rolls the whole block back and propagates.

    Nesting is re-entrant — an inner block joins the outer one and does
    not commit on its own exit. The depth is restored before the commit
    or rollback runs, so the repository is already back to its normal
    mode by the time that statement is issued.

    Args:
        session (AsyncSession): The session to bind the block to. Every
            repository sharing this session joins the same block.

    Yields:
        AsyncSession: The same session, so ``async with transaction(s) as
        session:`` reads naturally at the call site.

    Raises:
        BaseException: Re-raises whatever the body raised, after rolling
            back at the outermost level.

    Notes:
        Catching an exception *inside* the block and continuing leaves
        the session in a failed state — SQLAlchemy requires a rollback
        after a failed flush. Wrap the fallible part in :func:`savepoint`
        when you intend to recover from it.
    """
    depth = transaction_depth(session)
    session.info[TRANSACTION_DEPTH_KEY] = depth + 1
    try:
        yield session
    except BaseException:
        session.info[TRANSACTION_DEPTH_KEY] = depth
        if depth == 0:
            await session.rollback()
        raise
    else:
        session.info[TRANSACTION_DEPTH_KEY] = depth
        if depth == 0:
            await session.commit()


@asynccontextmanager
async def savepoint(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Run a nested, individually revertible unit of work.

    Issues a real ``SAVEPOINT`` via ``session.begin_nested()``. A failure
    inside the block rolls back to the savepoint and leaves the
    surrounding transaction usable, which is what lets a caller catch a
    :class:`~tempest_fastapi_sdk.exceptions.conflict.ConflictException`
    and carry on:

    ```python
    async with transaction(session):
        await accounts.add(account)
        try:
            async with savepoint(session):
                await nicknames.add(nickname)
        except ConflictException:
            pass  # the account survives, the nickname is rolled back
    ```

    The block behaves like :func:`transaction` for repository writes —
    it bumps the same depth counter, so writes inside it flush rather
    than commit and the outer block still owns the final commit.

    Args:
        session (AsyncSession): The session to open the savepoint on.

    Yields:
        AsyncSession: The same session.

    Raises:
        BaseException: Re-raises whatever the body raised, after rolling
            back to the savepoint.

    Notes:
        PostgreSQL supports ``SAVEPOINT`` unconditionally. SQLite needs
        :func:`~tempest_fastapi_sdk.db.connection.enable_sqlite_savepoints`,
        which
        :class:`~tempest_fastapi_sdk.db.connection.AsyncDatabaseManager`
        applies to every SQLite engine it builds — without it the
        driver's implicit transaction handling turns the closing
        ``RELEASE SAVEPOINT`` into a **commit**, so a nested block that
        exits cleanly becomes durable even when the outer block is later
        rolled back. A session built from a hand-rolled SQLite engine
        must call that function itself.
    """
    depth = transaction_depth(session)
    session.info[TRANSACTION_DEPTH_KEY] = depth + 1
    try:
        async with session.begin_nested():
            yield session
    finally:
        session.info[TRANSACTION_DEPTH_KEY] = depth


__all__: list[str] = [
    "TRANSACTION_DEPTH_KEY",
    "in_transaction",
    "savepoint",
    "transaction",
    "transaction_depth",
]
