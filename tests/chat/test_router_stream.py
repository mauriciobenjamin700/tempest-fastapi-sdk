"""The chat SSE stream, driven over raw ASGI.

``httpx``'s ASGI transport buffers the whole body before returning, so an
endless stream never comes back through it. These tests call the app
directly and read ``http.response.body`` messages as they are sent,
which is what lets them observe the two properties that matter while
the stream is **open**: no database session is held, and the stream
ends when the subscriber's membership does.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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
    PARTICIPANT_REMOVED_EVENT,
    ChatService,
    make_chat_router,
    make_conversation_model,
    make_conversation_participant_model,
    make_message_model,
)
from tempest_fastapi_sdk.sse import SSEBroker
from tempest_fastapi_sdk.utils.datetime import utcnow


class _StreamUser(BaseUserModel):
    __tablename__ = "stream_chat_users"


_Conversation = make_conversation_model(
    tablename="stream_chat_conversations",
    class_name="_StreamConversation",
)
_Participant = make_conversation_participant_model(
    conversation_table="stream_chat_conversations",
    user_table="stream_chat_users",
    tablename="stream_chat_participants",
    class_name="_StreamParticipant",
)
_Message = make_message_model(
    conversation_table="stream_chat_conversations",
    user_table="stream_chat_users",
    tablename="stream_chat_messages",
    class_name="_StreamMessage",
)

ANA: UUID = uuid4()
BRUNO: UUID = uuid4()
CARLA: UUID = uuid4()


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


@dataclass
class _Harness:
    """Everything a stream test drives.

    Attributes:
        app (FastAPI): The app with the chat router.
        client (AsyncClient): Buffered client for the ordinary routes.
        caller (_Caller): The identity dependency.
        sessionmaker (async_sessionmaker[AsyncSession]): Direct DB access.
        open_sessions (list[int]): One-element counter of sessions the
            router's ``session_factory`` has opened and not yet closed.
    """

    app: FastAPI
    client: AsyncClient
    caller: _Caller
    sessionmaker: async_sessionmaker[AsyncSession]
    open_sessions: list[int] = field(default_factory=lambda: [0])


async def _build(recheck: float | None) -> tuple[_Harness, Any]:
    """Build the app, the client and the engine.

    Args:
        recheck (float | None): ``membership_recheck_seconds`` for the
            router.

    Returns:
        tuple[_Harness, Any]: The harness and the engine to dispose.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    broker = SSEBroker(heartbeat_seconds=0.02)
    caller = _Caller()
    open_sessions = [0]

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            open_sessions[0] += 1
            try:
                yield session
            finally:
                open_sessions[0] -= 1

    def service_factory(session: AsyncSession) -> ChatService:
        return ChatService(
            conversations=BaseRepository(session, model=_Conversation),
            participants=BaseRepository(session, model=_Participant),
            messages=BaseRepository(session, model=_Message),
            broker=broker,
        )

    app = FastAPI()
    app.include_router(
        make_chat_router(
            service_factory=service_factory,
            session_factory=session_factory,
            current_user_id=caller,
            membership_recheck_seconds=recheck,
        ),
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    harness = _Harness(
        app=app,
        client=client,
        caller=caller,
        sessionmaker=sessionmaker,
        open_sessions=open_sessions,
    )
    return harness, engine


@pytest.fixture
async def harness() -> AsyncIterator[_Harness]:
    """Yield a harness with the periodic re-check off.

    Only the removal event can close its streams, so a test that passes
    here proves the event path on its own.

    Yields:
        _Harness: The app and its handles.
    """
    built, engine = await _build(recheck=None)
    async with built.client:
        yield built
    await engine.dispose()


@pytest.fixture
async def rechecking() -> AsyncIterator[_Harness]:
    """Yield a harness whose stream re-reads membership on every frame.

    Yields:
        _Harness: The app and its handles.
    """
    built, engine = await _build(recheck=0.0)
    async with built.client:
        yield built
    await engine.dispose()


class _OpenStream:
    """One SSE request in flight against the raw ASGI app.

    Attributes:
        status (int | None): The response status, once started.
        chunks (list[bytes]): Body chunks received so far.
        finished (asyncio.Event): Set when the body's last chunk arrived.
    """

    def __init__(self, app: FastAPI, path: str) -> None:
        """Start the request as a background task.

        Args:
            app (FastAPI): The ASGI app.
            path (str): The request path.
        """
        self.status: int | None = None
        self.chunks: list[bytes] = []
        self.finished: asyncio.Event = asyncio.Event()
        self._started: asyncio.Event = asyncio.Event()
        self._disconnect: asyncio.Event = asyncio.Event()
        self._requested: bool = False
        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"test")],
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
        }
        self.task: asyncio.Task[None] = asyncio.create_task(
            app(scope, self._receive, self._send),
        )

    async def _receive(self) -> dict[str, Any]:
        """Hand the request body once, then wait for the disconnect.

        Returns:
            dict[str, Any]: The next ASGI receive message.
        """
        if not self._requested:
            self._requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        """Record what the app sends.

        Args:
            message (dict[str, Any]): The ASGI send message.
        """
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self._started.set()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                self.chunks.append(body)
            if not message.get("more_body", False):
                self.finished.set()

    async def first_frame(self) -> None:
        """Wait until the response started and one frame arrived."""
        await asyncio.wait_for(self._started.wait(), timeout=5)
        for _ in range(250):
            if self.chunks or self.finished.is_set():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("no frame arrived")

    async def close(self) -> None:
        """Disconnect the client and let the app finish."""
        self._disconnect.set()
        try:
            await asyncio.wait_for(self.task, timeout=5)
        except TimeoutError:
            self.task.cancel()

    @property
    def text(self) -> str:
        """Return everything received, decoded.

        Returns:
            str: The concatenated body.
        """
        return b"".join(self.chunks).decode()


