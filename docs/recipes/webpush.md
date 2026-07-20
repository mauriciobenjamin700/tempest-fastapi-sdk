# Web Push

Notificações Web Push (assinadas com VAPID) para navegadores via
`WebPushDispatcher`. Embrulha o `pywebpush` síncrono em
`asyncio.to_thread` e expõe os dois erros que a aplicação realmente
trata: `WebPushGoneError` (HTTP 404/410 — apague a inscrição) e
`WebPushError` (qualquer outra falha). Requer o extra `[webpush]`
(`pywebpush` + `cryptography`).

!!! info "O que este guia segue"
    O SDK entrega as peças (tabela base, dispatcher, serviço, schema); o
    projeto as monta na arquitetura em camadas
    **router → controller → service → repository**. Cada bloco abaixo traz
    o **caminho do arquivo** no topo e a explicação logo em seguida, pra
    você colar direto no lugar certo.

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

O dispatcher é um **singleton de infraestrutura**: construído uma única
vez e alcançado por todo lugar via `Depends`, junto dos outros recursos
(banco, storage, cache). Construa-o **preguiçosamente** (só no primeiro
uso), como o broker de SSE — assim uma app sem chaves VAPID válidas (um
teste, ou um serviço que não usa push) nunca falha no import.

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import WebPushDispatcher

from src.core.settings import settings

_webpush_dispatcher: WebPushDispatcher | None = None


def get_webpush_dispatcher() -> WebPushDispatcher:
    """Retorna o dispatcher VAPID compartilhado, criado uma vez no 1º uso.

    ``settings.webpush_kwargs()`` devolve ``vapid_private_key``,
    ``vapid_subject`` e ``ttl_seconds``.
    """
    global _webpush_dispatcher
    if _webpush_dispatcher is None:
        _webpush_dispatcher = WebPushDispatcher(**settings.webpush_kwargs())
    return _webpush_dispatcher
```

## Tabela, repositório, serviço e controller (recomendado)

Para guardar os aparelhos do usuário e entregar com poda automática, o
SDK traz a **tabela base** `BaseWebPushSubscriptionModel` (uma linha por
device, `endpoint` único) e o **serviço base** `WebPushSubscriptionService`
(salva, remove e envia, podando as mortas sozinho). Montamos as quatro
camadas na ordem em que uma requisição as atravessa.

### 1. Model — a tabela concreta

Igual ao padrão de auth, o SDK fornece a linha abstrata e o projeto cria
a tabela concreta com a FK pro seu `UserModel`:

```python
# src/db/models/webpush.py
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseWebPushSubscriptionModel


class WebPushSubscriptionModel(BaseWebPushSubscriptionModel):
    """Inscrição Web Push de um device do usuário (uma linha por device)."""

    __tablename__ = "web_push_subscriptions"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

O `BaseWebPushSubscriptionModel` já traz `endpoint` (único + indexado),
`p256dh`, `auth`, `expiration_time` e `user_agent`; você só adiciona a FK
`user_id`. Lembre de gerar a migration pra essa tabela nova.

### 2. Repository — subclasse tipada do `BaseRepository`

Seguindo o padrão dos demais repositórios do projeto, crie uma subclasse
concreta (em vez de instanciar `BaseRepository` solto), pra ter um tipo
nomeado que a DI e os testes referenciam:

```python
# src/db/repositories/webpush.py
from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import BaseRepository

from src.db.models import WebPushSubscriptionModel


class WebPushSubscriptionRepository(BaseRepository[WebPushSubscriptionModel]):
    """Repositório CRUD para o WebPushSubscriptionModel."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session=session, model=WebPushSubscriptionModel)
```

### 3. Service — o do SDK, sem wrapper

O `WebPushSubscriptionService` do SDK já implementa
`subscribe`/`unsubscribe`/`list_for_user`/`notify_user` sobre um
`BaseRepository` + `WebPushDispatcher`. Como não há lógica extra a
adicionar, use-o **direto** — nada de subclasse pass-through. Reexporte-o
no pacote de serviços pra manter um ponto único de import:

```python
# src/services/__init__.py
from tempest_fastapi_sdk import WebPushSubscriptionService

# ... demais serviços do projeto ...

__all__: list[str] = [
    # ...
    "WebPushSubscriptionService",
]
```

O serviço expõe:

