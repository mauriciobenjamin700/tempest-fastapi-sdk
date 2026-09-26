"""``uploader_id`` is a schema change the consumer has to migrate (#317).

The column lives on the abstract ``BaseMessageAttachmentModel``, so every
concrete attachment table inherits it and every ``SELECT`` names it. These
tests pin both halves of what the recipe and the CHANGELOG say: an
attachment table created before the column fails on the first read, and the
migration the recipe prints is enough to bring it back — with the rows that
were already there staying claimable, which is the transition window.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import BaseModel, BaseRepository, BaseUserModel
from tempest_fastapi_sdk.chat import (
    ChatService,
    MessageCreateSchema,
    MessageKind,
    make_conversation_model,
    make_conversation_participant_model,
    make_message_attachment_model,
    make_message_model,
)
from tempest_fastapi_sdk.exceptions import NotFoundException


class _MigrationUser(BaseUserModel):
    __tablename__ = "migration_users"


_Conversation = make_conversation_model(
    tablename="migration_conversations",
    class_name="_MigrationConversation",
)
_Participant = make_conversation_participant_model(
    conversation_table="migration_conversations",
    user_table="migration_users",
    tablename="migration_participants",
    class_name="_MigrationParticipant",
)
_Message = make_message_model(
    conversation_table="migration_conversations",
    user_table="migration_users",
    tablename="migration_messages",
    class_name="_MigrationMessage",
)
_Attachment = make_message_attachment_model(
    message_table="migration_messages",
    tablename="migration_attachments",
    class_name="_MigrationAttachment",
)

TABLE: str = "migration_attachments"
"""The attachment table the tests migrate."""


def _create_old_schema(connection: Connection) -> None:
    """Create the chat tables, with the attachment table as before #317.

    Args:
        connection (Connection): The synchronous connection.
    """
    tables: list[sa.Table] = [
        model.__table__
        for model in (
            _MigrationUser,
            _Conversation,
            _Participant,
            _Message,
            _Attachment,
        )
    ]
    BaseModel.metadata.create_all(connection, tables=tables)
    connection.execute(text(f"DROP INDEX ix_{TABLE}_uploader_id"))
    connection.execute(text(f"ALTER TABLE {TABLE} DROP COLUMN uploader_id"))


def _upgrade(connection: Connection) -> None:
    """Run the ``upgrade()`` body the chat recipe prints.

    Args:
        connection (Connection): The synchronous connection.
    """
    op = Operations(MigrationContext.configure(connection))
    op.add_column(TABLE, sa.Column("uploader_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f(f"ix_{TABLE}_uploader_id"), TABLE, ["uploader_id"], unique=False
    )


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Yield an in-memory engine holding the pre-#317 schema.

    Yields:
        AsyncEngine: The engine.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(_create_old_schema)
    yield engine
    await engine.dispose()


def _service(session: AsyncSession) -> ChatService:
    """Build a service over the migration tables.

    Args:
        session (AsyncSession): The session.

    Returns:
        ChatService: The service under test.
    """
    return ChatService(
        conversations=BaseRepository(session, model=_Conversation),
        participants=BaseRepository(session, model=_Participant),
        messages=BaseRepository(session, model=_Message),
        attachments=BaseRepository(session, model=_Attachment),
    )


class TestOldSchema:
    """What a consumer who upgrades without migrating runs into."""

    async def test_even_a_text_message_fails_on_the_attachment_read(
        self,
        engine: AsyncEngine,
    ) -> None:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            service = _service(session)
            conversation = await service.start_conversation(uuid4(), [uuid4()])

            with pytest.raises(
                OperationalError, match=f"no such column: {TABLE}.uploader_id"
            ):
                await service.post_message(conversation.id, uuid4(), "oi")


class TestDocumentedMigration:
    """The recipe's ``upgrade()`` is all the schema needs."""

    async def test_legacy_rows_survive_and_new_rows_are_owned(
        self,
        engine: AsyncEngine,
    ) -> None:
        legacy_id = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f"INSERT INTO {TABLE} (id, position, storage_key, filename, "
                    "mime_type, size_bytes, is_active, created_at, updated_at) "
                    "VALUES (:id, 0, 'legacy.jpg', '', 'image/jpeg', 1, 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                ),
                {"id": legacy_id.hex},
            )
            await connection.run_sync(_upgrade)

        ana, bruno = uuid4(), uuid4()
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            service = _service(session)
            conversation = await service.start_conversation(ana, [bruno])
            owned = await service.add_attachment(ana, storage_key="ana.jpg")

            legacy: Any = await service.post_message(
                conversation.id,
                bruno,
                MessageCreateSchema(kind=MessageKind.IMAGE, attachment_ids=[legacy_id]),
            )
            with pytest.raises(NotFoundException):
                await service.post_message(
                    conversation.id,
                    bruno,
                    MessageCreateSchema(
                        kind=MessageKind.IMAGE,
                        attachment_ids=[owned.id],
                    ),
                )

        assert [a.storage_key for a in legacy.attachments] == ["legacy.jpg"]
