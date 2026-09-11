"""The HTTP surface of the messenger, end to end over ASGI.

One app, two users, and the endpoints a client actually calls: post with
a reply, react, edit, revoke, forward, mark read, pin, and manage a
group. The guard that matters most here is the participant check — every
route that touches a conversation must refuse someone who is not in it,
including someone who *left*.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import BaseModel, BaseRepository, BaseUserModel
from tempest_fastapi_sdk.chat import (
    ChatService,
    MessageKind,
    ParticipantRole,
    make_chat_router,
    make_conversation_model,
    make_conversation_participant_model,
    make_message_attachment_model,
    make_message_model,
    make_message_reaction_model,
)


class _RouterUser(BaseUserModel):
    __tablename__ = "router_messenger_users"


_Conversation = make_conversation_model(
    tablename="router_messenger_conversations",
    class_name="_RouterConversation",
)
_Participant = make_conversation_participant_model(
    conversation_table="router_messenger_conversations",
    user_table="router_messenger_users",
    tablename="router_messenger_participants",
    class_name="_RouterParticipant",
)
_Message = make_message_model(
    conversation_table="router_messenger_conversations",
    user_table="router_messenger_users",
    tablename="router_messenger_messages",
    class_name="_RouterMessage",
)
_Attachment = make_message_attachment_model(
    message_table="router_messenger_messages",
    tablename="router_messenger_attachments",
    class_name="_RouterAttachment",
)
_Reaction = make_message_reaction_model(
    message_table="router_messenger_messages",
    user_table="router_messenger_users",
    tablename="router_messenger_reactions",
    class_name="_RouterReaction",
)

ANA: UUID = uuid4()
BRUNO: UUID = uuid4()


class _Caller:
    """The authenticated user, swappable mid-test.

    Attributes:
        user_id (UUID): Who the dependency answers with.
    """

    def __init__(self) -> None:
        """Start as Ana."""
        self.user_id: UUID = ANA

    def __call__(self) -> UUID:
        """Return the current user id.

        Returns:
            UUID: The authenticated user.
        """
        return self.user_id


@pytest.fixture
async def caller() -> AsyncIterator[_Caller]:
    """Yield the swappable identity used by the app.

    Yields:
        _Caller: The dependency object.
    """
    yield _Caller()


@pytest.fixture
async def client(caller: _Caller) -> AsyncIterator[AsyncClient]:
    """Yield a client against an app with the full chat router.

    Args:
        caller (_Caller): The identity dependency.

    Yields:
        AsyncClient: The ASGI client.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> ChatService:
        return ChatService(
            conversations=BaseRepository(session, model=_Conversation),
            participants=BaseRepository(session, model=_Participant),
            messages=BaseRepository(session, model=_Message),
            attachments=BaseRepository(session, model=_Attachment),
            reactions=BaseRepository(session, model=_Reaction),
        )

    app = FastAPI()
    app.include_router(
        make_chat_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=caller,
        ),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


