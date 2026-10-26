"""Bundled SSE router: authenticated streams through a short-lived ticket.

The browser ``EventSource`` cannot send a header, so a stream route that
reads ``Authorization: Bearer`` refuses every browser client, and the usual
workaround — the access token in the query string — puts a credential that
authorizes the **whole API** into access logs, history and ``Referer``.

:func:`make_sse_router` closes both ends. The client trades its session
for a ticket on ``POST <prefix>/ticket`` (a normal authenticated call,
header or cookie), then opens ``GET <prefix>/stream?ticket=...``. The
ticket is a JWT with ``typ="stream"`` that names the channel it opens and
lives for :data:`DEFAULT_SSE_TICKET_TTL`. Every guard built on
:func:`~tempest_fastapi_sdk.token_type_allowed` (``current_user_dependency``,
``make_jwt_user_dependency``, ``make_bearer_token_dependency``) refuses that
``typ``, so a leaked ticket subscribes to one channel for a minute and
authorizes nothing else. A ticket is not single-use: until it expires it
opens the stream as many times as it is presented.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk.api.dependencies.auth import _resolve_tokens
from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException
from tempest_fastapi_sdk.sse.broker import SSEBroker
from tempest_fastapi_sdk.sse.schemas import SSETicketResponseSchema
from tempest_fastapi_sdk.utils.token_types import STREAM_TOKEN_TYPE

if TYPE_CHECKING:
    from tempest_fastapi_sdk.utils.jwt import JWTUtils

DEFAULT_SSE_TICKET_TTL: Final[timedelta] = timedelta(seconds=60)
"""How long a ticket can open a stream. Long enough for the round trip
between ``POST /ticket`` and ``new EventSource(...)``, short enough that a
URL lifted from a log is useless soon after."""

SSE_CHANNEL_CLAIM: Final[str] = "ch"
"""Claim of the ticket that names the broker channel it opens."""

SSE_TICKET_ERROR: Final[str] = "SSE ticket is missing or invalid"
"""Message of the ``401`` the stream route answers without a usable
credential."""


def _user_id_channel(user: Any) -> str:
    """Return the default channel of a user: its ``id`` as a string.

    Args:
        user (Any): The authenticated user.

    Returns:
        str: ``str(user.id)``.
    """
    return str(user.id)


async def _no_fallback_user() -> None:
    """Stand in for ``fallback_user`` when the router has none.

    Returns:
        None: Always; only the ticket opens the stream.
    """
    return None


def make_sse_router(
    broker: SSEBroker | Callable[[Request], SSEBroker],
    *,
    tokens: JWTUtils | Callable[[Request], JWTUtils],
    current_user: Callable[..., Coroutine[Any, Any, Any]],
    fallback_user: Callable[..., Coroutine[Any, Any, Any]] | None = None,
    channel_for: Callable[[Any], str] | None = None,
    prefix: str = "/sse",
    tag: str = "SSE",
    ticket_ttl: timedelta | None = None,
    ticket_param: str = "ticket",
) -> APIRouter:
    """Build the router that issues SSE tickets and serves the stream.

    Mounts two routes:

    * ``POST <prefix>/ticket`` — guarded by ``current_user``; answers a
      :class:`SSETicketResponseSchema` whose ticket opens the channel
      ``channel_for(user)``.
    * ``GET <prefix>/stream`` — reads ``?<ticket_param>=``, checks the
      signature, the expiry and ``typ="stream"``, and returns
      ``broker.response(channel)``. Without a ticket it asks
      ``fallback_user``; with neither it answers ``401``.

    The channel travels **inside** the signed ticket, so the stream route
    opens no database session and the client cannot pick a channel of
    its own. A deactivated account stops getting tickets at once (that is
    ``current_user``'s check) and loses the stream on its next reconnect.

    With :class:`~tempest_fastapi_sdk.UserAuthService`, prefer
    :meth:`~tempest_fastapi_sdk.UserAuthService.sse_router`, which builds
    ``tokens``, ``current_user`` and ``fallback_user`` from the service.

    Args:
        broker (SSEBroker | Callable[[Request], SSEBroker]): The broker the
            stream subscribes to, or a factory called with each request
            that returns it (``lambda request: request.app.state.broker``).
        tokens (JWTUtils | Callable[[Request], JWTUtils]): The helper that
            signs and verifies tickets — the same one that signs the
            access token, or a factory returning it.
        current_user (Callable[..., Coroutine[Any, Any, Any]]): The
            dependency that authenticates ``POST <prefix>/ticket`` and
            yields the user, raising on a missing or invalid session.
        fallback_user (Callable[..., Coroutine[Any, Any, Any]] | None): A
            **soft** user dependency the stream route asks when no ticket
            came — it returns ``None`` instead of raising. Lets a client
            that can send ``Authorization: Bearer`` skip the ticket, and,
            built with ``query_param``, keeps an existing
            ``?access_token=`` client working while it migrates. ``None``
            (default) makes the ticket the only way in.
        channel_for (Callable[[Any], str] | None): Maps the authenticated
            user to the broker channel. ``None`` (default) uses
            ``str(user.id)``.
        prefix (str): URL prefix. Defaults to ``"/sse"``.
        tag (str): OpenAPI tag. Defaults to ``"SSE"``.
        ticket_ttl (timedelta | None): Lifetime of a ticket. ``None``
            (default) uses :data:`DEFAULT_SSE_TICKET_TTL` (60 seconds).
        ticket_param (str): Query parameter the stream route reads the
            ticket from. Defaults to ``"ticket"``.

    Returns:
        APIRouter: The router, ready for ``app.include_router``.

    Raises:
        ValueError: When ``ticket_ttl`` is shorter than one second.
    """
    resolved_channel_for = channel_for if channel_for is not None else _user_id_channel
    resolved_ttl = ticket_ttl if ticket_ttl is not None else DEFAULT_SSE_TICKET_TTL
    ttl_seconds = int(resolved_ttl.total_seconds())
    if ttl_seconds < 1:
        raise ValueError("ticket_ttl must be at least one second.")

    def resolve_broker(request: Request) -> SSEBroker:
        """Return the broker for this request.

        Args:
            request (Request): The request being served.

        Returns:
            SSEBroker: ``broker`` itself, or what its factory returns.
        """
        if isinstance(broker, SSEBroker):
            return broker
        return broker(request)

    router = APIRouter(prefix=prefix, tags=[tag])

    @router.post(
        "/ticket",
        response_model=SSETicketResponseSchema,
        summary="Issue an SSE ticket",
        description=(
            "Trade the authenticated session for a short-lived ticket that "
            f"opens `GET {prefix}/stream?{ticket_param}=...`. The ticket "
            "authorizes that stream only; ask for a new one before every "
            "reconnect."
        ),
    )
    async def issue_ticket(
        request: Request,
        user: Any = Depends(current_user),
    ) -> SSETicketResponseSchema:
        """Sign a ticket for the caller's channel.

        Args:
            request (Request): The request being served.
            user (Any): The authenticated user.

        Returns:
            SSETicketResponseSchema: The ticket and its lifetime.
        """
        ticket = _resolve_tokens(tokens, request).encode(
            {"typ": STREAM_TOKEN_TYPE, SSE_CHANNEL_CLAIM: resolved_channel_for(user)},
            ttl=timedelta(seconds=ttl_seconds),
        )
        return SSETicketResponseSchema(ticket=ticket, expires_in=ttl_seconds)

    @router.get(
        "/stream",
        response_class=StreamingResponse,
        summary="Open the event stream",
        description=(
            f"Server-Sent Events. Authenticate with `?{ticket_param}=` from "
            f"`POST {prefix}/ticket`."
        ),
    )
    async def stream(
        request: Request,
        fallback: Any = Depends(fallback_user or _no_fallback_user),
    ) -> StreamingResponse:
        """Subscribe the caller to the channel its credential names.

        Args:
            request (Request): The request being served.
            fallback (Any): What ``fallback_user`` resolved, or ``None``.

        Returns:
            StreamingResponse: The SSE response.

        Raises:
            UnauthorizedException: When the ticket is absent, expired,
                forged or of another ``typ``, and no fallback user
                resolved.
        """
        ticket = request.query_params.get(ticket_param)
        if ticket is not None:
            payload = _resolve_tokens(tokens, request).decode_or_none(ticket)
            channel = None if payload is None else payload.get(SSE_CHANNEL_CLAIM)
            if (
                payload is None
                or payload.get("typ") != STREAM_TOKEN_TYPE
                or not isinstance(channel, str)
            ):
                raise UnauthorizedException(message=SSE_TICKET_ERROR)
        elif fallback is not None:
            channel = resolved_channel_for(fallback)
        else:
            raise UnauthorizedException(message=SSE_TICKET_ERROR)
        return resolve_broker(request).response(channel)

    return router


__all__: list[str] = [
    "DEFAULT_SSE_TICKET_TTL",
    "SSE_CHANNEL_CLAIM",
    "SSE_TICKET_ERROR",
    "make_sse_router",
]
