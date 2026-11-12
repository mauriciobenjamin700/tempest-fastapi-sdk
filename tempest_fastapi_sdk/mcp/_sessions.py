"""Bind a stateful MCP session to the principal that opened it.

Private to :func:`~tempest_fastapi_sdk.mcp.mount_mcp`. The MCP transport
hands out an ``Mcp-Session-Id`` on ``initialize`` and serves any later
request that presents it; it does not know who the FastAPI dependency in
front of it authenticated. :class:`SessionBindings` sits between the two:
it records ``session id -> owner`` when the id is issued and answers every
later request whose principal is not the owner as if the session did not
exist.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from starlette.responses import Response

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_ID_HEADER: str = "mcp-session-id"
"""The header the streamable HTTP transport issues and reads the session in."""

SESSION_BINDING_IDLE_SECONDS: float = 1800.0
"""Seconds a binding with no request in flight is kept after its last use.

The same 30 minutes the MCP SDK (``mcp`` 2.3.0) waits before it reaps an
idle stateful session, so a binding outlives the session it guards. A
binding dropped while the session is still alive fails closed: the id then
answers ``404`` and the client opens a new session, which the MCP
specification requires it to do on a ``404``.
"""

SESSION_NOT_FOUND_BODY: bytes = (
    b'{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Session not found"}}'
)
"""The body the MCP SDK answers for an unknown or expired session id.

Ported from ``mcp`` 2.3.0's ``streamable_http_manager._error_response(
"Session not found", 404)``, byte for byte, so a refused session cannot be
told apart from one that never existed.
"""


def principal_key(principal: Any) -> str | None:
    """Derive the stable identity a session is bound to.

    The principal is whatever ``mount_mcp(principal=...)`` resolved, a new
    object on every request, so the binding compares a key derived from it:

    * ``None`` (anonymous, or no ``principal=``) is ``None``.
    * A mapping — the claims of ``make_bearer_token_dependency`` — is keyed
      by its ``sub`` claim, which survives a token refresh while ``exp`` and
      ``iat`` do not.
    * An object with a non-``None`` ``id`` attribute — the user model of
      ``make_jwt_user_dependency`` — is keyed by its type and ``id``.
    * A ``str``, ``int`` or ``UUID`` is keyed by its type and value.

    Args:
        principal (Any): The resolved principal of the current request.

    Returns:
        str | None: The owner key, or ``None`` for an anonymous caller.

    Raises:
        TypeError: When no stable identity can be derived — a mapping
            without ``sub``, or an object without ``id``. Serve such a
            principal with ``stateless=True``.
    """
    if principal is None:
        return None
    if isinstance(principal, Mapping):
        subject = principal.get("sub")
        if subject is None:
            raise TypeError(
                "stateless=False binds an MCP session to its principal, and a "
                "mapping principal needs a 'sub' claim to be identified",
            )
        return f"sub:{subject}"
    kind = type(principal)
    identifier = getattr(principal, "id", None)
    if identifier is not None:
        return f"{kind.__module__}.{kind.__qualname__}:{identifier}"
    if isinstance(principal, str | int | UUID):
        return f"{kind.__qualname__}:{principal}"
    raise TypeError(
        "stateless=False binds an MCP session to its principal, and a "
        f"{kind.__name__} has no 'id' to identify it by",
    )


@dataclass
class _Binding:
    """One issued session and what keeps it alive.

    Attributes:
        owner (str | None): The :func:`principal_key` of the opener.
        in_flight (int): Requests on this session not yet answered; a
            long-lived ``GET`` stream counts for as long as it is open.
        last_seen (float): Clock reading when the last request ended.
    """

    owner: str | None
    in_flight: int
    last_seen: float


def _session_id(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Read the ``Mcp-Session-Id`` header from raw ASGI headers.

    Args:
        headers (list[tuple[bytes, bytes]]): Lower-cased header pairs.

    Returns:
        str | None: The session id, or ``None`` when absent.
    """
    wanted = SESSION_ID_HEADER.encode()
    for name, value in headers:
        if name.lower() == wanted:
            return value.decode("latin-1")
    return None


