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
  columns separately, in its ``DETAIL`` field, so a composite unique
  yields both names.
* SQLite names ``table.column`` pairs and **no** constraint, except for
  a foreign key, where it names nothing at all.
* Postgres quotes identifiers with ``"``; SQLite does not quote at all,
  so a pattern hunting for quoted text finds the *value* on one dialect
  and the *column* on the other.

On Postgres the parser reads the server's **structured** diagnostics
first — ``sqlstate``, ``detail``, ``constraint_name``, ``table_name`` and
``column_name``, the fields ``asyncpg`` exposes on its
``PostgresError`` — and falls back to the sentence only for what they
leave empty. The text is not a stable carrier: measured against
Postgres 16 with ``asyncpg`` 0.31.0, ``str(error.orig)`` carries the
``DETAIL:`` line under SQLAlchemy 2.0.52 and drops it under 2.1.1, whose
emulated DBAPI exception keeps only the first sentence and holds the
driver's own exception in ``orig``. The fields are read by attribute
name, so ``asyncpg`` is never imported here and an installation without
``[postgres]`` loses nothing.

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
* **The Postgres text alone names no table for a unique violation.**
  The sentence is about the constraint (``violates unique constraint
  "users_email_key"``) and the ``DETAIL`` is about the columns; neither
  says ``users``. The structured ``table_name`` does, so an error that
  still carries the driver exception reports the table. A bare message
  (a captured string, an exception whose chain holds no driver error)
  leaves :attr:`IntegrityFailure.table` ``None`` rather than splitting
  the constraint name on a convention a hand-written DDL need not
  follow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy.exc import DBAPIError, IntegrityError

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

_PG_SQLSTATE_VIOLATIONS: Final[dict[str, IntegrityViolation]] = {
    "23505": IntegrityViolation.UNIQUE,
    "23503": IntegrityViolation.FOREIGN_KEY,
    "23502": IntegrityViolation.NOT_NULL,
    "23514": IntegrityViolation.CHECK,
}
"""Postgres ``SQLSTATE`` codes of the four integrity violations.

Ported from the PostgreSQL manual's *Appendix A. PostgreSQL Error Codes*
(class 23: ``unique_violation``, ``foreign_key_violation``,
``not_null_violation``, ``check_violation``). The code does not depend
on ``lc_messages``, so it classifies a violation whose sentence the
server translated and no pattern below matches. Measured on Postgres 16
with ``lc_messages=de_DE.utf8``, under SQLAlchemy 2.0.52 and 2.1.1: a
unique comes back ``UNIQUE`` with its constraint and table, and with no
columns, because the ``DETAIL`` is translated too.
"""

_MAX_CHAIN: Final[int] = 8
"""How many exceptions :func:`_exception_chain` follows before stopping."""

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


@dataclass(frozen=True, slots=True)
class _ServerFields:
    """The structured diagnostics a Postgres driver exposes on its error.

    Attributes:
        sqlstate (str | None): The five-character ``SQLSTATE`` code.
        detail (str | None): The ``DETAIL`` field, which lists the key
            columns of a unique or foreign-key violation.
        constraint (str | None): The ``constraint_name`` field.
        table (str | None): The ``table_name`` field.
        column (str | None): The ``column_name`` field, which Postgres
            fills for a not-null violation.
    """

    sqlstate: str | None = None
    detail: str | None = None
    constraint: str | None = None
    table: str | None = None
    column: str | None = None


_SERVER_ATTRIBUTES: Final[tuple[tuple[str, str], ...]] = (
    ("sqlstate", "sqlstate"),
    ("detail", "detail"),
    ("constraint", "constraint_name"),
    ("table", "table_name"),
    ("column", "column_name"),
)
"""``_ServerFields`` field paired with the driver attribute it reads."""


