"""Auth emails sent after the response, opt-in (#644).

With ``AUTH_SEND_EMAILS_IN_BACKGROUND`` the auth routes hand the send to the
request's ``BackgroundTasks``. Pinned here: the default still awaits the
send (``503`` on failure), the opt-in answers without it and logs the
failure, a call from outside a request still awaits, and — over a real
server, because the ASGI test transport runs background tasks before it
returns — the response time stops depending on how long SMTP takes.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    EmailUtils,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions import EmailDeliveryException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
SMTP_DELAY_SECONDS: float = 2.0
"""How long the slow mailer holds every send."""


class _BgUser(BaseUserModel):
    __tablename__ = "bg_email_users"


_BgUserToken = make_user_token_model(
    user_table="bg_email_users",
    tablename="bg_email_user_tokens",
    class_name="_BgUserToken",
)


class Mailer(EmailUtils):
    """An ``EmailUtils`` whose send records, waits or fails, without SMTP."""

    def __init__(self, *, fail: bool = False, delay: float = 0.0) -> None:
        """Configure the behaviour of :meth:`send`.

        Args:
            fail (bool): Raise ``EmailDeliveryException`` on every send.
            delay (float): Seconds every send takes.
        """
        super().__init__("127.0.0.1", 1, from_addr="noreply@acme.com.br")
        self.fail = fail
        self.delay = delay
        self.sent: list[str] = []

    async def send(self, to: Any, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        """Record the recipient, after waiting, or fail.

        Args:
            to (Any): The recipient.
            *args (Any): Ignored.
            **kwargs (Any): Ignored.

        Raises:
            EmailDeliveryException: When built with ``fail=True``.
        """
        await asyncio.sleep(self.delay)
        if self.fail:
            raise EmailDeliveryException()
        self.sent.append(str(to))


def build_service(mailer: Mailer, *, background: bool) -> UserAuthService:
    """Build a service over the test models.

    Args:
        mailer (Mailer): The mailer.
        background (bool): ``AUTH_SEND_EMAILS_IN_BACKGROUND``.

    Returns:
        UserAuthService: The service.
    """
    return UserAuthService(
        user_model=_BgUser,
        token_model=_BgUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(AUTH_SEND_EMAILS_IN_BACKGROUND=background),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=mailer,
    )


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with built.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    yield built
    await built.dispose()


def build_app(engine: AsyncEngine, service: UserAuthService) -> FastAPI:
    """Mount the auth router over ``engine``.

    Args:
        engine (AsyncEngine): The database.
        service (UserAuthService): The service.

    Returns:
        FastAPI: The app.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with factory() as opened:
            yield opened

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_auth_router(service, session_factory=session_dependency))
    return app


def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


SIGNUP: dict[str, str] = {"email": "ana@acme.com.br", "password": PASSWORD}


async def test_default_still_awaits_and_answers_503(engine: AsyncEngine) -> None:
    app = build_app(engine, build_service(Mailer(fail=True), background=False))

    async with client(app) as http:
        response = await http.post("/auth/signup", json=SIGNUP)

    assert response.status_code == 503
    assert response.json()["code"] == "EMAIL_UNAVAILABLE"


async def test_background_sends_after_the_response(engine: AsyncEngine) -> None:
    mailer = Mailer()
    app = build_app(engine, build_service(mailer, background=True))

    async with client(app) as http:
        response = await http.post("/auth/signup", json=SIGNUP)

    assert response.status_code == 201
    assert mailer.sent == ["ana@acme.com.br"]


async def test_background_failure_is_logged_not_503(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    app = build_app(engine, build_service(Mailer(fail=True), background=True))

    with caplog.at_level(
        logging.ERROR, logger="tempest_fastapi_sdk.auth.email_delivery"
    ):
        async with client(app) as http:
            response = await http.post("/auth/signup", json=SIGNUP)

    assert response.status_code == 201
    messages = [record.getMessage() for record in caplog.records]
    assert any("'activation'" in m and "@acme.com.br" in m for m in messages)
    assert not any("ana@acme.com.br" in m for m in messages)


async def test_outside_a_request_the_send_is_awaited(engine: AsyncEngine) -> None:
    service = build_service(Mailer(fail=True), background=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        with pytest.raises(EmailDeliveryException):
            await service.signup(session, email="bia@acme.com.br", password=PASSWORD)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def served(engine: AsyncEngine, request: pytest.FixtureRequest) -> Iterator[str]:
    """Serve the app on a real socket and yield its base URL.

    Args:
        engine (AsyncEngine): The database.
        request (pytest.FixtureRequest): Carries ``background`` as param.

    Yields:
        str: ``http://127.0.0.1:<port>``.
    """
    service = build_service(Mailer(delay=SMTP_DELAY_SECONDS), background=request.param)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            build_app(engine, service), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.mark.parametrize("served", [False, True], indirect=True)
def test_response_time_stops_depending_on_smtp(
    served: str, request: pytest.FixtureRequest
) -> None:
    background = request.node.callspec.params["served"]
    started = time.monotonic()
    response = httpx.post(f"{served}/auth/signup", json=SIGNUP, timeout=10)
    elapsed = time.monotonic() - started

    assert response.status_code == 201
    if background:
        assert elapsed < SMTP_DELAY_SECONDS
    else:
        assert elapsed >= SMTP_DELAY_SECONDS
