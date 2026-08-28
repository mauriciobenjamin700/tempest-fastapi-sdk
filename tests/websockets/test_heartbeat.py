"""Tests for the public ``heartbeat`` primitive.

Issue #225: the machinery existed but only inside
``make_websocket_router``, which imposes bearer auth at the handshake and
registration in a ``user_id``-keyed hub. An endpoint that fits neither —
an anonymous room addressed by peer id, where the room code is the only
secret — had to reimplement it.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import WebSocketSettings
from tempest_fastapi_sdk.websockets import (
    HEARTBEAT_TIMEOUT_CODE,
    Liveness,
    heartbeat,
)
from tempest_fastapi_sdk.websockets.heartbeat import _install_frame_guard


class _RecordingSocket:
    """Feeds a fixed list of ASGI frames through ``receive``."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = list(frames)
        self.closed_with: int | None = None

    async def receive(self) -> dict[str, Any]:
        """Return the next queued frame.

        Returns:
            dict[str, Any]: The frame.

        Raises:
            AssertionError: When the queue runs dry, which means the
                guard swallowed more than the test expected.
        """
        assert self._frames, "guard consumed more frames than the test queued"
        return self._frames.pop(0)

    async def close(self, code: int = 1000) -> None:
        """Record the close code.

        Args:
            code (int): The close code.
        """
        self.closed_with = code


def _text_frame(payload: str) -> dict[str, Any]:
    """Build an inbound text frame.

    Args:
        payload (str): The frame body.

    Returns:
        dict[str, Any]: The ASGI message.
    """
    return {"type": "websocket.receive", "text": payload}


class TestAnyFrameIsProofOfLife:
    """The rule the issue asks for, tested without waiting on a clock."""

    async def test_application_data_stamps_liveness(self) -> None:
        """A busy peer mid-exchange must not be evicted for not ponging.

        Before v0.261.0 only `{"type": "pong"}` counted, so a client
        deep in an ICE negotiation — demonstrably present — could cross
        the deadline while answering something else.
        """
        live = Liveness(interval_seconds=1, timeout_seconds=2, last_seen=0.0)
        socket = _RecordingSocket([_text_frame('{"type": "offer", "data": {}}')])
        _install_frame_guard(socket, live=live, max_message_bytes=None)  # type: ignore[arg-type]

        message = await socket.receive()

        assert message["text"].startswith('{"type": "offer"')
        assert live.last_seen > 0.0

    async def test_a_pong_stamps_liveness_and_is_swallowed(self) -> None:
        """It answers our ping, so it is protocol and not application data."""
        live = Liveness(interval_seconds=1, timeout_seconds=2, last_seen=0.0)
        socket = _RecordingSocket(
            [
                _text_frame('{"type": "pong", "data": {}}'),
                _text_frame('{"type": "chat.message", "data": {}}'),
            ]
        )
        _install_frame_guard(socket, live=live, max_message_bytes=None)  # type: ignore[arg-type]

        message = await socket.receive()

        assert message["text"].startswith('{"type": "chat.message"')
        assert live.last_seen > 0.0

    async def test_an_unparseable_frame_still_counts(self) -> None:
        """Liveness is about arrival, not about shape."""
        live = Liveness(interval_seconds=1, timeout_seconds=2, last_seen=0.0)
        socket = _RecordingSocket([_text_frame("not json at all")])
        _install_frame_guard(socket, live=live, max_message_bytes=None)  # type: ignore[arg-type]

        await socket.receive()

        assert live.last_seen > 0.0

    async def test_touch_proves_life_out_of_band(self) -> None:
        """For liveness the socket itself cannot observe."""
        live = Liveness(interval_seconds=1, timeout_seconds=2, last_seen=0.0)

        live.touch()

        assert live.last_seen > 0.0


