"""``POST /auth/password-change`` never opens a fuller session than ``/login``.

The change revokes every session of the user, so the route answers with a
new session (#662). The current password is the first factor only: for an
account with MFA enrolled, the response without a ``code`` is the same
challenge ``/auth/login`` returns after a correct password, and a pair
stamped with the second factor needs a code proved in the request — the
``amr`` of the caller's access token is never copied into it.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pyotp
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
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
    MFAMixin,
    UserAuthService,
    make_auth_router,
    make_user_recovery_code_model,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"


class _PcMfaUser(MFAMixin, BaseUserModel):
    __tablename__ = "pw_change_mfa_users"


_PcMfaUserToken = make_user_token_model(
    user_table="pw_change_mfa_users",
    tablename="pw_change_mfa_user_tokens",
    class_name="_PcMfaUserToken",
)
_PcMfaRecoveryCode = make_user_recovery_code_model(
    user_table="pw_change_mfa_users",
    tablename="pw_change_mfa_recovery_codes",
    class_name="_PcMfaRecoveryCode",
)
_PcMfaRefreshToken = make_user_refresh_token_model(
    user_table="pw_change_mfa_users",
    tablename="pw_change_mfa_refresh_tokens",
    class_name="_PcMfaRefreshToken",
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
    """Build a service with MFA on and DB-backed refresh tokens.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_PcMfaUser,
        token_model=_PcMfaUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_MFA_ENABLED=True,
            AUTH_MFA_ISSUER="Tempest Test",
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        refresh_token_model=_PcMfaRefreshToken,  # type: ignore[arg-type]
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
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            recovery_code_model=_PcMfaRecoveryCode,
        )
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _account(
    service: UserAuthService,
    session: AsyncSession,
    *,
    mfa: bool,
    amr: tuple[str, ...],
) -> tuple[BaseUserModel, str, str, str | None, list[str]]:
    """Create an active account and open one session for it.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): Active session.
        mfa (bool): Whether to enroll and confirm TOTP.
        amr (tuple[str, ...]): The ``amr`` stamped on the session.

    Returns:
        tuple[BaseUserModel, str, str, str | None, list[str]]: The user,
        its access and refresh tokens, the Base32 secret (``None`` without
        MFA) and the recovery codes (empty without MFA).
    """
    user, _ = await service.signup(
        session, email="owner@example.com", password=PASSWORD
    )
    secret: str | None = None
    codes: list[str] = []
    if mfa:
        secret, _, codes = await service.mfa_enroll(
            session, user=user, recovery_code_model=_PcMfaRecoveryCode
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
    access, refresh = await service.issue_token_pair(session, user, amr=amr)
    await session.commit()
    return user, access, refresh, secret, list(codes)


def _next_code(secret: str) -> str:
    """Return the TOTP code of the step after the current one.

    Args:
        secret (str): The enrolled Base32 secret.

    Returns:
        str: The 6-digit code for ``now + 30s``, a step not yet spent.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


async def _live_refresh_tokens(session: AsyncSession) -> int:
    """Count the refresh tokens that are neither revoked nor spent.

    Args:
        session (AsyncSession): Active session.

    Returns:
        int: How many tokens could still be exchanged.
    """
    result = await session.execute(
        select(func.count())
        .select_from(_PcMfaRefreshToken)
        .where(
            _PcMfaRefreshToken.revoked_at.is_(None),
            _PcMfaRefreshToken.used_at.is_(None),
        )
    )
    return int(result.scalar_one())


async def _password_is(
    service: UserAuthService, session: AsyncSession, password: str
) -> bool:
    """Tell whether the stored hash matches ``password``.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): Active session.
        password (str): The plaintext to check.

    Returns:
        bool: Whether it verifies against the account's hash.
    """
    session.expire_all()
    user = (await session.execute(select(_PcMfaUser))).scalar_one()
    return service.passwords.verify(password, user.hashed_password)


def _change(access: str, **extra: Any) -> dict[str, Any]:
    """Build the keyword arguments of a password-change request.

    Args:
        access (str): The caller's access token.
        **extra (Any): Extra body fields (``code``).

    Returns:
        dict[str, Any]: ``json`` and ``headers`` for ``client.post``.
    """
    return {
        "json": {
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            **extra,
        },
        "headers": {"Authorization": f"Bearer {access}"},
    }


class TestMfaAccountWithoutCode:
    """The password changes; the response is the ``/login`` challenge."""

    async def test_the_response_is_the_login_challenge(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        _, access, refresh, _, _ = await _account(
            service, session, mfa=True, amr=MFA_AMR
        )
        async with _client(service, session) as client:
            changed = await client.post("/auth/password-change", **_change(access))
            login = await client.post(
                "/auth/login",
                json={"email": "owner@example.com", "password": NEW_PASSWORD},
            )
            old = await client.post("/auth/refresh", json={"refresh_token": refresh})
        body = changed.json()
        assert changed.status_code == 200, changed.text
        assert body["mfa_required"] is True
        assert body["mfa_token"]
        assert body["access_token"] is None
        assert body["refresh_token"] is None
        assert set(body) == set(login.json())
        assert login.json()["mfa_required"] is True
        assert old.status_code == 401
        assert await _live_refresh_tokens(session) == 0
        assert await _password_is(service, session, NEW_PASSWORD)

    async def test_the_challenge_completes_at_mfa_verify(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        _, access, _, secret, _ = await _account(
            service, session, mfa=True, amr=MFA_AMR
        )
        assert secret is not None
        async with _client(service, session) as client:
            changed = await client.post("/auth/password-change", **_change(access))
            verified = await client.post(
                "/auth/mfa/verify",
                json={
                    "mfa_token": changed.json()["mfa_token"],
                    "code": _next_code(secret),
                },
            )
        assert verified.status_code == 200, verified.text
        claims = service.jwt.decode(verified.json()["access_token"])
        assert tuple(claims["amr"]) == MFA_AMR


class TestMfaAccountWithCode:
    """A code proved in the request is what earns the pair."""

    async def test_a_valid_totp_code_issues_an_mfa_pair(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        _, access, refresh, secret, _ = await _account(
            service, session, mfa=True, amr=PASSWORD_AMR
        )
        assert secret is not None
        async with _client(service, session) as client:
            changed = await client.post(
                "/auth/password-change", **_change(access, code=_next_code(secret))
            )
            old = await client.post("/auth/refresh", json={"refresh_token": refresh})
            new = await client.post(
                "/auth/refresh",
                json={"refresh_token": changed.json()["refresh_token"]},
            )
        assert changed.status_code == 200, changed.text
        assert changed.json()["mfa_required"] is False
        claims = service.jwt.decode(changed.json()["access_token"])
        assert tuple(claims["amr"]) == MFA_AMR
        assert old.status_code == 401
        assert new.status_code == 200, new.text
        assert await _password_is(service, session, NEW_PASSWORD)

    async def test_a_recovery_code_issues_an_mfa_pair(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        _, access, _, _, codes = await _account(
            service, session, mfa=True, amr=PASSWORD_AMR
        )
        async with _client(service, session) as client:
            changed = await client.post(
                "/auth/password-change", **_change(access, code=codes[0])
            )
        assert changed.status_code == 200, changed.text
        assert changed.json()["access_token"]

    async def test_a_wrong_code_changes_and_issues_nothing(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        _, access, refresh, _, _ = await _account(
            service, session, mfa=True, amr=MFA_AMR
        )
        async with _client(service, session) as client:
            changed = await client.post(
                "/auth/password-change", **_change(access, code="000000")
            )
            still = await client.post("/auth/refresh", json={"refresh_token": refresh})
        assert changed.status_code == 401, changed.text
        assert "access_token" not in changed.text
        assert "mfa_token" not in changed.text
        assert await _password_is(service, session, PASSWORD)
        assert still.status_code == 200, still.text

    async def test_a_replayed_totp_code_is_refused(self, session: AsyncSession) -> None:
        service = _service()
        _, access, _, secret, _ = await _account(
            service, session, mfa=True, amr=MFA_AMR
        )
        assert secret is not None
        code = _next_code(secret)
        async with _client(service, session) as client:
            first = await client.post(
                "/auth/password-change", **_change(access, code=code)
            )
            replay = await client.post(
                "/auth/password-change",
                json={
                    "current_password": NEW_PASSWORD,
                    "new_password": "third-strong-pass-12",
                    "code": code,
                },
                headers={"Authorization": f"Bearer {first.json()['access_token']}"},
            )
        assert first.status_code == 200, first.text
        assert replay.status_code == 401, replay.text
        assert await _password_is(service, session, NEW_PASSWORD)


class TestAccountWithoutMfa:
    """No factor enrolled: a pair, never claiming a factor it did not see."""

    async def test_a_second_factor_in_the_token_is_not_copied(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        _, access, _, _, _ = await _account(service, session, mfa=False, amr=MFA_AMR)
        async with _client(service, session) as client:
            changed = await client.post("/auth/password-change", **_change(access))
        assert changed.status_code == 200, changed.text
        claims = service.jwt.decode(changed.json()["access_token"])
        assert tuple(claims["amr"]) == PASSWORD_AMR

    async def test_a_code_is_ignored(self, session: AsyncSession) -> None:
        service = _service()
        _, access, _, _, _ = await _account(
            service, session, mfa=False, amr=PASSWORD_AMR
        )
        async with _client(service, session) as client:
            changed = await client.post(
                "/auth/password-change", **_change(access, code="000000")
            )
        assert changed.status_code == 200, changed.text
        assert changed.json()["access_token"]
