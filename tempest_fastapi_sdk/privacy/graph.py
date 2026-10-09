"""Data-subject graph derived from SQLAlchemy metadata.

A service that stores personal data must be able to *export* everything it
holds about a data subject and *erase* it (LGPD art. 18, GDPR art. 15/17).
Erasure is cheapest when the database does it: every table that holds the
subject's rows declares a foreign key with ``ON DELETE CASCADE`` toward the
subject's root row, so deleting that one row removes the rest.

:class:`SubjectGraph` reads that design back from ``MetaData``:

- :meth:`SubjectGraph.tables` is the cascade closure from the root — the
  tables a root delete reaches;
- :meth:`SubjectGraph.export` selects every row of the closure that belongs
  to one subject, with secret columns left out;
- :meth:`SubjectGraph.violations` lists the foreign keys that point into the
  closure without cascading, which is exactly what breaks erasure (the root
  delete fails, or leaves rows behind). A ``SET NULL`` key is accepted only
  when it is listed in ``retained`` with the reason the data outlives the
  subject.

A table added later with a cascading foreign key toward the root joins the
closure, and therefore the export, without configuration.
"""

from __future__ import annotations

import base64
import enum
import uuid
from collections.abc import Collection, Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, MetaData, Table, false, func, or_, select
from sqlalchemy import tuple_ as sql_tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.schema import ForeignKeyConstraint

DEFAULT_SECRET_MARKERS: tuple[str, ...] = ("hash", "secret", "password")
"""Substrings that mark a column as secret by name (case-insensitive).

A column whose name contains any of them (``password_hash``,
``totp_secret``, ``api_key_hash``) is left out of :meth:`SubjectGraph.export`.
Mark a column explicitly with ``mapped_column(..., info={"secret": True})``
or list it in ``secret_columns`` when the name says nothing.
"""

SECRET_INFO_KEY: str = "secret"
"""Key of ``Column.info`` that marks a column as secret when truthy."""


def _jsonable(value: Any) -> Any:
    """Convert a column value to a JSON-serialisable value.

    Args:
        value (Any): A value read from the database.

    Returns:
        Any: ``datetime``/``date``/``time`` as ISO 8601 strings,
        ``timedelta`` as total seconds, ``UUID`` and ``Decimal`` as
        strings, ``bytes`` as base64, an ``Enum`` as its value, lists and
        dicts converted recursively, and anything else unchanged.
    """
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, enum.Enum):
        return _jsonable(value.value)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _ondelete(constraint: ForeignKeyConstraint) -> str:
    """Return the normalised ``ON DELETE`` action of a foreign key.

    Args:
        constraint (ForeignKeyConstraint): The foreign key.

    Returns:
        str: The action upper-cased (``"CASCADE"``, ``"SET NULL"``), or
        ``"NO ACTION"`` when the key declares none.
    """
    return (constraint.ondelete or "NO ACTION").upper()


def _fk_label(constraint: ForeignKeyConstraint) -> str:
    """Return ``table.col[,col]`` naming the local side of a foreign key.

    Args:
        constraint (ForeignKeyConstraint): The foreign key.

    Returns:
        str: The owning table and its local columns, comma-joined.
    """
    table = constraint.table
    columns = ",".join(column.name for column in constraint.columns)
    return f"{table.name}.{columns}"


