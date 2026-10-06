"""The admin MFA challenge limits wrong codes and accepts a code once.

``POST /admin/mfa`` used to verify with ``TOTPHelper.verify`` and keep no
count: the same code worked again inside its window and any number of
guesses were allowed. It now goes through
``AdminAuthBackend.claim_mfa_step`` (which records ``totp_last_step``) and
a per-principal ``AttemptThrottle``, the same budget ``/auth/mfa/verify``
has.
"""

from __future__ import annotations

import pyotp
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    AttemptThrottle,
    InMemoryThrottleBackend,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.utils.datetime import utcnow
from tests.admin.test_mfa import SECRET, MfaUser, Thing, _csrf_from

PASSWORD: str = "hunter2"


async def _app(
    throttle: AttemptThrottle | None = None,
) -> tuple[FastAPI, AsyncDatabaseManager, str]:
    """Build an admin app whose only user has MFA enrolled.

    Args:
        throttle (AttemptThrottle | None): Forwarded as ``mfa_throttle``.

    Returns:
        tuple[FastAPI, AsyncDatabaseManager, str]: The app, its database
        and the user's TOTP secret.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    secret = pyotp.random_base32()
    async with db.get_session_context() as session:
        user = MfaUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password(PASSWORD)
        user.totp_secret = secret
        user.totp_enabled_at = utcnow()
        session.add(user)
        await session.commit()
    site = AdminSite(title="MFA Admin")
    site.register(AdminModel(model=Thing, list_display=[Thing.id]))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(MfaUser, mfa_issuer="Admin"),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
            mfa_throttle=throttle,
        )
    )
    return app, db, secret


async def _challenge(client: AsyncClient) -> str:
    """Log in with the password and return the MFA challenge's CSRF token.

    Args:
        client (AsyncClient): A client with its own cookie jar.

    Returns:
        str: The CSRF token of the pending session.
    """
    login = await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert login.headers["location"] == "/admin/mfa"
    page = await client.get("/admin/mfa")
    return _csrf_from(page.text)


def _client(app: FastAPI) -> AsyncClient:
    """Bind a fresh client (fresh cookies) to ``app``.

    Args:
        app (FastAPI): The application under test.

    Returns:
        AsyncClient: The client.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _wrong(secret: str) -> str:
    """Return a 6-digit code that is not valid for ``secret`` right now.

    Args:
        secret (str): The TOTP secret.

    Returns:
        str: A code outside the current window.
    """
    totp = pyotp.TOTP(secret)
    valid = {totp.at(utcnow(), offset) for offset in (-1, 0, 1)}
    return next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in valid)


@pytest.mark.asyncio
async def test_a_code_is_accepted_once() -> None:
    """The same code, replayed from a second login, is refused."""
    app, db, secret = await _app()
    try:
        code = pyotp.TOTP(secret).now()
        async with _client(app) as first:
            token = await _challenge(first)
            ok = await first.post(
                "/admin/mfa",
                data={"csrf_token": token, "code": code},
                follow_redirects=False,
            )
            assert ok.status_code == 303
        async with _client(app) as second:
            token = await _challenge(second)
            replay = await second.post(
                "/admin/mfa", data={"csrf_token": token, "code": code}
            )
        assert replay.status_code == 401
    finally:
        await db.drop_tables()
        await db.disconnect()


@pytest.mark.asyncio
async def test_wrong_codes_are_limited_per_principal() -> None:
    """After 5 wrong codes even the right one is 429, also from a new login."""
    app, db, secret = await _app()
    try:
        async with _client(app) as client:
            token = await _challenge(client)
            for _ in range(5):
                bad = await client.post(
                    "/admin/mfa", data={"csrf_token": token, "code": _wrong(secret)}
                )
                assert bad.status_code == 401
            blocked = await client.post(
                "/admin/mfa",
                data={"csrf_token": token, "code": pyotp.TOTP(secret).now()},
            )
            assert blocked.status_code == 429
            assert int(blocked.headers["retry-after"]) > 0
        async with _client(app) as fresh:
            token = await _challenge(fresh)
            still = await fresh.post(
                "/admin/mfa",
                data={"csrf_token": token, "code": pyotp.TOTP(secret).now()},
            )
        assert still.status_code == 429
    finally:
        await db.drop_tables()
        await db.disconnect()


@pytest.mark.asyncio
async def test_a_success_resets_the_budget() -> None:
    """A wrong code, the right one, then another wrong code is still a 401.

    With ``max_attempts=2`` the third ``hit`` would be over budget; it is
    not, because the success in between reset the key.
    """
    throttle = AttemptThrottle(
        InMemoryThrottleBackend(), max_attempts=2, window_seconds=900
    )
    app, db, secret = await _app(throttle)
    try:
        async with _client(app) as client:
            token = await _challenge(client)
            first = await client.post(
                "/admin/mfa", data={"csrf_token": token, "code": _wrong(secret)}
            )
            ok = await client.post(
                "/admin/mfa",
                data={"csrf_token": token, "code": pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )
        async with _client(app) as again:
            token = await _challenge(again)
            after = await again.post(
                "/admin/mfa", data={"csrf_token": token, "code": _wrong(secret)}
            )
        assert (first.status_code, ok.status_code, after.status_code) == (
            401,
            303,
            401,
        )
    finally:
        await db.drop_tables()
        await db.disconnect()
