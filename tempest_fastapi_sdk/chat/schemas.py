"""Pydantic DTOs for the chat module."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from tempest_fastapi_sdk.chat.constants import (
    ConversationKind,
    MessageKind,
    ParticipantRole,
)
from tempest_fastapi_sdk.schemas.base import BaseSchema

REPLY_EXCERPT_LENGTH: int = 120
"""How much of a quoted message the reply stub carries.

The quote has to render without a second round-trip, and re-sending the
whole parent — attachments and reactions included — under every reply
multiplies the payload of a history page by the number of replies in it.
"""


class ReplyPreviewSchema(BaseSchema):
    """The quoted parent, small enough to inline under every reply.

    ``revoked`` is the field an obvious implementation forgets: deleting
    the quoted message has to delete the quote too, or the text leaks
    back through whoever replied to it.

    Attributes:
        id (UUID): The quoted message's id.
        sender_id (UUID): Who wrote it.
        kind (MessageKind): What it carried, so the stub can show
            "Photo" instead of an empty line.
        excerpt (str): The first :data:`REPLY_EXCERPT_LENGTH` characters
            of its body — empty when revoked.
        revoked (bool): Whether the quoted message was deleted for
            everyone. Render the tombstone, never the excerpt.
    """

    id: UUID
    sender_id: UUID
    kind: MessageKind = MessageKind.TEXT
    excerpt: str = ""
    revoked: bool = False


class AttachmentResponseSchema(BaseSchema):
    """One file carried by a message.

    Attributes:
        id (UUID): The attachment row id.
        position (int): Order within the message, 0-based.
        storage_key (str): Storage key — mint a URL per read; never
            store one.
        thumbnail_key (str | None): Key of the generated preview.
        filename (str): Original filename.
        mime_type (str): Type sniffed from the bytes.
        size_bytes (int): Size of the stored object.
        width (int | None): Pixel width.
        height (int | None): Pixel height.
        duration_ms (int | None): Duration in milliseconds.
        waveform (list[int]): Amplitude buckets for a voice note.
    """

    id: UUID
    position: int = 0
    storage_key: str
    thumbnail_key: str | None = None
    filename: str = ""
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    waveform: list[int] = Field(default_factory=list)

    @field_validator("waveform", mode="before")
    @classmethod
    def _empty_waveform(cls, value: list[int] | None) -> list[int]:
        """Read a ``NULL`` column as the empty list.

        The column is nullable — most attachments are not voice notes —
        while the field is a collection, and an empty collection is what
        "no waveform" means on the wire. Without this the schema rejects
        every non-voice attachment it is handed straight from the ORM.

        Args:
            value (list[int] | None): The stored value.

        Returns:
            list[int]: The buckets, or ``[]``.
        """
        return value if value is not None else []


class ReactionSummarySchema(BaseSchema):
    """One emoji and who reacted with it.

    Attributes:
        emoji (str): The emoji.
        count (int): How many people chose it.
        user_ids (list[UUID]): Who they are, so a client can render
            "you and 3 others" without another request.
    """

    emoji: str
    count: int = 0
    user_ids: list[UUID] = Field(default_factory=list)


class ReceiptCountsSchema(BaseSchema):
    """How far a message has travelled, derived from the watermarks.

    Counts come from comparing each participant's ``last_read_at`` /
    ``last_delivered_at`` against the message's ``created_at`` — there is
    no row per message per reader to count.

    Attributes:
        delivered (int): Participants whose delivery watermark has
            passed the message.
        read (int): Participants whose read watermark has passed it.
        total (int): Participants other than the sender.
    """

    delivered: int = 0
    read: int = 0
    total: int = 0


class MessageCreateSchema(BaseSchema):
    """Payload to post a message.

    Attributes:
        body (str): The text, or the caption of a media message. Empty
            is valid for media; a ``TEXT`` message with no body and no
            attachments is rejected by the service.
        kind (MessageKind): What the message carries.
        client_id (str | None): An id the client generates **before**
            sending. Retrying with the same one returns the message that
            was already stored instead of posting a second copy.
        reply_to_id (UUID | None): The message being replied to.
        attachment_ids (list[UUID]): Files already uploaded and not yet
            claimed by a message, in render order.
        payload (dict[str, Any] | None): Structured data for a
            ``LOCATION`` or ``CONTACT``.
    """

    body: str = Field(
        default="",
        title="Body",
        description="The message text, or the caption of a media message.",
    )
    kind: MessageKind = Field(
        default=MessageKind.TEXT,
        title="Kind",
        description="What the message carries.",
    )
    client_id: str | None = Field(
        default=None,
        max_length=64,
        title="Client id",
        description="Client-generated id that makes a retried send idempotent.",
    )
    reply_to_id: UUID | None = Field(
        default=None,
        title="Reply to",
        description="The message being replied to.",
    )
    attachment_ids: list[UUID] = Field(
        default_factory=list,
        title="Attachment ids",
        description="Uploaded, unclaimed attachments to attach, in order.",
    )
    payload: dict[str, Any] | None = Field(
        default=None,
        title="Payload",
        description="Structured data for a location or contact message.",
    )


class MessageEditSchema(BaseSchema):
    """Payload to edit a message's body.

    Attributes:
        body (str): The new text.
    """

    body: str = Field(
        title="Body",
        description="The new message text.",
    )


class ReactionCreateSchema(BaseSchema):
    """Payload to react to a message.

    Attributes:
        emoji (str): The emoji. Reacting again with a different one
            replaces the previous reaction rather than adding to it.
    """

    emoji: str = Field(
        max_length=16,
        title="Emoji",
        description="The emoji to react with.",
    )


class MarkReadSchema(BaseSchema):
    """Payload to move a participant's read watermark.

    Attributes:
        message_id (UUID | None): Read up to and including this message.
            ``None`` marks the whole conversation read as of now.
    """

    message_id: UUID | None = Field(
        default=None,
        title="Message id",
        description="Read up to this message; null marks everything read.",
    )


class ForwardSchema(BaseSchema):
    """Payload to forward a message into other conversations.

    Attributes:
        conversation_ids (list[UUID]): Where to forward it.
    """

    conversation_ids: list[UUID] = Field(
        default_factory=list,
        title="Conversation ids",
        description="Conversations to forward the message into.",
    )


class MessageResponseSchema(BaseSchema):
    """A message as returned to clients.

    Attributes:
        id (UUID): The message id.
        conversation_id (UUID): The owning conversation.
        sender_id (UUID): Who sent it, or the actor of a system notice.
        kind (MessageKind): What it carries.
        body (str): Text or caption; empty once revoked.
        client_id (str | None): The sender's idempotency id, echoed so a
            client can match it against its optimistic row.
        reply_to (ReplyPreviewSchema | None): The quoted parent.
        forwarded_from_id (UUID | None): The original of a forward.
        forward_score (int): How many hops the content has travelled.
        edited_at (datetime | None): When the body was last edited.
        revoked_at (datetime | None): When it was deleted for everyone.
        payload (dict[str, Any] | None): Kind-specific structured data.
        attachments (list[AttachmentResponseSchema]): Files it carries.
        reactions (list[ReactionSummarySchema]): Reactions, grouped by
            emoji.
        receipts (ReceiptCountsSchema | None): Delivery/read counts, when
            the caller asked for them.
        created_at (datetime): When it was posted.
    """

    id: UUID
    conversation_id: UUID
    sender_id: UUID
    kind: MessageKind = MessageKind.TEXT
    body: str = ""
    client_id: str | None = None
    reply_to: ReplyPreviewSchema | None = None
    forwarded_from_id: UUID | None = None
    forward_score: int = 0
    edited_at: datetime | None = None
    revoked_at: datetime | None = None
    payload: dict[str, Any] | None = None
    attachments: list[AttachmentResponseSchema] = Field(default_factory=list)
    reactions: list[ReactionSummarySchema] = Field(default_factory=list)
    receipts: ReceiptCountsSchema | None = None
    created_at: datetime


class ParticipantResponseSchema(BaseSchema):
    """One membership row as returned to clients.

    Attributes:
        user_id (UUID): The participant.
        role (ParticipantRole): What they may do.
        joined_at (datetime | None): When they joined.
        left_at (datetime | None): When they left, or ``None``.
        is_pinned (bool): Whether *this* user pinned the thread.
        is_archived (bool): Whether they archived it.
        muted_until (datetime | None): Mute expiry, or ``None``.
        last_read_at (datetime | None): Their read watermark.
    """

    user_id: UUID
    role: ParticipantRole = ParticipantRole.MEMBER
    joined_at: datetime | None = None
    left_at: datetime | None = None
    is_pinned: bool = False
    is_archived: bool = False
    muted_until: datetime | None = None
    last_read_at: datetime | None = None


class ConversationCreateSchema(BaseSchema):
    """Payload to start a conversation.

    Attributes:
        participant_ids (list[UUID]): The users to add. The
            authenticated creator is added automatically, so this lists
            only the other members.
        kind (ConversationKind | None): Direct thread or group. ``None``
            infers it from the headcount. A ``DIRECT`` request for a
            pair that already has one returns the existing conversation
            instead of a second.
        title (str | None): Group title.
        description (str | None): Group subject text.
    """

    participant_ids: list[UUID] = Field(
        default_factory=list,
        title="Participant ids",
        description="Users to add to the conversation.",
    )
    kind: ConversationKind | None = Field(
        default=None,
        title="Kind",
        description=(
            "Direct thread or group. Null infers it from the headcount: "
            "two people is a direct thread, more is a group."
        ),
    )
    title: str | None = Field(
        default=None,
        title="Title",
        description="Group title.",
    )
    description: str | None = Field(
        default=None,
        title="Description",
        description="Group subject text.",
    )


class ConversationUpdateSchema(BaseSchema):
    """Payload to edit a group's data.

    Attributes:
        title (str | None): New title, when given.
        description (str | None): New description, when given.
    """

    title: str | None = Field(default=None, title="Title")
    description: str | None = Field(default=None, title="Description")


class ParticipantPreferencesSchema(BaseSchema):
    """Payload to change *your own* view of a conversation.

    Pinning, archiving and muting are decisions about your inbox, not
    about the thread — written on the conversation they would apply to
    everyone in it.

    Attributes:
        is_pinned (bool | None): Pin or unpin, when given.
        is_archived (bool | None): Archive or unarchive, when given.
        muted_until (datetime | None): Mute until this instant.
    """

    is_pinned: bool | None = Field(default=None, title="Pinned")
    is_archived: bool | None = Field(default=None, title="Archived")
    muted_until: datetime | None = Field(default=None, title="Muted until")


class ConversationResponseSchema(BaseSchema):
    """A conversation as returned to clients.

    Attributes:
        id (UUID): The conversation id.
        kind (ConversationKind): Direct thread or group.
        title (str | None): The title, if any.
        description (str | None): The subject text, if any.
        avatar_path (str | None): Storage key of the picture.
        created_by (UUID | None): Who opened it.
        created_at (datetime): When it was created.
        last_message_at (datetime | None): When the newest message
            landed.
        participants (list[ParticipantResponseSchema]): The membership
            rows, when the caller asked for them.
        unread_count (int | None): Messages after this caller's read
            watermark, when the caller asked for it.
    """

    id: UUID
    kind: ConversationKind = ConversationKind.DIRECT
    title: str | None = None
    description: str | None = None
    avatar_path: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    last_message_at: datetime | None = None
    participants: list[ParticipantResponseSchema] = Field(default_factory=list)
    unread_count: int | None = None


__all__: list[str] = [
    "REPLY_EXCERPT_LENGTH",
    "AttachmentResponseSchema",
    "ConversationCreateSchema",
    "ConversationResponseSchema",
    "ConversationUpdateSchema",
    "ForwardSchema",
    "MarkReadSchema",
    "MessageCreateSchema",
    "MessageEditSchema",
    "MessageResponseSchema",
    "ParticipantPreferencesSchema",
    "ParticipantResponseSchema",
    "ReactionCreateSchema",
    "ReactionSummarySchema",
    "ReceiptCountsSchema",
    "ReplyPreviewSchema",
]