| Método | O que faz |
| --- | --- |
| `subscribe(user_id, subscription, *, user_agent=None)` | Salva a inscrição, **idempotente por `endpoint`** — re-subscribe atualiza, não duplica. |
| `unsubscribe(endpoint)` | Remove a inscrição (no-op se não existe). |
| `list_for_user(user_id)` | Lista os devices do usuário. |
| `notify_user(user_id, payload)` | Envia pra todos os devices e **poda os mortos** (404/410) antes de retornar. Devolve quantos receberam. |

### 4. Controller — a camada fina de política

O controller mantém o grafo `router → controller → service` uniforme e é
onde entra a política de aplicação — aqui, o gate de usuário ativo
(`require_active`) antes de delegar:

```python
# src/controllers/webpush.py
from tempest_fastapi_sdk import WebPushSubscriptionSchema, require_active

from src.db.models import UserModel, WebPushSubscriptionModel
from src.services import WebPushSubscriptionService


class WebPushController:
    """Controller de inscrições Web Push (gate de auth + delegação)."""

    def __init__(
        self, service: WebPushSubscriptionService[WebPushSubscriptionModel]
    ) -> None:
        self.service = service

    async def subscribe(
        self,
        user: UserModel,
        subscription: WebPushSubscriptionSchema,
        *,
        user_agent: str | None = None,
    ) -> None:
        """Persiste o device do usuário autenticado (idempotente por endpoint)."""
        require_active(user)
        await self.service.subscribe(user.id, subscription, user_agent=user_agent)

    async def unsubscribe(self, subscription: WebPushSubscriptionSchema) -> None:
        """Remove o device pelo endpoint (no-op se não existir)."""
        await self.service.unsubscribe(subscription.endpoint)
```

### 5. Providers de DI — uma camada por arquivo

Cada camada ganha seu provider `Depends`, no arquivo correspondente. O
`session` **sempre** vem via `Depends(get_session)` (senão o FastAPI
tenta resolvê-lo como parâmetro da request):

```python
# src/api/dependencies/repositories.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories import WebPushSubscriptionRepository

from .resources import get_session


def get_webpush_subscription_repository(
    session: AsyncSession = Depends(get_session),
) -> WebPushSubscriptionRepository:
    """Repositório de inscrições ligado à sessão da request."""
    return WebPushSubscriptionRepository(session=session)
```

```python
# src/api/dependencies/services.py
from fastapi import Depends
from tempest_fastapi_sdk import WebPushDispatcher

from src.db.models import WebPushSubscriptionModel
from src.db.repositories import WebPushSubscriptionRepository
from src.services import WebPushSubscriptionService

from .repositories import get_webpush_subscription_repository
from .resources import get_webpush_dispatcher


def get_webpush_service(
    webpush_repository: WebPushSubscriptionRepository = Depends(
        get_webpush_subscription_repository
    ),
    dispatcher: WebPushDispatcher = Depends(get_webpush_dispatcher),
) -> WebPushSubscriptionService[WebPushSubscriptionModel]:
    """Casa o repositório (por request) com o dispatcher (compartilhado)."""
    return WebPushSubscriptionService(webpush_repository, dispatcher)
```

```python
# src/api/dependencies/controllers.py
from fastapi import Depends

from src.controllers import WebPushController
from src.db.models import WebPushSubscriptionModel
from src.services import WebPushSubscriptionService

from .services import get_webpush_service


def get_webpush_controller(
    webpush_service: WebPushSubscriptionService[WebPushSubscriptionModel] = Depends(
        get_webpush_service
    ),
) -> WebPushController:
    """Monta o controller com o serviço da request."""
    return WebPushController(service=webpush_service)
```

### 6. Router — alinhado com o tempest-react-sdk

