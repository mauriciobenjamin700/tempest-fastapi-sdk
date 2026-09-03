"""FastAPI exception handlers for ``AppException`` and unhandled errors."""

from __future__ import annotations

import http
import logging
import traceback
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from tempest_fastapi_sdk.api.error_docs import describe_validation_envelope
from tempest_fastapi_sdk.core.context import get_request_id
from tempest_fastapi_sdk.core.logging import HTTP_500_MARKER
from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.i18n import (
    DEFAULT_LOCALE,
    VALIDATION_KEY_PREFIX,
    MessageCatalog,
)

logger = logging.getLogger("tempest_fastapi_sdk.api.handlers")

_DEFAULT_LOGGER = logger
"""Where the handlers log when the caller names no logger.

Aliased so a ``logger`` parameter can shadow the module name inside a
factory without the closure below silently reading the wrong object.
"""

ServerErrorCallback = Callable[[Request, Exception], Awaitable[None]]
"""Async callable invoked after a 5xx response has been built.

Runs as a Starlette ``BackgroundTask``, so it never delays or alters the
response the client receives.
"""


def _notify_after_response(
    callback: ServerErrorCallback | None,
    request: Request,
    exc: Exception,
    log: logging.Logger,
) -> BackgroundTask | None:
    """Wrap ``callback`` so a failure inside it cannot escape.

    A raw ``BackgroundTask`` does deliver — measured on starlette 1.6.0,
    a task attached to the response of an ``Exception`` handler and of an
    ``HTTPException`` handler both run — but a callback that raises
    propagates up the ASGI stack, so the notifier's exception replaces
    the original one in whatever the server logs. Wrapping keeps the
    500 the client already received from turning into a worse 500 with
    no trace of the real cause.

    Args:
        callback (ServerErrorCallback | None): What to run, or ``None``
            to attach nothing.
        request (Request): The request that failed.
        exc (Exception): The exception being reported.
        log (logging.Logger): Where a failure inside the callback goes.

    Returns:
        BackgroundTask | None: The task to attach, or ``None`` when no
        callback was given.
    """
    if callback is None:
        return None

    async def _run() -> None:
        """Invoke the callback, logging and swallowing its failure."""
        try:
            await callback(request, exc)
        except Exception:
            log.exception(
                "on_server_error callback failed while reporting %s %s",
                request.method,
                request.url.path,
            )

    return BackgroundTask(_run)


AppExceptionHandler = Callable[[Request, AppException], Awaitable[JSONResponse]]
"""Async callable resolving an :class:`AppException` to a JSON response."""

HTTPExceptionHandler = Callable[
    [Request, StarletteHTTPException],
    Awaitable[JSONResponse],
]
"""Async callable resolving a raw ``HTTPException`` to a JSON response."""

UnhandledExceptionHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]
"""Async callable resolving any uncaught exception to a JSON response."""

ValidationExceptionHandler = Callable[
    [Request, RequestValidationError],
    Awaitable[JSONResponse],
]
"""Async callable resolving a request-validation failure to a response."""

VALIDATION_ERROR_CODE: str = "VALIDATION_ERROR"
"""The envelope ``code`` a request-validation failure answers with."""


_STATUS_ERROR_CODES: Final[Mapping[int, str]] = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "FILE_TOO_LARGE",
    415: "INVALID_FILE_TYPE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
}
"""Envelope ``code`` for a raw ``HTTPException`` of each status.

**Declared, not derived.** The SDK has several exception classes per
status — measured, eight carry a 401 code and three a 404 — so there is
no status the mapping could read a single code off. A status absent here
answers ``HTTP_<status>``, which keeps the response parseable by ``code``
even where the SDK never modelled the status. 400 and 405 are in that
group today: no :class:`AppException` subclass carries either.

Every code here is one the built-in catalog translates, which is what
makes localizing a generic ``detail`` possible.
"""


def _client_error_code(status_code: int) -> str:
    """Return the envelope ``code`` for a client-error status.

    Args:
        status_code (int): The HTTP status of the response.

    Returns:
        str: The declared code, or ``HTTP_<status>`` for a status the SDK
        does not model.
    """
    return _STATUS_ERROR_CODES.get(status_code, f"HTTP_{status_code}")


