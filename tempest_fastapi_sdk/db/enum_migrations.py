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
from alembic.autogenerate.render import _alembic_autogenerate_prefix
from alembic.operations import BatchOperations, MigrateOperation, Operations
from alembic.operations.ops import (
    AddColumnOp,
    AlterColumnOp,
    CreateTableOp,
    DropColumnOp,
    DropTableOp,
    MigrationScript,
    ModifyTableOps,
)
from sqlalchemy.sql.elements import conv

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

_CHECK_COLUMN_RE = re.compile(
    r'^\s*\(?\s*(?:"(?P<quoted>(?:[^"]|"")+)"|(?P<bare>[^\s"(]+))\s+IN\s*\(',
    re.IGNORECASE,
)
"""Matches the column an enum ``CHECK`` constrains: ``status IN (``.

Anchored at the start of the expression, so the check of
``store_age_range`` is never mistaken for the check of ``age_range`` —
a substring test matched both, and a rebuild aimed at one column dropped
the other's check with it.
"""


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

    def to_diff_tuple(self) -> tuple[Any, ...]:
        """Describe the change so ``alembic check`` can report it.

        Alembic's ``check`` command flattens the autogenerated operations
        with ``UpgradeOps.as_diffs``, which calls this on every operation.
        The base :class:`~alembic.operations.MigrateOperation` raises
        ``NotImplementedError``, so an enum whose members changed — the
        exact case :func:`sync_enum_types` exists to catch — crashed
        ``alembic check`` instead of reporting the pending change.

        Returns:
            tuple[Any, ...]: ``("replace_enum", <name>, <new members>)``,
            the shape Alembic expects from a diff.
        """
        return ("replace_enum", self.name, self.new_values)


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
    therefore reflected here, the column's enum check replaced in the
    copy, and the result handed to Alembic as ``copy_from``, which is the
    documented way to tell batch mode exactly what the new table should
    look like.

    The replacement keeps the old check's name, so a column whose check
    is ``ck_<table>_<enum>_<column>`` keeps it; only a column with no
    enum check yet gets ``ck_<table>_<enum>``. The table is reflected into
    a ``MetaData`` without a naming convention: the SDK's ``ck`` template
    embeds ``%(constraint_name)s``, and reflecting under it rewrote every
    **other** check of the table to ``ck_<table>_ck_<table>_...``.

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
        create_constraint=False,
    )
    table = sa.Table(ref.table, sa.MetaData(), autoload_with=connection)
    name: str | None = None
    for constraint in list(table.constraints):
        if _is_enum_check(constraint, ref.column):
            name = name or (str(constraint.name) if constraint.name else None)
            table.constraints.discard(constraint)
    table.c[ref.column].type = enum_type
    if name is None:
        name = _check_name(dict(NAMING_CONVENTION), ref.table, ref.column, enum_type)
    labels = ", ".join(_sql_string(value) for value in values)
    column = connection.dialect.identifier_preparer.quote(ref.column)
    table.append_constraint(
        sa.CheckConstraint(sa.text(f"{column} IN ({labels})"), name=conv(name)),
    )

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
    return _check_column(sqltext) == column and _parse_check_values(sqltext) is not None


def _check_column(sqltext: str) -> str | None:
    """Return the column an enum ``CHECK`` expression constrains.

    Args:
        sqltext (str): The constraint's SQL text, as SQLAlchemy emits it
            or as the inspector reflects it.

    Returns:
        str | None: The column name, unquoted, or ``None`` when the text
        does not start with ``<column> IN (``.
    """
    match = _CHECK_COLUMN_RE.match(sqltext)
    if match is None:
        return None
    quoted = match.group("quoted")
    if quoted is not None:
        return quoted.replace('""', '"')
    return match.group("bare")


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


@Operations.register_operation("rename_enum_type")
class RenameEnumTypeOp(MigrateOperation):
    """Rename an enum type, keeping its members and every column's data.

    ``alembic revision --autogenerate`` never notices a renamed type: on
    PostgreSQL the columns keep pointing at the old ``pg_type`` row and
    the model points at a name that does not exist, so every statement
    that casts to it fails with ``type "<new>" does not exist``.
    :func:`sync_enum_types` detects the rename and emits this operation;
    it is also callable by hand as
    ``op.rename_enum_type("candidate_slug_enum_enum", "candidate_slug_enum",
    columns=[...])``.

    * **PostgreSQL**: ``ALTER TYPE <old> RENAME TO <new>``. The columns
      follow the type, so ``columns`` is not needed there.
    * **SQLite**: there is no type; the name lives in each column's
      ``CHECK`` (``ck_<table>_<old>``). Every listed column's table is
      rebuilt with the check renamed, so the name matches what the model
      declares and ``alembic check`` stops reporting it.

    Reversible: :meth:`reverse` swaps the two names.

    Attributes:
        old_name (str): The type name the database holds today.
        new_name (str): The type name the model declares.
        columns (tuple[EnumColumnRef, ...]): Columns typed by the enum,
            read on SQLite only.
        schema (str | None): The PostgreSQL schema holding the type, or
            ``None`` for the connection's search path.
    """

    def __init__(
        self,
        old_name: str,
        new_name: str,
        *,
        columns: Sequence[EnumColumnRef] = (),
        schema: str | None = None,
    ) -> None:
        """Record which type to rename.

        Args:
            old_name (str): The type name the database holds today.
            new_name (str): The type name the model declares.
            columns (Sequence[EnumColumnRef]): Columns typed by the enum.
            schema (str | None): The PostgreSQL schema holding the type.
        """
        self.old_name: str = old_name
        self.new_name: str = new_name
        self.columns: tuple[EnumColumnRef, ...] = tuple(columns)
        self.schema: str | None = schema

    @classmethod
    def rename_enum_type(
        cls,
        operations: Operations,
        old_name: str,
        new_name: str,
        *,
        columns: Sequence[EnumColumnRef] = (),
        schema: str | None = None,
    ) -> None:
        """Entry point registered as ``op.rename_enum_type(...)``.

        Args:
            operations (Operations): Alembic's operations proxy.
            old_name (str): The type name the database holds today.
            new_name (str): The type name the model declares.
            columns (Sequence[EnumColumnRef]): Columns typed by the enum.
            schema (str | None): The PostgreSQL schema holding the type.
        """
        operations.invoke(
            cls(old_name, new_name, columns=columns, schema=schema),
        )

    def reverse(self) -> RenameEnumTypeOp:
        """Return the operation that undoes this one.

        Returns:
            RenameEnumTypeOp: The same rename with the names swapped.
        """
        return RenameEnumTypeOp(
            self.new_name,
            self.old_name,
            columns=self.columns,
            schema=self.schema,
        )

    def to_diff_tuple(self) -> tuple[Any, ...]:
        """Describe the change so ``alembic check`` can report it.

        Returns:
            tuple[Any, ...]: ``("rename_enum_type", <old>, <new>)``.
        """
        return ("rename_enum_type", self.old_name, self.new_name)


