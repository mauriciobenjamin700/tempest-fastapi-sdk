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

## Broadcast to many clients (`SSEBroker`)

`EventStream` is **one** connection. To send the same event to every
client of a channel (e.g. a user's devices, or a topic), the SDK ships
`SSEBroker` — a per-channel stream registry plus fan-out. The channel is
any string (a user id, a room slug...).

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import SSEBroker

broker = SSEBroker()   # singleton — keep on app.state and inject via Depends
```

```python
# src/api/routers/feed.py
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import SSEBroker, sse_response

router = APIRouter()


@router.get("/feed")
async def feed(
    user_id: UUID = Depends(get_current_user_id),
    broker: SSEBroker = Depends(get_broker),
) -> StreamingResponse:
    """Subscribe the client to its user's channel."""
    channel = str(user_id)
    stream = broker.register(channel)

    async def lifecycle_aware() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream.stream():
                yield chunk
        finally:
            broker.unregister(channel, stream)   # client left

    return sse_response(lifecycle_aware())


# From anywhere (queue handler, another endpoint):
# await broker.publish(str(user_id), {"text": "New order"}, event="notice")
```

### Multi-worker: Redis bridge (ready, no extra code)

An in-memory `SSEBroker` lives in **one** worker — with `--workers N` a
`publish` only reaches the clients pinned to that process. Pass a Redis
client and the **same `broker`** publishes via Redis `PUBLISH`; a
background task (`run()`) `PSUBSCRIBE`-s and relays to **each** worker's
local streams. Same call site, now horizontal:

```python
# src/api/app.py
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from tempest_fastapi_sdk import SSEBroker

redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
broker = SSEBroker(redis=redis, channel_prefix="sse")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(broker.run())   # subscribe Redis + fan out
    try:
        yield
    finally:
        await broker.aclose()
        task.cancel()


app = FastAPI(lifespan=lifespan)
# broker.publish(...) on any worker -> reaches ALL workers
```

!!! tip "Start simple, scale later"
    Without Redis, `SSEBroker()` already covers a single process. When you
    need multiple workers/hosts, just inject a Redis client and start
    `run()` in the lifespan — no endpoint changes. `publish` becomes
    cross-process for free.

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
- Broadcast = `SSEBroker` (per-channel stream registry); multi-worker = pass a Redis client + start `broker.run()` in the lifespan (same call site).
- `tempest-react-sdk` `createEventStream`/`useEventStream` consumes with reconnect; `namedEvents` ↔ `publish(event=)`.
