"""Tests for ``WebSocketHub`` and ``make_websocket_router``."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    WebSocketConnection,
    WebSocketHub,
    WebSocketSettings,
    WSEnvelope,
    make_websocket_router,
)


def _skip_hello(ws: Any) -> dict[str, Any]:
    """Read past the router's `hello` frame and return it.

    Since v0.261.0 the first frame the router sends announces the
    heartbeat cadence, so a test that reads positionally has to consume
    it. Returned rather than discarded so a caller can assert on it.

    Args:
        ws: The connected test-client socket.

    Returns:
        dict[str, Any]: The `hello` envelope.
    """
    hello: dict[str, Any] = ws.receive_json()
    assert hello["type"] == "hello"
    return hello


# ---------------------------------------------------------------------------
# Hub unit tests
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal stand-in for ``fastapi.WebSocket`` for hub-level assertions."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None
        self.dead: bool = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.dead:
            raise RuntimeError("peer gone")
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


class TestWebSocketHub:
    async def test_register_assigns_unique_id_and_indexes_by_user(self) -> None:
        hub = WebSocketHub()
        user = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        conn_a = await hub.register(user, ws_a)  # type: ignore[arg-type]
        conn_b = await hub.register(user, ws_b)  # type: ignore[arg-type]
        assert conn_a.connection_id != conn_b.connection_id
        assert hub.connection_count() == 2
        assert hub.online_users() == {user}

    async def test_max_per_user_evicts_oldest_connection(self) -> None:
        hub = WebSocketHub(max_per_user=2)
        user = uuid4()
        first = _FakeWebSocket()
        second = _FakeWebSocket()
        third = _FakeWebSocket()
        await hub.register(user, first)  # type: ignore[arg-type]
        await hub.register(user, second)  # type: ignore[arg-type]
        await hub.register(user, third)  # type: ignore[arg-type]
        assert first.closed_with == 4429
        assert second.closed_with is None
        assert third.closed_with is None
        assert hub.connection_count() == 2

    async def test_send_to_delivers_only_to_target_user(self) -> None:
        hub = WebSocketHub()
        user_a = uuid4()
        user_b = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        await hub.register(user_a, ws_a)  # type: ignore[arg-type]
        await hub.register(user_b, ws_b)  # type: ignore[arg-type]
        envelope = WSEnvelope(type="ping", data={})
        delivered = await hub.send_to(user_a, envelope)
        assert delivered == 1
        assert len(ws_a.sent) == 1
        assert ws_b.sent == []

    async def test_broadcast_topic_filters_subscribers(self) -> None:
        hub = WebSocketHub()
        user_a = uuid4()
        user_b = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        conn_a = await hub.register(user_a, ws_a)  # type: ignore[arg-type]
        await hub.register(user_b, ws_b)  # type: ignore[arg-type]
        await hub.subscribe(conn_a.connection_id, "orders")
        envelope = WSEnvelope(type="order.paid", data={"id": "1"})
        delivered = await hub.broadcast(envelope, topic="orders")
        assert delivered == 1
        assert hub.topic_count("orders") == 1

    async def test_broadcast_without_topic_hits_every_connection(self) -> None:
        hub = WebSocketHub()
        user_a = uuid4()
        user_b = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        await hub.register(user_a, ws_a)  # type: ignore[arg-type]
        await hub.register(user_b, ws_b)  # type: ignore[arg-type]
        envelope = WSEnvelope(type="announce", data={})
        delivered = await hub.broadcast(envelope)
        assert delivered == 2

    async def test_send_to_evicts_dead_peer(self) -> None:
        hub = WebSocketHub()
        user = uuid4()
        ws_dead = _FakeWebSocket()
        ws_dead.dead = True
        ws_live = _FakeWebSocket()
        await hub.register(user, ws_dead)  # type: ignore[arg-type]
        await hub.register(user, ws_live)  # type: ignore[arg-type]
        envelope = WSEnvelope(type="ping", data={})
        delivered = await hub.send_to(user, envelope)
        assert delivered == 1
        assert hub.connection_count() == 1

    async def test_send_many_delivers_a_distinct_payload_per_user(self) -> None:
        hub = WebSocketHub()
        user_a = uuid4()
        user_b = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        await hub.register(user_a, ws_a)  # type: ignore[arg-type]
        await hub.register(user_b, ws_b)  # type: ignore[arg-type]
        delivered = await hub.send_many(
            {
                user_a: WSEnvelope(type="state", data={"sees": "a"}),
                user_b: WSEnvelope(type="state", data={"sees": "b"}),
            }
        )
        assert delivered == 2
        assert ws_a.sent[0]["data"] == {"sees": "a"}
        assert ws_b.sent[0]["data"] == {"sees": "b"}

    async def test_send_many_skips_users_without_connections(self) -> None:
        hub = WebSocketHub()
        user = uuid4()
        ws = _FakeWebSocket()
        await hub.register(user, ws)  # type: ignore[arg-type]
        delivered = await hub.send_many(
            {
                user: WSEnvelope(type="state", data={}),
                uuid4(): WSEnvelope(type="state", data={}),
            }
        )
        assert delivered == 1

    async def test_send_many_evicts_dead_peers(self) -> None:
        hub = WebSocketHub()
        user = uuid4()
        dead = _FakeWebSocket()
        dead.dead = True
        await hub.register(user, dead)  # type: ignore[arg-type]
        delivered = await hub.send_many({user: WSEnvelope(type="state", data={})})
        assert delivered == 0
        assert hub.connection_count() == 0

    async def test_unregister_clears_topic_indexes(self) -> None:
        hub = WebSocketHub()
        user = uuid4()
        ws = _FakeWebSocket()
        conn = await hub.register(user, ws)  # type: ignore[arg-type]
        await hub.subscribe(conn.connection_id, "orders")
        assert hub.topic_count("orders") == 1
        await hub.unregister(conn.connection_id)
        assert hub.topic_count("orders") == 0
        assert hub.connection_count() == 0


# ---------------------------------------------------------------------------
# Router integration tests (use FastAPI TestClient — sync only, fine for WS)
# ---------------------------------------------------------------------------


def _build_app(
    *,
    hub: WebSocketHub,
    user_id: UUID | None,
    settings: WebSocketSettings | None = None,
) -> FastAPI:
    """Build a FastAPI app with the websocket router wired.

    Args:
        hub (WebSocketHub): The hub the router registers into.
        user_id (UUID | None): What the bearer resolver returns for
            ``"valid-token"`` — ``None`` makes every handshake fail.
        settings (WebSocketSettings | None): Heartbeat / size limits.
            Defaults to heartbeats far enough out that they never fire
            during a fast test.
    """

    async def resolver(token: str) -> UUID | None:
        if token == "valid-token":
            return user_id
        return None

    async def handler(
        ws: WebSocket,
        connection: WebSocketConnection,
        hub: WebSocketHub,
    ) -> None:
        # Echo loop — every received message bounces back as type=echo.
        while True:
            message = await ws.receive_json()
            envelope = WSEnvelope.model_validate(message)
            if envelope.type == "pong":
                continue
            await ws.send_json(
                WSEnvelope(
                    type="echo",
                    data={"received": envelope.type},
                    request_id=envelope.request_id,
                ).model_dump()
            )

    effective = settings or WebSocketSettings(
        WS_HEARTBEAT_SECONDS=3600,  # disable heartbeats during fast tests
        WS_HEARTBEAT_TIMEOUT_SECONDS=3600,
    )

    app = FastAPI()
    app.include_router(
        make_websocket_router(
            handler,
            hub=hub,
            bearer_resolver=resolver,
            settings=effective,
        )
    )
    return app


class TestWebSocketRouter:
    def test_valid_query_token_handshake_then_echo(self) -> None:
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=user_id)
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
        ):
            _skip_hello(ws)
            ws.send_json({"type": "chat.message", "data": {"text": "hi"}})
            received = ws.receive_json()
        assert received["type"] == "echo"
        assert received["data"]["received"] == "chat.message"

    def test_invalid_token_closed_with_4401(self) -> None:
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=uuid4())
        with (
            TestClient(app) as client,
            pytest.raises(Exception),  # noqa: B017 — Starlette closes the handshake
            client.websocket_connect("/ws?token=wrong"),
        ):
            pass

    def test_subprotocol_bearer_negotiation(self) -> None:
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=user_id)
        with (
            TestClient(app) as client,
            client.websocket_connect(
                "/ws",
                subprotocols=["bearer", "valid-token"],
            ) as ws,
        ):
            ws.send_json({"type": "ping", "data": {}})
            ws.receive_json()

    def test_handler_sees_registered_connection(self) -> None:
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=user_id)
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
        ):
            # Send something so the handler enters the loop and the
            # registration is visible to the hub.
            ws.send_json({"type": "chat.message", "data": {}})
            ws.receive_json()
            assert hub.connection_count() == 1
            assert user_id in hub.online_users()
        # On close the router must unregister.
        assert hub.connection_count() == 0


class TestFrameGuard:
    """The router's own promises about inbound frames."""

    def test_oversized_frame_closes_with_1009(self) -> None:
        """A frame past ``WS_MAX_MESSAGE_BYTES`` never reaches the handler."""
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(
            hub=hub,
            user_id=user_id,
            settings=WebSocketSettings(
                WS_HEARTBEAT_SECONDS=3600,
                WS_HEARTBEAT_TIMEOUT_SECONDS=3600,
                WS_MAX_MESSAGE_BYTES=64,
            ),
        )
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
        ):
            _skip_hello(ws)
            ws.send_json({"type": "chat.message", "data": {"text": "x" * 500}})
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_json()
        assert excinfo.value.code == 1009

    def test_frame_within_the_cap_is_delivered(self) -> None:
        """The cap rejects only what exceeds it."""
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(
            hub=hub,
            user_id=user_id,
            settings=WebSocketSettings(
                WS_HEARTBEAT_SECONDS=3600,
                WS_HEARTBEAT_TIMEOUT_SECONDS=3600,
                WS_MAX_MESSAGE_BYTES=4096,
            ),
        )
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
        ):
            _skip_hello(ws)
            ws.send_json({"type": "chat.message", "data": {"text": "hi"}})
            received = ws.receive_json()
        assert received["type"] == "echo"