async def _conversation(client: AsyncClient, *, group: bool = False) -> str:
    """Create a conversation and return its id.

    Args:
        client (AsyncClient): The ASGI client.
        group (bool): Create a three-person group instead of a pair.

    Returns:
        str: The conversation id.
    """
    participants = [str(BRUNO)] + ([str(uuid4())] if group else [])
    response = await client.post(
        "/api/chat/conversations",
        json={"participant_ids": participants, "title": "Sala" if group else None},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _post(client: AsyncClient, conversation_id: str, **body: Any) -> Any:
    """Post a message and return the parsed response.

    Args:
        client (AsyncClient): The ASGI client.
        conversation_id (str): Where to post.
        **body (Any): The message payload.

    Returns:
        Any: The decoded message.
    """
    response = await client.post(
        f"/api/chat/conversations/{conversation_id}/messages",
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestMessageRoutes:
    """Post, reply, edit, revoke."""

    async def test_reply_stub_is_inlined_in_the_history(
        self,
        client: AsyncClient,
    ) -> None:
        conversation = await _conversation(client)
        parent = await _post(client, conversation, body="original")

        reply = await _post(
            client,
            conversation,
            body="resposta",
            reply_to_id=parent["id"],
        )

        assert reply["reply_to"]["excerpt"] == "original"
        listed = await client.get(
            f"/api/chat/conversations/{conversation}/messages",
        )
        bodies = [item["body"] for item in listed.json()["items"]]
        assert bodies == ["original", "resposta"]

    async def test_retry_with_the_same_client_id_posts_once(
        self,
        client: AsyncClient,
    ) -> None:
        conversation = await _conversation(client)
        payload = {"body": "oi", "client_id": "abc"}

        first = await _post(client, conversation, **payload)
        second = await _post(client, conversation, **payload)

        assert first["id"] == second["id"]
        listed = await client.get(
            f"/api/chat/conversations/{conversation}/messages",
        )
        assert listed.json()["total"] == 1

    async def test_edit_and_revoke(self, client: AsyncClient) -> None:
        conversation = await _conversation(client)
        message = await _post(client, conversation, body="typo")

        edited = await client.patch(
            f"/api/chat/messages/{message['id']}",
            json={"body": "corrigido"},
        )
        revoked = await client.delete(f"/api/chat/messages/{message['id']}")

        assert edited.json()["body"] == "corrigido"
        assert edited.json()["edited_at"] is not None
        assert revoked.json()["body"] == ""
        assert revoked.json()["revoked_at"] is not None

    async def test_a_stranger_cannot_post(self, client: AsyncClient) -> None:
        response = await client.post(
            f"/api/chat/conversations/{uuid4()}/messages",
            json={"body": "x"},
        )

        assert response.status_code == 403


class TestReactionRoutes:
    """One reaction per person, replaced on re-react."""

    async def test_react_replace_and_remove(
        self,
        client: AsyncClient,
        caller: _Caller,
    ) -> None:
        conversation = await _conversation(client)
        message = await _post(client, conversation, body="oi")

        caller.user_id = BRUNO
        first = await client.put(
            f"/api/chat/messages/{message['id']}/reaction",
            json={"emoji": "👍"},
        )
        second = await client.put(
            f"/api/chat/messages/{message['id']}/reaction",
            json={"emoji": "🎉"},
        )
        removed = await client.delete(
            f"/api/chat/messages/{message['id']}/reaction",
        )

        assert [r["emoji"] for r in first.json()["reactions"]] == ["👍"]
        assert [r["emoji"] for r in second.json()["reactions"]] == ["🎉"]
        assert removed.json()["reactions"] == []


class TestReceiptRoutes:
    """Read state over HTTP, and the counts derived from it."""

    async def test_marking_read_clears_the_unread_count(
        self,
        client: AsyncClient,
        caller: _Caller,
    ) -> None:
        conversation = await _conversation(client)
        await _post(client, conversation, body="oi")

        caller.user_id = BRUNO
        before = await client.get("/api/chat/conversations")
        await client.post(
            f"/api/chat/conversations/{conversation}/read",
            json={},
        )
        after = await client.get("/api/chat/conversations")

        assert before.json()[0]["unread_count"] == 1
        assert after.json()[0]["unread_count"] == 0

    async def test_receipts_are_opt_in_on_the_history(
        self,
        client: AsyncClient,
        caller: _Caller,
    ) -> None:
        conversation = await _conversation(client)
        await _post(client, conversation, body="oi")
        caller.user_id = BRUNO
        await client.post(f"/api/chat/conversations/{conversation}/read", json={})
        caller.user_id = ANA

        plain = await client.get(
            f"/api/chat/conversations/{conversation}/messages",
        )
        with_receipts = await client.get(
            f"/api/chat/conversations/{conversation}/messages",
            params={"with_receipts": True},
        )

        assert plain.json()["items"][0]["receipts"] is None
        assert with_receipts.json()["items"][0]["receipts"]["read"] == 1


class TestPreferenceRoutes:
    """Pin and archive apply to the caller's inbox only."""

    async def test_archiving_hides_it_from_the_caller_only(
        self,
        client: AsyncClient,
        caller: _Caller,
    ) -> None:
        conversation = await _conversation(client)

        await client.put(
            f"/api/chat/conversations/{conversation}/preferences",
            json={"is_archived": True},
        )
        mine = await client.get("/api/chat/conversations")
        caller.user_id = BRUNO
        theirs = await client.get("/api/chat/conversations")

        assert mine.json() == []
        assert [c["id"] for c in theirs.json()] == [conversation]


class TestGroupRoutes:
    """Membership changes, and the notice each one posts."""

    async def test_add_remove_and_leave(
        self,
        client: AsyncClient,
        caller: _Caller,
    ) -> None:
        conversation = await _conversation(client, group=True)
        newcomer = str(uuid4())

        added = await client.post(
            f"/api/chat/conversations/{conversation}/participants",
            json={"participant_ids": [newcomer]},
        )
        removed = await client.delete(
            f"/api/chat/conversations/{conversation}/participants/{newcomer}",
        )

        assert added.status_code == 200
        assert newcomer in [p["user_id"] for p in added.json()["participants"]]
        left = next(
            p for p in removed.json()["participants"] if p["user_id"] == newcomer
        )
        assert left["left_at"] is not None

    async def test_a_member_cannot_add(
        self,
        client: AsyncClient,
        caller: _Caller,
    ) -> None:
        conversation = await _conversation(client, group=True)

        caller.user_id = BRUNO
        response = await client.post(
            f"/api/chat/conversations/{conversation}/participants",
            json={"participant_ids": [str(uuid4())]},
        )

        assert response.status_code == 403

    async def test_leaving_then_posting_is_refused(
        self,
        client: AsyncClient,
        caller: _Caller,
    ) -> None:
        """Membership is ``left_at IS NULL``, not the row's existence."""
        conversation = await _conversation(client, group=True)

        caller.user_id = BRUNO
        await client.post(f"/api/chat/conversations/{conversation}/leave")
        response = await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"body": "ainda estou aqui?"},
        )

        assert response.status_code == 403

    async def test_group_title_can_be_edited_by_the_owner(
        self,
        client: AsyncClient,
    ) -> None:
        conversation = await _conversation(client, group=True)

        response = await client.patch(
            f"/api/chat/conversations/{conversation}",
            json={"title": "Novo nome"},
        )

        assert response.json()["title"] == "Novo nome"

    async def test_creating_a_group_posts_a_system_notice(
        self,
        client: AsyncClient,
    ) -> None:
        conversation = await _conversation(client, group=True)

        listed = await client.get(
            f"/api/chat/conversations/{conversation}/messages",
        )

        first = listed.json()["items"][0]
        assert first["kind"] == MessageKind.SYSTEM
        assert first["payload"]["event"] == "conversation_created"


class TestForwardRoute:
    """Forwarding over HTTP."""

    async def test_forward_into_another_conversation(
        self,
        client: AsyncClient,
    ) -> None:
        source = await _conversation(client)
        target = await client.post(
            "/api/chat/conversations",
            json={"participant_ids": [str(uuid4())]},
        )
        target_id = target.json()["id"]
        message = await _post(client, source, body="repassar")

        response = await client.post(
            f"/api/chat/messages/{message['id']}/forward",
            json={"conversation_ids": [target_id]},
        )

        body = response.json()
        assert len(body) == 1
        assert body[0]["forward_score"] == 1
        assert body[0]["conversation_id"] == target_id


class TestRoles:
    """Promotion is an admin action with its own notice."""

    async def test_owner_role_is_reported(self, client: AsyncClient) -> None:
        conversation = await _conversation(client, group=True)

        response = await client.get(f"/api/chat/conversations/{conversation}")

        owner = next(
            p for p in response.json()["participants"] if p["user_id"] == str(ANA)
        )
        assert owner["role"] == ParticipantRole.OWNER
