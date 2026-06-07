# Web Push

Notificações Web Push (assinadas com VAPID) para navegadores via
`WebPushDispatcher`. Embrulha o `pywebpush` síncrono em
`asyncio.to_thread` e expõe os dois erros que a aplicação realmente
trata: `WebPushGoneError` (HTTP 404/410 — apague a inscrição) e
`WebPushError` (qualquer outra falha). Requer o extra `[webpush]`
(`pywebpush` + `cryptography`).

## Configuração VAPID

`WebPushSettings` traz `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` e
`VAPID_SUBJECT`. A chave **pública** vai para o frontend (no
`pushManager.subscribe`); a **privada** assina cada push no backend. O
`sub` deve ser um URI `mailto:` ou `https:`.

```python
# src/services/notifications.py
from tempest_fastapi_sdk import WebPushDispatcher

from src.core.settings import settings


dispatcher = WebPushDispatcher(
    settings.VAPID_PRIVATE_KEY,
    vapid_subject=settings.VAPID_SUBJECT,   # ex.: "mailto:ops@example.com"
    ttl_seconds=60,
)
```

## Guardar a inscrição

`WebPushSubscriptionSchema` faz round-trip exato do JSON que o
`PushSubscription.toJSON()` emite no navegador (aliasa
`expiration_time` ↔ `expirationTime`), então você guarda a inscrição que
chega verbatim e a reusa no envio.

```python
# src/api/routers/push.py
from fastapi import APIRouter

from tempest_fastapi_sdk import WebPushSubscriptionSchema

router = APIRouter(prefix="/push", tags=["push"])


@router.post("/subscribe", status_code=201)
async def subscribe(subscription: WebPushSubscriptionSchema) -> dict[str, str]:
    """Persiste a inscrição enviada pelo Service Worker do navegador."""
    await subscriptions_repo.upsert_by_endpoint(subscription)
    return {"status": "subscribed"}
```

## Enviar uma notificação

O `payload` aceita `WebPushPayloadSchema`, `dict`, `str` ou `bytes`
(models e dicts viram JSON). Trate `WebPushGoneError` para podar a
inscrição morta do seu store.

```python
from tempest_fastapi_sdk import (
    WebPushGoneError,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
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
        await subscriptions_repo.delete_by_endpoint(subscription.endpoint)
```

## Broadcast com poda automática

`send_many()` dispara o mesmo payload concorrentemente
(`asyncio.gather`) e **retorna os endpoints mortos** (404/410) para você
remover — outras falhas são logadas, não levantadas.

```python
async def broadcast(
    subs: list[WebPushSubscriptionSchema],
    payload: WebPushPayloadSchema,
) -> None:
    gone: list[str] = await dispatcher.send_many(subs, payload)
    if gone:
        await subscriptions_repo.delete_by_endpoints(gone)
```

!!! warning "Sempre pode as inscrições mortas"
    Inscrições expiram quando o usuário troca de device ou revoga a
    permissão. Ignorar `WebPushGoneError` / o retorno do `send_many`
    acumula endpoints zumbis e desperdiça dispatch. Apague-os assim que
    o push service responder 404/410.

## Recap

- Instale `[webpush]` e configure `WebPushSettings` (chaves VAPID).
- Chave pública → frontend; privada → assina os pushes no backend.
- Guarde `WebPushSubscriptionSchema` verbatim; reuse no envio.
- `send()` para um destino, `send_many()` para broadcast (retorna mortos).
- Trate `WebPushGoneError` (404/410) podando a inscrição do store.
