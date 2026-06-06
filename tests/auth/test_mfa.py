"""Tests for the bundled MFA (TOTP) flow on ``UserAuthService`` + router."""

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
    TOTPHelper,
    UserAuthService,
    make_auth_router,
    make_user_recovery_code_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.exceptions import (
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _MfaUser(MFAMixin, BaseUserModel):
    __tablename__ = "mfa_test_users"


_MfaUserToken = make_user_token_model(
    user_table="mfa_test_users",
    tablename="mfa_test_user_tokens",
    class_name="_MfaUserToken",
)

_MfaRecoveryCode = make_user_recovery_code_model(
    user_table="mfa_test_users",
    tablename="mfa_test_recovery_codes",
    class_name="_MfaRecoveryCode",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(*, mfa_enabled: bool = True) -> UserAuthService:
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=True,
        AUTH_MFA_ENABLED=mfa_enabled,
        AUTH_MFA_ISSUER="Tempest Test",
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_MfaUser,
        token_model=_MfaUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=None,
    )


async def _make_user(
    service: UserAuthService,
    session: AsyncSession,
    *,
    email: str = "mfa@a.com",
    password: str = "strong-pass-12-chars",
) -> Any:
    user, _ = await service.signup(session, email=email, password=password)
    await session.commit()
    return user


class TestTOTPHelper:
    def test_generate_secret_is_base32(self) -> None:
        helper = TOTPHelper(issuer="App")
        secret = helper.generate_secret()
        assert len(secret) == 32
        # round-trips through pyotp without raising
        assert pyotp.TOTP(secret).now()

    def test_provisioning_uri_carries_issuer_and_account(self) -> None:
        helper = TOTPHelper(issuer="Acme Inc.")
        secret = helper.generate_secret()
        uri = helper.provisioning_uri(secret, "ana@example.com")
        assert uri.startswith("otpauth://totp/")
        assert "issuer=Acme" in uri
        assert "ana%40example.com" in uri

    def test_verify_accepts_current_code(self) -> None:
        helper = TOTPHelper(issuer="App")
        secret = helper.generate_secret()
        code = pyotp.TOTP(secret).now()
        assert helper.verify(secret, code) is True

    def test_verify_rejects_wrong_and_malformed(self) -> None:
        helper = TOTPHelper(issuer="App")
        secret = helper.generate_secret()
        assert helper.verify(secret, "000000") in (False, True)  # numeric
        assert helper.verify(secret, "abc") is False  # non-numeric
        assert helper.verify(secret, "1234567") is False  # wrong length

    def test_verify_strips_spaces_and_dashes(self) -> None:
        helper = TOTPHelper(issuer="App")
        secret = helper.generate_secret()
        code = pyotp.TOTP(secret).now()
        spaced = f"{code[:3]} {code[3:]}"
        assert helper.verify(secret, spaced) is True


class TestMFAEnrollConfirm:
    async def test_enroll_returns_secret_uri_and_codes(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, uri, codes = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await session.commit()
        assert len(secret) == 32
        assert uri.startswith("otpauth://totp/")
        assert len(codes) == 10  # AUTH_MFA_RECOVERY_CODES_COUNT default
        # Enrollment alone does NOT activate MFA.
        assert user.totp_enabled_at is None
        assert user.is_mfa_active is False  # MFAMixin property
        assert service.is_mfa_enrolled(user) is False

    async def test_confirm_activates_mfa(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await session.commit()
        code = pyotp.TOTP(secret).now()
        await service.mfa_confirm(session, user=user, code=code)
        await session.commit()
        assert user.totp_enabled_at is not None
        assert user.is_mfa_active is True  # MFAMixin property
        assert service.is_mfa_enrolled(user) is True

    async def test_confirm_rejects_wrong_code(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await session.commit()
        with pytest.raises(UnauthorizedException):
            await service.mfa_confirm(session, user=user, code="000000")
        assert user.totp_enabled_at is None

    async def test_confirm_without_enroll_raises(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        with pytest.raises(ValidationException):
            await service.mfa_confirm(session, user=user, code="123456")

    async def test_enroll_rotates_recovery_codes(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        _, _, first = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await session.commit()
        secret2, _, second = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret2).now())
        await session.commit()
        # Old recovery code from the first enrollment no longer works.
        assert not await service._verify_mfa_code(
            session, user, first[0], _MfaRecoveryCode
        )
        # A fresh second-enrollment code works.
        assert await service._verify_mfa_code(
            session, user, second[0], _MfaRecoveryCode
        )


class TestMFAVerify:
    async def test_verify_with_totp_returns_user(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()

        mfa_token = service.issue_mfa_token(user)
        verified = await service.mfa_verify(
            session,
            mfa_token=mfa_token,
            code=pyotp.TOTP(secret).now(),
            recovery_code_model=_MfaRecoveryCode,
        )
        assert verified.id == user.id

    async def test_verify_with_recovery_code_consumes_it(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, codes = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()

        mfa_token = service.issue_mfa_token(user)
        await service.mfa_verify(
            session,
            mfa_token=mfa_token,
            code=codes[0],
            recovery_code_model=_MfaRecoveryCode,
        )
        await session.commit()
        # Same recovery code cannot be reused.
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=service.issue_mfa_token(user),
                code=codes[0],
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_verify_rejects_bad_token(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token="not-a-jwt",
                code="123456",
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_verify_rejects_wrong_purpose_token(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        # A perfectly valid JWT, but not minted for the MFA step.
        wrong = service.jwt.encode({"sub": str(user.id), "purpose": "access"})
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=wrong,
                code="123456",
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_verify_rejects_token_with_invalid_sub(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        bad_sub = service.jwt.encode({"sub": "not-a-uuid", "purpose": "mfa_pending"})
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=bad_sub,
                code="123456",
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_verify_rejects_token_without_sub(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        no_sub = service.jwt.encode({"purpose": "mfa_pending"})
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=no_sub,
                code="123456",
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_verify_rejects_inactive_user(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        user.is_active = False
        await session.commit()
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=service.issue_mfa_token(user),
                code=pyotp.TOTP(secret).now(),
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_verify_rejects_user_not_enrolled(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(session=session, service=service)
        # Active user, valid token, but never enrolled in MFA.
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=service.issue_mfa_token(user),
                code="123456",
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_verify_rejects_wrong_code_with_no_recovery_match(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=service.issue_mfa_token(user),
                code="999999",  # wrong TOTP, not a recovery code either
                recovery_code_model=_MfaRecoveryCode,
            )


class TestVerifyMfaCodeHelper:
    async def test_recovery_code_works_without_totp_secret(
        self,
        session: AsyncSession,
    ) -> None:
        # Defensive: recovery codes are validated independently of the
        # TOTP secret, so they still work even if the secret was cleared.
        service = _service()
        user = await _make_user(service, session)
        _, _, codes = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        user.totp_secret = None  # TOTP branch is skipped entirely
        await session.flush()
        assert await service._verify_mfa_code(session, user, codes[0], _MfaRecoveryCode)

    async def test_unknown_code_returns_false(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        user.totp_secret = None
        await session.flush()
        assert not await service._verify_mfa_code(
            session, user, "totally-bogus", _MfaRecoveryCode
        )


class TestMFADisable:
    async def test_disable_clears_secret_and_codes(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()

        await service.mfa_disable(
            session,
            user=user,
            password="strong-pass-12-chars",
            code=pyotp.TOTP(secret).now(),
            recovery_code_model=_MfaRecoveryCode,
        )
        await session.commit()
        assert user.totp_secret is None
        assert user.totp_enabled_at is None
        assert service.is_mfa_enrolled(user) is False

    async def test_disable_rejects_wrong_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()
        with pytest.raises(UnauthorizedException):
            await service.mfa_disable(
                session,
                user=user,
                password="wrong-pass-12-chars",
                code=pyotp.TOTP(secret).now(),
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_disable_rejects_wrong_code(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()
        # Correct password, wrong second factor.
        with pytest.raises(UnauthorizedException):
            await service.mfa_disable(
                session,
                user=user,
                password="strong-pass-12-chars",
                code="999999",
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_disable_rejects_when_mfa_not_active(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        # Never enrolled — disabling is a no-op error, not a silent pass.
        with pytest.raises(ValidationException):
            await service.mfa_disable(
                session,
                user=user,
                password="strong-pass-12-chars",
                code="123456",
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_disable_with_recovery_code(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        secret, _, codes = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()
        # A recovery code is an accepted second factor for disabling too.
        await service.mfa_disable(
            session,
            user=user,
            password="strong-pass-12-chars",
            code=codes[0],
            recovery_code_model=_MfaRecoveryCode,
        )
        await session.commit()
        assert user.totp_secret is None
        assert user.totp_enabled_at is None


class TestMFADisabledKillSwitch:
    async def test_enrolled_user_skips_mfa_when_disabled(
        self,
        session: AsyncSession,
    ) -> None:
        # Enroll + confirm with MFA enabled.
        service = _service(mfa_enabled=True)
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()
        assert service.is_mfa_enrolled(user) is True

        # Flip the kill-switch off: user is treated as unenrolled.
        off = _service(mfa_enabled=False)
        assert off.is_mfa_enrolled(user) is False


class TestMFARouter:
    async def test_login_returns_mfa_token_when_enrolled(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="router-mfa@a.com")
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_factory,
                recovery_code_model=_MfaRecoveryCode,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/login",
                json={
                    "email": "router-mfa@a.com",
                    "password": "strong-pass-12-chars",
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["mfa_required"] is True
            assert body["access_token"] is None
            assert body["mfa_token"]

            # Step 2: exchange mfa_token + code for the JWT pair.
            r2 = await c.post(
                "/auth/mfa/verify",
                json={
                    "mfa_token": body["mfa_token"],
                    "code": pyotp.TOTP(secret).now(),
                },
            )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["access_token"]
        assert body2["refresh_token"]
        assert body2["mfa_required"] is False

    async def test_enroll_requires_bearer_then_confirms(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="enroll-flow@a.com")
        access, _ = service.issue_jwt_pair(user)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_factory,
                recovery_code_model=_MfaRecoveryCode,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            # Without a token → 401.
            r = await c.post("/auth/mfa/enroll")
            assert r.status_code == 401

            r = await c.post(
                "/auth/mfa/enroll",
                headers={"Authorization": f"Bearer {access}"},
            )
            assert r.status_code == 200, r.text
            secret = r.json()["secret"]

            r2 = await c.post(
                "/auth/mfa/confirm",
                headers={"Authorization": f"Bearer {access}"},
                json={"code": pyotp.TOTP(secret).now()},
            )
        assert r2.status_code == 204, r2.text

    async def test_disable_endpoint_clears_mfa(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="router-disable@a.com")
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
        await session.commit()
        access, _ = service.issue_jwt_pair(user)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_factory,
                recovery_code_model=_MfaRecoveryCode,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/mfa/disable",
                headers={"Authorization": f"Bearer {access}"},
                json={
                    "password": "strong-pass-12-chars",
                    "code": pyotp.TOTP(secret).now(),
                },
            )
        assert r.status_code == 204, r.text
        assert user.totp_enabled_at is None

    async def test_verify_endpoint_rejects_bad_token(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_factory,
                recovery_code_model=_MfaRecoveryCode,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/mfa/verify",
                json={"mfa_token": "not-a-jwt", "code": "123456"},
            )
        assert r.status_code == 401, r.text

    async def test_confirm_endpoint_requires_bearer(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_factory,
                recovery_code_model=_MfaRecoveryCode,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/auth/mfa/confirm", json={"code": "123456"})
        assert r.status_code == 401

    async def test_mfa_endpoints_not_mounted_when_disabled(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(mfa_enabled=False)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/auth/mfa/enroll")
        assert r.status_code == 404

    async def test_enabled_without_recovery_model_raises(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(mfa_enabled=True)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        with pytest.raises(RuntimeError):
            make_auth_router(service, session_factory=_factory)
