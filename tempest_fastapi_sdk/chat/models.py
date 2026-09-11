"""Abstract chat tables — conversations, participants, messages, media.

Mirrors the SDK's other reusable tables (``BaseUserModel``,
``BaseWebPushSubscriptionModel``): the SDK ships the abstract rows, the
project ships the concrete tables so the foreign keys and
``__tablename__`` live in the application's metadata and Alembic emits
them under its naming convention.

Five tables model a messenger:

* :class:`BaseConversationModel` — one row per conversation/thread.
* :class:`BaseConversationParticipantModel` — join row: a user in a
  conversation, plus their read watermarks and their own preferences.
* :class:`BaseMessageModel` — one row per message.
* :class:`BaseMessageAttachmentModel` — one row per file a message
  carries.
* :class:`BaseMessageReactionModel` — one row per person per message.

Use the ``make_*`` factories for tests and light scripts; production
projects should hand-write the concrete classes so the FK columns are
editable and importable for refactors. The factories are where the
constraints that make the behaviour correct live — the unique
``(sender_id, client_id)`` that makes a retried send idempotent, the
unique ``(message_id, user_id)`` that makes reacting twice *replace*
rather than stack — so a hand-written class must declare them too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.chat.constants import (
    ConversationKind,
    MessageKind,
    ParticipantRole,
)
from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.db.enums import enum_column
from tempest_fastapi_sdk.db.model import BaseModel

JSON_COLUMN = JSON()
"""The JSON column type the abstract rows declare.

Plain ``JSON``, like every other JSON column in this SDK, rather than
``JSON().with_variant(JSONB(), "postgresql")`` — which is the better
PostgreSQL type and still the wrong default here. Measured: the variant
makes ``alembic revision --autogenerate`` emit

``sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")``

into a file that imports neither ``Text`` nor the dialect module, so the
generated migration fails lint and then ``NameError`` — in the
consumer's repo, for a column they never declared themselves.

A project that wants ``JSONB`` declares it on its own concrete table,
where the migration is generated next to an import it controls::

    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