def _detail_is_the_framework_default(exc: StarletteHTTPException) -> bool:
    """Whether ``exc.detail`` is the status phrase rather than a message.

    Starlette fills ``detail`` with ``HTTPStatus(status_code).phrase``
    when the caller passes none, so the two cases are distinguishable:
    ``HTTPException(404, "no such order")`` carries a sentence a developer
    wrote, and ``HTTPException(404)`` carries ``"Not Found"``.

    Only the second may be replaced with a catalog message. Localizing the
    first would **destroy** the specific message — the more informative of
    the two — which is why this is not a blanket translation. Measured,
    FastAPI's own ``"Not authenticated"`` for a missing bearer token also
    falls in the first group and is preserved.

    Args:
        exc (StarletteHTTPException): The exception being answered.

    Returns:
        bool: ``True`` when ``detail`` is the framework's own phrase.
    """
    try:
        phrase = http.HTTPStatus(exc.status_code).phrase
    except ValueError:
        return False
    return str(exc.detail) == phrase


def make_app_exception_handler(
    *,
    log_level: int = logging.INFO,
    catalog: MessageCatalog | None = None,
    default_locale: str = DEFAULT_LOCALE,
    logger: logging.Logger | None = None,
    on_server_error: ServerErrorCallback | None = None,
) -> AppExceptionHandler:
    """Build the handler for :class:`AppException` subclasses.

    Serializes the exception to the SDK envelope and emits an
    ``INFO``-level log line (no traceback — 4xx is normal client
    flow). ``5xx`` ``AppException`` subclasses bump up to
    ``log_level`` with a traceback and the
    :data:`HTTP_500_MARKER` flag so ``500.log`` captures them.

    When a ``catalog`` is supplied, the ``detail`` field is localized:
    the locale is negotiated from the request's ``Accept-Language``
    header (falling back to ``default_locale``) and the exception's
    ``message_key`` (or its ``code``) is resolved against the catalog.
    A missing translation falls back to the exception's literal
    ``detail``, so partial catalogs never blank out a message.

    Args:
        log_level (int): Level used **only** for 5xx ``AppException``
            records (the 4xx path always logs at ``INFO`` regardless,
            since elevating client errors to WARN/ERROR adds noise).
            Defaults to :data:`logging.INFO`; pass ``logging.ERROR``
            (or pass ``log_level=logging.ERROR`` through
            :func:`register_exception_handlers`) when 5xx
            ``AppException`` subclasses should trigger paging.
        catalog (MessageCatalog | None): Message catalog used to
            localize ``detail``. ``None`` keeps the literal message.
        default_locale (str): Locale used when ``Accept-Language`` is
            absent or matches nothing in the catalog.
        logger (logging.Logger | None): Where this handler logs.
            ``None`` uses the SDK's own
            ``tempest_fastapi_sdk.api.handlers`` logger — which a
            service that configures logging with
            ``LogUtils(..., scope="logger")`` does not cover: measured,
            the records then reach neither ``500.log`` nor
            ``error.log``. Pass ``LogUtils(...).logger`` to route them
            into the service's own configuration.

            Typed as ``logging.Logger`` and not as the
            :class:`~tempest_fastapi_sdk.RetryLogger` protocol on
            purpose. That protocol carries ``warning`` and ``error``
            only, with no ``extra=`` and no ``exc_info=``, and these
            handlers pass both plus the ``500.log`` marker. Measured, no
            single type spans the two candidates:
            ``LogUtils.error(extra=...)`` stores a field *named*
            ``extra``, and ``logging.Logger.error(request_id=...)``
            raises ``TypeError``.
        on_server_error (ServerErrorCallback | None): Called with
            ``(request, exc)`` after a 5xx response is built, as a
            background task, so it never delays or alters the response.
            A failure inside it is logged and swallowed — a notifier
            that raises propagates up the ASGI stack and replaces the
            original exception in whatever the server logs.

    Returns:
        AppExceptionHandler: An async ``(request, exc) -> JSONResponse``
        callable.

    Notes:
        The request id is looked up in three places, in order: the
        contextvar, which works for plain ASGI middlewares; the inbound
        ``X-Request-ID`` header, needed because ``BaseHTTPMiddleware``
        spawns a child task whose contextvars do not always reach the
        exception handler; and finally ``None``.
    """

    log: logging.Logger = _DEFAULT_LOGGER if logger is None else logger

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
        log.log(
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
        detail = exc.detail
        if catalog is not None:
            locale = catalog.negotiate(
                request.headers.get("accept-language"),
                default_locale=default_locale,
            )
            localized = catalog.resolve(
                exc.message_key or exc.code,
                locale,
                exc.message_params,
            )
            if localized is not None:
                detail = localized
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": detail,
                "code": exc.code,
                "details": exc.details,
            },
            headers=exc.headers,
            background=(
                _notify_after_response(on_server_error, request, exc, log)
                if is_server_error
                else None
            ),
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
    logger: logging.Logger | None = None,
    on_server_error: ServerErrorCallback | None = None,
) -> UnhandledExceptionHandler:
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
        logger (logging.Logger | None): Where this handler logs.
            ``None`` uses the SDK's own
            ``tempest_fastapi_sdk.api.handlers`` logger — which a
            service that configures logging with
            ``LogUtils(..., scope="logger")`` does not cover: measured,
            the records then reach neither ``500.log`` nor
            ``error.log``. Pass ``LogUtils(...).logger`` to route them
            into the service's own configuration.

            Typed as ``logging.Logger`` and not as the
            :class:`~tempest_fastapi_sdk.RetryLogger` protocol on
            purpose. That protocol carries ``warning`` and ``error``
            only, with no ``extra=`` and no ``exc_info=``, and these
            handlers pass both plus the ``500.log`` marker. Measured, no
            single type spans the two candidates:
            ``LogUtils.error(extra=...)`` stores a field *named*
            ``extra``, and ``logging.Logger.error(request_id=...)``
            raises ``TypeError``.
        on_server_error (ServerErrorCallback | None): Called with
            ``(request, exc)`` after a 5xx response is built, as a
            background task, so it never delays or alters the response.
            A failure inside it is logged and swallowed — a notifier
            that raises propagates up the ASGI stack and replaces the
            original exception in whatever the server logs.

    Returns:
        UnhandledExceptionHandler: An async
        ``(request, exc) -> JSONResponse`` callable ready to pass to
        :meth:`FastAPI.add_exception_handler`.
    """

    log: logging.Logger = _DEFAULT_LOGGER if logger is None else logger

    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = (
            get_request_id()
            or request.headers.get("X-Request-ID")
            or request.headers.get("x-request-id")
        )
        log.log(
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
        return JSONResponse(
            status_code=500,
            content=body,
            background=_notify_after_response(on_server_error, request, exc, log),
        )

    return _handler


def make_http_exception_handler(
    *,
    log_traceback: bool = True,
    log_level: int = logging.ERROR,
    logger: logging.Logger | None = None,
    on_server_error: ServerErrorCallback | None = None,
    catalog: MessageCatalog | None = None,
    default_locale: str = DEFAULT_LOCALE,
    envelope_client_errors: bool = False,
) -> HTTPExceptionHandler:
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
        catalog (MessageCatalog | None): Catalog used to localize a
            generic 4xx ``detail`` when ``envelope_client_errors`` is on.
            This handler had no catalog parameter at all before v0.285.0,
            which is why a service configuring one still answered
            ``{"detail": "Forbidden"}`` in English.
        default_locale (str): Locale used when ``Accept-Language`` is
            absent or matches nothing in the catalog.
        envelope_client_errors (bool): Whether a 4xx answers the SDK
            envelope (``detail`` / ``code`` / ``details``) instead of
            Starlette's bare ``{"detail": ...}``. Off by default: adding
            ``code`` is additive, but a client that pins the exact body
            shape still sees a change.
        logger (logging.Logger | None): Where this handler logs.
            ``None`` uses the SDK's own
            ``tempest_fastapi_sdk.api.handlers`` logger — which a
            service that configures logging with
            ``LogUtils(..., scope="logger")`` does not cover: measured,
            the records then reach neither ``500.log`` nor
            ``error.log``. Pass ``LogUtils(...).logger`` to route them
            into the service's own configuration.

            Typed as ``logging.Logger`` and not as the
            :class:`~tempest_fastapi_sdk.RetryLogger` protocol on
            purpose. That protocol carries ``warning`` and ``error``
            only, with no ``extra=`` and no ``exc_info=``, and these
            handlers pass both plus the ``500.log`` marker. Measured, no
            single type spans the two candidates:
            ``LogUtils.error(extra=...)`` stores a field *named*
            ``extra``, and ``logging.Logger.error(request_id=...)``
            raises ``TypeError``.
        on_server_error (ServerErrorCallback | None): Called with
            ``(request, exc)`` after a 5xx response is built, as a
            background task, so it never delays or alters the response.
            A failure inside it is logged and swallowed — a notifier
            that raises propagates up the ASGI stack and replaces the
            original exception in whatever the server logs.

    Returns:
        HTTPExceptionHandler: An async
        ``(request, exc) -> JSONResponse`` callable ready to pass to
        :meth:`FastAPI.add_exception_handler`.

    Notes:
        4xx responses are logged at ``INFO`` with no traceback and no
        ``500.log`` marker, so an operator can still see which request
        failed without paying for a stack trace on normal client errors.
    """

    log: logging.Logger = _DEFAULT_LOGGER if logger is None else logger

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
            log.log(
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
                background=_notify_after_response(
                    on_server_error,
                    request,
                    exc,
                    log,
                ),
            )
        code: str = _client_error_code(exc.status_code)
        log.log(
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
                "code": code,
            },
        )
        if not envelope_client_errors:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=getattr(exc, "headers", None),
            )

        detail: str = str(exc.detail)
        if catalog is not None and _detail_is_the_framework_default(exc):
            locale = catalog.negotiate(
                request.headers.get("accept-language"),
                default_locale=default_locale,
            )
            localized = catalog.resolve(code, locale)
            if localized is not None:
                detail = localized
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": detail,
                "code": code,
                "details": ({"request_id": request_id} if request_id else {}),
            },
            headers=getattr(exc, "headers", None),
        )

    return _handler


