# Chat (conversations + messages)

A ready threaded-chat module over the SDK primitives (`BaseModel` /
`BaseRepository` / pagination / SSE). You inherit the concrete tables,
mount the router, and get conversations, messages and real-time delivery
— without hand-writing the data layer.

The `tempest_fastapi_sdk.chat` module ships three pieces:

- **Abstract tables** — `BaseConversationModel`,
  `BaseConversationParticipantModel`, `BaseMessageModel`,
  `BaseMessageAttachmentModel`, `BaseMessageReactionModel` (+ `make_*`
  factories for tests/scripts).
- **`ChatService`** — the business logic: start a conversation, post,
  reply, edit, delete for everyone, react, forward, mark read and run
  the group.
- **`make_chat_router`** — the HTTP endpoints, in the same shape as
  `make_auth_router` / `make_web_push_router`.

!!! info "What the module decides for you"
    The surface exists so each service stops taking, alone, the
    decisions that go wrong the same way everywhere:

    - **a read receipt is a watermark on the participant**, not a row
      per message per reader — in a group of 200 the obvious path costs
      200 rows *per message*, and marking the thread read becomes a bulk
      insert;
    - **one reaction per person**, replaced on re-react — that is the
      `UniqueConstraint(message_id, user_id)`, and widening it to
      include the emoji is what turns a double-tap into two reactions;
    - **deleting for everyone actually clears `body`** and keeps the
      row, so the thread holds its shape and replies still have
      something to quote;
    - **a direct conversation is idempotent per pair** — two threads
      between the same two people is a state the user cannot repair from
      the UI;
    - **a client-generated `client_id`** makes a retry return the
      message that was already stored instead of posting a second one.

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

The columns these tables carry — `kind`, `client_id`, `reply_to_id`,
`forwarded_from_id`, `forward_score`, `edited_at`, `revoked_at`,
`payload` on the message; `role`, `joined_at`, `left_at`,
`history_from`, `muted_until`, `is_pinned`, `is_archived`,
`last_read_at`, `last_read_message_id`, `last_delivered_at` on the
participant — are the minimum any thread between people needs. Two of
them carry a constraint that **changes the behaviour**, so a
hand-written class has to declare them:

```python
from uuid import UUID

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.chat import BaseMessageModel, BaseMessageReactionModel


class MessageModel(BaseMessageModel):
    """One message posted to a conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("sender_id", "client_id", name="uq_messages_sender_client"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class MessageReactionModel(BaseMessageReactionModel):
    """One person's reaction to one message."""

    __tablename__ = "message_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_reaction_per_user"),
    )

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

The first constraint is what makes a retry return the existing row
instead of posting a second one; the second is what makes reacting again
**replace** rather than stack.

!!! danger "The attachment's `message_id` is nullable, and that is the design"
    Upload and post are **two calls**: a 40 MB video that fails on the
    last byte must not take the caption with it, and a repeated post
    must not re-send the file. The attachment row is written when the
    bytes land, and the message that claims it may not exist for another
    minute — or ever, if the sender gives up. Declaring the column
    `NOT NULL` makes **every** upload fail with an integrity error.

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
    Attachment = make_message_attachment_model()
    Reaction = make_message_reaction_model()
    ```

    The factories already declare the constraints above — the quickest
    way to get the right behaviour in a test.

## The service

`ChatService` takes three repositories (and, optionally, an `SSEBroker`
for real time):

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService

from src.db.models import ConversationModel, ConversationParticipantModel, MessageModel


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

Running `demo`, the two `print` calls emit each message in order
(oldest first), with the sender's UUID and body:

```text
2b1e9a4c-1f0d-4c3a-9c21-8e7f0a1b2c3d Shall we start?
7d3c5f8a-6b2e-4a19-b0d4-1c2e3f4a5b6c Let's!
```

`list_messages` returns the SDK's standard offset-pagination dict
(`items` already mapped to `MessageResponseSchema`, `total`, `page`,
`page_size`, `pages`), ordered oldest-first.

### Replying, and the stub the quote carries

Replying is the most-used operation in a group chat. The expensive part
is not the FK — it is the **stub**. The quote has to render without a
second round-trip, and re-sending the whole parent (attachments and
reactions included) under every reply multiplies the page's payload.

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService, MessageCreateSchema


async def reply_to_a_question(
    service: ChatService,
    conversation_id: UUID,
    alice: UUID,
    bob: UUID,
) -> None:
    """Post a question and reply quoting it."""
    parent = await service.post_message(
        conversation_id,
        alice,
        MessageCreateSchema(body="anyone reviewing the PR?"),
    )
    reply = await service.post_message(
        conversation_id,
        bob,
        MessageCreateSchema(body="I will", reply_to_id=parent.id),
    )

    assert reply.reply_to is not None
    print(reply.reply_to.sender_id, reply.reply_to.excerpt, reply.reply_to.revoked)
