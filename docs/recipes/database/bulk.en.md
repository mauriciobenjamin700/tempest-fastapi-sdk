# 5. Bulk operations

Use to write many rows at once, keeping or bypassing the unit of work, including idempotent inserts (ON CONFLICT DO NOTHING).

Part of the [Database](../database.md) recipe.

For volume, row-by-row ORM is expensive. The repository offers two
families: those that **keep** the unit-of-work (instances refreshed back)
and those that **bypass** it (a single statement, no refresh).

```python
import asyncio

from tempest_fastapi_sdk import BaseRepository

from db_setup import db
from src.db.models import UserModel


async def main() -> None:
    """Run this example."""
    m1, m2, m3 = (UserModel(name=n, email=f"{n}@x.com") for n in "abc")
    async with db.get_session_context() as session:
        repository = BaseRepository(session, model=UserModel)
        # Keeps the UoW — attached, refreshed instances
        created = await repository.add_all([m1, m2, m3])      # several INSERTs, 1 tx
        u1, u2 = created[0], created[1]
        updated = await repository.update_many([u1, u2])      # several UPDATEs, 1 tx

        # Bypasses the UoW — one statement, scales better (>= 50 rows)
        n = await repository.bulk_create_values([
            {"name": "A", "email": "a@x.com", "password_hash": "..."},
            {"name": "B", "email": "b@x.com", "password_hash": "..."},
        ])  # INSERT ... VALUES (...), (...) — returns row count

        n = await repository.bulk_update(
            filters={"is_active": False},
            values={"is_active": True},
        )  # UPDATE ... WHERE — returns affected row count

        n = await repository.bulk_upsert(
            rows=[{"sku": "ABC", "price": 10}, {"sku": "DEF", "price": 20}],
            conflict_columns=["sku"],          # requires a UNIQUE index
            update_columns=["price"],          # None = update everything but PK + conflict
        )  # INSERT ... ON CONFLICT DO UPDATE — Postgres and SQLite


asyncio.run(main())
```

!!! warning "`bulk_update` refuses an empty filter"
    Passing `filters={}` raises `ValueError` — it's the guard against an
    accidental table-wide UPDATE. To genuinely update every row, pass an
    explicit always-true condition.

!!! danger "`bulk_*` does not refresh the session"
    `bulk_create_values`, `bulk_update` and `bulk_upsert` emit a raw
    statement and do **not** refresh or attach instances to the session.
    Use them when you don't need the ORM objects back. If you need the
    instances, use `add_all` / `update_many`.

!!! note "`bulk_upsert` is dialect-specific"
    Postgres and SQLite have native upsert. Other dialects raise
    `NotImplementedError` — fall back to a `SELECT FOR UPDATE` + `UPDATE`
    loop.

## Idempotent insert (ON CONFLICT DO NOTHING)

Sometimes the conflict **is the expected case**. Picture a routine that
materializes a task's occurrences (`unique(task, occurs_on)`), called by
the route and by a sweep, sometimes at the same time: every round tries to
insert the same rows, and what you want is to create only the missing
ones — no duplicates, no failure, and no rollback of the caller's
transaction.

`on_conflict="nothing"` emits `INSERT ... ON CONFLICT (...) DO NOTHING`:
the existing row stays untouched and the database itself drops the new
one.

```python
import asyncio
from datetime import date
from uuid import UUID

from sqlalchemy import Date, String, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import StaticPool
from tempest_fastapi_sdk import BaseModel, BaseRepository


class OccurrenceModel(BaseModel):
    """One occurrence of a task on a given day."""

    __tablename__ = "occurrences"
    __table_args__ = (UniqueConstraint("task", "occurs_on"),)

    task: Mapped[str] = mapped_column(String(32))
    occurs_on: Mapped[date] = mapped_column(Date)


def occurrences(*days: int) -> list[dict[str, object]]:
    """Build one row per day of October 2026 for the task ``math``."""
    return [{"task": "math", "occurs_on": date(2026, 10, day)} for day in days]


async def main() -> None:
    """Materialize the same occurrences twice and see what each call created."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)

    async with AsyncSession(engine) as session:
        repository = BaseRepository(session, model=OccurrenceModel)

        created: int = await repository.bulk_upsert(
            occurrences(1, 2, 3),
            conflict_columns=["task", "occurs_on"],
            on_conflict="nothing",
        )
        print("first round:", created)

        again: int = await repository.bulk_upsert(
            occurrences(1, 2, 3),
            conflict_columns=["task", "occurs_on"],
            on_conflict="nothing",
        )
        print("same rows again:", again)

        new_ids: list[UUID] = await repository.bulk_upsert(
            occurrences(3, 4, 5),
            conflict_columns=["task", "occurs_on"],
            on_conflict="nothing",
            returning_ids=True,
        )
        print("ids created:", len(new_ids))
        print("rows in the table:", await repository.count())

    await engine.dispose()


asyncio.run(main())
```

Running it prints:

```text
first round: 3
same rows again: 0
ids created: 2
rows in the table: 5
```

Piece by piece:

* **The return value is the number of rows actually inserted.** It comes
  from `RETURNING` the primary key, not from the driver's `rowcount`, so
  the second round really returns `0`.
* **`returning_ids=True` returns the created `id`s** (`list[UUID]`)
  instead of the count. Use it to publish one event per new row: only the
  rows this call created show up, never the ones that already existed. The
  order is the database's, not the order of `rows`.
* **No rollback.** The database resolves the conflict without an
  exception, so a surrounding `repository.transaction()` stays intact.
* **Concurrency.** Two sessions inserting the same keys neither duplicate
  nor fail: the second waits for the first one's `COMMIT` and skips the
  keys it created. Measured on PostgreSQL 16 and SQLite 3.47
  (`tests/db/test_bulk_upsert_conflict.py`).

!!! warning "Only the conflict on `conflict_columns` is skipped"
    A violation of **another** constraint (another unique index, a foreign
    key, `NOT NULL`) still raises the repository's conflict exception, as
    `bulk_create_values` does.

!!! note "`update_columns` does not go with `on_conflict='nothing'`"
    A non-empty `update_columns` with `on_conflict="nothing"` raises
    `ValueError`: the list would be ignored, and it is almost always a
    mistaken call. The reverse too: `on_conflict="update"` with no column
    to update (`update_columns=[]`) raises `ValueError` pointing to
    `"nothing"`.

??? info "What the number means in each mode"
    | Call | Returns |
    | --- | --- |
    | `on_conflict="update"` (default) | the driver's `rowcount`: inserted **+** updated (on PostgreSQL and SQLite a conflicting row counts once) |
    | `on_conflict="nothing"` | inserted rows only, counted from `RETURNING` (`0` when all already existed) |
    | `returning_ids=True` with `"nothing"` | the created `id`s |
    | `returning_ids=True` with `"update"` | the `id`s inserted **or** updated — neither dialect tells the two apart portably |

    `bulk_create_values` returns the driver's `rowcount`. A real `0` comes
    back as `0`; only a driver that answers `-1` (count unknown) falls back
    to `len(rows)`.

**Recap:** `add_all`/`update_many` when you want the instances back;
`bulk_*` when you want throughput. An empty filter on `bulk_update` is a
deliberate error. An expected conflict is
`bulk_upsert(on_conflict="nothing")`, with `returning_ids=True` when you
need to know which rows were created.
