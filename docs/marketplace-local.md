# Exemplo integrado — marketplace de bairro

O [checkout com Pix](integrated.md) junta os blocos de pagamento. Aqui
juntamos os módulos mais novos do SDK num fluxo de **comércio local**: um
comprador autenticado encontra vendedores **próximos**, vê a **distância e
o tempo** até cada um, conversa por **chat** em tempo real, recebe
**notificações ao vivo** (pedido novo, mensagem nova) e, no fim,
**avalia** o vendedor com estrelas.

Componentes exercitados de uma vez: **geo** (`GeoPointMixin` +
`GeoRepositoryMixin`, `NominatimBackend`, `estimate_travel`), **chat**
(`ChatService` + `make_chat_router` + `SSEBroker`), **notificações**
(`SSEBroker` + `WebPushSubscriptionService`, um evento em dois canais),
**reviews** (`ReviewService` + `make_reviews_router`) e a **auth** do SDK
para o usuário atual.

!!! info "O que você precisa"
    Núcleo do SDK + o extra `[geo]` (para o `httpx` do geocoder/OSRM). Chat,
    reviews e o SSE das notificações são core (sem extra); o Web Push pede o
    extra `[webpush]` — `uv add "tempest-fastapi-sdk[webpush]"`. Um Redis é
    opcional (fan-out SSE multi-worker).

## 1. Modelos

O vendedor carrega um ponto geográfico; chat e reviews usam as tabelas
base do SDK apontando para o seu `UserModel`.

```python
# src/db/models.py
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from tempest_fastapi_sdk import BaseModel, BaseUserModel
from tempest_fastapi_sdk.chat import (
    BaseConversationModel,
    BaseConversationParticipantModel,
    BaseMessageModel,
)
from tempest_fastapi_sdk.geo import GeoPointMixin
from tempest_fastapi_sdk.reviews import BaseCommentModel, BaseRatingModel


class UserModel(BaseUserModel):
    __tablename__ = "users"


class SellerModel(GeoPointMixin, BaseModel):
    """A seller pinned to a location (latitude/longitude from the mixin)."""

    __tablename__ = "sellers"
    name: Mapped[str] = mapped_column(String(120))


class ConversationModel(BaseConversationModel):
    __tablename__ = "conversations"


class ParticipantModel(BaseConversationParticipantModel):
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_participant"),
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class MessageModel(BaseMessageModel):
    __tablename__ = "messages"
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class CommentModel(BaseCommentModel):
    __tablename__ = "comments"
    author_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class RatingModel(BaseRatingModel):
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint(
            "target_type", "target_id", "user_id", name="uq_rating_target_user"
        ),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

## 2. Encontrar vendedores próximos (geo)

O comprador manda um endereço (ou CEP). Geocodificamos com Nominatim,
buscamos vendedores num raio direto do banco e anexamos a estimativa de
viagem de moto — tudo sem API paga.

```python
# src/services/discovery.py
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.geo import (
    Coordinate,
    GeoRepositoryMixin,
    NominatimBackend,
    TravelMode,
    estimate_travel,
)

from src.db.models import SellerModel


class SellerRepository(GeoRepositoryMixin, BaseRepository[SellerModel]):
    """Repository with the radius search mixin."""


class DiscoveryService:
    """Find nearby sellers from a buyer's address."""

    def __init__(
        self,
        sellers: SellerRepository,
        geocoder: NominatimBackend,
    ) -> None:
        self.sellers = sellers
        self.geocoder = geocoder

    async def nearby(
        self,
        address: str,
        *,
        radius_km: float = 5.0,
    ) -> list[dict[str, object]]:
        """Return active sellers within ``radius_km`` of the address.

        Each entry carries the seller plus a motorcycle travel estimate.

        Args:
            address: The buyer's address or CEP.
            radius_km: Search radius in kilometres.

        Returns:
            Nearest-first sellers with a `TravelEstimate` each (`[]` when
            the address cannot be geocoded or nothing is nearby).
        """
        hit = await self.geocoder.geocode(address)
        if hit is None:
            return []
        origin: Coordinate = hit.coordinate
        found = await self.sellers.nearby(
            origin,
            radius_km=radius_km,
            extra_filters={"is_active": True},
            limit=20,
        )
        return [
            {
                "seller": seller,
                "eta": estimate_travel(
                    origin,
                    seller.coordinate(),
                    TravelMode.MOTORCYCLE,
                ),
            }
            for seller in found
        ]
