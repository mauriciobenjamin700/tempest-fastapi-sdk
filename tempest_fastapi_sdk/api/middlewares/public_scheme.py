"""Stamp the deployment's public scheme on every request scope."""

from __future__ import annotations

from typing import Literal

from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_SCHEMES: frozenset[str] = frozenset({"http", "https", ""})
"""Values ``PublicSchemeMiddleware`` accepts; ``""`` turns it off."""

WEBSOCKET_SCHEMES: dict[str, str] = {"http": "ws", "https": "wss"}
"""The websocket spelling of each HTTP scheme, as ASGI writes it."""


class PublicSchemeMiddleware:
    """Overwrite ``scope["scheme"]`` with the scheme the browser used.

    Starlette builds every absolute URL — ``request.url_for``, and so
    the ``action`` of each admin panel form — from ``scope["scheme"]``.
    Behind a proxy that terminates TLS that scope says ``http``, because
    the proxy speaks plain HTTP to the app, and an HTTPS page renders
    ``<form action="http://.../admin/login">``: the browser flags the
    password field as not secure.

    The usual fix, uvicorn's ``--forwarded-allow-ips``, trusts
    ``X-Forwarded-Proto`` and ``X-Forwarded-For`` from the listed peers.
    When the app port is published, a request that skips the proxy
    arrives from the Docker gateway, so trusting that subnet lets any
    client forge the forwarded headers — including the client address
    the rate limit keys on. Here the scheme is declared in configuration
    (``SERVER_PUBLIC_SCHEME``) and nothing the client sends changes it.

    Register it outermost, so every layer below builds URLs in the same
    scheme. It writes one scope key and logs nothing. ``lifespan`` scopes
    pass through untouched, and websocket scopes get ``ws``/``wss``.

    Attributes:
        app (ASGIApp): The wrapped application.
        scheme (str): ``"http"``, ``"https"``, or ``""`` for passthrough.
    """

    def __init__(
        self,
        app: ASGIApp,
        scheme: Literal["http", "https", ""] = "",
    ) -> None:
        """Wrap ``app`` and stamp ``scheme`` on every connection.

        Args:
            app (ASGIApp): The application to wrap.
            scheme (Literal["http", "https", ""]): The scheme the service
                is publicly reached by. ``""`` leaves the scope as the
                connection describes it, which is what local development
                without a proxy wants.

        Raises:
            ValueError: When ``scheme`` is not ``"http"``, ``"https"`` or
                ``""``.
        """
        if scheme not in PUBLIC_SCHEMES:
            raise ValueError(f"scheme={scheme!r} is not one of 'http', 'https' or ''.")
        self.app: ASGIApp = app
        self.scheme: str = scheme

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Stamp the scheme and hand the connection down the stack.

        Args:
            scope (Scope): The ASGI connection scope.
            receive (Receive): The ASGI ``receive`` callable.
            send (Send): The ASGI ``send`` callable.
        """
        if self.scheme:
            if scope["type"] == "http":
                scope["scheme"] = self.scheme
            elif scope["type"] == "websocket":
                scope["scheme"] = WEBSOCKET_SCHEMES[self.scheme]
        await self.app(scope, receive, send)


__all__: list[str] = [
    "PUBLIC_SCHEMES",
    "WEBSOCKET_SCHEMES",
    "PublicSchemeMiddleware",
]
