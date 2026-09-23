"""Build the unhandled-error envelope inside the middleware stack."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ErrorEnvelopeHandler = Callable[[Request, Exception], Awaitable[Response]]
"""Async ``(request, exc) -> Response`` that renders the 500 envelope."""

_ENVELOPED_FLAG: Final[str] = "__tempest_error_enveloped__"
"""Attribute set on an exception once its envelope was sent.

An attribute on the exception, not a key in ``scope``: a middleware may
hand a copied scope downstream, and the exception is the one object
``ServerErrorMiddleware`` is guaranteed to see. Weak references are not
an option, since built-in exceptions do not support them.
"""


def _skip_enveloped(handler: ErrorEnvelopeHandler) -> ErrorEnvelopeHandler:
    """Wrap ``handler`` so it ignores an exception already enveloped.

    ``ServerErrorMiddleware`` calls its handler for every exception that
    reaches it, **even when a response already started**, and only skips
    sending what the handler returns. Registered bare, the handler would
    log the failure and fire ``on_server_error`` a second time for every
    500 :class:`ErrorEnvelopeMiddleware` answered.

    Args:
        handler (ErrorEnvelopeHandler): The handler to guard.

    Returns:
        ErrorEnvelopeHandler: A handler that returns an empty ``500``
        without calling ``handler`` for an enveloped exception; that
        response is never sent, because the envelope already was.
    """

    async def _guarded(request: Request, exc: Exception) -> Response:
        if getattr(exc, _ENVELOPED_FLAG, False) is True:
            return Response(status_code=500)
        return await handler(request, exc)

    return _guarded


class ErrorEnvelopeMiddleware:
    """Answer an unhandled exception from inside the user middleware stack.

    Starlette runs the handler registered for :class:`Exception` inside
    ``ServerErrorMiddleware``, the outermost layer of the stack, so the
    response it builds never passes back through ``CORSMiddleware``,
    ``RequestIDMiddleware`` or any other middleware the application
    added. The browser then discards the 500 for lack of
    ``Access-Control-Allow-Origin`` and ``fetch`` rejects with
    ``TypeError: Failed to fetch``: no status, no ``code``, no
    ``X-Request-ID`` to correlate with the server log.

    This layer catches the exception below every user middleware and
    sends the envelope from there, so each of them decorates the 500 as
    it decorates a 200. It never decides CORS itself: an origin outside
    the allow-list still gets no header, because ``CORSMiddleware`` is
    still the one answering that question.

    After sending the envelope the exception is **re-raised**, the same
    contract ``ServerErrorMiddleware`` keeps: the ASGI server still logs
    it, and a ``TestClient`` built with the default
    ``raise_server_exceptions=True`` still raises it in the test. On the
    way out ``ServerErrorMiddleware`` sees a response already started and
    does not answer a second time. It still calls its handler, though, so
    the exception is flagged first and the handler that
    :func:`~tempest_fastapi_sdk.register_exception_handlers` leaves there
    skips it: each 500 is logged and reported to ``on_server_error`` once.

    When the failure happens after the response already started (a
    stream that breaks mid-body), the status is already on the wire and a
    second response cannot be sent, so the exception is re-raised
    untouched.

    :func:`~tempest_fastapi_sdk.register_exception_handlers` installs this
    layer itself, as the innermost user middleware, so its position does
    not depend on the order of the ``add_middleware`` / ``apply_cors``
    calls around it.

    Args:
        app (ASGIApp): The downstream ASGI application.
        handler (ErrorEnvelopeHandler): Renders the envelope for the
            exception; normally the callable built by
            :func:`~tempest_fastapi_sdk.make_unhandled_exception_handler`.
    """

    def __init__(self, app: ASGIApp, handler: ErrorEnvelopeHandler) -> None:
        """Wrap ``app`` with the envelope handler.

        Args:
            app (ASGIApp): The downstream ASGI application.
            handler (ErrorEnvelopeHandler): Renders the envelope.
        """
        self.app: ASGIApp = app
        self.handler: ErrorEnvelopeHandler = handler

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the downstream app and envelope an exception it leaks.

        Args:
            scope (Scope): The ASGI connection scope.
            receive (Receive): The ASGI receive channel.
            send (Send): The ASGI send channel.

        Raises:
            Exception: The exception the downstream app raised, always,
                after the envelope was sent (or untouched, when the
                response had already started).
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started: bool = False

        async def _send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            if started:
                raise
            response = await self.handler(Request(scope, receive), exc)
            await response(scope, receive, send)
            setattr(exc, _ENVELOPED_FLAG, True)
            raise


__all__: list[str] = [
    "ErrorEnvelopeHandler",
    "ErrorEnvelopeMiddleware",
]
