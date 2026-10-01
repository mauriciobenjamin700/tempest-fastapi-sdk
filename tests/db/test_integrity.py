"""Tests for ``parse_integrity_error``.

Every fixture below is a **captured** message — the exact ``str`` of the
driver exception a real server produced, copied out of a probe run
against Postgres 16 in a container and SQLite through ``aiosqlite``, not
transcribed from documentation. ``test_integrity_live.py`` reproduces
them against live servers under the ``docker`` marker; this file is what
runs on every checkout, so a regex regression fails without a daemon.
"""

from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk import (
    IntegrityFailure,
    IntegrityViolation,
    parse_integrity_error,
)
from tempest_fastapi_sdk.db.integrity import _PG_SQLSTATE_VIOLATIONS

PG_UNIQUE = (
    "<class 'asyncpg.exceptions.UniqueViolationError'>: duplicate key "
    'value violates unique constraint "users_email_key"\n'
    "DETAIL:  Key (email)=(a@x.com) already exists."
)
PG_UNIQUE_COMPOSITE = (
    "<class 'asyncpg.exceptions.UniqueViolationError'>: duplicate key "
    'value violates unique constraint "users_name_pair_key"\n'
    "DETAIL:  Key (nickname, age)=(ann, 30) already exists."
)
PG_NOT_NULL = (
    "<class 'asyncpg.exceptions.NotNullViolationError'>: null value in "
    'column "email" of relation "users" violates not-null constraint\n'
    "DETAIL:  Failing row contains (4, null, dan, 20)."
)
PG_CHECK = (
    "<class 'asyncpg.exceptions.CheckViolationError'>: new row for "
    'relation "users" violates check constraint "users_age_check"\n'
    "DETAIL:  Failing row contains (5, e@x.com, eve, 5)."
)
PG_FOREIGN_KEY = (
    "<class 'asyncpg.exceptions.ForeignKeyViolationError'>: insert or "
    'update on table "orders" violates foreign key constraint '
    '"orders_user_id_fkey"\n'
    'DETAIL:  Key (user_id)=(9999) is not present in table "users".'
)

SQLITE_UNIQUE = "UNIQUE constraint failed: users.email"
SQLITE_UNIQUE_COMPOSITE = "UNIQUE constraint failed: users.nickname, users.age"
SQLITE_NOT_NULL = "NOT NULL constraint failed: users.email"
SQLITE_CHECK = "CHECK constraint failed: age >= 18"
SQLITE_FOREIGN_KEY = "FOREIGN KEY constraint failed"


def _error(message: str) -> IntegrityError:
    """Wrap a captured driver message the way SQLAlchemy would.

    Args:
        message (str): The driver's own message.

    Returns:
        IntegrityError: An error whose ``orig`` carries ``message``.
    """
    return IntegrityError("INSERT INTO t VALUES (1)", {}, Exception(message))


class TestPostgres:
    """Postgres names the constraint and lists columns in ``DETAIL:``."""

    def test_unique_single_column(self) -> None:
        failure = parse_integrity_error(_error(PG_UNIQUE))

        assert failure.kind is IntegrityViolation.UNIQUE
        assert failure.constraint == "users_email_key"
        assert failure.columns == ("email",)
        assert failure.column == "email"

    def test_unique_composite_reports_every_column(self) -> None:
        failure = parse_integrity_error(_error(PG_UNIQUE_COMPOSITE))

        assert failure.columns == ("nickname", "age")

    def test_unique_composite_has_no_single_column(self) -> None:
        """Naming only the first would guess which half the user got wrong."""
        assert parse_integrity_error(_error(PG_UNIQUE_COMPOSITE)).column is None

    def test_unique_text_alone_reports_no_table(self) -> None:
        """Measured absence: the sentence never names one.

        The constraint name usually starts with the table by convention,
        and splitting on a convention a hand-written DDL need not follow
        would be a guess. The structured ``table_name`` does name it —
        see :class:`TestStructuredDiagnostics`.
        """
        assert parse_integrity_error(_error(PG_UNIQUE)).table is None

    def test_not_null(self) -> None:
        failure = parse_integrity_error(_error(PG_NOT_NULL))

        assert failure.kind is IntegrityViolation.NOT_NULL
        assert failure.table == "users"
        assert failure.column == "email"

    def test_check(self) -> None:
        failure = parse_integrity_error(_error(PG_CHECK))

        assert failure.kind is IntegrityViolation.CHECK
        assert failure.constraint == "users_age_check"
        assert failure.table == "users"

    def test_foreign_key(self) -> None:
        failure = parse_integrity_error(_error(PG_FOREIGN_KEY))

        assert failure.kind is IntegrityViolation.FOREIGN_KEY
        assert failure.constraint == "orders_user_id_fkey"
        assert failure.table == "orders"
        assert failure.column == "user_id"


