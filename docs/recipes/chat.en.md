# Chat (conversations + messages)

A ready threaded-chat module over the SDK primitives (`BaseModel` /
`BaseRepository` / pagination / SSE). You inherit the concrete tables,
mount the router, and get conversations, messages and real-time delivery
— without hand-writing the data layer.

The `tempest_fastapi_sdk.chat` module ships three pieces:

- **Abstract tables** — `BaseConversationModel`,
  `BaseConversationParticipantModel`, `BaseMessageModel` (+ `make_*`
  factories for tests/scripts).
- **`ChatService`** — the business logic: start a conversation, post a
  message, list history, list a user's conversations.
- **`make_chat_router`** — the HTTP endpoints, in the same shape as
  `make_auth_router` / `make_web_push_router`.

!!! info "No extra"
    The module uses only the SDK core. No extras to install — import and
    go.

## The tables

Like the SDK's other reusable tables, the SDK ships the **abstract** row
and your project ships the **concrete** one (so the FK and
`__tablename__` live in the application's metadata). Hand-write them in
production:

```python
from tempest_fastapi_sdk.chat import (
    BaseConversationModel,
    BaseConversationParticipantModel,
    BaseMessageModel,
)
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID


class ConversationModel(BaseConversationModel):
    __tablename__ = "conversations"


class ConversationParticipantModel(BaseConversationParticipantModel):
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
```

!!! tip "Shortcut for tests"
    In tests and scripts, the factories build the concrete class at
    runtime:

    ```python
    from tempest_fastapi_sdk.chat import (
        make_conversation_model,
        make_conversation_participant_model,
        make_message_model,
    )

    Conversation = make_conversation_model()
    Participant = make_conversation_participant_model()
    Message = make_message_model()
    ```

## The service

`ChatService` takes three repositories (and, optionally, an `SSEBroker`
for real time):

```python
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService
from sqlalchemy.ext.asyncio import AsyncSession


def build_chat_service(session: AsyncSession) -> ChatService:
    return ChatService(
        conversations=BaseRepository(session, model=ConversationModel),
        participants=BaseRepository(session, model=ConversationParticipantModel),
        messages=BaseRepository(session, model=MessageModel),
    )


async def demo(session: AsyncSession, alice: UUID, bob: UUID) -> None:
    service = build_chat_service(session)

    # The creator joins as a participant automatically.
    conversation = await service.start_conversation(alice, [bob], title="Project X")

    await service.post_message(conversation.id, alice, "Shall we start?")
    await service.post_message(conversation.id, bob, "Let's!")

    page = await service.list_messages(conversation.id, page=1, page_size=20)
    for message in page["items"]:
        print(message.sender_id, message.body)

    mine = await service.list_conversations(alice)  # [] when there are none
```

`list_messages` returns the SDK's standard offset-pagination dict
(`items` already mapped to `MessageResponseSchema`, `total`, `page`,
`size`, `pages`), ordered oldest-first.

## The router

`make_chat_router` takes **how** to resolve the session, the service and
the authenticated user — like the SDK's other routers:

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.chat import make_chat_router


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session


def current_user_id() -> UUID:
    ...  # your auth dependency (JWT/session) resolving the user's UUID


app = FastAPI()
app.include_router(
    make_chat_router(
        service_factory=build_chat_service,
        session_factory=get_session,
        current_user_id=current_user_id,
    )
)
```

Mounted endpoints (all require authentication):

| Method | Route | Does |
| --- | --- | --- |
| `POST` | `/api/chat/conversations` | Start a conversation (creator becomes a participant) |
| `GET` | `/api/chat/conversations` | List the user's conversations |
| `POST` | `/api/chat/conversations/{id}/messages` | Post a message (participant only) |
| `GET` | `/api/chat/conversations/{id}/messages` | Page the history (participant only) |
| `GET` | `/api/chat/conversations/{id}/stream` | SSE of new messages (participant only) |

!!! warning "Participant guard"
    Posting, reading and subscribing require the authenticated user to be
    a participant of the conversation; otherwise the router responds
    `403`.

## Real time via SSE

Inject an `SSEBroker` into the service and every posted message is also
published to the conversation's channel (`str(conversation_id)`),
reusing the SSE fan-out the SDK already has. Without a broker, the
`/stream` endpoint responds `404`.

```python
from tempest_fastapi_sdk.sse import SSEBroker

broker = SSEBroker()  # single-process; pass redis=<client> for multi-worker


def build_chat_service(session: AsyncSession) -> ChatService:
    return ChatService(
        conversations=BaseRepository(session, model=ConversationModel),
        participants=BaseRepository(session, model=ConversationParticipantModel),
        messages=BaseRepository(session, model=MessageModel),
        broker=broker,
    )
```

The client subscribes with an `EventSource` pointing at
`/api/chat/conversations/{id}/stream` and receives `message` events whose
`data` is the JSON of `MessageResponseSchema`. See the
**[Server-Sent Events »](sse.md)** recipe for the client side and the
multi-worker Redis bridge.

## Recap

- Inherit `BaseConversationModel` / `BaseConversationParticipantModel` /
  `BaseMessageModel` and point the FKs at your `UserModel`.
- `ChatService` covers start/post/list; it returns schemas, not ORM rows.
- `make_chat_router` mounts the endpoints with the participant guard.
- Pass an `SSEBroker` to get real-time delivery for free.
