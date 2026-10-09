"""Alembic support for enum columns: rendering, replacement, and detection.

Changing an enum's members is a schema change, and Alembic handles it
badly out of the box in three separate ways. This module fixes each one.

**1. The generated migration does not even import.** Alembic renders a
custom type subclass such as
:class:`~tempest_fastapi_sdk.db.enums.TempestEnum` through its
``user_module_prefix`` path, producing
``tempest_fastapi_sdk.db.enums.TempestEnum(...)`` in a file whose only
imports are ``alembic.op`` and ``sqlalchemy as sa``.
:func:`render_enum_types` renders it as a plain ``sa.Enum`` with the
values spelled out instead — which also makes the migration a real
snapshot, independent of what the Python enum looks like later.

**2. Autogenerate never notices a changed member list.** On PostgreSQL
the labels live in ``pg_enum``, which autogenerate does not compare; on
SQLite they live inside a ``CHECK`` constraint, which it does not
compare either. Adding a member is silently a no-op. Worse, on SQLite
the ``VARCHAR`` length only moves when the *longest* value changes, so
even ``compare_type`` catches nothing. :func:`sync_enum_types` closes
this by reading the database and diffing it against the metadata.

**3. The obvious fix is the one PostgreSQL refuses.**
``ALTER TYPE ... ADD VALUE`` cannot run in a transaction block on older
servers, cannot remove a value at all, and cannot reorder. The
:class:`ReplaceEnumOp` operation therefore renames the old type, creates
the new one, casts every dependent column across, and drops the old —
all of which is ordinary DDL that runs inside Alembic's transaction:

```sql
ALTER TYPE order_status_enum RENAME TO order_status_enum__old;
CREATE TYPE order_status_enum AS ENUM ('open', 'in_progress', 'done');
ALTER TABLE "order" ALTER COLUMN status TYPE order_status_enum
    USING status::text::order_status_enum;
DROP TYPE order_status_enum__old;
```

On SQLite the same operation rebuilds the table through
``batch_alter_table`` so the ``CHECK`` constraint follows.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import sqlalchemy as sa
from alembic.autogenerate import renderers
from alembic.autogenerate.api import AutogenContext
from alembic.operations import MigrateOperation, Operations
from alembic.operations.ops import (
    CreateTableOp,
    DropTableOp,
    MigrationScript,
    UpgradeOps,
)

from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.db.model import NAMING_CONVENTION
from tempest_fastapi_sdk.db.search import POSTGRESQL_DIALECT

OLD_TYPE_SUFFIX: Final[str] = "__old"
"""Suffix for the temporary name the outgoing PostgreSQL type is renamed to.

The rename has to happen before the replacement type can take the real
name, and the placeholder must not collide with anything a project could
plausibly have declared.
"""

SQLITE_DIALECT: Final[str] = "sqlite"
"""SQLAlchemy's dialect name for SQLite."""

_CHECK_IN_LIST_RE = re.compile(
    r"\bIN\s*\((?P<values>(?:\s*'(?:[^']|'')*'\s*,?)+)\)",
    re.IGNORECASE,
)
"""Matches the ``IN ('a', 'b')`` list SQLAlchemy emits for an enum CHECK.

Used to read back the members SQLite is currently enforcing. Deliberately
narrow: a hand-written CHECK in another shape does not match, and the
column is then reported as undetectable rather than diffed against a
guess.
"""

_SQL_STRING_RE = re.compile(r"'((?:[^']|'')*)'")


@dataclass(frozen=True)
class EnumColumnRef:
    """A column that uses an enum type.

    A named pair rather than a bare tuple so a migration file reads
    ``EnumColumnRef(table="order", column="status")`` and a swapped
    argument is a type error instead of a silent mis-migration.

    Attributes:
        table (str): The table holding the column.
        column (str): The column name.
    """

    table: str
    column: str


@dataclass(frozen=True)
class EnumTypeState:
    """The members of one enum type, and where they are used.

    Attributes:
        name (str): The database type name (PostgreSQL) or the enum name
            carried by the ``CHECK`` constraint (SQLite).
        values (tuple[str, ...]): The members, in order.
        columns (tuple[EnumColumnRef, ...]): Columns typed by it.
    """

    name: str
    values: tuple[str, ...]
    columns: tuple[EnumColumnRef, ...]