@Operations.implementation_for(RenameEnumTypeOp)
def _run_rename_enum_type(
    operations: Operations,
    operation: RenameEnumTypeOp,
) -> None:
    """Rename the PostgreSQL type, or the SQLite checks that carry its name.

    Args:
        operations (Operations): Alembic's operations proxy.
        operation (RenameEnumTypeOp): The rename to perform.

    Raises:
        NotImplementedError: On SQLite in offline (``--sql``) mode, where
            the table cannot be reflected to rebuild it.
    """
    dialect = operations.migration_context.dialect
    if dialect.name == POSTGRESQL_DIALECT:
        preparer = dialect.identifier_preparer
        old_name = preparer.quote(operation.old_name)
        if operation.schema:
            old_name = f"{preparer.quote_schema(operation.schema)}.{old_name}"
        new_name = preparer.quote(operation.new_name)
        operations.execute(f"ALTER TYPE {old_name} RENAME TO {new_name}")
        return
    if dialect.name != SQLITE_DIALECT:
        return
    if operations.migration_context.as_sql:
        raise NotImplementedError(
            "rename_enum_type needs a live connection on SQLite: the enum name "
            "lives in each column's CHECK, and renaming it rebuilds the table "
            "from its reflected definition.",
        )
    for ref in operation.columns:
        _sqlite_rename_check(operations, ref, operation.old_name, operation.new_name)


def _sqlite_rename_check(
    operations: Operations,
    ref: EnumColumnRef,
    old_name: str,
    new_name: str,
) -> None:
    """Rebuild a SQLite table so ``ref``'s enum check carries ``new_name``.

    The check keeps its expression; only the enum name embedded in the
    constraint name changes, at its last occurrence, so the rest of the
    name the naming convention produced (``ck_<table>_`` and a
    ``_<column>`` suffix) is preserved. A check whose name does not carry
    ``old_name`` is left alone.

    The table is reflected into a ``MetaData`` **without** a naming
    convention: the SDK's ``ck`` template embeds ``%(constraint_name)s``,
    so reflecting under it rewrites every reflected check name a second
    time (``ck_things_ck_things_other_enum``).

    Args:
        operations (Operations): Alembic's operations proxy.
        ref (EnumColumnRef): The column whose check to rename.
        old_name (str): The enum name the check carries today.
        new_name (str): The enum name it should carry.
    """
    connection = operations.get_bind()
    table = sa.Table(ref.table, sa.MetaData(), autoload_with=connection)
    renamed = False
    for constraint in list(table.constraints):
        if not isinstance(constraint, sa.CheckConstraint) or not _is_enum_check(
            constraint, ref.column
        ):
            continue
        name = constraint.name
        if not isinstance(name, str) or old_name not in name:
            continue
        cut = name.rfind(old_name)
        target = f"{name[:cut]}{new_name}{name[cut + len(old_name) :]}"
        table.constraints.discard(constraint)
        table.append_constraint(
            sa.CheckConstraint(
                sa.text(str(constraint.sqltext)),
                name=conv(target),
            ),
        )
        renamed = True
    if not renamed:
        return
    with operations.batch_alter_table(
        ref.table,
        copy_from=table,
        recreate="always",
    ):
        pass


@renderers.dispatch_for(RenameEnumTypeOp)
def _render_rename_enum_type(
    autogen_context: AutogenContext,
    operation: RenameEnumTypeOp,
) -> str:
    """Render :class:`RenameEnumTypeOp` as the call that reproduces it.

    Args:
        autogen_context (AutogenContext): Alembic's rendering context.
        operation (RenameEnumTypeOp): The operation to render.

    Returns:
        str: The ``op.rename_enum_type(...)`` call.
    """
    parts = [repr(operation.old_name), repr(operation.new_name)]
    if operation.columns:
        autogen_context.imports.add(
            "from tempest_fastapi_sdk.db.enum_migrations import EnumColumnRef",
        )
        columns = ", ".join(
            f"EnumColumnRef(table={ref.table!r}, column={ref.column!r})"
            for ref in operation.columns
        )
        parts.append(f"columns=[{columns}]")
    if operation.schema:
        parts.append(f"schema={operation.schema!r}")
    return f"op.rename_enum_type({', '.join(parts)})"