class TestSQLite:
    """SQLite names ``table.column`` pairs and, mostly, no constraint."""

    def test_unique_single_column(self) -> None:
        failure = parse_integrity_error(_error(SQLITE_UNIQUE))

        assert failure.kind is IntegrityViolation.UNIQUE
        assert failure.table == "users"
        assert failure.column == "email"

    def test_unique_composite_reports_every_column(self) -> None:
        failure = parse_integrity_error(_error(SQLITE_UNIQUE_COMPOSITE))

        assert failure.columns == ("nickname", "age")
        assert failure.table == "users"

    def test_not_null(self) -> None:
        failure = parse_integrity_error(_error(SQLITE_NOT_NULL))

        assert failure.kind is IntegrityViolation.NOT_NULL
        assert failure.column == "email"

    def test_unnamed_check_reports_its_expression(self) -> None:
        """A measured limit: SQLite has no name to give for an unnamed CHECK."""
        failure = parse_integrity_error(_error(SQLITE_CHECK))

        assert failure.kind is IntegrityViolation.CHECK
        assert failure.constraint == "age >= 18"

    def test_foreign_key_carries_nothing_but_the_kind(self) -> None:
        """The whole message is five words; there is nothing to parse."""
        failure = parse_integrity_error(_error(SQLITE_FOREIGN_KEY))

        assert failure.kind is IntegrityViolation.FOREIGN_KEY
        assert failure.constraint is None
        assert failure.table is None
        assert failure.columns == ()


class TestRobustness:
    """A parser for error prose must never make things worse."""

    def test_an_unknown_message_is_not_an_exception(self) -> None:
        """Raising here turns a handled 409 into an unhandled 500."""
        failure = parse_integrity_error(_error("something entirely new"))

        assert failure.kind is IntegrityViolation.UNKNOWN
        assert failure.message == "something entirely new"

    def test_the_echoed_statement_cannot_be_mistaken_for_a_message(self) -> None:
        """``str(error)`` appends ``[SQL: ...]``, and a row can say anything.

        Reading the wrapper's text instead of ``orig`` lets a value the
        user typed be parsed as a column name.
        """
        error = IntegrityError(
            "INSERT INTO t (note) VALUES ('UNIQUE constraint failed: evil.col')",
            {},
            Exception(SQLITE_NOT_NULL),
        )

        failure = parse_integrity_error(error)

        assert failure.kind is IntegrityViolation.NOT_NULL
        assert failure.column == "email"

    def test_a_bare_exception_still_parses(self) -> None:
        """Callers holding the driver error directly should not have to wrap it."""
        failure = parse_integrity_error(Exception(SQLITE_UNIQUE))

        assert failure.kind is IntegrityViolation.UNIQUE
        assert failure.column == "email"

    def test_the_default_failure_is_unknown(self) -> None:
        assert IntegrityFailure().kind is IntegrityViolation.UNKNOWN


class _DriverError(Exception):
    """Stand-in for an ``asyncpg`` error: the attributes, not the import.

    The parser reads the diagnostics by attribute name, so a plain
    exception carrying the same five attributes exercises the same path
    as ``asyncpg.exceptions.UniqueViolationError`` without the extra.
    """

    def __init__(
        self,
        message: str,
        *,
        sqlstate: str,
        detail: str | None = None,
        constraint_name: str | None = None,
        table_name: str | None = None,
        column_name: str | None = None,
    ) -> None:
        """Build the error.

        Args:
            message (str): The server's primary sentence.
            sqlstate (str): The ``SQLSTATE`` code.
            detail (str | None): The ``DETAIL`` field.
            constraint_name (str | None): The ``constraint_name`` field.
            table_name (str | None): The ``table_name`` field.
            column_name (str | None): The ``column_name`` field.
        """
        super().__init__(message)
        self.sqlstate: str = sqlstate
        self.detail: str | None = detail
        self.constraint_name: str | None = constraint_name
        self.table_name: str | None = table_name
        self.column_name: str | None = column_name


class _EmulatedError(Exception):
    """Shape of SQLAlchemy 2.1.1's emulated ``asyncpg`` DBAPI exception.

    Measured against Postgres 16: ``str()`` is the first sentence only,
    ``detail`` and ``sqlstate`` are copied, ``constraint_name`` and
    ``table_name`` are absent, and the driver error sits in ``orig``.
    """

    def __init__(self, driver: _DriverError) -> None:
        """Wrap ``driver`` the way SQLAlchemy 2.1.1 does.

        Args:
            driver (_DriverError): The driver exception.
        """
        super().__init__(str(driver))
        self.orig: _DriverError = driver
        self.detail: str | None = driver.detail
        self.sqlstate: str = driver.sqlstate


