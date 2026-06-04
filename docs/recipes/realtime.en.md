# Real-time

Push data to clients without the client polling. SSE for server→browser broadcasts, Web Push for notifications even when the page is closed.

## Server-Sent Events (SSE)


`EventStream` is an in-memory async queue feeding one SSE HTTP connection. `ServerSentEvent` encodes one frame; `sse_response` wraps the byte stream in a Starlette `StreamingResponse` with SSE-friendly headers.

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
    """Emit three SSE frames then close the stream."""
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
            # Client closed the connection (or producer finished + close()
            # was called) — cancel the producer so it doesn't leak.
            task.cancel()

    return sse_response(lifecycle_aware())
```

!!! note "Pattern: link producer to client connection lifecycle"
    SSE streams are long-lived. If the client disconnects mid-stream, you don't want the producer running forever. Wrapping `stream.stream()` in an outer async generator gives a `finally` block that runs when the underlying response closes — cancel the producer there.

Browser side:

```javascript
const es = new EventSource("/events");
es.addEventListener("counter", (e) => console.log("got", JSON.parse(e.data)));
```

`heartbeat_seconds` emits a `: keepalive` SSE comment when idle so load-balancers don't close long-lived connections. `ServerSentEvent.data` accepts strings, bytes or any JSON-serializable Python object — non-strings are JSON-encoded automatically. Pass `retry=` to hint the browser at the reconnect delay (milliseconds).


## Web Push notifications


`WebPushDispatcher` wraps the synchronous `pywebpush` library in `asyncio.to_thread` and surfaces the two errors the application cares about: `WebPushGoneError` (HTTP 404/410 — delete the subscription) and `WebPushError` (everything else). Install with `[webpush]`.

```python
# src/services/notifications.py
from tempest_fastapi_sdk import (
    WebPushDispatcher,
    WebPushGoneError,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)


dispatcher = WebPushDispatcher(
    settings.VAPID_PRIVATE_KEY,
    vapid_subject="mailto:ops@example.com",
    ttl_seconds=60,
)


async def notify_order_paid(
    subscription: WebPushSubscriptionSchema,
    order_id: str,
) -> None:
    payload = WebPushPayloadSchema(
        title="Pagamento confirmado",
        body=f"Pedido {order_id} aprovado.",
        icon="/static/icons/order.png",
        data={"orderId": order_id, "url": f"/orders/{order_id}"},
    )
    try:
        await dispatcher.send(subscription, payload)
    except WebPushGoneError:
        # Prune the subscription from your store.
        await subscriptions_repo.delete_by_endpoint(subscription.endpoint)


async def broadcast(subs: list[WebPushSubscriptionSchema], payload: WebPushPayloadSchema) -> None:
    gone = await dispatcher.send_many(subs, payload)
    if gone:
        await subscriptions_repo.delete_by_endpoints(gone)
```

`WebPushSubscriptionSchema` round-trips the exact JSON `PushSubscription.toJSON()` emits in the browser (it aliases `expiration_time` ↔ `expirationTime`), so you can store inbound subscriptions verbatim and replay them on dispatch.

