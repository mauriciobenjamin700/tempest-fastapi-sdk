"""The 5xx handlers keep the database's text out of the log (issue #296).

Postgres answers a duplicate with a ``DETAIL`` line that quotes the row,
``asyncpg`` relays it and SQLAlchemy prints it in ``str(IntegrityError)``.
The fixture message below is the one Postgres 16 produced for a unique
``cpf`` column; ``tests/db/test_integrity_live.py`` reproduces it against
a live server.
"""

from __future__ import annotations

import logging
import traceback

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import DataError, IntegrityError

from tempest_fastapi_sdk import (
    WITHHELD_NOTICE,
    AppException,
    RedactedError,
    redact_database_errors,
    register_exception_handlers,
)

SECRET = "123.456.789-00"

PG_UNIQUE = (
    'duplicate key value violates unique constraint "p_cpf_key"\n'
    f"DETAIL:  Key (cpf)=({SECRET}) already exists."
)


class DriverUniqueViolationError(Exception):
    """Stands in for asyncpg's ``UniqueViolationError``."""


DRIVER = f"{__name__}.DriverUniqueViolationError"


class ServiceError(Exception):
    """An application error raised from the database one."""


def _integrity_error() -> IntegrityError:
    """Raise and catch an ``IntegrityError`` chained the way asyncpg chains it.

    Returns:
        IntegrityError: The error, with the driver exception as its cause
        and a real traceback.
    """
    try:
        try:
            raise DriverUniqueViolationError(PG_UNIQUE)
        except DriverUniqueViolationError as driver:
            raise IntegrityError(
                "INSERT INTO p (cpf) VALUES ($1)",
                (SECRET,),
                driver,
            ) from driver
    except IntegrityError as error:
        return error
    raise AssertionError("unreachable")


def _formatted(error: BaseException) -> str:
    """Format ``error`` the way ``logging.Formatter.formatException`` does.

    Args:
        error (BaseException): The exception to format.

    Returns:
        str: The whole traceback, chain included.
    """
    return "".join(traceback.format_exception(error))


class TestRedactDatabaseErrors:
    def test_the_raw_traceback_leaks_the_value(self) -> None:
        """Pins the premise, so the tests below cannot pass vacuously."""
        assert SECRET in _formatted(_integrity_error())

    def test_redacted_traceback_drops_the_value(self) -> None:
        text = _formatted(redact_database_errors(_integrity_error()))

        assert SECRET not in text
        assert "DETAIL" not in text

    def test_summary_keeps_the_schema_names(self) -> None:
        redacted = redact_database_errors(_integrity_error())

        assert isinstance(redacted, RedactedError)
        assert redacted.original_type == "sqlalchemy.exc.IntegrityError"
        assert str(redacted) == (
            "sqlalchemy.exc.IntegrityError: unique violation; "
            "constraint=p_cpf_key; columns=cpf; "
            f"driver={DRIVER}; "
            f"{WITHHELD_NOTICE}"
        )

    def test_frames_are_kept(self) -> None:
        original = _integrity_error()
        redacted = redact_database_errors(original)

        assert traceback.extract_tb(redacted.__traceback__) == traceback.extract_tb(
            original.__traceback__
        )

    def test_driver_link_below_is_dropped(self) -> None:
        redacted = redact_database_errors(_integrity_error())

        assert redacted.__cause__ is None
        assert redacted.__suppress_context__ is True

    def test_original_is_not_mutated(self) -> None:
        original = _integrity_error()
        cause = original.__cause__

        redact_database_errors(original)

        assert original.__cause__ is cause
        assert SECRET in str(original)

    def test_without_database_error_returns_the_same_object(self) -> None:
        error = RuntimeError("kaboom")

        assert redact_database_errors(error) is error

    def test_link_above_keeps_its_text(self) -> None:
        try:
            raise ServiceError("could not create") from _integrity_error()
        except ServiceError as error:
            redacted = redact_database_errors(error)

        text = _formatted(redacted)
        assert f"{__name__}.ServiceError: could not create" in text
        assert "unique violation" in text
        assert SECRET not in text

    def test_implicit_context_is_redacted(self) -> None:
        try:
            try:
                raise _integrity_error()
            except IntegrityError:
                raise KeyError("while handling") from None
        except KeyError as error:
            error.__suppress_context__ = False
            redacted = redact_database_errors(error)

        text = _formatted(redacted)
        assert "During handling of the above exception" in text
        assert SECRET not in text

    def test_exception_group_member_is_redacted(self) -> None:
        group = ExceptionGroup("batch", [_integrity_error(), ValueError("other")])

        text = _formatted(redact_database_errors(group))

        assert SECRET not in text
        assert "ValueError: other" in text
        assert "unique violation" in text

    def test_cycle_terminates(self) -> None:
        first = RuntimeError("first")
        second = _integrity_error()
        first.__context__ = second
        second.__context__ = first

        text = _formatted(redact_database_errors(first))

        assert SECRET not in text

    def test_other_dbapi_errors_keep_only_the_driver_type(self) -> None:
        driver = DriverUniqueViolationError(
            f"invalid input for query argument $1: {SECRET!r}"
        )
        error = DataError("SELECT 1 WHERE x = $1", (SECRET,), driver)

        redacted = redact_database_errors(error)

        assert str(redacted) == (
            f"sqlalchemy.exc.DataError: driver={DRIVER}; {WITHHELD_NOTICE}"
        )

    def test_statement_with_a_literal_is_not_logged(self) -> None:
        driver = DriverUniqueViolationError(PG_UNIQUE)
        error = IntegrityError(f"INSERT INTO p (cpf) VALUES ('{SECRET}')", {}, driver)

        assert SECRET not in _formatted(redact_database_errors(error))