class SessionBindings:
    """The ``session id -> owner`` registry of one mounted endpoint.

    Per process, like the stateful sessions of the MCP transport itself:
    behind several workers a session only works when its requests reach the
    worker that opened it, and so does its binding.
    """

    def __init__(
        self,
        *,
        idle_seconds: float = SESSION_BINDING_IDLE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start an empty registry.

        Args:
            idle_seconds (float): How long a binding with nothing in flight
                is kept after its last request.
            clock (Callable[[], float]): Monotonic clock, injectable for tests.
        """
        self._idle_seconds: float = idle_seconds
        self._clock: Callable[[], float] = clock
        self._bindings: dict[str, _Binding] = {}

    def __len__(self) -> int:
        """Return how many sessions are bound.

        Returns:
            int: The number of live bindings.
        """
        return len(self._bindings)

    def _prune(self) -> None:
        """Forget bindings idle for longer than ``idle_seconds``."""
        horizon = self._clock() - self._idle_seconds
        stale = [
            session_id
            for session_id, binding in self._bindings.items()
            if binding.in_flight == 0 and binding.last_seen < horizon
        ]
        for session_id in stale:
            del self._bindings[session_id]

    async def serve(
        self,
        app: ASGIApp,
        owner: str | None,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Serve one MCP request, enforcing the session's owner.

        A request without ``Mcp-Session-Id`` is passed through, and the id
        the transport issues in its response is bound to ``owner``. A
        request with one is answered ``404`` with the transport's own
        ``Session not found`` body when the id is not bound to ``owner`` —
        bound to someone else, or never issued here. ``404`` is what the MCP
        specification (streamable HTTP, session management) prescribes for a
        session the server no longer serves, so a conforming client opens a
        new one, and it reveals nothing ``403`` would: that the id exists.
        The binding is released when a ``DELETE`` ends the session or the
        transport answers ``404`` for it.

        Args:
            app (ASGIApp): The MCP transport.
            owner (str | None): The :func:`principal_key` of the caller.
            scope (Scope): The ASGI scope of the request.
            receive (Receive): Its receive channel.
            send (Send): Its send channel.
        """
        session_id = _session_id(scope["headers"])
        if session_id is None:
            await self._open(app, owner, scope, receive, send)
            return
        binding = self._bindings.get(session_id)
        if binding is None or binding.owner != owner:
            refusal = Response(
                SESSION_NOT_FOUND_BODY,
                status_code=404,
                media_type="application/json",
            )
            await refusal(scope, receive, send)
            return
        status: list[int] = []

        async def watch(message: Message) -> None:
            if message["type"] == "http.response.start":
                status.append(message["status"])
            await send(message)

        binding.in_flight += 1
        try:
            await app(scope, receive, watch)
        finally:
            binding.in_flight -= 1
            binding.last_seen = self._clock()
        ended = status and (
            status[0] == 404 or (scope["method"] == "DELETE" and status[0] < 300)
        )
        if ended:
            self._bindings.pop(session_id, None)

    async def _open(
        self,
        app: ASGIApp,
        owner: str | None,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Pass an opening request through and bind the id it is issued.

        The binding is recorded when the response headers go out, before the
        client can read the id and present it again.

        Args:
            app (ASGIApp): The MCP transport.
            owner (str | None): The :func:`principal_key` of the caller.
            scope (Scope): The ASGI scope of the request.
            receive (Receive): Its receive channel.
            send (Send): Its send channel.
        """
        self._prune()
        opened: list[_Binding] = []

        async def bind(message: Message) -> None:
            if message["type"] == "http.response.start":
                issued = _session_id(list(message.get("headers", [])))
                if issued is not None:
                    binding = _Binding(owner=owner, in_flight=1, last_seen=0.0)
                    self._bindings[issued] = binding
                    opened.append(binding)
            await send(message)

        try:
            await app(scope, receive, bind)
        finally:
            for binding in opened:
                binding.in_flight -= 1
                binding.last_seen = self._clock()


__all__: list[str] = [
    "SESSION_BINDING_IDLE_SECONDS",
    "SESSION_ID_HEADER",
    "SESSION_NOT_FOUND_BODY",
    "SessionBindings",
    "principal_key",
]
