"""Tests for the email change / re-verify / recovery flow."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pyotp
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
    MFAMixin,
    UserAuthService,
    make_auth_router,
    make_user_recovery_code_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    InvalidTokenException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _EcUser(MFAMixin, BaseUserModel):
    __tablename__ = "ec_test_users"


_EcUserToken = make_user_token_model(
    user_table="ec_test_users",
    tablename="ec_test_user_tokens",
    class_name="_EcUserToken",
)

_EcRecoveryCode = make_user_recovery_code_model(
    user_table="ec_test_users",
    tablename="ec_test_recovery_codes",
    class_name="_EcRecoveryCode",
)


class _FakeEmail:
    """Records recipients + subjects instead of hitting SMTP."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.renders: list[str] = []

    def render_template(
        self,
        template: str,
        context: dict[str, Any],
        *,
        locale: str | None = None,
    ) -> str:
        self.renders.append(template)
        return f"<html>{template}</html>"

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        html: str | None = None,
    ) -> None:
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
    return_token: bool = True,
    email: Any = None,
    notify_old: bool = True,
    recovery_enabled: bool = False,
    mfa_enabled: bool = False,
) -> UserAuthService:
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=True,
        AUTH_RETURN_TOKEN_IN_RESPONSE=return_token,
        AUTH_EMAIL_CHANGE_NOTIFY_OLD=notify_old,
        AUTH_EMAIL_RECOVERY_ENABLED=recovery_enabled,
        AUTH_MFA_ENABLED=mfa_enabled,
        AUTH_MFA_ISSUER="Tempest Test",
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_EcUser,
        token_model=_EcUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=email,
    )


async def _make_user(
    service: UserAuthService,
    session: AsyncSession,
    *,
    email: str = "ana@example.com",
    password: str = "strong-pass-12-chars",
) -> Any:
    user, _ = await service.signup(session, email=email, password=password)
    await session.commit()
    return user


class TestRequestEmailChange:
    async def test_happy_path_stages_new_email(self, session: AsyncSession) -> None:
        service = _service()
        user = await _make_user(service, session)
        token = await service.request_email_change(
            session,
            user=user,
            current_password="strong-pass-12-chars",
            new_email="NOVA@example.com",
        )
        assert token is not None
        assert token.new_email == "nova@example.com"  # normalized
        # Email is NOT changed until confirmation.
        assert user.email == "ana@example.com"

    async def test_wrong_password_rejected(self, session: AsyncSession) -> None:
        service = _service()
        user = await _make_user(service, session)
        with pytest.raises(UnauthorizedException):
            await service.request_email_change(
                session,
                user=user,
                current_password="wrong",
                new_email="nova@example.com",
            )

    async def test_same_email_rejected(self, session: AsyncSession) -> None:
        service = _service()
        user = await _make_user(service, session)
        with pytest.raises(ValidationException):
            await service.request_email_change(
                session,
                user=user,
                current_password="strong-pass-12-chars",
                new_email="ana@example.com",
            )

    async def test_taken_email_rejected(self, session: AsyncSession) -> None:
        service = _service()
        user = await _make_user(service, session)
        await _make_user(service, session, email="taken@example.com")
        with pytest.raises(ConflictException):
            await service.request_email_change(
                session,
                user=user,
                current_password="strong-pass-12-chars",
                new_email="taken@example.com",
            )

    async def test_confirmation_email_goes_to_new_address(
        self,
        session: AsyncSession,
    ) -> None:
        fake = _FakeEmail()
        service = _service(return_token=False, email=fake)
        user = await _make_user(service, session)
        await service.request_email_change(
            session,
            user=user,
            current_password="strong-pass-12-chars",
            new_email="nova@example.com",
        )
        assert fake.sent[-1][0] == "nova@example.com"


class TestConfirmEmailChange:
    async def test_applies_new_email(self, session: AsyncSession) -> None:
        service = _service()
        user = await _make_user(service, session)
        token = await service.request_email_change(
            session,
            user=user,
            current_password="strong-pass-12-chars",
            new_email="nova@example.com",
        )
        await session.commit()
        assert token is not None
        confirmed = await service.confirm_email_change(session, token=token.token)
        assert confirmed.email == "nova@example.com"

    async def test_notifies_old_address(self, session: AsyncSession) -> None:
        fake = _FakeEmail()
        service = _service(return_token=True, email=fake)
        user = await _make_user(service, session)
        token = await service.request_email_change(
            session,
            user=user,
            current_password="strong-pass-12-chars",
            new_email="nova@example.com",
        )
        await session.commit()
        assert token is not None
        fake.sent.clear()
        await service.confirm_email_change(session, token=token.token)
        # A security notice went to the OLD address.
        assert ("ana@example.com", "") not in fake.sent  # subject non-empty
        assert any(to == "ana@example.com" for to, _ in fake.sent)

    async def test_taken_meanwhile_rejected(self, session: AsyncSession) -> None:
        service = _service()
        user = await _make_user(service, session)
        token = await service.request_email_change(
            session,
            user=user,
            current_password="strong-pass-12-chars",
            new_email="nova@example.com",
        )
        await session.commit()
        assert token is not None
        # Someone else grabs the target address before confirmation.
        await _make_user(service, session, email="nova@example.com")
        with pytest.raises(ConflictException):
            await service.confirm_email_change(session, token=token.token)

    async def test_bad_token_rejected(self, session: AsyncSession) -> None:
        service = _service()
        with pytest.raises(InvalidTokenException):
            await service.confirm_email_change(session, token="not-a-real-token")

    async def test_token_is_one_shot(self, session: AsyncSession) -> None:
        service = _service()
        user = await _make_user(service, session)
        token = await service.request_email_change(
            session,
            user=user,
            current_password="strong-pass-12-chars",
            new_email="nova@example.com",
        )
        await session.commit()
        assert token is not None
        await service.confirm_email_change(session, token=token.token)
        with pytest.raises(InvalidTokenException):
            await service.confirm_email_change(session, token=token.token)


