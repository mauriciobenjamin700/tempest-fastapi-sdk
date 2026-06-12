"""Tests for ``UserAuthService`` + ``make_auth_router``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    InvalidTokenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _TestUser(BaseUserModel):
    __tablename__ = "auth_test_users"


_TestUserToken = make_user_token_model(
    user_table="auth_test_users",
    tablename="auth_test_user_tokens",
    class_name="_TestUserToken",
)


class _NamedUser(BaseUserModel):
    """User model that exposes a ``name`` column (signup writes it)."""

    __tablename__ = "auth_named_users"

    name: Mapped[str | None] = mapped_column(default=None)


_NamedUserToken = make_user_token_model(
    user_table="auth_named_users",
    tablename="auth_named_user_tokens",
    class_name="_NamedUserToken",
)


class _FakeEmail:
    """Minimal stand-in for ``EmailUtils`` that records what it sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def render_template(self, template: str, context: dict[str, Any]) -> str:
        """Return deterministic HTML so the send path can be asserted."""
        return f"<html>{template}</html>"

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        html: str | None = None,
    ) -> None:
        """Record the recipient + subject instead of hitting SMTP."""
        self.sent.append((to, subject))


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(
    *,
    auto_activate: bool = False,
    return_token: bool = True,
    email: Any = None,
) -> UserAuthService:
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=auto_activate,
        AUTH_RETURN_TOKEN_IN_RESPONSE=return_token,
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_TestUser,
        token_model=_TestUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=email,
    )


class TestSignup:
    async def test_creates_user_with_hashed_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, activation = await service.signup(
            session,
            email="ANA@example.com",
            password="strong-pass-12-chars",
        )
        assert user.email == "ana@example.com"  # normalized lowercase
        assert user.hashed_password != "strong-pass-12-chars"
        assert user.is_active is False
        assert activation is not None
        assert "token=" in activation.url

    async def test_auto_activate_returns_no_token(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        user, activation = await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        assert user.is_active is True
        assert activation is None

    async def test_duplicate_email_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await service.signup(
            session,
            email="dup@b.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        with pytest.raises(ConflictException):
            await service.signup(
                session,
                email="dup@b.com",
                password="strong-pass-12-chars",
            )

    async def test_short_password_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(ValidationException):
            await service.signup(
                session,
                email="a@b.com",
                password="short",
            )


def _service_minlen(minlen: int) -> UserAuthService:
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=True,
        AUTH_PASSWORD_MIN_LENGTH=minlen,
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_TestUser,
        token_model=_TestUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=None,
    )


def _service_complexity(minlen: int = 12) -> UserAuthService:
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=True,
        AUTH_PASSWORD_MIN_LENGTH=minlen,
        AUTH_PASSWORD_REQUIRE_COMPLEXITY=True,
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_TestUser,
        token_model=_TestUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=None,
    )