"""


class BaseConversationModel(BaseModel):
    """Abstract conversation/thread row.

    ``title`` stays ``NULL`` for a direct thread on purpose: a direct
    conversation is labelled by *the other person*, which is a per-reader
    answer and therefore never a stored column.

    ``last_message_at`` is denormalized from the newest message so the
    conversation list orders and paginates without touching the messages
    table. Write it in the same transaction as the message that moves it.

    Attributes:
        kind (ConversationKind): Direct thread or group.
        title (str | None): Human label for a group. ``None`` for a
            direct thread.
        description (str | None): The group's subject text.
        avatar_path (str | None): Storage key of the group picture —
            a key, never a URL, because a URL is a capability with a TTL
            and storing one freezes an access into the row.
        created_by (UUID | None): The user who opened the conversation.
        last_message_at (datetime | None): Timestamp of the newest
            message, or ``None`` while the thread is empty.
        last_message_id (UUID | None): Id of that newest message. Not an
            FK, so deleting a message never has to update it first.
    """

    __abstract__ = True

    kind: Mapped[ConversationKind] = enum_column(
        ConversationKind,
        default=ConversationKind.DIRECT,
        nullable=False,
        index=True,
        doc="Whether this is a direct thread or a group.",
    )
    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        doc="Optional conversation title (group name), or NULL.",
    )
    description: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        default=None,
        doc="The group's subject text, or NULL.",
    )
    avatar_path: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        default=None,
        doc="Storage key of the group picture, or NULL.",
    )
    created_by: Mapped[UUID | None] = mapped_column(
        nullable=True,
        default=None,
        index=True,
        doc="The user who opened the conversation (set by subclass FK).",
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        index=True,
        doc="Timestamp of the newest message, denormalized for ordering.",
    )
    last_message_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        default=None,
        doc="Id of the newest message. Not an FK, to keep deletes cheap.",
    )


class BaseConversationParticipantModel(BaseModel):
    """Abstract join row: one user's membership in one conversation.

    Read state is a **watermark**, not a row per message per reader. A
    message counts as read by this participant when
    ``last_read_at >= message.created_at``, so a group of 200 people
    costs 200 rows in total rather than 200 rows per message, and marking
    a thread read is one ``UPDATE`` instead of a bulk insert. The cost is
    that "read" is per-instant rather than per-message — which is exactly
    the granularity a messenger UI shows.

    The row survives leaving (``left_at`` set, not deleted), because old
    messages still have to resolve a sender name.

    Preferences live here and not on the conversation: pinning, archiving
    and muting are decisions about *your* inbox. Written on the
    conversation they would silence the thread for everyone.

    Attributes:
        conversation_id (UUID): FK to the conversation (set by subclass).
        user_id (UUID): FK to the participant user (set by subclass).
        role (ParticipantRole): Member, admin or owner.
        joined_at (datetime | None): When the membership started.
        left_at (datetime | None): When it ended, or ``None`` while
            active.
        history_from (datetime | None): Earliest message this participant
            may read. ``None`` means the whole history — set it when a
            member joins a group that should not hand over the backlog.
        muted_until (datetime | None): Notifications suppressed until
            this instant. ``None`` means not muted.
        is_pinned (bool): Pinned to the top of this user's list.
        is_archived (bool): Archived for this user.
        last_read_at (datetime | None): Read watermark.
        last_read_message_id (UUID | None): The message that moved the
            watermark, kept for the "unread from here" divider.
        last_delivered_at (datetime | None): Delivery watermark, moved
            when a live connection acknowledges a message.
    """

    __abstract__ = True

    conversation_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the conversation (set by subclass).",
    )
    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the participant user (set by subclass).",
    )
    role: Mapped[ParticipantRole] = enum_column(
        ParticipantRole,
        default=ParticipantRole.MEMBER,
        nullable=False,
        doc="What this participant may do in the conversation.",
    )
    joined_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="When the membership started.",
    )
    left_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="When the membership ended, or NULL while active.",
    )
    history_from: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="Earliest message this participant may read; NULL = all.",
    )
    muted_until: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="Notifications suppressed until this instant, or NULL.",
    )
    is_pinned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether this user pinned the thread.",
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether this user archived the thread.",
    )
    last_read_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="Read watermark: messages at or before this are read.",
    )
    last_read_message_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        default=None,
        doc="The message that moved the read watermark.",
    )
    last_delivered_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="Delivery watermark.",
    )


class BaseMessageModel(BaseModel):
    """Abstract message row posted to a conversation.

    ``body`` holds the text of a ``TEXT`` message and the caption of
    every media kind, so a caption never needs a second column.

    Deleting is two-valued on purpose. ``revoked_at`` is *delete for
    everyone*: the row survives so the thread keeps its shape and replies
    still have something to quote, while ``body`` is cleared — actually
    cleared, not hidden behind a flag the next query forgets to filter —
    and the attachments leave storage.

    ``client_id`` is what makes a retried send idempotent. A ``POST``
    whose ``201`` is lost leaves the client unable to tell whether the
    message landed: resending blindly posts twice, not resending loses
    it. With an id the client generates before the request, unique per
    sender, the retry returns the row that was already written. It is the
    same problem the SDK's ``IdempotencyMiddleware`` solves for HTTP in
    general, but the key here is a domain value — it outlives the request
    and belongs to the message.

    Attributes:
        conversation_id (UUID): FK to the owning conversation (set by
            subclass).
        sender_id (UUID): FK to the sending user (set by subclass).
            Non-null even for a ``SYSTEM`` notice: a system message
            always has an actor, and making the column nullable for the
            one kind that always has a value costs every reader a check.
        kind (MessageKind): What the message carries.
        body (str): Text, or the caption of a media message.
        client_id (str | None): Sender-generated id; unique per sender.
        reply_to_id (UUID | None): The message being replied to.
        forwarded_from_id (UUID | None): The original of a forward.
        forward_score (int): How many hops this content has travelled.
        edited_at (datetime | None): When the body was last edited.
        revoked_at (datetime | None): When it was deleted for everyone.
        payload (dict[str, Any] | None): Kind-specific structured data —
            a ``LOCATION``'s coordinates, a ``CONTACT``'s card, a
            ``SYSTEM`` notice's event. One document rather than a column
            per kind, so a new kind is a client change and not a
            migration.
    """

    __abstract__ = True

    conversation_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the owning conversation (set by subclass).",
    )
    sender_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the sending user (set by subclass).",
    )
    kind: Mapped[MessageKind] = enum_column(
        MessageKind,
        default=MessageKind.TEXT,
        nullable=False,
        doc="What the message carries.",
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        doc="The message text, or the caption of a media message.",
    )
    client_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        doc="Sender-generated id making a retried send idempotent.",
    )
    reply_to_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        default=None,
        index=True,
        doc="FK to the message being replied to (set by subclass).",
    )
    forwarded_from_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        default=None,
        doc="FK to the original of a forward (set by subclass).",
    )
    forward_score: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        doc="How many hops this content has been forwarded.",
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="When the body was last edited, or NULL.",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="When the message was deleted for everyone, or NULL.",
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_COLUMN,
        nullable=True,
        default=None,
        doc="Kind-specific structured data (location, contact, system event).",
    )


class BaseMessageAttachmentModel(BaseModel):
    """Abstract row for one file carried by a message.

    A separate table rather than columns on the message, because an album
    posts several images under one message and because dimensions,
    duration and waveform mean nothing for text.

    ``message_id`` is **nullable**, and that is the whole point of the
    two-step upload: the row is written when the bytes land, and the
    message that claims it may not exist for another minute — or ever, if
    the sender gives up. Declaring it ``NOT NULL`` makes every upload
    fail with an integrity error.

    ``storage_key`` is a storage key, never a URL: a URL is minted per
    read with a short TTL, so storing one freezes a capability into the
    row.

    Attributes:
        message_id (UUID | None): FK to the owning message, or ``None``
            while the file is uploaded but not yet posted.
        position (int): Order within the message, 0-based.
        storage_key (str): Storage key of the stored file.
        thumbnail_key (str | None): Key of the generated preview.
        filename (str): Original filename, shown for documents.
        mime_type (str): Type sniffed from the bytes, not the client's
            claim.
        size_bytes (int): Size of the stored object.
        width (int | None): Pixel width, for image and video.
        height (int | None): Pixel height, for image and video.
        duration_ms (int | None): Duration, for audio, voice and video.
        waveform (list[int] | None): Normalized amplitude buckets (0-100)
            for a voice note. Supplied by the recorder, because the
            reader has to draw the bar before fetching the audio.
    """

    __abstract__ = True

    message_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        default=None,
        index=True,
        doc="FK to the owning message, or NULL while unclaimed.",
    )
    position: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        doc="Order within the message, 0-based.",
    )
    storage_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        doc="Storage key of the stored file.",
    )
    thumbnail_key: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        default=None,
        doc="Storage key of the generated preview, or NULL.",
    )
    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
        doc="Original filename, shown for documents.",
    )
    mime_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="application/octet-stream",
        doc="Content type sniffed from the bytes, not the client's claim.",
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Size of the stored object, in bytes.",
    )
    width: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        doc="Pixel width, or NULL.",
    )
    height: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        doc="Pixel height, or NULL.",
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        doc="Duration in milliseconds, or NULL.",
    )
    waveform: Mapped[list[int] | None] = mapped_column(
        JSON_COLUMN,
        nullable=True,
        default=None,
        doc="Normalized amplitude buckets (0-100) for a voice note.",
    )


class BaseMessageReactionModel(BaseModel):
    """Abstract row for one person's reaction to one message.

    The uniqueness that matters is ``(message_id, user_id)``, **not**
    ``(message_id, user_id, emoji)``: one reaction per person, and
    reacting again replaces it. The wider constraint is exactly what
    turns a double-tap into two reactions.

    Attributes:
        message_id (UUID): FK to the reacted message (set by subclass).
        user_id (UUID): FK to the reacting user (set by subclass).
        emoji (str): The emoji itself, stored as text.
    """

    __abstract__ = True

    message_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the reacted message (set by subclass).",
    )
    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the reacting user (set by subclass).",
    )
    emoji: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        doc="The emoji, stored as text.",
    )


def make_conversation_model(
    *,
    user_table: str | None = None,
    tablename: str = "conversations",
    class_name: str = "ConversationModel",
) -> type[BaseConversationModel]:
    """Build a concrete ``ConversationModel`` subclass at runtime.

    Args:
        user_table (str | None): Table the ``created_by`` FK references.
            ``None`` (the default) leaves ``created_by`` as a plain
            column with no foreign key — a default of ``"users"`` would
            make this factory fail for every caller whose user table is
            named something else, or who has none in the same metadata.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name (affects repr / Alembic ids).

    Returns:
        type[BaseConversationModel]: A concrete mapped class.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "__table_args__": (
            Index(
                f"ix_{tablename}_kind_last_message_at",
                "kind",
                "last_message_at",
            ),
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    if user_table is not None:
        attrs["created_by"] = mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="SET NULL"),
            nullable=True,
            default=None,
            index=True,
        )
    return type(class_name, (BaseConversationModel,), attrs)


