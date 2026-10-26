"""``make_sse_router`` / ``UserAuthService.sse_router``: the stream behind a ticket.

The browser ``EventSource`` cannot send a header. Before this router a
service guarded its stream with ``current_user_dependency`` and either
forgot ``query_param`` (every browser client got ``401`` right after a
successful login) or turned it on and put the access token — valid on
every route — into the URL. The ticket opens one channel, for a minute,
and nothing else.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, MutableMapping
from datetime import timedelta
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import (
    DEFAULT_SSE_TICKET_TTL,
    SSE_CHANNEL_CLAIM,
    SSE_TICKET_ERROR,
    STREAM_TOKEN_TYPE,
    AsyncDatabaseManager,
    BaseUserModel,
    JWTUtils,
    SSEBroker,
    UserAuthService,
    make_bearer_token_dependency,
    make_sse_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
SECRET: str = "x" * 32


class _SSEUser(BaseUserModel):
    """User model for the SSE router tests."""

    __tablename__ = "sse_router_users"


_SSEUserToken = make_user_token_model(
    user_table="sse_router_users",
    tablename="sse_router_user_tokens",
    class_name="_SSEUserToken",
)


class _RecordingBroker(SSEBroker):
    """Broker whose stream names the channel it opened, then ends.

    ``ASGITransport`` buffers the whole body, so a real infinite stream
    never returns to the test. This one answers the same headers with a
    single frame carrying the channel.

    Attributes:
        opened (list[str]): Channels a stream was opened on, in order.
    """

    def __init__(self) -> None:
        """Initialize with no heartbeat and no opened channel."""
        super().__init__(heartbeat_seconds=None)
        self.opened: list[str] = []

    def response(
        self,
        channel: str,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        """Record ``channel`` and return a one-frame SSE response.

        Args:
            channel (str): The channel the route resolved.
            status_code (int): HTTP status.
            headers (dict[str, str] | None): Extra headers.

        Returns:
            StreamingResponse: One ``data:`` frame with the channel.
        """
        self.opened.append(channel)

        async def frames() -> AsyncIterator[str]:
            """Yield the single frame.

            Yields:
                str: ``data: <channel>``.
            """
            yield f"data: {channel}\n\n"

        return StreamingResponse(
            frames(),
            status_code=status_code,
            headers=headers,
            media_type="text/event-stream",
        )


class _Stack:
    """A service, a broker and the tokens of one active and one other user.

    Attributes:
        service (UserAuthService): The auth service.
        broker (_RecordingBroker): The broker the router subscribes to.
        user_id (str): Id of the active user.
        access (str): Access token of the active user.
        refresh (str): Refresh token of the active user.
        inactive_access (str): Access token of a user deactivated after
            login.
    """

    def __init__(
        self,
        service: UserAuthService,
        broker: _RecordingBroker,
        user_id: str,
        access: str,
        refresh: str,
        inactive_access: str,
    ) -> None:
        """Store the parts.

        Args:
            service (UserAuthService): The auth service.
            broker (_RecordingBroker): The broker.
            user_id (str): Id of the active user.
            access (str): Its access token.
            refresh (str): Its refresh token.
            inactive_access (str): Access token of the deactivated user.
        """
        self.service: UserAuthService = service
        self.broker: _RecordingBroker = broker
        self.user_id: str = user_id
        self.access: str = access
        self.refresh: str = refresh
        self.inactive_access: str = inactive_access


@pytest.fixture
async def stack() -> AsyncIterator[_Stack]:
    """Yield a service with one active and one deactivated user.

    Yields:
        _Stack: The service, the broker and the tokens.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    service = UserAuthService(
        db=db,
        user_model=_SSEUser,
        token_model=_SSEUserToken,
        auth_settings=AuthSettings(_env_file=None, AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET=SECRET),
    )
    async with db.get_session_context() as session:
        user, _ = await service.signup(
            session, email="ana@example.com", password=PASSWORD
        )
        gone, _ = await service.signup(
            session, email="bia@example.com", password=PASSWORD
        )
        access, refresh = service.issue_jwt_pair(user)
        inactive_access, _ = service.issue_jwt_pair(gone)
        gone.is_active = False
        user_id = str(user.id)
        await session.commit()
    try:
        yield _Stack(
            service, _RecordingBroker(), user_id, access, refresh, inactive_access
        )
    finally:
        await db.disconnect()