O `WebPushClient` do [`tempest-react-sdk`](https://github.com/mauriciobenjamin700/tempest-react-sdk)
chama `onSubscribe(subscription)` e `onUnsubscribe(subscription)` com o
`PushSubscription.toJSON()` cru. Esse JSON é exatamente o
`WebPushSubscriptionSchema` (aliasa `expiration_time` ↔ `expirationTime`),
então o front bate direto nos endpoints abaixo. O router recebe o
**controller** via `Depends`, o usuário via a sua dependency de auth, e
usa **prefixo nu** (`/webpush`) — o `/api` é aplicado no mount agregado:

```python
# src/api/routers/webpush.py
from fastapi import APIRouter, Depends, Request, status
from tempest_fastapi_sdk import WebPushSubscriptionSchema

from src.api.dependencies import get_current_user, get_webpush_controller
from src.controllers import WebPushController
from src.db.models import UserModel

router = APIRouter(prefix="/webpush", tags=["webpush"])


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe(
    subscription: WebPushSubscriptionSchema,
    request: Request,
    user: UserModel = Depends(get_current_user),
    controller: WebPushController = Depends(get_webpush_controller),
) -> dict[str, str]:
    """Recebe o onSubscribe e persiste o device, rotulando pelo User-Agent."""
    await controller.subscribe(
        user, subscription, user_agent=request.headers.get("user-agent")
    )
    return {"status": "subscribed"}


@router.post("/unsubscribe", status_code=status.HTTP_200_OK)
async def unsubscribe(
    subscription: WebPushSubscriptionSchema,
    controller: WebPushController = Depends(get_webpush_controller),
) -> dict[str, str]:
    """Recebe o onUnsubscribe e remove o device."""
    await controller.unsubscribe(subscription)
    return {"status": "unsubscribed"}
```

### 7. Registro — sob o `/api`

Inclua o router no agregado de negócio (que o `app.py` monta com
`prefix="/api"`), do mesmo jeito que os demais domínios:

```python
# src/api/routers/__init__.py
from fastapi import APIRouter

from .webpush import router as webpush_router

router = APIRouter()

# ... demais routers ...
router.include_router(webpush_router)
# efetivo: POST /api/webpush/subscribe (201) e POST /api/webpush/unsubscribe (200)
```

!!! note "Rota final"
    Como o prefixo do router é nu (`/webpush`) e o agregado é montado sob
    `/api`, hardcodar `/api/webpush` no `APIRouter(prefix=...)` duplicaria
    o segmento (`/api/api/webpush`). Deixe o `/api` só no mount.

Enviar pra um usuário (todos os devices, poda automática embutida) — de
dentro de qualquer serviço/controller que tenha o `WebPushSubscriptionService`:

```python
delivered: int = await service.notify_user(
    user.id,
    {"title": "Pagamento confirmado", "body": "Pedido aprovado."},
)
```

### Router pronto (opt-in, contorna as camadas)

Pra um protótipo rápido, `make_web_push_router` monta `/subscribe` +
`/unsubscribe` já ligados ao serviço — estilo `make_auth_router`. Ele
**pula o controller** e liga o router direto no serviço, então prefira as
camadas acima em apps de produção; use isto só como atalho:

```python
# src/api/app.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    WebPushDispatcher,
    WebPushSubscriptionService,
    make_web_push_router,
)

from src.api.dependencies import get_current_user_id, get_session
from src.core.settings import settings
from src.db.models import WebPushSubscriptionModel
from src.db.repositories import WebPushSubscriptionRepository


def _service(session: AsyncSession) -> WebPushSubscriptionService:
    repo = WebPushSubscriptionRepository(session)
    return WebPushSubscriptionService(
        repo, WebPushDispatcher(**settings.webpush_kwargs())
    )


app.include_router(
    make_web_push_router(
        service_factory=_service,
        session_factory=get_session,
        current_user_id=get_current_user_id,   # dependency -> UUID
    )
)
# POST /api/push/subscribe (201) e POST /api/push/unsubscribe (200)
```

O `User-Agent` da requisição vira o rótulo do device (`store_user_agent=True`,
default). Ambos os endpoints exigem autenticação via `current_user_id`.

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
- Dispatcher é singleton de infra em `resources.py`, construído preguiçosamente via `get_webpush_dispatcher`.
- Monte as camadas: **model** (FK pro seu user) → **repository** (subclasse do `BaseRepository`) → **service** (o do SDK, sem wrapper) → **controller** (gate `require_active`) → **providers** (um por arquivo, `session` via `Depends`) → **router** (prefixo nu, controller via `Depends`) → registro sob `/api`.
- O JSON do `WebPushClient` (tempest-react-sdk) é o próprio `WebPushSubscriptionSchema` — `subscribe`/`unsubscribe` batem direto.
- `make_web_push_router` é atalho opt-in que **pula o controller** — bom pra protótipo, não pra app em camadas.
- Caminho baixo nível: `send()` para um destino, `send_many()` para broadcast (retorna mortos); trate `WebPushGoneError` (404/410) podando o store.