@Operations.register_operation("replace_enum")
class ReplaceEnumOp(MigrateOperation):
    """Replace an enum type's members, on PostgreSQL or SQLite.

    Handles additions, removals and reordering in one operation, because
    the PostgreSQL primitive that would handle additions alone
    (``ALTER TYPE ... ADD VALUE``) cannot run inside Alembic's
    transaction on older servers and cannot express the other two at all.

    Reversible: :meth:`reverse` swaps the value lists, so ``downgrade``
    is generated for free. A downgrade that would drop a member still
    held by a row fails loudly on the cast rather than corrupting data.

    Attributes:
        name (str): The enum type name.
        new_values (tuple[str, ...]): Members after the change.
        old_values (tuple[str, ...]): Members before it.
        columns (tuple[EnumColumnRef, ...]): Columns to migrate.
        value_map (dict[str, str]): Old-value to new-value renames
            applied while casting, so a renamed member keeps its rows.
    """

    def __init__(
        self,
        name: str,
        *,
        new_values: Sequence[str],
        old_values: Sequence[str],
        columns: Sequence[EnumColumnRef],
        value_map: dict[str, str] | None = None,
    ) -> None:
        """Record what the operation should do.

        Args:
            name (str): The enum type name.
            new_values (Sequence[str]): Members after the change.
            old_values (Sequence[str]): Members before it, needed so the
                operation can reverse itself.
            columns (Sequence[EnumColumnRef]): Columns typed by the enum.
            value_map (dict[str, str] | None): Renames to apply to
                existing rows while casting.
        """
        self.name: str = name
        self.new_values: tuple[str, ...] = tuple(new_values)
        self.old_values: tuple[str, ...] = tuple(old_values)
        self.columns: tuple[EnumColumnRef, ...] = tuple(columns)
        self.value_map: dict[str, str] = dict(value_map or {})

    @classmethod
    def replace_enum(
        cls,
        operations: Operations,
        name: str,
        *,
        new_values: Sequence[str],
        old_values: Sequence[str],
        columns: Sequence[EnumColumnRef],
        value_map: dict[str, str] | None = None,
    ) -> None:
        """Entry point registered as ``op.replace_enum(...)``.

        Args:
            operations (Operations): Alembic's operations proxy.
            name (str): The enum type name.
            new_values (Sequence[str]): Members after the change.
            old_values (Sequence[str]): Members before it.
            columns (Sequence[EnumColumnRef]): Columns typed by the enum.
            value_map (dict[str, str] | None): Renames applied to rows.
        """
        operations.invoke(
            cls(
                name,
                new_values=new_values,
                old_values=old_values,
                columns=columns,
                value_map=value_map,
            ),
        )

    def reverse(self) -> ReplaceEnumOp:
        """Return the operation that undoes this one.

        Returns:
            ReplaceEnumOp: The same replacement with the value lists and
            the rename map inverted.
        """
        return ReplaceEnumOp(
            self.name,
            new_values=self.old_values,
            old_values=self.new_values,
            columns=self.columns,
            value_map={new: old for old, new in self.value_map.items()},
        )


@Operations.implementation_for(ReplaceEnumOp)
def _run_replace_enum(operations: Operations, operation: ReplaceEnumOp) -> None:
    """Dispatch :class:`ReplaceEnumOp` to the backend that can perform it.

    Args:
        operations (Operations): Alembic's operations proxy.
        operation (ReplaceEnumOp): The replacement to perform.

    Raises:
        NotImplementedError: On a backend other than PostgreSQL or
            SQLite. Failing is better than emitting DDL that silently
            does nothing on an untested engine.
    """
    dialect = operations.get_bind().dialect.name
    if dialect == POSTGRESQL_DIALECT:
        _replace_enum_postgresql(operations, operation)
        return
    if dialect == SQLITE_DIALECT:
        _replace_enum_sqlite(operations, operation)
        return
    raise NotImplementedError(
        f"replace_enum has no implementation for the {dialect!r} dialect; "
        "the supported backends are PostgreSQL and SQLite.",
    )