def _client(router: APIRouter) -> AsyncClient:
    """Mount ``router`` on a fresh app and return a client for it.

    Args:
        router (APIRouter): The router under test.

    Returns:
        AsyncClient: A client bound to the app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _ticket(client: AsyncClient, access: str) -> str:
    """Ask the router for a ticket with ``access``.

    Args:
        client (AsyncClient): The client.
        access (str): The bearer access token.

    Returns:
        str: The ticket.
    """
    response = await client.post(
        "/sse/ticket", headers={"Authorization": f"Bearer {access}"}
    )
    assert response.status_code == 200, response.text
    ticket: str = response.json()["ticket"]
    return ticket


class TestTicketFlow:
    """``POST /ticket`` then ``GET /stream?ticket=``."""

    async def test_ticket_opens_the_users_channel(self, stack: _Stack) -> None:
        """The stream subscribes to ``str(user.id)``, named by the ticket."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            issued = await client.post(
                "/sse/ticket", headers={"Authorization": f"Bearer {stack.access}"}
            )
            ticket = issued.json()["ticket"]
            response = await client.get("/sse/stream", params={"ticket": ticket})

        assert issued.json()["expires_in"] == 60
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text == f"data: {stack.user_id}\n\n"
        assert stack.broker.opened == [stack.user_id]

    async def test_a_ticket_opens_the_stream_more_than_once(
        self, stack: _Stack
    ) -> None:
        """Not single-use: within its lifetime it opens the stream again."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            ticket = await _ticket(client, stack.access)
            first = await client.get("/sse/stream", params={"ticket": ticket})
            second = await client.get("/sse/stream", params={"ticket": ticket})

        assert (first.status_code, second.status_code) == (200, 200)
        assert stack.broker.opened == [stack.user_id, stack.user_id]

    async def test_ticket_claims(self, stack: _Stack) -> None:
        """``typ="stream"``, the channel in ``ch``, a 60 s lifetime."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            ticket = await _ticket(client, stack.access)

        claims = stack.service.jwt.decode(ticket)

        assert claims["typ"] == STREAM_TOKEN_TYPE
        assert claims[SSE_CHANNEL_CLAIM] == stack.user_id
        assert claims["exp"] - claims["iat"] == 60
        assert timedelta(seconds=60) == DEFAULT_SSE_TICKET_TTL

    async def test_channel_for_picks_the_channel(self, stack: _Stack) -> None:
        """A product that streams per tenant maps the user to the tenant."""
        router = stack.service.sse_router(
            stack.broker, channel_for=lambda user: f"tenant:{user.email}"
        )
        async with _client(router) as client:
            ticket = await _ticket(client, stack.access)
            await client.get("/sse/stream", params={"ticket": ticket})

        assert stack.broker.opened == ["tenant:ana@example.com"]

    async def test_ticket_ttl_and_names(self, stack: _Stack) -> None:
        """Prefix, query parameter and lifetime are the caller's."""
        router = stack.service.sse_router(
            stack.broker,
            prefix="/events",
            ticket_param="t",
            ticket_ttl=timedelta(seconds=15),
        )
        async with _client(router) as client:
            issued = await client.post(
                "/events/ticket",
                headers={"Authorization": f"Bearer {stack.access}"},
            )
            response = await client.get(
                "/events/stream", params={"t": issued.json()["ticket"]}
            )

        assert issued.json()["expires_in"] == 15
        assert response.status_code == 200

    async def test_ticket_ttl_under_a_second_is_refused(self, stack: _Stack) -> None:
        """A zero lifetime would mint tickets that never open anything."""
        with pytest.raises(ValueError, match="at least one second"):
            stack.service.sse_router(
                stack.broker, ticket_ttl=timedelta(milliseconds=500)
            )

    async def test_zero_ticket_ttl_is_refused_not_defaulted(
        self, stack: _Stack
    ) -> None:
        """``timedelta(0)`` is falsy; it must not turn into 60 s."""
        with pytest.raises(ValueError, match="at least one second"):
            stack.service.sse_router(stack.broker, ticket_ttl=timedelta(0))

    async def test_compat_param_clashing_with_ticket_param(self, stack: _Stack) -> None:
        """Same name for both would leave the compat flag inert."""
        with pytest.raises(ValueError, match="must differ"):
            stack.service.sse_router(
                stack.broker,
                allow_access_token_query=True,
                ticket_param="access_token",
            )

    async def test_broker_factory(self, stack: _Stack) -> None:
        """A broker built in ``create_app`` is read per request."""
        router = stack.service.sse_router(lambda request: stack.broker)
        async with _client(router) as client:
            ticket = await _ticket(client, stack.access)
            response = await client.get("/sse/stream", params={"ticket": ticket})

        assert response.status_code == 200
        assert stack.broker.opened == [stack.user_id]


