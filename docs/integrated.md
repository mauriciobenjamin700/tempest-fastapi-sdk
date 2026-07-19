# Exemplo integrado — checkout com Pix

O [Tour](tour.md) mostra cada peça isolada. Aqui elas trabalham **juntas**
num fluxo real: um cliente autenticado paga um pedido via Pix, e o sistema
precifica com cache, grava pedido + evento na **mesma transação** (outbox),
dispara um e-mail em background e notifica o cliente em tempo real por
**SSE + Web Push** — tudo com os blocos do SDK.

Componentes exercitados de uma vez: **settings + db**, **auth (JWT)**,
**campos validados + PixKeyField**, **cache (`@cached`)**, **repository +
service**, **outbox transacional**, **MessageBroker**, **TaskQueue**, **SSE**
e **Web Push**.

## 1. Recursos (um lugar só)

```python
# src/core/resources.py
from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.queue import MessageBroker
from tempest_fastapi_sdk.sse import SSEBroker
from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

db = AsyncDatabaseManager(settings.DATABASE_URL)
cache = AsyncRedisManager(settings.REDIS_URL)
mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)      # eventos entre serviços
tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)     # trabalho fora do request
events = SSEBroker(redis=cache.client)                  # status em tempo real
```

Todos sobem/descem no lifespan (`connect`/`disconnect`) — veja o
[Tutorial](tutorial.md) e a receita de [Deploy seguro](recipes/deploy-safety.md).

## 2. Schema do checkout (campos que se validam)

```python
# src/schemas/checkout.py
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import PixKeyField, PositiveIntField


class CheckoutSchema(BaseSchema):
    product_id: str
    quantity: PositiveIntField        # > 0, senão 422
    pix_key: PixKeyField              # valida CPF/CNPJ/e-mail/telefone/aleatória
```

## 3. Preço com cache

O produto muda pouco; cacheie a leitura e invalide na escrita.

```python
# src/services/catalog.py
from tempest_fastapi_sdk.cache import CacheInvalidator, cached

from src.core.resources import cache


@cached(cache, ttl=300, key_prefix="catalog:", namespace="products")
async def get_product_cents(product_id: str) -> int:
    """Preço unitário em centavos; 5 min de cache."""
    return await load_price_from_db(product_id)


async def invalidate_product(product_id: str) -> None:
    await CacheInvalidator(cache, key_prefix="catalog:").invalidate_namespace("products")
```

## 4. Service: gravar pedido + evento na mesma transação (outbox)

Escrever o pedido e publicar "pedido pago" como duas operações separadas é
inseguro. Grave a linha do pedido **e** a linha de outbox juntas —
`save_with_outbox` faz isso numa transação só.

```python
# src/services/orders.py
from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.utils import CentsField    # (ilustrativo)

from src.core.resources import db
from src.db.models import OrderModel, OutboxModel
from src.services.catalog import get_product_cents


class OrderService:
    async def checkout(self, *, user_id: str, data: CheckoutSchema) -> OrderModel:
        unit_cents = await get_product_cents(data.product_id)     # cache
        total = unit_cents * data.quantity

        async with db.get_session_context() as session:
            repo = OrderRepository(session)
            order = OrderModel(
                user_id=user_id,
                product_id=data.product_id,
                total_cents=total,
                pix_key=data.pix_key,
                status="paid",
            )
            # pedido + evento outbox commitam juntos (ou nenhum):
            await repo.save_with_outbox(
                order,
                OutboxModel.new_event(
                    "orders.paid",
                    {"order_id": str(order.id), "user_id": user_id, "total": total},
                ),
            )
        return order
```

## 5. Endpoint autenticado

O usuário vem do JWT; payload inválido nunca chega aqui (422 automático).

```python
# src/api/routers/checkout.py
from fastapi import APIRouter, Depends

from src.api.dependencies import current_user, get_order_service

router = APIRouter(prefix="/api/checkout")


@router.post("")
async def checkout(
    data: CheckoutSchema,
    user: UserModel = Depends(current_user),        # JWT (header/cookie/query)
    service: OrderService = Depends(get_order_service),
) -> dict[str, str]:
    order = await service.checkout(user_id=str(user.id), data=data)
    return {"order_id": str(order.id), "status": order.status}
```

`current_user` sai de `make_jwt_user_dependency` / `UserAuthService` —
veja [Auth flow](recipes/auth-flow.md).

## 6. Relay do outbox → publica no broker

Um processo drena o outbox e publica no `MessageBroker` (com backoff e
lock). O `publish` do relay encaixa direto:

```python
# src/tasks/relay.py
from tempest_fastapi_sdk import OutboxRelay

from src.core.resources import db, mq
from src.db.models import OutboxModel

relay = OutboxRelay(db, model=OutboxModel,
                    publish=lambda e: mq.publish(e.topic, e.payload))
# asyncio.create_task(relay.run()) no lifespan (ou processo dedicado)
```