def make_validation_exception_handler(
    *,
    log_level: int = logging.INFO,
    catalog: MessageCatalog | None = None,
    default_locale: str = DEFAULT_LOCALE,
    logger: logging.Logger | None = None,
) -> ValidationExceptionHandler:
    """Build the handler for FastAPI's ``RequestValidationError``.

    The 422 is the error a client sees most often and was the only one
    outside the SDK's envelope: FastAPI intercepts
    ``RequestValidationError`` in its own default handler, so it answered
    with pydantic's raw list and no ``code``, and never passed through
    the :class:`MessageCatalog` a service configured.

    Two things this handler changes, both measured:

    * **The envelope.** The response is
      ``{"detail", "code": "VALIDATION_ERROR", "details": {"errors": [...]}}``,
      so a client parses one shape for every failure instead of two.
    * **The messages.** Each entry's message comes from the catalog,
      keyed by the pydantic error *type* rather than by field, so a new
      field on a schema is translated the day it is added. A type the
      catalog does not carry keeps pydantic's own ``msg``, which is
      already the English sentence — so English needs no table and
      cannot drift from upstream.

    It also stops echoing the submitted value. FastAPI's default 422
    includes ``input`` for every failing field: measured, a
    ``password`` field that fails ``min_length`` puts the password in the
    response body, and ``SecretStr`` does **not** prevent it, because
    validation runs before the secret wrapper is built. ``details``
    carries ``loc`` and ``type`` and never ``input``.

    Args:
        log_level (int): Level for the log line. Defaults to
            :data:`logging.INFO`, since a 422 is normal client flow.
        catalog (MessageCatalog | None): Catalog used to localize each
            entry. ``None`` keeps pydantic's own messages.
        default_locale (str): Locale used when ``Accept-Language`` is
            absent or matches nothing in the catalog.
        logger (logging.Logger | None): Where this handler logs.
            ``None`` uses the SDK's own logger.

    Returns:
        ValidationExceptionHandler: An async
        ``(request, exc) -> JSONResponse`` callable ready to pass to
        :meth:`FastAPI.add_exception_handler`.
    """
    log: logging.Logger = _DEFAULT_LOGGER if logger is None else logger

    async def _handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = (
            get_request_id()
            or request.headers.get("X-Request-ID")
            or request.headers.get("x-request-id")
        )
        locale: str = default_locale
        if catalog is not None:
            locale = catalog.negotiate(
                request.headers.get("accept-language"),
                default_locale=default_locale,
            )

        errors: list[dict[str, Any]] = []
        for error in exc.errors():
            error_type = str(error.get("type", ""))
            message = str(error.get("msg", ""))
            if catalog is not None:
                localized = catalog.resolve(
                    f"{VALIDATION_KEY_PREFIX}{error_type}",
                    locale,
                    error.get("ctx"),
                )
                if localized is not None:
                    message = localized
            errors.append(
                {
                    "loc": list(error.get("loc", ())),
                    "type": error_type,
                    "msg": message,
                },
            )

        detail: str = "Validation error"
        if catalog is not None:
            localized_detail = catalog.resolve(VALIDATION_ERROR_CODE, locale)
            if localized_detail is not None:
                detail = localized_detail

        log.log(
            log_level,
            "Request validation failed during %s %s: %d error(s)",
            request.method,
            request.url.path,
            len(errors),
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "status_code": 422,
                "code": VALIDATION_ERROR_CODE,
            },
        )

        details: dict[str, Any] = {"errors": errors}
        if request_id:
            details["request_id"] = request_id
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "detail": detail,
                    "code": VALIDATION_ERROR_CODE,
                    "details": details,
                },
            ),
        )

    return _handler