def postgresql_replace_statements(
    operation: ReplaceEnumOp,
    defaults: dict[EnumColumnRef, str | None],
    quote: Callable[[str], str],
) -> list[str]:
    """Build the DDL that swaps a PostgreSQL ``ENUM`` type.

    Kept separate from execution so the exact statement sequence can be
    asserted without a live PostgreSQL server — the production path is
    the one the SDK's own test suite cannot otherwise reach.

    The sequence renames the outgoing type out of the way, creates the
    replacement under the real name, casts each dependent column across
    via text, and drops the old type once nothing references it. Every
    statement is ordinary DDL, so the whole thing runs inside Alembic's
    transaction — unlike ``ALTER TYPE ... ADD VALUE``, which is the
    operation people reach for first and the reason enum migrations have
    a reputation.

    Column defaults are dropped before the cast and restored after: a
    ``DEFAULT 'open'::order_status_enum`` still points at the outgoing
    type, and PostgreSQL refuses to change the column's type while it
    does.

    Args:
        operation (ReplaceEnumOp): The replacement to perform.
        defaults (dict[EnumColumnRef, str | None]): Each column's current
            default expression, or ``None`` when it has none.
        quote (Callable[[str], str]): The dialect's identifier quoter.

    Returns:
        list[str]: The statements, in the order they must run.
    """
    name = quote(operation.name)
    old_name = quote(f"{operation.name}{OLD_TYPE_SUFFIX}")
    labels = ", ".join(_sql_string(value) for value in operation.new_values)

    statements: list[str] = [
        f"ALTER TYPE {name} RENAME TO {old_name}",
        f"CREATE TYPE {name} AS ENUM ({labels})",
    ]
    for ref in operation.columns:
        table = quote(ref.table)
        column = quote(ref.column)
        default = defaults.get(ref)
        if default is not None:
            statements.append(
                f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT",
            )
        cast_source = _cast_expression(column, operation.value_map)
        statements.append(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"TYPE {name} USING ({cast_source})::{name}",
        )
        if default is not None:
            statements.append(
                f"ALTER TABLE {table} ALTER COLUMN {column} "
                f"SET DEFAULT {_remapped_default(default, operation.value_map)}",
            )
    statements.append(f"DROP TYPE {old_name}")
    return statements


def _replace_enum_postgresql(
    operations: Operations,
    operation: ReplaceEnumOp,
) -> None:
    """Read the current column defaults, then run the replacement DDL.

    Args:
        operations (Operations): Alembic's operations proxy.
        operation (ReplaceEnumOp): The replacement to perform.

    Raises:
        NotImplementedError: In offline (``--sql``) mode. Preserving a
            column default requires reading it from
            ``information_schema``, which needs a live connection;
            guessing would silently drop the default from the generated
            script. Run the upgrade online, or write the ``ALTER TYPE``
            sequence by hand for the offline script.
    """
    if operations.migration_context.as_sql:
        raise NotImplementedError(
            "replace_enum needs a live connection on PostgreSQL: it reads each "
            "column's current DEFAULT so it can restore it after the cast, and "
            "offline (--sql) mode cannot. Run the upgrade online, or hand-write "
            "the ALTER TYPE sequence for the offline script.",
        )
    preparer = operations.get_bind().dialect.identifier_preparer
    defaults: dict[EnumColumnRef, str | None] = {
        ref: _column_default(operations, ref) for ref in operation.columns
    }
    for statement in postgresql_replace_statements(operation, defaults, preparer.quote):
        operations.execute(statement)


def _replace_enum_sqlite(
    operations: Operations,
    operation: ReplaceEnumOp,
) -> None:
    """Rebuild each SQLite table so its enum ``CHECK`` matches the new members.

    SQLite cannot alter a constraint in place, so Alembic's
    ``batch_alter_table`` copies the table into a new one carrying the
    new definition.

    Renaming a member takes **two** rebuilds, because the copy enforces
    the constraint as it inserts and there is no single member list that
    both spellings satisfy: the old constraint rejects the new value, and
    the new constraint rejects the rows that still hold the old one. The
    column is therefore first widened to the union of both lists, the
    ``UPDATE`` runs while every spelling is legal, and a second rebuild
    narrows it to the final list. Without a rename, one rebuild is
    enough.

    PostgreSQL needs none of this — its ``USING`` clause converts and
    renames in the same statement.

    Args:
        operations (Operations): Alembic's operations proxy.
        operation (ReplaceEnumOp): The replacement to perform.
    """
    connection = operations.get_bind()
    preparer = connection.dialect.identifier_preparer

    for ref in operation.columns:
        if operation.value_map:
            widened = tuple(
                dict.fromkeys([*operation.old_values, *operation.new_values]),
            )
            _sqlite_rebuild(operations, connection, ref, operation.name, widened)
            table = preparer.quote(ref.table)
            column = preparer.quote(ref.column)
            for old_value, new_value in operation.value_map.items():
                operations.execute(
                    f"UPDATE {table} SET {column} = {_sql_string(new_value)} "
                    f"WHERE {column} = {_sql_string(old_value)}",
                )
        _sqlite_rebuild(
            operations, connection, ref, operation.name, operation.new_values
        )


