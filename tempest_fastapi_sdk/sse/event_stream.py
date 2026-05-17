"""Encoder, in-memory queue and FastAPI helper for SSE responses."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Any

from starlette.responses import StreamingResponse

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
        data (Any): The event payload.
        event (str | None): Optional event name; the browser routes
            ``EventSource.addEventListener(name, ...)`` by this.
        id (str | None): Optional ``Last-Event-ID`` value used by
            the browser to resume after a reconnect.
        retry (int | None): Reconnection delay (milliseconds) the
            browser should use after a connection drop.
        comment (str | None): Optional comment line prepended to the
            frame (renders as ``: comment``); useful for heartbeats.
    """

    data: Any = ""
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

    Attributes:
        heartbeat_seconds (float | None): Idle interval that triggers
            a comment heartbeat. ``None`` disables heartbeats.
    """

    def __init__(self, *, heartbeat_seconds: float | None = 15.0) -> None:
        """Initialize the stream.

        Args:
            heartbeat_seconds (float | None): Idle interval before
                a comment heartbeat is emitted.
        """
        self._queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()
        self.heartbeat_seconds: float | None = heartbeat_seconds

    async def publish(
        self,
        data: Any = "",
        *,
        event: str | None = None,
        id: str | None = None,
        retry: int | None = None,
    ) -> None:
        """Enqueue a new event for delivery.

        Args:
            data (Any): The payload (string, bytes or JSON-serializable).
            event (str | None): Optional event name.
            id (str | None): Optional Last-Event-ID.
            retry (int | None): Optional reconnect hint in milliseconds.
        """
        await self._queue.put(
            ServerSentEvent(data=data, event=event, id=id, retry=retry),
        )

    async def publish_event(self, event: ServerSentEvent) -> None:
        """Enqueue a pre-built :class:`ServerSentEvent`.

        Args:
            event (ServerSentEvent): The event to enqueue.
        """
        await self._queue.put(event)

    async def close(self) -> None:
        """Signal the stream to end after draining queued events."""
        await self._queue.put(None)

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


def sse_response(
    stream: AsyncIterable[bytes],
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """Wrap ``stream`` in a Starlette ``text/event-stream`` response.

    Adds the SSE-specific headers (``Cache-Control: no-cache``,
    ``Connection: keep-alive``, ``X-Accel-Buffering: no``) so
    intermediate proxies don't buffer or cache the long-lived
    response. Caller-supplied ``headers`` are layered **below** the
    SSE defaults so the three critical headers above cannot be
    accidentally overridden — pass extra metadata, not replacements.

    Args:
        stream (AsyncIterable[bytes]): The byte stream produced by
            :meth:`EventStream.stream` (or any compatible generator).
        status_code (int): HTTP status code. Defaults to ``200``.
        headers (dict[str, str] | None): Extra headers to attach.

    Returns:
        StreamingResponse: A ready-to-return SSE response.
    """
    merged: dict[str, str] = {**(headers or {}), **_SSE_HEADERS}
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        status_code=status_code,
        headers=merged,
    )


__all__: list[str] = [
    "EventStream",
    "ServerSentEvent",
    "sse_response",
]