def _exception_chain(error: BaseException) -> list[BaseException]:
    """Return ``error`` and the driver exceptions it wraps, outermost first.

    Follows ``orig`` when an object holds an exception there — the
    SQLAlchemy ``DBAPIError`` and, measured on SQLAlchemy 2.1.1, its
    emulated ``asyncpg`` DBAPI exception — and ``__cause__`` otherwise,
    which is where SQLAlchemy 2.0.52's adapter leaves the ``asyncpg``
    error. Bounded and cycle-safe, because the chain is built by code
    this SDK does not own.

    Args:
        error (BaseException): The outermost exception.

    Returns:
        list[BaseException]: Every exception reached, without repeats.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if len(chain) >= _MAX_CHAIN:
            break
        seen.add(id(current))
        chain.append(current)
        wrapped: Any = getattr(current, "orig", None)
        current = wrapped if isinstance(wrapped, BaseException) else current.__cause__
    return chain


def _server_fields(error: BaseException) -> _ServerFields | None:
    """Collect the structured diagnostics anywhere in the error's chain.

    Each field comes from the outermost exception that carries it as a
    non-empty ``str``. Measured on SQLAlchemy 2.1.1, the emulated
    exception copies ``detail`` and ``sqlstate`` but not
    ``constraint_name`` or ``table_name``, which only the ``asyncpg``
    exception it wraps has. Reading by attribute name is what keeps
    ``asyncpg`` out of this module's imports.

    Args:
        error (BaseException): The error to read.

    Returns:
        _ServerFields | None: The fields found, or ``None`` when no
        exception in the chain carries any — SQLite, or a bare message.
    """
    found: dict[str, str] = {}
    for source in _exception_chain(error):
        for key, attribute in _SERVER_ATTRIBUTES:
            value: Any = getattr(source, attribute, None)
            if key not in found and isinstance(value, str) and value:
                found[key] = value
    if not found:
        return None
    return _ServerFields(**found)


def _pg_key_columns(text: str) -> tuple[str, ...]:
    """Read the column list out of a Postgres ``Key (a, b)=(...)`` detail.

    Args:
        text (str): The ``DETAIL`` field, or a whole message holding a
            ``DETAIL:`` line.

    Returns:
        tuple[str, ...]: The columns in the order listed, or an empty
        tuple when the text carries no key.
    """
    detail = _PG_DETAIL_KEY.search(text)
    if detail is None:
        return ()
    return tuple(part.strip() for part in detail.group(1).split(","))


def _merge_server_fields(
    parsed: IntegrityFailure,
    server: _ServerFields,
) -> IntegrityFailure:
    """Overlay the structured diagnostics on what the text yielded.

    A structured field wins over the parsed one and the text fills only
    what the server left out, so a captured message with no driver
    exception behind it parses exactly as it did before.

    Args:
        parsed (IntegrityFailure): The result of parsing the message.
        server (_ServerFields): The structured diagnostics.

    Returns:
        IntegrityFailure: The merged result.
    """
    kind = _PG_SQLSTATE_VIOLATIONS.get(server.sqlstate or "", parsed.kind)
    columns = _pg_key_columns(server.detail) if server.detail else ()
    if not columns and kind is IntegrityViolation.NOT_NULL and server.column:
        columns = (server.column,)
    return IntegrityFailure(
        kind=kind,
        constraint=server.constraint or parsed.constraint,
        table=server.table or parsed.table,
        columns=columns or parsed.columns,
        message=parsed.message,
    )


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
    parsed = _parse_message(_driver_message(error))
    server = _server_fields(error)
    if server is None:
        return parsed
    return _merge_server_fields(parsed, server)


def _parse_message(message: str) -> IntegrityFailure:
    """Parse a driver message with the dialect patterns alone.

    Args:
        message (str): The driver message, SQL echo already stripped.

    Returns:
        IntegrityFailure: What the text says; ``UNKNOWN`` when no
        pattern matches.
    """

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
            return IntegrityFailure(
                kind=kind,
                constraint=match.group(1),
                table=table_match.group(1) if table_match else None,
                columns=_pg_key_columns(message),
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


WITHHELD_NOTICE: Final[str] = "database message withheld from the log"
"""Suffix of every redacted database line.

Says outright that the server's text was dropped, so an operator reading
the traceback does not mistake the summary for the whole message.
"""


def dotted_type_name(error: BaseException) -> str:
    """Return the name the traceback module would print for ``error``.

    Args:
        error (BaseException): The exception to name.

    Returns:
        str: ``module.QualName``, or the bare qualified name for a
        builtin.
    """
    kind = type(error)
    if kind.__module__ in ("builtins", "__main__"):
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def describe_database_error(error: DBAPIError) -> str:
    """Summarize a database error without the server's text.

    What the SDK logs in place of ``str(error)``: the 5xx handlers through
    :func:`~tempest_fastapi_sdk.redact_database_errors`, and
    :class:`~tempest_fastapi_sdk.BaseRepository` when it turns an
    ``IntegrityError`` into a ``409``. Postgres's text quotes the row —
    ``DETAIL:  Key (cpf)=(123.456.789-00) already exists.`` — so the value
    the client sent would reach ``warning.log`` on every duplicate.

    An integrity error keeps what :func:`parse_integrity_error` reads out
    of it — the kind, the constraint, the table and the columns, which
    are schema names — and drops the ``DETAIL`` line, which is data.
    Every other ``DBAPIError`` keeps only the driver's exception type:
    a ``DataError`` quotes the rejected input too (``invalid input for
    query argument $1: 'abc'``), so no server text is safe by category.

    The SQL statement goes too. What the ORM emits carries placeholders,
    but a statement built with ``text()`` and an f-string carries the
    literal, and the constraint plus the frames already say which write
    failed.

    Args:
        error (DBAPIError): The error to summarize.

    Returns:
        str: One line naming what failed and stating the omission.
    """
    parts: list[str] = []
    if isinstance(error, IntegrityError):
        failure = parse_integrity_error(error)
        if failure.kind is not IntegrityViolation.UNKNOWN:
            parts.append(f"{failure.kind.value} violation")
        if failure.constraint:
            parts.append(f"constraint={failure.constraint}")
        if failure.table:
            parts.append(f"table={failure.table}")
        if failure.columns:
            parts.append(f"columns={','.join(failure.columns)}")
    if error.orig is not None:
        parts.append(f"driver={dotted_type_name(error.orig)}")
    parts.append(WITHHELD_NOTICE)
    return "; ".join(parts)


__all__: list[str] = [
    "WITHHELD_NOTICE",
    "IntegrityFailure",
    "IntegrityViolation",
    "describe_database_error",
    "dotted_type_name",
    "parse_integrity_error",
]