class SubjectGraph:
    """Cascade closure of a data subject, read from SQLAlchemy metadata.

    Example:

        >>> graph = SubjectGraph(Base.metadata, root="users")
        >>> graph.tables()
        ['users', 'addresses', 'orders', 'order_items']
        >>> graph.violations()
        []
        >>> payload = await graph.export(session, user_id)
        >>> payload["orders"][0]["total"]
        '19.90'

    Attributes:
        metadata (MetaData): The metadata the graph was derived from.
        root (Table): The table whose primary key identifies the subject.
        retained (dict[str, str]): ``"table.column"`` of each ``SET NULL``
            foreign key accepted into the closure, mapped to the reason the
            row outlives the subject.
        secret_columns (dict[str, frozenset[str]]): Extra columns per table
            left out of the export.
        secret_markers (tuple[str, ...]): Name substrings that mark a column
            as secret.
    """

    def __init__(
        self,
        metadata: MetaData,
        *,
        root: str | Table,
        retained: Mapping[str, str] | None = None,
        secret_columns: Mapping[str, Collection[str]] | None = None,
        secret_markers: Collection[str] = DEFAULT_SECRET_MARKERS,
    ) -> None:
        """Derive the closure from ``metadata``.

        Args:
            metadata (MetaData): Metadata holding every table of the
                service (``Base.metadata``).
            root (str | Table): The subject's root table, by name or object.
                Its single-column primary key is the subject id.
            retained (Mapping[str, str] | None): Foreign keys that may point
                into the closure with ``ON DELETE SET NULL`` because the row
                is kept after erasure (an invoice the tax law requires, a
                message in a shared thread). Keys are ``"table.column"``
                (``"table.col_a,col_b"`` for a composite key), values are
                the reason. Every entry must name an existing nullable
                ``SET NULL`` key into the closure, or it is reported by
                :meth:`violations`.
            secret_columns (Mapping[str, Collection[str]] | None): Columns
                to leave out of the export per table name, beyond the ones
                caught by ``secret_markers`` and ``info={"secret": True}``.
            secret_markers (Collection[str]): Case-insensitive substrings of
                a column name that mark it as secret. Defaults to
                :data:`DEFAULT_SECRET_MARKERS`.

        Raises:
            KeyError: When ``root`` names a table missing from ``metadata``.
            ValueError: When the root table does not have exactly one
                primary-key column.
        """
        self.metadata: MetaData = metadata
        self.root: Table = metadata.tables[root] if isinstance(root, str) else root
        primary_key = list(self.root.primary_key.columns)
        if len(primary_key) != 1:
            raise ValueError(
                f"root table {self.root.name!r} must have exactly one "
                f"primary-key column, found {len(primary_key)}"
            )
        self.retained: dict[str, str] = dict(retained or {})
        self.secret_columns: dict[str, frozenset[str]] = {
            table: frozenset(columns)
            for table, columns in (secret_columns or {}).items()
        }
        self.secret_markers: tuple[str, ...] = tuple(
            marker.lower() for marker in secret_markers
        )
        self._order: list[Table] = self._closure()
        self._members: frozenset[str] = frozenset(t.name for t in self._order)

    def _closure(self) -> list[Table]:
        """Walk cascading foreign keys outward from the root.

        Returns:
            list[Table]: The root first, then each table in the order it was
            reached (breadth-first; ties broken by table name).
        """
        order: list[Table] = [self.root]
        seen: set[str] = {self.root.name}
        frontier: list[Table] = [self.root]
        while frontier:
            reached: list[Table] = []
            for table in sorted(self.metadata.tables.values(), key=lambda t: t.name):
                if table.name in seen:
                    continue
                if any(
                    constraint.referred_table.name in seen
                    and _ondelete(constraint) == "CASCADE"
                    for constraint in table.foreign_key_constraints
                ):
                    reached.append(table)
            for table in reached:
                seen.add(table.name)
                order.append(table)
            frontier = reached
        return order

    def tables(self) -> list[str]:
        """Return the tables a delete of the root row reaches.

        Returns:
            list[str]: Table names, root first, breadth-first from it.
        """
        return [table.name for table in self._order]

    def violations(self) -> list[str]:
        """List every foreign key that would break erasure of a subject.

        A foreign key whose target table is in the closure must cascade, or
        be a nullable ``SET NULL`` key listed in ``retained``. Anything else
        (``NO ACTION``, ``RESTRICT``, an unlisted ``SET NULL``) makes the
        root delete fail or leaves the subject's id behind. Stale
        ``retained`` entries are reported too, so the allowlist cannot rot.

        Returns:
            list[str]: One human-readable line per problem, sorted. Empty
            list when the schema supports erasure.
        """
        problems: list[str] = []
        matched: set[str] = set()
        for table in self.metadata.tables.values():
            for constraint in table.foreign_key_constraints:
                target = constraint.referred_table
                if target.name not in self._members:
                    continue
                action = _ondelete(constraint)
                label = _fk_label(constraint)
                arrow = f"{label} -> {target.name}"
                if action == "CASCADE":
                    continue
                if action == "SET NULL" and label in self.retained:
                    matched.add(label)
                    if not all(column.nullable for column in constraint.columns):
                        problems.append(
                            f"{arrow}: ON DELETE SET NULL on a NOT NULL column"
                        )
                    continue
                if label in self.retained:
                    matched.add(label)
                    problems.append(
                        f"{arrow}: listed in retained but ON DELETE is "
                        f"{action}, expected SET NULL"
                    )
                    continue
                problems.append(
                    f"{arrow}: ON DELETE {action} (expected CASCADE, or "
                    f"SET NULL listed in retained)"
                )
        for label in sorted(set(self.retained) - matched):
            problems.append(
                f"{label}: listed in retained but no foreign key from it "
                f"points into the subject closure"
            )
        return sorted(problems)

    def is_secret(self, table: Table, column_name: str) -> bool:
        """Tell whether a column is left out of the export.

        Args:
            table (Table): The table owning the column.
            column_name (str): The column name.

        Returns:
            bool: ``True`` when the column is listed in ``secret_columns``,
            carries ``info={"secret": True}``, or its name contains one of
            ``secret_markers``.
        """
        if column_name in self.secret_columns.get(table.name, frozenset()):
            return True
        column = table.columns[column_name]
        if column.info.get(SECRET_INFO_KEY):
            return True
        lowered = column_name.lower()
        return any(marker in lowered for marker in self.secret_markers)

    def exported_columns(self, table: str | Table) -> list[str]:
        """Return the columns of a closure table that the export includes.

        Args:
            table (str | Table): A table of the closure, by name or object.

        Returns:
            list[str]: Column names in declaration order, secrets removed.

        Raises:
            KeyError: When the table is not part of the closure.
        """
        resolved = self._member(table)
        return [
            column.name
            for column in resolved.columns
            if not self.is_secret(resolved, column.name)
        ]

    def _member(self, table: str | Table) -> Table:
        """Resolve a closure table by name or object.

        Args:
            table (str | Table): The table.

        Returns:
            Table: The table object.

        Raises:
            KeyError: When the table is not part of the closure.
        """
        name = table if isinstance(table, str) else table.name
        if name not in self._members:
            raise KeyError(f"table {name!r} is not in the subject closure")
        return self.metadata.tables[name]

    def condition(self, table: str | Table, subject_id: Any) -> ColumnElement[bool]:
        """Build the ``WHERE`` clause selecting one subject's rows of a table.

        The root matches on its primary key. Any other table matches when
        one of its cascading foreign keys points at a row of a closure table
        that itself belongs to the subject, as a nested ``IN (SELECT ...)``.
        A row reachable only through a cycle back to its own table (a reply
        to a reply, by another subject) is not considered the subject's.

        Args:
            table (str | Table): A table of the closure.
            subject_id (Any): The value of the root's primary key.

        Returns:
            ColumnElement[bool]: The clause, usable in ``select``,
            ``update`` or ``delete``.

        Raises:
            KeyError: When the table is not part of the closure.
        """
        return self._condition(self._member(table), subject_id, frozenset())

    def _condition(
        self,
        table: Table,
        subject_id: Any,
        visiting: frozenset[str],
    ) -> ColumnElement[bool]:
        """Recursive worker of :meth:`condition`.

        Args:
            table (Table): The table to build the clause for.
            subject_id (Any): The root primary-key value.
            visiting (frozenset[str]): Tables on the current path, skipped
                to stop recursion on cycles.

        Returns:
            ColumnElement[bool]: The clause; ``false()`` when no acyclic
            path reaches the root.
        """
        if table is self.root:
            primary_key = next(iter(self.root.primary_key.columns))
            matches_root: ColumnElement[bool] = primary_key == subject_id
            return matches_root
        path = visiting | {table.name}
        clauses: list[ColumnElement[bool]] = []
        for constraint in sorted(
            table.foreign_key_constraints, key=lambda c: _fk_label(c)
        ):
            parent = constraint.referred_table
            if (
                _ondelete(constraint) != "CASCADE"
                or parent.name not in self._members
                or parent.name in path
            ):
                continue
            local = list(constraint.columns)
            remote = [element.column for element in constraint.elements]
            parent_rows = select(*remote).where(
                self._condition(parent, subject_id, path)
            )
            if len(local) == 1:
                clauses.append(local[0].in_(parent_rows))
            else:
                clauses.append(sql_tuple(*local).in_(parent_rows))
        if not clauses:
            return false()
        return or_(*clauses)

    async def export(
        self,
        session: AsyncSession,
        subject_id: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        """Read every row of the closure that belongs to one subject.

        Args:
            session (AsyncSession): An open async session.
            subject_id (Any): The value of the root's primary key.

        Returns:
            dict[str, list[dict[str, Any]]]: Every closure table name (in
            :meth:`tables` order) mapped to its rows, each row a dict of the
            :meth:`exported_columns` with JSON-serialisable values. A table
            with no rows maps to an empty list; an unknown subject yields
            empty lists throughout.
        """
        payload: dict[str, list[dict[str, Any]]] = {}
        for table in self._order:
            columns = [table.columns[name] for name in self.exported_columns(table)]
            statement = select(*columns).where(
                self._condition(table, subject_id, frozenset())
            )
            result = await session.execute(statement)
            payload[table.name] = [
                {key: _jsonable(value) for key, value in row.items()}
                for row in result.mappings()
            ]
        return payload

    async def count(
        self,
        session: AsyncSession,
        subject_id: Any,
    ) -> dict[str, int]:
        """Count one subject's rows per closure table.

        Run it after deleting the root row to prove the erasure reached
        every table: every count must be ``0``.

        Args:
            session (AsyncSession): An open async session.
            subject_id (Any): The value of the root's primary key.

        Returns:
            dict[str, int]: Every closure table name mapped to its row
            count for the subject.
        """
        counts: dict[str, int] = {}
        for table in self._order:
            statement = (
                select(func.count())
                .select_from(table)
                .where(self._condition(table, subject_id, frozenset()))
            )
            counts[table.name] = int((await session.execute(statement)).scalar_one())
        return counts


__all__: list[str] = [
    "DEFAULT_SECRET_MARKERS",
    "SECRET_INFO_KEY",
    "SubjectGraph",
]
