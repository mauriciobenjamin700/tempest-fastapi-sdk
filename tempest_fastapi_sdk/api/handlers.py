"""FastAPI exception handlers for ``AppException`` and unhandled errors."""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tempest_fastapi_sdk.core.context import get_request_id
from tempest_fastapi_sdk.core.logging import HTTP_500_MARKER
from tempest_fastapi_sdk.exceptions.base import AppException

logger = logging.getLogger("tempest_fastapi_sdk.api.handlers")


def make_app_exception_handler(
    *,
    log_level: int = logging.INFO,
) -> Any:
    """Build the handler for :class:`AppException` subclasses.

    Serializes the exception to the SDK envelope and emits an
    ``INFO``-level log line (no traceback — 4xx is normal client
    flow). ``5xx`` ``AppException`` subclasses bump up to
    ``log_level`` with a traceback and the
    :data:`HTTP_500_MARKER` flag so ``500.log`` captures them.

    Args:
        log_level (int): Level used **only** for 5xx ``AppException``
            records (the 4xx path always logs at ``INFO`` regardless,
            since elevating client errors to WARN/ERROR adds noise).
            Defaults to :data:`logging.INFO`; pass ``logging.ERROR``
            (or pass ``log_level=logging.ERROR`` through
            :func:`register_exception_handlers`) when 5xx
            ``AppException`` subclasses should trigger paging.

    Returns:
        Any: An async ``(request, exc) -> JSONResponse`` callable.
    """

    async def _handler(
        request: Request,
        exc: AppException,
    ) -> JSONResponse:
        request_id = (
            get_request_id()
            or request.headers.get("X-Request-ID")
            or request.headers.get("x-request-id")
        )
        is_server_error = exc.status_code >= 500
        extra: dict[str, Any] = {
            "request_id": request_id,
            "path": request.url.path,
            "status_code": exc.status_code,
            "code": exc.code,
        }
        if is_server_error:
            extra[HTTP_500_MARKER] = True
        logger.log(
            log_level if is_server_error else logging.INFO,
            "AppException %s (%s) during %s %s: %s",
            exc.status_code,
            exc.code,
            request.method,
            request.url.path,
            exc.detail,
            exc_info=exc if is_server_error else None,
            extra=extra,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "code": exc.code,
                "details": exc.details,
            },
            headers=exc.headers,
        )

    return _handler


async def app_exception_handler(
    request: Request,
    exc: AppException,
) -> JSONResponse:
    """Default :class:`AppException` handler (logs at INFO).

    Thin wrapper around :func:`make_app_exception_handler` kept for
    backwards compatibility with code that imports the handler
    callable directly.

    Args:
        request (Request): The incoming HTTP request.
        exc (AppException): The exception raised.

    Returns:
        JSONResponse: The serialized response.
    """
    handler = make_app_exception_handler()
    response: JSONResponse = await handler(request, exc)
    return response


