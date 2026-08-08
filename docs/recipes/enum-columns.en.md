# Enum columns (safe on both databases)

SQLAlchemy already maps `Mapped[MyEnum]` to a column. Its defaults,
however, cost safety in three ways — and the SDK changes all three.

## What changes

```python
from sqlalchemy.orm import Mapped

from tempest_fastapi_sdk import BaseModel, BaseStrEnum


class OrderStatus(BaseStrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class OrderModel(BaseModel):
    status: Mapped[OrderStatus]
```

With no configuration at all, that annotation produces:

```sql
-- PostgreSQL
CREATE TYPE order_status_enum AS ENUM ('open', 'in_progress', 'done');
status order_status_enum NOT NULL

-- SQLite
status VARCHAR(11) NOT NULL
CONSTRAINT ck_order_order_status_enum
    CHECK (status IN ('open', 'in_progress', 'done'))
```

The three changed defaults:

1. **It stores the `value`, not the `name`.** SQLAlchemy's default would
   write `IN_PROGRESS`. Every consumer that is not this Python process —
   a report, a dashboard, a sibling service — would read a string the
   domain never defined.
2. **A `CHECK` on SQLite.** The default emits a bare `VARCHAR` with **no
   constraint**: the production column rejects an invalid value, the test
   column accepts it silently. A bug the database would have caught in
   production would pass the test suite.
3. **A collision-free type name.** The default would name the PostgreSQL
   type `orderstatus`; the SDK uses `order_status_enum`, because types and
   tables share one namespace.

!!! info "Declaration order becomes the type's order"
    PostgreSQL sorts an `ENUM` column by label order, not alphabetically.
    Declaring `OPEN, IN_PROGRESS, DONE` makes `ORDER BY status` follow the
    workflow.

## When the annotation is not enough

`enum_column()` is the same thing spelled out, for when the column needs
arguments:

```python
from sqlalchemy.orm import Mapped

from tempest_fastapi_sdk import BaseModel, BaseStrEnum, enum_column


class OrderStatus(BaseStrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class OrderModel(BaseModel):
    status: Mapped[OrderStatus] = enum_column(
        OrderStatus, default=OrderStatus.OPEN, index=True
    )
```

An explicit type always wins over the annotation map, so
`mapped_column(sqlalchemy.Enum(...))` remains available for a column that
needs the original behavior.

## Changed the enum? That is a schema change

And `alembic revision --autogenerate` **does not detect it on its own**,
on either backend:

- on PostgreSQL the labels live in `pg_enum`, which autogenerate does not
  compare;
- on SQLite they live inside the `CHECK`, which it does not compare
  either — and the `VARCHAR(n)` only changes length when the *longest*
  value changes, so not even `compare_type` notices.

The SDK closes this with the `sync_enum_types` hook, already wired into
the `env.py` that `tempest db init` generates. Add a member to the enum,
run autogenerate, and the migration comes out filled in:

```python
from alembic import op

from tempest_fastapi_sdk import EnumColumnRef


def upgrade() -> None:
    """Add ``archived`` to the order status enum."""
    op.replace_enum(
        "order_status_enum",
        new_values=["open", "in_progress", "done", "archived"],
        old_values=["open", "in_progress", "done"],
        columns=[EnumColumnRef(table="order", column="status")],
    )
```

### Why not `ALTER TYPE ... ADD VALUE`

It is the command everyone reaches for first, and it:

- cannot run inside a transaction block on older servers — the classic
  enum-migration error;
- cannot remove a value at all;
- cannot reorder.

`replace_enum` renames the old type, creates the new one under the real
name, casts every dependent column across and drops the old one. All of
that is ordinary DDL, so it runs inside Alembic's transaction:

```sql
ALTER TYPE order_status_enum RENAME TO order_status_enum__old;
CREATE TYPE order_status_enum AS ENUM ('open', 'in_progress', 'done', 'archived');
ALTER TABLE "order" ALTER COLUMN status
    TYPE order_status_enum USING (status::text)::order_status_enum;
DROP TYPE order_status_enum__old;
```

On SQLite the same operation rebuilds the table so the `CHECK` follows.

!!! tip "The column `DEFAULT` is preserved"
    A `DEFAULT 'open'::order_status_enum` still points at the outgoing
    type, and PostgreSQL refuses the cast while that is true. The
    operation reads the current default from `information_schema`, drops
    it before the cast and restores it after — rather than assuming there
    is no default.

### Renaming a member

Unaided, removing `wip` to introduce `in_progress` fails when casting the
rows that still hold `wip`. State the mapping:

```python
from alembic import op

from tempest_fastapi_sdk import EnumColumnRef


def upgrade() -> None:
    """Rename ``wip`` to ``in_progress``, carrying the rows along."""
    op.replace_enum(
        "task_status_enum",
        new_values=["open", "in_progress"],
        old_values=["open", "wip"],
        columns=[EnumColumnRef(table="task", column="status")],
        value_map={"wip": "in_progress"},
    )
```

The operation is reversible: `downgrade` swaps the lists and inverts the
`value_map` by itself.

!!! warning "Offline (`--sql`) mode is unsupported on PostgreSQL"
    Preserving the `DEFAULT` requires reading it from the database, and an
    offline script has no connection. Rather than silently generating a
    script that drops the default, the operation raises
    `NotImplementedError` saying so. Run the upgrade online, or hand-write
    the `ALTER TYPE` sequence for the offline script.

### Detection is deliberately conservative

An enum the backend cannot report on is **skipped**, not diffed against a
guess — emitting a wrong `replace_enum` would drop values from live rows.
On SQLite that means only a `CHECK` in the shape the SDK generates is read
back; a hand-written constraint is not interpreted.

## Migrations do not import the SDK

Alembic would render `TempestEnum` as a dotted path into this package, in
a file whose only imports are `alembic.op` and `sqlalchemy as sa` — the
migration would fail on import. The `render_enum_types` hook renders a
plain `sa.Enum` with the values spelled out, which also makes the
migration a real snapshot, independent of what the Python enum becomes
later.

## Recap

- `Mapped[MyEnum]` is already safe: the `value` in the database, a native
  `ENUM` on PostgreSQL, a `CHECK` on SQLite, a collision-free type name.
- `enum_column()` for when the column needs `default`, `index`, and so on.
- A member change is a schema change, and `sync_enum_types` detects it
  where autogenerate is blind.
- `op.replace_enum(...)` adds, removes and reorders in one operation,
  inside the transaction, with `value_map=` for renames and an automatic
  `downgrade`.