class TestTicketIssuance:
    """Who gets a ticket."""

    async def test_no_session_is_401(self, stack: _Stack) -> None:
        """The ticket is bought with a session."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            response = await client.post("/sse/ticket")

        assert response.status_code == 401

    async def test_inactive_account_is_403(self, stack: _Stack) -> None:
        """A deactivated account stops getting tickets at once."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            response = await client.post(
                "/sse/ticket",
                headers={"Authorization": f"Bearer {stack.inactive_access}"},
            )

        assert response.status_code == 403
        assert response.json()["code"] == "ACCOUNT_INACTIVE"

    async def test_refresh_token_buys_nothing(self, stack: _Stack) -> None:
        """The refresh token is not a session."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            response = await client.post(
                "/sse/ticket", headers={"Authorization": f"Bearer {stack.refresh}"}
            )

        assert response.status_code == 401

    async def test_a_ticket_does_not_buy_a_ticket(self, stack: _Stack) -> None:
        """A leaked ticket cannot be renewed into a longer-lived one."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            ticket = await _ticket(client, stack.access)
            response = await client.post(
                "/sse/ticket", headers={"Authorization": f"Bearer {ticket}"}
            )

        assert response.status_code == 401


class TestStreamRefusals:
    """What the stream route turns away."""

    @staticmethod
    async def _stream(stack: _Stack, **params: str) -> tuple[int, dict[str, Any]]:
        """Open the stream of the default router with ``params``.

        Args:
            stack (_Stack): The fixture.
            **params (str): Query parameters.

        Returns:
            tuple[int, dict[str, Any]]: Status and JSON body.
        """
        async with _client(stack.service.sse_router(stack.broker)) as client:
            response = await client.get("/sse/stream", params=params)
        return response.status_code, response.json()

    async def test_no_credential(self, stack: _Stack) -> None:
        """The case that answered 401 right after a successful login."""
        status, body = await self._stream(stack)

        assert status == 401
        assert body["detail"] == SSE_TICKET_ERROR
        assert stack.broker.opened == []

    async def test_access_token_in_the_url_is_refused_by_default(
        self, stack: _Stack
    ) -> None:
        """Without the compat flag, the URL never carries a session."""
        status, _ = await self._stream(stack, access_token=stack.access)

        assert status == 401
        assert stack.broker.opened == []

    async def test_access_token_as_ticket(self, stack: _Stack) -> None:
        """Signed with the same secret, but ``typ="access"``."""
        status, _ = await self._stream(stack, ticket=stack.access)

        assert status == 401

    async def test_channel_claim_with_another_typ(self, stack: _Stack) -> None:
        """The ``typ`` check stands on its own, not on the missing ``ch``."""
        token = stack.service.jwt.encode(
            {"typ": "access", SSE_CHANNEL_CLAIM: stack.user_id}
        )

        status, _ = await self._stream(stack, ticket=token)

        assert status == 401

    async def test_expired_ticket(self, stack: _Stack) -> None:
        """A reconnect after the TTL needs a fresh ticket."""
        expired = stack.service.jwt.encode(
            {"typ": STREAM_TOKEN_TYPE, SSE_CHANNEL_CLAIM: stack.user_id},
            ttl=timedelta(seconds=-1),
        )

        status, _ = await self._stream(stack, ticket=expired)

        assert status == 401

    async def test_forged_ticket(self, stack: _Stack) -> None:
        """A ticket signed with another secret names any channel it likes."""
        forged = JWTUtils("y" * 32).encode(
            {"typ": STREAM_TOKEN_TYPE, SSE_CHANNEL_CLAIM: stack.user_id}
        )

        status, _ = await self._stream(stack, ticket=forged)

        assert status == 401

    async def test_empty_ticket(self, stack: _Stack) -> None:
        """``?ticket=`` with nothing in it."""
        status, _ = await self._stream(stack, ticket="")

        assert status == 401

    async def test_deleted_account_is_401_not_404(self, stack: _Stack) -> None:
        """A valid token whose user is gone gets the same answer as no token."""
        assert stack.service.db is not None
        async with stack.service.db.get_session_context() as session:
            user = await stack.service.get_user(stack.user_id, session)
            await session.delete(user)

        async with _client(stack.service.sse_router(stack.broker)) as client:
            response = await client.get(
                "/sse/stream", headers={"Authorization": f"Bearer {stack.access}"}
            )

        assert response.status_code == 401
        assert stack.broker.opened == []

    async def test_ticket_without_channel(self, stack: _Stack) -> None:
        """Right ``typ``, no ``ch``: nothing to subscribe to."""
        ticket = stack.service.jwt.encode({"typ": STREAM_TOKEN_TYPE})

        status, _ = await self._stream(stack, ticket=ticket)

        assert status == 401

    async def test_bad_ticket_does_not_fall_back(self, stack: _Stack) -> None:
        """A present-but-bad ticket is refused even beside a valid header."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            response = await client.get(
                "/sse/stream",
                params={"ticket": "garbage"},
                headers={"Authorization": f"Bearer {stack.access}"},
            )

        assert response.status_code == 401
        assert stack.broker.opened == []


class TestTicketIsNotASession:
    """The point of the ticket: leaking it leaks one stream."""

    async def test_bearer_guard_refuses_a_ticket(self, stack: _Stack) -> None:
        """Every route guarded by ``current_user_dependency`` answers 401."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            ticket = await _ticket(client, stack.access)

        app = FastAPI()
        register_exception_handlers(app)
        current_user = stack.service.current_user_dependency(query_param="access_token")

        @app.get("/me")
        async def me(user: Any = Depends(current_user)) -> dict[str, str]:
            """Echo the user.

            Args:
                user (Any): The authenticated user.

            Returns:
                dict[str, str]: The email.
            """
            return {"email": user.email}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            by_header = await client.get(
                "/me", headers={"Authorization": f"Bearer {ticket}"}
            )
            by_query = await client.get("/me", params={"access_token": ticket})

        assert by_header.status_code == 401
        assert by_query.status_code == 401

    async def test_bearer_token_dependency_refuses_a_ticket(
        self, stack: _Stack
    ) -> None:
        """The claims-only guard refuses it too, by header and by query."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            ticket = await _ticket(client, stack.access)

        app = FastAPI()
        register_exception_handlers(app)
        claims = make_bearer_token_dependency(
            stack.service.jwt, query_param="access_token"
        )

        @app.get("/claims")
        async def read_claims(
            payload: dict[str, Any] | None = Depends(claims),
        ) -> dict[str, Any] | None:
            """Echo the decoded claims.

            Args:
                payload (dict[str, Any] | None): The claims.

            Returns:
                dict[str, Any] | None: The claims.
            """
            return payload

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            by_header = await client.get(
                "/claims", headers={"Authorization": f"Bearer {ticket}"}
            )
            by_query = await client.get("/claims", params={"access_token": ticket})
            access = await client.get(
                "/claims", headers={"Authorization": f"Bearer {stack.access}"}
            )

        assert by_header.status_code == 401
        assert by_query.status_code == 401
        assert access.status_code == 200


class TestFallbacks:
    """Clients that do not use the ticket."""

    async def test_bearer_header_opens_the_stream(self, stack: _Stack) -> None:
        """A client that can send a header skips the round trip."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            response = await client.get(
                "/sse/stream", headers={"Authorization": f"Bearer {stack.access}"}
            )

        assert response.status_code == 200
        assert stack.broker.opened == [stack.user_id]

    async def test_compat_flag_accepts_the_access_token_query(
        self, stack: _Stack
    ) -> None:
        """The client already shipped against ``?access_token=`` keeps working."""
        router = stack.service.sse_router(stack.broker, allow_access_token_query=True)
        async with _client(router) as client:
            response = await client.get(
                "/sse/stream", params={"access_token": stack.access}
            )

        assert response.status_code == 200
        assert stack.broker.opened == [stack.user_id]

    async def test_compat_flag_still_refuses_an_inactive_account(
        self, stack: _Stack
    ) -> None:
        """The compat path keeps the active-account check."""
        router = stack.service.sse_router(stack.broker, allow_access_token_query=True)
        async with _client(router) as client:
            response = await client.get(
                "/sse/stream", params={"access_token": stack.inactive_access}
            )

        assert response.status_code == 401
        assert stack.broker.opened == []

    async def test_compat_flag_refuses_the_refresh_token(self, stack: _Stack) -> None:
        """Only the access token stands in for a ticket."""
        router = stack.service.sse_router(stack.broker, allow_access_token_query=True)
        async with _client(router) as client:
            response = await client.get(
                "/sse/stream", params={"access_token": stack.refresh}
            )

        assert response.status_code == 401

    async def test_generic_router_without_fallback_takes_only_tickets(
        self, stack: _Stack
    ) -> None:
        """``make_sse_router`` with no ``fallback_user``: ticket or 401."""
        router = make_sse_router(
            stack.broker,
            tokens=stack.service.jwt,
            current_user=stack.service.current_user_dependency(),
        )
        async with _client(router) as client:
            ticket = await _ticket(client, stack.access)
            by_header = await client.get(
                "/sse/stream", headers={"Authorization": f"Bearer {stack.access}"}
            )
            by_ticket = await client.get("/sse/stream", params={"ticket": ticket})

        assert by_header.status_code == 401
        assert by_ticket.status_code == 200
        assert stack.broker.opened == [stack.user_id]


