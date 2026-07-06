"""Tests for the chat module — ChatService and make_chat_router."""

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
    make_chat_router,
    make_conversation_model,
    make_conversation_participant_model,
    make_message_model,
)
from tempest_fastapi_sdk.sse import SSEBroker


class _ChatUser(BaseUserModel):
    __tablename__ = "chat_users"


_Conversation = make_conversation_model(
    tablename="chat_conversations",
    class_name="_ChatConversation",
)
_Participant = make_conversation_participant_model(
    conversation_table="chat_conversations",
    user_table="chat_users",
    tablename="chat_participants",
    class_name="_ChatParticipant",
)
_Message = make_message_model(
    conversation_table="chat_conversations",
    user_table="chat_users",
    tablename="chat_messages",
    class_name="_ChatMessage",
)


class _SpyBroker(SSEBroker):
    """Broker that records every publish call."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, Any, str | None]] = []

    async def publish(
        self,
        channel: str,
        data: Any = "",
        *,
        event: str | None = None,
        id: str | None = None,
        retry: int | None = None,
    ) -> None:
        self.published.append((channel, data, event))


def _service(session: AsyncSession, broker: SSEBroker | None = None) -> ChatService:
    return ChatService(
        conversations=BaseRepository(session, model=_Conversation),
        participants=BaseRepository(session, model=_Participant),
        messages=BaseRepository(session, model=_Message),
        broker=broker,
    )


class TestStartConversation:
    async def test_adds_creator_and_participants(self, session: AsyncSession) -> None:
        service = _service(session)
        creator, other = uuid4(), uuid4()
        conv = await service.start_conversation(creator, [other], title="Team")
        assert conv.title == "Team"
        assert await service.is_participant(conv.id, creator) is True
        assert await service.is_participant(conv.id, other) is True

    async def test_creator_not_duplicated(self, session: AsyncSession) -> None:
        service = _service(session)
        creator = uuid4()
        conv = await service.start_conversation(creator, [creator])
        participants = await service.participants.list(
            filters={"conversation_id": conv.id},
        )
        assert len(participants) == 1

    async def test_non_member_is_not_participant(self, session: AsyncSession) -> None:
        service = _service(session)
        conv = await service.start_conversation(uuid4(), [])
        assert await service.is_participant(conv.id, uuid4()) is False


class TestListConversations:
    async def test_lists_only_users_conversations(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(session)
        alice, bob = uuid4(), uuid4()
        conv_ab = await service.start_conversation(alice, [bob])
        await service.start_conversation(bob, [])
        alice_convs = await service.list_conversations(alice)
        assert [c.id for c in alice_convs] == [conv_ab.id]

    async def test_empty_when_no_conversations(self, session: AsyncSession) -> None:
        service = _service(session)
        assert await service.list_conversations(uuid4()) == []


class TestMessages:
    async def test_post_and_list(self, session: AsyncSession) -> None:
        service = _service(session)
        creator = uuid4()
        conv = await service.start_conversation(creator, [])
        await service.post_message(conv.id, creator, "first")
        await service.post_message(conv.id, creator, "second")
        page = await service.list_messages(conv.id)
        assert page["total"] == 2
        assert [m.body for m in page["items"]] == ["first", "second"]

    async def test_post_publishes_to_broker(self, session: AsyncSession) -> None:
        broker = _SpyBroker()
        service = _service(session, broker=broker)
        creator = uuid4()
        conv = await service.start_conversation(creator, [])
        await service.post_message(conv.id, creator, "hi")
        assert len(broker.published) == 1
        channel, data, event = broker.published[0]
        assert channel == str(conv.id)
        assert event == "message"
        assert data["body"] == "hi"


USER_ID = uuid4()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> ChatService:
        return _service(session)

    def current_user_id() -> UUID:
        return USER_ID

    app = FastAPI()
    app.include_router(
        make_chat_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=current_user_id,
        ),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


class TestChatRouter:
    async def test_full_flow(self, client: AsyncClient) -> None:
        created = await client.post("/api/chat/conversations", json={"title": "Room"})
        assert created.status_code == 201
        conv_id = created.json()["id"]

        posted = await client.post(
            f"/api/chat/conversations/{conv_id}/messages",
            json={"body": "hello"},
        )
        assert posted.status_code == 201

        listed = await client.get(f"/api/chat/conversations/{conv_id}/messages")
        assert listed.status_code == 200
        assert [m["body"] for m in listed.json()["items"]] == ["hello"]

        mine = await client.get("/api/chat/conversations")
        assert [c["id"] for c in mine.json()] == [conv_id]

    async def test_non_participant_forbidden(self, client: AsyncClient) -> None:
        # A conversation the current user is not part of (created via a
        # second conversation started by someone else is not reachable
        # here, so post to a random id -> not a participant).
        resp = await client.post(
            f"/api/chat/conversations/{uuid4()}/messages",
            json={"body": "x"},
        )
        assert resp.status_code == 403

    async def test_stream_without_broker_is_404(self, client: AsyncClient) -> None:
        created = await client.post("/api/chat/conversations", json={})
        conv_id = created.json()["id"]
        resp = await client.get(f"/api/chat/conversations/{conv_id}/stream")
        assert resp.status_code == 404

    async def test_empty_body_rejected(self, client: AsyncClient) -> None:
        created = await client.post("/api/chat/conversations", json={})
        conv_id = created.json()["id"]
        resp = await client.post(
            f"/api/chat/conversations/{conv_id}/messages",
            json={"body": "   "},
        )
        assert resp.status_code == 422
