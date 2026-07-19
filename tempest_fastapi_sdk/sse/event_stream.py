"""Encoder, in-memory queue and FastAPI helper for SSE responses."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from starlette.responses import StreamingResponse

OverflowPolicy = Literal["drop_oldest", "drop_newest", "block"]
"""How :class:`EventStream` reacts when its bounded queue is full.

* ``"drop_oldest"`` (default) — evict the oldest queued event to make
  room for the new one. Keeps the freshest data; best for live tickers
  / progress where stale frames are worthless.
* ``"drop_newest"`` — discard the incoming event, keep the backlog.
  Best when every early event matters and later ones are redundant.
* ``"block"`` — apply backpressure: :meth:`EventStream.publish` waits
  until the consumer drains a slot. Use only when the producer is
  dedicated to a single connection and losing events is unacceptable.
"""

SSEData: TypeAlias = (
    str | bytes | Mapping[str, Any] | Sequence[Any] | int | float | bool | None
)
"""Payload accepted by the SSE publishers (:meth:`EventStream.publish`,
:meth:`SSEBroker.publish`, and the :class:`ServerSentEvent` ``data`` field).

``str`` and ``bytes`` are written to the wire as-is; every other value is
JSON-encoded (``json.dumps(..., default=str)``) before transmission. The
union spells out the JSON value shapes — objects (``Mapping``), arrays
(``Sequence``) and the scalars ``str`` / ``int`` / ``float`` / ``bool`` /
``None``. To send an arbitrary object that only serializes via
``default=str`` (e.g. a bare ``UUID``), wrap it in ``str(...)`` or a dict
first.
"""

_SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
"""Default response headers for SSE streams.

