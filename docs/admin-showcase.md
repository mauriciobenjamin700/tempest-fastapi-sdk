# Exemplo integrado — admin de loja completo

Este exemplo junta **todos** os recursos do painel admin num app só — uma
loja pequena — pra você ver como eles se combinam. Cada recurso tem sua
própria receita em [Painel admin](recipes/admin.md); aqui é a visão de
conjunto.

Exercitados de uma vez: **audit history** (`audit_model=`),
**autocomplete FK** (`autocomplete_fields=`), **inlines** (`inlines=`),
**cards de negócio** (`dashboard_cards=`), **import CSV** (`can_import=`),
**RBAC granular** (`access_policy=`), **lenses** (`lenses=`) e o **widget
JSON** (automático em colunas `JSON`).

!!! info "O que você precisa"
    Núcleo do SDK + o extra `[admin]`. A seção de **notificações
    operacionais** (§5) soma o extra `[webpush]`; o canal SSE é núcleo e
    não pede nada.

## 1. Modelos

Uma loja: categorias, produtos (com specs em JSON e FK pra categoria),
pedidos e itens de pedido. Mais uma tabela de auditoria e um usuário com
papel (`role`) pro RBAC.

```python
# src/db/models.py
import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseAuditLogModel, BaseModel, BaseUserModel
from tempest_fastapi_sdk.core import BaseStrEnum


class OrderStatus(BaseStrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class User(BaseUserModel):
    __tablename__ = "users"
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="staff")


class AuditLog(BaseAuditLogModel):
    __tablename__ = "audit_log"


class Category(BaseModel):
    __tablename__ = "categories"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Product(BaseModel):
    __tablename__ = "products"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    specs: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Order(BaseModel):
    __tablename__ = "orders"
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        String(16), nullable=False, default=OrderStatus.PENDING
    )
    placed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class OrderItem(BaseModel):
    __tablename__ = "order_items"
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(nullable=False, default=1)
```

## 2. Cards de negócio pro dashboard

Cada card recebe a sessão e devolve um `MetricValue`, `MetricTrend` ou
`MetricPartition`.

```python
# src/admin/cards.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    BaseRepository,
    MetricPartition,
    MetricTrend,
    MetricValue,
)

from src.db.models import Order, OrderStatus, Product


async def total_products(session: AsyncSession) -> MetricValue:
    count = await BaseRepository(session, model=Product).count()
    return MetricValue(count, unit="products")


async def paid_vs_pending(session: AsyncSession) -> MetricTrend:
    repo = BaseRepository(session, model=Order)
    paid = await repo.count({"status": OrderStatus.PAID.value})
    pending = await repo.count({"status": OrderStatus.PENDING.value})
    return MetricTrend(value=float(paid), previous=float(pending), unit="orders")


async def orders_by_status(session: AsyncSession) -> MetricPartition:
    repo = BaseRepository(session, model=Order)
    segments = [
        (status.value, float(await repo.count({"status": status.value})))
        for status in OrderStatus
    ]
    return MetricPartition(segments=segments)
```

## 3. Configuração do admin

Aqui tudo se encaixa. Note os recursos anotados em cada `AdminModel`.

```python
# src/admin/site.py
from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermission,
    AdminSite,
    Inline,
    Lens,
    MetricCard,
)

from src.admin.cards import orders_by_status, paid_vs_pending, total_products
from src.db.models import AuditLog, Category, Order, OrderItem, Product, User


def access_policy(user: User, admin: AdminModel, action: AdminPermission) -> bool:
    """superadmin faz tudo; staff só lê; ninguém mais entra."""
    if user.role == "superadmin":
        return True
    if user.role == "staff":
        return action is AdminPermission.VIEW
    return False


site = AdminSite(
    title="Loja",
    dashboard_cards=[
        MetricCard("Produtos", total_products, help_text="catálogo ativo"),
        MetricCard("Pagos vs pendentes", paid_vs_pending),
        MetricCard("Pedidos por status", orders_by_status),
    ],
)

site.register(AdminModel(model=Category, search_fields=[Category.name]))

site.register(
    AdminModel(
        model=Product,
        search_fields=[Product.name],
        # FK como busca HTMX (categoria pode ter milhares de linhas):
        autocomplete_fields=[Product.category_id],
        # coluna JSON `specs` vira editor JSON automaticamente;
        # importa catálogo por CSV:
        can_import=True,
        # trilha de auditoria por produto no detail:
        audit_model=AuditLog,
    )
)

site.register(
    AdminModel(
        model=Order,
        search_fields=[Order.customer_email],
        # itens do pedido listados no detail do pedido:
        inlines=[Inline(OrderItem, OrderItem.order_id)],
        # abas de fila de trabalho:
        lenses=[
            Lens("Pendentes", filters={"status": "pending"}),
            Lens("Pagos", filters={"status": "paid"}, order_by="-placed_at"),
        ],
        audit_model=AuditLog,
    )
)

# OrderItem precisa de admin registrado pros links do inline funcionarem:
site.register(AdminModel(model=OrderItem, autocomplete_fields=[OrderItem.product_id]))
```