## 7. Consumidor reage: e-mail em background + push SSE

Quem escuta "orders.paid" dispara o e-mail (TaskQueue, fora do request) e
empurra o status pro canal SSE do usuário.

```python
# src/queue/consumers.py
from src.core.resources import events, mq, tq
from src.schemas.events import OrderPaid


@tq.task
async def send_receipt(to: str, order_id: str) -> None:
    await email.send(to, "Recibo", f"Pedido {order_id} pago.")


@mq.on("orders.paid")
async def on_order_paid(event: OrderPaid) -> None:
    await send_receipt.enqueue(to=event.user_email, order_id=event.order_id)   # background
    await events.publish(event.user_id, {"order_id": event.order_id, "status": "paid"},
                         event="order_update")                                 # SSE
```

## 8. Notificação ao vivo do pagamento (SSE + Web Push)

A seção 7 empurrou o status com `events.publish` cru — perfeito com a aba
**aberta**. Mas a confirmação do Pix é um evento de domínio que merece os
**dois** canais: SSE para quem está online e **Web Push (VAPID)** para quem
fechou o app. Um `NotificationService` faz o fan-out do **mesmo payload** para
ambos.

```python
# src/services/notification.py
from uuid import UUID

from tempest_fastapi_sdk import (
    SSEBroker,
    WebPushPayloadSchema,
    WebPushSubscriptionService,
)


class NotificationService:
    """Fan one domain event out to SSE (foreground) and Web Push (background)."""

    def __init__(self, broker: SSEBroker, push: WebPushSubscriptionService) -> None:
        self.broker = broker
        self.push = push

    async def notify(
        self, user_id: UUID, event: str, title: str, body: str, data: dict
    ) -> None:
        """Deliver the same event over SSE (live) and Web Push (closed app)."""
        await self.broker.publish(str(user_id), data, event=event)
        await self.push.notify_user(
            user_id,
            WebPushPayloadSchema(title=title, body=body, tag=event, data=data),
        )
```

Monte `notifications` com o `events` global (seção 1) e um
`WebPushSubscriptionService(BaseRepository(session, PushSubscriptionModel), dispatcher)`
— o `dispatcher` sai de `WebPushDispatcher(**settings.webpush_kwargs())`. Na
confirmação, o handler da seção 7 troca o `events.publish` cru por um único
`notify`:

```python
# src/queue/consumers.py
@mq.on("orders.paid")
async def on_order_paid(event: OrderPaid) -> None:
    await send_receipt.enqueue(to=event.user_email, order_id=event.order_id)   # background
    await notifications.notify(                                                # SSE + Web Push
        event.user_id,
        event="payment_confirmed",
        title="Pagamento aprovado",
        body=f"Pedido {event.order_id} confirmado.",
        data={"order_id": event.order_id},
    )
```

Quem está com o app aberto assina o canal por SSE; o `broker.response` cuida de
registrar/transmitir/desregistrar:

```python
# src/api/routers/notifications.py
@router.get("/notifications/stream")
async def stream(user: UserModel = Depends(current_user)) -> StreamingResponse:
    return notifications.broker.response(str(user.id))
```

O register/unregister das assinaturas Web Push (VAPID) entra montado via
`make_web_push_router(...)` — o modelo concreto (`PushSubscriptionModel`) e o
router estão na receita de [Web Push](recipes/webpush.md). Um frame SSE
entregue nesse canal:

```text
event: payment_confirmed
id: 42
data: {"order_id": "ord_123"}
```

SSE é **core** (sem extra); Web Push precisa do extra:
`uv add "tempest-fastapi-sdk[webpush]"`. Os primitivos de cada canal estão em
[SSE](recipes/sse.md) e [Web Push](recipes/webpush.md) — aqui só os compomos.

## 9. Frontend recebe o status em tempo real

```python
# src/api/routers/feed.py
from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from src.core.resources import events

router = APIRouter()


@router.get("/api/feed")
async def feed(user: UserModel = Depends(current_user)) -> StreamingResponse:
    return events.response(str(user.id))    # register + stream + unregister
```

## O fluxo, ponta a ponta

1. `POST /api/checkout` — JWT autentica, schema valida (quantidade > 0, Pix ok).
2. Service precifica com **cache**, grava **pedido + evento outbox** numa transação.
3. **Relay** publica `orders.paid` no **broker** quando o commit firmou.
4. Consumidor enfileira o e-mail (**TaskQueue**) e faz o fan-out da confirmação
   com o **NotificationService**: **SSE** para a aba aberta, **Web Push** para a fechada.
5. O browser recebe o update na hora em `GET /api/feed` / `GET /notifications/stream`;
   quem está fora recebe o **Web Push**.

Cada capacidade tem sua receita dedicada (veja o [Tour](tour.md)); aqui o
ponto é como elas se compõem sem cola manual: exceções viram HTTP certo,
tokens abrem a rota, campos barram lixo, o outbox garante o evento, e o
tempo real fecha o loop.
