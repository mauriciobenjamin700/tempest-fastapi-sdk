"""Abstract chat tables — conversations, participants and messages.

Mirrors the SDK's other reusable tables (``BaseUserModel``,
``BaseWebPushSubscriptionModel``): the SDK ships the abstract rows, the
project ships the concrete tables so the foreign keys and
``__tablename__`` live in the application's metadata and Alembic emits
them under its naming convention.

Three tables model a threaded chat:

* :class:`BaseConversationModel` — one row per conversation/thread.
* :class:`BaseConversationParticipantModel` — join row: a user in a
  conversation.
* :class:`BaseMessageModel` — one row per message posted to a
  conversation.

Use the ``make_*`` factories for tests and light scripts; production
projects should hand-write the concrete classes so the FK columns are
editable and importable for refactors.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseConversationModel(BaseModel):
    """Abstract conversation/thread row.

    Attributes:
        title (str | None): Optional human label for the conversation
            (e.g. a group name). ``None`` for a plain direct thread.
    """

    __abstract__ = True

    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        doc="Optional conversation title (group name), or NULL.",
    )


class BaseConversationParticipantModel(BaseModel):
    """Abstract join row: one user's membership in one conversation.

    Concrete subclasses add the two foreign keys (to the conversation
    table and the user table). A ``(conversation_id, user_id)`` pair is
    unique — a user joins a conversation once.

    Attributes:
        conversation_id (UUID): FK to the conversation (set by subclass).
        user_id (UUID): FK to the participant user (set by subclass).
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


class BaseMessageModel(BaseModel):
    """Abstract message row posted to a conversation.

    Attributes:
        conversation_id (UUID): FK to the owning conversation (set by
            subclass).
        sender_id (UUID): FK to the user who sent the message (set by
            subclass).
        body (str): The message text.
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
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="The message text.",
    )


def make_conversation_model(
    *,
    tablename: str = "conversations",
    class_name: str = "ConversationModel",
) -> type[BaseConversationModel]:
    """Build a concrete ``ConversationModel`` subclass at runtime.

    Args:
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name (affects repr / Alembic ids).

    Returns:
        type[BaseConversationModel]: A concrete mapped class.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "__module__": __name__,
        "__qualname__": class_name,
    }
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
    from sqlalchemy import UniqueConstraint

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
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseMessageModel,), attrs)


__all__: list[str] = [
    "BaseConversationModel",
    "BaseConversationParticipantModel",
    "BaseMessageModel",
    "make_conversation_model",
    "make_conversation_participant_model",
    "make_message_model",
]