## 4. Montando o router

O `access_policy` entra aqui; a trilha de auditoria é gravada pelo
repository (`add_audited`/`update_audited`) — o admin já faz isso quando
`audit_model=` está setado nos writes que ele mesmo executa.

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin.site import access_policy, site
from src.core.settings import settings
from src.db.connection import db  # seu AsyncDatabaseManager


def create_app() -> FastAPI:
    app = FastAPI(title="Loja")
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(User, mfa_issuer="Loja"),
            secret_key=settings.SECRET_KEY,
            access_policy=access_policy,
        )
    )
    return app
```

## 5. Notificações operacionais (SSE + Web Push)

A trilha de auditoria (§1, §4) é o **registro durável** do que aconteceu —
uma linha por escrita, pra consultar depois. Mas ela é passiva: ninguém
fica com o `audit_log` aberto esperando. Quando um evento de negócio
interessa ao time **agora** — um pedido novo entrou, um cadastro caiu, uma
denúncia de moderação subiu — você quer um **sinal ao vivo**, não uma linha
pra auditar depois. É a outra metade do par: auditoria = durável, passivo;
notificação = efêmero, ao vivo.

O mesmo evento vai por **dois canais, com o mesmo payload**:

- **SSE** pro painel **aberto** — um canal compartilhado `"staff"` que todo
  admin logado assina; chega como toast/badge na hora.
- **Web Push** pro painel **fechado** — o navegador entrega em segundo
  plano via Service Worker, mesmo sem aba aberta.

Um `NotificationService.notify_staff(...)` faz o fan-out pros dois.

!!! info "Instalação"
    SSE é núcleo — já vem no SDK. O Web Push pede o extra:
    `uv add "tempest-fastapi-sdk[webpush]"` (traz `pywebpush` +
    `cryptography`). Primitivas em [SSE](recipes/sse.md) e
    [Web Push](recipes/webpush.md).

### A tabela de inscrições Web Push

Um device por linha, `endpoint` único, FK pro usuário — a linha concreta
sobre a base do SDK (igual ao padrão de auth):

```python
# src/db/models.py  (junto dos modelos da §1)
from tempest_fastapi_sdk import BaseWebPushSubscriptionModel


class WebPushSubscription(BaseWebPushSubscriptionModel):
    __tablename__ = "web_push_subscriptions"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
```

### O serviço de notificação

Uma peça só, que orquestra os dois canais. O broker é o mesmo singleton de
processo do [recipe de SSE](recipes/sse.md#broadcast-pra-varios-clientes-ssebroker);
o `WebPushSubscriptionService` sai do [recipe de Web Push](recipes/webpush.md#tabela-repositorio-servico-e-controller-recomendado).

```python
# src/services/notification.py
from tempest_fastapi_sdk import (
    BaseRepository,
    SSEBroker,
    WebPushPayloadSchema,
    WebPushSubscriptionService,
)

from src.db.models import User

STAFF_CHANNEL = "staff"


