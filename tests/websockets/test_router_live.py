"""``make_websocket_router`` close codes as a real client sees them.

Starlette's ``TestClient`` implements the WebSocket protocol itself and
reports an application close code even when the server closed **before**
accepting, which uvicorn turns into an ``HTTP 403`` handshake rejection.
Only a real server and a real client tell the two apart, so these tests
run the router under uvicorn in this process and connect with the
``websockets`` library.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket

from tempest_fastapi_sdk import (
    WebSocketConnection,
    WebSocketHub,
    WebSocketSettings,
    make_websocket_router,
)

pytest.importorskip("websockets")

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Subprotocol

VALID_TOKEN: str = "valid-token"


def _build_app() -> FastAPI:
    """Build an app whose resolver accepts only ``VALID_TOKEN``.

    Returns:
        FastAPI: The app with the WebSocket router mounted at ``/ws``.
    """
    user_id: UUID = uuid4()

    async def resolver(token: str) -> UUID | None:
        """Map ``VALID_TOKEN`` to a fixed user and anything else to ``None``.

        Args:
            token (str): The bearer token from the handshake.

        Returns:
            UUID | None: The user id, or ``None`` for an unknown token.
        """
        return user_id if token == VALID_TOKEN else None

    async def handler(
        ws: WebSocket,
        connection: WebSocketConnection,
        hub: WebSocketHub,
    ) -> None:
        """Wait for one frame, then return.

        Args:
            ws (WebSocket): The accepted socket.
            connection (WebSocketConnection): The hub registration.
            hub (WebSocketHub): The shared hub.
        """
        await ws.receive_text()

    app = FastAPI()
    app.include_router(
        make_websocket_router(
            handler,
            hub=WebSocketHub(),
            bearer_resolver=resolver,
            settings=WebSocketSettings(
                WS_HEARTBEAT_SECONDS=3600,
                WS_HEARTBEAT_TIMEOUT_SECONDS=3600,
            ),
        )
    )
    return app


@pytest.fixture
async def ws_base() -> AsyncIterator[str]:
    """Serve the app under uvicorn on a free loopback port.

    Yields:
        str: The ``ws://`` base URL of the running server.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(_build_app(), log_level="warning", ws="websockets-sansio")
    )
    task: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        sock.close()


class TestUnauthorizedCloseOnTheWire:
    """The documented ``4401`` reaches a real client, not an ``HTTP 403``."""

    async def test_invalid_query_token_receives_4401(self, ws_base: str) -> None:
        with pytest.raises(ConnectionClosed) as excinfo:
            async with connect(f"{ws_base}/ws?token=wrong") as client:
                await client.recv()
        assert excinfo.value.rcvd is not None
        assert excinfo.value.rcvd.code == 4401

    async def test_missing_token_receives_4401(self, ws_base: str) -> None:
        with pytest.raises(ConnectionClosed) as excinfo:
            async with connect(f"{ws_base}/ws") as client:
                await client.recv()
        assert excinfo.value.rcvd is not None
        assert excinfo.value.rcvd.code == 4401

    async def test_invalid_subprotocol_token_negotiates_then_4401(
        self, ws_base: str
    ) -> None:
        """A browser that offered ``bearer`` needs it echoed even on rejection.

        Without the echo the client fails the handshake itself and never
        reads the close frame.
        """
        with pytest.raises(ConnectionClosed) as excinfo:
            async with connect(
                f"{ws_base}/ws",
                subprotocols=[Subprotocol("bearer"), Subprotocol("wrong")],
            ) as client:
                assert client.subprotocol == "bearer"
                await client.recv()
        assert excinfo.value.rcvd is not None
        assert excinfo.value.rcvd.code == 4401

    async def test_valid_token_is_accepted(self, ws_base: str) -> None:
        async with connect(f"{ws_base}/ws?token={VALID_TOKEN}") as client:
            hello: str | bytes = await client.recv()
        assert "hello" in str(hello)
