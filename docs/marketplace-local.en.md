# Integrated example — neighbourhood marketplace

The [Pix checkout](integrated.md) wires the payment blocks together. Here
we combine the SDK's newest modules into a **local-commerce** flow: an
authenticated buyer finds **nearby** sellers, sees the **distance and
time** to each, chats over **real-time chat**, gets **live notifications**
(new order, new message), and finally **rates** the seller with stars.

Components exercised at once: **geo** (`GeoPointMixin` +
`GeoRepositoryMixin`, `NominatimBackend`, `estimate_travel`), **chat**
(`ChatService` + `make_chat_router` + `SSEBroker`), **notifications**
(`SSEBroker` + `WebPushSubscriptionService`, one event on two channels),
**reviews** (`ReviewService` + `make_reviews_router`) and the SDK **auth**
for the current user.

!!! info "What you need"
    The SDK core + the `[geo]` extra (for the geocoder/OSRM `httpx`). Chat,
    reviews and the notification SSE are core (no extra); Web Push needs the
    `[webpush]` extra — `uv add "tempest-fastapi-sdk[webpush]"`. Redis is
    optional (multi-worker SSE fan-out).

## 1. Models

The seller carries a geographic point; chat and reviews use the SDK's base
tables pointing at your `UserModel`.

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

## 2. Find nearby sellers (geo)

The buyer sends an address (or CEP). We geocode it with Nominatim, search
sellers in a radius straight from the database, and attach a motorcycle
travel estimate — all with no paid API.

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

!!! tip "Cheap first, precise later"
    `nearby` already pre-filters by bounding box in SQL and refines with
    Haversine. Use `estimate_travel` (offline) for the list; call
    `OSRMBackend.route` only for the chosen seller, when real time matters.

## 3. Chat with the seller (real time)

An `SSEBroker` on the `ChatService` publishes every message to the
conversation's channel; `make_chat_router` exposes `/stream`.

```python
# src/services/chat.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService
from tempest_fastapi_sdk.sse import SSEBroker

from src.db.models import ConversationModel, MessageModel, ParticipantModel

broker = SSEBroker()  # pass redis=<client> for multi-worker


def build_chat_service(session: AsyncSession) -> ChatService:
    return ChatService(
        conversations=BaseRepository(session, model=ConversationModel),
        participants=BaseRepository(session, model=ParticipantModel),
        messages=BaseRepository(session, model=MessageModel),
        broker=broker,
    )
```

The frontend opens an `EventSource` at
`/api/chat/conversations/{id}/stream` and receives `message` events as the
seller replies. No hand-rolling: the router registers and unregisters the
stream for you.

## 4. Rate the seller (0–5 stars)

After the purchase, the buyer rates. The target is polymorphic
(`("seller", seller_id)`), so the same table later serves products, posts,
or anything else.

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

The storefront shows the average: `await service.aggregate("seller",
seller_id)` returns `average`, `count` and the per-star `distribution` —
the numbers behind a "4.7 ★ (321 reviews)" badge.

## 5. Wiring the app

The SDK's three routers plug in with the same shape: a service factory, a
session factory and the current-user dependency.

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
    app = FastAPI(title="Neighbourhood marketplace")
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

## 6. Live notifications (SSE + Web Push)

A domain event — a **new order** for the seller, a **new message** for the
recipient — has to arrive both ways: **live** with the app open (SSE) and
**in the background** with the app closed (Web Push). A
`NotificationService` takes the event once and fans it out to both channels
with the **same payload**.

The SSE side reuses the **same `SSEBroker` as chat**, now on a per-user
channel (`str(user_id)`). Web Push needs a per-device subscription table —
the SDK ships the base row and you create the concrete one with the FK to
your `UserModel` (just like the [recipe »](recipes/webpush.md)):

```python
# src/db/models.py (alongside the section 1 models)
from tempest_fastapi_sdk import BaseWebPushSubscriptionModel


class WebPushSubscriptionModel(BaseWebPushSubscriptionModel):
    """A user's Web Push subscription (one row per device)."""

    __tablename__ = "web_push_subscriptions"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

The fan-out service is tiny: publish on the broker and call `notify_user`
(which delivers to every device and prunes dead ones on its own).

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

Each **business event** calls `notify` once with the id of whoever should
hear about it. The new order notifies the **seller**; the new chat message
notifies the **recipient**:

```python
# New order -> notify the seller (after persisting the order):
await notifications.notify(
    seller_id,
    event="order_created",
    title="New order",
    body=f"Order for R$ {order.total}",
    data={"order_id": str(order.id), "total": str(order.total)},
)

# New chat message -> notify the recipient (after persisting the message):
await notifications.notify(
    recipient_id,
    event="chat_message",
    title=sender_name,
    body=preview,
    data={"room_id": str(conversation_id)},
)
```

In the app, the client subscribes to its own channel with
`GET /notifications/stream`, and Web Push plugs in via the ready-made
`make_web_push_router` (`/api/push/subscribe` + `/unsubscribe`):

```python
# src/api/app.py (additions to create_app)
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import (
    BaseRepository,
    WebPushDispatcher,
    WebPushSubscriptionService,
    make_web_push_router,
)

from src.core.resources import settings
from src.services.chat import broker  # the same SSEBroker as chat
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

With the app open, whoever holds the `GET /notifications/stream` gets the
frame instantly — the same `data` the push would carry:

```text
event: chat_message
data: {"room_id": "8c2f..."}
```

!!! tip "SSE can't send headers — authenticate via cookie or query"
    The native `EventSource` can't send `Authorization`. Use a session
    cookie on the same origin, or a short-lived `access token` in the query
    string. Both seams and the primitives (`broker.response`, backpressure,
    Redis bridge) live in the **[SSE recipe »](recipes/sse.md)**; VAPID, the
    subscription table and dead-device pruning in the
    **[Web Push recipe »](recipes/webpush.md)**.

## Recap

A whole local-commerce flow, from SDK blocks only:

- **Discovery** — `NominatimBackend` geocodes the address,
  `GeoRepositoryMixin.nearby` finds sellers in the radius, `estimate_travel`
  gives the ETA. No paid API.
- **Chat** — `ChatService` + `make_chat_router` with `SSEBroker` for live
  messages.
- **Notifications** — `NotificationService.notify` sends one domain event
  (new order, new message) on both channels with the same payload: SSE
  (`broker.publish`, app open) and Web Push (`notify_user`, app closed).
- **Rating** — `ReviewService` upserts one vote per user and `aggregate`
  yields the storefront numbers.
- The SDK routers follow the same shape (service + session + current-user
  factories), so swapping auth or database never touches the module.

See the individual recipes: **[Geolocation »](recipes/geo.md)**,
**[Chat »](recipes/chat.md)**, **[SSE »](recipes/sse.md)**,
**[Web Push »](recipes/webpush.md)** and
**[Comments + ratings »](recipes/reviews.md)**.
