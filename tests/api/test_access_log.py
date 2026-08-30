"""Tests for :class:`AccessLogMiddleware`."""

from __future__ import annotations

import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import AccessLogMiddleware, RequestIDMiddleware

LOGGER_NAME: str = "tempest.access.test"


def build_app(**options: object) -> FastAPI:
    """Build an app whose routes cover every logged outcome.

    Args:
        **options (object): Forwarded to :class:`AccessLogMiddleware`.

    Returns:
        FastAPI: The configured application.
    """
    app: FastAPI = FastAPI()

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("handler exploded")

    @app.get("/rendered-500")
    def rendered_500() -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": "nope"})

    @app.get("/stream/events")
    def stream() -> dict[str, str]:
        return {"status": "streaming"}

    @app.get("/auth/google/{token}")
    def google(token: str) -> dict[str, str]:
        return {"token": token}

    app.add_middleware(
        AccessLogMiddleware,
        logger_name=LOGGER_NAME,
        **options,
    )
    return app


class TestAccessLogFields:
    """One record per request, with the details as real fields."""

    def test_success_emits_one_record_with_structured_fields(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A 200 produces exactly one INFO record carrying every field."""
        with (
            caplog.at_level(logging.INFO, logger=LOGGER_NAME),
            TestClient(build_app()) as client,
        ):
            assert client.get("/ok?page=2").status_code == 200

        records = [r for r in caplog.records if r.name == LOGGER_NAME]
        assert len(records) == 1
        record = records[0]
        assert record.levelno == logging.INFO
        assert record.http_method == "GET"
        assert record.http_path == "/ok"
        assert record.http_query == "page=2"
        assert record.http_status == 200
        assert record.duration_ms >= 0.0
        assert not hasattr(record, "error")

    def test_message_stays_readable_for_a_plain_formatter(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The rendered message is the familiar access-log line."""
        with (
            caplog.at_level(logging.INFO, logger=LOGGER_NAME),
            TestClient(build_app()) as client,
        ):
            client.get("/ok")

        message = next(r.getMessage() for r in caplog.records if r.name == LOGGER_NAME)
        assert re.fullmatch(r"GET /ok 200 \d+\.\d+ms", message), message

    def test_level_is_configurable(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``level`` moves non-error lines off INFO."""
        with (
            caplog.at_level(logging.DEBUG, logger=LOGGER_NAME),
            TestClient(build_app(level=logging.DEBUG)) as client,
        ):
            client.get("/ok")

        record = next(r for r in caplog.records if r.name == LOGGER_NAME)
        assert record.levelno == logging.DEBUG


class TestAccessLogFailures:
    """The request that most needs a line is the one that blew up."""

    def test_unhandled_exception_is_logged_and_re_raised(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The exception still propagates, and the line says 500."""
        client = TestClient(build_app(), raise_server_exceptions=True)
        with (
            caplog.at_level(logging.INFO, logger=LOGGER_NAME),
            pytest.raises(RuntimeError, match="handler exploded"),
        ):
            client.get("/boom")

        record = next(r for r in caplog.records if r.name == LOGGER_NAME)
        assert record.levelno == logging.ERROR
        assert record.http_status == 500
        assert record.error == "RuntimeError"

    def test_rendered_server_error_is_logged_at_error(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A 500 the app rendered itself is as bad as one that escaped."""
        with (
            caplog.at_level(logging.INFO, logger=LOGGER_NAME),
            TestClient(build_app()) as client,
        ):
            assert client.get("/rendered-500").status_code == 500

        record = next(r for r in caplog.records if r.name == LOGGER_NAME)
        assert record.levelno == logging.ERROR
        assert record.http_status == 500
        assert not hasattr(record, "error")


class TestAccessLogExemptions:
    """A stream held open for an hour is not a request that took an hour."""

    def test_exempt_prefix_produces_no_record(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``("/stream",)`` covers ``/stream/events``."""
        app = build_app(exempt_paths=("/stream",))
        with (
            caplog.at_level(logging.INFO, logger=LOGGER_NAME),
            TestClient(app) as client,
        ):
            assert client.get("/stream/events").status_code == 200
            assert client.get("/ok").status_code == 200

        paths = [r.http_path for r in caplog.records if r.name == LOGGER_NAME]
        assert paths == ["/ok"]


class TestAccessLogRedaction:
    """A secret in the URL is already logged by the time a handler refuses."""

    def test_redact_rewrites_path_and_query(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Both halves of the target go through the redactor."""

        def redact(value: str) -> str:
            """Replace anything that looks like a token."""
            return re.sub(r"secret-[A-Za-z0-9]+", "<redacted>", value)

        app = build_app(redact=redact)
        with (
            caplog.at_level(logging.INFO, logger=LOGGER_NAME),
            TestClient(app) as client,
        ):
            client.get("/auth/google/secret-abc123?t=secret-xyz789")

        record = next(r for r in caplog.records if r.name == LOGGER_NAME)
        assert record.http_path == "/auth/google/<redacted>"
        assert record.http_query == "t=<redacted>"
        assert "secret-abc123" not in record.getMessage()


class TestAccessLogRequestIdOrdering:
    """The correlation id is only there while the binding is."""

    @staticmethod
    def _bound_request_ids(app: FastAPI) -> list[str | None]:
        """Drive one request and report the id each access record carried.

        Args:
            app (FastAPI): The application to exercise.

        Returns:
            list[str | None]: One entry per access record.
        """
        from tempest_fastapi_sdk.core.context import get_request_id

        seen: list[str | None] = []

        class _Capture(logging.Handler):
            """Record the bound correlation id at emit time."""

            def emit(self, record: logging.LogRecord) -> None:
                """Capture the context variable, not the record.

                Args:
                    record (logging.LogRecord): Ignored; the point is
                        when this runs, not what it carries.
                """
                del record
                seen.append(get_request_id())

        handler = _Capture()
        logger = logging.getLogger(LOGGER_NAME)
        previous = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            with TestClient(app) as client:
                client.get("/ok")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        return seen

    def test_access_log_added_first_sees_the_request_id(self) -> None:
        """Added first, it is innermost, so the binding is still live."""
        app = build_app()
        app.add_middleware(RequestIDMiddleware)
        assert self._bound_request_ids(app) == [_NotNone()]

    def test_access_log_added_last_is_outside_the_binding(self) -> None:
        """Added last, it is outermost, and the id is already cleared."""
        app: FastAPI = FastAPI()

        @app.get("/ok")
        def ok() -> dict[str, str]:
            return {"status": "ok"}

        app.add_middleware(RequestIDMiddleware)
        app.add_middleware(AccessLogMiddleware, logger_name=LOGGER_NAME)
        assert self._bound_request_ids(app) == [None]


class _NotNone:
    """Sentinel comparing equal to any value that is not ``None``."""

    def __eq__(self, other: object) -> bool:
        """Report whether ``other`` is anything but ``None``.

        Args:
            other (object): The value under comparison.

        Returns:
            bool: ``True`` when ``other`` is not ``None``.
        """
        return other is not None

    def __hash__(self) -> int:
        """Hash consistently with :meth:`__eq__`.

        Returns:
            int: A constant, since equality is not value-based.
        """
        return 0