@Operations.register_operation("create_enum_check")
@BatchOperations.register_operation("create_enum_check", "batch_create_enum_check")
class CreateEnumCheckOp(MigrateOperation):
    """Create the ``CHECK`` that enforces an enum column on SQLite.

    SQLite has no enum type: the members live in a ``CHECK`` named by the
    naming convention. Left to ``sa.Enum(create_constraint=True)`` inside
    ``batch_alter_table``, that check is named after the **type**, so two
    columns of one enum produce two checks under one name and the batch,
    which keys constraints by name, keeps only the last — the other column
    then accepts any value, with nothing reporting it. This operation names
    the check explicitly, per column.

    It is a no-op on every other backend: PostgreSQL enforces the members
    through the native type and carries no ``CHECK``.

    :func:`sqlite_enum_checks` emits it after every enum ``add_column``
    (and after a ``create_table`` whose columns share an enum); it is also
    callable by hand as ``op.create_enum_check(...)`` or, inside a batch,
    ``batch_op.create_enum_check(...)``.

    Attributes:
        table_name (str): The table holding the column.
        column_name (str): The enum column.
        values (tuple[str, ...]): The members the check allows.
        name (str): The constraint name, used as given.
        schema (str | None): The table's schema.
    """

    def __init__(
        self,
        table_name: str,
        column_name: str,
        values: Sequence[str],
        *,
        name: str,
        schema: str | None = None,
    ) -> None:
        """Record which check to create.

        Args:
            table_name (str): The table holding the column.
            column_name (str): The enum column.
            values (Sequence[str]): The members the check allows.
            name (str): The constraint name, used as given (the naming
                convention is not applied a second time).
            schema (str | None): The table's schema.
        """
        self.table_name: str = table_name
        self.column_name: str = column_name
        self.values: tuple[str, ...] = tuple(values)
        self.name: str = name
        self.schema: str | None = schema

    @classmethod
    def create_enum_check(
        cls,
        operations: Operations,
        table_name: str,
        column_name: str,
        values: Sequence[str],
        *,
        name: str,
        schema: str | None = None,
    ) -> None:
        """Entry point registered as ``op.create_enum_check(...)``.

        Args:
            operations (Operations): Alembic's operations proxy.
            table_name (str): The table holding the column.
            column_name (str): The enum column.
            values (Sequence[str]): The members the check allows.
            name (str): The constraint name.
            schema (str | None): The table's schema.
        """
        operations.invoke(
            cls(table_name, column_name, values, name=name, schema=schema),
        )

    @classmethod
    def batch_create_enum_check(
        cls,
        operations: BatchOperations,
        column_name: str,
        values: Sequence[str],
        *,
        name: str,
    ) -> None:
        """Entry point registered as ``batch_op.create_enum_check(...)``.

        Args:
            operations (BatchOperations): Alembic's batch proxy.
            column_name (str): The enum column.
            values (Sequence[str]): The members the check allows.
            name (str): The constraint name.
        """
        operations.invoke(
            cls(
                operations.impl.table_name,
                column_name,
                values,
                name=name,
                schema=operations.impl.schema,
            ),
        )

    def reverse(self) -> DropEnumCheckOp:
        """Return the operation that undoes this one.

        Returns:
            DropEnumCheckOp: The drop of this column's enum check.
        """
        return DropEnumCheckOp(self.table_name, self.column_name, schema=self.schema)

    def to_diff_tuple(self) -> tuple[Any, ...]:
        """Describe the change so ``alembic check`` can report it.

        Returns:
            tuple[Any, ...]: ``("create_enum_check", <table>, <column>)``.
        """
        return ("create_enum_check", self.table_name, self.column_name)


@Operations.implementation_for(CreateEnumCheckOp)
def _run_create_enum_check(
    operations: Operations | BatchOperations,
    operation: CreateEnumCheckOp,
) -> None:
    """Add the check on SQLite, through a batch; skip every other backend.

    Inside a ``batch_alter_table`` the constraint joins the batch's own
    rebuild; outside one, a batch is opened for it, because SQLite cannot
    ``ALTER TABLE ... ADD CONSTRAINT``.

    Args:
        operations (Operations): Alembic's operations proxy, or the batch
            proxy when invoked as ``batch_op.create_enum_check``.
        operation (CreateEnumCheckOp): The check to create.
    """
    dialect = operations.migration_context.dialect
    if dialect.name != SQLITE_DIALECT:
        return
    labels = ", ".join(_sql_string(value) for value in operation.values)
    condition = (
        f"{dialect.identifier_preparer.quote(operation.column_name)} IN ({labels})"
    )
    if isinstance(operations, BatchOperations):
        operations.create_check_constraint(conv(operation.name), condition)
        return
    with operations.batch_alter_table(
        operation.table_name, schema=operation.schema
    ) as batch:
        batch.create_check_constraint(conv(operation.name), condition)


