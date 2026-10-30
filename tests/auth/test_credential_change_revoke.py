"""Password change and e-mail change revoke the refresh tokens left behind.

``confirm_password_reset``, ``mfa_confirm`` and ``change_password_with_mfa``
already revoked every session of the user by default. ``change_password``
(``POST /auth/password-change``) and ``confirm_email_change`` (``POST
/auth/email-change/confirm`` and the backend-links page) did not: a refresh
token minted before the change kept rotating into fresh access tokens.
Both now revoke by default, with ``revoke_sessions=False`` as the explicit
opt-out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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
    MFA_AMR,
    PASSWORD_AMR,
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"


class _CcUser(BaseUserModel):
    __tablename__ = "cred_change_test_users"


_CcUserToken = make_user_token_model(
    user_table="cred_change_test_users",
    tablename="cred_change_test_user_tokens",
    class_name="_CcUserToken",
)
_CcRefreshToken = make_user_refresh_token_model(
    user_table="cred_change_test_users",
    tablename="cred_change_test_refresh_tokens",
    class_name="_CcRefreshToken",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory database.

    Yields:
        AsyncSession: The session every request of a test shares.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened
    await engine.dispose()


def _service() -> UserAuthService:
    """Build a service with DB-backed refresh tokens and links in the body.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_CcUser,
        token_model=_CcUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            AUTH_EMAIL_CHANGE_NOTIFY_OLD=False,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        refresh_token_model=_CcRefreshToken,  # type: ignore[arg-type]
    )