class TestEmailVerification:
    async def test_request_and_confirm_marks_active(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        user.is_active = False
        await session.commit()
        token = await service.request_email_verification(session, user=user)
        await session.commit()
        assert token is not None
        confirmed = await service.confirm_email_verification(session, token=token.token)
        assert confirmed.is_active is True


class TestEmailRecovery:
    async def test_happy_path_without_mfa(self, session: AsyncSession) -> None:
        service = _service(recovery_enabled=True)
        await _make_user(service, session, email="old@example.com")
        token = await service.request_email_recovery(
            session,
            email="old@example.com",
            new_email="new@example.com",
            current_password="strong-pass-12-chars",
        )
        assert token is not None
        assert token.new_email == "new@example.com"

    async def test_unknown_email_returns_none(self, session: AsyncSession) -> None:
        service = _service(recovery_enabled=True)
        token = await service.request_email_recovery(
            session,
            email="ghost@example.com",
            new_email="new@example.com",
            current_password="whatever",
        )
        assert token is None

    async def test_wrong_password_returns_none(self, session: AsyncSession) -> None:
        service = _service(recovery_enabled=True)
        await _make_user(service, session, email="old@example.com")
        token = await service.request_email_recovery(
            session,
            email="old@example.com",
            new_email="new@example.com",
            current_password="wrong",
        )
        assert token is None

    async def test_mfa_enrolled_without_code_returns_none(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(recovery_enabled=True, mfa_enabled=True)
        user = await _make_user(service, session, email="old@example.com")
        secret = pyotp.random_base32()
        user.totp_secret = secret
        from tempest_fastapi_sdk.utils.datetime import utcnow

        user.totp_enabled_at = utcnow()
        await session.commit()
        token = await service.request_email_recovery(
            session,
            email="old@example.com",
            new_email="new@example.com",
            current_password="strong-pass-12-chars",
            recovery_code_model=_EcRecoveryCode,
        )
        assert token is None

    async def test_mfa_enrolled_with_valid_code_succeeds(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(recovery_enabled=True, mfa_enabled=True)
        user = await _make_user(service, session, email="old@example.com")
        secret = pyotp.random_base32()
        user.totp_secret = secret
        from tempest_fastapi_sdk.utils.datetime import utcnow

        user.totp_enabled_at = utcnow()
        await session.commit()
        code = pyotp.TOTP(secret).now()
        token = await service.request_email_recovery(
            session,
            email="old@example.com",
            new_email="new@example.com",
            current_password="strong-pass-12-chars",
            mfa_code=code,
            recovery_code_model=_EcRecoveryCode,
        )
        assert token is not None


class TestRouter:
    async def _client(self, service: UserAuthService, session: AsyncSession) -> Any:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_factory,
                recovery_code_model=_EcRecoveryCode,
            )
        )
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    async def test_change_request_requires_auth(self, session: AsyncSession) -> None:
        service = _service()
        async with await self._client(service, session) as c:
            r = await c.post(
                "/auth/email-change/request",
                json={
                    "new_email": "nova@example.com",
                    "current_password": "strong-pass-12-chars",
                },
            )
        assert r.status_code in (401, 403)

    async def test_change_request_and_confirm(self, session: AsyncSession) -> None:
        service = _service()
        await _make_user(service, session, email="ana@example.com")
        async with await self._client(service, session) as c:
            login = await c.post(
                "/auth/login",
                json={"email": "ana@example.com", "password": "strong-pass-12-chars"},
            )
            access = login.json()["access_token"]
            req = await c.post(
                "/auth/email-change/request",
                json={
                    "new_email": "nova@example.com",
                    "current_password": "strong-pass-12-chars",
                },
                headers={"Authorization": f"Bearer {access}"},
            )
            assert req.status_code == 202, req.text
            url = req.json()["confirm_url"]
            token = url.split("token=")[-1]
            confirm = await c.post(
                "/auth/email-change/confirm",
                json={"token": token},
            )
        assert confirm.status_code == 200, confirm.text

    async def test_recovery_endpoint_absent_when_disabled(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(recovery_enabled=False)
        async with await self._client(service, session) as c:
            r = await c.post(
                "/auth/email-recovery/request",
                json={
                    "email": "a@b.com",
                    "new_email": "c@d.com",
                    "current_password": "x",
                },
            )
        assert r.status_code == 404

    async def test_recovery_endpoint_present_when_enabled(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(recovery_enabled=True)
        await _make_user(service, session, email="old@example.com")
        async with await self._client(service, session) as c:
            r = await c.post(
                "/auth/email-recovery/request",
                json={
                    "email": "old@example.com",
                    "new_email": "new@example.com",
                    "current_password": "strong-pass-12-chars",
                },
            )
        assert r.status_code == 202, r.text

    async def test_backend_html_confirm_page(self, session: AsyncSession) -> None:
        auth = AuthSettings(
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            AUTH_BACKEND_LINKS=True,
        )
        jwt = JWTSettings(JWT_SECRET="x" * 32)
        service = UserAuthService(
            user_model=_EcUser,
            token_model=_EcUserToken,  # type: ignore[arg-type]
            auth_settings=auth,
            jwt_settings=jwt,
            email=None,
        )
        user = await _make_user(service, session, email="ana@example.com")
        token = await service.request_email_change(
            session,
            user=user,
            current_password="strong-pass-12-chars",
            new_email="nova@example.com",
        )
        await session.commit()
        assert token is not None
        async with await self._client(service, session) as c:
            page = await c.get(f"/auth/email-change/{token.token}")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
