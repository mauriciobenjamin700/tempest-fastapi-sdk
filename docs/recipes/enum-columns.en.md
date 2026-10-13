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
   A trailing `Enum` in the class name is dropped before the suffix:
   `OrderStatusEnum` also becomes `order_status_enum`, not
   `order_status_enum_enum`. With or without the suffix on the class, the
   type is the same.

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

### One `CHECK` per column

Under SQLAlchemy 2.1, Alembic no longer recognizes the `CHECK` the enum type
attaches to the column (it looks for `constraint._create_rule.target`, which
2.1 no longer sets). Measured with SQLAlchemy 2.1.4 and Alembic 1.20.0, the
generated `create_table` came out like this (the `BaseModel` columns reduced
to `id`):

```python
import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    """What autogenerate wrote under SQLAlchemy 2.1, without the SDK."""
    op.create_table(
        "things",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "GUARDIAN",
                "TEEN",
                name="user_role_enum",
                native_enum=True,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('GUARDIAN', 'TEEN')", name="user_role_enum"),
        sa.CheckConstraint(
            "role IN ('GUARDIAN', 'TEEN')", name=op.f("ck_things_user_role_enum")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_things")),
    )
```

The naming convention turns both names into `ck_things_user_role_enum`, and
PostgreSQL 16 refuses the second one:
`check constraint "ck_things_user_role_enum" already exists`.

`render_enum_types` omits that type-bound `CHECK`. The column's
`sa.Enum(..., create_constraint=True)` already recreates it where it belongs:
on SQLite the table ends up with exactly one `CHECK`, named
`ck_things_user_role_enum` (measured under SQLAlchemy 2.0.52 and 2.1.4), and on
PostgreSQL there is no `CHECK` at all, only the native type. A
`CheckConstraint` you declare in `__table_args__` is not type bound and keeps
rendering.

## Enum column on an existing table

On SQLite the SDK's `env.py` generates every table change inside
`batch_alter_table`, which rebuilds the table copying the `CHECK`s it
reflects. Two things went wrong there, measured with SQLAlchemy 2.0.52 and
2.1.4:

- **Two columns of the same enum lost a `CHECK`.** The `CHECK` is named after
  the type, so both got `ck_things_age_range_enum`; the batch keys
  constraints by name and kept one. The other column accepted any value,
  with no warning.
- **The downgrade of every added enum column broke.** `drop_column` rebuilt
  the table with the `CHECK` naming the removed column, and SQLite refused it
  with `no such column: age_range`.

The `sqlite_enum_checks` hook, wired into the generated `env.py`, rewrites the
migration:

```python
import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    """Two columns of the same enum, each with its own CHECK."""
    with op.batch_alter_table("things", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "age_range",
                sa.Enum(
                    "KID",
                    "TEEN",
                    name="age_range_enum",
                    native_enum=True,
                    create_constraint=False,
                ),
                nullable=True,
            )
        )
        batch_op.create_enum_check(
            "age_range", ["KID", "TEEN"], name="ck_things_age_range_enum"
        )
        batch_op.add_column(
            sa.Column(
                "store_age_range",
                sa.Enum(
                    "KID",
                    "TEEN",
                    name="age_range_enum",
                    native_enum=True,
                    create_constraint=False,
                ),
                nullable=True,
            )
        )
        batch_op.create_enum_check(
            "store_age_range",
            ["KID", "TEEN"],
            name="ck_things_age_range_enum_store_age_range",
        )


def downgrade() -> None:
    """Drop each column's CHECK before the column."""
    with op.batch_alter_table("things", schema=None) as batch_op:
        batch_op.drop_enum_check("store_age_range")
        batch_op.drop_column("store_age_range")
        batch_op.drop_enum_check("age_range")
        batch_op.drop_column("age_range")
```

- The first column of an enum in a table keeps `ck_<table>_<enum>`; the
  second and later get `ck_<table>_<enum>_<column>`. `TempestEnum` gives the
  model the same name, so a table with one column per enum does not change
  names.
- `create_enum_check` and `drop_enum_check` do nothing outside SQLite: on
  PostgreSQL the same migration runs with no `CHECK` at all, only the native
  type.
- `drop_enum_check` finds the `CHECK` by shape (`<column> IN (...)`), not by
  name, so it also cleans up a table created before this version.

!!! tip "Migration written before this version"
    In `downgrade()`, add `batch_op.drop_enum_check("<column>")` before each
    `batch_op.drop_column("<column>")` of an enum column.

## The downgrade drops the type

The downgrade Alembic writes for a new table is only `op.drop_table`, and on
PostgreSQL that leaves the `ENUM` behind. Measured on PostgreSQL 16: after
`upgrade` → `downgrade`, `user_role_enum` was still in `pg_type`, and the next
`upgrade` failed with `type "user_role_enum" already exists`.

The `drop_enum_types_on_downgrade` hook, wired into the generated `env.py`,
appends the type drop at the end of the downgrade:

```python
from alembic import op


def downgrade() -> None:
    """Generated downgrade for two tables sharing one enum."""
    op.drop_table("things")
    op.drop_table("others")
    op.drop_enum_type("user_role_enum")
```

