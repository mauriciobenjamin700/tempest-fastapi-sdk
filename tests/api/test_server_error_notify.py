"""The two seams a service needs when a 5xx happens.

``register_exception_handlers`` accepted ``log_level``, which decides
severity, and nothing that decided **destination** or **side effect** —
so a consumer that wanted to alert someone, or to route the record into
its own logging configuration, kept its own handlers instead of adopting
the registrar.

Note on the ``500.log`` claim that prompted this: it is **not** true that
the marker is missing. ``handlers.py`` imports ``HTTP_500_MARKER`` and
sets it on all three 5xx paths, and measured with ``LogUtils`` at its
default ``scope="root"`` the file gets the record. The real gap is
narrower and is what ``logger=`` closes: the handlers log to their own
logger, so a service configuring ``scope="logger"`` covers none of them
— and the records then reach neither ``500.log`` nor ``error.log``.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Literal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from tempest_fastapi_sdk import (
    LogUtils,
    ServerErrorCallback,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions.base import AppException


class UpstreamDown(AppException):
    """A 5xx ``AppException``, which is one of the three 5xx paths."""

    status_code = 503
    code = "UPSTREAM_DOWN"


def _app(
    *,
    on_server_error: ServerErrorCallback | Literal["record"] | None = "record",
    logger: logging.Logger | None = None,
) -> tuple[FastAPI, list[tuple[str, str]]]:
    """Build an app whose routes fail in each of the four ways.

    Args:
        on_server_error (ServerErrorCallback | Literal["record"] | None):
            The callback to register. ``"record"`` installs one that
            appends to the returned list; ``None`` registers none.
        logger (logging.Logger | None): Passed straight through to
            :func:`register_exception_handlers`.

    Returns:
        tuple[FastAPI, list[tuple[str, str]]]: The app, and the list the
        recording callback appends ``(path, exception type)`` to.
    """
    seen: list[tuple[str, str]] = []

    async def notify(request: Request, exc: Exception) -> None:
        """Record the notification instead of sending one.

        Args:
            request (Request): The failed request.
            exc (Exception): The exception reported.
        """
        seen.append((request.url.path, type(exc).__name__))

    app = FastAPI()
    register_exception_handlers(
        app,
        on_server_error=notify if on_server_error == "record" else on_server_error,
        logger=logger,
    )

    @app.get("/unhandled")
    async def unhandled() -> None:
        raise RuntimeError("kaboom")

    @app.get("/http500")
    async def http500() -> None:
        raise HTTPException(500, "nope")

    @app.get("/app5xx")
    async def app5xx() -> None:
        raise UpstreamDown("upstream is down")

    @app.get("/http404")
    async def http404() -> None:
        raise HTTPException(404, "missing")

    return app, seen


class TestTheCallbackFiresOnEveryServerError:
    """Three code paths produce a 5xx, and all three must notify.

    A hook wired only to the catch-all misses two of them: a raw
    ``HTTPException(500)`` is intercepted by Starlette before the
    catch-all sees it, and a 5xx ``AppException`` is handled by its own
    registration.
    """

    @pytest.mark.parametrize(
        ("path", "status", "exc_name"),
        [
            ("/unhandled", 500, "RuntimeError"),
            ("/http500", 500, "HTTPException"),
            ("/app5xx", 503, "UpstreamDown"),
        ],
    )
    def test_each_five_hundred_path_notifies(
        self,
        path: str,
        status: int,
        exc_name: str,
    ) -> None:
        app, seen = _app()
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(path)

        assert response.status_code == status
        assert seen == [(path, exc_name)]

    def test_a_client_error_does_not_notify(self) -> None:
        """Paging on a 404 is how an alert channel becomes ignored."""
        app, seen = _app()
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/http404")

        assert response.status_code == 404
        assert seen == []


class TestAFailingCallbackCannotMakeThingsWorse:
    """A notifier that raises must not replace the real failure.

    Measured before the wrapper: a raw ``BackgroundTask`` does deliver,
    but its exception propagates up the ASGI stack, so the notifier's
    ``ValueError`` is what the server ends up reporting instead of the
    original error.
    """

    def test_the_response_is_still_the_envelope(self) -> None:
        async def bad(request: Request, exc: Exception) -> None:
            raise ValueError("the notifier broke")

        app, _ = _app(on_server_error=bad)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/unhandled")

        assert response.status_code == 500
        assert response.json()["code"] == "INTERNAL_SERVER_ERROR"

    def test_the_original_exception_is_what_propagates(self) -> None:
        async def bad(request: Request, exc: Exception) -> None:
            raise ValueError("the notifier broke")

        app, _ = _app(on_server_error=bad)
        with (
            TestClient(app, raise_server_exceptions=True) as client,
            pytest.raises(RuntimeError, match="kaboom"),
        ):
            client.get("/unhandled")

    def test_the_failure_is_logged_once(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async def bad(request: Request, exc: Exception) -> None:
            raise ValueError("the notifier broke")

        app, _ = _app(on_server_error=bad)
        with (
            caplog.at_level(
                logging.ERROR,
                logger="tempest_fastapi_sdk.api.handlers",
            ),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            client.get("/unhandled")

        failures = [
            record
            for record in caplog.records
            if "on_server_error callback failed" in record.getMessage()
        ]
        assert len(failures) == 1


class TestTheLoggerParameterRoutesTheRecord:
    """``logger=`` sends the handler's record where the service reads.

    With ``LogUtils(..., scope="logger")`` the SDK's own logger is not
    in the configured tree, so measured on 0.283.1 the 500 reached
    neither ``500.log`` nor ``error.log``.
    """

    def _lines(self, directory: pathlib.Path, name: str) -> int:
        """Count non-blank lines in one log file.

        Args:
            directory (pathlib.Path): Where the logs were written.
            name (str): The file to count.

        Returns:
            int: The line count, or ``0`` when the file is absent.
        """
        path = directory / name
        if not path.exists():
            return 0
        return len([line for line in path.read_text().splitlines() if line.strip()])

    def test_an_isolated_scope_gets_nothing_without_it(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        LogUtils("svc.without", log_dir=str(tmp_path), scope="logger")
        app, _ = _app(on_server_error=None)
        with TestClient(app, raise_server_exceptions=False) as client:
            client.get("/unhandled")

        assert self._lines(tmp_path, "500.log") == 0
        assert self._lines(tmp_path, "error.log") == 0

    def test_passing_the_logger_routes_it_into_both_files(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        log = LogUtils("svc.with", log_dir=str(tmp_path), scope="logger")
        app, _ = _app(on_server_error=None, logger=log.logger)
        with TestClient(app, raise_server_exceptions=False) as client:
            client.get("/unhandled")

        assert self._lines(tmp_path, "500.log") == 1
        assert self._lines(tmp_path, "error.log") == 1