class TestPasswordPolicy:
    """AUTH_PASSWORD_MIN_LENGTH is the single source of truth."""

    async def test_setting_floor_of_8_accepts_8_char_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service_minlen(8)
        user, _ = await service.signup(
            session,
            email="floor8@a.com",
            password="abcdefgh",  # exactly 8
        )
        assert user.email == "floor8@a.com"

    async def test_floor_is_fully_customizable_below_8(
        self,
        session: AsyncSession,
    ) -> None:
        # ge=1 on the setting → a project can demand as few as 4 chars.
        service = _service_minlen(4)
        user, _ = await service.signup(
            session,
            email="floor4@a.com",
            password="abcd",  # exactly 4
        )
        assert user.email == "floor4@a.com"
        with pytest.raises(ValidationException):
            await service.signup(
                session,
                email="floor4-short@a.com",
                password="abc",  # 3 < 4
            )

    async def test_router_honors_floor_of_4(
        self,
        session: AsyncSession,
    ) -> None:
        # Regression: schema must not pin a min above the configured
        # floor — a 4-char password must reach the (min=4) service.
        service = _service_minlen(4)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/signup",
                json={"email": "router4@a.com", "password": "abcd"},
            )
        assert r.status_code == 201, r.text

    async def test_setting_floor_of_8_rejects_7_char_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service_minlen(8)
        with pytest.raises(ValidationException):
            await service.signup(
                session,
                email="short7@a.com",
                password="abcdefg",  # 7
            )

    async def test_raised_floor_rejects_password_below_it(
        self,
        session: AsyncSession,
    ) -> None:
        # 12 chars passes the schema's 8-char floor but the service
        # enforces the configured 16.
        service = _service_minlen(16)
        with pytest.raises(ValidationException):
            await service.signup(
                session,
                email="needs16@a.com",
                password="only-twelve!",  # 12
            )

    async def test_complexity_off_accepts_simple_password(
        self,
        session: AsyncSession,
    ) -> None:
        # Default: any password of the right length passes.
        service = _service_minlen(8)
        user, _ = await service.signup(
            session,
            email="simple@a.com",
            password="abcdefghij",  # all lowercase, no symbols
        )
        assert user.email == "simple@a.com"

    async def test_complexity_on_accepts_compliant_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service_complexity()
        user, _ = await service.signup(
            session,
            email="compliant@a.com",
            password="Abcdefghij1!",  # lower + upper + digit + special
        )
        assert user.email == "compliant@a.com"

    @pytest.mark.parametrize(
        ("password", "missing"),
        [
            ("abcdefghij1!", "uppercase"),  # no uppercase
            ("ABCDEFGHIJ1!", "lowercase"),  # no lowercase
            ("Abcdefghij!?", "digit"),  # no digit
            ("Abcdefghij12", "special"),  # no special char
        ],
    )
    async def test_complexity_on_rejects_missing_class(
        self,
        session: AsyncSession,
        password: str,
        missing: str,
    ) -> None:
        service = _service_complexity()
        with pytest.raises(ValidationException) as exc:
            await service.signup(
                session,
                email=f"{missing}@a.com",
                password=password,
            )
        assert missing in exc.value.details["missing_classes"]

    async def test_complexity_on_still_enforces_length_first(
        self,
        session: AsyncSession,
    ) -> None:
        # Too short trips the length check before complexity.
        service = _service_complexity()
        with pytest.raises(ValidationException) as exc:
            await service.signup(
                session,
                email="tooshort@a.com",
                password="Ab1!",  # complex but only 4 chars
            )
        assert exc.value.details.get("min_length") == 12

    async def test_complexity_on_forces_minimum_floor_of_8(
        self,
        session: AsyncSession,
    ) -> None:
        # Configured floor 4, but complexity mode raises it to 8.
        service = _service_complexity(minlen=4)
        with pytest.raises(ValidationException) as exc:
            await service.signup(
                session,
                email="weak@a.com",
                password="Ab1!",  # complex, 4 chars — below the forced 8
            )
        assert exc.value.details.get("min_length") == 8
        # An 8-char complex password is accepted under the forced floor.
        user, _ = await service.signup(
            session,
            email="ok8@a.com",
            password="Abcdef1!",  # 8, all four classes
        )
        assert user.email == "ok8@a.com"

    async def test_router_honors_lowered_floor(
        self,
        session: AsyncSession,
    ) -> None:
        # Regression: the SPA schema must not hardcode 12 — an 8-char
        # password must reach the (min=8) service and succeed (201),
        # not be rejected at the Pydantic layer (422).
        service = _service_minlen(8)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/signup",
                json={"email": "router8@a.com", "password": "abcdefgh"},
            )
        assert r.status_code == 201, r.text


