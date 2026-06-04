"""In-process WebSocket connection registry + broadcast utilities."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from fastapi import WebSocket

    from tempest_fastapi_sdk.websockets.schemas import WSEnvelope


@dataclass
class WebSocketConnection:
    """A single live WebSocket bound to an authenticated user.

    Attributes:
        connection_id (UUID): Unique identifier; the hub keys
            connections by this so the same user can hold several
            sockets at once (e.g. multi-tab).
        user_id (UUID): The user the connection belongs to. Set by
            ``WebSocketHub.register`` based on whatever the bearer
            resolver returns.
        ws (WebSocket): The underlying FastAPI/Starlette socket.
        topics (set[str]): Set of topic strings the connection has
            subscribed to. Populated by
            :meth:`WebSocketHub.subscribe`.
    """

    connection_id: UUID
    user_id: UUID
    ws: WebSocket
    topics: set[str] = field(default_factory=set)


class WebSocketHub:
    """In-process registry of live WebSocket connections.

    Tracks every connection accepted by
    :func:`tempest_fastapi_sdk.make_websocket_router` and offers three
    delivery patterns:

    * ``send_to(user_id, envelope)`` — every socket the user has
      open right now.
    * ``broadcast(envelope, topic=...)`` — every subscriber of
      ``topic`` (or every connection when ``topic`` is omitted).
    * ``subscribe(connection_id, topic)`` /
      ``unsubscribe(connection_id, topic)`` — per-connection
      topic membership.

    This hub is single-process. For multi-replica deployments, fan
    out across processes via a pub/sub backend (Redis pub/sub,
    RabbitMQ topic exchange) — the hub itself only handles
    in-process delivery. The future ``RedisWebSocketHub`` will swap
    the local broadcast for a redis-driven one without changing the
    public surface; today, run a single replica or use sticky
    sessions when WebSocket fan-out matters.

    The hub is safe to share across handlers in the same FastAPI
    app — all mutators take an ``asyncio.Lock`` so concurrent
    register/unregister calls do not corrupt the internal state.
    """

    def __init__(self, *, max_per_user: int = 5) -> None:
        """Initialize the hub.

        Args:
            max_per_user (int): Cap on concurrent connections per
                user. When the cap is hit on ``register``, the
                oldest connection for that user is force-closed
                with code ``4429`` and removed before the new one
                is registered.
        """
        self._connections: dict[UUID, WebSocketConnection] = {}
        self._by_user: dict[UUID, list[UUID]] = {}
        self._by_topic: dict[str, set[UUID]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self.max_per_user: int = max_per_user

    async def register(self, user_id: UUID, ws: WebSocket) -> WebSocketConnection:
        """Register an accepted WebSocket against ``user_id``.

        When the user is already at ``max_per_user`` open
        connections, the oldest one is force-closed (code ``4429``)
        before the new connection is admitted.

        Args:
            user_id (UUID): The authenticated user owning the new
                connection.
            ws (WebSocket): The accepted FastAPI WebSocket.

        Returns:
            WebSocketConnection: The registered handle. Pass its
            ``connection_id`` to :meth:`unregister`,
            :meth:`subscribe` or :meth:`unsubscribe`.
        """
        async with self._lock:
            existing = self._by_user.get(user_id, [])
            if len(existing) >= self.max_per_user:
                oldest_id = existing[0]
                await self._evict_locked(oldest_id, code=4429)
            connection = WebSocketConnection(
                connection_id=uuid4(),
                user_id=user_id,
                ws=ws,
            )
            self._connections[connection.connection_id] = connection
            self._by_user.setdefault(user_id, []).append(connection.connection_id)
            return connection

    async def unregister(self, connection_id: UUID) -> None:
        """Remove a connection from every index. Idempotent."""
        async with self._lock:
            await self._evict_locked(connection_id, code=None)

    async def _evict_locked(self, connection_id: UUID, *, code: int | None) -> None:
        """Internal — already-locked eviction (close socket if ``code``)."""
        connection = self._connections.pop(connection_id, None)
        if connection is None:
            return
        user_list = self._by_user.get(connection.user_id, [])
        if connection_id in user_list:
            user_list.remove(connection_id)
            if not user_list:
                self._by_user.pop(connection.user_id, None)
        for topic in list(connection.topics):
            self._by_topic.get(topic, set()).discard(connection_id)
            if not self._by_topic.get(topic):
                self._by_topic.pop(topic, None)
        if code is not None:
            with contextlib.suppress(Exception):
                await connection.ws.close(code=code)

    async def subscribe(self, connection_id: UUID, topic: str) -> None:
        """Add ``topic`` to the connection's subscription set."""
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                return
            connection.topics.add(topic)
            self._by_topic.setdefault(topic, set()).add(connection_id)

    async def unsubscribe(self, connection_id: UUID, topic: str) -> None:
        """Drop ``topic`` from the connection's subscription set."""
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                return
            connection.topics.discard(topic)
            self._by_topic.get(topic, set()).discard(connection_id)
            if not self._by_topic.get(topic):
                self._by_topic.pop(topic, None)

    async def send_to(self, user_id: UUID, envelope: WSEnvelope) -> int:
        """Send ``envelope`` to every connection owned by ``user_id``.

        Args:
            user_id (UUID): Target user.
            envelope (WSEnvelope): Frame to deliver.

        Returns:
            int: Number of sockets that successfully received the
            frame. Dead connections are evicted transparently and do
            not count.
        """
        async with self._lock:
            targets = list(self._by_user.get(user_id, []))
        return await self._fan_out(targets, envelope)

    async def broadcast(
        self,
        envelope: WSEnvelope,
        *,
        topic: str | None = None,
    ) -> int:
        """Deliver ``envelope`` to every subscriber of ``topic``.

        When ``topic`` is ``None``, the envelope is delivered to
        every active connection across every user — useful for
        system-wide announcements, but expensive at scale; prefer
        topic-scoped delivery when you can.

        Args:
            envelope (WSEnvelope): Frame to deliver.
            topic (str | None): Topic to fan out on, or ``None`` for
                everyone.

        Returns:
            int: Number of successful sends. See :meth:`send_to`.
        """
        async with self._lock:
            if topic is None:
                targets = list(self._connections.keys())
            else:
                targets = list(self._by_topic.get(topic, set()))
        return await self._fan_out(targets, envelope)

    async def _fan_out(
        self,
        connection_ids: list[UUID],
        envelope: WSEnvelope,
    ) -> int:
        """Deliver ``envelope`` to ``connection_ids``, evicting dead peers."""
        if not connection_ids:
            return 0
        payload = envelope.model_dump()
        delivered = 0
        for connection_id in connection_ids:
            connection = self._connections.get(connection_id)
            if connection is None:
                continue
            try:
                await connection.ws.send_json(payload)
                delivered += 1
            except Exception:
                await self.unregister(connection_id)
        return delivered

    def online_users(self) -> set[UUID]:
        """Return the set of users with at least one active connection."""
        return set(self._by_user.keys())

    def connection_count(self) -> int:
        """Return the total number of live connections."""
        return len(self._connections)

    def topic_count(self, topic: str) -> int:
        """Return the number of subscribers for ``topic``."""
        return len(self._by_topic.get(topic, set()))


__all__: list[str] = ["WebSocketConnection", "WebSocketHub"]