```

```text
2b1e9a4c-1f0d-4c3a-9c21-8e7f0a1b2c3d anyone reviewing the PR? False
```

!!! danger "Deleting the quoted message deletes the quote"
    The `revoked` field is the one no naive implementation has. Without
    it the deleted text **leaks through whoever replied to it** — the
    message disappears from the thread and stays readable inside every
    reply. `revoke_message` clears `body`, and the stub then answers
    `revoked=True` with an empty `excerpt`.

### Idempotent sending

A message `POST` whose `201` is lost leaves the client unable to tell
whether it landed. Resending blindly posts two; not resending loses it.
The way out is a `client_id` the **client generates before** the
request:

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService, MessageCreateSchema


async def resend(service: ChatService, conversation_id: UUID, alice: UUID) -> None:
    """Send twice with the same `client_id` and store one row."""
    payload = MessageCreateSchema(body="hi", client_id="7f3a-1")

    first = await service.post_message(conversation_id, alice, payload)
    second = await service.post_message(conversation_id, alice, payload)

    assert first.id == second.id  # the same row, not a copy
```

It is the same problem `IdempotencyMiddleware` solves for HTTP in
general — but this key is a domain value: it outlives the message rather
than the request, and it is unique per `(sender_id, client_id)`, so two
clients can pick ids independently.

!!! warning "Uniqueness is per sender, not per conversation"
    Reusing a `client_id` in a **different** conversation is refused with
    a `422` naming the field. Handing back the message that was found
    would be worse than it sounds: the client would get a `201` carrying
    a row from another thread, while the message it actually sent was
    never written. Generate a fresh id per message (a UUID will do).

### Receipts: a watermark, not a row per reader

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService


async def mark_as_read(
    service: ChatService,
    conversation_id: UUID,
    bob: UUID,
    last_seen_id: UUID,
) -> None:
    """Move the watermark and read the counts derived from it."""
    await service.mark_read(conversation_id, bob)  # everything, now
    await service.mark_read(conversation_id, bob, message_id=last_seen_id)

    page = await service.list_messages(conversation_id, with_receipts=True)
    print(page["items"][0].receipts)
```

```text
delivered=1 read=1 total=1
```

A row per message per reader is O(messages × participants): in a group
of 200 that is 200 rows per message. The watermark is **200 rows in
total**, and marking read is one `UPDATE`. "Who read it" becomes an
aggregate over `last_read_at >= message.created_at`. The cost is that
"read" is per-instant rather than per-message — exactly the granularity
the UI shows.

`unread_count` comes free from `list_conversations`.

### Attachments

```python
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService, MessageCreateSchema, MessageKind


async def attach(
    service: ChatService,
    attachments: BaseRepository[Any],
    conversation_id: UUID,
    alice: UUID,
    storage_key: str,
    mime_type: str,
    size_bytes: int,
) -> None:
    """Store the file first, post the message afterwards."""
    attachment = await attachments.add(
        attachments.model(
            storage_key=storage_key,  # a key, never a URL
            filename="photo.jpg",
            mime_type=mime_type,
            size_bytes=size_bytes,
        ),
    )

    await service.post_message(
        conversation_id,
        alice,
        MessageCreateSchema(
            kind=MessageKind.IMAGE,
            body="look at this",  # the caption lives in body
            attachment_ids=[attachment.id],
        ),
    )
```

`storage_key` is a key, **never a URL**: a URL is a capability with a
TTL, and storing one freezes an access into the row. An id that was
already claimed is refused with `404` — the same file never enters two
messages.

!!! tip "Validating a text upload"
    `UploadUtils(verify_magic_bytes=True)` rejects an unrecognized
    signature, and `.txt` / `.csv` **have none** — in a chat that
    refuses text attachments outright. Use
    `require_known_signature=False`: it rejects only when the signature
    **contradicts** the declared type. And for a checksum of the whole
    file pass `hasher=hashlib.sha256()`, because `content_validator`
    sees only the first chunk.

### Delete for everyone ≠ delete the row

```python
from uuid import UUID

from tempest_fastapi_sdk import UploadUtils
from tempest_fastapi_sdk.chat import ChatService


async def delete_for_everyone(
    service: ChatService,
    uploads: UploadUtils,
    message_id: UUID,
    alice: UUID,
) -> None:
    """Revoke the message and delete the files it orphaned."""
    tombstone, orphaned_keys = await service.revoke_message(message_id, alice)

    for key in orphaned_keys:
        await uploads.delete(key)

    assert tombstone.body == ""