```

!!! tip "Barato primeiro, preciso depois"
    `nearby` já pré-filtra por bounding-box no SQL e refina com Haversine.
    Use `estimate_travel` (offline) para a lista; chame `OSRMBackend.route`
    só no vendedor escolhido, quando o tempo real importa.

## 3. Conversar com o vendedor (chat em tempo real)

Um `SSEBroker` no `ChatService` publica cada mensagem no canal da conversa;
o `make_chat_router` já expõe o `/stream`.

```python
# src/services/chat.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService
from tempest_fastapi_sdk.sse import SSEBroker

from src.db.models import ConversationModel, MessageModel, ParticipantModel

broker = SSEBroker()  # passe redis=<client> para multi-worker


def build_chat_service(session: AsyncSession) -> ChatService:
    return ChatService(
        conversations=BaseRepository(session, model=ConversationModel),
        participants=BaseRepository(session, model=ParticipantModel),
        messages=BaseRepository(session, model=MessageModel),
        broker=broker,
    )
```

O frontend abre um `EventSource` em
`/api/chat/conversations/{id}/stream` e recebe eventos `message` conforme
o vendedor responde. Sem hand-rolling: o router cuida de registrar e
desinscrever o stream.

## 4. Avaliar o vendedor (0–5 estrelas)

Depois da compra, o comprador avalia. O alvo é polimórfico
(`("seller", seller_id)`), então a mesma tabela serve produto, post ou
qualquer coisa depois.

```python
# src/services/reviews.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.reviews import ReviewService

from src.db.models import CommentModel, RatingModel


def build_review_service(session: AsyncSession) -> ReviewService:
    return ReviewService(
        comments=BaseRepository(session, model=CommentModel),
        ratings=BaseRepository(session, model=RatingModel),
    )
```

A vitrine mostra a média: `await service.aggregate("seller", seller_id)`
devolve `average`, `count` e a `distribution` por estrela — os números do
selo "4,7 ★ (321 avaliações)".

## 5. Montando o app

Os três routers do SDK entram com o mesmo molde: fábrica de serviço,
fábrica de sessão e a dependência de usuário atual.

```python
# src/api/app.py
import httpx
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.chat import make_chat_router
from tempest_fastapi_sdk.geo import NominatimBackend
from tempest_fastapi_sdk.reviews import make_reviews_router

from src.core.resources import db, get_current_user_id
from src.services.chat import build_chat_service
from src.services.discovery import DiscoveryService, SellerRepository
from src.services.reviews import build_review_service
from src.db.models import SellerModel


async def get_session() -> AsyncIterator[AsyncSession]:
    async with db.get_session_context() as session:
        yield session


def create_app() -> FastAPI:
    app = FastAPI(title="Marketplace de bairro")
    http = httpx.AsyncClient()

    @app.get("/api/discovery")
    async def discovery(
        address: str,
        session: AsyncSession = Depends(get_session),
        _user_id: UUID = Depends(get_current_user_id),
    ) -> list[dict[str, object]]:
        service = DiscoveryService(
            SellerRepository(session, model=SellerModel),
            NominatimBackend(http_client=http, user_agent="marketplace/1.0"),
        )
        return await service.nearby(address)

    app.include_router(
        make_chat_router(
            service_factory=build_chat_service,
            session_factory=get_session,
            current_user_id=get_current_user_id,
        )
    )
    app.include_router(
        make_reviews_router(
            service_factory=build_review_service,
            session_factory=get_session,
            current_user_id=get_current_user_id,
        )
    )
    return app
```

## 6. Notificações ao vivo (SSE + Web Push)

Um evento de domínio — **pedido novo** para o vendedor, **mensagem nova**
para o destinatário — precisa chegar dos dois jeitos: **ao vivo** com o app
aberto (SSE) e **em segundo plano** com o app fechado (Web Push). Um
`NotificationService` recebe o evento **uma vez** e faz o *fan-out* para os
dois canais.

**O que "fan-out" quer dizer aqui:** você chama `notify(...)` uma só vez, e por
baixo o mesmo evento sai por **dois** caminhos independentes — um frame SSE (pro
app que está aberto na hora) e uma notificação Web Push (pro app que está
fechado, entregue pelo Service Worker). Os dois carregam o **mesmo payload**
(`data`), então o cliente trata a notificação igual, tenha ela chegado por SSE
ou por push. Um `notify` → duas entregas.

Este passo tem três partes: **(1)** a tabela de inscrições do Web Push, **(2)**
o serviço que faz o fan-out e **(3)** a fiação no app (endpoint de inscrição SSE
+ router de push). Vamos uma de cada vez.

#### Parte 1 — a tabela de inscrições do Web Push

O SSE reaproveita o **mesmo `SSEBroker` do chat** (seção 3), agora num canal por
usuário (`str(user_id)`) em vez do canal da conversa — nenhuma peça nova. O Web
Push, por outro lado, precisa de uma tabela de inscrições por device: o SDK traz
a linha base `BaseWebPushSubscriptionModel` e você cria a concreta com a FK pro
seu `UserModel` (igual à [receita »](recipes/webpush.md)):

```python
# src/db/models.py (junto com os modelos da seção 1)
from tempest_fastapi_sdk import BaseWebPushSubscriptionModel