@Operations.register_operation("drop_enum_check")
@BatchOperations.register_operation("drop_enum_check", "batch_drop_enum_check")
class DropEnumCheckOp(MigrateOperation):
    """Drop the SQLite ``CHECK`` of an enum column, before dropping the column.

    SQLite's batch mode copies every reflected ``CHECK`` into the rebuilt
    table and cannot tell which column a check text names, so
    ``batch_op.drop_column("age_range")`` rebuilt the table with
    ``CHECK (age_range IN (...))`` still attached and failed with
    ``no such column: age_range`` — the downgrade of every enum
    ``add_column`` broke. This operation reads the table's checks, picks the
    ones in the generated ``<column> IN (...)`` shape for this column, and
    drops them by name, whatever name they carry.

    It is a no-op on every other backend. :func:`sqlite_enum_checks` emits it
    before every enum ``drop_column``.

    Attributes:
        table_name (str): The table holding the column.
        column_name (str): The enum column.
        schema (str | None): The table's schema.
    """

    def __init__(
        self,
        table_name: str,
        column_name: str,
        *,
        schema: str | None = None,
    ) -> None:
        """Record which column's check to drop.

        Args:
            table_name (str): The table holding the column.
            column_name (str): The enum column.
            schema (str | None): The table's schema.
        """
        self.table_name: str = table_name
        self.column_name: str = column_name
        self.schema: str | None = schema

    @classmethod
    def drop_enum_check(
        cls,
        operations: Operations,
        table_name: str,
        column_name: str,
        *,
        schema: str | None = None,
    ) -> None:
        """Entry point registered as ``op.drop_enum_check(...)``.

        Args:
            operations (Operations): Alembic's operations proxy.
            table_name (str): The table holding the column.
            column_name (str): The enum column.
            schema (str | None): The table's schema.
        """
        operations.invoke(cls(table_name, column_name, schema=schema))

    @classmethod
    def batch_drop_enum_check(
        cls,
        operations: BatchOperations,
        column_name: str,
    ) -> None:
        """Entry point registered as ``batch_op.drop_enum_check(...)``.

        Args:
            operations (BatchOperations): Alembic's batch proxy.
            column_name (str): The enum column.
        """
        operations.invoke(
            cls(
                operations.impl.table_name,
                column_name,
                schema=operations.impl.schema,
            ),
        )

    def to_diff_tuple(self) -> tuple[Any, ...]:
        """Describe the change so ``alembic check`` can report it.

        Returns:
            tuple[Any, ...]: ``("drop_enum_check", <table>, <column>)``.
        """
        return ("drop_enum_check", self.table_name, self.column_name)


@Operations.implementation_for(DropEnumCheckOp)
def _run_drop_enum_check(
    operations: Operations | BatchOperations,
    operation: DropEnumCheckOp,
) -> None:
    """Drop the column's enum checks on SQLite; skip every other backend.

    Args:
        operations (Operations): Alembic's operations proxy, or the batch
            proxy when invoked as ``batch_op.drop_enum_check``.
        operation (DropEnumCheckOp): The drop to perform.

    Raises:
        NotImplementedError: On SQLite in offline (``--sql``) mode, where
            the checks cannot be read.
    """
    context = operations.migration_context
    if context.dialect.name != SQLITE_DIALECT:
        return
    if context.as_sql:
        raise NotImplementedError(
            "drop_enum_check needs a live connection on SQLite: it reads the "
            "table's CHECK constraints to find the ones naming the column.",
        )
    inspector = sa.inspect(operations.get_bind())
    names = list(
        dict.fromkeys(
            str(check["name"])
            for check in inspector.get_check_constraints(
                operation.table_name, schema=operation.schema
            )
            if check.get("name")
            and _check_column(str(check.get("sqltext") or "")) == operation.column_name
            and _parse_check_values(str(check.get("sqltext") or "")) is not None
        ),
    )
    if not names:
        return
    if isinstance(operations, BatchOperations):
        for name in names:
            operations.drop_constraint(conv(name), type_="check")
        return
    with operations.batch_alter_table(
        operation.table_name, schema=operation.schema
    ) as batch:
        for name in names:
            batch.drop_constraint(conv(name), type_="check")


@renderers.dispatch_for(CreateEnumCheckOp)
def _render_create_enum_check(
    autogen_context: AutogenContext,
    operation: CreateEnumCheckOp,
) -> str:
    """Render :class:`CreateEnumCheckOp`, as ``batch_op.`` inside a batch.

    Args:
        autogen_context (AutogenContext): Alembic's rendering context.
        operation (CreateEnumCheckOp): The operation to render.

    Returns:
        str: The ``create_enum_check(...)`` call.
    """
    prefix = _alembic_autogenerate_prefix(autogen_context)
    parts = [] if prefix == "batch_op." else [repr(operation.table_name)]
    parts.extend(
        [
            repr(operation.column_name),
            repr(list(operation.values)),
            f"name={operation.name!r}",
        ],
    )
    if operation.schema and prefix != "batch_op.":
        parts.append(f"schema={operation.schema!r}")
    return f"{prefix}create_enum_check({', '.join(parts)})"


@renderers.dispatch_for(DropEnumCheckOp)
def _render_drop_enum_check(
    autogen_context: AutogenContext,
    operation: DropEnumCheckOp,
) -> str:
    """Render :class:`DropEnumCheckOp`, as ``batch_op.`` inside a batch.

    Args:
        autogen_context (AutogenContext): Alembic's rendering context.
        operation (DropEnumCheckOp): The operation to render.

    Returns:
        str: The ``drop_enum_check(...)`` call.
    """
    prefix = _alembic_autogenerate_prefix(autogen_context)
    parts = [] if prefix == "batch_op." else [repr(operation.table_name)]
    parts.append(repr(operation.column_name))
    if operation.schema and prefix != "batch_op.":
        parts.append(f"schema={operation.schema!r}")
    return f"{prefix}drop_enum_check({', '.join(parts)})"


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

    A **renamed** type is detected too, and emitted as
    :class:`RenameEnumTypeOp` ahead of any replacement — the case
    :func:`~tempest_fastapi_sdk.db.enums.enum_type_name` creates for a
    class ending in ``Enum``, whose type used to be
    ``<name>_enum_enum``. On PostgreSQL the declared type is missing and
    every column it types is still typed by one other existing enum that
    the metadata no longer declares; on SQLite the column's enum
    ``CHECK`` carries a name that differs from the declared one only
    where the enum name sits.

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
    renamed = _renamed_enum_types(
        connection, declared, existing, _declared_check_names(metadata)
    )

    for name, wanted in declared.items():
        old_name = renamed.get(name)
        current = existing.get(name)
        if old_name is not None:
            _drop_enum_type_swaps(script.upgrade_ops.ops, {old_name, name})
            if script.downgrade_ops is not None:
                _drop_enum_type_swaps(script.downgrade_ops.ops, {old_name, name})
            _append_with_reverse(
                script,
                RenameEnumTypeOp(old_name, name, columns=wanted.columns),
            )
            current = current or existing.get(old_name)
        if current is None or current.values == wanted.values:
            continue
        _append_with_reverse(
            script,
            ReplaceEnumOp(
                wanted.name,
                new_values=wanted.values,
                old_values=current.values,
                columns=wanted.columns,
            ),
        )


