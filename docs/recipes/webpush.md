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

!!! tip "Gerando um par de chaves VAPID"
    Você gera o par uma única vez e reaproveita em todos os ambientes.
    Com o `pywebpush` (extra `[webpush]`) instalado:

    ```bash
    vapid --gen
    ```

    Isso escreve `private_key.pem` + `public_key.pem` e imprime a chave
    pública em base64 url-safe (a `applicationServerKey` do frontend).
    Sem Python à mão, o `web-push` do Node faz o mesmo:

    ```bash
    npx web-push generate-vapid-keys
    ```

    O output traz **Public Key** e **Private Key**: mapeie
    `Public Key` → `VAPID_PUBLIC_KEY` e `Private Key` →
    `VAPID_PRIVATE_KEY`. Defina `VAPID_SUBJECT` como um `mailto:` ou
    `https:` seu.

```python
# src/services/notifications.py
from tempest_fastapi_sdk import WebPushDispatcher

from src.core.settings import settings


# settings.webpush_kwargs() -> vapid_private_key + vapid_subject + ttl_seconds
dispatcher = WebPushDispatcher(**settings.webpush_kwargs())
```

## Tabela + serviço (recomendado)

Para guardar os aparelhos do usuário e entregar com poda automática, o
SDK traz a **tabela base** `BaseWebPushSubscriptionModel` (uma linha por
device, `endpoint` único) e o **serviço base** `WebPushSubscriptionService`
(salva, remove e envia, podando as mortas sozinho). Igual ao padrão de
auth, o SDK fornece a linha abstrata e o projeto cria a tabela concreta
com a FK pro seu `UserModel`:

```python
# src/db/models/web_push_subscription.py
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from tempest_fastapi_sdk import BaseWebPushSubscriptionModel


class WebPushSubscriptionModel(BaseWebPushSubscriptionModel):
    """Inscrição Web Push de um device do usuário."""

    __tablename__ = "web_push_subscriptions"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

Monte o serviço com um `BaseRepository` da tabela + o dispatcher VAPID:

```python
# src/api/dependencies/resources.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, WebPushDispatcher, WebPushSubscriptionService

from src.core.settings import settings
from src.db.models import WebPushSubscriptionModel


def get_push_service(session: AsyncSession) -> WebPushSubscriptionService:
    repo = BaseRepository(session, model=WebPushSubscriptionModel)
    dispatcher = WebPushDispatcher(**settings.webpush_kwargs())
    return WebPushSubscriptionService(repo, dispatcher)
```

O serviço expõe:

| Método | O que faz |
| --- | --- |
| `subscribe(user_id, subscription, *, user_agent=None)` | Salva a inscrição, **idempotente por `endpoint`** — re-subscribe atualiza, não duplica. |
| `unsubscribe(endpoint)` | Remove a inscrição (no-op se não existe). |
| `list_for_user(user_id)` | Lista os devices do usuário. |
| `notify_user(user_id, payload)` | Envia pra todos os devices e **poda os mortos** (404/410) antes de retornar. Devolve quantos receberam. |

## Alinhado com o tempest-react-sdk

O `WebPushClient` do [`tempest-react-sdk`](https://github.com/mauriciobenjamin700/tempest-react-sdk)
chama `onSubscribe(subscription)` e `onUnsubscribe(subscription)` com o
`PushSubscription.toJSON()` cru. Esse JSON é exatamente o
`WebPushSubscriptionSchema` (aliasa `expiration_time` ↔ `expirationTime`),
então o front bate direto nos endpoints abaixo:

```python
# src/api/routers/push.py
from fastapi import APIRouter, Depends, status

from tempest_fastapi_sdk import WebPushSubscriptionSchema, WebPushSubscriptionService

router = APIRouter(prefix="/api/push", tags=["push"])


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe(
    subscription: WebPushSubscriptionSchema,
    user: CurrentUser,                       # sua dependency de auth
    service: WebPushSubscriptionService = Depends(get_push_service),
) -> dict[str, str]:
    """Recebe o onSubscribe do WebPushClient e persiste o device."""
    await service.subscribe(user.id, subscription)
    return {"status": "subscribed"}


@router.post("/unsubscribe", status_code=status.HTTP_200_OK)
async def unsubscribe(
    subscription: WebPushSubscriptionSchema,
    service: WebPushSubscriptionService = Depends(get_push_service),
) -> dict[str, str]:
    """Recebe o onUnsubscribe e remove o device."""
    await service.unsubscribe(subscription.endpoint)
    return {"status": "unsubscribed"}
```

### Router pronto (opt-in)

Não quer escrever os dois endpoints? `make_web_push_router` monta
`/subscribe` + `/unsubscribe` já ligados ao serviço — estilo
`make_auth_router`. Você só injeta como o serviço e o usuário atual são
resolvidos:

```python
# src/api/app.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    BaseRepository,
    WebPushDispatcher,
    WebPushSubscriptionService,
    make_web_push_router,
)

from src.api.dependencies import get_current_user_id, get_session
from src.core.settings import settings
from src.db.models import WebPushSubscriptionModel


def _service(session: AsyncSession) -> WebPushSubscriptionService:
    repo = BaseRepository(session, model=WebPushSubscriptionModel)
    return WebPushSubscriptionService(repo, WebPushDispatcher(**settings.webpush_kwargs()))


app.include_router(
    make_web_push_router(
        service_factory=_service,
        session_factory=get_session,
        current_user_id=get_current_user_id,   # dependency -> UUID
    )
)
# POST /api/push/subscribe   (201) e  POST /api/push/unsubscribe (200)
```

O `User-Agent` da requisição vira o rótulo do device (`store_user_agent=True`,
default). Ambos os endpoints exigem autenticação via `current_user_id`.

Enviar pra um usuário (todos os devices, poda automática embutida):

```python
delivered: int = await service.notify_user(
    user.id,
    {"title": "Pagamento confirmado", "body": "Pedido aprovado."},
)
```

## Enviar uma notificação (dispatcher direto)

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
- Tabela `BaseWebPushSubscriptionModel` (1 linha por device, `endpoint` único) + `WebPushSubscriptionService` (`subscribe`/`unsubscribe`/`notify_user`) — o caminho recomendado, com poda automática.
- O JSON do `WebPushClient` (tempest-react-sdk) é o próprio `WebPushSubscriptionSchema` — `subscribe`/`unsubscribe` batem direto.
- `make_web_push_router` monta `/subscribe` + `/unsubscribe` prontos (estilo `make_auth_router`) se você não quiser escrever as rotas.
- Caminho baixo nível: `send()` para um destino, `send_many()` para broadcast (retorna mortos); trate `WebPushGoneError` (404/410) podando o store.