def _sqlite_rebuild(
    operations: Operations,
    connection: sa.Connection,
    ref: EnumColumnRef,
    enum_name: str,
    values: Sequence[str],
) -> None:
    """Copy a SQLite table so ``ref``'s enum ``CHECK`` lists ``values``.

    Alembic's batch mode reflects the table to know what to recreate, and
    reflection brings the **old** ``CHECK`` along — so a plain
    ``batch_alter_table`` rebuilds the table with the stale constraint
    still attached and the migration appears to do nothing. The table is
    therefore reflected here, the enum's own check removed from the copy,
    and the result handed to Alembic as ``copy_from``, which is the
    documented way to tell batch mode exactly what the new table should
    look like.

    Args:
        operations (Operations): Alembic's operations proxy.
        connection (sa.Connection): The live connection to reflect from.
        ref (EnumColumnRef): The column being migrated.
        enum_name (str): The enum name carried into the constraint name.
        values (Sequence[str]): The members the new check should allow.
    """
    enum_type = sa.Enum(
        *values,
        name=enum_name,
        native_enum=True,
        create_constraint=True,
    )
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    table = sa.Table(ref.table, metadata, autoload_with=connection)
    for constraint in list(table.constraints):
        if _is_enum_check(constraint, ref.column):
            table.constraints.discard(constraint)
    table.c[ref.column].type = enum_type

    with operations.batch_alter_table(ref.table, copy_from=table) as batch:
        batch.alter_column(ref.column, type_=enum_type)


def _is_enum_check(constraint: sa.Constraint, column: str) -> bool:
    """Whether ``constraint`` is the generated enum check for ``column``.

    Matching on the generated shape rather than on the name means an
    unrelated business rule expressed as a ``CHECK`` survives the
    rebuild, which a name-based match could not guarantee across
    projects with their own naming conventions.

    Args:
        constraint (sa.Constraint): A reflected table constraint.
        column (str): The column whose enum check is being replaced.

    Returns:
        bool: ``True`` when the constraint is this column's enum check.
    """
    if not isinstance(constraint, sa.CheckConstraint):
        return False
    sqltext = str(constraint.sqltext)
    return column in sqltext and _parse_check_values(sqltext) is not None


@renderers.dispatch_for(ReplaceEnumOp)
def _render_replace_enum(
    autogen_context: AutogenContext,
    operation: ReplaceEnumOp,
) -> str:
    """Render :class:`ReplaceEnumOp` as the call that reproduces it.

    Registers the :class:`EnumColumnRef` import so the generated file is
    runnable as written — an operation that renders into a migration
    Alembic cannot import is worse than no autogeneration at all.

    Args:
        autogen_context (AutogenContext): Alembic's rendering context.
        operation (ReplaceEnumOp): The operation to render.

    Returns:
        str: The ``op.replace_enum(...)`` call.
    """
    autogen_context.imports.add(
        "from tempest_fastapi_sdk.db.enum_migrations import EnumColumnRef",
    )
    columns = ", ".join(
        f"EnumColumnRef(table={ref.table!r}, column={ref.column!r})"
        for ref in operation.columns
    )
    parts = [
        repr(operation.name),
        f"new_values={list(operation.new_values)!r}",
        f"old_values={list(operation.old_values)!r}",
        f"columns=[{columns}]",
    ]
    if operation.value_map:
        parts.append(f"value_map={operation.value_map!r}")
    return f"op.replace_enum({', '.join(parts)})"


@Operations.register_operation("drop_enum_type")
class DropEnumTypeOp(MigrateOperation):
    """Drop a PostgreSQL ``ENUM`` type, and do nothing on other backends.

    ``op.drop_table`` removes the table but not the native type its
    ``create_table`` created, so a ``downgrade`` followed by an
    ``upgrade`` fails with ``type "<name>" already exists``. This
    operation emits ``DROP TYPE IF EXISTS``, which is safe to run when
    the type is already gone. SQLite has no type to drop — the enum
    lives in a ``CHECK`` that leaves with its table — so the operation
    is a no-op there and on every other backend.

    :func:`drop_enum_types_on_downgrade` appends it to autogenerated
    downgrades; it is also callable by hand as
    ``op.drop_enum_type("order_status_enum")``.

    Attributes:
        name (str): The enum type name.
        schema (str | None): The schema holding the type, or ``None``
            for the connection's search path.
    """

    def __init__(self, name: str, *, schema: str | None = None) -> None:
        """Record which type to drop.

        Args:
            name (str): The enum type name.
            schema (str | None): The schema holding the type.
        """
        self.name: str = name
        self.schema: str | None = schema

    @classmethod
    def drop_enum_type(
        cls,
        operations: Operations,
        name: str,
        *,
        schema: str | None = None,
    ) -> None:
        """Entry point registered as ``op.drop_enum_type(...)``.

        Args:
            operations (Operations): Alembic's operations proxy.
            name (str): The enum type name.
            schema (str | None): The schema holding the type.
        """
        operations.invoke(cls(name, schema=schema))