class TestTheSizeCapIsOptional:
    """A cap is a separate policy from liveness."""

    async def test_no_cap_by_default(self) -> None:
        """The primitive leaves frame size alone unless asked."""
        live = Liveness(interval_seconds=1, timeout_seconds=2)
        socket = _RecordingSocket([_text_frame("x" * 5_000)])
        _install_frame_guard(socket, live=live, max_message_bytes=None)  # type: ignore[arg-type]

        message = await socket.receive()

        assert len(message["text"]) == 5_000
        assert socket.closed_with is None

    async def test_a_cap_closes_with_1009(self) -> None:
        """The router passes its own cap through, and it still bites."""
        live = Liveness(interval_seconds=1, timeout_seconds=2)
        socket = _RecordingSocket([_text_frame("x" * 5_000)])
        _install_frame_guard(socket, live=live, max_message_bytes=100)  # type: ignore[arg-type]

        with pytest.raises(WebSocketDisconnect):
            await socket.receive()

        assert socket.closed_with == 1009


def _anonymous_app(settings: WebSocketSettings) -> FastAPI:
    """Build an endpoint with no auth and no hub, only the heartbeat.

    Args:
        settings (WebSocketSettings): Cadence for the primitive.

    Returns:
        FastAPI: The app, mounting the endpoint at ``/relay``.

    This is the shape issue #225 reported as unreachable: the socket is
    accepted without a bearer, and nothing registers it anywhere.
    """
    app = FastAPI()

    @app.websocket("/relay")
    async def relay(ws: WebSocket) -> None:
        """Echo frames back, announcing the cadence first."""
        await ws.accept()
        async with heartbeat(ws, settings=settings) as live:
            await ws.send_json(
                {"type": "hello", "heartbeat_seconds": live.interval_seconds}
            )
            try:
                while True:
                    payload = await ws.receive_json()
                    await ws.send_json({"type": "echo", "data": payload})
            except WebSocketDisconnect:
                return

    return app


class TestThePrimitiveWorksWithoutAuthOrHub:
    """The whole point of extracting it."""

    def test_an_anonymous_endpoint_gets_pings(self) -> None:
        """No bearer, no hub, still a heartbeat."""
        app = _anonymous_app(
            WebSocketSettings(
                WS_HEARTBEAT_SECONDS=1,
                WS_HEARTBEAT_TIMEOUT_SECONDS=5,
            )
        )
        with TestClient(app) as client, client.websocket_connect("/relay") as ws:
            hello = ws.receive_json()
            first = ws.receive_json()

        assert hello == {"type": "hello", "heartbeat_seconds": 1}
        assert first["type"] == "ping"

    def test_a_silent_anonymous_peer_is_evicted(self) -> None:
        """The window this closes: a peer gone without a close frame."""
        app = _anonymous_app(
            WebSocketSettings(
                WS_HEARTBEAT_SECONDS=1,
                WS_HEARTBEAT_TIMEOUT_SECONDS=1,
            )
        )
        with (
            TestClient(app) as client,
            client.websocket_connect("/relay") as ws,
            pytest.raises(WebSocketDisconnect) as excinfo,
        ):
            while True:
                ws.receive_json()

        assert excinfo.value.code == HEARTBEAT_TIMEOUT_CODE

    def test_application_traffic_alone_keeps_it_alive(self) -> None:
        """End to end: never pong, only talk, and survive past the deadline."""
        app = _anonymous_app(
            WebSocketSettings(
                WS_HEARTBEAT_SECONDS=1,
                WS_HEARTBEAT_TIMEOUT_SECONDS=2,
            )
        )
        deadline = time.monotonic() + 3.0
        with TestClient(app) as client, client.websocket_connect("/relay") as ws:
            assert ws.receive_json()["type"] == "hello"
            echoes = 0
            while time.monotonic() < deadline:
                ws.send_json({"type": "offer", "data": {}})
                while True:
                    frame = ws.receive_json()
                    if frame["type"] == "echo":
                        echoes += 1
                        break
            ws.send_json({"type": "offer", "data": {}})
            while ws.receive_json()["type"] != "echo":
                continue

        assert echoes > 0


class TestTheSocketIsLeftAsItWasFound:
    """Exiting the block undoes what entering it did."""

    async def test_receive_is_restored(self) -> None:
        """So a nested block does not leave a guard behind."""
        settings = WebSocketSettings(
            WS_HEARTBEAT_SECONDS=1,
            WS_HEARTBEAT_TIMEOUT_SECONDS=2,
        )
        socket = _RecordingSocket([])
        original = socket.receive

        async with heartbeat(socket, settings=settings):  # type: ignore[arg-type]
            assert socket.receive != original

        assert socket.receive == original