class TestActivate:
    async def test_consumes_token_and_activates_user(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        _user, activation = await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        assert activation is not None

        activated = await service.activate(session, token=activation.token)
        assert activated.is_active is True

    async def test_reused_token_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        _user, activation = await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        assert activation is not None

        await service.activate(session, token=activation.token)
        with pytest.raises(InvalidTokenException):
            await service.activate(session, token=activation.token)

    async def test_unknown_token_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(InvalidTokenException):
            await service.activate(session, token="nope-never-issued")


class TestLogin:
    async def test_valid_credentials_returns_user(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        user = await service.login(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        assert user.email == "a@b.com"
        assert user.last_login_at is not None

    async def test_wrong_password_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        with pytest.raises(UnauthorizedException):
            await service.login(
                session,
                email="a@b.com",
                password="wrong-pass-12-chars",
            )

    async def test_inactive_user_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        with pytest.raises(UnauthorizedException):
            await service.login(
                session,
                email="a@b.com",
                password="strong-pass-12-chars",
            )


class TestPasswordReset:
    async def test_request_reset_returns_token_when_user_exists(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        token = await service.request_password_reset(session, email="a@b.com")
        assert token is not None
        assert "token=" in token.url

    async def test_request_reset_returns_none_for_unknown_email(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        token = await service.request_password_reset(
            session,
            email="ghost@nowhere.example",
        )
        assert token is None

    async def test_confirm_reset_rotates_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        token = await service.request_password_reset(session, email="a@b.com")
        assert token is not None

        await service.confirm_password_reset(
            session,
            token=token.token,
            new_password="brand-new-pass-12",
        )
        # Old password no longer works
        with pytest.raises(UnauthorizedException):
            await service.login(
                session,
                email="a@b.com",
                password="strong-pass-12-chars",
            )
        # New password works
        await service.login(
            session,
            email="a@b.com",
            password="brand-new-pass-12",
        )


class TestRouter:
    async def test_signup_endpoint_returns_activation_url(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/signup",
                json={
                    "email": "router@a.com",
                    "password": "strong-pass-12-chars",
                },
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["activation_required"] is True
        assert "token=" in body["activation_url"]
        assert body["access_token"] is None

    async def test_signup_endpoint_auto_activate_returns_tokens(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/signup",
                json={
                    "email": "auto@a.com",
                    "password": "strong-pass-12-chars",
                },
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["activation_required"] is False
        assert body["access_token"]
        assert body["refresh_token"]

    async def test_activate_endpoint_returns_tokens(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        _user, activation = await service.signup(
            session,
            email="activate-ep@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        assert activation is not None

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(f"/auth/activate/{activation.token}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"]
        assert body["refresh_token"]

    async def test_login_endpoint_returns_tokens_without_mfa(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="login-ep@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/login",
                json={
                    "email": "login-ep@a.com",
                    "password": "strong-pass-12-chars",
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mfa_required"] is False
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["mfa_token"] is None

    async def test_password_reset_confirm_endpoint_rotates_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="reset-confirm-ep@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        token = await service.request_password_reset(
            session,
            email="reset-confirm-ep@a.com",
        )
        await session.commit()
        assert token is not None

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/password-reset/confirm",
                json={
                    "token": token.token,
                    "new_password": "brand-new-pass-12",
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"]
        assert body["refresh_token"]

    async def test_password_reset_request_returns_url_for_known_email(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(return_token=True)
        await service.signup(
            session,
            email="known-reset@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/password-reset/request",
                json={"email": "known-reset@a.com"},
            )
        assert r.status_code == 202, r.text
        body = r.json()
        assert "matches an account" in body["message"]
        assert body["reset_url"] is not None
        assert "token=" in body["reset_url"]

    async def test_password_reset_request_never_leaks(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/password-reset/request",
                json={"email": "ghost@nowhere.example"},
            )
        assert r.status_code == 202
        body = r.json()
        # Generic 202 always — no leak of "user not found".
        assert "matches an account" in body["message"]
        assert body["reset_url"] is None


def _backend_service(
    *,
    auto_activate: bool = False,
    return_token: bool = True,
    login_url: str | None = "https://app.example.com/login",
) -> UserAuthService:
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=auto_activate,
        AUTH_RETURN_TOKEN_IN_RESPONSE=return_token,
        AUTH_BACKEND_LINKS=True,
        AUTH_LOGIN_URL=login_url,
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_TestUser,
        token_model=_TestUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=None,
    )


class TestBackendOnlyMode:
    """Backend-only HTML pages (``AUTH_BACKEND_LINKS=True``)."""

    async def test_get_activate_renders_success_page(
        self,
        session: AsyncSession,
    ) -> None:
        service = _backend_service()
        _user, activation = await service.signup(
            session,
            email="be@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        assert activation is not None

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get(f"/auth/activate/{activation.token}")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/html")
        assert "Account activated" in r.text
        assert "https://app.example.com/login" in r.text

    async def test_get_activate_invalid_token_renders_error(
        self,
        session: AsyncSession,
    ) -> None:
        service = _backend_service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/auth/activate/this-is-not-a-real-token")
        assert r.status_code == 400
        assert "Activation failed" in r.text

    async def test_get_password_reset_renders_form(
        self,
        session: AsyncSession,
    ) -> None:
        service = _backend_service(auto_activate=True)
        await service.signup(
            session,
            email="reset@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        token = await service.request_password_reset(
            session,
            email="reset@a.com",
        )
        await session.commit()
        assert token is not None

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get(f"/auth/password-reset/{token.token}")
        assert r.status_code == 200, r.text
        assert "Reset your password" in r.text
        assert f'action="/auth/password-reset/{token.token}"' in r.text

    async def test_get_password_reset_invalid_token_renders_error(
        self,
        session: AsyncSession,
    ) -> None:
        service = _backend_service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/auth/password-reset/bogus-token")
        assert r.status_code == 400
        assert "Password reset failed" in r.text

    async def test_post_password_reset_form_success(
        self,
        session: AsyncSession,
    ) -> None:
        service = _backend_service(auto_activate=True)
        await service.signup(
            session,
            email="postreset@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        token = await service.request_password_reset(
            session,
            email="postreset@a.com",
        )
        await session.commit()
        assert token is not None

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                f"/auth/password-reset/{token.token}",
                data={
                    "new_password": "different-pass-12",
                    "confirm_password": "different-pass-12",
                },
            )
        assert r.status_code == 200, r.text
        assert "Password updated" in r.text

    async def test_post_password_reset_form_mismatch_rerenders_form(
        self,
        session: AsyncSession,
    ) -> None:
        service = _backend_service(auto_activate=True)
        await service.signup(
            session,
            email="mismatch@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        token = await service.request_password_reset(
            session,
            email="mismatch@a.com",
        )
        await session.commit()
        assert token is not None

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                f"/auth/password-reset/{token.token}",
                data={
                    "new_password": "different-pass-12",
                    "confirm_password": "differentXXXXX-12",
                },
            )
        assert r.status_code == 400
        assert "Passwords do not match" in r.text

        # Token must not be consumed when the form is rejected.
        async def _factory2() -> AsyncIterator[AsyncSession]:
            yield session

        # Second submit with matching pair should succeed.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r2 = await c.post(
                f"/auth/password-reset/{token.token}",
                data={
                    "new_password": "different-pass-12",
                    "confirm_password": "different-pass-12",
                },
            )
        assert r2.status_code == 200, r2.text

    async def test_post_password_reset_form_short_password_rerenders(
        self,
        session: AsyncSession,
    ) -> None:
        service = _backend_service(auto_activate=True)
        await service.signup(
            session,
            email="shortpw@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        token = await service.request_password_reset(session, email="shortpw@a.com")
        await session.commit()
        assert token is not None

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                f"/auth/password-reset/{token.token}",
                data={"new_password": "short", "confirm_password": "short"},
            )
        assert r.status_code == 400, r.text
        # Form is re-rendered (not the success page), token NOT consumed.
        assert f'action="/auth/password-reset/{token.token}"' in r.text

        # Token survived the rejected submit — a valid retry still works.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r2 = await c.post(
                f"/auth/password-reset/{token.token}",
                data={
                    "new_password": "brand-new-pass-12",
                    "confirm_password": "brand-new-pass-12",
                },
            )
        assert r2.status_code == 200, r2.text
        assert "Password updated" in r2.text

    async def test_post_reset_form_mismatch_with_bad_token_renders_error(
        self,
        session: AsyncSession,
    ) -> None:
        # Passwords mismatch AND the token is invalid → the nested
        # peek_token fails and the error page is rendered (not the form).
        service = _backend_service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/password-reset/bogus-token",
                data={
                    "new_password": "aaaaaaaaaaaa",
                    "confirm_password": "bbbbbbbbbbbb",
                },
            )
        assert r.status_code == 400, r.text
        assert "Password reset failed" in r.text

    async def test_post_reset_form_matching_with_bad_token_renders_error(
        self,
        session: AsyncSession,
    ) -> None:
        # Matching passwords but invalid token → confirm raises and the
        # error page is rendered.
        service = _backend_service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/password-reset/bogus-token",
                data={
                    "new_password": "brand-new-pass-12",
                    "confirm_password": "brand-new-pass-12",
                },
            )
        assert r.status_code == 400, r.text
        assert "Password reset failed" in r.text

    async def test_post_reset_form_short_password_with_bad_token_renders_error(
        self,
        session: AsyncSession,
    ) -> None:
        # Matching but too-short password raises ValidationException; the
        # nested peek with the invalid token also fails → error page.
        service = _backend_service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/password-reset/bogus-token",
                data={"new_password": "short", "confirm_password": "short"},
            )
        assert r.status_code == 400, r.text
        assert "Password reset failed" in r.text

    async def test_get_activate_does_not_mount_when_backend_links_disabled(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()  # AUTH_BACKEND_LINKS default False

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/auth/activate/anything")
        assert r.status_code == 405  # POST exists, GET not mounted


def _named_service(*, email: Any = None, return_token: bool = True) -> UserAuthService:
    auth = AuthSettings(AUTH_RETURN_TOKEN_IN_RESPONSE=return_token)
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_NamedUser,
        token_model=_NamedUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=email,
    )


class TestServiceEdgeCases:
    """Non-happy-path branches of UserAuthService."""

    async def test_signup_writes_name_when_model_has_column(
        self,
        session: AsyncSession,
    ) -> None:
        service = _named_service()
        user, _ = await service.signup(
            session,
            email="named@a.com",
            password="strong-pass-12-chars",
            name="Ana Silva",
        )
        assert user.name == "Ana Silva"

    async def test_activate_token_for_deleted_user_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, activation = await service.signup(
            session,
            email="ghost-activate@a.com",
            password="strong-pass-12-chars",
        )
        assert activation is not None
        await session.delete(user)
        await session.flush()
        with pytest.raises(InvalidTokenException):
            await service.activate(session, token=activation.token)

    async def test_confirm_reset_for_deleted_user_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        user, _ = await service.signup(
            session,
            email="ghost-reset@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        token = await service.request_password_reset(session, email="ghost-reset@a.com")
        assert token is not None
        await session.delete(user)
        await session.flush()
        with pytest.raises(NotFoundException):
            await service.confirm_password_reset(
                session,
                token=token.token,
                new_password="brand-new-pass-12",
            )

    async def test_peek_token_for_deleted_user_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        from tempest_fastapi_sdk.db.user_token_model import UserTokenPurpose

        service = _backend_service(auto_activate=True)
        user, _ = await service.signup(
            session,
            email="ghost-peek@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        token = await service.request_password_reset(session, email="ghost-peek@a.com")
        assert token is not None
        await session.delete(user)
        await session.flush()
        with pytest.raises(NotFoundException):
            await service.peek_token(
                session,
                token=token.token,
                purpose=UserTokenPurpose.PASSWORD_RESET,
            )

    async def test_expired_token_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        _user, activation = await service.signup(
            session,
            email="expired@a.com",
            password="strong-pass-12-chars",
        )
        assert activation is not None
        # Backdate the token so the expiry check trips.
        record = (await session.execute(select(_TestUserToken))).scalars().first()
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await session.flush()
        with pytest.raises(InvalidTokenException):
            await service.activate(session, token=activation.token)

    async def test_signup_sends_activation_email_when_wired(
        self,
        session: AsyncSession,
    ) -> None:
        fake = _FakeEmail()
        # return_token=False + real email → the send path runs.
        service = _named_service(email=fake, return_token=False)
        await service.signup(
            session,
            email="mailme@a.com",
            password="strong-pass-12-chars",
        )
        assert ("mailme@a.com", "Activate your account") in fake.sent

    async def test_request_reset_sends_email_and_returns_none(
        self,
        session: AsyncSession,
    ) -> None:
        fake = _FakeEmail()
        service = _named_service(email=fake, return_token=False)
        user, _ = await service.signup(
            session,
            email="resetmail@a.com",
            password="strong-pass-12-chars",
        )
        user.is_active = True
        await session.commit()
        token = await service.request_password_reset(session, email="resetmail@a.com")
        # Email wired + not returning token in response → returns None,
        # link delivered by email instead.
        assert token is None
        assert len(fake.sent) == 2  # activation + reset


async def _db_service() -> tuple[AsyncDatabaseManager, UserAuthService]:
    """Build a db-backed service sharing an in-memory SQLite schema."""
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    auth = AuthSettings(AUTH_RETURN_TOKEN_IN_RESPONSE=True)
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    service = UserAuthService(
        db=db,
        user_model=_TestUser,
        token_model=_TestUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
    )
    return db, service


class TestCurrentUserResolution:
    async def test_get_user_returns_persisted_user(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, _ = await service.signup(
            session,
            email="who@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        resolved = await service.get_user(str(user.id), session)
        assert resolved.id == user.id
        assert resolved.email == "who@a.com"

    async def test_get_user_accepts_uuid_subject(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, _ = await service.signup(
            session,
            email="uuid@a.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        resolved = await service.get_user(user.id, session)
        assert resolved.id == user.id

    async def test_get_user_unknown_id_raises(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(NotFoundException):
            await service.get_user(
                "00000000-0000-0000-0000-000000000000",
                session,
            )

    async def test_get_user_malformed_subject_raises(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(NotFoundException):
            await service.get_user("not-a-uuid", session)

    async def test_load_user_without_db_raises(self) -> None:
        service = _service()  # built without db=
        with pytest.raises(RuntimeError):
            await service.load_user("00000000-0000-0000-0000-000000000000")

    async def test_load_user_opens_own_session(self) -> None:
        db, service = await _db_service()
        try:
            async with db.get_session_context() as s:
                user, _ = await service.signup(
                    s,
                    email="self@a.com",
                    password="strong-pass-12-chars",
                )
                await s.commit()
                user_id = str(user.id)
            resolved = await service.load_user(user_id)
            assert resolved.email == "self@a.com"
        finally:
            await db.disconnect()

    async def test_current_user_dependency_end_to_end(self) -> None:
        db, service = await _db_service()
        try:
            async with db.get_session_context() as s:
                user, _ = await service.signup(
                    s,
                    email="bearer@a.com",
                    password="strong-pass-12-chars",
                )
                user.is_active = True
                await s.commit()
                await s.refresh(user)
            access, _ = service.issue_jwt_pair(user)

            get_current_user = service.current_user_dependency()

            app = FastAPI()

            @app.get("/me")
            async def me(current: Any = Depends(get_current_user)) -> dict[str, str]:
                return {"email": current.email}

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as client:
                ok = await client.get(
                    "/me", headers={"Authorization": f"Bearer {access}"}
                )
                assert ok.status_code == 200
                assert ok.json() == {"email": "bearer@a.com"}

                missing = await client.get("/me")
                assert missing.status_code == 401
        finally:
            await db.disconnect()

    async def test_current_user_dependency_soft_returns_none(self) -> None:
        db, service = await _db_service()
        try:
            get_current_user_or_none = service.current_user_dependency(soft=True)

            app = FastAPI()

            @app.get("/maybe")
            async def maybe(
                current: Any = Depends(get_current_user_or_none),
            ) -> dict[str, bool]:
                return {"anonymous": current is None}

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as client:
                resp = await client.get("/maybe")
                assert resp.status_code == 200
                assert resp.json() == {"anonymous": True}
        finally:
            await db.disconnect()
