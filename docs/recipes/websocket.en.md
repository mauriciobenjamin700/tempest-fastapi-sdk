# WebSocket router

Since v0.33.0 the SDK ships `make_websocket_router` + `WebSocketHub` — the bidirectional counterpart to SSE, with bearer auth at the handshake, automatic ping/pong heartbeats and a central registry for broadcast / per-user / topic-scoped delivery.

## What the router solves

FastAPI's bare WebSocket route gives you `await ws.receive_json()` / `await ws.send_json()` — and nothing else. The rest is boilerplate every project ends up writing the same way:

1. **Handshake auth** — browsers can't set an `Authorization` header on `new WebSocket(...)`. That leaves two options: a query string (`?token=`) or a subprotocol (`Sec-WebSocket-Protocol: bearer,<jwt>`). The SDK accepts both.
2. **Heartbeat** — load balancers (Nginx, AWS ALB) close "idle" connections after 60s. Without ping/pong the client sees the connection as alive while the server already lost it.
3. **Shared registry** — to call `broadcast("orders", payload)` or `send_to(user_id, payload)` from any HTTP handler, you need a global structure indexed by user id + topics.
4. **Deterministic cleanup** — when the client drops (refresh, tab close, wifi loss), the structures must be cleared or memory leaks.

`make_websocket_router` solves all four; your handler only sees an authenticated socket ready to talk + the hub for fan-out.

## Recipe contents

