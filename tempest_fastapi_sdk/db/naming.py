"""Find the constraints a database still names by the legacy convention.

Until the composite fix, :data:`~tempest_fastapi_sdk.db.model.NAMING_CONVENTION`
named a unique constraint, an index and a foreign key after their
**first** column only. A database created under that convention holds
``uq_books_title`` for ``UniqueConstraint("title", "release_year")``; the
metadata now says ``uq_books_title_release_year``. Single-column names
did not change, so only composite constraints drift.

Three things about that drift were measured against PostgreSQL 16 and
Alembic 1.19.1 rather than assumed, and they are why this module exists
instead of a paragraph telling the reader to write the migration:

* ``alembic revision --autogenerate`` sees the unique constraints and
  the indexes, and renders each as a drop followed by a create.
* It does **not** see the foreign keys: a composite foreign key whose
  convention name changed produces no diff at all, and keeps its old
  name in the database — the name ``parse_integrity_error`` then
  reports.
* The drop-and-create it renders fails on PostgreSQL when a foreign key
  references the unique constraint being dropped
  (``cannot drop constraint ... because other objects depend on it``),
  and where it succeeds it rebuilds the index behind the constraint.
  ``ALTER TABLE ... RENAME CONSTRAINT`` changes only the name, keeps the
  dependents, and renames the backing index with it.

:func:`legacy_constraint_renames` walks a ``MetaData`` and returns one
:class:`ConstraintRename` per convention-named composite constraint whose
legacy name differs, foreign keys included; :meth:`ConstraintRename.statement`
renders the PostgreSQL ``RENAME`` for it. Names are computed by
SQLAlchemy itself, through a scratch table under each convention, so
the result is exactly the name the DDL would have carried — including
the truncation PostgreSQL's 63-character limit imposes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Table,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.schema import conv

from tempest_fastapi_sdk.core.enums import BaseStrEnum

LEGACY_NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""The naming convention ``BaseModel.metadata`` used before the composite fix.

Every template that names a constraint after its columns read only the
first one, so two composite constraints sharing a first column shared a
name. Kept as the default ``legacy`` of :func:`legacy_constraint_renames`,
and for a project that builds its own ``MetaData`` and needs the old
names on purpose.
"""


class ConstraintKind(BaseStrEnum):
    """Which kind of schema object a :class:`ConstraintRename` renames.

    Attributes:
        UNIQUE: A ``UNIQUE`` constraint.
        INDEX: A (non-constraint) index.
        FOREIGN_KEY: A ``FOREIGN KEY`` constraint.
    """

    UNIQUE = "unique"
    INDEX = "index"
    FOREIGN_KEY = "foreign_key"


@dataclass(frozen=True, slots=True)
class ConstraintRename:
    """One constraint whose convention name changed.

    ``old_name`` and ``new_name`` are the names the convention produces
    before any dialect shortens them; :meth:`statement` applies the
    dialect's identifier limit, the way the DDL did.

    Attributes:
        kind (ConstraintKind): What is being renamed.
        table (str): The table the constraint belongs to.
        schema (str | None): The table's schema, or ``None`` for the
            default one.
        columns (tuple[str, ...]): The constrained columns, in order.
        old_name (str): The name under the legacy convention — what a
            database created before the fix holds.
        new_name (str): The name under the current convention — what the
            metadata declares now.
    """

    kind: ConstraintKind
    table: str
    schema: str | None
    columns: tuple[str, ...]
    old_name: str
    new_name: str

    def inverse(self) -> ConstraintRename:
        """Return the rename that undoes this one, for a ``downgrade()``.

        Returns:
            ConstraintRename: The same constraint with the two names
            swapped.
        """
        return ConstraintRename(
            kind=self.kind,
            table=self.table,
            schema=self.schema,
            columns=self.columns,
            old_name=self.new_name,
            new_name=self.old_name,
        )

    def statement(self, dialect: Dialect) -> str:
        """Render the SQL that renames the constraint in place.

        Unique and foreign-key constraints use ``ALTER TABLE ... RENAME
        CONSTRAINT``, which on PostgreSQL also renames the index behind a
        unique constraint; a plain index uses ``ALTER INDEX ... RENAME
        TO``. Both names go through the dialect's identifier preparer, so
        a name past the 63-character limit is shortened and quoted
        exactly as ``CREATE`` shortened it.

        Only PostgreSQL is supported. SQLite cannot rename a constraint
        at all — its name lives inside the ``CREATE TABLE`` text — so
        the Alembic batch migration ``--autogenerate`` renders, which
        rebuilds the table, is the path there.

        Args:
            dialect (Dialect): The target dialect, such as
                ``op.get_bind().dialect`` inside a migration or
                ``sqlalchemy.dialects.postgresql.dialect()`` in a script.

        Returns:
            str: One SQL statement.

        Raises:
            ValueError: When ``dialect`` is not PostgreSQL.
        """
        if dialect.name != "postgresql":
            raise ValueError(
                f"ConstraintRename.statement supports postgresql, not "
                f"{dialect.name!r}; SQLite cannot rename a constraint, so "
                "use the batch migration alembic --autogenerate renders",
            )
        preparer = dialect.identifier_preparer
        prefix = f"{preparer.quote_schema(self.schema)}." if self.schema else ""
        if self.kind is ConstraintKind.INDEX:
            old = preparer.truncate_and_render_index_name(conv(self.old_name))
            new = preparer.truncate_and_render_index_name(conv(self.new_name))
            return f"ALTER INDEX {prefix}{old} RENAME TO {new}"
        old = preparer.truncate_and_render_constraint_name(conv(self.old_name))
        new = preparer.truncate_and_render_constraint_name(conv(self.new_name))
        table = f"{prefix}{preparer.quote(self.table)}"
        return f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}"