@Operations.implementation_for(DropEnumTypeOp)
def _run_drop_enum_type(operations: Operations, operation: DropEnumTypeOp) -> None:
    """Emit ``DROP TYPE IF EXISTS`` on PostgreSQL; skip every other backend.

    The dialect is read from the migration context rather than from a
    bind, so the statement also lands in an offline (``--sql``) script.

    Args:
        operations (Operations): Alembic's operations proxy.
        operation (DropEnumTypeOp): The drop to perform.
    """
    dialect = operations.migration_context.dialect
    if dialect.name != POSTGRESQL_DIALECT:
        return
    preparer = dialect.identifier_preparer
    name = preparer.quote(operation.name)
    if operation.schema:
        name = f"{preparer.quote_schema(operation.schema)}.{name}"
    operations.execute(f"DROP TYPE IF EXISTS {name}")


@renderers.dispatch_for(DropEnumTypeOp)
def _render_drop_enum_type(
    autogen_context: AutogenContext,
    operation: DropEnumTypeOp,
) -> str:
    """Render :class:`DropEnumTypeOp` as the call that reproduces it.

    Args:
        autogen_context (AutogenContext): Alembic's rendering context.
        operation (DropEnumTypeOp): The operation to render.

    Returns:
        str: The ``op.drop_enum_type(...)`` call.
    """
    parts = [repr(operation.name)]
    if operation.schema:
        parts.append(f"schema={operation.schema!r}")
    return f"op.drop_enum_type({', '.join(parts)})"


def render_enum_types(
    type_: str,
    obj: Any,
    autogen_context: AutogenContext,
) -> str | bool | None:
    """Render an SDK column type as a self-contained SQLAlchemy type.

    Alembic's default renders a type it does not know as a dotted path
    into this package — ``tempest_fastapi_sdk.db.…`` — which the
    generated migration never imports, so the file raises ``NameError``
    (or ``F821`` under lint) the first time it runs. Two SDK types hit
    that path:

    * :class:`TempestEnum` renders as ``sa.Enum`` with the members
      spelled out, which avoids the import **and** freezes the members
      as they were when the migration was written — what a migration is
      supposed to record.
    * :class:`~tempest_fastapi_sdk.UtcDateTime` renders as
      ``sa.TIMESTAMP(timezone=True)``, the exact DDL it emits. The
      Python-side normalization it adds is a runtime behaviour of the
      model, not of the schema, so a migration has no reason to carry
      it.

    It also omits the ``CHECK`` constraint that an ``Enum`` (or a
    ``Boolean``) column carries because of its type — the one SQLAlchemy
    marks ``_type_bound``. The ``sa.Enum(..., create_constraint=True)``
    rendered on the column already recreates that ``CHECK`` where the
    backend needs it (SQLite), and PostgreSQL uses the native type.
    Alembic means to skip it on its own, but recognizes it only through
    ``constraint._create_rule.target``, which SQLAlchemy 2.1 no longer
    sets; there the migration came out with two explicit
    ``CheckConstraint`` per enum column, and PostgreSQL refused the
    second (``check constraint "ck_<table>_<enum>" already exists``).
    A ``CheckConstraint`` declared in ``__table_args__`` is not type
    bound and keeps rendering.

    Wire it into ``env.py`` as ``render_item=render_enum_types``.

    Args:
        type_ (str): What Alembic is rendering (``"type"``,
            ``"check"``, ``"column"``, …).
        obj (Any): The object to render.
        autogen_context (AutogenContext): Alembic's rendering context.

    Returns:
        str | bool | None: The rendered expression; ``None`` to omit
        the object from the migration; or ``False`` to let Alembic
        render it its own way.
    """
    if type_ == "check":
        return None if getattr(obj, "_type_bound", False) else False
    if type_ != "type":
        return False
    if isinstance(obj, UtcDateTime):
        return "sa.TIMESTAMP(timezone=True)"
    if not isinstance(obj, sa.Enum):
        return False
    values = ", ".join(repr(value) for value in obj.enums)
    parts = [values] if values else []
    if obj.name:
        parts.append(f"name={obj.name!r}")
    parts.append(f"native_enum={obj.native_enum!r}")
    parts.append(f"create_constraint={obj.create_constraint!r}")
    return f"sa.Enum({', '.join(parts)})"