class NotificationService:
    """Fan one staff-relevant domain event out to SSE and Web Push.

    The audit log is the durable record of what happened; this is the live
    signal that pokes whoever is on shift. The same payload goes on two
    channels: a shared SSE channel every open admin panel subscribes to,
    and Web Push for the panels that are closed.
    """

    def __init__(
        self,
        broker: SSEBroker,
        push: WebPushSubscriptionService,
        users: BaseRepository,
    ) -> None:
        """Wire the SSE broker, the Web Push service and the user repository.

        Args:
            broker (SSEBroker): Fan-out broker for the shared staff channel.
            push (WebPushSubscriptionService): Per-device Web Push delivery.
            users (BaseRepository): Repository used to resolve staff members.
        """
        self.broker = broker
        self.push = push
        self.users = users

    async def notify_staff(
        self, event: str, title: str, body: str, data: dict
    ) -> None:
        """Broadcast a domain event to the whole staff, on both channels.

        Publishes once to the shared SSE channel (every open panel), then
        pushes each staff member's registered devices for the closed ones.

        Args:
            event (str): SSE event name and Web Push tag (e.g. "new_order").
            title (str): Web Push notification title.
            body (str): Web Push notification body.
            data (dict): Payload carried identically on both channels.
        """
        await self.broker.publish(STAFF_CHANNEL, data, event=event)
        payload = WebPushPayloadSchema(title=title, body=body, tag=event, data=data)
        admins = await self.users.list()
        for user in admins:
            if user.role in {"staff", "superadmin"}:
                await self.push.notify_user(user.id, payload)
```

`notify_staff` é o **fan-out**: um mesmo evento de domínio sai por dois canais
no mesmo instante — SSE pro painel **aberto**, Web Push pro **fechado** —
carregando o **mesmo `data`**. Passo a passo do corpo do método:

1. **Broadcast SSE** — `await self.broker.publish(STAFF_CHANNEL, data, event=event)`
   publica **uma vez** no canal compartilhado `"staff"`. O broker percorre todos
   os streams inscritos nesse canal (um por painel admin aberto) e entrega o
   mesmo evento a cada um: um `publish` → N painéis. Quem está com o painel
   fechado não tem stream aqui — é o passo 3 que cobre esse caso.
2. **Monta o payload do sistema** — `WebPushPayloadSchema(title=..., body=...,
   tag=event, data=data)` embrulha o **mesmo `data`** no formato do Web Push,
   somando o que só a notificação do sistema mostra (`title`/`body`) e a `tag`
   que colapsa duplicatas na bandeja.
3. **Fan-out do Web Push** — `self.users.list()` resolve o time e, pra cada
   `staff`/`superadmin`, `push.notify_user(user.id, payload)` empurra pra
   **todos os devices registrados** daquele usuário (a tabela da subseção
   anterior). É o canal que chega com o painel **fechado**.

As duas chamadas-chave, argumento por argumento:

- `broker.publish(STAFF_CHANNEL, data, event=event)` — 1º argumento é o
  **canal** (`"staff"`, a mesma string que o endpoint de inscrição usa); 2º é o
  **payload** (`data`, que vira JSON no frame SSE); `event=` é o **nome** do
  evento que o front escuta com `addEventListener`.
- `push.notify_user(user.id, payload)` — 1º argumento é **quem** recebe (o id do
  usuário; o service resolve os devices dele por baixo); 2º é o
  `WebPushPayloadSchema` entregue a cada device.

!!! note "Por que os dois canais, não um só"
    O `publish` SSE só alcança quem está **conectado agora**; o Web Push depende
    de permissão do browser. Nenhum dos dois é registro confiável — quem guarda
    *"o que aconteceu"* de forma durável é a trilha de auditoria (§1, §4). Aqui o
    objetivo é o oposto: **avisar na hora**. A auditoria grava, a notificação
    cutuca — o `notify_staff` só cuida da segunda metade do par.

### Disparando no evento de domínio

O pedido entra pela loja (não pelo admin). O controller que cria o pedido
delega pros dois services depois de persistir — auditoria e notificação são
camadas distintas: uma grava, a outra avisa. Sem acesso a banco na rota nem
no controller: quem consulta staff é o `NotificationService`.

```python
# src/controllers/order.py  (lado da loja — onde o pedido é feito)
from src.schemas import OrderCreateSchema, OrderResponseSchema
from src.services import NotificationService, OrderService


class OrderController:
    """Create an order and alert staff in real time."""

    def __init__(
        self, orders: OrderService, notifications: NotificationService
    ) -> None:
        """Wire the order service and the notification fan-out.

        Args:
            orders (OrderService): Order business logic.
            notifications (NotificationService): Live staff notifier.
        """
        self.orders = orders
        self.notifications = notifications

    async def place_order(self, data: OrderCreateSchema) -> OrderResponseSchema:
        """Persist an order and push a live alert to every staff member.

        Args:
            data (OrderCreateSchema): The order creation payload.

        Returns:
            The created order.
        """
        order = await self.orders.create(data)
        await self.notifications.notify_staff(
            event="new_order",
            title="Novo pedido",
            body=f"{order.customer_email} — R$ {order.total}",
            data={"order_id": str(order.id), "url": f"/admin/orders/{order.id}"},
        )
        return order
