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
import logging
from typing import TYPE_CHECKING, Any, Final

from tempest_fastapi_sdk.sse.event_stream import (
    EventStream,
    OverflowPolicy,
    ServerSentEvent,
    SSEData,
    sse_response,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from redis.asyncio import Redis
    from starlette.responses import StreamingResponse

_LOGGER: logging.Logger = logging.getLogger(__name__)

_DEFAULT_CHANNEL_PREFIX: Final[str] = "sse"
"""The ``channel_prefix`` :class:`SSEBroker` uses when none is given.

Fine for a single process, where the prefix is never sent anywhere. In Redis
mode it is a namespace on a server-global bus: Redis pub/sub ignores the
logical DB index (``SELECT``), so two services that share one instance — even
on different DB numbers — and both keep this prefix subscribe to the same
``sse:*`` pattern and receive each other's :meth:`SSEBroker.publish` and
:meth:`SSEBroker.broadcast` events. :class:`SSEBroker` logs a warning when it
is built with a Redis client and this prefix.
"""

BROADCAST_CHANNEL: Final[str] = "__broadcast__"
"""Channel name :meth:`SSEBroker.broadcast` reserves for itself.

Reserved rather than merely conventional: in Redis mode a broadcast is a
normal ``PUBLISH`` under this name, already reached by the ``<prefix>:*``
pattern every worker subscribes to, and the receiving side reads the name to
decide whether to fan to one channel or to all of them. A stream registered
under it would make that decision ambiguous, so :meth:`SSEBroker.register`
and :meth:`SSEBroker.publish` refuse it outright — a loud error beats a
subscriber that silently receives everything.
"""


def _reject_reserved(channel: str) -> None:
    """Refuse the channel name :meth:`SSEBroker.broadcast` owns.

    Args:
        channel (str): The channel a caller asked to use.

    Raises:
        ValueError: When ``channel`` is :data:`BROADCAST_CHANNEL`.
    """
    if channel == BROADCAST_CHANNEL:
        raise ValueError(
            f"{BROADCAST_CHANNEL!r} is reserved for SSEBroker.broadcast(); "
            "use broadcast() to reach every channel.",
        )


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
        channel_prefix: str = _DEFAULT_CHANNEL_PREFIX,
        heartbeat_seconds: float | None = 15.0,
        heartbeat_event: ServerSentEvent | Callable[[], ServerSentEvent] | None = None,
        max_queue: int = 1000,
        overflow: OverflowPolicy = "drop_oldest",
    ) -> None:
        """Initialize the broker.

        Args:
            redis (Redis | None): Async Redis client (from the ``[cache]``
                extra). ``None`` runs single-process (local fan-out only).
            channel_prefix (str): Prefix for Redis channels. In Redis mode
                give every service its own value (for example
                ``"orders-api:sse"``): Redis pub/sub is shared by every
                client of the instance regardless of the DB index, so the
                prefix is the only thing that keeps one service's events out
                of another's streams. A nested prefix (``app`` next to
                ``app:chat``) is safe since 0.340.0: the ``"<prefix>:*"``
                pattern still receives the longer prefix's messages, but each
                envelope carries the prefix that published it and the
                receiver drops the ones that are not its own. Defaults to
                ``"sse"``, which is kept for
                compatibility; with a Redis client it logs a warning naming
                the risk rather than failing, so existing single-service
                deployments keep working unchanged.
            heartbeat_seconds (float | None): Idle heartbeat for streams.
            heartbeat_event (ServerSentEvent | Callable[[], ServerSentEvent] | None):
                Frame every stream emits on an idle beat, forwarded to
                :class:`EventStream`. ``None`` keeps the ``: keepalive``
                comment. Set it here rather than subclassing: this is the
                only place a stream is constructed, so a service that
                needs a visible beat has no other seam.
            max_queue (int): Bounded-queue size for every stream opened by
                :meth:`register`. ``0`` disables the bound. Defaults to
                ``1000``.
            overflow (OverflowPolicy): Overflow policy for those streams.
                Defaults to ``"drop_oldest"``.
        """
        self._redis: Redis | None = redis
        self.channel_prefix: str = channel_prefix
        if redis is not None and channel_prefix == _DEFAULT_CHANNEL_PREFIX:
            _LOGGER.warning(
                "SSEBroker is using the shared default channel_prefix %r with "
                "Redis. Redis pub/sub ignores the DB index, so any other "
                "service on the same Redis instance with this prefix receives "
                "these events and sends its own here. Pass an app-unique "
                "channel_prefix.",
                channel_prefix,
            )
        self.heartbeat_seconds: float | None = heartbeat_seconds
        self.heartbeat_event: ServerSentEvent | Callable[[], ServerSentEvent] | None = (
            heartbeat_event
        )
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

        Raises:
            ValueError: When ``channel`` is :data:`BROADCAST_CHANNEL`,
                which :meth:`broadcast` reserves.
        """
        _reject_reserved(channel)
        stream = EventStream(
            heartbeat_seconds=self.heartbeat_seconds,
            heartbeat_event=self.heartbeat_event,
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

    def local_channels(self) -> list[str]:
        """Return the channels with at least one open stream on this worker.

        The counterpart of :meth:`local_subscribers`, which answers for one
        channel: this enumerates them, so "how many clients are connected
        right now" is a metric a service can read without touching the
        broker's internals. Local by construction — a worker knows its own
        streams and nothing about its siblings'.

        Returns:
            list[str]: The channel names, sorted, that hold a live stream
            in this process (``[]`` when nobody is connected here).
        """
        return sorted(self._channels)

    async def publish(
        self,
        channel: str,
        data: SSEData = "",
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
            data (SSEData): Payload (string, bytes or JSON-serializable).
            event (str | None): Optional SSE event name.
            id (str | None): Optional ``Last-Event-ID``.
            retry (int | None): Optional reconnect hint (ms).

        Raises:
            ValueError: When ``channel`` is :data:`BROADCAST_CHANNEL`,
                which :meth:`broadcast` reserves.
        """
        _reject_reserved(channel)
        if self._redis is None:
            await self._emit_local(
                channel,
                ServerSentEvent(data=data, event=event, id=id, retry=retry),
            )
            return
        envelope = self._envelope(data, event=event, id=id, retry=retry)
        await self._redis.publish(self._key(channel), envelope)

    async def broadcast(
        self,
        data: SSEData = "",
        *,
        event: str | None = None,
        id: str | None = None,
        retry: int | None = None,
    ) -> None:
        """Publish an event to every subscriber of every channel.

        The axis :meth:`publish` does not cover: one event to N channels
        instead of one channel to N connections. This is the global notice —
        "maintenance in 5 minutes", "a new version is available" — that a
        service otherwise has to fake by keeping its own registry of open
        channels alongside the broker's.

        In Redis mode the event is ``PUBLISH``-ed under
        :data:`BROADCAST_CHANNEL`, which the ``<prefix>:*`` pattern every
        worker already subscribes to reaches without any new subscription;
        each worker's :meth:`run` loop then fans it to **all** of its local
        streams. In single-process mode it fans out locally right away.

        A stream registered under more than one channel — which
        :meth:`register` does not produce, but a caller holding a stream can
        arrange — receives the event once: the fan-out is over the set of
        streams, not the list of channels.

        Args:
            data (SSEData): Payload (string, bytes or JSON-serializable).
            event (str | None): Optional SSE event name.
            id (str | None): Optional ``Last-Event-ID``.
            retry (int | None): Optional reconnect hint (ms).
        """
        frame = ServerSentEvent(data=data, event=event, id=id, retry=retry)
        if self._redis is None:
            await self._emit_everywhere(frame)
            return
        envelope = self._envelope(data, event=event, id=id, retry=retry)
        await self._redis.publish(self._key(BROADCAST_CHANNEL), envelope)

    async def run(self) -> None:
        """Consume Redis pub/sub and fan messages to local streams.

        Long-running: start it as a background task in the app lifespan
        and cancel it (or call :meth:`aclose`) on shutdown. A no-op in
        single-process mode.

        Each message is dispatched in isolation. The ``<prefix>:*`` pattern
        receives whatever any client of the Redis instance publishes on a
        matching channel, not only this broker's envelopes, so a frame that
        fails to decode or to fan out is logged (channel and error type,
        never the payload) and skipped. Letting it escape would end the
        loop, and with it every later delivery to this worker's streams,
        while the worker kept accepting connections.

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
                try:
                    await self._dispatch_raw(message["channel"], message["data"])
                except Exception as exc:
                    _LOGGER.warning(
                        "SSEBroker skipped an undeliverable pub/sub message on %r: %s",
                        message.get("channel"),
                        type(exc).__name__,
                    )
        finally:
            await pubsub.aclose()
            self._pubsub = None

    async def aclose(self) -> None:
        """Stop the pub/sub subscription opened by :meth:`run`."""
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None

    def _envelope(
        self,
        data: SSEData,
        *,
        event: str | None,
        id: str | None,
        retry: int | None,
    ) -> str:
        """Serialize one event for ``PUBLISH``, stamped with this prefix.

        The ``prefix`` field is what lets a receiver tell its own messages
        from a nested prefix's: ``PSUBSCRIBE app:*`` also matches every key
        a broker with prefix ``app:chat`` publishes (``app:chat:room``), and
        the channel name alone cannot say which broker sent it.

        Args:
            data (SSEData): Payload.
            event (str | None): SSE event name.
            id (str | None): ``Last-Event-ID``.
            retry (int | None): Reconnect hint (ms).

        Returns:
            str: The JSON envelope.
        """
        return json.dumps(
            {
                "data": data,
                "event": event,
                "id": id,
                "retry": retry,
                "prefix": self.channel_prefix,
            },
            default=str,
        )

    def _key(self, channel: str) -> str:
        """Return the Redis channel key for ``channel``."""
        return f"{self.channel_prefix}:{channel}"

    async def _dispatch_raw(self, channel_key: str | bytes, raw: str | bytes) -> None:
        """Decode a Redis pub/sub frame and fan it to local streams.

        The envelope is checked against the shape :meth:`publish` writes —
        a JSON object whose ``event`` and ``id`` are strings or ``None`` and
        whose ``retry`` is an integer or ``None`` — before any stream sees
        it. An envelope stamped with another ``prefix`` is dropped: it was
        published by a broker whose prefix nests under this one (``app`` and
        ``app:chat``), and reached this worker only because ``<prefix>:*``
        matches the longer key. An envelope with no ``prefix`` (written by
        an SDK before 0.340.0, during a rolling deploy) is still delivered.

        Args:
            channel_key (str | bytes): The Redis channel
                (``<prefix>:<channel>``), as delivered by pub/sub.
            raw (str | bytes): The JSON envelope payload.

        Raises:
            ValueError: When ``raw`` is not JSON (``json.JSONDecodeError``),
                or is JSON that is not a well-formed envelope.
            UnicodeDecodeError: When ``raw`` or ``channel_key`` is not UTF-8.
        """
        key = channel_key.decode() if isinstance(channel_key, bytes) else channel_key
        body = raw.decode() if isinstance(raw, bytes) else raw
        channel = key.removeprefix(f"{self.channel_prefix}:")
        payload: Any = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("SSE envelope is not a JSON object.")
        sender = payload.get("prefix")
        if sender is not None and sender != self.channel_prefix:
            return
        event: Any = payload.get("event")
        event_id: Any = payload.get("id")
        retry: Any = payload.get("retry")
        if not (
            (event is None or isinstance(event, str))
            and (event_id is None or isinstance(event_id, str))
            and (
                retry is None
                or (isinstance(retry, int) and not isinstance(retry, bool))
            )
        ):
            raise ValueError("SSE envelope has a field of the wrong type.")
        frame = ServerSentEvent(
            data=payload.get("data", ""),
            event=event,
            id=event_id,
            retry=retry,
        )
        if channel == BROADCAST_CHANNEL:
            await self._emit_everywhere(frame)
            return
        await self._emit_local(channel, frame)

    async def _emit_local(self, channel: str, event: ServerSentEvent) -> None:
        """Enqueue ``event`` on every local stream of ``channel``.

        Args:
            channel (str): The channel to fan out to.
            event (ServerSentEvent): The event to deliver.
        """
        for stream in tuple(self._channels.get(channel, set())):
            await stream.publish_event(event)

    async def _emit_everywhere(self, event: ServerSentEvent) -> None:
        """Enqueue ``event`` on every local stream, once each.

        Args:
            event (ServerSentEvent): The event to deliver.
        """
        streams: set[EventStream] = set()
        for channel_streams in tuple(self._channels.values()):
            streams.update(channel_streams)
        for stream in streams:
            await stream.publish_event(event)


__all__: list[str] = [
    "BROADCAST_CHANNEL",
    "SSEBroker",
]