def make_conversation_participant_model(
    *,
    conversation_table: str = "conversations",
    user_table: str = "users",
    tablename: str = "conversation_participants",
    class_name: str = "ConversationParticipantModel",
) -> type[BaseConversationParticipantModel]:
    """Build a concrete participant join model at runtime.

    Args:
        conversation_table (str): Table name of the concrete conversation
            model the FK references.
        user_table (str): Table name of the concrete user model the FK
            references.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.

    Returns:
        type[BaseConversationParticipantModel]: A concrete mapped class
        with a unique ``(conversation_id, user_id)`` constraint.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "conversation_id": mapped_column(
            ForeignKey(f"{conversation_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "__table_args__": (
            UniqueConstraint("conversation_id", "user_id", name="uq_participant"),
            Index(f"ix_{tablename}_user_archived", "user_id", "is_archived"),
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseConversationParticipantModel,), attrs)


def make_message_model(
    *,
    conversation_table: str = "conversations",
    user_table: str = "users",
    tablename: str = "messages",
    class_name: str = "MessageModel",
) -> type[BaseMessageModel]:
    """Build a concrete ``MessageModel`` subclass at runtime.

    The generated class carries the two constraints the behaviour
    depends on: ``(sender_id, client_id)`` unique, which is what makes a
    retried send return the existing row instead of posting twice, and a
    ``(conversation_id, created_at)`` index, which is the ordering every
    history page reads.

    Args:
        conversation_table (str): Table name of the concrete conversation
            model the FK references.
        user_table (str): Table name of the concrete user model the sender
            FK references.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.

    Returns:
        type[BaseMessageModel]: A concrete mapped class.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "conversation_id": mapped_column(
            ForeignKey(f"{conversation_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "sender_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "reply_to_id": mapped_column(
            ForeignKey(f"{tablename}.id", ondelete="SET NULL"),
            nullable=True,
            default=None,
            index=True,
        ),
        "forwarded_from_id": mapped_column(
            ForeignKey(f"{tablename}.id", ondelete="SET NULL"),
            nullable=True,
            default=None,
        ),
        "__table_args__": (
            UniqueConstraint(
                "sender_id",
                "client_id",
                name=f"uq_{tablename}_sender_client",
            ),
            Index(
                f"ix_{tablename}_conversation_created",
                "conversation_id",
                "created_at",
            ),
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseMessageModel,), attrs)


def make_message_attachment_model(
    *,
    message_table: str = "messages",
    tablename: str = "message_attachments",
    class_name: str = "MessageAttachmentModel",
) -> type[BaseMessageAttachmentModel]:
    """Build a concrete attachment model at runtime.

    Args:
        message_table (str): Table name of the concrete message model.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.

    Returns:
        type[BaseMessageAttachmentModel]: A concrete mapped class with a
        unique ``(message_id, position)`` constraint.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "message_id": mapped_column(
            ForeignKey(f"{message_table}.id", ondelete="CASCADE"),
            nullable=True,
            default=None,
            index=True,
        ),
        "__table_args__": (
            UniqueConstraint(
                "message_id",
                "position",
                name=f"uq_{tablename}_position",
            ),
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseMessageAttachmentModel,), attrs)


def make_message_reaction_model(
    *,
    message_table: str = "messages",
    user_table: str = "users",
    tablename: str = "message_reactions",
    class_name: str = "MessageReactionModel",
) -> type[BaseMessageReactionModel]:
    """Build a concrete reaction model at runtime.

    Args:
        message_table (str): Table name of the concrete message model.
        user_table (str): Table name of the concrete user model.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.

    Returns:
        type[BaseMessageReactionModel]: A concrete mapped class with a
        unique ``(message_id, user_id)`` constraint — one reaction per
        person, replaced rather than stacked.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "message_id": mapped_column(
            ForeignKey(f"{message_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "__table_args__": (
            UniqueConstraint(
                "message_id",
                "user_id",
                name=f"uq_{tablename}_per_user",
            ),
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseMessageReactionModel,), attrs)


__all__: list[str] = [
    "BaseConversationModel",
    "BaseConversationParticipantModel",
    "BaseMessageAttachmentModel",
    "BaseMessageModel",
    "BaseMessageReactionModel",
    "make_conversation_model",
    "make_conversation_participant_model",
    "make_message_attachment_model",
    "make_message_model",
    "make_message_reaction_model",
]
