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
buffering). Day to day you call the shortcuts `EventStream.response(...)` /
`SSEBroker.response(channel)`, which wrap `sse_response` under the hood; reach
for raw `sse_response` only when you want to drive the generator by hand.

!!! info "Do you need to install anything? SSE is built in"
    `EventStream`, `ServerSentEvent`, `sse_response` and the in-memory
    `SSEBroker` are part of the **core** — no extra of their own, they ship
    with `tempest-fastapi-sdk` (they depend only on `starlette`, which FastAPI
    already pulls in). There is no `[sse]` extra. Only the **Redis bridge**
    (multi-worker) needs the `[cache]` extra —
    `uv add "tempest-fastapi-sdk[cache]"`, which pulls in `redis`. Cookie/query
    auth uses `JWTUtils` from the `[auth]` extra.

!!! tip "New in v0.91"
    - **Backpressure** — the `EventStream` queue is now **bounded**
      (`max_queue`, default `1000`): a slow client can't grow memory
      without limit. The `overflow` policy decides what gives. See
      [Backpressure](#backpressure-bounded-queue).
    - **Lifecycle without boilerplate** — `sse_response(..., on_disconnect=)`,
      `EventStream.response(...)` and `SSEBroker.response(channel)` tear
      down the producer / unregister the channel on their own when the
      client drops.
    - **Query-string auth** for cookieless clients (`EventSource`). See
      [Authentication](#authentication-cookie-or-query-string).

## One SSE endpoint

Create an `EventStream` per request, publish from a producer, and tie the
producer's lifecycle to the client connection — if the client drops, the
producer stops.

```python
# src/api/routers/events.py
import asyncio

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import EventStream

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

    # on_disconnect runs when the client drops OR the stream ends:
    # that's where you cancel the producer so it doesn't leak.
    return stream.response(on_disconnect=task.cancel)
```

!!! warning "Always tie the producer to the connection"
    SSE streams are long-lived. If the client disconnects mid-stream you
    don't want the producer running forever. Pass `on_disconnect=` to
    `EventStream.response` (or `sse_response`) — it runs in the response
    generator's `finally`, the one place that fires on disconnect.

Start the API and watch the raw frames in your terminal — `curl -N`
disables buffering and prints each frame as it arrives:

```bash
curl -N http://127.0.0.1:8000/events
```

```text
event: counter
id: 1
data: {"n": 1}

event: counter
id: 2
data: {"n": 2}

event: counter
id: 3
data: {"n": 3}
```

Note the spec wire format: each frame is a block of `field: value` lines
(`event`, `id`, then `data`), and the **blank line** (`\n\n`) separates one
frame from the next. Because you passed a dict, `data` was JSON-serialized
for you.

??? note "Before v0.91: hand-rolled `try/finally`"
    Up to v0.90 you wrapped `stream()` in an outer generator just to get
    the `finally`. `on_disconnect=` replaces that boilerplate:

    ```python
    from collections.abc import AsyncIterator
    from tempest_fastapi_sdk import sse_response

    async def lifecycle_aware() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream.stream():
                yield chunk
        finally:
            task.cancel()

    return sse_response(lifecycle_aware())
    ```

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

## Backpressure (bounded queue)

If a client **stops reading** (backgrounded tab, bad network) but the
producer keeps publishing, the `EventStream` queue would grow forever — a
classic memory leak. So the queue is **bounded**: `max_queue` (default
`1000`) plus an `overflow` policy that decides what to do when it fills.

```python
from tempest_fastapi_sdk import EventStream

# Live ticker: a stale frame is worthless -> drop the oldest.
stream = EventStream(max_queue=500, overflow="drop_oldest")
```

| `overflow` | When the queue fills | Use when |
| --- | --- | --- |
| `"drop_oldest"` (default) | Evict the **oldest** event | Live data: ticker, progress, telemetry — only the recent state matters. |
| `"drop_newest"` | Discard the **incoming** event | The start of the stream matters more than the end. |
| `"block"` | Hold `publish()` until a slot frees | Producer dedicated to **one** connection and losing an event is unacceptable. |

!!! danger "`block` can stall a shared producer"
    With `overflow="block"`, a slow client **holds** `publish()`. If the
    same producer feeds many clients (fan-out), one bad client stalls them
    all. Only use `block` when the producer serves **one** connection.

The `close()` sentinel is **never** dropped or blocked — the stream always
terminates. `stream.dropped_events` counts how many events were lost to
overflow, so you can surface it in metrics/logs:

```python
if stream.dropped_events:
    logger.warning("Slow SSE client: %d events dropped", stream.dropped_events)
```

!!! tip "Back to the old behavior"
    `max_queue=0` disables the bound (unbounded queue, pre-0.91). Only do
    this if you're sure the producer stops together with the connection.

## Broadcast to many clients (`SSEBroker`)

`EventStream` is **one** connection. To send the same event to every
client of a channel (e.g. a user's devices, or a topic), the SDK ships
`SSEBroker` — a per-channel stream registry plus fan-out. The channel is
any string (a user id, a room slug...).

It takes **three steps**: create the broker once, keep that instance on the
app (so everyone uses the same one), and inject it into endpoints.

#### Step 1 — create the broker and the wiring

`SSEBroker()` is a **process-wide singleton**: every open channel and stream
lives inside it. So it must be **one** instance shared across the whole app —
if each request made its own, a `publish` on one broker would never reach the
streams pinned to another.

```python
# src/api/dependencies/resources.py
from fastapi import FastAPI, Request

from tempest_fastapi_sdk import SSEBroker

broker = SSEBroker()


def register_broker(app: FastAPI) -> None:
    """Store the singleton broker on app.state (call it in create_app)."""
    app.state.broker = broker


def get_broker(request: Request) -> SSEBroker:
    """Return the shared broker from app.state for use in Depends()."""
    return request.app.state.broker
```

What each part does:

- `broker = SSEBroker()` — creates the broker at module **import**. Since the
  module is imported once, this is the same object for everyone who uses it.
- `register_broker(app)` — pins the broker on `app.state.broker`. You call it
  **once** when building the app (inside `create_app` or the lifespan):
  `register_broker(app)`.
- `get_broker(request)` — returns `request.app.state.broker`. This is what
  endpoints receive via `Depends(get_broker)`, guaranteeing they all talk to
  the **same** instance.

#### Step 2 — the subscribe endpoint

The client opens `GET /feed`; the endpoint subscribes it to its own user
channel and returns the stream. One line does it all: `broker.response(channel)`.

```python
# src/api/routers/feed.py
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import SSEBroker

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.resources import get_broker

router = APIRouter()


@router.get("/feed")
async def feed(
    user_id: UUID = Depends(get_current_user_id),
    broker: SSEBroker = Depends(get_broker),
) -> StreamingResponse:
    """Subscribe the caller to their own user channel and stream it."""
    return broker.response(str(user_id))
```

Step by step, on each `GET /feed`:

1. `Depends(get_current_user_id)` resolves **who** the client is. Their id
   becomes the channel name — each user gets their own, isolated from others.
2. `Depends(get_broker)` hands over the shared broker (the one from Step 1).
3. `broker.response(str(user_id))` does **three things in a single call**:
     - **register** — creates a fresh `EventStream` and subscribes it to the
       `user_id` channel;
     - **stream** — returns a `StreamingResponse` with the SSE headers already
       set (the client starts receiving);
     - **unregister** — wires an `on_disconnect` that removes this stream from
       the channel when the client drops.

!!! tip "Why `broker.response()` doesn't leak streams"
    The three steps (register → stream → unregister) are tied together in one
    call. The `unregister` runs in the response generator's `finally` — the one
    point that fires on disconnect — so there's no `try/finally` for you to
    forget: every client that leaves cleans up its own registration. And
    `SSEBroker(max_queue=..., overflow=...)` applies the same backpressure
    policy (see above) to every stream the broker opens.

### Firing from the domain (controller)

Step 2 showed the **subscribe** side (the client joins a channel). What's left
is the **publish** side — the *broadcast* itself.

**What "broadcast" means here:** the broker keeps, per channel, the list of
subscribed streams. When you call `broker.publish("<channel>", ...)`, it walks
**every** stream on that channel and delivers the same event to each. One
`publish` → N clients. If the channel is a user's id and they have two tabs
open, both receive it; if nobody is subscribed, the `publish` is a no-op (no
error).

The **controller** is what fires the `publish`, when a business event happens
(order created, payment confirmed, new message). It orchestrates the service
(business rule) and the broker (live notification), using the id of the user to
notify as the channel.

```python
# src/controllers/order.py
from tempest_fastapi_sdk import SSEBroker

from src.schemas import OrderCreateSchema, OrderResponseSchema
from src.services import OrderService


class OrderController:
    """Orchestrates order creation and the live seller notification."""

    def __init__(self, order_service: OrderService, broker: SSEBroker) -> None:
        """Wire the order service and the SSE broker.

        Args:
            order_service (OrderService): Order business logic.
            broker (SSEBroker): Fan-out broker for live notifications.
        """
        self.order_service = order_service
        self.broker = broker

    async def create_order(self, data: OrderCreateSchema) -> OrderResponseSchema:
        """Create an order and notify the seller in real time.

        Args:
            data (OrderCreateSchema): The order creation payload.

        Returns:
            The created order.
        """
        order = await self.order_service.create(data)
        await self.broker.publish(
            str(order.seller_id),   # channel = id of whoever gets the notice
            {"order_id": str(order.id), "total": str(order.total)},
            event="order_created",
            id=str(order.id),
        )
        return order
```

The heart of it is the `broker.publish(...)` call, argument by argument:

- **1st argument (`str(order.seller_id)`)** — the **channel**. Sends the event
  to every stream subscribed to that id (here, the seller's devices). This is
  why the subscribe endpoint uses the user's id as the channel: both sides must
  agree on the same string.
- **2nd argument (the dict)** — the **payload** (SSE `data`). A non-string
  object becomes JSON automatically (see [Anatomy](#anatomy-of-an-event)).
- **`event="order_created"`** — the event **name**; the front listens with
  `addEventListener("order_created", ...)`.
- **`id=str(order.id)`** — becomes `Last-Event-ID`, so the client can resume
  from the right spot if it reconnects.

Notice the controller **never** touches `EventStream` or an HTTP response: it
just hands the event to the broker, which handles the fan-out. Publishing is
fire-and-forget.

The provider builds the controller with its service and the broker injected —
the same `get_broker` from Step 1:

```python
# src/api/dependencies/controllers.py
from fastapi import Depends

from tempest_fastapi_sdk import SSEBroker

from src.api.dependencies.resources import get_broker
from src.api.dependencies.services import get_order_service
from src.controllers import OrderController
from src.services import OrderService


def get_order_controller(
    order_service: OrderService = Depends(get_order_service),
    broker: SSEBroker = Depends(get_broker),
) -> OrderController:
    """Build an OrderController with its service and the SSE broker."""
    return OrderController(order_service, broker)
```

The router just receives the controller via `Depends` and delegates — no
business rule, no loose `publish` in the route:

```python
# src/api/routers/orders.py
from fastapi import APIRouter, Depends

from src.api.dependencies.controllers import get_order_controller
from src.controllers import OrderController
from src.schemas import OrderCreateSchema, OrderResponseSchema

router = APIRouter()


@router.post("/orders")
async def create_order(
    data: OrderCreateSchema,
    controller: OrderController = Depends(get_order_controller),
) -> OrderResponseSchema:
    """Create an order; the seller gets a live SSE notification."""
    return await controller.create_order(data)
```

A buyer with `GET /feed` open receives it instantly:

```text
event: order_created
id: 9f3a...
data: {"order_id": "9f3a...", "total": "149.90"}
```

!!! tip "Publishing from outside the request (queue, task, webhook)"
    `broker.publish` is just a coroutine — call it from anywhere that has the
    broker: a FastStream consumer, a TaskIQ task, a webhook. It only reaches
    who is **connected right now** (the channel registration drops on
    disconnect); for a **durable** notification, persist it in the database and
    treat SSE as the live layer on top. In multi-worker mode (Redis), `publish`
    reaches the worker the client is pinned to — see below.

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
app.state.broker = broker   # the same get_broker from above resolves this
# broker.publish(...) on any worker -> reaches ALL workers
```

!!! tip "Start simple, scale later"
    Without Redis, `SSEBroker()` already covers a single process. When you
    need multiple workers/hosts, just inject a Redis client and start
    `run()` in the lifespan — no endpoint changes. `publish` becomes
    cross-process for free. `redis` comes from the `[cache]` extra
    (`uv add "tempest-fastapi-sdk[cache]"`).

## Authentication (cookie or query string)

Here's the SSE gotcha: the browser's native `EventSource` **can't** send
a header. No `Authorization: Bearer` on the handshake. So there are two
ways to authenticate the stream.

### Preferred: session cookie

If the front is on the **same origin** as the API, use an `HttpOnly`
cookie. The browser sends it on its own when you open with
`withCredentials`:

```javascript
const es = new EventSource("/api/feed", { withCredentials: true });
```

On the backend, the SDK already reads the token from the cookie — the
same seam as `make_auth_router`'s cookie delivery mode:

```python
# src/api/dependencies/auth.py
from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency

tokens = JWTUtils(secret=settings.JWT_SECRET)

current_user = make_jwt_user_dependency(
    tokens,
    load_user,
    cookie_name="access_token",   # <- EventSource + withCredentials
)
```

!!! check "Why cookie is better"
    The token stays out of the URL: no leak into access logs, browser
    history or the `Referer` header. `HttpOnly` also keeps the token out
    of JavaScript's reach (XSS defense). Prefer this path **whenever** the
    origin is shared.

### Cookieless alternative: token in the query string

Without a session cookie (front on a **different origin**, a mobile app
opening a raw `EventSource`, an environment where `withCredentials` isn't
an option), pass the **access token** in the query string. As of v0.91
the dependency accepts this via `query_param`:

```python
# src/api/dependencies/auth.py
from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency

tokens = JWTUtils(secret=settings.JWT_SECRET)

# Lookup order: header -> cookie -> query string.
current_user = make_jwt_user_dependency(
    tokens,
    load_user,
    query_param="access_token",   # <- ?access_token=<jwt>
)
```

```python
# src/api/routers/feed.py
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import SSEBroker

router = APIRouter()


@router.get("/feed")
async def feed(
    user: User = Depends(current_user),      # resolves the JWT from the query
    broker: SSEBroker = Depends(get_broker),
) -> StreamingResponse:
    """Authenticated stream without a cookie — token comes in the URL."""
    return broker.response(str(user.id))
```

On the front:

```javascript
// The short-lived access token goes in the URL — never the refresh token.
const es = new EventSource(`/api/feed?access_token=${accessToken}`);
```

!!! danger "Query strings leak — treat the token as disposable"
    A token in the URL shows up in **access logs**, **history** and the
    **`Referer`** header. Non-negotiable rules:

    - **Short-lived access token** only (minutes). **Never** the refresh
      token.
    - Always over **TLS** (HTTPS).
    - Strip the value from your proxy/server log format.
    - Refresh through a normal endpoint (header/cookie), not the query.

!!! info "`query_param` also exists on the low-level dependency"
    `make_bearer_token_dependency(tokens, query_param="access_token")`
    returns just the decoded claims — use it when you build
    `get_current_user` by hand. Same header → cookie → query order.

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

- `EventStream` (one per connection) + `.response()` — an SSE endpoint with headers set (`sse_response` is the low-level primitive underneath).
- Tie the producer to the connection with `on_disconnect=` (on `EventStream.response`, `sse_response` or `broker.response`) — no hand-rolled `try/finally`.
- Queue is **bounded** (`max_queue`, default `1000`) + `overflow` (`drop_oldest`/`drop_newest`/`block`) prevents leaks from slow clients; `dropped_events` counts the discards.
- `publish(data, event=, id=, retry=)` covers the 4 spec fields; non-string `data` becomes JSON.
- Heartbeat is a comment (invisible to EventSource); `None` disables it.
- Broadcast = `SSEBroker`; `broker.response(channel)` does register + response + unregister; publish from controllers/tasks/queues with `broker.publish(channel, ...)`; multi-worker = pass a Redis client + start `broker.run()` in the lifespan.
- Auth: **cookie** (`cookie_name` + `withCredentials`) on the same origin; **query string** (`query_param`, short-lived access token over TLS only) for cookieless clients.
- `tempest-react-sdk` `createEventStream`/`useEventStream` consumes with reconnect; `namedEvents` ↔ `publish(event=)`.
