"""Exception handling for HTML routes: error page on reads, flash on actions.

The same domain exception needs three answers depending on who asked:

* a JSON client gets the SDK envelope, as always;
* a browser **reading** an HTML screen (``GET`` / ``HEAD``) gets an error
  page with the right status;
* a browser **submitting** a form (any other method) goes back to the
  screen it came from, with the message as a flash notice.

:func:`register_html_error_handlers` wraps the handlers that
:func:`tempest_fastapi_sdk.register_exception_handlers` installed, so the
JSON path — logging, localization through the catalog, ``on_server_error``
— keeps running for every request, and HTML routes only change the shape
of the response.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from tempest_fastapi_sdk.api.handlers import make_app_exception_handler
from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.ssr.flashes import flash, flash_enabled
from tempest_fastapi_sdk.ssr.redirects import redirect_back
from tempest_fastapi_sdk.ssr.response import html_response
from tempest_fastapi_sdk.ui.pages.error import ErrorPage

ExceptionHandler = Callable[[Request, Any], Response | Awaitable[Response]]
"""A Starlette exception handler, sync or async."""

GENERIC_ERROR_MESSAGE: Final[str] = "Não foi possível concluir a operação."
"""Message shown when the JSON error response carries no string ``detail``."""

_READ_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD"})
_SKIPPED_HEADERS: Final[frozenset[str]] = frozenset(
    {"content-length", "content-type"},
)


def _read_envelope(response: Response) -> tuple[str, str | None]:
    """Read the message and code out of a JSON error response.

    Args:
        response (Response): The response the JSON handler built.

    Returns:
        tuple[str, str | None]: The ``detail`` as text (a non-string
        detail, like FastAPI's list of validation errors, is replaced by
        a generic phrase) and the ``code`` when the envelope has one.
    """
    try:
        body: Any = json.loads(bytes(response.body))
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return GENERIC_ERROR_MESSAGE, None
    detail = body.get("detail")
    code = body.get("code")
    return (
        detail if isinstance(detail, str) and detail else GENERIC_ERROR_MESSAGE,
        code if isinstance(code, str) else None,
    )


def register_html_error_handlers(
    app: FastAPI,
    *,
    prefixes: Sequence[str] = (),
    tags: Sequence[str] = (),
    error_page: type[ErrorPage] = ErrorPage,
    fallback: str | None = None,
) -> None:
    """Answer HTML routes with an error page or a flash-and-redirect.

    Call it **after** :func:`tempest_fastapi_sdk.register_exception_handlers`
    (when the service uses it). It wraps the :class:`AppException` and
    ``HTTPException`` handlers registered on ``app``: the wrapped handler
    always runs first — so logging, catalog localization and
    ``on_server_error`` behave exactly as for the API — and then, for a
    request to an HTML route:

    * ``GET`` / ``HEAD`` answers ``error_page`` rendered with the same
      status code and the (localized) ``detail``;
    * any other method queues the ``detail`` as an ``"error"`` flash and
      answers ``303`` back to the ``Referer`` through
      :func:`~tempest_fastapi_sdk.ssr.redirect_back`, validated against
      the matched prefix. Without :class:`~tempest_fastapi_sdk.ssr.FlashMiddleware`
      the message would be lost, so the error page is rendered instead.

    Every other request keeps the JSON response untouched.

    Args:
        app (FastAPI): The application.
        prefixes (Sequence[str]): Path prefixes of the HTML routes
            (``"/admin"`` matches ``/admin`` and ``/admin/...``, not
            ``/administrator``).
        tags (Sequence[str]): Router tags that mark a route as HTML. Only
            a matched route has tags, so an unknown path is recognised by
            prefix alone.
        error_page (type[ErrorPage]): The page class rendered on reads,
            built through :meth:`ErrorPage.from_error`. Combine
            :class:`~tempest_fastapi_sdk.ui.pages.ErrorPage` with the
            service's base page to inherit its chrome and head; set its
            ``title_template`` to change the ``"Erro {status_code}"``
            title.
        fallback (str | None): Where the redirect goes when the
            ``Referer`` is refused. ``None`` uses the matched prefix, or
            ``"/"`` for a route matched by tag.

    Raises:
        ValueError: When neither ``prefixes`` nor ``tags`` is given —
            there would be no HTML route to handle.
    """
    if not prefixes and not tags:
        raise ValueError(
            "register_html_error_handlers() needs prefixes= or tags= "
            "to recognise the HTML routes.",
        )
    normalized = tuple(prefix.rstrip("/") or "/" for prefix in prefixes)
    tag_set = frozenset(tags)

    def matched_prefix(request: Request) -> str | None:
        """Return the prefix (or ``"/"`` for a tag) that marks the route as HTML.

        Args:
            request (Request): The failing request.

        Returns:
            str | None: The matched prefix, ``"/"`` when only a tag
            matched, or ``None`` for a non-HTML route.
        """
        path = request.url.path
        for prefix in normalized:
            if prefix == "/" or path == prefix or path.startswith(prefix + "/"):
                return prefix
        route = request.scope.get("route")
        route_tags = getattr(route, "tags", None) or ()
        if tag_set and any(tag in tag_set for tag in route_tags):
            return "/"
        return None

    def wrap(inner: ExceptionHandler) -> ExceptionHandler:
        """Wrap a JSON handler with the HTML branch.

        Args:
            inner (ExceptionHandler): The handler already registered.

        Returns:
            ExceptionHandler: The dispatching handler.
        """

        async def handler(request: Request, exc: Any) -> Response:
            """Run the JSON handler, then reshape the answer for HTML routes.

            Args:
                request (Request): The failing request.
                exc (Any): The exception raised.

            Returns:
                Response: The JSON response for API routes; an error page
                or a redirect with a flash for HTML routes.
            """
            outcome = inner(request, exc)
            json_response = await outcome if inspect.isawaitable(outcome) else outcome
            prefix = matched_prefix(request)
            if prefix is None:
                return json_response
            detail, code = _read_envelope(json_response)
            status_code = json_response.status_code
            if request.method not in _READ_METHODS and flash_enabled(request):
                flash(request, detail, "error")
                response: Response = redirect_back(
                    request,
                    fallback=fallback if fallback is not None else prefix,
                    allowed_prefix=prefix,
                )
            else:
                response = html_response(
                    error_page.from_error(
                        status_code=status_code,
                        detail=detail,
                        code=code,
                    ),
                    status_code=status_code,
                )
                for name, value in json_response.headers.items():
                    if name.lower() not in _SKIPPED_HEADERS:
                        response.headers.append(name, value)
            response.background = json_response.background
            return response

        return handler

    app_handler: ExceptionHandler = app.exception_handlers.get(
        AppException,
        make_app_exception_handler(),
    )
    http_handler: ExceptionHandler | None = app.exception_handlers.get(
        StarletteHTTPException,
    )
    app.add_exception_handler(AppException, wrap(app_handler))
    if http_handler is not None:
        app.add_exception_handler(StarletteHTTPException, wrap(http_handler))


__all__: list[str] = ["GENERIC_ERROR_MESSAGE", "register_html_error_handlers"]