``X-Accel-Buffering: no`` disables nginx response buffering so each
event reaches the browser immediately; the other two stop
intermediate proxies from caching the long-lived response.
"""


@dataclass(slots=True)
class ServerSentEvent:
    """A single SSE frame.

    Encodes to the line-based wire format defined by the spec
    (https://html.spec.whatwg.org/multipage/server-sent-events.html).
    ``data`` may be a string, bytes, or any JSON-serializable Python
    object — non-string payloads are JSON-encoded before transmission.

    Attributes:
        data (SSEData): The event payload.
        event (str | None): Optional event name; the browser routes
            ``EventSource.addEventListener(name, ...)`` by this.
        id (str | None): Optional ``Last-Event-ID`` value used by
            the browser to resume after a reconnect.
        retry (int | None): Reconnection delay (milliseconds) the
            browser should use after a connection drop.
        comment (str | None): Optional comment line prepended to the
            frame (renders as ``: comment``); useful for heartbeats.
    """

    data: SSEData = ""
    event: str | None = None
    id: str | None = None
    retry: int | None = None
    comment: str | None = None

    def encode(self) -> str:
        """Render the event as the wire-format string.

        Returns:
            str: The encoded event, including the trailing blank line
            that marks frame boundaries.
        """
        lines: list[str] = []
        if self.comment is not None:
            lines.append(f": {self.comment}")
        if self.event is not None:
            lines.append(f"event: {self.event}")
        if self.id is not None:
            lines.append(f"id: {self.id}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")

        payload: str
        if isinstance(self.data, bytes):
            payload = self.data.decode("utf-8")
        elif isinstance(self.data, str):
            payload = self.data
        else:
            payload = json.dumps(self.data, default=str)

        for chunk in payload.splitlines() or [""]:
            lines.append(f"data: {chunk}")
        return "\n".join(lines) + "\n\n"


class EventStream:
    """Async in-memory queue feeding one SSE HTTP connection.

    A handler builds one stream per client request, ``publish``-es
    events from anywhere in the application (background tasks,
    websockets, dependency callbacks), and passes :meth:`stream` to
    :func:`sse_response`. A ``None`` enqueued by :meth:`close`
    terminates the iteration so the response completes cleanly.

    Heartbeats are emitted as SSE comments
    (``: keepalive`` lines) when the queue stays empty for longer
    than ``heartbeat_seconds``; this keeps load-balancers from
    closing idle TCP connections.

    **Backpressure.** The internal queue is **bounded** (``max_queue``)
    so a slow or stalled client cannot make a busy producer grow the
    queue without limit and exhaust memory. When the queue fills, the
    ``overflow`` policy decides what gives — evict the oldest event
    (default), drop the incoming one, or block the producer. The
    :meth:`close` sentinel always gets through, evicting a queued event
    if that is the only way to fit. :attr:`dropped_events` counts every
    event lost to overflow so you can surface it in metrics / logs.

    Attributes:
        heartbeat_seconds (float | None): Idle interval that triggers
            a comment heartbeat. ``None`` disables heartbeats.
        max_queue (int): Maximum number of buffered events. ``0``
            disables the bound (unbounded — pre-0.91 behavior).
        overflow (OverflowPolicy): What happens when the queue is full.
    """

    def __init__(
        self,
        *,
        heartbeat_seconds: float | None = 15.0,
        max_queue: int = 1000,
        overflow: OverflowPolicy = "drop_oldest",
    ) -> None:
        """Initialize the stream.

        Args:
            heartbeat_seconds (float | None): Idle interval before
                a comment heartbeat is emitted.
            max_queue (int): Maximum buffered events before ``overflow``
                kicks in. ``0`` disables the bound. Defaults to ``1000``.
            overflow (OverflowPolicy): Overflow reaction. Defaults to
                ``"drop_oldest"``.
        """
        # Only "block" needs a hard-bounded queue (real backpressure). The
        # drop policies keep an unbounded queue and enforce the bound
        # manually on data events, so the close sentinel is never blocked
        # or evicted — the stream can always be terminated.
        native_maxsize: int = (
            max_queue if (max_queue > 0 and overflow == "block") else 0
        )
        self._queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue(
            maxsize=native_maxsize,
        )
        self.heartbeat_seconds: float | None = heartbeat_seconds
        self.max_queue: int = max_queue
        self.overflow: OverflowPolicy = overflow
        self._dropped: int = 0

    @property
    def dropped_events(self) -> int:
        """Return how many events were discarded by the overflow policy.

        Returns:
            int: Count of events lost to ``drop_oldest`` / ``drop_newest``
            since the stream was created. Always ``0`` under ``"block"``
            or an unbounded queue.
        """
        return self._dropped

    async def _put(self, item: ServerSentEvent | None) -> None:
        """Enqueue ``item`` honoring ``max_queue`` and ``overflow``.

        The ``None`` close sentinel always gets in — if the queue is
        full it evicts one queued event to make room, so a stream can
        never be wedged open by a backlog.

        Args:
            item (ServerSentEvent | None): The event, or ``None`` to
                signal end-of-stream.
        """
        # The close sentinel, "block" mode and unbounded streams put
        # straight through — no data event is ever dropped for them.
        if item is None or self.max_queue <= 0 or self.overflow == "block":
            await self._queue.put(item)
            return
        if self._queue.qsize() < self.max_queue:
            self._queue.put_nowait(item)
            return
        if self.overflow == "drop_newest":
            self._dropped += 1
            return
        # drop_oldest: evict the stalest queued event to fit the new one.
        try:
            self._queue.get_nowait()
            self._dropped += 1
        except asyncio.QueueEmpty:  # pragma: no cover - race: drained meanwhile
            pass
        self._queue.put_nowait(item)

    async def publish(
        self,
        data: SSEData = "",
        *,
        event: str | None = None,
        id: str | None = None,
        retry: int | None = None,
    ) -> None:
        """Enqueue a new event for delivery.

        Args:
            data (SSEData): The payload (string, bytes or JSON-serializable).
            event (str | None): Optional event name.
            id (str | None): Optional Last-Event-ID.
            retry (int | None): Optional reconnect hint in milliseconds.
        """
        await self._put(
            ServerSentEvent(data=data, event=event, id=id, retry=retry),
        )

    async def publish_event(self, event: ServerSentEvent) -> None:
        """Enqueue a pre-built :class:`ServerSentEvent`.

        Args:
            event (ServerSentEvent): The event to enqueue.
        """
        await self._put(event)

    async def close(self) -> None:
        """Signal the stream to end after draining queued events."""
        await self._put(None)

    def response(
        self,
        *,
        on_disconnect: Callable[[], Awaitable[None] | None] | None = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        """Return an SSE :class:`StreamingResponse` wired to this stream.

        Convenience over ``sse_response(self.stream(), ...)`` that also
        threads ``on_disconnect`` — run when the client goes away — so a
        producer bound to this connection is torn down without hand-rolled
        ``try/finally`` boilerplate at every call site.

        Args:
            on_disconnect (Callable[[], Awaitable[None] | None] | None):
                Called (awaited if a coroutine) when the response
                generator closes — i.e. the client disconnected or the
                stream ended. Cancel the producer task here.
            status_code (int): HTTP status. Defaults to ``200``.
            headers (dict[str, str] | None): Extra headers to attach.

        Returns:
            StreamingResponse: A ready-to-return SSE response.
        """
        return sse_response(
            self.stream(),
            on_disconnect=on_disconnect,
            status_code=status_code,
            headers=headers,
        )

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield encoded SSE bytes until :meth:`close` is invoked.

        Yields:
            bytes: An encoded SSE frame ready to write to the wire.
        """
        while True:
            event: ServerSentEvent | None
            if self.heartbeat_seconds is not None:
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=self.heartbeat_seconds,
                    )
                except TimeoutError:
                    yield ServerSentEvent(comment="keepalive").encode().encode("utf-8")
                    continue
            else:
                event = await self._queue.get()
            if event is None:
                return
            yield event.encode().encode("utf-8")