class WebPushSubscriptionModel(BaseWebPushSubscriptionModel):
    """A user's Web Push subscription (one row per device)."""

    __tablename__ = "web_push_subscriptions"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

#### Parte 2 — o serviço de fan-out

O `NotificationService` é minúsculo: ele guarda as duas peças (o broker e o
serviço de push) e expõe um único método `notify(...)`. É esse método que faz o
fan-out de verdade.

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
        """Wire the SSE broker and the Web Push subscription service.

        Args:
            broker (SSEBroker): Per-user fan-out for live (app-open) delivery.
            push (WebPushSubscriptionService): Delivers to a user's devices
                when the app is closed, pruning dead subscriptions.
        """
        self.broker = broker
        self.push = push

    async def notify(
        self,
        user_id: UUID,
        *,
        event: str,
        title: str,
        body: str,
        data: dict[str, object],
    ) -> None:
        """Deliver the same event on both channels.

        Args:
            user_id (UUID): The recipient — SSE channel and Web Push target.
            event (str): Event name (SSE `event:` field and push `tag`).
            title (str): Notification title (Web Push).
            body (str): Notification body / preview (Web Push).
            data (dict[str, object]): Shared payload carried by both channels.
        """
        await self.broker.publish(str(user_id), data, event=event)
        await self.push.notify_user(
            user_id,
            WebPushPayloadSchema(title=title, body=body, tag=event, data=data),
        )
```

O corpo do `notify` são **duas linhas**, uma por canal:

- **`await self.broker.publish(str(user_id), data, event=event)`** — a entrega
  **ao vivo (SSE)**. Publica no `SSEBroker` usando o **id do usuário como
  canal**; todo stream inscrito nesse canal (o app aberto do destinatário)
  recebe o frame na hora. É fire-and-forget: se ninguém está conectado, não faz
  nada e não dá erro.
- **`await self.push.notify_user(user_id, WebPushPayloadSchema(...))`** — a
  entrega **em segundo plano (Web Push)**. O `notify_user` busca todas as
  inscrições daquele usuário, dispara o push pra cada device e **poda sozinho**
  as inscrições mortas (expiradas ou canceladas). O `WebPushPayloadSchema`
  embrulha o `title`/`body` (o texto que aparece na notificação do sistema),
  usa o `event` como `tag` e carrega o mesmo `data` do SSE.

Repare que as duas linhas recebem o **mesmo `user_id`** como destino e o **mesmo
`data`** como payload — é isso que garante que app aberto (SSE) e app fechado
(push) veem exatamente a mesma coisa.

Feito o serviço, cada **evento de negócio** chama `notify` **uma vez**, passando
o id de **quem deve ser avisado**. O pedido novo avisa o **vendedor**; a
mensagem nova de chat avisa o **destinatário**:

```python
# Pedido novo -> avisa o vendedor (após persistir o pedido):
await notifications.notify(
    seller_id,
    event="order_created",
    title="Novo pedido",
    body=f"Pedido de R$ {order.total}",
    data={"order_id": str(order.id), "total": str(order.total)},
)