def register_exception_handlers(
    app: FastAPI,
    *,
    log_traceback: bool = True,
    include_traceback: bool = False,
    log_level: int = logging.ERROR,
    catalog: MessageCatalog | None = None,
    default_locale: str = DEFAULT_LOCALE,
    logger: logging.Logger | None = None,
    on_server_error: ServerErrorCallback | None = None,
    envelope_validation_errors: bool = False,
    envelope_client_errors: bool = False,
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
        catalog (MessageCatalog | None): When set, the
            :class:`AppException` handler localizes ``detail`` against
            this catalog (see :func:`make_app_exception_handler`). Use
            :func:`tempest_fastapi_sdk.default_message_catalog` for the
            built-in PT-BR + EN strings, optionally
            :meth:`MessageCatalog.merge`-d with domain codes.
        default_locale (str): Locale used when ``Accept-Language`` is
            absent or unmatched. Defaults to ``"pt-BR"``.
        logger (logging.Logger | None): Where every handler logs.
            ``None`` uses the SDK's own logger, which a service
            configuring ``LogUtils(..., scope="logger")`` does not cover.
            Pass ``LogUtils(...).logger``. See
            :func:`make_unhandled_exception_handler` for the measurement.
        on_server_error (ServerErrorCallback | None): Called with
            ``(request, exc)`` after any 5xx response is built, as a
            background task. Fires on all three 5xx paths and never on a
            4xx; a failure inside it is logged and swallowed.
        envelope_validation_errors (bool): Whether to register the
            :class:`fastapi.exceptions.RequestValidationError` handler,
            putting the ``422`` in the envelope and localizing it by
            pydantic error type. Also rewrites the ``HTTPValidationError``
            component of the OpenAPI schema so it does not contradict the
            runtime. Off by default: the 422 body changes shape, which
            breaks a client that models ``detail`` as an array.
        envelope_client_errors (bool): Whether **every** 4xx answers the
            envelope — the ``422`` included, so this implies
            ``envelope_validation_errors``. A raw
            ``HTTPException(403)``, the ``401`` from a security
            dependency, an unknown route and a wrong method all reach
            this SDK's handler and answered a bare
            ``{"detail": "<English phrase>"}`` with no ``code`` before
            v0.285.0. Off by default for the same reason as above.

            A ``detail`` the caller wrote is **preserved**; only the
            framework's own status phrase is localized. See
            :func:`make_http_exception_handler`.

    Notes:
        Starlette types ``add_exception_handler`` to accept only callables
        keyed by the broad ``Exception``, while these handlers narrow their
        second argument to ``AppException`` / ``StarletteHTTPException`` for
        the SDK consumer's benefit. The ``type: ignore`` at each call is
        therefore safe: the narrowing is exactly what the registration key
        guarantees.
    """
    app.add_exception_handler(
        AppException,
        make_app_exception_handler(  # type: ignore[arg-type]
            log_level=log_level,
            catalog=catalog,
            default_locale=default_locale,
            logger=logger,
            on_server_error=on_server_error,
        ),
    )
    app.add_exception_handler(
        StarletteHTTPException,
        make_http_exception_handler(  # type: ignore[arg-type]
            log_traceback=log_traceback,
            log_level=log_level,
            logger=logger,
            on_server_error=on_server_error,
            catalog=catalog,
            default_locale=default_locale,
            envelope_client_errors=envelope_client_errors,
        ),
    )
    app.add_exception_handler(
        Exception,
        make_unhandled_exception_handler(
            log_traceback=log_traceback,
            include_traceback=include_traceback,
            log_level=log_level,
            logger=logger,
            on_server_error=on_server_error,
        ),
    )
    if envelope_validation_errors or envelope_client_errors:
        app.add_exception_handler(
            RequestValidationError,
            make_validation_exception_handler(  # type: ignore[arg-type]
                catalog=catalog,
                default_locale=default_locale,
                logger=logger,
            ),
        )
        describe_validation_envelope(app)


__all__: list[str] = [
    "VALIDATION_ERROR_CODE",
    "ServerErrorCallback",
    "ValidationExceptionHandler",
    "app_exception_handler",
    "make_app_exception_handler",
    "make_http_exception_handler",
    "make_unhandled_exception_handler",
    "make_validation_exception_handler",
    "register_exception_handlers",
]