- `op.drop_enum_type` emits `DROP TYPE IF EXISTS` on PostgreSQL and does
  nothing on other backends — on SQLite the enum lives in the `CHECK`, which
  leaves with the table.
- A type used by two tables is only dropped by the revision that removes the
  last one: when another table in the model still uses the type, the hook adds
  no drop.
- The decision comes from the metadata alone, with no connection, so the same
  migration runs on both backends. With the hook, `upgrade` → `downgrade` →
  `upgrade` passes on PostgreSQL 16.

!!! tip "Migration written before this version"
    The hook only acts on new revisions. In a migration already generated, add
    `op.drop_enum_type("<name>_enum")` at the end of `downgrade()`, after the
    `op.drop_table` calls that used the type.

### Enum column with a new type

`create_table` creates its columns' `ENUM`; `add_column` does not. Measured on
PostgreSQL 16: adding `age_range: Mapped[AgeRange | None]` to an existing
table generated only `ALTER TABLE things ADD COLUMN age_range age_range_enum`,
and the `upgrade` failed with `type "age_range_enum" does not exist`.

The `create_enum_types_on_add_column` hook, wired into the generated `env.py`,
puts the type before the column, and `drop_enum_types_on_downgrade` drops it
after the `drop_column`:

```python
import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    """New enum column on an existing table."""
    op.create_enum_type("age_range_enum", ["KID", "TEEN"])
    with op.batch_alter_table("things", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "age_range",
                sa.Enum(
                    "KID",
                    "TEEN",
                    name="age_range_enum",
                    native_enum=True,
                    create_constraint=False,
                ),
                nullable=True,
            )
        )
        batch_op.create_enum_check(
            "age_range", ["KID", "TEEN"], name="ck_things_age_range_enum"
        )


def downgrade() -> None:
    """Remove the column and, last, the type."""
    with op.batch_alter_table("things", schema=None) as batch_op:
        batch_op.drop_enum_check("age_range")
        batch_op.drop_column("age_range")
    op.drop_enum_type("age_range_enum")
```

- `op.create_enum_type` checks `pg_type` and only creates a missing type; in
  offline (`--sql`) mode it writes the `CREATE TYPE` directly. Outside
  PostgreSQL it does nothing.
- A type another table already uses stays out of the migration: no
  `create_enum_type` in the upgrade, no `drop_enum_type` in the downgrade.
- With both hooks, `upgrade` → `downgrade` → `upgrade` passes on
  PostgreSQL 16 in all three cases (one enum, two enums, one enum on two
  columns), with no `CHECK` on PostgreSQL.

## Renaming the type

Autogenerate does not notice a renamed type: on PostgreSQL the columns keep
pointing at the old type, and the model writes the new name in every statement
that casts to it. Renaming the enum class renames the type, and so did every
class ending in `Enum` when the SDK stopped doubling the suffix.

`sync_enum_types` detects the case and writes `op.rename_enum_type`:

```python
from alembic import op

from tempest_fastapi_sdk.db.enum_migrations import EnumColumnRef


def upgrade() -> None:
    """Rename the type without recreating the database."""
    op.rename_enum_type(
        "candidate_slug_enum_enum",
        "candidate_slug_enum",
        columns=[EnumColumnRef(table="candidates", column="slug")],
    )


def downgrade() -> None:
    """Go back to the old name."""
    op.rename_enum_type(
        "candidate_slug_enum",
        "candidate_slug_enum_enum",
        columns=[EnumColumnRef(table="candidates", column="slug")],
    )
```

- On PostgreSQL it becomes `ALTER TYPE candidate_slug_enum_enum RENAME TO
  candidate_slug_enum`. The rows stay where they are; `columns=` is not read.
- SQLite has no type: the name lives in each column's `CHECK`
  (`ck_candidates_candidate_slug_enum_enum`). Each listed column's table is
  rebuilt with the `CHECK` renamed, so `alembic check` does not report the
  difference.
- Detection only acts when every column of the type agrees on the old name,
  and when no model still declares the old name. Otherwise nothing is
  generated.

## Recap

- `Mapped[MyEnum]` is already safe: the `value` in the database, a native
  `ENUM` on PostgreSQL, a `CHECK` on SQLite, a collision-free type name.
- `enum_column()` for when the column needs `default`, `index`, and so on.
- `OrderStatus` and `OrderStatusEnum` give the same type,
  `order_status_enum`; a renamed type becomes `op.rename_enum_type` on
  autogenerate.
- A member change is a schema change, and `sync_enum_types` detects it
  where autogenerate is blind.
- `op.replace_enum(...)` adds, removes and reorders in one operation,
  inside the transaction, with `value_map=` for renames and an automatic
  `downgrade`.
- An enum column with a new type on an existing table gets
  `op.create_enum_type` before the `add_column`, and the downgrade drops the
  type after the `drop_column`.
- On SQLite each enum column has its own `CHECK`, two of the same enum
  included, and the downgrade of an `add_column` drops the `CHECK` before the
  column.
- The type-bound `CHECK` is not duplicated in the migration
  (`render_enum_types` omits it), and the downgrade of a new table drops the
  PostgreSQL `ENUM` with `op.drop_enum_type`.