def make_unhandled_exception_handler(
    *,
    log_traceback: bool = True,
    include_traceback: bool = False,
    log_level: int = logging.ERROR,
) -> Any:
    """Build the catch-all handler for non-:class:`AppException` errors.

    Default FastAPI/Starlette behavior on uncaught exceptions is to
    return a bare ``Internal Server Error`` string and emit nothing
    beyond the access log line — the actual traceback never reaches
    the logger and never reaches the operator. This handler closes
    that gap:

    1. Logs the failure at ``log_level`` (ERROR by default) under the
       ``tempest_fastapi_sdk.api.handlers`` logger. When
       ``log_traceback=True`` (the default), the full traceback is
       attached via ``exc_info`` so the application's
       ``LogUtils`` / ``configure_logging`` setup serializes it. The
       record is flagged with
       :data:`tempest_fastapi_sdk.core.logging.HTTP_500_MARKER` so
       ``configure_logging(log_dir=...)`` can route it to a dedicated
       ``500.log``.
    2. Returns the canonical SDK JSON envelope with
       ``code="INTERNAL_SERVER_ERROR"`` and ``status_code=500``.
    3. When ``include_traceback=True`` (development only) appends
       the formatted traceback under ``details.traceback`` so the
       failure is visible in the browser too. Leave it off in
       production — the body would leak module paths, secrets in
       ``repr`` output and SQL fragments.

    Args:
        log_traceback (bool): Whether to attach the full traceback to
            the log record via ``exc_info``. Defaults to ``True`` — we
            want operators to see the cause every time. Pass ``False``
            only when the trace would be noisy AND the failure is
            already being captured elsewhere (e.g. an APM agent).
        include_traceback (bool): Whether to surface the traceback in
            the *response body*. Off in production.
        log_level (int): Logging level used by the catch-all handler.

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
            exc_info=exc if log_traceback else None,
            extra={
                "request_id": request_id,
                "path": request.url.path,
                HTTP_500_MARKER: True,
            },
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


def make_http_exception_handler(
    *,
    log_traceback: bool = True,
    log_level: int = logging.ERROR,
) -> Any:
    """Build the handler for raw :class:`starlette.exceptions.HTTPException`.

    Without this, ``raise HTTPException(500, "...")`` (or ``404``,
    ``403``, …) bypasses the SDK's ``Exception`` catch-all entirely:
    Starlette intercepts ``HTTPException`` instances inside its
    ``ExceptionMiddleware`` and routes them to its own default — a
    bare ``JSONResponse({"detail": exc.detail})`` with no log entry.
    Operators see the 500 in the access log and *no* trace.

    This handler closes that gap for 5xx HTTPExceptions:

    1. Whenever ``exc.status_code >= 500``, the failure is logged at
       ``log_level`` (ERROR by default) under
       ``tempest_fastapi_sdk.api.handlers``. The record is flagged
       with :data:`HTTP_500_MARKER` so ``configure_logging(log_dir=…)``
       routes it to the dedicated ``500.log`` alongside the trace.
    2. The response keeps the original ``status_code`` /
       ``headers`` and adds the SDK envelope shape
       (``detail`` / ``code`` / ``details``), so frontends consuming
       the same envelope across :class:`AppException` and raw
       ``HTTPException`` don't need to branch.

    4xx HTTPExceptions are returned untouched (Starlette's default
    behavior), since those represent normal client-side outcomes that
    don't deserve a stack trace.

    Args:
        log_traceback (bool): Whether to attach ``exc_info=exc`` to
            the 5xx log record. ``True`` by default.
        log_level (int): Logging level used for 5xx records.

    Returns:
        Any: An async ``(request, exc) -> JSONResponse`` callable
        ready to pass to :meth:`FastAPI.add_exception_handler`.
    """

    async def _handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        request_id = (
            get_request_id()
            or request.headers.get("X-Request-ID")
            or request.headers.get("x-request-id")
        )
        if exc.status_code >= 500:
            logger.log(
                log_level,
                "HTTPException %s during %s %s: %s",
                exc.status_code,
                request.method,
                request.url.path,
                exc.detail,
                exc_info=exc if log_traceback else None,
                extra={
                    "request_id": request_id,
                    "path": request.url.path,
                    "status_code": exc.status_code,
                    HTTP_500_MARKER: True,
                },
            )
            body: dict[str, Any] = {
                "detail": str(exc.detail or "Internal server error"),
                "code": "INTERNAL_SERVER_ERROR",
                "details": ({"request_id": request_id} if request_id else {}),
            }
            return JSONResponse(
                status_code=exc.status_code,
                content=body,
                headers=getattr(exc, "headers", None),
            )
        # 4xx — INFO-level log (no traceback, no 500.log marker) so
        # operators can still see the request that failed without
        # paying the cost of a stack trace.
        logger.log(
            logging.INFO,
            "HTTPException %s during %s %s: %s",
            exc.status_code,
            request.method,
            request.url.path,
            exc.detail,
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "status_code": exc.status_code,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    return _handler


def register_exception_handlers(
    app: FastAPI,
    *,
    log_traceback: bool = True,
    include_traceback: bool = False,
    log_level: int = logging.ERROR,
) -> None:
    """Register the SDK's exception handlers on a FastAPI app.

    Wires three handlers, in order of specificity:

    * :class:`AppException` → :func:`app_exception_handler`. Every
      domain-specific subclass returned by routers, services and
      repositories is serialized consistently.
    * :class:`starlette.exceptions.HTTPException` →
      :func:`make_http_exception_handler` factory. ``raise
      HTTPException(500, ...)`` would otherwise bypass the SDK's
      catch-all (Starlette intercepts HTTPException inside its own
      middleware), so this handler restores the log + envelope
      behavior for 5xx HTTPExceptions while leaving 4xx untouched.
    * :class:`Exception` (catch-all) → traceback logger + generic
      500 envelope. Without this, FastAPI's default returns the
      string ``"Internal Server Error"`` with no log entry beyond
      the access line, leaving operators blind to real failures.

    Args:
        app (FastAPI): The FastAPI application to wire.
        log_traceback (bool): Whether the 5xx handlers attach the
            full traceback to the log record. Defaults to ``True``
            (always emit the trace). Pass ``False`` to silence the
            trace when an APM / Sentry / equivalent is already
            capturing the failure.
        include_traceback (bool): When ``True``, the unhandled-500
            response body includes the formatted traceback under
            ``details.traceback``. Use only in development.
        log_level (int): Logging level used by the 5xx handlers.
            Defaults to :data:`logging.ERROR`.
    """
    app.add_exception_handler(
        AppException,
        make_app_exception_handler(log_level=log_level),
    )
    app.add_exception_handler(
        StarletteHTTPException,
        make_http_exception_handler(
            log_traceback=log_traceback,
            log_level=log_level,
        ),
    )
    app.add_exception_handler(
        Exception,
        make_unhandled_exception_handler(
            log_traceback=log_traceback,
            include_traceback=include_traceback,
            log_level=log_level,
        ),
    )


__all__: list[str] = [
    "app_exception_handler",
    "make_app_exception_handler",
    "make_http_exception_handler",
    "make_unhandled_exception_handler",
    "register_exception_handlers",
]