# Mensagem nova de chat -> avisa o destinatário (após persistir a mensagem):
await notifications.notify(
    recipient_id,
    event="chat_message",
    title=sender_name,
    body=preview,
    data={"room_id": str(conversation_id)},
)
```

Por que o `user_id` de cada chamada é diferente:

- **Pedido novo → `seller_id`.** Quem precisa saber do pedido é o **vendedor**,
  então o canal (e o alvo do push) é o id dele. O comprador que criou o pedido
  não recebe nada — ele já sabe que comprou.
- **Mensagem nova → `recipient_id`.** Quem precisa ser avisado é **quem vai
  receber** a mensagem, não quem enviou. O canal é o id do destinatário; o
  remetente vê a própria mensagem pelo retorno normal do chat.

Em ambos os casos, o id passado pro `notify` é **o mesmo** que o destinatário
usa pra se inscrever no SSE (`GET /notifications/stream`, abaixo): os dois lados
precisam combinar na mesma string de canal, senão o frame é publicado num canal
que ninguém está ouvindo.

#### Parte 3 — a fiação no app

No app, o cliente assina o próprio canal com `GET /notifications/stream` e o
Web Push entra com o `make_web_push_router` pronto (`/api/push/subscribe` +
`/unsubscribe`):

```python
# src/api/app.py (adições ao create_app)
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import (
    BaseRepository,
    WebPushDispatcher,
    WebPushSubscriptionService,
    make_web_push_router,
)

from src.core.resources import settings
from src.services.chat import broker  # o mesmo SSEBroker do chat
from src.db.models import WebPushSubscriptionModel


def build_push_service(session: AsyncSession) -> WebPushSubscriptionService:
    return WebPushSubscriptionService(
        BaseRepository(session, model=WebPushSubscriptionModel),
        WebPushDispatcher(**settings.webpush_kwargs()),
    )


@app.get("/notifications/stream")
async def notifications_stream(
    user_id: UUID = Depends(get_current_user_id),
) -> StreamingResponse:
    """Subscribe the caller to their live notification channel."""
    return broker.response(str(user_id))


app.include_router(
    make_web_push_router(
        service_factory=build_push_service,
        session_factory=get_session,
        current_user_id=get_current_user_id,
    )
)
```

Passo a passo do que acontece a cada `GET /notifications/stream`:

1. `Depends(get_current_user_id)` resolve **quem** é o cliente a partir do
   token. O id dele vira o nome do canal — cada usuário tem o seu, isolado dos
   outros.
2. `broker.response(str(user_id))` faz **três coisas numa chamada só** (é o
   mesmo atalho que o endpoint de chat usa, agora num canal por usuário):
     - **register** — cria um `EventStream` novo e o inscreve no canal `user_id`;
     - **stream** — devolve um `StreamingResponse` com os headers de SSE já
       prontos, e o cliente começa a receber;
     - **unregister** — liga um `on_disconnect` que tira esse stream do canal
       quando o cliente cai, sem `try/finally` na mão.

É **o mesmo `broker` do chat** (importado de `src.services.chat`): um único
`SSEBroker` no processo atende os dois usos, só mudando a string de canal —
`conversation_id` no chat, `user_id` aqui.

Com o app aberto, quem estiver no `GET /notifications/stream` recebe o frame
na hora — o mesmo `data` que iria no push:

```text
event: chat_message
data: {"room_id": "8c2f..."}
```

!!! tip "SSE não manda header — autentique por cookie ou query"
    O `EventSource` nativo não deixa mandar `Authorization`. Use cookie de
    sessão na mesma origem, ou o `access token` curto na query string. Os
    dois seams e as primitivas (`broker.response`, backpressure, bridge
    Redis) estão na **[receita de SSE »](recipes/sse.md)**; o VAPID, a tabela
    de inscrições e a poda de devices mortos, na
    **[receita de Web Push »](recipes/webpush.md)**.

## Recap

Um fluxo de comércio local inteiro, só com blocos do SDK:

- **Descoberta** — `NominatimBackend` geocodifica o endereço,
  `GeoRepositoryMixin.nearby` acha vendedores no raio, `estimate_travel`
  dá o ETA. Sem API paga.
- **Conversa** — `ChatService` + `make_chat_router` com `SSEBroker` para
  mensagens ao vivo.
- **Notificações** — `NotificationService.notify` manda um evento de domínio
  (pedido novo, mensagem nova) nos dois canais com o mesmo payload: SSE
  (`broker.publish`, app aberto) e Web Push (`notify_user`, app fechado).
- **Avaliação** — `ReviewService` faz upsert de 1 voto por usuário e
  `aggregate` entrega os números da vitrine.
- Os routers do SDK seguem o mesmo molde (fábrica de serviço + sessão +
  usuário atual), então trocar de auth ou de banco não toca no módulo.

Veja as receitas individuais: **[Geolocalização »](recipes/geo.md)**,
**[Chat »](recipes/chat.md)**, **[SSE »](recipes/sse.md)**,
**[Web Push »](recipes/webpush.md)** e
**[Comentários + avaliações »](recipes/reviews.md)**.
