"""The way back in when the activation email never arrived.

Signup mails the activation link once. When it does not land, every other
door is shut: signing up again conflicts on the email, logging in is
refused because the account is inactive, and `/auth/email-verify/request`
— the route whose own description offers itself for this case — needs a
bearer token that only a successful login hands out. The account was
unreachable without database access.

`POST /auth/activation/request` is that door, and these tests pin both
halves of it: it works without credentials, and it says nothing about the
address to whoever asks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _ArUser(BaseUserModel):
    __tablename__ = "ar_test_users"


_ArUserToken = make_user_token_model(
    user_table="ar_test_users",
    tablename="ar_test_user_tokens",
    class_name="_ArUserToken",
)

PASSWORD: str = "strong-pass-12-chars"


class _FakeEmail:
    """Records recipients + subjects instead of hitting SMTP."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def render_template(
        self,
        template: str,
        context: dict[str, Any],
        *,
        locale: str | None = None,
    ) -> str:
        """Return a stand-in body.

        Args:
            template (str): Template name.
            context (dict[str, Any]): Render context.
            locale (str | None): Negotiated locale.

        Returns:
            str: A marker carrying the template name.
        """
        return f"<html>{template}</html>"

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        html: str | None = None,
    ) -> None:
        """Record the delivery.

        Args:
            to (str): Recipient.
            subject (str): Subject line.
            body (str): Plain-text body.
            html (str | None): HTML body.
        """
        self.sent.append((to, subject))


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened
    await engine.dispose()


def _service(*, return_token: bool = True, email: Any = None) -> UserAuthService:
    """Build a service whose signups require activation.

    Args:
        return_token (bool): Whether links are surfaced in responses.
        email (Any): Optional `EmailUtils` stand-in.

    Returns:
        UserAuthService: The configured service.
    """
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=False,
        AUTH_RETURN_TOKEN_IN_RESPONSE=return_token,
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_ArUser,
        token_model=_ArUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=email,
    )


class TestRequestActivation:
    """The service method, straight."""

    async def test_a_pending_account_gets_a_fresh_link(
        self, session: AsyncSession
    ) -> None:
        """The whole point: a second link for an account that never activated."""
        service = _service()
        await service.signup(session, email="ana@example.com", password=PASSWORD)
        await session.commit()

        token = await service.request_activation(session, email="ana@example.com")
        await session.commit()

        assert token is not None
        user = await service.activate(session, token=token.token)
        assert user.is_active is True

    async def test_the_email_is_matched_case_insensitively(
        self, session: AsyncSession
    ) -> None:
        """People type their address the way they remember it."""
        service = _service()
        await service.signup(session, email="ana@example.com", password=PASSWORD)
        await session.commit()

        token = await service.request_activation(session, email="  ANA@Example.com ")

        assert token is not None

    async def test_an_active_account_is_not_re_mailed(
        self, session: AsyncSession
    ) -> None:
        """A link that would authorize nothing, and a leak if it were sent."""
        mailer = _FakeEmail()
        service = _service(return_token=False, email=mailer)
        user, activation = await service.signup(
            session, email="ana@example.com", password=PASSWORD
        )
        assert activation is not None
        await service.activate(session, token=activation.token)
        await session.commit()
        mailer.sent.clear()

        token = await service.request_activation(session, email="ana@example.com")

        assert token is None
        assert mailer.sent == []
        assert user.is_active is True

    async def test_an_unknown_address_is_silent(self, session: AsyncSession) -> None:
        """No row, no token, no mail — and no exception to time."""
        mailer = _FakeEmail()
        service = _service(return_token=False, email=mailer)

        token = await service.request_activation(session, email="ghost@example.com")

        assert token is None
        assert mailer.sent == []

    async def test_the_link_is_emailed_when_smtp_is_wired(
        self, session: AsyncSession
    ) -> None:
        """In production the link travels by email, not in the body."""
        mailer = _FakeEmail()
        service = _service(return_token=False, email=mailer)
        await service.signup(session, email="ana@example.com", password=PASSWORD)
        await session.commit()
        mailer.sent.clear()

        token = await service.request_activation(session, email="ana@example.com")

        assert token is None
        assert [to for to, _subject in mailer.sent] == ["ana@example.com"]


class TestActivationRequestRoute:
    """The route, which is what the stuck person actually reaches."""

    async def _client(self, service: UserAuthService) -> AsyncClient:
        """Build a client over an app mounting the auth router.

        Args:
            service: The service to mount.

        Returns:
            AsyncClient: Driving the app over ASGI.
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def _session() -> AsyncIterator[AsyncSession]:
            async with factory() as opened:
                yield opened

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_session))
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_it_needs_no_credentials(self) -> None:
        """Login is refused while inactive, so auth here would be a dead end."""
        service = _service()
        async with await self._client(service) as client:
            signup = await client.post(
                "/auth/signup",
                json={"email": "ana@example.com", "password": PASSWORD},
            )
            assert signup.status_code == 201
            assert signup.json()["activation_required"] is True

            login = await client.post(
                "/auth/login",
                json={"email": "ana@example.com", "password": PASSWORD},
            )
            assert login.status_code == 401

            resend = await client.post(
                "/auth/activation/request", json={"email": "ana@example.com"}
            )
            assert resend.status_code == 202
            url = resend.json()["activation_url"]
            assert url is not None
            token = parse_qs(urlparse(url).query)["token"][0]

            activated = await client.post(f"/auth/activate/{token}")
            assert activated.status_code == 200

            after = await client.post(
                "/auth/login",
                json={"email": "ana@example.com", "password": PASSWORD},
            )
            assert after.status_code == 200

    async def test_it_does_not_enumerate_accounts(self) -> None:
        """Unknown, active and pending answer the same status and sentence."""
        mailer = _FakeEmail()
        service = _service(return_token=False, email=mailer)
        async with await self._client(service) as client:
            await client.post(
                "/auth/signup",
                json={"email": "pending@example.com", "password": PASSWORD},
            )

            answers = [
                await client.post("/auth/activation/request", json={"email": address})
                for address in (
                    "pending@example.com",
                    "ghost@example.com",
                )
            ]

        assert {answer.status_code for answer in answers} == {202}
        assert len({answer.json()["message"] for answer in answers}) == 1
        assert all(answer.json()["activation_url"] is None for answer in answers)