def _app(**options: object) -> tuple[FastAPI, list[BaseException]]:
    """Build an app whose routes fail with a database error.

    Args:
        **options (object): Forwarded to ``register_exception_handlers``.

    Returns:
        tuple[FastAPI, list[BaseException]]: The app and the list
        ``on_server_error`` appends to.
    """
    notified: list[BaseException] = []

    async def notify(request: Request, exc: Exception) -> None:
        notified.append(exc)

    app = FastAPI()
    register_exception_handlers(app, on_server_error=notify, **options)  # type: ignore[arg-type]

    @app.get("/unhandled")
    async def unhandled() -> None:
        raise _integrity_error()

    @app.get("/app-500")
    async def app_500() -> None:
        raise AppException(message="write failed") from _integrity_error()

    return app, notified


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    """Return every captured 5xx record, formatted with its traceback.

    Args:
        caplog (pytest.LogCaptureFixture): The capture fixture.

    Returns:
        str: The records as a formatter would write them.
    """
    formatter = logging.Formatter()
    return "\n".join(formatter.format(record) for record in caplog.records)


class TestHandlersRedact:
    @pytest.mark.parametrize("path", ["/unhandled", "/app-500"])
    def test_default_log_has_no_value(
        self,
        path: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        app, _ = _app()
        with caplog.at_level(logging.INFO, logger="tempest_fastapi_sdk.api.handlers"):
            response = TestClient(app, raise_server_exceptions=False).get(path)

        assert response.status_code == 500
        text = _logged(caplog)
        assert "unique violation" in text
        assert SECRET not in text

    def test_on_server_error_receives_the_original(self) -> None:
        app, notified = _app()

        TestClient(app, raise_server_exceptions=False).get("/unhandled")

        assert len(notified) == 1
        assert isinstance(notified[0], IntegrityError)
        assert SECRET in str(notified[0])

    def test_none_logs_the_exception_as_raised(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        app, _ = _app(redact_exception=None)
        with caplog.at_level(logging.ERROR, logger="tempest_fastapi_sdk.api.handlers"):
            TestClient(app, raise_server_exceptions=False).get("/unhandled")

        assert SECRET in _logged(caplog)

    def test_custom_redactor_is_used(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def redactor(error: BaseException) -> BaseException:
            return RuntimeError("custom")

        app, _ = _app(redact_exception=redactor)
        with caplog.at_level(logging.ERROR, logger="tempest_fastapi_sdk.api.handlers"):
            TestClient(app, raise_server_exceptions=False).get("/unhandled")

        text = _logged(caplog)
        assert "RuntimeError: custom" in text
        assert SECRET not in text