1. **[Minimum setup](#minimum-setup)** — wiring 3 objects (`WebSocketHub`, `bearer_resolver`, `make_websocket_router`).
2. **[Bearer auth — query vs subprotocol](#bearer-auth)** — when to use each.
3. **[JavaScript / browser client](#javascript-client)** — `new WebSocket(...)` with heartbeat + reconnect.
4. **[Broadcast / send_to / topics](#broadcast)** — fan-out via `WebSocketHub`.
5. **[Heartbeat and close codes](#heartbeat)** — `4401`/`4408`/`4429` and how the client reacts.
6. **[Settings (`WebSocketSettings`)](#settings)** — flags + defaults.
7. **[Trade-offs and when NOT to use](#trade-offs)** — single-process, multi-replica fan-out, SSE vs WS.

---

## Minimum setup

Three objects: the **hub** (in-memory state), the **resolver** (token → user UUID), and the **handler** (message loop).

```python
# src/api/app.py
from uuid import UUID

from fastapi import FastAPI, WebSocket

from tempest_fastapi_sdk import (
    JWTUtils,
    WSEnvelope,
    WebSocketConnection,
    WebSocketHub,
    WebSocketSettings,
    make_websocket_router,
)
from src.core.settings import settings

ws_settings = WebSocketSettings()
hub = WebSocketHub(max_per_user=ws_settings.WS_MAX_CONNECTIONS_PER_USER)
tokens = JWTUtils(secret=settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def bearer_resolver(token: str) -> UUID | None:
    """Decode JWT and return the subject (user id) — None on bad token."""
    try:
        payload = tokens.decode(token)
    except Exception:  # noqa: BLE001 — any decode failure = reject
        return None
    return UUID(payload["sub"])


async def handler(
    ws: WebSocket,
    connection: WebSocketConnection,
    hub: WebSocketHub,
) -> None:
    """Bidirectional loop — every connection runs this until disconnect."""
    while True:
        message = await ws.receive_json()
        envelope = WSEnvelope.model_validate(message)
        if envelope.type == "pong":
            continue          # heartbeat — handled by the router
        if envelope.type == "subscribe":
            await hub.subscribe(connection.connection_id, envelope.data["topic"])
            continue
        if envelope.type == "chat.message":
            # Broadcast to everyone subscribed to `chat:<room>`
            await hub.broadcast(
                WSEnvelope(
                    type="chat.message",
                    data={
                        "from": str(connection.user_id),
                        "text": envelope.data["text"],
                    },
                ),
                topic=envelope.data["room"],
            )


app = FastAPI()
app.include_router(
    make_websocket_router(
        handler,
        hub=hub,
        bearer_resolver=bearer_resolver,
        settings=ws_settings,
        path="/ws",
    )
)
```

Done. `ws://localhost:8000/ws?token=<jwt>` now accepts connections; `hub.broadcast(...)` and `hub.send_to(...)` are reachable from **any HTTP handler** in the same app to push events to the sockets.

---

## Bearer auth

The SDK reads the token from one of two places — in preference order:

| Mechanism | Browser-friendly | Logged? | When to use |
|---|---|---|---|
| `Sec-WebSocket-Protocol: bearer,<jwt>` | **Yes** (via the 2nd arg of `new WebSocket(...)`) | No (header) | **Preferred** — works in the browser, hides the token from proxy logs. |
| `?token=<jwt>` query string | Yes (native URL) | Yes (request log, Referer, history) | Fallback only or limited clients. |

When both are present, **subprotocol wins**.

A resolver returning `None` → the SDK closes the socket with code `4401` before the handler runs.

---

## JavaScript client

```javascript
// Preferred — subprotocol bearer
const ws = new WebSocket("wss://api.example.com/ws", ["bearer", jwtToken]);

ws.addEventListener("open", () => {
  ws.send(JSON.stringify({ type: "subscribe", data: { topic: "chat:lobby" } }));
});

ws.addEventListener("message", (event) => {
  const envelope = JSON.parse(event.data);

  // Heartbeat — reply immediately or the server drops you in 60s
  if (envelope.type === "ping") {
    ws.send(JSON.stringify({ type: "pong", data: {} }));
    return;
  }

  // Your app
  if (envelope.type === "chat.message") {
    console.log("got", envelope.data);
  }
});

// Reconnect with exponential backoff
ws.addEventListener("close", (event) => {
  const code = event.code;
  if (code === 4401) {
    // bad/expired token → redirect to login
    window.location.href = "/login";
    return;
  }
  setTimeout(() => connect(), Math.min(30_000, 1_000 * 2 ** attempts++));
});
```

---

## Broadcast

`WebSocketHub` exposes three patterns:

```python
# 1. send_to — every socket the user has open (multi-tab)
await hub.send_to(user_id, WSEnvelope(type="notification", data={"text": "..."}))

# 2. broadcast with topic — only subscribers of that topic
await hub.broadcast(
    WSEnvelope(type="order.paid", data={"id": str(order_id)}),
    topic=f"order:{order_id}",
)

# 3. broadcast without topic — EVERYONE connected (use sparingly)
await hub.broadcast(
    WSEnvelope(type="system.announcement", data={"text": "Server maintenance"}),
)
```

Subscription lifecycle is owned by the handler:

```python
await hub.subscribe(connection.connection_id, "order:01HE...")
# ... later
await hub.unsubscribe(connection.connection_id, "order:01HE...")
```

Dead sockets are detected at `send_to`/`broadcast` time (the `send_json` call fails) — the hub removes them from the registry automatically.

---

## Heartbeat

Every `WS_HEARTBEAT_SECONDS` (default 30s) the SDK sends:

```json
{"type": "ping", "data": {}, "request_id": null}
```

The client **must** reply with `{"type": "pong", "data": {}}` within `WS_HEARTBEAT_TIMEOUT_SECONDS` (default 60s) — otherwise the socket is closed with code `4408` (custom Request Timeout in the 4000-4999 app-reserved range).

Close codes the router emits:

| Code | When |
|---|---|
| `1000` | Normal exit (handler returned, or client closed cleanly) |
| `1009` | Inbound frame larger than `WS_MAX_MESSAGE_BYTES` |
| `4401` | Invalid / expired / missing token at handshake |
| `4408` | Heartbeat timeout — client did not reply `pong` |
| `4429` | `WS_MAX_CONNECTIONS_PER_USER` exceeded — the **oldest** connection of the user is evicted |

---

## Settings

Mix `WebSocketSettings` into your `Settings` class:

```python
# src/core/settings.py
from tempest_fastapi_sdk import BaseAppSettings, WebSocketSettings


class Settings(WebSocketSettings, BaseAppSettings):
    pass
```

```bash
# .env
WS_HEARTBEAT_SECONDS=30                # default
WS_HEARTBEAT_TIMEOUT_SECONDS=60        # default
WS_MAX_CONNECTIONS_PER_USER=5          # default
WS_MAX_MESSAGE_BYTES=65536             # 64 KiB default
```

---

## Trade-offs

**Single-process by design.** `WebSocketHub` keeps state in the process's memory. For a multi-replica deployment:

- **Option 1 — Sticky sessions**: the load balancer routes the same client to the same replica every time. Works, but you give up balancing.
- **Option 2 — Pub/sub fan-out** (future v0.34+): an HTTP handler publishes to a Redis pub/sub / RabbitMQ topic, and each hub replica re-emits to its local sockets. Identical surface, transparent plumbing. **Not shipped yet** — for v0.33.0 use Option 1, or run a single replica of the WS service behind a separate HTTP balancer.

**When to prefer SSE over WebSocket:**

- Server → client only (notifications, order status, live dashboards).
- The client rarely sends (1 request/minute).
- You want automatic reconnect for free — `EventSource` reconnects on its own with backoff; WebSocket requires custom code.
- Behind a proxy / CDN that doesn't handle WebSocket well (some ALBs / lower-tier Cloudflare).

**When WebSocket is the right call:**

- Heavy bidirectional traffic (chat, simultaneous collaboration, games, drawing apps).
- Ultra-low latency in both directions.
- Custom protocol per message-type that SSE models poorly.
- High client → server message volume.

## Next steps

- **[Auth flow »](auth-flow.en.md)** — the JWT that lands in `?token=` or the subprotocol comes straight from `POST /auth/login` in `UserAuthService`.
- **[Real-time (SSE) »](realtime.en.md)** — when server → client only is enough.
- **[Cache »](cache.en.md)** — Redis pub/sub will back the future multi-replica fan-out.