def sync_enum_types(
    context: Any,
    revision: str | tuple[str, ...],
    directives: list[MigrationScript],
) -> None:
    """Append :class:`ReplaceEnumOp` for every enum whose members changed.

    An ``alembic revision --autogenerate`` hook. Reads the enum members
    the database currently enforces, compares them with the metadata,
    and emits a replacement for each difference — the gap that makes
    adding a member to a Python enum a silent no-op today.

    Detection is best-effort **by design**: an enum the backend cannot
    report on is skipped rather than diffed against a guess, because
    emitting a wrong ``replace_enum`` would drop values from live rows.
    PostgreSQL is read from ``pg_enum``; SQLite from the generated
    ``CHECK`` constraint, and only when it still has the generated shape.

    Args:
        context (Any): Alembic's ``MigrationContext``.
        revision (str | tuple[str, ...]): The revision being generated.
        directives (list[MigrationScript]): The scripts to amend.
    """
    if not directives:
        return
    script = directives[0]
    if script.upgrade_ops is None:
        return
    connection = context.connection
    if connection is None:
        return

    metadata = context.opts.get("target_metadata")
    if metadata is None:
        return

    declared = _declared_enum_types(metadata)
    existing = _existing_enum_types(connection, declared)

    for name, wanted in declared.items():
        current = existing.get(name)
        if current is None or current.values == wanted.values:
            continue
        _append_replacement(script.upgrade_ops, wanted, current)


def _append_replacement(
    upgrade_ops: UpgradeOps,
    wanted: EnumTypeState,
    current: EnumTypeState,
) -> None:
    """Add one replacement to the generated upgrade operations.

    Args:
        upgrade_ops (UpgradeOps): The upgrade section to extend.
        wanted (EnumTypeState): What the metadata declares.
        current (EnumTypeState): What the database currently holds.
    """
    upgrade_ops.ops.append(
        ReplaceEnumOp(
            wanted.name,
            new_values=wanted.values,
            old_values=current.values,
            columns=wanted.columns,
        ),
    )


def drop_enum_types_on_downgrade(
    context: Any,
    revision: str | tuple[str, ...],
    directives: list[MigrationScript],
) -> None:
    """Append :class:`DropEnumTypeOp` after the tables a downgrade drops.

    An ``alembic revision --autogenerate`` hook. The downgrade Alembic
    writes for a new table is only ``op.drop_table(...)``, which leaves
    the PostgreSQL ``ENUM`` type behind, so the next ``upgrade`` fails
    with ``type "<name>" already exists``. For every native enum type
    used by a table this downgrade drops, the hook appends
    ``op.drop_enum_type(name)`` at the end of the downgrade — but only
    when no table that survives the downgrade still uses it: every
    column of the type in the metadata belongs to a dropped table, and
    no table the downgrade recreates declares it. A type shared by two
    tables is therefore dropped by the revision that removes the last
    one.

    The decision is made from the metadata alone, with no database
    connection, and the operation itself is a no-op outside PostgreSQL,
    so the same migration runs on SQLite. Compose it with the other
    hooks through
    :func:`~tempest_fastapi_sdk.db.alembic_hooks.compose_hooks`.

    Args:
        context (Any): Alembic's ``MigrationContext``.
        revision (str | tuple[str, ...]): The revision being generated.
        directives (list[MigrationScript]): The scripts to amend.
    """
    if not directives:
        return
    downgrade_ops = directives[0].downgrade_ops
    if downgrade_ops is None:
        return
    metadata = context.opts.get("target_metadata")
    if metadata is None:
        return

    dropped: dict[tuple[str | None, str], sa.Table] = {}
    recreated: list[sa.Table] = []
    _collect_downgrade_tables(downgrade_ops.ops, dropped, recreated)
    if not dropped:
        return

    users: dict[tuple[str | None, str], set[tuple[str | None, str]]] = {}
    tables = _metadata_tables(metadata)
    for table in tables:
        for key in _native_enum_types(table):
            users.setdefault(key, set()).add((table.schema, table.name))
    kept = {key for table in recreated for key in _native_enum_types(table)}

    candidates: dict[tuple[str | None, str], None] = {}
    by_name = {(table.schema, table.name): table for table in tables}
    for table_key, table in dropped.items():
        for source in (table, by_name.get(table_key)):
            if source is not None:
                candidates.update(dict.fromkeys(_native_enum_types(source)))

    for schema, name in candidates:
        holders = users.get((schema, name), set())
        if (schema, name) in kept or not holders <= dropped.keys():
            continue
        downgrade_ops.ops.append(DropEnumTypeOp(name, schema=schema))


