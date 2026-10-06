"""Tests for the bundled MFA (TOTP) flow on ``UserAuthService`` + router."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
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
    AttemptThrottle,
    BaseModel,
    BaseUserModel,
    InMemoryThrottleBackend,
    MFAMixin,
    TOTPHelper,
    UserAuthService,
    make_auth_router,
    make_user_recovery_code_model,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.auth import MFAAlreadyEnrolledException
from tempest_fastapi_sdk.auth.router import (
    MFA_THROTTLE_MAX_ATTEMPTS,
    MFA_THROTTLE_WINDOW_SECONDS,
)
from tempest_fastapi_sdk.exceptions import (
    TooManyRequestsException,
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


def _next_code(secret: str) -> str:
    """Return the code of the step after the current one.

    A TOTP step is accepted once, so a test that already spent the
    current code on ``mfa_confirm`` proves the next factor with the
    following step, which the default drift window (``1``) accepts.

    Args:
        secret (str): The enrolled Base32 secret.

    Returns:
        str: The 6-digit code for ``now + 30s``.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


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
            code=_next_code(secret),
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
                code=_next_code(secret),
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
            code=_next_code(secret),
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
                code=_next_code(secret),
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
                    "code": _next_code(secret),
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
                    "code": _next_code(secret),
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


class TestMFAEnrollOnActiveAccount:
    """A bearer token alone must never switch off an active second factor.

    ``/mfa/enroll`` only requires the access token. Before the guard, a
    second enroll on an account with MFA already active wiped every
    recovery code and reset ``totp_enabled_at`` to ``None``, so whoever
    held a stolen access token could disable MFA without the password or
    a TOTP code, the two proofs ``/mfa/disable`` demands.
    """

    async def test_token_alone_cannot_disable_mfa_through_enroll(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await _make_user(service, session, email="reenroll@a.com")

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_auth_router(
                service,
                session_factory=_factory,
                recovery_code_model=_MfaRecoveryCode,
            )
        )
        credentials: dict[str, str] = {
            "email": "reenroll@a.com",
            "password": "strong-pass-12-chars",
        }
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/auth/login", json=credentials)
            assert r.status_code == 200, r.text
            bearer = {"Authorization": f"Bearer {r.json()['access_token']}"}

            r = await c.post("/auth/mfa/enroll", headers=bearer)
            assert r.status_code == 200, r.text
            secret = r.json()["secret"]
            r = await c.post(
                "/auth/mfa/confirm",
                headers=bearer,
                json={"code": pyotp.TOTP(secret).now()},
            )
            assert r.status_code == 204, r.text

            r = await c.post("/auth/mfa/enroll", headers=bearer)
            assert r.status_code == 409, r.text
            assert r.json()["code"] == "MFA_ALREADY_ENROLLED"

            r = await c.post("/auth/login", json=credentials)
        assert r.status_code == 200, r.text
        assert r.json()["mfa_required"] is True
        assert r.json()["access_token"] is None

    async def test_refused_enroll_keeps_factor_and_recovery_codes(
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
        enabled_at = user.totp_enabled_at

        with pytest.raises(MFAAlreadyEnrolledException) as excinfo:
            await service.mfa_enroll(
                session,
                user=user,
                recovery_code_model=_MfaRecoveryCode,
            )
        assert excinfo.value.status_code == 409
        assert user.totp_secret == secret
        assert user.totp_enabled_at == enabled_at
        assert await service._verify_mfa_code(session, user, codes[0], _MfaRecoveryCode)

    async def test_refused_even_with_kill_switch_off(
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
        with pytest.raises(MFAAlreadyEnrolledException):
            await _service(mfa_enabled=False).mfa_enroll(
                session,
                user=user,
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_disable_then_enroll_rotates(
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
        await service.mfa_disable(
            session,
            user=user,
            password="strong-pass-12-chars",
            code=_next_code(secret),
            recovery_code_model=_MfaRecoveryCode,
        )
        new_secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        assert new_secret != secret
        assert user.totp_enabled_at is None


def _wrong_code(secret: str) -> str:
    """Return a 6-digit code that no step in the default window produces.

    Args:
        secret (str): The enrolled Base32 secret.

    Returns:
        str: A code guaranteed wrong for ``window=1`` right now.
    """
    totp = pyotp.TOTP(secret)
    now = int(time.time())
    live = {totp.at(now + offset) for offset in (-60, -30, 0, 30, 60)}
    return next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in live)


async def _enrolled(
    service: UserAuthService,
    session: AsyncSession,
    *,
    email: str = "mfa@a.com",
) -> tuple[Any, str]:
    """Create a user with MFA confirmed and committed.

    Args:
        service (UserAuthService): The service under test.
        session (AsyncSession): The test session.
        email (str): Login of the new user.

    Returns:
        tuple[Any, str]: The user and its TOTP secret. The current step
        is already spent by the confirm.
    """
    user = await _make_user(service, session, email=email)
    secret, _, _ = await service.mfa_enroll(
        session,
        user=user,
        recovery_code_model=_MfaRecoveryCode,
    )
    await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
    await session.commit()
    return user, secret


def _router_app(
    service: UserAuthService,
    session: AsyncSession,
    *,
    mfa_throttle: AttemptThrottle | None = None,
) -> FastAPI:
    """Mount the bundled router over the shared test session.

    Args:
        service (UserAuthService): The service under test.
        session (AsyncSession): Session every request reuses.
        mfa_throttle (AttemptThrottle | None): Forwarded to the factory.

    Returns:
        FastAPI: The app with exception handlers registered.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            recovery_code_model=_MfaRecoveryCode,
            mfa_throttle=mfa_throttle,
        )
    )
    return app


async def _mfa_token(client: AsyncClient, email: str) -> str:
    """Run step 1 of the login and return the ``mfa_token``.

    Args:
        client (AsyncClient): Client bound to the test app.
        email (str): Login of the enrolled user.

    Returns:
        str: A freshly minted ``mfa_token``.
    """
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": "strong-pass-12-chars"},
    )
    assert r.status_code == 200, r.text
    token: str = r.json()["mfa_token"]
    return token


class TestMFAAttemptLimit:
    """``/auth/mfa/verify`` stops answering after too many wrong codes."""

    def test_default_budget(self) -> None:
        """The documented default is five wrong codes per fifteen minutes."""
        assert MFA_THROTTLE_MAX_ATTEMPTS == 5
        assert MFA_THROTTLE_WINDOW_SECONDS == 900

    async def test_sixth_attempt_is_429_even_with_the_right_code(
        self,
        session: AsyncSession,
    ) -> None:
        """Past the budget the code is not even looked at."""
        service = _service()
        _, secret = await _enrolled(service, session, email="limit@a.com")
        app = _router_app(service, session)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            token = await _mfa_token(c, "limit@a.com")
            wrong = _wrong_code(secret)
            for _ in range(MFA_THROTTLE_MAX_ATTEMPTS):
                r = await c.post(
                    "/auth/mfa/verify",
                    json={"mfa_token": token, "code": wrong},
                )
                assert r.status_code == 401, r.text
            r = await c.post(
                "/auth/mfa/verify",
                json={"mfa_token": token, "code": _next_code(secret)},
            )
            assert r.status_code == 429, r.text
            assert int(r.headers["Retry-After"]) > 0

            fresh = await _mfa_token(c, "limit@a.com")
            r = await c.post(
                "/auth/mfa/verify",
                json={"mfa_token": fresh, "code": _next_code(secret)},
            )
        assert r.status_code == 429, r.text

    async def test_success_below_the_limit_works_and_resets(
        self,
        session: AsyncSession,
    ) -> None:
        """Four wrong codes then the right one log in and clear the count."""
        service = _service()
        user, secret = await _enrolled(service, session, email="below@a.com")
        throttle = AttemptThrottle(
            InMemoryThrottleBackend(),
            max_attempts=MFA_THROTTLE_MAX_ATTEMPTS,
            window_seconds=MFA_THROTTLE_WINDOW_SECONDS,
        )
        app = _router_app(service, session, mfa_throttle=throttle)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            token = await _mfa_token(c, "below@a.com")
            for _ in range(MFA_THROTTLE_MAX_ATTEMPTS - 1):
                r = await c.post(
                    "/auth/mfa/verify",
                    json={"mfa_token": token, "code": _wrong_code(secret)},
                )
                assert r.status_code == 401, r.text
            r = await c.post(
                "/auth/mfa/verify",
                json={"mfa_token": token, "code": _next_code(secret)},
            )
        assert r.status_code == 200, r.text
        assert r.json()["access_token"]
        assert (await throttle.status(f"mfa:{user.id}")).attempts == 0

    async def test_custom_throttle_is_the_one_used(
        self,
        session: AsyncSession,
    ) -> None:
        """``mfa_throttle=`` replaces the default budget and backend."""
        service = _service()
        user, secret = await _enrolled(service, session, email="custom@a.com")
        backend = InMemoryThrottleBackend()
        throttle = AttemptThrottle(backend, max_attempts=1, window_seconds=60)
        app = _router_app(service, session, mfa_throttle=throttle)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            token = await _mfa_token(c, "custom@a.com")
            r = await c.post(
                "/auth/mfa/verify",
                json={"mfa_token": token, "code": _wrong_code(secret)},
            )
            assert r.status_code == 401, r.text
            r = await c.post(
                "/auth/mfa/verify",
                json={"mfa_token": token, "code": _next_code(secret)},
            )
        assert r.status_code == 429, r.text
        assert int(r.headers["Retry-After"]) <= 60
        assert await backend.get(f"throttle:mfa:{user.id}") == "2"

    async def test_concurrent_guesses_cannot_overrun_the_budget(
        self,
        tmp_path: Path,
    ) -> None:
        """A burst gets ``max_attempts`` verdicts, the rest are refused.

        Each guess runs in its own session over a file database, so the
        verifications genuinely interleave at their ``await`` points.
        Counting only after a failed check would let every guess in the
        burst read "not blocked yet" first.
        """
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/mfa.db")
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        service = _service()
        async with factory() as setup:
            user, secret = await _enrolled(service, setup, email="burst@a.com")
        token = service.issue_mfa_token(user)
        wrong = _wrong_code(secret)
        throttle = AttemptThrottle(
            InMemoryThrottleBackend(),
            max_attempts=MFA_THROTTLE_MAX_ATTEMPTS,
            window_seconds=MFA_THROTTLE_WINDOW_SECONDS,
        )

        async def _guess() -> str:
            async with factory() as s:
                try:
                    await service.mfa_verify(
                        s,
                        mfa_token=token,
                        code=wrong,
                        recovery_code_model=_MfaRecoveryCode,
                        throttle=throttle,
                    )
                except TooManyRequestsException:
                    return "429"
                except UnauthorizedException:
                    return "401"
                return "200"

        outcomes = await asyncio.gather(*(_guess() for _ in range(20)))
        await engine.dispose()
        assert outcomes.count("401") == MFA_THROTTLE_MAX_ATTEMPTS
        assert outcomes.count("429") == 20 - MFA_THROTTLE_MAX_ATTEMPTS


class TestTOTPSingleUse:
    """A TOTP code buys one acceptance, even inside its drift window."""

    async def test_same_code_twice_is_refused(
        self,
        session: AsyncSession,
    ) -> None:
        """The replay fails although the code is still inside the window."""
        service = _service()
        user, secret = await _enrolled(service, session)
        code = _next_code(secret)
        await service.mfa_verify(
            session,
            mfa_token=service.issue_mfa_token(user),
            code=code,
            recovery_code_model=_MfaRecoveryCode,
        )
        await session.commit()
        assert TOTPHelper(issuer="x").verify(secret, code) is True
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=service.issue_mfa_token(user),
                code=code,
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_confirm_code_cannot_be_replayed_at_verify(
        self,
        session: AsyncSession,
    ) -> None:
        """The code that activated MFA is already spent."""
        service = _service()
        user = await _make_user(service, session)
        secret, _, _ = await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        code = pyotp.TOTP(secret).now()
        await service.mfa_confirm(session, user=user, code=code)
        await session.commit()
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=service.issue_mfa_token(user),
                code=code,
                recovery_code_model=_MfaRecoveryCode,
            )

    async def test_next_step_is_accepted_after(
        self,
        session: AsyncSession,
    ) -> None:
        """The replay is refused, the following step still logs in."""
        service = _service()
        user, secret = await _enrolled(service, session)
        now = int(time.time())
        totp = pyotp.TOTP(secret)
        with pytest.raises(UnauthorizedException):
            await service.mfa_verify(
                session,
                mfa_token=service.issue_mfa_token(user),
                code=totp.at(now),
                recovery_code_model=_MfaRecoveryCode,
            )
        verified = await service.mfa_verify(
            session,
            mfa_token=service.issue_mfa_token(user),
            code=totp.at(now + 30),
            recovery_code_model=_MfaRecoveryCode,
        )
        assert verified.totp_last_step == now // 30 + 1

    async def test_step_is_decided_by_the_row_not_the_loaded_object(
        self,
        tmp_path: Path,
    ) -> None:
        """Two sessions holding the same stale row get one acceptance.

        Both load the user before either writes, so both see
        ``totp_last_step`` unset in memory; only the conditional
        ``UPDATE`` can tell the second one the step is already gone.
        """
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/race.db")
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        service = _service()
        async with factory() as setup:
            user, secret = await _enrolled(service, setup)
        code = _next_code(secret)
        async with factory() as first, factory() as second:
            stale_a = await first.get(_MfaUser, user.id)
            stale_b = await second.get(_MfaUser, user.id)
            assert stale_a is not None and stale_b is not None
            assert await service._claim_totp_step(first, stale_a, code) is True
            await first.commit()
            assert await service._claim_totp_step(second, stale_b, code) is False
        await engine.dispose()

    async def test_disable_refuses_the_code_just_used_to_log_in(
        self,
        session: AsyncSession,
    ) -> None:
        """``disable`` spends from the same step counter as ``verify``."""
        service = _service()
        user, secret = await _enrolled(service, session)
        code = _next_code(secret)
        await service.mfa_verify(
            session,
            mfa_token=service.issue_mfa_token(user),
            code=code,
            recovery_code_model=_MfaRecoveryCode,
        )
        with pytest.raises(UnauthorizedException):
            await service.mfa_disable(
                session,
                user=user,
                password="strong-pass-12-chars",
                code=code,
                recovery_code_model=_MfaRecoveryCode,
            )
        assert user.totp_enabled_at is not None

    async def test_reenroll_resets_the_spent_step(
        self,
        session: AsyncSession,
    ) -> None:
        """Steps count against one secret; a new secret starts clean."""
        service = _service()
        user, secret = await _enrolled(service, session)
        assert user.totp_last_step is not None
        await service.mfa_disable(
            session,
            user=user,
            password="strong-pass-12-chars",
            code=_next_code(secret),
            recovery_code_model=_MfaRecoveryCode,
        )
        assert user.totp_last_step is None
        await service.mfa_enroll(
            session,
            user=user,
            recovery_code_model=_MfaRecoveryCode,
        )
        assert user.totp_last_step is None


class TestMatchingStep:
    """``TOTPHelper.matching_step`` names the step a code came from."""

    def test_returns_the_step_of_each_code_in_the_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Previous, current and next steps resolve to their own counter."""
        monkeypatch.setattr(time, "time", lambda: 1_700_000_015.0)
        helper = TOTPHelper(issuer="App")
        secret = helper.generate_secret()
        totp = pyotp.TOTP(secret)
        current = 1_700_000_015 // 30
        for offset in (-1, 0, 1):
            code = totp.generate_otp(current + offset)
            assert helper.matching_step(secret, code) == current + offset
        assert helper.matching_step(secret, totp.generate_otp(current + 2)) is None
        assert helper.matching_step(secret, totp.generate_otp(current - 2)) is None

    def test_outside_the_window_is_none(self) -> None:
        """A code two steps ahead is refused at ``window=1``."""
        helper = TOTPHelper(issuer="App")
        secret = helper.generate_secret()
        far = pyotp.TOTP(secret).at(int(time.time()) + 120)
        assert helper.matching_step(secret, far, window=1) is None

    def test_malformed_is_none(self) -> None:
        """Non-digits and the wrong length never reach the HMAC."""
        helper = TOTPHelper(issuer="App")
        secret = helper.generate_secret()
        assert helper.matching_step(secret, "abc") is None
        assert helper.matching_step(secret, "1234567") is None