def _append_with_reverse(
    script: MigrationScript,
    operation: RenameEnumTypeOp | ReplaceEnumOp,
) -> None:
    """Append an operation to the upgrade and its reverse to the downgrade.

    The reverse goes to the **front** of the downgrade, so operations
    appended in order are undone in the opposite order, before anything
    autogenerate reversed. Without it the downgrade of a revision that
    renamed or replaced a type was empty, and ``downgrade`` left the
    database on the new definition while marking it as the old one.

    Args:
        script (MigrationScript): The script being generated.
        operation (RenameEnumTypeOp | ReplaceEnumOp): The operation.
    """
    assert script.upgrade_ops is not None
    script.upgrade_ops.ops.append(operation)
    if script.downgrade_ops is not None:
        script.downgrade_ops.ops.insert(0, operation.reverse())


def _drop_enum_type_swaps(op_list: list[Any], names: set[str]) -> None:
    """Remove autogenerate's type change between two names of one enum.

    On PostgreSQL, autogenerate compares an ``Enum`` column by type name
    and proposes ``alter_column(type_=...)`` from the old name to the new
    one — which fails with ``type "<new>" does not exist``, because the
    type is renamed, not created. :class:`RenameEnumTypeOp` replaces it,
    so the swap is dropped (an ``alter_column`` that also changes
    something else keeps that part), and a batch block left empty goes
    with it.

    Args:
        op_list (list[Any]): The operations to edit in place.
        names (set[str]): The old and the new type name.
    """
    for operation in list(op_list):
        nested: Any = getattr(operation, "ops", None)
        if isinstance(nested, list):
            _drop_enum_type_swaps(nested, names)
            if not nested:
                op_list.remove(operation)
            continue
        if not isinstance(operation, AlterColumnOp):
            continue
        before = operation.existing_type
        after = operation.modify_type
        if not (
            isinstance(before, sa.Enum)
            and isinstance(after, sa.Enum)
            and {before.name, after.name} == names
        ):
            continue
        operation.modify_type = None
        if not operation.has_changes():
            op_list.remove(operation)


def sqlite_enum_checks(
    context: Any,
    revision: str | tuple[str, ...],
    directives: list[MigrationScript],
) -> None:
    """Give every enum column its own SQLite ``CHECK``, and drop it with the column.

    An ``alembic revision --autogenerate`` hook, for the two places where
    the ``CHECK`` an ``sa.Enum(create_constraint=True)`` carries goes wrong
    under the batch mode the SDK's ``env.py`` turns on:

    * **Adding a column.** The check is named after the enum type, so two
      columns of one enum get two checks under one name, and the batch
      keeps one. Every enum ``add_column`` is rewritten to
      ``create_constraint=False`` and followed by
      :class:`CreateEnumCheckOp`, named as the model's metadata names that
      column's check — ``ck_<table>_<enum>``, or ``ck_<table>_<enum>_<column>``
      for the second and later columns of one enum in a table
      (:class:`~tempest_fastapi_sdk.db.enums.TempestEnum`). A
      ``create_table`` whose columns share an enum gets the same treatment
      for those later columns.
    * **Dropping a column.** The batch copies the reflected check that
      names the column into the rebuilt table, which then fails with
      ``no such column``. Every enum ``drop_column`` is preceded by
      :class:`DropEnumCheckOp`.

    Both operations are no-ops outside SQLite, so the same migration runs on
    PostgreSQL with no ``CHECK`` at all. Both the upgrade and the downgrade
    are rewritten. Compose it with the other hooks through
    :func:`~tempest_fastapi_sdk.db.alembic_hooks.compose_hooks`.

    Args:
        context (Any): Alembic's ``MigrationContext``.
        revision (str | tuple[str, ...]): The revision being generated.
        directives (list[MigrationScript]): The scripts to amend.
    """
    if not directives:
        return
    metadata = context.opts.get("target_metadata")
    check_names = _declared_check_names(metadata) if metadata is not None else {}
    convention = _naming_convention(metadata)
    script = directives[0]
    for container in (script.upgrade_ops, script.downgrade_ops):
        if container is not None:
            container.ops[:] = _with_enum_checks(
                container.ops, None, check_names, convention
            )


