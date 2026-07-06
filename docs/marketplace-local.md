# Exemplo integrado — marketplace de bairro

O [checkout com Pix](integrated.md) junta os blocos de pagamento. Aqui
juntamos os módulos mais novos do SDK num fluxo de **comércio local**: um
comprador autenticado encontra vendedores **próximos**, vê a **distância e
o tempo** até cada um, conversa por **chat** em tempo real e, no fim,
**avalia** o vendedor com estrelas.

Componentes exercitados de uma vez: **geo** (`GeoPointMixin` +
`GeoRepositoryMixin`, `NominatimBackend`, `estimate_travel`), **chat**
(`ChatService` + `make_chat_router` + `SSEBroker`), **reviews**
(`ReviewService` + `make_reviews_router`) e a **auth** do SDK para o
usuário atual.

!!! info "O que você precisa"
    Núcleo do SDK + o extra `[geo]` (para o `httpx` do geocoder/OSRM). Chat
    e reviews não pedem extra. Um Redis é opcional (fan-out SSE
    multi-worker).

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

## Recap

Um fluxo de comércio local inteiro, só com blocos do SDK:

- **Descoberta** — `NominatimBackend` geocodifica o endereço,
  `GeoRepositoryMixin.nearby` acha vendedores no raio, `estimate_travel`
  dá o ETA. Sem API paga.
- **Conversa** — `ChatService` + `make_chat_router` com `SSEBroker` para
  mensagens ao vivo.
- **Avaliação** — `ReviewService` faz upsert de 1 voto por usuário e
  `aggregate` entrega os números da vitrine.
- Os três routers seguem o mesmo molde (fábrica de serviço + sessão +
  usuário atual), então trocar de auth ou de banco não toca no módulo.

Veja as receitas individuais: **[Geolocalização »](recipes/geo.md)**,
**[Chat »](recipes/chat.md)** e **[Comentários + avaliações »](recipes/reviews.md)**.