def _unique_driver(
    detail: str = "Key (email)=(a@x.com) already exists.",
) -> _DriverError:
    """Build the driver error for the captured single-column unique.

    Args:
        detail (str): The ``DETAIL`` field.

    Returns:
        _DriverError: The error, fields as Postgres 16 filled them.
    """
    return _DriverError(
        'duplicate key value violates unique constraint "users_email_key"',
        sqlstate="23505",
        detail=detail,
        constraint_name="users_email_key",
        table_name="users",
    )


def _sqlalchemy_21(driver: _DriverError) -> IntegrityError:
    """Wrap ``driver`` in the chain SQLAlchemy 2.1.1 builds.

    Args:
        driver (_DriverError): The driver exception.

    Returns:
        IntegrityError: The error a consumer's ``except`` receives.
    """
    emulated = _EmulatedError(driver)
    emulated.__cause__ = driver
    return IntegrityError("INSERT INTO users VALUES (1)", {}, emulated)


def _sqlalchemy_20(driver: _DriverError, text: str) -> IntegrityError:
    """Wrap ``driver`` in the chain SQLAlchemy 2.0.52 builds.

    Args:
        driver (_DriverError): The driver exception.
        text (str): The adapter's ``str``, which still has ``DETAIL:``.

    Returns:
        IntegrityError: The error a consumer's ``except`` receives.
    """
    adapter = Exception(text)
    adapter.__cause__ = driver
    return IntegrityError("INSERT INTO users VALUES (1)", {}, adapter)


class TestStructuredDiagnostics:
    """The driver's fields come first; the text only fills the gaps (#367)."""

    def test_sqlalchemy_21_unique_keeps_its_columns(self) -> None:
        """The defect: 2.1.1's ``str(orig)`` has no ``DETAIL:`` line."""
        error = _sqlalchemy_21(_unique_driver())

        assert "DETAIL" not in str(error.orig)
        failure = parse_integrity_error(error)

        assert failure.kind is IntegrityViolation.UNIQUE
        assert failure.constraint == "users_email_key"
        assert failure.columns == ("email",)
        assert failure.table == "users"

    def test_sqlalchemy_21_composite_unique_keeps_every_column(self) -> None:
        driver = _unique_driver("Key (nickname, age)=(ann, 30) already exists.")

        failure = parse_integrity_error(_sqlalchemy_21(driver))

        assert failure.columns == ("nickname", "age")

    def test_sqlalchemy_20_chain_reads_the_cause(self) -> None:
        """2.0.52 leaves the driver error in ``__cause__``, not ``orig``."""
        error = _sqlalchemy_20(_unique_driver(), PG_UNIQUE)

        failure = parse_integrity_error(error)

        assert failure.columns == ("email",)
        assert failure.table == "users"
        assert failure.message == PG_UNIQUE

    def test_a_driver_error_passed_directly_parses(self) -> None:
        assert parse_integrity_error(_unique_driver()).column == "email"

    def test_not_null_reads_column_name(self) -> None:
        """Postgres fills ``column_name`` for not-null; ``detail`` is the row."""
        driver = _DriverError(
            'null value in column "email" of relation "users" violates '
            "not-null constraint",
            sqlstate="23502",
            detail="Failing row contains (4, null, d, 20).",
            table_name="users",
            column_name="email",
        )

        failure = parse_integrity_error(_sqlalchemy_21(driver))

        assert failure.kind is IntegrityViolation.NOT_NULL
        assert failure.columns == ("email",)
        assert failure.table == "users"

    def test_a_translated_sentence_is_classified_by_sqlstate(self) -> None:
        """``lc_messages`` translates the sentence and the detail, not the code.

        Captured from Postgres 16 with ``lc_messages=de_DE.utf8``. The
        translated ``DETAIL`` matches no key pattern, so the columns stay
        empty — measured, and pinned so it is not mistaken for a defect.
        """
        driver = _DriverError(
            "doppelter Schlüsselwert verletzt Unique-Constraint »users_email_key«",
            sqlstate="23505",
            detail="Schlüssel »(email)=(a@x.com)« existiert bereits.",
            constraint_name="users_email_key",
            table_name="users",
        )

        failure = parse_integrity_error(_sqlalchemy_21(driver))

        assert failure.kind is IntegrityViolation.UNIQUE
        assert failure.constraint == "users_email_key"
        assert failure.table == "users"
        assert failure.columns == ()

    def test_a_cyclic_chain_terminates(self) -> None:
        driver = _unique_driver()
        emulated = _EmulatedError(driver)
        driver.__cause__ = emulated

        assert parse_integrity_error(emulated).column == "email"

    def test_sqlstate_table_is_pinned(self) -> None:
        """Ported from PostgreSQL's Appendix A, class 23; drift fails here."""
        assert _PG_SQLSTATE_VIOLATIONS == {
            "23505": IntegrityViolation.UNIQUE,
            "23503": IntegrityViolation.FOREIGN_KEY,
            "23502": IntegrityViolation.NOT_NULL,
            "23514": IntegrityViolation.CHECK,
        }