def _with_enum_checks(
    op_list: Sequence[Any],
    batch_table: str | None,
    check_names: dict[EnumColumnRef, str],
    convention: dict[Any, Any],
) -> list[Any]:
    """Rewrite one list of operations for :func:`sqlite_enum_checks`.

    Args:
        op_list (Sequence[Any]): The operations, top level or inside one
            ``ModifyTableOps`` (a batch block).
        batch_table (str | None): The batch's table, or ``None`` at top
            level.
        check_names (dict[EnumColumnRef, str]): The metadata's check name
            per enum column.
        convention (dict[Any, Any]): The metadata's naming convention, for
            columns the metadata no longer declares.

    Returns:
        list[Any]: The rewritten operations.
    """
    result: list[Any] = []
    for operation in op_list:
        if isinstance(operation, ModifyTableOps):
            operation.ops[:] = _with_enum_checks(
                operation.ops, operation.table_name, check_names, convention
            )
            result.append(operation)
        elif isinstance(operation, AddColumnOp):
            enum_type = _checked_enum(operation.column.type)
            if enum_type is None:
                result.append(operation)
                continue
            operation.column = _without_check(operation.column, enum_type)
            ref = EnumColumnRef(
                table=operation.table_name, column=operation.column.name
            )
            name = check_names.get(ref) or _check_name(
                convention, operation.table_name, operation.column.name, enum_type
            )
            result.extend(
                [
                    operation,
                    CreateEnumCheckOp(
                        operation.table_name,
                        operation.column.name,
                        enum_type.enums,
                        name=name,
                        schema=operation.schema,
                    ),
                ],
            )
        elif isinstance(operation, DropColumnOp):
            if _dropped_enum(operation) is not None:
                result.append(
                    DropEnumCheckOp(
                        operation.table_name,
                        operation.column_name,
                        schema=operation.schema,
                    ),
                )
            result.append(operation)
        elif isinstance(operation, CreateTableOp) and batch_table is None:
            result.append(operation)
            result.extend(_split_shared_enum_checks(operation, check_names, convention))
        else:
            result.append(operation)
    return result


def _split_shared_enum_checks(
    operation: CreateTableOp,
    check_names: dict[EnumColumnRef, str],
    convention: dict[Any, Any],
) -> list[CreateEnumCheckOp]:
    """Give the later columns of a shared enum in a new table their own check.

    The rendered ``sa.Enum`` names every column's check after the type, so
    the second column of one enum would reuse the first one's name — which
    SQLite accepts in ``CREATE TABLE``, and the next batch on the table then
    loses. Those columns are rewritten to ``create_constraint=False`` and
    get a :class:`CreateEnumCheckOp` each, under the metadata's name.

    Args:
        operation (CreateTableOp): The table being created; edited in place.
        check_names (dict[EnumColumnRef, str]): The metadata's check name
            per enum column.
        convention (dict[Any, Any]): The metadata's naming convention.

    Returns:
        list[CreateEnumCheckOp]: The checks to create after the table.
    """
    seen: set[str] = set()
    created: list[CreateEnumCheckOp] = []
    columns = list(operation.columns)
    for index, item in enumerate(columns):
        if not isinstance(item, sa.Column):
            continue
        enum_type = _checked_enum(item.type)
        if enum_type is None or enum_type.name is None:
            continue
        if enum_type.name not in seen:
            seen.add(enum_type.name)
            continue
        ref = EnumColumnRef(table=operation.table_name, column=item.name)
        name = check_names.get(ref) or _check_name(
            convention,
            operation.table_name,
            item.name,
            enum_type,
            per_column=True,
        )
        columns[index] = _without_check(item, enum_type)
        created.append(
            CreateEnumCheckOp(
                operation.table_name,
                item.name,
                enum_type.enums,
                name=name,
                schema=operation.schema,
            ),
        )
    operation.columns = columns
    return created


def _checked_enum(type_: Any) -> sa.Enum | None:
    """Return ``type_`` when it is an enum that carries a ``CHECK``.

    Args:
        type_ (Any): A column type.

    Returns:
        sa.Enum | None: The enum, or ``None`` for any other type or an
        enum created with ``create_constraint=False``.
    """
    if isinstance(type_, sa.Enum) and type_.create_constraint:
        return type_
    return None


def _dropped_enum(operation: DropColumnOp) -> sa.Enum | None:
    """Return the enum type of the column a ``drop_column`` removes.

    Args:
        operation (DropColumnOp): The drop.

    Returns:
        sa.Enum | None: The type, when autogenerate recorded the column
        and it is an enum that carries a ``CHECK``.
    """
    try:
        column = operation.to_column()
    except (AttributeError, ValueError):
        return None
    return _checked_enum(column.type)


def _without_check(column: sa.Column[Any], enum_type: sa.Enum) -> sa.Column[Any]:
    """Copy ``column`` with an equal enum type that carries no ``CHECK``.

    A copy, because the column of an autogenerated operation is the
    model's own ``Column``.

    Args:
        column (sa.Column[Any]): The column to copy.
        enum_type (sa.Enum): Its enum type.

    Returns:
        sa.Column[Any]: The copy, typed ``create_constraint=False``.
    """
    copy = column._copy()
    copy.type = sa.Enum(
        *enum_type.enums,
        name=enum_type.name,
        schema=enum_type.schema,
        native_enum=enum_type.native_enum,
        create_constraint=False,
    )
    return copy


def _naming_convention(
    metadata: sa.MetaData | Sequence[sa.MetaData] | None,
) -> dict[Any, Any]:
    """Return the naming convention of the target metadata.

    Args:
        metadata (sa.MetaData | Sequence[sa.MetaData] | None): The target
            metadata.

    Returns:
        dict[Any, Any]: The first metadata's convention, or the SDK's
        :data:`~tempest_fastapi_sdk.db.model.NAMING_CONVENTION` when there
        is none.
    """
    if isinstance(metadata, sa.MetaData):
        return dict(metadata.naming_convention)
    if metadata:
        return dict(metadata[0].naming_convention)
    return dict(NAMING_CONVENTION)


