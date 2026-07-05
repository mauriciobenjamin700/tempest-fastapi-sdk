"""Multi-worker SSE fan-out: local registry + optional Redis pub/sub bridge.

:class:`EventStream` feeds **one** connection. To broadcast the same
event to every client subscribed to a channel — across multiple
Uvicorn/Gunicorn workers — you need a fan-out layer plus a cross-process
transport. :class:`SSEBroker` is both:

* **Single process** (``redis=None``) — keeps a registry of local
  streams per channel; :meth:`publish` fans out to them in-process.
* **Multi-worker** (``redis=<client>``) — :meth:`publish` goes through
  Redis ``PUBLISH``; a background :meth:`run` task ``PSUBSCRIBE``-s the
  channel prefix and re-fans every message to the worker's local
  streams. Run :meth:`run` from the app lifespan and stop it with
  :meth:`aclose`.

The same call site (``register`` / ``publish`` / ``unregister``) works in
both modes, so a service starts single-process and gains horizontal
scale by passing a Redis client — no code change.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.sse.event_stream import (
    EventStream,
    OverflowPolicy,
    ServerSentEvent,
    sse_response,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from starlette.responses import StreamingResponse


class SSEBroker:
    """Channel-based SSE fan-out, optionally bridged over Redis pub/sub.

    Attributes:
        channel_prefix (str): Redis key prefix for published channels
            (``"<prefix>:<channel>"``). Also the ``PSUBSCRIBE`` pattern
            root. Ignored in single-process mode.
        heartbeat_seconds (float | None): Idle interval passed to every
            :class:`EventStream` created by :meth:`register`.
    """

    def __init__(
        self,
        *,
        redis: Redis | None = None,
        channel_prefix: str = "sse",
        heartbeat_seconds: float | None = 15.0,
        max_queue: int = 1000,
        overflow: OverflowPolicy = "drop_oldest",
    ) -> None:
        """Initialize the broker.

        Args:
            redis (Redis | None): Async Redis client (from the ``[cache]``
                extra). ``None`` runs single-process (local fan-out only).
            channel_prefix (str): Prefix for Redis channels.
            heartbeat_seconds (float | None): Idle heartbeat for streams.
            max_queue (int): Bounded-queue size for every stream opened by
                :meth:`register`. ``0`` disables the bound. Defaults to
                ``1000``.
            overflow (OverflowPolicy): Overflow policy for those streams.
                Defaults to ``"drop_oldest"``.
        """
        self._redis: Redis | None = redis
        self.channel_prefix: str = channel_prefix
        self.heartbeat_seconds: float | None = heartbeat_seconds
        self.max_queue: int = max_queue
        self.overflow: OverflowPolicy = overflow
        self._channels: dict[str, set[EventStream]] = {}
        self._pubsub: Any = None

    def register(self, channel: str) -> EventStream:
        """Open a local stream subscribed to ``channel`` and track it.

        Args:
            channel (str): The logical channel (e.g. a user id or topic).

        Returns:
            EventStream: A fresh stream to hand to
            :func:`tempest_fastapi_sdk.sse_response`.
        """
        stream = EventStream(
            heartbeat_seconds=self.heartbeat_seconds,
            max_queue=self.max_queue,
            overflow=self.overflow,
        )
        self._channels.setdefault(channel, set()).add(stream)
        return stream

    def response(
        self,
        channel: str,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        """Subscribe to ``channel`` and return the SSE response in one call.

        Bundles the whole per-connection lifecycle: :meth:`register` opens
        a local stream, :func:`tempest_fastapi_sdk.sse_response` wraps it,
        and an ``on_disconnect`` hook calls :meth:`unregister` when the
        client goes away. This removes the hand-rolled ``try/finally``
        wrapper — forgetting which used to leak a stream per disconnect.

        Args:
            channel (str): The logical channel to subscribe the client to.
            status_code (int): HTTP status. Defaults to ``200``.
            headers (dict[str, str] | None): Extra headers to attach.

        Returns:
            StreamingResponse: A ready-to-return SSE response that
            unregisters its stream on disconnect.
        """
        stream = self.register(channel)
        return sse_response(
            stream.stream(),
            status_code=status_code,
            headers=headers,
            on_disconnect=lambda: self.unregister(channel, stream),
        )

    def unregister(self, channel: str, stream: EventStream) -> None:
        """Drop a closed stream from ``channel``.

        Call this from the response generator's ``finally`` so a
        disconnected client is forgotten.

        Args:
            channel (str): The channel the stream was registered under.
            stream (EventStream): The stream to remove.
        """
        streams = self._channels.get(channel)
        if streams:
            streams.discard(stream)
            if not streams:
                del self._channels[channel]

    def local_subscribers(self, channel: str) -> int:
        """Return how many local streams are open on ``channel``.

        Args:
            channel (str): The channel to inspect.

        Returns:
            int: The local subscriber count (this worker only).
        """
        return len(self._channels.get(channel, set()))

    async def publish(
        self,
        channel: str,
        data: Any = "",
        *,
        event: str | None = None,
        id: str | None = None,
        retry: int | None = None,
    ) -> None:
        """Publish an event to every subscriber of ``channel``.

        In Redis mode the event is ``PUBLISH``-ed and delivered to all
        workers (including this one) by their :meth:`run` loop. In
        single-process mode it fans out to local streams immediately.

        Args:
            channel (str): Target channel.
            data (Any): Payload (string, bytes or JSON-serializable).
            event (str | None): Optional SSE event name.
            id (str | None): Optional ``Last-Event-ID``.
            retry (int | None): Optional reconnect hint (ms).
        """
        if self._redis is None:
            await self._emit_local(
                channel,
                ServerSentEvent(data=data, event=event, id=id, retry=retry),
            )
            return
        envelope = json.dumps(
            {"data": data, "event": event, "id": id, "retry": retry},
            default=str,
        )
        await self._redis.publish(self._key(channel), envelope)

    async def run(self) -> None:
        """Consume Redis pub/sub and fan messages to local streams.

        Long-running: start it as a background task in the app lifespan
        and cancel it (or call :meth:`aclose`) on shutdown. A no-op in
        single-process mode.

        Raises:
            RuntimeError: If called without a Redis client.
        """
        if self._redis is None:
            raise RuntimeError("SSEBroker.run() requires a Redis client.")
        pubsub: Any = self._redis.pubsub()
        self._pubsub = pubsub
        await pubsub.psubscribe(f"{self.channel_prefix}:*")
        try:
            async for message in pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                await self._dispatch_raw(message["channel"], message["data"])
        finally:
            await pubsub.aclose()
            self._pubsub = None

    async def aclose(self) -> None:
        """Stop the pub/sub subscription opened by :meth:`run`."""
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None

    def _key(self, channel: str) -> str:
        """Return the Redis channel key for ``channel``."""
        return f"{self.channel_prefix}:{channel}"

    async def _dispatch_raw(self, channel_key: str | bytes, raw: str | bytes) -> None:
        """Decode a Redis pub/sub frame and fan it to local streams.

        Args:
            channel_key (str | bytes): The Redis channel
                (``<prefix>:<channel>``), as delivered by pub/sub.
            raw (str | bytes): The JSON envelope payload.
        """
        key = channel_key.decode() if isinstance(channel_key, bytes) else channel_key
        body = raw.decode() if isinstance(raw, bytes) else raw
        channel = key.removeprefix(f"{self.channel_prefix}:")
        payload: dict[str, Any] = json.loads(body)
        await self._emit_local(
            channel,
            ServerSentEvent(
                data=payload.get("data", ""),
                event=payload.get("event"),
                id=payload.get("id"),
                retry=payload.get("retry"),
            ),
        )

    async def _emit_local(self, channel: str, event: ServerSentEvent) -> None:
        """Enqueue ``event`` on every local stream of ``channel``.

        Args:
            channel (str): The channel to fan out to.
            event (ServerSentEvent): The event to deliver.
        """
        for stream in tuple(self._channels.get(channel, set())):
            await stream.publish_event(event)


__all__: list[str] = [
    "SSEBroker",
]
