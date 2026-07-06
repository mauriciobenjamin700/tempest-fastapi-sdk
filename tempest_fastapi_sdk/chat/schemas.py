"""Pydantic DTOs for the chat module."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils.fields import NonEmptyStrField


class ConversationCreateSchema(BaseSchema):
    """Payload to start a conversation.

    Attributes:
        participant_ids (list[UUID]): The users to add as participants.
            The authenticated creator is added automatically by the
            router, so this may list only the other members.
        title (str | None): Optional conversation title.
    """

    participant_ids: list[UUID] = Field(
        default_factory=list,
        title="Participant ids",
        description="Users to add to the conversation.",
    )
    title: str | None = Field(
        default=None,
        title="Title",
        description="Optional conversation title.",
    )


class ConversationResponseSchema(BaseSchema):
    """A conversation as returned to clients.

    Attributes:
        id (UUID): The conversation id.
        title (str | None): The conversation title, if any.
        created_at (datetime): When the conversation was created.
    """

    id: UUID
    title: str | None = None
    created_at: datetime


class MessageCreateSchema(BaseSchema):
    """Payload to post a message.

    Attributes:
        body (str): The message text (non-empty).
    """

    body: NonEmptyStrField = Field(
        title="Body",
        description="The message text.",
    )


class MessageResponseSchema(BaseSchema):
    """A message as returned to clients.

    Attributes:
        id (UUID): The message id.
        conversation_id (UUID): The owning conversation.
        sender_id (UUID): The user who sent it.
        body (str): The message text.
        created_at (datetime): When the message was posted.
    """

    id: UUID
    conversation_id: UUID
    sender_id: UUID
    body: str
    created_at: datetime


__all__: list[str] = [
    "ConversationCreateSchema",
    "ConversationResponseSchema",
    "MessageCreateSchema",
    "MessageResponseSchema",
]