def _kind_of(item: Any) -> ConstraintKind | None:
    """Classify a table-level schema item.

    Args:
        item (Any): A constraint or index attached to a table.

    Returns:
        ConstraintKind | None: The kind, or ``None`` for a primary key or
        a check constraint, whose templates do not name columns.
    """
    if isinstance(item, UniqueConstraint):
        return ConstraintKind.UNIQUE
    if isinstance(item, ForeignKeyConstraint):
        return ConstraintKind.FOREIGN_KEY
    if isinstance(item, Index):
        return ConstraintKind.INDEX
    return None


def _column_names(item: UniqueConstraint | ForeignKeyConstraint | Index) -> list[str]:
    """Return the names of the columns a constraint or index covers.

    Args:
        item (UniqueConstraint | ForeignKeyConstraint | Index): The item.

    Returns:
        list[str]: Column names in declaration order. A foreign key
        lists its local columns; an expression index lists the columns
        its expressions reference, which is what the convention reads.
    """
    if isinstance(item, ForeignKeyConstraint):
        return [element.parent.name for element in item.elements]
    return [column.name for column in item.columns]


def _convention_name(
    item: UniqueConstraint | ForeignKeyConstraint | Index,
    table: Table,
    convention: Mapping[Any, Any],
) -> str | None:
    """Compute the name ``convention`` gives ``item``, through SQLAlchemy.

    Builds a scratch table with the same name, schema and columns under
    a ``MetaData`` carrying ``convention``, attaches an unnamed copy of
    the item, and reads back the name SQLAlchemy assigned. Reading the
    name this way keeps every token — ``column_0_label`` with a schema
    prefix, ``referred_table_name`` — exactly as SQLAlchemy expands it.

    Args:
        item (UniqueConstraint | ForeignKeyConstraint | Index): The item
            to name.
        table (Table): The table ``item`` belongs to.
        convention (Mapping[Any, Any]): The naming convention to apply.

    Returns:
        str | None: The convention name, or ``None`` when ``convention``
        has no template for this kind of item.
    """
    names = _column_names(item)
    scratch = Table(
        table.name,
        MetaData(naming_convention=dict(convention)),
        *[Column(name, table.c[name].type) for name in dict.fromkeys(names)],
        schema=table.schema,
    )
    probe: UniqueConstraint | ForeignKeyConstraint | Index
    if isinstance(item, ForeignKeyConstraint):
        probe = ForeignKeyConstraint(
            names,
            [element.target_fullname for element in item.elements],
        )
        scratch.append_constraint(probe)
    elif isinstance(item, UniqueConstraint):
        probe = UniqueConstraint(*names)
        scratch.append_constraint(probe)
    else:
        probe = Index(None, *[scratch.c[name] for name in names])
    name = probe.name
    return str(name) if isinstance(name, str) else None


def legacy_constraint_renames(
    metadata: MetaData,
    *,
    legacy: Mapping[str, str] = LEGACY_NAMING_CONVENTION,
) -> list[ConstraintRename]:
    """List the constraints whose convention name changed since ``legacy``.

    Example:

        >>> from sqlalchemy.dialects import postgresql
        >>> from tempest_fastapi_sdk import BaseModel, legacy_constraint_renames
        >>> for rename in legacy_constraint_renames(BaseModel.metadata):
        ...     print(rename.statement(postgresql.dialect()))

    A constraint is listed when it covers more than one column, its
    current name is the one ``metadata``'s convention produces (a
    constraint with an explicit ``name=`` is left alone), and the legacy
    convention names it differently. Primary keys and check constraints
    are never listed: their templates did not change.

    Two entries can share an ``old_name``: that is the legacy collision
    itself, and PostgreSQL refused the second ``CREATE``, so a database
    holds at most one of them. Keep the entry whose columns match the
    constraint the database actually has.

    Run it once, while writing the migration, and paste the statements
    into the revision. A migration that calls it at upgrade time would
    compute its renames from whatever the models look like on the day it
    runs, not on the day it was written.

    Args:
        metadata (MetaData): The metadata to inspect, normally
            ``BaseModel.metadata`` with every model imported.
        legacy (Mapping[str, str]): The convention the database was
            created under. Defaults to :data:`LEGACY_NAMING_CONVENTION`.

    Returns:
        list[ConstraintRename]: One entry per renamed constraint, sorted
        by schema, table, kind and new name — empty when nothing drifts,
        which is also what a metadata with no composite constraint gives.
    """
    renames: list[ConstraintRename] = []
    for table in metadata.tables.values():
        items: list[Any] = [*table.constraints, *table.indexes]
        for item in items:
            kind = _kind_of(item)
            if kind is None:
                continue
            columns = _column_names(item)
            if len(columns) < 2:
                continue
            current = _convention_name(item, table, metadata.naming_convention)
            if current is None or item.name != current:
                continue
            old = _convention_name(item, table, legacy)
            if old is None or old == current:
                continue
            renames.append(
                ConstraintRename(
                    kind=kind,
                    table=table.name,
                    schema=table.schema,
                    columns=tuple(columns),
                    old_name=old,
                    new_name=current,
                ),
            )
    return sorted(
        renames,
        key=lambda rename: (
            rename.schema or "",
            rename.table,
            rename.kind.value,
            rename.new_name,
        ),
    )


__all__: list[str] = [
    "LEGACY_NAMING_CONVENTION",
    "ConstraintKind",
    "ConstraintRename",
    "legacy_constraint_renames",
]
