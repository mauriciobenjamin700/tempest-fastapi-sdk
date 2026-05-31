"""FastAPI exception handlers for ``AppException`` and unhandled errors."""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tempest_fastapi_sdk.core.context import get_request_id
from tempest_fastapi_sdk.exceptions.base import AppException

logger = logging.getLogger("tempest_fastapi_sdk.api.handlers")


async def app_exception_handler(
    request: Request,
    exc: AppException,
) -> JSONResponse:
    """Serialize an :class:`AppException` to the SDK JSON envelope.

    Emits the shape::

        {
            "detail": "<message>",
            "code": "<machine-readable code>",
            "details": {...}
        }

    Args:
        request (Request): The incoming HTTP request. Unused — kept
            for signature compatibility with FastAPI handlers.
        exc (AppException): The exception raised.

    Returns:
        JSONResponse: The serialized response.
    """
    del request
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "code": exc.code,
            "details": exc.details,
        },
        headers=exc.headers,
    )


def make_unhandled_exception_handler(
    *,
    include_traceback: bool = False,
    log_level: int = logging.ERROR,
) -> Any:
    """Build the catch-all handler for non-:class:`AppException` errors.

    Default FastAPI/Starlette behavior on uncaught exceptions is to
    return a bare ``Internal Server Error`` string and emit nothing
    beyond the access log line — the actual traceback never reaches
    the logger and never reaches the operator. This handler closes
    that gap:

    1. Logs the full traceback at ``log_level`` (ERROR by default)
       under the ``tempest_fastapi_sdk.api.handlers`` logger, with
       the active request ID attached so the failure correlates
       with the inbound request line.
    2. Returns the canonical SDK JSON envelope with
       ``code="INTERNAL_SERVER_ERROR"`` and ``status_code=500``.
    3. When ``include_traceback=True`` (development only) appends
       the formatted traceback under ``details.traceback`` so the
       failure is visible in the browser too. Leave it off in
       production — the body would leak module paths, secrets in
       ``repr`` output and SQL fragments.

    Args:
        include_traceback (bool): Whether to surface the traceback
            in the response body. Off in production.
        log_level (int): Logging level used to emit the traceback.

    Returns:
        Any: An async ``(request, exc) -> JSONResponse`` callable
        ready to pass to :meth:`FastAPI.add_exception_handler`.
    """

    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        # contextvar first (works for plain ASGI middlewares), then the
        # inbound X-Request-ID header (BaseHTTPMiddleware spawns a child
        # task so its contextvars don't always reach the exception
        # handler), then None.
        request_id = (
            get_request_id()
            or request.headers.get("X-Request-ID")
            or request.headers.get("x-request-id")
        )
        logger.log(
            log_level,
            "Unhandled exception during %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
            extra={"request_id": request_id, "path": request.url.path},
        )
        body: dict[str, Any] = {
            "detail": "Internal server error",
            "code": "INTERNAL_SERVER_ERROR",
            "details": ({"request_id": request_id} if request_id else {}),
        }
        if include_traceback:
            body["details"]["traceback"] = traceback.format_exception(
                type(exc), exc, exc.__traceback__
            )
        return JSONResponse(status_code=500, content=body)

    return _handler


def register_exception_handlers(
    app: FastAPI,
    *,
    include_traceback: bool = False,
    log_level: int = logging.ERROR,
) -> None:
    """Register the SDK's exception handlers on a FastAPI app.

    Wires two handlers:

    * :class:`AppException` → :func:`app_exception_handler`. Every
      domain-specific subclass returned by routers, services and
      repositories is serialized consistently.
    * :class:`Exception` (catch-all) → traceback logger + generic
      500 envelope. Without this, FastAPI's default returns the
      string ``"Internal Server Error"`` with no log entry beyond
      the access line, leaving operators blind to real failures.

    Args:
        app (FastAPI): The FastAPI application to wire.
        include_traceback (bool): When ``True``, the unhandled-500
            response body includes the formatted traceback under
            ``details.traceback``. Use only in development.
        log_level (int): Logging level used by the catch-all
            handler. Defaults to :data:`logging.ERROR`.
    """
    app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        Exception,
        make_unhandled_exception_handler(
            include_traceback=include_traceback,
            log_level=log_level,
        ),
    )


__all__: list[str] = [
    "app_exception_handler",
    "make_unhandled_exception_handler",
    "register_exception_handlers",
]
