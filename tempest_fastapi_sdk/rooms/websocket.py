"""Game rooms over WebSocket: one endpoint, JSON control and binary relay.

Text frames carry the JSON control protocol of
:class:`~tempest_fastapi_sdk.rooms.RoomSession`; binary frames carry opaque
game bytes, ``[u8 slot][payload]``, so a lockstep game keeps its own binary
protocol and only swaps the byte channel. Liveness and the frame-size cap
come from :func:`~tempest_fastapi_sdk.websockets.heartbeat`: any inbound
frame proves the client alive, the server's ``{"type": "ping"}`` expects a
``{"type": "pong"}`` only from an idle client, and a client silent past
``WS_HEARTBEAT_TIMEOUT_SECONDS`` is closed with ``4408`` — its seat then
waits ``reconnect_grace_seconds`` for a ``resume``.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, WebSocket, status
from starlette.websockets import WebSocketDisconnect, WebSocketState

from tempest_fastapi_sdk.rooms.errors import UnauthorizedError
from tempest_fastapi_sdk.rooms.registry import (
    IdentityResolver,
    RoomIdentity,
    RoomRegistry,
)
from tempest_fastapi_sdk.rooms.session import RoomSession, hello_message
from tempest_fastapi_sdk.settings import WebSocketSettings
from tempest_fastapi_sdk.utils.token_types import ACCESS_TOKEN_TYPE
from tempest_fastapi_sdk.websockets.heartbeat import heartbeat
from tempest_fastapi_sdk.websockets.router import make_jwt_bearer_resolver

if TYPE_CHECKING:
    from tempest_fastapi_sdk.utils.jwt import JWTUtils

UNAUTHORIZED_CLOSE_CODE: int = 4401
"""Close code for a handshake without a valid token, when one is required."""

DEFAULT_GAME_ROOM_PATH: str = "/rooms/ws"
"""Where :func:`make_game_room_router` mounts its endpoint by default."""


class WebSocketRoomPeer:
    """A :class:`~tempest_fastapi_sdk.rooms.RoomPeer` over a FastAPI socket.

    Attributes:
        ws (WebSocket): The accepted socket.
    """

    def __init__(self, ws: WebSocket) -> None:
        """Wrap a socket.

        Args:
            ws (WebSocket): An accepted socket.
        """
        self.ws: WebSocket = ws

    async def send_json(self, message: dict[str, Any], /) -> None:
        """Send one control message as a text frame.

        Args:
            message (dict[str, Any]): The message.
        """
        await self.ws.send_text(json.dumps(message, separators=(",", ":")))

    async def send_bytes(self, data: bytes, /) -> None:
        """Send one binary relay frame.

        Args:
            data (bytes): The frame.
        """
        await self.ws.send_bytes(data)

    async def close(self, code: int, reason: str, /) -> None:
        """Close the socket.

        Args:
            code (int): Close code.
            reason (str): Short reason.
        """
        if self.ws.application_state != WebSocketState.DISCONNECTED:
            await self.ws.close(code=code, reason=reason)


def make_jwt_room_identity_resolver(
    tokens: JWTUtils,
    *,
    name_claim: str = "name",
    accepted: Collection[str] = (ACCESS_TOKEN_TYPE,),
    strict: bool = True,
) -> IdentityResolver:
    """Build the identity resolver for access tokens signed by ``tokens``.

    Built on :func:`~tempest_fastapi_sdk.make_jwt_bearer_resolver`, so it
    refuses what that one refuses: an expired or forged token, and — the
    reason not to hand-write ``decode`` + ``sub`` — a refresh token or an
    MFA-pending token signed by the same secret, which carry ``sub`` too.

    Args:
        tokens (JWTUtils): The helper that signs the application's access
            tokens.
        name_claim (str): Claim holding the display name; missing or not a
            string falls back to the room's ``"Player <n>"``.
        accepted (Collection[str]): Token types that may join a room.
        strict (bool): Refuse a token whose type cannot be determined.

    Returns:
        IdentityResolver: Maps a token to a :class:`RoomIdentity` whose
        ``player_id`` is the token's UUID subject, or ``None``.
    """
    bearer = make_jwt_bearer_resolver(tokens, accepted=accepted, strict=strict)

    async def resolve(token: str) -> RoomIdentity | None:
        """Map ``token`` to an identity, or ``None`` to refuse it.

        Args:
            token (str): The bearer token.

        Returns:
            RoomIdentity | None: The identity, or ``None``.
        """
        user_id = await bearer(token)
        if user_id is None:
            return None
        name = (tokens.decode_or_none(token) or {}).get(name_claim)
        return RoomIdentity(
            player_id=str(user_id), name=name if isinstance(name, str) else ""
        )

    return resolve


def make_game_room_router(
    registry: RoomRegistry,
    *,
    settings: WebSocketSettings,
    path: str = DEFAULT_GAME_ROOM_PATH,
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the router of a game room server.

    One endpoint. With an identity resolver on the registry, the token comes
    in ``?token=`` and a missing or bad one is closed with ``4401`` before
    the handshake completes; without one, players are guests.

    Args:
        registry (RoomRegistry): The rooms; one per game server.
        settings (WebSocketSettings): ``WS_HEARTBEAT_SECONDS``,
            ``WS_HEARTBEAT_TIMEOUT_SECONDS`` and ``WS_MAX_MESSAGE_BYTES``
            (a larger frame is closed with ``1009``).
        path (str): Mount path.
        tags (list[str] | None): OpenAPI tags; defaults to ``["rooms"]``.

    Returns:
        APIRouter: Ready for ``app.include_router``.
    """
    router = APIRouter(tags=list(tags or ["rooms"]))

    @router.websocket(path)
    async def game_room_endpoint(
        ws: WebSocket,
        token: str | None = Query(default=None),
    ) -> None:
        """Serve one player's connection.

        Args:
            ws (WebSocket): The socket.
            token (str | None): Bearer token, when the server requires one.
        """
        identity: RoomIdentity | None = None
        if registry.requires_identity:
            try:
                identity = await registry.identify(token)
            except UnauthorizedError:
                await ws.close(code=UNAUTHORIZED_CLOSE_CODE)
                return
        await ws.accept()
        peer = WebSocketRoomPeer(ws)
        session = RoomSession(registry, peer, identity=identity)
        async with heartbeat(
            ws, settings=settings, max_message_bytes=settings.WS_MAX_MESSAGE_BYTES
        ) as live:
            try:
                await peer.send_json(hello_message(registry, live.interval_seconds))
                while True:
                    frame = await ws.receive()
                    if frame["type"] == "websocket.disconnect":
                        break
                    if frame.get("bytes") is not None:
                        await session.handle_binary(frame["bytes"])
                    elif frame.get("text") is not None:
                        await session.handle_text(frame["text"])
            except WebSocketDisconnect:
                pass
            finally:
                await session.close()
                if ws.application_state != WebSocketState.DISCONNECTED:
                    with contextlib.suppress(Exception):
                        await ws.close(code=status.WS_1000_NORMAL_CLOSURE)

    return router


__all__: list[str] = [
    "DEFAULT_GAME_ROOM_PATH",
    "UNAUTHORIZED_CLOSE_CODE",
    "WebSocketRoomPeer",
    "make_game_room_router",
    "make_jwt_room_identity_resolver",
]
