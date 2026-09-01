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

    def test_unique_reports_no_table(self) -> None:
        """Measured absence: the sentence never names one.

        The constraint name usually starts with the table by convention,
        and splitting on a convention a hand-written DDL need not follow
        would be a guess.
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