def _check_name(
    convention: dict[Any, Any],
    table: str,
    column: str,
    enum_type: sa.Enum,
    *,
    per_column: bool = False,
) -> str:
    """Name an enum check the way the naming convention would.

    Used for a column the metadata does not declare (a downgrade re-adding
    a column the upgrade removed). The name comes from SQLAlchemy applying
    ``convention`` to a scratch table, not from a template rebuilt here.

    Args:
        convention (dict[Any, Any]): The naming convention.
        table (str): The table name.
        column (str): The column name.
        enum_type (sa.Enum): The column's enum type.
        per_column (bool): Whether the column shares its enum with an
            earlier column of the table, which suffixes the column name.

    Returns:
        str: The constraint name.
    """
    base = enum_type.name or column
    constraint_name = f"{base}_{column}" if per_column else base
    scratch = sa.Table(
        table,
        sa.MetaData(naming_convention=convention),
        sa.Column(column, sa.String()),
    )
    probe = sa.CheckConstraint(scratch.c[column].is_not(None), name=constraint_name)
    scratch.append_constraint(probe)
    return str(probe.name)


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
                if _check_column(sqltext) != ref.column:
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


def _declared_check_names(
    metadata: sa.MetaData | Sequence[sa.MetaData],
) -> dict[EnumColumnRef, str]:
    """Map each enum column to the name of the ``CHECK`` its type attaches.

    Args:
        metadata (sa.MetaData | Sequence[sa.MetaData]): The target
            metadata.

    Returns:
        dict[EnumColumnRef, str]: The type-bound check name per column,
        as the naming convention rendered it.
    """
    names: dict[EnumColumnRef, str] = {}
    for table in _metadata_tables(metadata):
        for constraint in table.constraints:
            if not (
                isinstance(constraint, sa.CheckConstraint)
                and getattr(constraint, "_type_bound", False)
                and isinstance(constraint.name, str)
            ):
                continue
            for column in constraint.columns:
                ref = EnumColumnRef(table=table.name, column=column.name)
                names[ref] = str(constraint.name)
    return names


def _renamed_enum_types(
    connection: sa.Connection,
    declared: dict[str, EnumTypeState],
    existing: dict[str, EnumTypeState],
    check_names: dict[EnumColumnRef, str],
) -> dict[str, str]:
    """Find declared enum types the database still holds under another name.

    Args:
        connection (sa.Connection): The live migration connection.
        declared (dict[str, EnumTypeState]): What the metadata declares.
        existing (dict[str, EnumTypeState]): What the database holds.
        check_names (dict[EnumColumnRef, str]): The declared check name
            per enum column, read on SQLite.

    Returns:
        dict[str, str]: Declared type name to the name the database holds.
        A type whose columns disagree, or whose old name is still declared
        by the metadata, is left out rather than guessed.
    """
    dialect = connection.dialect.name
    if dialect == POSTGRESQL_DIALECT:
        found = _renamed_enum_types_postgresql(connection, declared, existing)
    elif dialect == SQLITE_DIALECT:
        found = _renamed_enum_types_sqlite(connection, declared, check_names)
    else:
        return {}
    return {
        new: old for new, old in found.items() if old != new and old not in declared
    }


def _renamed_enum_types_postgresql(
    connection: sa.Connection,
    declared: dict[str, EnumTypeState],
    existing: dict[str, EnumTypeState],
) -> dict[str, str]:
    """Read which existing enum type the columns of a missing type use.

    Args:
        connection (sa.Connection): The live migration connection.
        declared (dict[str, EnumTypeState]): What the metadata declares.
        existing (dict[str, EnumTypeState]): The enum types in ``pg_enum``.

    Returns:
        dict[str, str]: Declared type name to the existing type every one
        of its columns uses today.
    """
    renamed: dict[str, str] = {}
    for name, wanted in declared.items():
        if name in existing:
            continue
        used: set[str] = set()
        for ref in wanted.columns:
            row = connection.execute(
                sa.text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name = :table AND column_name = :column",
                ),
                {"table": ref.table, "column": ref.column},
            ).first()
            if row is not None:
                used.add(str(row[0]))
        if len(used) == 1:
            (old_name,) = used
            if old_name in existing:
                renamed[name] = old_name
    return renamed


def _renamed_enum_types_sqlite(
    connection: sa.Connection,
    declared: dict[str, EnumTypeState],
    check_names: dict[EnumColumnRef, str],
) -> dict[str, str]:
    """Read the enum name each column's SQLite ``CHECK`` still carries.

    The declared check name is split around the declared enum name; a
    reflected check on the same column with the same prefix and suffix
    but something else in the middle carries the old enum name. A column
    that already has a check under the declared name, or whose check
    already ends with the declared enum name (a database created under a
    naming convention the metadata does not declare), is not renamed.

    Args:
        connection (sa.Connection): The live migration connection.
        declared (dict[str, EnumTypeState]): What the metadata declares.
        check_names (dict[EnumColumnRef, str]): The declared check name
            per enum column.

    Returns:
        dict[str, str]: Declared type name to the old name its checks
        carry, when every column agrees.
    """
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    renamed: dict[str, str] = {}
    for name, wanted in declared.items():
        old_names: set[str] = set()
        for ref in wanted.columns:
            expected = check_names.get(ref)
            if ref.table not in tables or expected is None or name not in expected:
                continue
            cut = expected.rfind(name)
            prefix, suffix = expected[:cut], expected[cut + len(name) :]
            reflected = [
                str(constraint.get("name") or "")
                for constraint in inspector.get_check_constraints(ref.table)
                if _check_column(str(constraint.get("sqltext") or "")) == ref.column
            ]
            if expected in reflected:
                continue
            for current in reflected:
                if current.endswith(f"{name}{suffix}"):
                    continue
                if (
                    current.startswith(prefix)
                    and current.endswith(suffix)
                    and len(current) > len(prefix) + len(suffix)
                ):
                    old_names.add(current[len(prefix) : len(current) - len(suffix)])
        if len(old_names) == 1:
            renamed[name] = old_names.pop()
    return renamed


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


