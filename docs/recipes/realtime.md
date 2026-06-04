# Tempo real

Empurre dados para os clientes sem que o cliente fique fazendo polling. SSE para broadcasts servidor→navegador, Web Push para notificações mesmo com a página fechada.

## Server-Sent Events (SSE)


`EventStream` é uma fila async em memória que alimenta uma conexão HTTP SSE. `ServerSentEvent` codifica um frame; `sse_response` embrulha o stream de bytes em um `StreamingResponse` do Starlette com headers amigáveis ao SSE.

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

No navegador:

```javascript
const es = new EventSource("/events");
es.addEventListener("counter", (e) => console.log("got", JSON.parse(e.data)));
```

`heartbeat_seconds` emite um comentário SSE `: keepalive` quando ocioso, para que load-balancers não fechem conexões de longa duração. `ServerSentEvent.data` aceita strings, bytes ou qualquer objeto Python serializável em JSON — não-strings são codificados em JSON automaticamente. Passe `retry=` para sugerir ao navegador o atraso de reconexão (em milissegundos).


## Notificações Web Push


`WebPushDispatcher` embrulha a biblioteca síncrona `pywebpush` em `asyncio.to_thread` e expõe os dois erros que importam para a aplicação: `WebPushGoneError` (HTTP 404/410 — apague a inscrição) e `WebPushError` (todo o resto). Instale com `[webpush]`.

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

`WebPushSubscriptionSchema` faz round-trip exato do JSON que `PushSubscription.toJSON()` emite no navegador (ele faz o alias `expiration_time` ↔ `expirationTime`), então você pode armazenar inscrições recebidas literalmente e reproduzi-las no envio.