class TestStreamHoldsNoSession:
    """An open stream must not pin a database connection.

    A request-scoped session closes when the response ends, and an SSE
    response ends when the client leaves. Measured on fastapi 0.141.1
    with the stream guarded by ``current_user_dependency(session_dependency=
    ...)``: one session stayed open per connected client, on the ticket
    path too, until the disconnect.
    """

    @staticmethod
    async def _sessions_while_streaming(
        stack: _Stack, query: bytes, headers: list[tuple[bytes, bytes]]
    ) -> tuple[int, int]:
        """Open a real stream, read its first frame, count open sessions.

        ``ASGITransport`` buffers the body until the stream ends, so the
        app is driven through raw ASGI: the test holds the connection
        open, reads a heartbeat, counts, then disconnects.

        Args:
            stack (_Stack): The fixture.
            query (bytes): Raw query string.
            headers (list[tuple[bytes, bytes]]): Raw request headers.

        Returns:
            tuple[int, int]: Sessions open while streaming, and after the
            disconnect.
        """
        assert stack.service.db is not None
        db = stack.service.db
        open_sessions: list[int] = [0]

        async def get_session() -> AsyncIterator[Any]:
            """Yield a counted request session.

            Yields:
                Any: The session.
            """
            async with db.get_session_context() as session:
                open_sessions[0] += 1
                try:
                    yield session
                finally:
                    open_sessions[0] -= 1

        broker = SSEBroker(heartbeat_seconds=0.05)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            stack.service.sse_router(
                broker,
                session_dependency=get_session,
                allow_access_token_query=True,
            )
        )
        first_frame = asyncio.Event()
        disconnect = asyncio.Event()
        status: list[int] = []

        async def receive() -> dict[str, Any]:
            """Block until the test disconnects.

            Returns:
                dict[str, Any]: ``http.disconnect``.
            """
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            """Record the status and the first body frame.

            Args:
                message (MutableMapping[str, Any]): The ASGI message.
            """
            if message["type"] == "http.response.start":
                status.append(message["status"])
            elif message.get("body"):
                first_frame.set()

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/sse/stream",
            "raw_path": b"/sse/stream",
            "query_string": query,
            "headers": headers,
            "client": ("t", 1),
            "server": ("t", 80),
            "root_path": "",
        }
        task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(first_frame.wait(), 5)
        while_streaming = open_sessions[0]
        disconnect.set()
        await asyncio.wait_for(task, 5)
        assert status == [200]
        return while_streaming, open_sessions[0]

    async def test_ticket(self, stack: _Stack) -> None:
        """The ticket path never opens a session."""
        async with _client(stack.service.sse_router(stack.broker)) as client:
            ticket = await _ticket(client, stack.access)

        counts = await self._sessions_while_streaming(
            stack, f"ticket={ticket}".encode(), []
        )

        assert counts == (0, 0)

    async def test_bearer_header(self, stack: _Stack) -> None:
        """The header fallback loads the user and closes its session."""
        counts = await self._sessions_while_streaming(
            stack, b"", [(b"authorization", f"Bearer {stack.access}".encode())]
        )

        assert counts == (0, 0)

    async def test_access_token_query(self, stack: _Stack) -> None:
        """The compat path closes its session too."""
        counts = await self._sessions_while_streaming(
            stack, f"access_token={stack.access}".encode(), []
        )

        assert counts == (0, 0)


