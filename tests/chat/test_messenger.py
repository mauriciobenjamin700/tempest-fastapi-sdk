"""The nine decisions a messenger gets wrong when each service takes them alone.

The SDK shipped conversation, participant and message with three columns
between them, so every product rebuilt the rest — and rebuilt it a little
differently. What is pinned here is not the plumbing but the decisions:
the watermark instead of a receipt row per reader, one reaction per
person, a revoke that actually clears the body, a direct conversation
that cannot exist twice for the same pair, and a reply stub that dies
with the message it quotes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, BaseUserModel
from tempest_fastapi_sdk.chat import (
    ChatService,
    ConversationKind,
    MessageCreateSchema,
    MessageKind,
    ParticipantRole,
    SystemEvent,
    make_conversation_model,
    make_conversation_participant_model,
    make_message_attachment_model,
    make_message_model,
    make_message_reaction_model,
)
from tempest_fastapi_sdk.exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)


class _User(BaseUserModel):
    __tablename__ = "messenger_users"


_Conversation = make_conversation_model(
    tablename="messenger_conversations",
    class_name="_MessengerConversation",
)
_Participant = make_conversation_participant_model(
    conversation_table="messenger_conversations",
    user_table="messenger_users",
    tablename="messenger_participants",
    class_name="_MessengerParticipant",
)
_Message = make_message_model(
    conversation_table="messenger_conversations",
    user_table="messenger_users",
    tablename="messenger_messages",
    class_name="_MessengerMessage",
)
_Attachment = make_message_attachment_model(
    message_table="messenger_messages",
    tablename="messenger_attachments",
    class_name="_MessengerAttachment",
)
_Reaction = make_message_reaction_model(
    message_table="messenger_messages",
    user_table="messenger_users",
    tablename="messenger_reactions",
    class_name="_MessengerReaction",
)


def _service(session: AsyncSession) -> ChatService:
    """Build a service wired to every table.

    Args:
        session (AsyncSession): The suite's session.

    Returns:
        ChatService: The service under test.
    """
    return ChatService(
        conversations=BaseRepository(session, model=_Conversation),
        participants=BaseRepository(session, model=_Participant),
        messages=BaseRepository(session, model=_Message),
        attachments=BaseRepository(session, model=_Attachment),
        reactions=BaseRepository(session, model=_Reaction),
    )


@pytest.fixture
async def service(session: AsyncSession) -> AsyncIterator[ChatService]:
    """Yield a fully wired chat service.

    Args:
        session (AsyncSession): The suite's session.

    Yields:
        ChatService: The service under test.
    """
    yield _service(session)


async def _upload(service: ChatService, key: str = "a/b.jpg") -> Any:
    """Write an unclaimed attachment row, the way an upload endpoint does.

    Args:
        service (ChatService): The service under test.
        key (str): The storage key to record.

    Returns:
        Any: The attachment row, with ``message_id`` still ``None``.
    """
    assert service.attachments is not None
    return await service.attachments.add(
        service.attachments.model(
            storage_key=key,
            filename="b.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
        ),
    )


class TestDirectIsIdempotent:
    """Two threads between the same two people is unrepairable in the UI."""

    async def test_same_pair_returns_the_same_conversation(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()

        first = await service.start_conversation(ana, [bruno])
        second = await service.start_conversation(bruno, [ana])

        assert first.id == second.id

    async def test_kind_is_inferred_from_the_headcount(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()

        pair = await service.start_conversation(ana, [bruno])
        group = await service.start_conversation(ana, [bruno, carla])

        assert pair.kind == ConversationKind.DIRECT
        assert group.kind == ConversationKind.GROUP

    async def test_explicit_direct_with_three_is_refused(
        self,
        service: ChatService,
    ) -> None:
        with pytest.raises(ValidationException):
            await service.start_conversation(
                uuid4(),
                [uuid4(), uuid4()],
                kind=ConversationKind.DIRECT,
            )

    async def test_a_group_is_never_deduplicated(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()

        first = await service.start_conversation(ana, [bruno, carla])
        second = await service.start_conversation(ana, [bruno, carla])

        assert first.id != second.id


class TestIdempotentSend:
    """A lost 201 must not become a duplicate message."""

    async def test_retry_with_the_same_client_id_returns_the_same_row(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        payload = MessageCreateSchema(body="oi", client_id="c-1")

        first = await service.post_message(conversation.id, ana, payload)
        second = await service.post_message(conversation.id, ana, payload)

        assert first.id == second.id
        page = await service.list_messages(conversation.id)
        assert page["total"] == 1

    async def test_a_different_sender_may_reuse_the_id(
        self,
        service: ChatService,
    ) -> None:
        """Uniqueness is per sender: clients pick ids independently."""
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])

        first = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi", client_id="c-1"),
        )
        second = await service.post_message(
            conversation.id,
            bruno,
            MessageCreateSchema(body="oi", client_id="c-1"),
        )

        assert first.id != second.id

    async def test_client_id_is_echoed_back(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])

        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi", client_id="c-9"),
        )

        assert message.client_id == "c-9"


class TestReplyStub:
    """The quote renders without a second round-trip — and dies with its parent."""

    async def test_reply_carries_an_excerpt(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        parent = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="mensagem original"),
        )

        reply = await service.post_message(
            conversation.id,
            bruno,
            MessageCreateSchema(body="resposta", reply_to_id=parent.id),
        )

        assert reply.reply_to is not None
        assert reply.reply_to.id == parent.id
        assert reply.reply_to.sender_id == ana
        assert reply.reply_to.excerpt == "mensagem original"
        assert reply.reply_to.revoked is False

    async def test_revoking_the_parent_empties_every_quote(
        self,
        service: ChatService,
    ) -> None:
        """Otherwise the deleted text survives inside each reply to it."""
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        parent = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="segredo"),
        )
        await service.post_message(
            conversation.id,
            bruno,
            MessageCreateSchema(body="resposta", reply_to_id=parent.id),
        )

        await service.revoke_message(parent.id, ana)

        page = await service.list_messages(conversation.id)
        reply = next(item for item in page["items"] if item.body == "resposta")
        assert reply.reply_to is not None
        assert reply.reply_to.revoked is True
        assert reply.reply_to.excerpt == ""
        assert "segredo" not in str(page["items"])

    async def test_replying_across_conversations_is_refused(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        here = await service.start_conversation(ana, [bruno])
        there = await service.start_conversation(ana, [uuid4()])
        elsewhere = await service.post_message(
            there.id,
            ana,
            MessageCreateSchema(body="outra conversa"),
        )

        with pytest.raises(NotFoundException):
            await service.post_message(
                here.id,
                ana,
                MessageCreateSchema(body="resposta", reply_to_id=elsewhere.id),
            )


class TestWatermarks:
    """Read state is one row per participant, not one per message per reader."""

    async def test_marking_read_moves_one_row(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        await service.post_message(conversation.id, ana, MessageCreateSchema(body="1"))
        await service.post_message(conversation.id, ana, MessageCreateSchema(body="2"))

        membership = await service.mark_read(conversation.id, bruno)

        assert membership.last_read_at is not None
        rows = await service.participants.list(
            filters={"conversation_id": conversation.id},
        )
        assert len(rows) == 2

    async def test_unread_count_follows_the_watermark(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        await service.post_message(conversation.id, ana, MessageCreateSchema(body="1"))
        await service.post_message(conversation.id, ana, MessageCreateSchema(body="2"))

        before = await service.list_conversations(bruno)
        await service.mark_read(conversation.id, bruno)
        after = await service.list_conversations(bruno)

        assert before[0].unread_count == 2
        assert after[0].unread_count == 0

    async def test_receipts_are_derived_not_stored(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno, carla])
        await service.post_message(conversation.id, ana, MessageCreateSchema(body="oi"))
        await service.mark_read(conversation.id, bruno)

        page = await service.list_messages(conversation.id, with_receipts=True)
        posted = next(item for item in page["items"] if item.kind == MessageKind.TEXT)

        assert posted.receipts is not None
        assert posted.receipts.total == 2
        assert posted.receipts.read == 1
        assert posted.receipts.delivered == 1

    async def test_reading_up_to_a_message_does_not_read_later_ones(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        first = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="1"),
        )
        await service.post_message(conversation.id, ana, MessageCreateSchema(body="2"))

        await service.mark_read(conversation.id, bruno, message_id=first.id)

        assert (await service.list_conversations(bruno))[0].unread_count == 1


class TestReactions:
    """One per person: reacting again replaces, never stacks."""

    async def test_reacting_twice_replaces(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi"),
        )

        await service.react(message.id, bruno, "👍")
        updated = await service.react(message.id, bruno, "❤️")

        assert [(r.emoji, r.count) for r in updated.reactions] == [("❤️", 1)]

    async def test_two_people_group_under_one_emoji(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno, carla])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi"),
        )

        await service.react(message.id, bruno, "👍")
        updated = await service.react(message.id, carla, "👍")

        assert updated.reactions[0].count == 2
        assert set(updated.reactions[0].user_ids) == {bruno, carla}

    async def test_unreacting_removes_it(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi"),
        )
        await service.react(message.id, bruno, "👍")

        updated = await service.unreact(message.id, bruno)

        assert updated.reactions == []

    async def test_a_service_without_the_table_refuses_clearly(
        self,
        session: AsyncSession,
    ) -> None:
        service = ChatService(
            conversations=BaseRepository(session, model=_Conversation),
            participants=BaseRepository(session, model=_Participant),
            messages=BaseRepository(session, model=_Message),
        )
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi"),
        )

        with pytest.raises(ValidationException):
            await service.react(message.id, bruno, "👍")


class TestRevoke:
    """Delete for everyone: the row stays, the content does not."""

    async def test_body_is_cleared_not_flagged(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="apagar isto"),
        )

        tombstone, _keys = await service.revoke_message(message.id, ana)

        row = await service.messages.get_by_id(message.id)
        assert tombstone.body == ""
        assert row.body == ""
        assert row.revoked_at is not None

    async def test_attachments_are_returned_for_the_caller_to_delete(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        attachment = await _upload(service, key="uploads/photo.jpg")
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(
                body="",
                kind=MessageKind.IMAGE,
                attachment_ids=[attachment.id],
            ),
        )

        _tombstone, keys = await service.revoke_message(message.id, ana)

        assert keys == ["uploads/photo.jpg"]
        assert service.attachments is not None
        assert await service.attachments.list(filters={"message_id": message.id}) == []

    async def test_only_the_sender_may_revoke(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi"),
        )

        with pytest.raises(ForbiddenException):
            await service.revoke_message(message.id, bruno)

    async def test_a_revoked_message_cannot_be_edited(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi"),
        )
        await service.revoke_message(message.id, ana)

        with pytest.raises(ValidationException):
            await service.edit_message(message.id, ana, "de novo")


class TestAttachments:
    """Upload and post are two calls, so a failed upload loses no caption."""

    async def test_claiming_sets_message_and_position(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        first = await _upload(service, key="a.jpg")
        second = await _upload(service, key="b.jpg")

        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(
                body="álbum",
                kind=MessageKind.IMAGE,
                attachment_ids=[second.id, first.id],
            ),
        )

        assert [a.storage_key for a in message.attachments] == ["b.jpg", "a.jpg"]
        assert [a.position for a in message.attachments] == [0, 1]

    async def test_an_already_claimed_file_is_refused(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        attachment = await _upload(service)
        await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(kind=MessageKind.IMAGE, attachment_ids=[attachment.id]),
        )

        with pytest.raises(NotFoundException):
            await service.post_message(
                conversation.id,
                ana,
                MessageCreateSchema(
                    kind=MessageKind.IMAGE,
                    attachment_ids=[attachment.id],
                ),
            )

    async def test_a_media_message_needs_no_body(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        attachment = await _upload(service)

        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(kind=MessageKind.IMAGE, attachment_ids=[attachment.id]),
        )

        assert message.body == ""

    async def test_an_empty_text_message_is_refused(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])

        with pytest.raises(ValidationException):
            await service.post_message(
                conversation.id,
                ana,
                MessageCreateSchema(body=""),
            )


class TestGroups:
    """Roles, leaving, and the backlog a newcomer does not inherit."""

    async def test_creator_owns_the_group(self, service: ChatService) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()

        conversation = await service.start_conversation(ana, [bruno, carla])

        owner = next(p for p in conversation.participants if p.user_id == ana)
        member = next(p for p in conversation.participants if p.user_id == bruno)
        assert owner.role == ParticipantRole.OWNER
        assert member.role == ParticipantRole.MEMBER

    async def test_a_member_cannot_add_people(self, service: ChatService) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno, carla])

        with pytest.raises(ForbiddenException):
            await service.add_participants(conversation.id, bruno, [uuid4()])

    async def test_a_newcomer_does_not_inherit_the_backlog(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno, carla])
        await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="antes"),
        )
        newcomer = uuid4()

        await service.add_participants(conversation.id, ana, [newcomer])
        await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="depois"),
        )

        page = await service.list_messages(conversation.id, user_id=newcomer)
        bodies = [item.body for item in page["items"]]
        assert "antes" not in bodies
        assert "depois" in bodies

    async def test_share_history_hands_over_the_backlog(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno, carla])
        await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="antes"),
        )
        newcomer = uuid4()

        await service.add_participants(
            conversation.id,
            ana,
            [newcomer],
            share_history=True,
        )

        page = await service.list_messages(conversation.id, user_id=newcomer)
        assert "antes" in [item.body for item in page["items"]]

    async def test_leaving_keeps_the_row(self, service: ChatService) -> None:
        """Old messages still have to resolve a sender name."""
        ana, bruno, carla = uuid4(), uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno, carla])

        await service.leave(conversation.id, bruno)

        row = await service.participants.get_or_none(
            {"conversation_id": conversation.id, "user_id": bruno},
        )
        assert row is not None
        assert row.left_at is not None
        assert await service.is_participant(conversation.id, bruno) is False

    async def test_the_owner_cannot_be_removed(self, service: ChatService) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno, carla])
        await service.set_role(conversation.id, ana, bruno, ParticipantRole.ADMIN)

        with pytest.raises(ForbiddenException):
            await service.remove_participant(conversation.id, bruno, ana)


class TestSystemMessages:
    """A notice the client renders from structured data, not from prose."""

    async def test_creating_a_group_posts_a_system_message(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()

        conversation = await service.start_conversation(ana, [bruno, carla])

        page = await service.list_messages(conversation.id)
        notice = page["items"][0]
        assert notice.kind == MessageKind.SYSTEM
        assert notice.payload is not None
        assert notice.payload["event"] == SystemEvent.CONVERSATION_CREATED
        assert notice.payload["actor_id"] == str(ana)

    async def test_leaving_names_who_left(self, service: ChatService) -> None:
        ana, bruno, carla = uuid4(), uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno, carla])

        await service.leave(conversation.id, bruno)

        page = await service.list_messages(conversation.id)
        notice = page["items"][-1]
        assert notice.payload is not None
        assert notice.payload["event"] == SystemEvent.PARTICIPANT_LEFT
        assert notice.payload["user_ids"] == [str(bruno)]

    async def test_a_direct_thread_gets_no_notice(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()

        conversation = await service.start_conversation(ana, [bruno])

        assert (await service.list_messages(conversation.id))["total"] == 0


class TestPreferences:
    """Pin, archive and mute belong to one inbox, not to the thread."""

    async def test_archiving_hides_it_from_one_side_only(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])

        await service.set_preferences(conversation.id, ana, is_archived=True)

        assert await service.list_conversations(ana) == []
        assert [c.id for c in await service.list_conversations(bruno)] == [
            conversation.id,
        ]

    async def test_pinned_threads_sort_first(self, service: ChatService) -> None:
        ana = uuid4()
        older = await service.start_conversation(ana, [uuid4()])
        newer = await service.start_conversation(ana, [uuid4()])
        await service.post_message(newer.id, ana, MessageCreateSchema(body="novo"))
        await service.set_preferences(older.id, ana, is_pinned=True)

        listed = await service.list_conversations(ana)

        assert [c.id for c in listed] == [older.id, newer.id]

    async def test_archived_can_be_asked_for(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        await service.set_preferences(conversation.id, ana, is_archived=True)

        listed = await service.list_conversations(ana, include_archived=True)

        assert [c.id for c in listed] == [conversation.id]


class TestForward:
    """A forward is a copy that remembers where it came from."""

    async def test_forwarding_increments_the_score(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        source = await service.start_conversation(ana, [bruno])
        target = await service.start_conversation(ana, [uuid4()])
        original = await service.post_message(
            source.id,
            ana,
            MessageCreateSchema(body="repassar"),
        )

        forwarded = await service.forward(original.id, ana, [target.id])

        assert len(forwarded) == 1
        assert forwarded[0].body == "repassar"
        assert forwarded[0].forwarded_from_id == original.id
        assert forwarded[0].forward_score == 1

    async def test_forwarding_a_forward_keeps_counting(
        self,
        service: ChatService,
    ) -> None:
        ana = uuid4()
        first = await service.start_conversation(ana, [uuid4()])
        second = await service.start_conversation(ana, [uuid4()])
        third = await service.start_conversation(ana, [uuid4()])
        original = await service.post_message(
            first.id,
            ana,
            MessageCreateSchema(body="viral"),
        )

        once = await service.forward(original.id, ana, [second.id])
        twice = await service.forward(once[0].id, ana, [third.id])

        assert twice[0].forward_score == 2

    async def test_forwarding_into_a_conversation_you_left_is_refused(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        source = await service.start_conversation(ana, [bruno])
        target = await service.start_conversation(bruno, [uuid4()])
        original = await service.post_message(
            source.id,
            ana,
            MessageCreateSchema(body="oi"),
        )

        with pytest.raises(ForbiddenException):
            await service.forward(original.id, ana, [target.id])

    async def test_no_targets_forwards_nothing(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        source = await service.start_conversation(ana, [bruno])
        original = await service.post_message(
            source.id,
            ana,
            MessageCreateSchema(body="oi"),
        )

        assert await service.forward(original.id, ana, []) == []


class TestEdit:
    """Editing stamps the message so a client can label it."""

    async def test_edit_sets_edited_at(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="typo"),
        )

        edited = await service.edit_message(message.id, ana, "corrigido")

        assert edited.body == "corrigido"
        assert edited.edited_at is not None

    async def test_only_the_sender_may_edit(self, service: ChatService) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        message = await service.post_message(
            conversation.id,
            ana,
            MessageCreateSchema(body="oi"),
        )

        with pytest.raises(ForbiddenException):
            await service.edit_message(message.id, bruno, "editado por outro")


class TestConversationOrdering:
    """The list is ordered by the denormalized pointer, not by a join."""

    async def test_last_message_at_moves_with_each_post(
        self,
        service: ChatService,
    ) -> None:
        ana = uuid4()
        first = await service.start_conversation(ana, [uuid4()])
        second = await service.start_conversation(ana, [uuid4()])
        await service.post_message(first.id, ana, MessageCreateSchema(body="a"))
        await service.post_message(second.id, ana, MessageCreateSchema(body="b"))
        await service.post_message(first.id, ana, MessageCreateSchema(body="c"))

        listed = await service.list_conversations(ana)

        assert [c.id for c in listed] == [first.id, second.id]
        assert listed[0].last_message_at is not None


class TestAdversarial:
    """Four defects this module shipped with, each measured before the fix."""

    async def test_client_id_from_another_conversation_is_refused(
        self,
        service: ChatService,
    ) -> None:
        """It used to answer 201 with the other thread's message.

        The id is unique per sender, so the lookup found the row from
        conversation A while the caller was posting into B: the response
        carried a foreign ``conversation_id`` and the message the caller
        actually sent was never written.
        """
        ana, bruno = uuid4(), uuid4()
        here = await service.start_conversation(ana, [bruno])
        there = await service.start_conversation(ana, [uuid4()])
        await service.post_message(
            here.id,
            ana,
            MessageCreateSchema(body="primeira", client_id="k"),
        )

        with pytest.raises(ValidationException):
            await service.post_message(
                there.id,
                ana,
                MessageCreateSchema(body="segunda", client_id="k"),
            )

    async def test_the_same_conversation_stays_idempotent(
        self,
        service: ChatService,
    ) -> None:
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        payload = MessageCreateSchema(body="oi", client_id="k")

        first = await service.post_message(conversation.id, ana, payload)
        second = await service.post_message(conversation.id, ana, payload)

        assert first.id == second.id

    async def test_your_own_message_is_not_unread_for_you(
        self,
        service: ChatService,
    ) -> None:
        """The badge used to light up on the thread you just posted in."""
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        await service.post_message(conversation.id, ana, MessageCreateSchema(body="oi"))

        mine = await service.list_conversations(ana)
        theirs = await service.list_conversations(bruno)

        assert mine[0].unread_count == 0
        assert theirs[0].unread_count == 1

    async def test_forwarding_media_carries_the_files(
        self,
        service: ChatService,
    ) -> None:
        """A forwarded image used to arrive as a media message with no media."""
        ana = uuid4()
        source = await service.start_conversation(ana, [uuid4()])
        target = await service.start_conversation(ana, [uuid4()])
        attachment = await _upload(service, key="uploads/original.jpg")
        original = await service.post_message(
            source.id,
            ana,
            MessageCreateSchema(
                kind=MessageKind.IMAGE,
                attachment_ids=[attachment.id],
            ),
        )

        forwarded = await service.forward(original.id, ana, [target.id])

        assert len(forwarded[0].attachments) == 1
        assert forwarded[0].attachments[0].storage_key == "uploads/original.jpg"

    async def test_forwarding_copies_the_row_not_the_bytes(
        self,
        service: ChatService,
    ) -> None:
        """Both messages point at the same stored object."""
        ana = uuid4()
        source = await service.start_conversation(ana, [uuid4()])
        target = await service.start_conversation(ana, [uuid4()])
        attachment = await _upload(service, key="uploads/one.jpg")
        original = await service.post_message(
            source.id,
            ana,
            MessageCreateSchema(
                kind=MessageKind.IMAGE,
                attachment_ids=[attachment.id],
            ),
        )

        forwarded = await service.forward(original.id, ana, [target.id])

        assert service.attachments is not None
        rows = await service.attachments.list(
            filters={"storage_key": "uploads/one.jpg"}
        )
        assert len(rows) == 2
        assert {row.message_id for row in rows} == {original.id, forwarded[0].id}

    async def test_a_history_page_costs_a_fixed_number_of_queries(
        self,
        service: ChatService,
        session: AsyncSession,
    ) -> None:
        """Measured at 42 statements for 20 messages before batching."""
        ana, bruno = uuid4(), uuid4()
        conversation = await service.start_conversation(ana, [bruno])
        for index in range(20):
            await service.post_message(
                conversation.id,
                ana,
                MessageCreateSchema(body=f"m{index}"),
            )

        statements: list[str] = []

        @event.listens_for(session.sync_session.get_bind(), "before_cursor_execute")
        def _record(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        page = await service.list_messages(conversation.id, page_size=20)

        assert len(page["items"]) == 20
        assert len(statements) <= 6, statements
