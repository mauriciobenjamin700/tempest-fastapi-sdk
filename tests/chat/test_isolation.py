"""Isolation inside a group: the newcomer's backlog and the owner's seat.

Two guarantees the chat service states and enforces only on the primary
path. A member added without ``share_history`` must not read a pre-join
message through a derived copy (the excerpt a reply carries), and an admin
must not be able to demote, replace and then remove the owner.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, BaseUserModel
from tempest_fastapi_sdk.chat import (
    ChatService,
    MessageCreateSchema,
    ParticipantRoleEnum,
    make_conversation_model,
    make_conversation_participant_model,
    make_message_model,
)
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tests._seed import seed_users


class _User(BaseUserModel):
    __tablename__ = "isolation_chat_users"


_Conversation = make_conversation_model(
    tablename="isolation_chat_conversations",
    class_name="_IsolationConversation",
)
_Participant = make_conversation_participant_model(
    conversation_table="isolation_chat_conversations",
    user_table="isolation_chat_users",
    tablename="isolation_chat_participants",
    class_name="_IsolationParticipant",
)
_Message = make_message_model(
    conversation_table="isolation_chat_conversations",
    user_table="isolation_chat_users",
    tablename="isolation_chat_messages",
    class_name="_IsolationMessage",
)


@pytest.fixture
async def service(session: AsyncSession) -> ChatService:
    """Build a chat service on the suite's session.

    Args:
        session (AsyncSession): The suite's session.

    Returns:
        ChatService: The service under test.
    """
    return ChatService(
        conversations=BaseRepository(session, model=_Conversation),
        participants=BaseRepository(session, model=_Participant),
        messages=BaseRepository(session, model=_Message),
    )


@pytest.fixture
async def users(session: AsyncSession) -> list[UUID]:
    """Seed five users.

    Args:
        session (AsyncSession): The suite's session.

    Returns:
        list[UUID]: Their ids.
    """
    return await seed_users(session, _User, 5)


class TestReplyExcerptRespectsHistoryFrom:
    """A reply by an older member must not quote the backlog to a newcomer."""

    async def test_newcomer_page_blanks_pre_join_excerpt(
        self, service: ChatService, users: list[UUID]
    ) -> None:
        """Before the fix the excerpt held the pre-join body."""
        owner, older, other, newcomer, _ = users
        conversation = await service.start_conversation(owner, [older, other])
        pre_join = await service.post_message(
            conversation.id, owner, MessageCreateSchema(body="PRE-JOIN-TEXT")
        )
        await asyncio.sleep(0.02)
        await service.add_participants(conversation.id, owner, [newcomer])
        await asyncio.sleep(0.02)
        await service.post_message(
            conversation.id,
            older,
            MessageCreateSchema(body="agree", reply_to_id=pre_join.id),
        )

        page = await service.list_messages(conversation.id, user_id=newcomer)
        replies = [item for item in page["items"] if item.body == "agree"]

        assert len(replies) == 1
        assert replies[0].reply_to is not None
        assert replies[0].reply_to.id == pre_join.id
        assert replies[0].reply_to.excerpt == ""
        assert all("PRE-JOIN" not in item.model_dump_json() for item in page["items"])

    async def test_older_member_page_keeps_the_excerpt(
        self, service: ChatService, users: list[UUID]
    ) -> None:
        """The redaction is per reader, not a global blanking."""
        owner, older, other, newcomer, _ = users
        conversation = await service.start_conversation(owner, [older, other])
        pre_join = await service.post_message(
            conversation.id, owner, MessageCreateSchema(body="PRE-JOIN-TEXT")
        )
        await asyncio.sleep(0.02)
        await service.add_participants(conversation.id, owner, [newcomer])
        await service.post_message(
            conversation.id,
            older,
            MessageCreateSchema(body="agree", reply_to_id=pre_join.id),
        )

        page = await service.list_messages(conversation.id, user_id=other)
        reply = next(item for item in page["items"] if item.body == "agree")

        assert reply.reply_to is not None
        assert reply.reply_to.excerpt == "PRE-JOIN-TEXT"


class TestRoleHierarchy:
    """Only the owner changes the owner's seat or another admin's role."""

    async def test_admin_cannot_take_over_the_group(
        self, service: ChatService, users: list[UUID]
    ) -> None:
        """The three-step takeover is refused at its first step."""
        creator, admin, member, _, _ = users
        conversation = await service.start_conversation(creator, [admin, member])
        await service.set_role(
            conversation.id, creator, admin, ParticipantRoleEnum.ADMIN
        )

        with pytest.raises(ForbiddenException):
            await service.set_role(
                conversation.id, admin, creator, ParticipantRoleEnum.MEMBER
            )
        with pytest.raises(ForbiddenException):
            await service.set_role(
                conversation.id, admin, admin, ParticipantRoleEnum.OWNER
            )
        with pytest.raises(ForbiddenException):
            await service.remove_participant(conversation.id, admin, creator)

        roles = {
            p.user_id: ParticipantRoleEnum(p.role)
            for p in (await service.get_conversation(conversation.id)).participants
        }
        assert roles[creator] is ParticipantRoleEnum.OWNER
        assert roles[admin] is ParticipantRoleEnum.ADMIN

    async def test_admin_cannot_demote_another_admin(
        self, service: ChatService, users: list[UUID]
    ) -> None:
        creator, admin, other_admin, _, _ = users
        conversation = await service.start_conversation(creator, [admin, other_admin])
        await service.set_role(
            conversation.id, creator, admin, ParticipantRoleEnum.ADMIN
        )
        await service.set_role(
            conversation.id, creator, other_admin, ParticipantRoleEnum.ADMIN
        )

        with pytest.raises(ForbiddenException):
            await service.set_role(
                conversation.id, admin, other_admin, ParticipantRoleEnum.MEMBER
            )

    async def test_admin_can_still_promote_a_member(
        self, service: ChatService, users: list[UUID]
    ) -> None:
        creator, admin, member, _, _ = users
        conversation = await service.start_conversation(creator, [admin, member])
        await service.set_role(
            conversation.id, creator, admin, ParticipantRoleEnum.ADMIN
        )

        promoted = await service.set_role(
            conversation.id, admin, member, ParticipantRoleEnum.ADMIN
        )

        assert ParticipantRoleEnum(promoted.role) is ParticipantRoleEnum.ADMIN

    async def test_owner_can_change_any_role(
        self, service: ChatService, users: list[UUID]
    ) -> None:
        creator, admin, member, _, _ = users
        conversation = await service.start_conversation(creator, [admin, member])
        await service.set_role(
            conversation.id, creator, admin, ParticipantRoleEnum.ADMIN
        )

        demoted = await service.set_role(
            conversation.id, creator, admin, ParticipantRoleEnum.MEMBER
        )
        granted = await service.set_role(
            conversation.id, creator, member, ParticipantRoleEnum.OWNER
        )

        assert ParticipantRoleEnum(demoted.role) is ParticipantRoleEnum.MEMBER
        assert ParticipantRoleEnum(granted.role) is ParticipantRoleEnum.OWNER
