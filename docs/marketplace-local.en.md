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
`NotificationService` takes the event **once** and *fans it out* to both
channels.

**What "fan-out" means here:** you call `notify(...)` a single time, and under
the hood the same event leaves through **two** independent paths — an SSE frame
(for the app that's open right now) and a Web Push notification (for the app
that's closed, delivered by the Service Worker). Both carry the **same payload**
(`data`), so the client handles the notification the same way whether it arrived
over SSE or push. One `notify` → two deliveries.

This step has three parts: **(1)** the Web Push subscription table, **(2)** the
service that does the fan-out, and **(3)** wiring it into the app (the SSE
subscribe endpoint + the push router). One at a time.

#### Part 1 — the Web Push subscription table

The SSE side reuses the **same `SSEBroker` as chat** (section 3), now on a
per-user channel (`str(user_id)`) instead of the conversation channel — no new
moving part. Web Push, on the other hand, needs a per-device subscription table:
the SDK ships the base row `BaseWebPushSubscriptionModel` and you create the
concrete one with the FK to your `UserModel` (just like the
[recipe »](recipes/webpush.md)):

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

#### Part 2 — the fan-out service

The `NotificationService` is tiny: it holds the two pieces (the broker and the
push service) and exposes a single `notify(...)` method. That method is what
actually does the fan-out.

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

The body of `notify` is **two lines**, one per channel:

- **`await self.broker.publish(str(user_id), data, event=event)`** — the
  **live (SSE)** delivery. Publishes on the `SSEBroker` using the **user id as
  the channel**; every stream subscribed to that channel (the recipient's open
  app) gets the frame instantly. It's fire-and-forget: if nobody is connected,
  it does nothing and raises nothing.
- **`await self.push.notify_user(user_id, WebPushPayloadSchema(...))`** — the
  **background (Web Push)** delivery. `notify_user` looks up every subscription
  for that user, fires the push to each device and **prunes dead ones on its
  own** (expired or unsubscribed). The `WebPushPayloadSchema` wraps the
  `title`/`body` (the text the system notification shows), uses `event` as the
  `tag`, and carries the same `data` as the SSE frame.

Notice both lines get the **same `user_id`** as the target and the **same
`data`** as the payload — that's what guarantees the open app (SSE) and the
closed app (push) see exactly the same thing.

With the service in place, each **business event** calls `notify` **once**,
passing the id of **whoever should hear about it**. The new order notifies the
**seller**; the new chat message notifies the **recipient**:

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

Why each call's `user_id` is different:

- **New order → `seller_id`.** The one who needs to know about the order is the
  **seller**, so the channel (and the push target) is their id. The buyer who
  placed the order gets nothing — they already know they bought.
- **New message → `recipient_id`.** The one who needs to be told is **whoever
  will receive** the message, not whoever sent it. The channel is the
  recipient's id; the sender sees their own message through the chat's normal
  response.

In both cases, the id passed to `notify` is **the same** the recipient uses to
subscribe over SSE (`GET /notifications/stream`, below): both sides must agree
on the same channel string, otherwise the frame is published on a channel nobody
is listening to.

#### Part 3 — wiring it into the app

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

Step by step, what happens on each `GET /notifications/stream`:

1. `Depends(get_current_user_id)` resolves **who** the client is from the token.
   Their id becomes the channel name — each user has their own, isolated from
   the others.
2. `broker.response(str(user_id))` does **three things in one call** (the same
   shortcut the chat endpoint uses, now on a per-user channel):
     - **register** — creates a fresh `EventStream` and subscribes it to the
       `user_id` channel;
     - **stream** — returns a `StreamingResponse` with the SSE headers already
       set, and the client starts receiving;
     - **unregister** — wires an `on_disconnect` that removes this stream from
       the channel when the client drops, no hand-rolled `try/finally`.

It's **the same `broker` as chat** (imported from `src.services.chat`): a single
`SSEBroker` in the process serves both uses, only the channel string changes —
`conversation_id` in chat, `user_id` here.

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