class TestCookieDelivery:
    """Same origin: the login cookie authenticates both routes."""

    async def test_login_cookie_buys_a_ticket_and_opens_the_stream(
        self, stack: _Stack
    ) -> None:
        """``AUTH_TOKEN_DELIVERY="cookie"`` needs no wiring on the router."""
        assert stack.service.db is not None
        cookie_service = UserAuthService(
            db=stack.service.db,
            user_model=_SSEUser,
            token_model=_SSEUserToken,
            auth_settings=AuthSettings(_env_file=None, AUTH_TOKEN_DELIVERY="cookie"),
            jwt_settings=JWTSettings(_env_file=None, JWT_SECRET=SECRET),
        )
        cookie = {cookie_service.auth_settings.AUTH_ACCESS_COOKIE_NAME: stack.access}
        async with _client(cookie_service.sse_router(stack.broker)) as client:
            client.cookies.update(cookie)
            issued = await client.post("/sse/ticket")
            streamed = await client.get("/sse/stream")

        assert issued.status_code == 200
        assert streamed.status_code == 200
        assert stack.broker.opened == [stack.user_id]


class TestOpenStreamOutlivesTheTicket:
    """The ticket is checked when the stream opens, not while it runs."""

    async def test_stream_keeps_flowing_after_expiry(self, stack: _Stack) -> None:
        """A 1 s ticket; the stream still delivers 2 s later."""
        broker = SSEBroker(heartbeat_seconds=0.05)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(stack.service.sse_router(broker))
        ticket = stack.service.jwt.encode(
            {"typ": STREAM_TOKEN_TYPE, SSE_CHANNEL_CLAIM: stack.user_id},
            ttl=timedelta(seconds=1),
        )
        frames: list[bytes] = []
        disconnect = asyncio.Event()

        async def receive() -> dict[str, Any]:
            """Block until the test disconnects.

            Returns:
                dict[str, Any]: ``http.disconnect``.
            """
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            """Collect body frames.

            Args:
                message (MutableMapping[str, Any]): The ASGI message.
            """
            if message.get("body"):
                frames.append(message["body"])

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/sse/stream",
            "raw_path": b"/sse/stream",
            "query_string": f"ticket={ticket}".encode(),
            "headers": [],
            "client": ("t", 1),
            "server": ("t", 80),
            "root_path": "",
        }
        task = asyncio.create_task(app(scope, receive, send))
        await asyncio.sleep(2.1)
        before = len(frames)
        await broker.publish(stack.user_id, {"late": True}, event="late")
        await asyncio.sleep(0.2)
        late = b"".join(frames[before:])
        disconnect.set()
        await asyncio.wait_for(task, 5)

        assert stack.service.jwt.decode_or_none(ticket) is None
        assert b"event: late" in late
