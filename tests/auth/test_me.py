"""``GET /auth/me`` — the account behind the bearer token.

Every project ends up writing this endpoint the same way, so the bundled
router ships it. The tests here pin the three things a consumer relies
on: it resolves the token to the right account, it never serializes the
password hash, and a project can widen the payload with its own response
model without touching the handler.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AuthUserSchema,
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _MeUser(BaseUserModel):
    __tablename__ = "me_users"

    display_name: Mapped[str | None] = mapped_column(
        String(120), nullable=True, default=None
    )


_MeUserToken = make_user_token_model(
    user_table="me_users",
    tablename="me_user_tokens",
    class_name="_MeUserToken",
)

EMAIL = "me@example.com"
PASSWORD = "strong-pass-12-chars"


class _WideUserSchema(AuthUserSchema):
    """A project's own response model, adding a column the base lacks."""

    display_name: str | None = None


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory over one shared in-memory database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _service() -> UserAuthService:
    """Build an auth service that activates signups immediately."""
    return UserAuthService(
        user_model=_MeUser,
        token_model=_MeUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )


def _client(
    service: UserAuthService,
    factory: async_sessionmaker[AsyncSession],
    **router_kwargs: object,
) -> AsyncClient:
    """Mount the auth router over a per-request session factory."""

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session
            await session.commit()

    app = FastAPI()
    app.include_router(
        make_auth_router(service, session_factory=sessions, **router_kwargs)  # type: ignore[arg-type]
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _seed(
    service: UserAuthService,
    factory: async_sessionmaker[AsyncSession],
    *,
    display_name: str | None = None,
) -> str:
    """Create an active account and return an access token for it."""
    async with factory() as session:
        user, _ = await service.signup(session, email=EMAIL, password=PASSWORD)
        if display_name is not None:
            user.display_name = display_name  # type: ignore[attr-defined]
        await session.commit()
        access, _ = service.issue_jwt_pair(user)
    return access


class TestMeResolvesTheToken:
    """The endpoint answers with the account owning the bearer token."""

    async def test_returns_the_authenticated_account(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        service = _service()
        access = await _seed(service, factory)

        async with _client(service, factory) as client:
            response = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {access}"}
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["email"] == EMAIL
        assert body["is_active"] is True
        assert body["is_admin"] is False

    async def test_rejects_a_missing_token(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        service = _service()

        async with _client(service, factory) as client:
            response = await client.get("/auth/me")

        assert response.status_code == 401, response.text

    async def test_rejects_a_token_signed_with_another_secret(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A token from a different deployment must not authenticate here."""
        service = _service()
        foreign = UserAuthService(
            user_model=_MeUser,
            token_model=_MeUserToken,  # type: ignore[arg-type]
            auth_settings=AuthSettings(AUTH_AUTO_ACTIVATE=True),
            jwt_settings=JWTSettings(JWT_SECRET="y" * 32),
        )
        async with factory() as session:
            user, _ = await foreign.signup(
                session, email="other@example.com", password=PASSWORD
            )
            await session.commit()
        access, _ = foreign.issue_jwt_pair(user)

        async with _client(service, factory) as client:
            response = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {access}"}
            )

        assert response.status_code == 401, response.text


class TestMeNeverLeaksTheHash:
    """The password hash must not reach the wire, by construction."""

    async def test_default_model_omits_the_password_hash(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The handler returns the whole ORM row; the response model filters.

        This is the assertion that keeps the default safe: the endpoint
        does not hand-pick fields, so only what ``AuthUserSchema``
        declares is serialized.
        """
        service = _service()
        access = await _seed(service, factory)

        async with _client(service, factory) as client:
            response = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {access}"}
            )

        body = response.json()
        assert "hashed_password" not in body
        assert set(body) == {
            "id",
            "is_active",
            "created_at",
            "updated_at",
            "email",
            "is_admin",
            "last_login_at",
        }

    async def test_a_wider_model_still_omits_it(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Subclassing to add a column must not reopen the hole."""
        service = _service()
        access = await _seed(service, factory, display_name="Ana")

        async with _client(
            service, factory, me_response_model=_WideUserSchema
        ) as client:
            response = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {access}"}
            )

        body = response.json()
        assert "hashed_password" not in body


class TestMeResponseModelIsOverridable:
    """A project widens the payload without touching the handler."""

    async def test_extra_column_is_serialized(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        service = _service()
        access = await _seed(service, factory, display_name="Ana")

        async with _client(
            service, factory, me_response_model=_WideUserSchema
        ) as client:
            response = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {access}"}
            )

        assert response.status_code == 200, response.text
        assert response.json()["display_name"] == "Ana"

    async def test_default_model_drops_the_extra_column(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Without the override, a project-specific column stays hidden."""
        service = _service()
        access = await _seed(service, factory, display_name="Ana")

        async with _client(service, factory) as client:
            response = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {access}"}
            )

        assert "display_name" not in response.json()
