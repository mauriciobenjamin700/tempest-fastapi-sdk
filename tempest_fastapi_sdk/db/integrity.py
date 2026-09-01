"""Read an ``IntegrityError`` back into the constraint that rejected it.

A database says *why* it refused a write, and says it in prose whose
shape is the driver's, not the application's. Every service that wanted
to answer ``409 {"code": "EMAIL_TAKEN", "field": "email"}`` instead of a
generic conflict was reaching into that prose with a regular expression
of its own — usually one, usually written against whichever dialect the
author had running at the time.

The two dialects this SDK supports say the same five things five
different ways, and the differences are not cosmetic:

* Postgres names the constraint (``users_email_key``) and lists the
  columns separately, in a ``DETAIL:`` line, so a composite unique
  yields both names.
* SQLite names ``table.column`` pairs and **no** constraint, except for
  a foreign key, where it names nothing at all.
* Postgres quotes identifiers with ``"``; SQLite does not quote at all,
  so a pattern hunting for quoted text finds the *value* on one dialect
  and the *column* on the other.

Every pattern below was read off a real error from a real server —
Postgres 16 in a container, SQLite through ``aiosqlite`` — not from
documentation. The fixtures in ``tests/db/test_integrity.py`` are those
captured strings verbatim, and
``tests/db/test_integrity_live.py`` (marked ``docker``) reproduces them
against a live server so a driver that changes its wording fails here
rather than in a consumer's error handler.

Known limits, both measured rather than assumed:

* **SQLite foreign keys carry no detail.** The message is exactly
  ``FOREIGN KEY constraint failed`` — no table, no column, no
  constraint name. :attr:`IntegrityFailure.kind` is still
  ``FOREIGN_KEY``; everything else is empty. There is nothing to parse,
  so a caller that needs the column has to know it from the statement.
* **An unnamed SQLite CHECK reports its expression**, not a name:
  ``CHECK constraint failed: age >= 18``. Naming the constraint in the
  DDL makes SQLite report the name instead, and SQLite reports no table
  for a ``CHECK`` either way.
* **A Postgres unique violation names no table.** The sentence is about
  the constraint (``violates unique constraint "users_email_key"``) and
  the ``DETAIL:`` line is about the columns; neither says ``users``.
  The constraint name usually starts with the table by convention, and
  splitting on that convention is a guess — a constraint named in the
  DDL need not follow it — so :attr:`IntegrityFailure.table` stays
  ``None`` rather than carrying one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class IntegrityViolation(BaseStrEnum):
    """Which kind of constraint refused the write.

    Attributes:
        UNIQUE: A unique index or constraint already holds that value.
        FOREIGN_KEY: The referenced row does not exist.
        NOT_NULL: A column that forbids ``NULL`` received one.
        CHECK: A ``CHECK`` expression evaluated false.
        UNKNOWN: The message matched no known pattern.
    """

    UNIQUE = "unique"
    FOREIGN_KEY = "foreign_key"
    NOT_NULL = "not_null"
    CHECK = "check"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IntegrityFailure:
    """What a database said about the constraint it enforced.

    Every field except :attr:`kind` is best-effort: which of them the
    server fills in depends on the dialect and on the constraint, and
    the module docstring lists where each one is simply absent. Treat an
    empty value as "this server did not say", never as "there is none".

    Attributes:
        kind (IntegrityViolation): The constraint category.
        constraint (str | None): The constraint name, when the server
            named one. Postgres always does; SQLite only for ``CHECK``,
            and then only when the DDL named it.
        table (str | None): The table, when the message identifies one.
        columns (tuple[str, ...]): The columns involved, in the order
            the server listed them. A composite unique yields more than
            one.
        message (str): The driver's own message, with the echoed SQL
            statement stripped — useful for logging the thing that was
            parsed when :attr:`kind` came back ``UNKNOWN``.
    """

    kind: IntegrityViolation = IntegrityViolation.UNKNOWN
    constraint: str | None = None
    table: str | None = None
    columns: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""

    @property
    def column(self) -> str | None:
        """Return the single column involved, when there is exactly one.

        The common case — a unique email, a not-null name — is one
        column, and a caller answering ``{"field": ...}`` wants it
        without unpacking a tuple. ``None`` for a composite constraint
        is deliberate: naming only the first would be a guess about
        which half the user got wrong.

        Returns:
            str | None: The column name, or ``None`` when the server
            named zero or more than one.
        """
        return self.columns[0] if len(self.columns) == 1 else None


_SQL_ECHO: re.Pattern[str] = re.compile(r"\n\[SQL:", re.MULTILINE)

_PG_UNIQUE: re.Pattern[str] = re.compile(
    r'duplicate key value violates unique constraint "([^"]+)"',
)
_PG_FOREIGN_KEY: re.Pattern[str] = re.compile(
    r'violates foreign key constraint "([^"]+)"',
)
_PG_CHECK: re.Pattern[str] = re.compile(
    r'violates check constraint "([^"]+)"',
)
_PG_NOT_NULL: re.Pattern[str] = re.compile(
    r'null value in column "([^"]+)" of relation "([^"]+)" '
    r"violates not-null constraint",
)
_PG_TABLE: re.Pattern[str] = re.compile(
    r'(?:on table|for relation|of relation) "([^"]+)"',
)
_PG_DETAIL_KEY: re.Pattern[str] = re.compile(r"Key \(([^)]+)\)=")

_SQLITE_UNIQUE: re.Pattern[str] = re.compile(
    r"UNIQUE constraint failed: (.+)",
)
_SQLITE_NOT_NULL: re.Pattern[str] = re.compile(
    r"NOT NULL constraint failed: (.+)",
)
_SQLITE_CHECK: re.Pattern[str] = re.compile(
    r"CHECK constraint failed: (.+)",
)
_SQLITE_FOREIGN_KEY: re.Pattern[str] = re.compile(
    r"FOREIGN KEY constraint failed",
)


def _driver_message(error: BaseException) -> str:
    """Return the driver's message without SQLAlchemy's SQL echo.

    ``IntegrityError.orig`` is the DBAPI exception, whose ``str`` is the
    server's sentence alone. ``str(error)`` on the SQLAlchemy wrapper
    appends ``[SQL: <the statement>]``, and a statement can contain any
    text at all — including the words these patterns hunt for, which is
    how a parser ends up reading the row's own data as a column name.

    Args:
        error (BaseException): The error to read.

    Returns:
        str: The driver message, or the wrapper's text truncated before
        the echoed statement when there is no ``orig``.
    """
    orig: Any = getattr(error, "orig", None)
    if orig is not None:
        return str(orig)
    return _SQL_ECHO.split(str(error), maxsplit=1)[0]


def _sqlite_columns(raw: str) -> tuple[str | None, tuple[str, ...]]:
    """Split SQLite's ``table.column, table.column`` list.

    Args:
        raw (str): The text after the colon in a SQLite constraint
            message.

    Returns:
        tuple[str | None, tuple[str, ...]]: The table (from the first
        pair, or ``None`` when the entries carry no table prefix) and
        every column name in the order listed.
    """
    table: str | None = None
    columns: list[str] = []
    for item in raw.split(","):
        entry = item.strip()
        if not entry:
            continue
        head, separator, tail = entry.rpartition(".")
        if separator and table is None:
            table = head
        columns.append(tail if separator else entry)
    return table, tuple(columns)


def parse_integrity_error(error: BaseException) -> IntegrityFailure:
    """Read a database integrity error into its constituent parts.

    Example:

        >>> from sqlalchemy.exc import IntegrityError
        >>> from tempest_fastapi_sdk import parse_integrity_error
        >>>
        >>> def to_conflict(error: IntegrityError) -> dict[str, object]:
        ...     failure = parse_integrity_error(error)
        ...     return {"code": failure.kind.value, "field": failure.column}

    Never raises: an unrecognized message comes back as
    :attr:`IntegrityViolation.UNKNOWN` with the text in
    :attr:`IntegrityFailure.message`. A parser for error prose that
    raises on prose it does not know turns a handled ``409`` into an
    unhandled ``500``, which is worse than the generic conflict it was
    added to improve on.

    Args:
        error (BaseException): The error to read. Normally a
            ``sqlalchemy.exc.IntegrityError``; any exception whose text
            carries a supported message works, which is what lets a
            caller pass a driver exception directly.

    Returns:
        IntegrityFailure: What the server said. Fields the dialect does
        not report are ``None`` or empty — see the module docstring for
        which those are.
    """
    message = _driver_message(error)

    match = _PG_NOT_NULL.search(message)
    if match:
        return IntegrityFailure(
            kind=IntegrityViolation.NOT_NULL,
            table=match.group(2),
            columns=(match.group(1),),
            message=message,
        )

    for pattern, kind in (
        (_PG_UNIQUE, IntegrityViolation.UNIQUE),
        (_PG_FOREIGN_KEY, IntegrityViolation.FOREIGN_KEY),
        (_PG_CHECK, IntegrityViolation.CHECK),
    ):
        match = pattern.search(message)
        if match:
            table_match = _PG_TABLE.search(message)
            detail = _PG_DETAIL_KEY.search(message)
            columns = (
                tuple(part.strip() for part in detail.group(1).split(","))
                if detail
                else ()
            )
            return IntegrityFailure(
                kind=kind,
                constraint=match.group(1),
                table=table_match.group(1) if table_match else None,
                columns=columns,
                message=message,
            )

    for pattern, kind in (
        (_SQLITE_UNIQUE, IntegrityViolation.UNIQUE),
        (_SQLITE_NOT_NULL, IntegrityViolation.NOT_NULL),
    ):
        match = pattern.search(message)
        if match:
            table, columns = _sqlite_columns(match.group(1))
            return IntegrityFailure(
                kind=kind,
                table=table,
                columns=columns,
                message=message,
            )

    match = _SQLITE_CHECK.search(message)
    if match:
        return IntegrityFailure(
            kind=IntegrityViolation.CHECK,
            constraint=match.group(1).strip(),
            message=message,
        )

    if _SQLITE_FOREIGN_KEY.search(message):
        return IntegrityFailure(
            kind=IntegrityViolation.FOREIGN_KEY,
            message=message,
        )

    return IntegrityFailure(message=message)


__all__: list[str] = [
    "IntegrityFailure",
    "IntegrityViolation",
    "parse_integrity_error",
]