async def _group(harness: _Harness) -> str:
    """Create a three-person group owned by Ana.

    Args:
        harness (_Harness): The app handles.

    Returns:
        str: The conversation id.
    """
    harness.caller.user_id = ANA
    response = await harness.client.post(
        "/api/chat/conversations",
        json={"participant_ids": [str(BRUNO), str(CARLA)], "title": "Sala"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


class TestStreamHoldsNoSession:
    """An idle subscriber must not pin a pooled connection."""

    async def test_no_session_is_open_while_streaming(
        self,
        harness: _Harness,
    ) -> None:
        """Before the fix the request's session stayed open for the stream."""
        conversation = await _group(harness)
        harness.caller.user_id = BRUNO

        stream = _OpenStream(
            harness.app, f"/api/chat/conversations/{conversation}/stream"
        )
        try:
            await stream.first_frame()

            assert stream.status == 200
            assert harness.open_sessions[0] == 0
        finally:
            await stream.close()

    async def test_an_outsider_is_refused_before_streaming(
        self,
        harness: _Harness,
    ) -> None:
        conversation = await _group(harness)
        harness.caller.user_id = uuid4()

        response = await harness.client.get(
            f"/api/chat/conversations/{conversation}/stream",
        )

        assert response.status_code == 403
        assert harness.open_sessions[0] == 0


class TestStreamEndsWithTheMembership:
    """Leaving or being removed must stop the conversation reaching you."""

    async def test_leaving_closes_the_leavers_stream(
        self,
        harness: _Harness,
    ) -> None:
        conversation = await _group(harness)
        harness.caller.user_id = BRUNO
        stream = _OpenStream(
            harness.app, f"/api/chat/conversations/{conversation}/stream"
        )
        try:
            await stream.first_frame()

            left = await harness.client.post(
                f"/api/chat/conversations/{conversation}/leave",
            )

            assert left.status_code == 200
            await asyncio.wait_for(stream.finished.wait(), timeout=5)
            assert f"event: {PARTICIPANT_REMOVED_EVENT}" in stream.text
            reconnect = await harness.client.get(
                f"/api/chat/conversations/{conversation}/stream",
            )
            assert reconnect.status_code == 403
        finally:
            await stream.close()

    async def test_removal_closes_the_removed_members_stream(
        self,
        harness: _Harness,
    ) -> None:
        conversation = await _group(harness)
        harness.caller.user_id = BRUNO
        stream = _OpenStream(
            harness.app, f"/api/chat/conversations/{conversation}/stream"
        )
        try:
            await stream.first_frame()

            harness.caller.user_id = ANA
            removed = await harness.client.delete(
                f"/api/chat/conversations/{conversation}/participants/{BRUNO}",
            )
            await asyncio.wait_for(stream.finished.wait(), timeout=5)

            assert removed.status_code == 200
            before = stream.text
            await harness.client.post(
                f"/api/chat/conversations/{conversation}/messages",
                json={"body": "depois da saída"},
            )
            assert "depois da saída" not in before + stream.text
        finally:
            await stream.close()

    async def test_someone_elses_removal_keeps_the_stream_open(
        self,
        harness: _Harness,
    ) -> None:
        conversation = await _group(harness)
        harness.caller.user_id = BRUNO
        stream = _OpenStream(
            harness.app, f"/api/chat/conversations/{conversation}/stream"
        )
        try:
            await stream.first_frame()

            harness.caller.user_id = ANA
            await harness.client.delete(
                f"/api/chat/conversations/{conversation}/participants/{CARLA}",
            )
            await harness.client.post(
                f"/api/chat/conversations/{conversation}/messages",
                json={"body": "ainda aqui"},
            )
            for _ in range(250):
                if "ainda aqui" in stream.text:
                    break
                await asyncio.sleep(0.01)

            assert "ainda aqui" in stream.text
            assert f"event: {PARTICIPANT_REMOVED_EVENT}" in stream.text
            assert not stream.finished.is_set()
        finally:
            await stream.close()

    async def test_a_removal_outside_the_service_is_caught_by_the_recheck(
        self,
        rechecking: _Harness,
    ) -> None:
        """A direct ``UPDATE`` publishes no event; the re-check still ends it."""
        conversation = await _group(rechecking)
        rechecking.caller.user_id = BRUNO
        stream = _OpenStream(
            rechecking.app,
            f"/api/chat/conversations/{conversation}/stream",
        )
        try:
            await stream.first_frame()

            async with rechecking.sessionmaker() as session:
                repository = BaseRepository(session, model=_Participant)
                row = await repository.get(
                    {"conversation_id": UUID(conversation), "user_id": BRUNO},
                )
                row.left_at = utcnow()
                await repository.update(row)

            await asyncio.wait_for(stream.finished.wait(), timeout=5)
            assert rechecking.open_sessions[0] == 0
        finally:
            await stream.close()