class TestHeartbeatTimeout:
    """The 4408 eviction the module docstring promises."""

    def test_silent_peer_is_closed_with_4408(self) -> None:
        """A peer that never pongs loses its slot instead of keeping it."""
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(
            hub=hub,
            user_id=user_id,
            settings=WebSocketSettings(
                WS_HEARTBEAT_SECONDS=1,
                WS_HEARTBEAT_TIMEOUT_SECONDS=1,
            ),
        )
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
            pytest.raises(WebSocketDisconnect) as excinfo,
        ):
            while True:
                ws.receive_json()
        assert excinfo.value.code == 4408
        assert hub.connection_count() == 0

    def test_pong_keeps_the_socket_alive(self) -> None:
        """Answering the ping resets the deadline, so pings keep coming."""
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(
            hub=hub,
            user_id=user_id,
            settings=WebSocketSettings(
                WS_HEARTBEAT_SECONDS=1,
                WS_HEARTBEAT_TIMEOUT_SECONDS=3,
            ),
        )
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
        ):
            _skip_hello(ws)
            first = ws.receive_json()
            ws.send_json({"type": "pong", "data": {}})
            second = ws.receive_json()
        assert first["type"] == "ping"
        assert second["type"] == "ping"

    def test_pong_is_swallowed_before_the_handler(self) -> None:
        """A pong answers the router, so the handler never sees it."""
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=user_id)
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
        ):
            _skip_hello(ws)
            ws.send_json({"type": "pong", "data": {}})
            ws.send_json({"type": "chat.message", "data": {}})
            received = ws.receive_json()
        assert received["data"]["received"] == "chat.message"