def type_bound_check_include_object(
    metadata: sa.MetaData | Sequence[sa.MetaData] | None,
) -> Callable[[Any, str | None, str, bool, Any], bool]:
    """Build the ``include_object`` filter that hides enum ``CHECK`` drift.

    Alembic's CHECK comparison reads the constraint from the database but
    drops it from the metadata side: it collects metadata CHECKs through
    ``all_table_check_constraints``, which skips the ones a column's type
    attaches (SQLAlchemy marks them ``_type_bound``). On SQLite — where an
    ``Enum`` column is a ``VARCHAR`` plus a ``CHECK`` — the database
    therefore always shows one constraint the metadata does not, and
    ``alembic check`` fails on a clean ``upgrade head`` proposing
    ``remove_constraint`` for ``ck_<table>_<enum>``.

    A reflected ``CHECK`` whose name matches a type-bound CHECK declared in
    ``metadata`` **on the same table** is not drift: the column type
    recreates it. Returning ``False`` skips it; every other object is left
    to Alembic. The table is part of the key because two tables may carry a
    check of the same name under a project's own naming convention, and a
    match on the name alone would hide one table's drift behind the
    other's.

    A reflected enum-shaped ``CHECK`` (``<column> IN (...)``) on a column
    the metadata types with a check-carrying enum is hidden too, whatever
    its name. Its name can legitimately differ from the declared one —
    after :func:`~tempest_fastapi_sdk.db.enums.enum_type_name` stopped
    doubling the ``_enum`` suffix, or in a table an older SDK rebuilt —
    and the name-only match then proposed ``remove_constraint`` for the
    check that enforces the column. Renames and member changes are
    :func:`sync_enum_types`' job, which reads the same check.

    Pass it as ``include_object`` in ``context.configure``;
    :func:`~tempest_fastapi_sdk.db.alembic_env.run_alembic_env` does.

    Args:
        metadata (sa.MetaData | Sequence[sa.MetaData] | None): The
            autogenerate target metadata, or a sequence of metadatas.

    Returns:
        Callable[[Any, str | None, str, bool, Any], bool]: The
        ``include_object`` callback Alembic expects.
    """
    names = _type_bound_check_names(metadata)
    columns = (
        {(ref.table, ref.column) for ref in _declared_check_names(metadata)}
        if metadata is not None
        else set()
    )

    def include_object(
        object_: Any,
        name: str | None,
        type_: str,
        reflected: bool,
        compare_to: Any,
    ) -> bool:
        """Keep every object except a table's own type-bound enum check.

        Args:
            object_ (Any): The object Alembic is considering — for a
                ``check_constraint`` it is the ``CheckConstraint``, whose
                ``table`` names the scope.
            name (str | None): The object's name, as Alembic reports it.
            type_ (str): The object kind (``"check_constraint"``, ...).
            reflected (bool): Whether the object came from the database.
            compare_to (Any): The metadata counterpart, when there is one.

        Returns:
            bool: ``False`` to hide a reflected check that belongs to the
            same table and name as a declared type-bound check, or that
            is the enum check of a declared enum column; ``True``
            otherwise.
        """
        if type_ != "check_constraint" or not reflected:
            return True
        table = getattr(object_, "table", None)
        table_name = getattr(table, "name", None)
        if (table_name, name) in names:
            return False
        sqltext = getattr(object_, "sqltext", None)
        column = _check_column(str(sqltext)) if sqltext is not None else None
        return (table_name, column) not in columns

    return include_object


def _type_bound_check_names(
    metadata: sa.MetaData | Sequence[sa.MetaData] | None,
) -> set[tuple[str | None, str]]:
    """Collect the ``(table, name)`` of the type-bound CHECKs in metadata.

    ``_type_bound`` is the flag SQLAlchemy sets on the ``CheckConstraint``
    a column's ``Enum`` or ``Boolean`` type carries; reading it is a
    deliberate coupling, the same the renderer makes, and
    ``tests/db/test_alembic_env.py`` pins the behaviour end to end. The
    table name is part of the key so a check is matched only against the
    table that declares it.

    Args:
        metadata (sa.MetaData | Sequence[sa.MetaData] | None): The
            autogenerate target metadata, or a sequence of metadatas.

    Returns:
        set[tuple[str | None, str]]: The ``(table name, constraint name)``
        of every named type-bound CHECK. A constraint whose name is not a
        string (SQLAlchemy's symbolic ``NONE_NAME``) is skipped — the
        database has no name to match it.
    """
    if metadata is None:
        return set()
    metadatas: Sequence[sa.MetaData] = (
        (metadata,) if isinstance(metadata, sa.MetaData) else metadata
    )
    names: set[tuple[str | None, str]] = set()
    for single in metadatas:
        for table in single.tables.values():
            for constraint in table.constraints:
                if (
                    isinstance(constraint, sa.CheckConstraint)
                    and getattr(constraint, "_type_bound", False)
                    and isinstance(constraint.name, str)
                ):
                    names.add((table.name, constraint.name))
    return names


__all__: list[str] = [
    "OLD_TYPE_SUFFIX",
    "SQLITE_DIALECT",
    "CreateEnumCheckOp",
    "DropEnumCheckOp",
    "DropEnumTypeOp",
    "EnumColumnRef",
    "EnumTypeState",
    "RenameEnumTypeOp",
    "ReplaceEnumOp",
    "drop_enum_types_on_downgrade",
    "postgresql_replace_statements",
    "render_enum_types",
    "sqlite_enum_checks",
    "sync_enum_types",
    "type_bound_check_include_object",
]