async def _guard_stream(
    stream: AsyncIterable[bytes],
    on_disconnect: Callable[[], Awaitable[None] | None] | None,
) -> AsyncIterator[bytes]:
    """Relay ``stream`` and run ``on_disconnect`` once it finishes.

    Starlette closes this generator when the client disconnects (or the
    inner stream ends), so the ``finally`` is the single place a bound
    producer / channel registration is guaranteed to be torn down.

    Args:
        stream (AsyncIterable[bytes]): The wrapped byte stream.
        on_disconnect (Callable[[], Awaitable[None] | None] | None):
            Cleanup callback, awaited when it returns a coroutine.

    Yields:
        bytes: Each frame produced by ``stream``.
    """
    try:
        async for chunk in stream:
            yield chunk
    finally:
        if on_disconnect is not None:
            result = on_disconnect()
            if inspect.isawaitable(result):
                await result


def sse_response(
    stream: AsyncIterable[bytes],
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    on_disconnect: Callable[[], Awaitable[None] | None] | None = None,
) -> StreamingResponse:
    """Wrap ``stream`` in a Starlette ``text/event-stream`` response.

    Adds the SSE-specific headers (``Cache-Control: no-cache``,
    ``Connection: keep-alive``, ``X-Accel-Buffering: no``) so
    intermediate proxies don't buffer or cache the long-lived
    response. Caller-supplied ``headers`` are layered **below** the
    SSE defaults so the three critical headers above cannot be
    accidentally overridden — pass extra metadata, not replacements.

    Pass ``on_disconnect`` to run cleanup when the client goes away —
    the response generator's ``finally`` is the one place guaranteed to
    fire on disconnect, so this is where you cancel a bound producer
    task or call :meth:`SSEBroker.unregister`. It removes the
    hand-rolled ``try/finally`` wrapper the recipe used to require.

    Args:
        stream (AsyncIterable[bytes]): The byte stream produced by
            :meth:`EventStream.stream` (or any compatible generator).
        status_code (int): HTTP status code. Defaults to ``200``.
        headers (dict[str, str] | None): Extra headers to attach.
        on_disconnect (Callable[[], Awaitable[None] | None] | None):
            Cleanup callback run (and awaited if a coroutine) when the
            stream ends or the client disconnects. ``None`` skips it.

    Returns:
        StreamingResponse: A ready-to-return SSE response.
    """
    merged: dict[str, str] = {**(headers or {}), **_SSE_HEADERS}
    body: AsyncIterable[bytes] = (
        stream if on_disconnect is None else _guard_stream(stream, on_disconnect)
    )
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        status_code=status_code,
        headers=merged,
    )


__all__: list[str] = [
    "EventStream",
    "OverflowPolicy",
    "SSEData",
    "ServerSentEvent",
    "sse_response",
]