```

The row survives (the thread keeps its shape, replies keep something to
quote) but `body` is **actually cleared** — not hidden behind a flag the
next query forgets to filter. The storage keys come back as the second
item of the tuple: the SDK does not own the bucket, and a forgotten key
is a file that outlives the message that justified it.

### Reactions: one per person

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService


async def react_twice(service: ChatService, message_id: UUID, bob: UUID) -> None:
    """React twice; the second one replaces the first."""
    await service.react(message_id, bob, "👍")
    updated = await service.react(message_id, bob, "🎉")

    print([(r.emoji, r.count) for r in updated.reactions])
```

```text
[('🎉', 1)]
```

### Preferences belong to your inbox

Pinning, archiving and muting live on the participant. Written on the
conversation they would silence it for everyone:

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService


async def tidy_inbox(
    service: ChatService,
    conversation_id: UUID,
    alice: UUID,
) -> None:
    """Pin and archive the conversation for one person only."""
    await service.set_preferences(conversation_id, alice, is_pinned=True)
    await service.set_preferences(conversation_id, alice, is_archived=True)
```

`list_conversations` sorts pinned first, then by newest message, and
hides archived threads unless `include_archived=True`.

### A real group

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import (
    ChatService,
    ConversationKind,
    ParticipantRole,
)


async def run_a_group(
    service: ChatService,
    alice: UUID,
    bob: UUID,
    carol: UUID,
    dave: UUID,
) -> None:
    """Create the group and change its membership."""
    group = await service.start_conversation(
        alice,
        [bob, carol],
        kind=ConversationKind.GROUP,
        title="Project X",
    )
    await service.add_participants(group.id, alice, [dave])  # history_from = now
    await service.set_role(group.id, alice, bob, ParticipantRole.ADMIN)
    await service.remove_participant(group.id, alice, dave)
    await service.leave(group.id, bob)
```

Someone who joins later does **not** inherit the backlog by default
(`history_from` is stamped on join); pass `share_history=True` to hand
it over. Someone who leaves keeps their row with `left_at` — otherwise
old messages lose the sender's name. The owner cannot be removed.

!!! tip "`kind` is inferred when you don't say"
    With no `kind`, up to two people is `DIRECT` and more is `GROUP` —
    which is what someone who never heard of the distinction means.
    Asking for `DIRECT` **explicitly** with any other headcount is
    refused with `422`.

### System messages

"Ana created the group", "Bruno left". Every membership change posts a
`kind=system` message with a structured payload:

```json
{"event": "participant_left", "actor_id": "…", "user_ids": ["…"]}
```

The client renders **from the payload** — so the text is localized and
the names become links. The `body` the server writes is the fallback for
push and search, never the primary representation.

### What a page costs

`list_messages` resolves the whole page in a **fixed** number of queries
— attachments, reactions and the quoted messages each come back in one
`IN`, not one per row. Measured on a 20-message page: resolving row by
row was 42 statements; batched it is 4, and stays 4 as the page grows.
Forwarding copies the attachment row (same `storage_key`), never the
bytes.

## The router

`make_chat_router` takes **how** to resolve the session, the service and
the authenticated user — like the SDK's other routers:

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.chat import make_chat_router

from src.api.dependencies.resources import db
from src.api.dependencies.services import build_chat_service

sessionmaker = db.get_session_context


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
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService
from tempest_fastapi_sdk.sse import SSEBroker

from src.db.models import ConversationModel, ConversationParticipantModel, MessageModel


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
`data` is the JSON of `MessageResponseSchema`:

```text
event: message
id: 7d3c5f8a-6b2e-4a19-b0d4-1c2e3f4a5b6c
data: {"id": "7d3c5f8a-...", "conversation_id": "1a2b3c4d-...", "sender_id": "7d3c5f8a-...", "body": "Let's!", "created_at": "2026-07-18T14:32:07Z"}
```

See the
**[Server-Sent Events »](sse.md)** recipe for the client side and the
multi-worker Redis bridge.

## Recap

- Inherit the five abstract tables and point the FKs at your
  `UserModel` — declaring the two constraints that change behaviour
  (`(sender_id, client_id)` and `(message_id, user_id)`).
- `ChatService` covers start, post, reply, edit, delete for everyone,
  react, forward, mark read and run the group; it returns schemas, not
  ORM rows.
- A receipt is a **watermark on the participant**; a reaction is **one
  per person**; revoking **clears the body** and hands you the storage
  keys to delete.
- `client_id` makes resending idempotent; a direct conversation is
  idempotent per pair.
- `make_chat_router` mounts the endpoints with the participant guard —
  which treats someone who **left** as not a participant.
- Pass an `SSEBroker` to get real-time delivery for free.
