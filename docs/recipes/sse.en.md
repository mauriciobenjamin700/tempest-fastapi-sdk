# Server-Sent Events (SSE)

SSE pushes data from the server to the browser over **one long-lived HTTP
connection**, no polling. It's the simplest path to "one-way real time":
a notification feed, a progress bar, a price ticker, live logs.

!!! info "SSE vs WebSocket vs Web Push"
    - **SSE** — server → client, text only, auto-reconnects, runs over
      plain HTTP. Use when the client only **receives**.
    - **WebSocket** — bidirectional, binary, more complex. Use when the
      client also **sends** often. See [WebSocket](websocket.md).
    - **Web Push** — arrives with the **page closed** (Service Worker).
      See [Web Push](webpush.md).

The SDK ships three pieces: `EventStream` (an in-memory async queue
feeding one connection), `ServerSentEvent` (encodes a frame in the spec
wire format) and `sse_response` (wraps the stream in a
`StreamingResponse` with the right headers — `Cache-Control: no-cache`,
`Connection: keep-alive`, `X-Accel-Buffering: no` to disable nginx
buffering).

## One SSE endpoint

Create an `EventStream` per request, publish from a producer, and tie the
producer's lifecycle to the client connection — if the client drops, the
producer stops.

```python
# src/api/routers/events.py
import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import EventStream, sse_response

router = APIRouter()


@router.get("/events")
async def events() -> StreamingResponse:
    """Emit 3 SSE frames then close the stream."""
    stream = EventStream(heartbeat_seconds=15.0)

    async def producer() -> None:
        try:
            for n in range(1, 4):
                await stream.publish({"n": n}, event="counter", id=str(n))
                await asyncio.sleep(1)
        finally:
            await stream.close()

    task = asyncio.create_task(producer())

    async def lifecycle_aware() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream.stream():
                yield chunk
        finally:
            task.cancel()  # client disconnected -> don't leak the producer

    return sse_response(lifecycle_aware())
```

!!! warning "Always tie the producer to the connection"
    SSE streams are long-lived. If the client disconnects mid-stream you
    don't want the producer running forever. The outer generator's
    `finally` runs when the response closes — cancel the producer there.

## Anatomy of an event

`publish()` takes the four spec fields:

```python
await stream.publish(
    {"orderId": "abc", "status": "paid"},  # data: auto-JSON
    event="order_update",                  # event: front-end listener name
    id="42",                               # id: becomes Last-Event-ID (resume)
    retry=3000,                            # retry: reconnect hint (ms)
)
```

| Field | What it does |
| --- | --- |
| `data` | Payload. String/bytes go raw; any object becomes JSON. |
| `event` | Event name — the front listens with `addEventListener(name)`. Without it, falls back to `"message"`. |
| `id` | Becomes `Last-Event-ID`; the browser resends it on reconnect so you can resume. |
| `retry` | Suggested reconnect delay (ms). |

`heartbeat_seconds` emits an SSE **comment** (`: keepalive`) while the
stream is idle so load-balancers don't cut the connection. Comments are
**invisible** to `EventSource` — they fire no listener, they just keep
the socket alive. `None` disables the heartbeat.

## Broadcast to many clients

`EventStream` is one connection. To send the same event to everyone (or
to a user's devices), keep a registry of streams and publish to all — a
simple "hub":

```python
# src/services/sse_hub.py
from uuid import UUID

from tempest_fastapi_sdk import EventStream


class SSEHub:
    """In-memory registry of open SSE streams, per user."""

    def __init__(self) -> None:
        self._streams: dict[UUID, set[EventStream]] = {}

    def register(self, user_id: UUID) -> EventStream:
        """Open a stream for a client and register it."""
        stream = EventStream(heartbeat_seconds=15.0)
        self._streams.setdefault(user_id, set()).add(stream)
        return stream

    def unregister(self, user_id: UUID, stream: EventStream) -> None:
        """Drop a closed stream from the registry."""
        streams = self._streams.get(user_id)
        if streams:
            streams.discard(stream)
            if not streams:
                del self._streams[user_id]

    async def publish_to_user(self, user_id: UUID, data: object, *, event: str) -> int:
        """Publish an event to all of a user's open streams."""
        streams = self._streams.get(user_id, set())
        for stream in streams:
            await stream.publish(data, event=event)
        return len(streams)
```

```python
# src/api/routers/feed.py
@router.get("/feed")
async def feed(
    user_id: UUID = Depends(get_current_user_id),
    hub: SSEHub = Depends(get_sse_hub),       # singleton on app.state
) -> StreamingResponse:
    """Subscribe the client to its user's feed."""
    stream = hub.register(user_id)

    async def lifecycle_aware() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream.stream():
                yield chunk
        finally:
            hub.unregister(user_id, stream)

    return sse_response(lifecycle_aware())


# From anywhere (queue handler, another endpoint):
# await hub.publish_to_user(user_id, {"text": "New order"}, event="notice")
```

!!! danger "In-memory hub = single process"
    The `SSEHub` above lives in **one** worker's memory. With multiple
    workers (Gunicorn/Uvicorn `--workers N`), a publish only reaches the
    clients pinned to that process. For multi-process, back the hub with a
    Pub/Sub (Redis `PUBLISH`/`SUBSCRIBE`): each worker subscribes to the
    channel and relays to its local streams.

## Aligned with tempest-react-sdk

`tempest-react-sdk`'s `createEventStream` / `useEventStream`
([repo](https://github.com/mauriciobenjamin700/tempest-react-sdk))
consumes these endpoints with built-in exponential-backoff reconnect:

```typescript
import { createEventStream } from "@mauriciobenjamin700/tempest-react-sdk";

const stream = createEventStream<{ text: string }>("/feed", {
    withCredentials: true,        // sends the auth cookie on the handshake
    namedEvents: ["notice"],      // <- matches publish(event="notice")
    onMessage: (m) => console.log(m.event, m.data),  // data already JSON-parsed
});
// stream.close() to tear down; stream.reconnect() to force a reconnect
```

!!! tip "Heartbeat: comment vs a `ping` event"
    `EventStream`'s heartbeat is a **comment** — `EventSource` ignores it,
    so the react-sdk doesn't even need `heartbeatEvents`. If you prefer a
    visible **named** heartbeat, publish
    `await stream.publish("", event="ping")` and set
    `heartbeatEvents: ["ping"]` on the front (its default).

Alignment points:

- `publish(event="x")` ↔ `namedEvents: ["x"]` + `onMessage`.
- non-string `data` becomes JSON ↔ the react default parser decodes JSON.
- `id=` ↔ `Last-Event-ID` resent on reconnect (resume where you left off).
- cookie auth ↔ `withCredentials: true`.

## Recap

- `EventStream` (one per connection) + `sse_response` — an SSE endpoint with headers set.
- Tie the producer to the connection lifecycle (`finally` → cancel/unregister).
- `publish(data, event=, id=, retry=)` covers the 4 spec fields; non-string `data` becomes JSON.
- Heartbeat is a comment (invisible to EventSource); `None` disables it.
- Broadcast = a registry of streams (hub); multi-worker needs Pub/Sub (Redis).
- `tempest-react-sdk` `createEventStream`/`useEventStream` consumes with reconnect; `namedEvents` ↔ `publish(event=)`.