def _collect_downgrade_tables(
    op_list: Sequence[Any],
    dropped: dict[tuple[str | None, str], sa.Table],
    recreated: list[sa.Table],
) -> None:
    """Gather the tables a downgrade drops and the ones it recreates.

    Walks nested operation containers too, so a table operation grouped
    under another directive is not missed.

    Args:
        op_list (Sequence[Any]): The operations to walk.
        dropped (dict[tuple[str | None, str], sa.Table]): Filled with
            ``(schema, table name)`` to the table each ``drop_table``
            removes.
        recreated (list[sa.Table]): Filled with the tables each
            ``create_table`` rebuilds.
    """
    for operation in op_list:
        if isinstance(operation, DropTableOp):
            dropped[(operation.schema, operation.table_name)] = operation.to_table()
        elif isinstance(operation, CreateTableOp):
            recreated.append(operation.to_table())
        nested: Any = getattr(operation, "ops", None)
        if isinstance(nested, list):
            _collect_downgrade_tables(nested, dropped, recreated)


def _metadata_tables(metadata: sa.MetaData | Sequence[sa.MetaData]) -> list[sa.Table]:
    """List every table of the target metadata.

    Alembic accepts ``target_metadata`` as a single ``MetaData`` or as a
    sequence of them.

    Args:
        metadata (sa.MetaData | Sequence[sa.MetaData]): The target
            metadata.

    Returns:
        list[sa.Table]: The tables, across every ``MetaData``.
    """
    collections = [metadata] if isinstance(metadata, sa.MetaData) else metadata
    return [table for item in collections for table in item.tables.values()]


def _native_enum_types(table: sa.Table) -> list[tuple[str | None, str]]:
    """List the native enum types a table's columns use.

    Only named enums with ``native_enum=True`` count: a non-native enum
    is a ``VARCHAR`` plus ``CHECK`` and creates no type to drop.

    Args:
        table (sa.Table): The table to inspect.

    Returns:
        list[tuple[str | None, str]]: ``(schema, type name)`` pairs, in
        column order, without repeats.
    """
    found: dict[tuple[str | None, str], None] = {}
    for column in table.columns:
        enum_type = column.type
        if isinstance(enum_type, sa.Enum) and enum_type.native_enum and enum_type.name:
            found[(enum_type.schema, enum_type.name)] = None
    return list(found)


def _declared_enum_types(metadata: sa.MetaData) -> dict[str, EnumTypeState]:
    """Collect every named enum type declared across the metadata.

    Columns sharing a type are grouped under it, because the replacement
    has to cast all of them in one operation — PostgreSQL will not drop
    the old type while any column still references it.

    Args:
        metadata (sa.MetaData): The project's model metadata.

    Returns:
        dict[str, EnumTypeState]: Type name to its declared state.
    """
    values: dict[str, tuple[str, ...]] = {}
    columns: dict[str, list[EnumColumnRef]] = {}
    for table in metadata.tables.values():
        for column in table.columns:
            enum_type = column.type
            if not isinstance(enum_type, sa.Enum) or not enum_type.name:
                continue
            values[enum_type.name] = tuple(enum_type.enums)
            columns.setdefault(enum_type.name, []).append(
                EnumColumnRef(table=table.name, column=column.name),
            )
    return {
        name: EnumTypeState(
            name=name,
            values=members,
            columns=tuple(columns[name]),
        )
        for name, members in values.items()
    }


def _existing_enum_types(
    connection: sa.Connection,
    declared: dict[str, EnumTypeState],
) -> dict[str, EnumTypeState]:
    """Read the enum members the database currently enforces.

    Args:
        connection (sa.Connection): The live migration connection.
        declared (dict[str, EnumTypeState]): What the metadata declares,
            used to know which tables to inspect on SQLite.

    Returns:
        dict[str, EnumTypeState]: Type name to its state in the
        database. A type the backend cannot report on is absent, which
        the caller treats as "do not touch".
    """
    dialect = connection.dialect.name
    if dialect == POSTGRESQL_DIALECT:
        return _existing_enum_types_postgresql(connection)
    if dialect == SQLITE_DIALECT:
        return _existing_enum_types_sqlite(connection, declared)
    return {}