def _client(service: UserAuthService, session: AsyncSession) -> AsyncClient:
    """Mount the auth router on a throwaway app.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session every request shares.

    Returns:
        AsyncClient: A client bound to the app over ASGI.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_auth_router(service, session_factory=_factory))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _login(client: AsyncClient) -> dict[str, str]:
    """Log in and return the response body.

    Args:
        client (AsyncClient): The client.

    Returns:
        dict[str, str]: The token pair and the user id.
    """
    response = await client.post(
        "/auth/login", json={"email": "ana@acme.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    body: dict[str, str] = response.json()
    return body


async def _signup(service: UserAuthService, session: AsyncSession) -> BaseUserModel:
    """Create the active account ``ana@acme.com``.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session to write through.

    Returns:
        BaseUserModel: The committed user.
    """
    user, _ = await service.signup(session, email="ana@acme.com", password=PASSWORD)
    await session.commit()
    return user


class TestRoutesRevoke:
    async def test_password_change_refuses_the_older_refresh_token(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await _signup(service, session)
        async with _client(service, session) as client:
            stolen = (await _login(client))["refresh_token"]
            own = await _login(client)
            changed = await client.post(
                "/auth/password-change",
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
                headers={"Authorization": f"Bearer {own['access_token']}"},
            )
            replay = await client.post("/auth/refresh", json={"refresh_token": stolen})
        assert changed.status_code == 200
        assert replay.status_code == 401

    async def test_email_change_refuses_the_older_refresh_token(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await _signup(service, session)
        async with _client(service, session) as client:
            stolen = (await _login(client))["refresh_token"]
            own = await _login(client)
            requested = await client.post(
                "/auth/email-change/request",
                json={"current_password": PASSWORD, "new_email": "ana2@acme.com"},
                headers={"Authorization": f"Bearer {own['access_token']}"},
            )
            token = parse_qs(urlparse(requested.json()["confirm_url"]).query)
            confirmed = await client.post(
                "/auth/email-change/confirm", json={"token": token["token"][0]}
            )
            replay = await client.post("/auth/refresh", json={"refresh_token": stolen})
        assert confirmed.status_code == 200
        assert replay.status_code == 401


class TestPasswordChangeReissuesThePair:
    """The change ends every session, so the caller gets a new pair (#662)."""

    async def test_the_response_carries_a_pair_that_rotates(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await _signup(service, session)
        async with _client(service, session) as client:
            own = await _login(client)
            changed = await client.post(
                "/auth/password-change",
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
                headers={"Authorization": f"Bearer {own['access_token']}"},
            )
            body = changed.json()
            old = await client.post(
                "/auth/refresh", json={"refresh_token": own["refresh_token"]}
            )
            new = await client.post(
                "/auth/refresh", json={"refresh_token": body["refresh_token"]}
            )
            me = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
            )
        assert changed.status_code == 200, changed.text
        assert body["user_id"] == own["user_id"]
        assert body["access_token"]
        assert body["refresh_token"] != own["refresh_token"]
        assert old.status_code == 401
        assert new.status_code == 200, new.text
        assert me.status_code == 200, me.text

    async def test_the_pair_repeats_the_amr_of_the_caller(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        user = await _signup(service, session)
        access, _ = await service.issue_token_pair(session, user, amr=MFA_AMR)
        await session.commit()
        async with _client(service, session) as client:
            changed = await client.post(
                "/auth/password-change",
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
                headers={"Authorization": f"Bearer {access}"},
            )
        claims = service.jwt.decode(changed.json()["access_token"])
        assert changed.status_code == 200, changed.text
        assert tuple(claims["amr"]) == MFA_AMR

    async def test_a_password_session_stays_a_password_session(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await _signup(service, session)
        async with _client(service, session) as client:
            own = await _login(client)
            changed = await client.post(
                "/auth/password-change",
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
                headers={"Authorization": f"Bearer {own['access_token']}"},
            )
        claims = service.jwt.decode(changed.json()["access_token"])
        assert tuple(claims["amr"]) == PASSWORD_AMR

    async def test_a_wrong_password_issues_nothing(self, session: AsyncSession) -> None:
        service = _service()
        await _signup(service, session)
        async with _client(service, session) as client:
            own = await _login(client)
            changed = await client.post(
                "/auth/password-change",
                json={
                    "current_password": "not-it-at-all",
                    "new_password": NEW_PASSWORD,
                },
                headers={"Authorization": f"Bearer {own['access_token']}"},
            )
            still = await client.post(
                "/auth/refresh", json={"refresh_token": own["refresh_token"]}
            )
        assert changed.status_code == 401
        assert "access_token" not in changed.text
        assert still.status_code == 200

    def test_the_openapi_documents_the_pair(self, session: AsyncSession) -> None:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(_service(), session_factory=_factory))
        operation = app.openapi()["paths"]["/auth/password-change"]["post"]
        assert "204" not in operation["responses"]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/LoginResponseSchema")


class TestServiceOptOut:
    async def test_change_password_opt_out_keeps_the_session(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        user = await _signup(service, session)
        _, refresh = await service.issue_token_pair(session, user)
        await session.commit()
        await service.change_password(
            session,
            user=user,
            current_password=PASSWORD,
            new_password=NEW_PASSWORD,
            revoke_sessions=False,
        )
        await session.commit()
        _, _, rotated = await service.refresh_tokens(session, refresh_token=refresh)
        assert rotated

    async def test_change_password_default_revokes(self, session: AsyncSession) -> None:
        service = _service()
        user = await _signup(service, session)
        _, refresh = await service.issue_token_pair(session, user)
        await session.commit()
        await service.change_password(
            session, user=user, current_password=PASSWORD, new_password=NEW_PASSWORD
        )
        await session.commit()
        with pytest.raises(UnauthorizedException):
            await service.refresh_tokens(session, refresh_token=refresh)

    async def test_confirm_email_change_opt_out_keeps_the_session(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        user = await _signup(service, session)
        _, refresh = await service.issue_token_pair(session, user)
        bundle = await service.request_email_change(
            session, user=user, current_password=PASSWORD, new_email="ana2@acme.com"
        )
        await session.commit()
        assert bundle is not None
        await service.confirm_email_change(
            session, token=bundle.token, revoke_sessions=False
        )
        await session.commit()
        _, _, rotated = await service.refresh_tokens(session, refresh_token=refresh)
        assert rotated