```

### O endpoint de inscrição SSE

O painel aberto assina o canal `"staff"`. Como o `EventSource` nativo não
manda header, a rota reusa **o mesmo cookie de sessão** que o
`make_admin_router` emitiu no login — o `SignedCookieSessionStore` com o
mesmo `secret_key` e o `UserModelAuthBackend` da §4:

```python
# src/api/dependencies/admin_stream.py
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.admin import SignedCookieSessionStore, UserModelAuthBackend

from src.core.settings import settings
from src.db.connection import db
from src.db.models import User

_store = SignedCookieSessionStore(secret_key=settings.SECRET_KEY)
_backend = UserModelAuthBackend(User, mfa_issuer="Loja")


async def require_admin(
    request: Request,
    session: AsyncSession = Depends(db.session_dependency),
) -> User:
    """Resolve the logged-in admin from the panel's own session cookie.

    EventSource cannot send an Authorization header, so the stream leans on
    the signed session cookie make_admin_router already issued at login —
    no second auth mechanism to keep in sync.

    Args:
        request (Request): The inbound SSE request.
        session (AsyncSession): A live DB session.

    Returns:
        The authenticated admin user.

    Raises:
        HTTPException: 401 when there is no valid, fully-authenticated
            admin session (missing, tampered, or still MFA-pending).
    """
    admin_session = _store.load(request)
    if admin_session is None or admin_session.mfa_pending:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    user = await _backend.load_principal(session, admin_session.principal_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return user
```

Passo a passo do que o `require_admin` faz a cada requisição do stream:

1. `_store.load(request)` — lê o cookie de sessão que o `make_admin_router`
   gravou no login e **confere a assinatura**. Cookie ausente ou adulterado
   volta como `None`.
2. `admin_session is None or admin_session.mfa_pending` — barra tanto a sessão
   inexistente/inválida quanto a que passou a senha mas **ainda não** concluiu o
   MFA. Qualquer um dos dois → `401`.
3. `_backend.load_principal(session, admin_session.principal_id)` — carrega o
   `User` real do banco pelo id guardado na sessão. Se o usuário sumiu (removido
   depois do login) → `401`.
4. Devolve o `User` autenticado. O endpoint não usa o objeto além de exigir que
   ele exista — por isso o parâmetro na rota é `_`.

```python
# src/api/routers/admin_stream.py
from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import SSEBroker

from src.api.dependencies.admin_stream import require_admin
from src.api.dependencies.resources import get_broker
from src.db.models import User

router = APIRouter(prefix="/admin/notifications")


@router.get("/stream")
async def stream(
    _: User = Depends(require_admin),
    broker: SSEBroker = Depends(get_broker),
) -> StreamingResponse:
    """Subscribe the open admin panel to the shared "staff" channel."""
    return broker.response(STAFF_CHANNEL)
```

O endpoint em si é uma linha. Passo a passo de cada
`GET /admin/notifications/stream`:

1. `Depends(require_admin)` roda o guard acima. Sem uma sessão de admin válida o
   request morre em `401` e **nunca** chega no broker — o stream é privado do
   time.
2. `Depends(get_broker)` injeta o broker compartilhado — o mesmo singleton de
   processo do [recipe de SSE](recipes/sse.md#broadcast-pra-varios-clientes-ssebroker),
   guardado em `app.state`.
3. `broker.response(STAFF_CHANNEL)` faz **três coisas numa chamada só**:
     - **register** — cria um `EventStream` novo e o inscreve no canal `"staff"`;
     - **stream** — devolve o `StreamingResponse` com os headers de SSE já
       prontos (o painel começa a receber na hora);
     - **unregister** — amarra um `on_disconnect` que remove esse stream do canal
       quando o painel fecha. Roda no `finally` do gerador da resposta, o único
       ponto que dispara na desconexão — então não tem `try/finally` pra você
       esquecer.

!!! info "Canal compartilhado — todo admin no mesmo `\"staff\"`"
    No [recipe de SSE](recipes/sse.md#broadcast-pra-varios-clientes-ssebroker) o
    canal era o **id de cada usuário**: cada um no seu, isolado dos outros. Aqui
    é o oposto — **todo** admin logado se inscreve no **mesmo** canal `"staff"`.
    Por isso um único `broker.publish("staff", ...)` (o passo 1 do
    `notify_staff`) alcança **todos** os painéis abertos de uma vez, sem o
    serviço precisar saber quem está conectado. Os dois lados só combinam na
    mesma string constante `STAFF_CHANNEL` — inscrição de um lado, publicação do
    outro.

### Montando: inscrição de device + stream

O Web Push precisa que cada device se inscreva; o `make_web_push_router`
entrega `/subscribe` + `/unsubscribe` prontos (estilo `make_auth_router`).
Ligue-os no `create_app` da §4, ao lado do router do admin e do stream:

```python
# src/api/app.py  (somando à §4)
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    BaseRepository,
    SSEBroker,
    WebPushDispatcher,
    WebPushSubscriptionService,
    make_web_push_router,
)

from src.api.dependencies import get_current_user_id, get_session
from src.api.routers.admin_stream import router as admin_stream_router
from src.db.models import WebPushSubscription

broker = SSEBroker()   # mesmo singleton que o get_broker resolve


def _push_service(session: AsyncSession) -> WebPushSubscriptionService:
    repo = BaseRepository(session, model=WebPushSubscription)
    return WebPushSubscriptionService(repo, WebPushDispatcher(**settings.webpush_kwargs()))


app.state.broker = broker
app.include_router(admin_stream_router)
app.include_router(
    make_web_push_router(
        service_factory=_push_service,
        session_factory=get_session,
        current_user_id=get_current_user_id,
    )
)
# GET  /admin/notifications/stream         (SSE, cookie de admin)
# POST /api/push/subscribe | /unsubscribe  (registro de device)
```

No front do painel, o `EventSource` assina o canal e cada `new_order` vira
um toast (o cookie de admin vai sozinho com `withCredentials`):

```javascript
const es = new EventSource("/admin/notifications/stream", { withCredentials: true });
es.addEventListener("new_order", (e) => toast(JSON.parse(e.data)));
```

Quando um pedido entra, todo painel aberto recebe o frame na hora:

```text
event: new_order
data: {"order_id": "9f3a...", "url": "/admin/orders/9f3a..."}
```

E quem estiver com o painel fechado recebe o **mesmo** evento como
notificação Web Push do sistema (`title`/`body`), com o Service Worker
abrindo `data.url` no clique.

!!! check "Durável vs ao vivo — os dois, não um ou outro"
    A auditoria (`audit_model=`) responde *"o que aconteceu e quem fez"*
    semanas depois; a notificação responde *"o que preciso ver agora"*. O
    SSE só alcança quem está **conectado no momento** e o push depende de
    permissão do browser — nenhum dos dois é registro confiável. Persista o
    fato (auditoria/banco) **e** dispare o sinal (notificação); nunca troque
    um pelo outro.

## 6. O que você vê

- **Dashboard** — três cards de negócio (número, tendência, partição) no
  topo, ao lado do painel de sistema (CPU/RAM). Só os modelos que o
  `access_policy` libera pra `VIEW` aparecem (staff não vê o que não pode).
- **Produtos** — busca de categoria por autocomplete no form; `specs`
  como editor JSON (pretty-print + validação); botão **Import CSV**;
  timeline de auditoria no detail de cada produto.
- **Pedidos** — abas **All / Pendentes / Pagos** (lenses); os itens do
  pedido numa tabela no detail, com "Add" já ligado ao pedido.
- **Notificações** — com o painel aberto, cada pedido novo pinga um toast
  ao vivo (SSE no canal `"staff"`); com ele fechado, chega como Web Push do
  sistema — o mesmo evento que a auditoria registra em paralelo.
- **RBAC** — um `staff` navega e lê tudo o que pode, mas todo botão de
  criar/editar/apagar some e as rotas respondem `403`.

!!! check "Recap"
    Um `AdminSite` + alguns `AdminModel` bem anotados entregam um admin de
    produção: auditoria, autocomplete, inlines, métricas, import, RBAC e
    lenses — cada um um argumento, todos tipados, sem metaclasse. Por cima,
    um `NotificationService` reusa o `SSEBroker` (canal `"staff"`) e o
    `WebPushSubscriptionService` pra transformar eventos de domínio em
    sinais ao vivo — o par da auditoria: uma grava durável, o outro avisa
    na hora. Detalhe de cada um nas receitas do
    [Painel admin](recipes/admin.md), [SSE](recipes/sse.md) e
    [Web Push](recipes/webpush.md).