def _existing_enum_types_postgresql(
    connection: sa.Connection,
) -> dict[str, EnumTypeState]:
    """Read enum labels from ``pg_enum``, in their declared order.

    Order matters: PostgreSQL sorts an enum column by label order, so a
    reordering is a real schema change even when the set is unchanged.

    Args:
        connection (sa.Connection): The live migration connection.

    Returns:
        dict[str, EnumTypeState]: Type name to its state.
    """
    rows = connection.execute(
        sa.text(
            "SELECT t.typname AS name, e.enumlabel AS label "
            "FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "ORDER BY t.typname, e.enumsortorder",
        ),
    )
    labels: dict[str, list[str]] = {}
    for row in rows:
        labels.setdefault(row.name, []).append(row.label)
    return {
        name: EnumTypeState(name=name, values=tuple(values), columns=())
        for name, values in labels.items()
    }


def _existing_enum_types_sqlite(
    connection: sa.Connection,
    declared: dict[str, EnumTypeState],
) -> dict[str, EnumTypeState]:
    """Read enum members from the ``CHECK`` constraints SQLite enforces.

    Only constraints still in the shape SQLAlchemy generates are read;
    anything else is left out, so a hand-written constraint is never
    diffed against a guess.

    Args:
        connection (sa.Connection): The live migration connection.
        declared (dict[str, EnumTypeState]): What the metadata declares,
            which names the tables and constraints worth inspecting.

    Returns:
        dict[str, EnumTypeState]: Type name to its state.
    """
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    found: dict[str, EnumTypeState] = {}

    for name, wanted in declared.items():
        for ref in wanted.columns:
            if ref.table not in tables:
                continue
            for constraint in inspector.get_check_constraints(ref.table):
                sqltext = constraint.get("sqltext") or ""
                if not sqltext.strip().startswith(ref.column):
                    continue
                values = _parse_check_values(sqltext)
                if values is None:
                    continue
                found[name] = EnumTypeState(
                    name=name,
                    values=values,
                    columns=wanted.columns,
                )
                break
    return found


def _parse_check_values(sqltext: str) -> tuple[str, ...] | None:
    """Extract the member list from a ``col IN ('a', 'b')`` check.

    Args:
        sqltext (str): The constraint's SQL text as reported by the
            inspector.

    Returns:
        tuple[str, ...] | None: The members, or ``None`` when the text is
        not in the generated shape.
    """
    match = _CHECK_IN_LIST_RE.search(sqltext)
    if match is None:
        return None
    return tuple(
        value.replace("''", "'")
        for value in _SQL_STRING_RE.findall(match.group("values"))
    )


def _column_default(operations: Operations, ref: EnumColumnRef) -> str | None:
    """Read a PostgreSQL column's current default expression.

    Args:
        operations (Operations): Alembic's operations proxy.
        ref (EnumColumnRef): The column to inspect.

    Returns:
        str | None: The default expression, or ``None`` when the column
        has none.
    """
    row = (
        operations.get_bind()
        .execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column",
            ),
            {"table": ref.table, "column": ref.column},
        )
        .first()
    )
    if row is None:
        return None
    default: str | None = row[0]
    return default


def _cast_expression(column: str, value_map: dict[str, str]) -> str:
    """Build the ``USING`` source that casts a column to the new type.

    Args:
        column (str): The already-quoted column name.
        value_map (dict[str, str]): Renames to apply during the cast.

    Returns:
        str: A text expression producing the new value per row.
    """
    if not value_map:
        return f"{column}::text"
    cases = " ".join(
        f"WHEN {_sql_string(old)} THEN {_sql_string(new)}"
        for old, new in value_map.items()
    )
    return f"CASE {column}::text {cases} ELSE {column}::text END"


def _remapped_default(default: str, value_map: dict[str, str]) -> str:
    """Rewrite a column default so it names a member the new type has.

    A default is stored as ``'open'::order_status_enum``; the type name
    is dropped because the ``SET DEFAULT`` runs after the column already
    has the new type, and the literal is remapped when the member was
    renamed.

    Args:
        default (str): The default expression read from the database.
        value_map (dict[str, str]): Renames applied to rows.

    Returns:
        str: The default expression to restore.
    """
    literal = _SQL_STRING_RE.search(default)
    if literal is None:
        return default
    value = literal.group(1).replace("''", "'")
    return _sql_string(value_map.get(value, value))


def _sql_string(value: str) -> str:
    """Quote a value as a SQL string literal.

    Args:
        value (str): The raw value.

    Returns:
        str: The quoted literal, with embedded quotes doubled.
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


__all__: list[str] = [
    "OLD_TYPE_SUFFIX",
    "SQLITE_DIALECT",
    "DropEnumTypeOp",
    "EnumColumnRef",
    "EnumTypeState",
    "ReplaceEnumOp",
    "drop_enum_types_on_downgrade",
    "postgresql_replace_statements",
    "render_enum_types",
    "sync_enum_types",
]
