"""End-to-end tests for MFA (TOTP) on the admin login flow."""

from __future__ import annotations

import re

import pyotp
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    MFAMixin,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.utils.datetime import utcnow

SECRET = "x" * 48


class MfaUser(MFAMixin, BaseUserModel):
    __tablename__ = "admin_mfa_users"


class Thing(BaseModel):
    __tablename__ = "admin_mfa_thing"


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None, "csrf token not found"
    return match.group(1)


async def _build_app(*, with_mfa: bool) -> tuple[FastAPI, AsyncDatabaseManager, str]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    totp_secret = pyotp.random_base32()
    async with db.get_session_context() as session:
        user = MfaUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        if with_mfa:
            user.totp_secret = totp_secret
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
        )
    )
    return app, db, totp_secret


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_non_mfa_user_logs_in_directly() -> None:
    app, db, _secret = await _build_app(with_mfa=False)
    try:
        async with _client(app) as client:
            login = await client.post(
                "/admin/login",
                data={"identifier": "root@example.com", "password": "hunter2"},
                follow_redirects=False,
            )
            assert login.status_code == 303
            assert login.headers["location"] == "/admin/"
            dash = await client.get("/admin/")
        assert dash.status_code == 200
    finally:
        await db.drop_tables()
        await db.disconnect()


@pytest.mark.asyncio
async def test_mfa_user_is_challenged_then_admitted() -> None:
    app, db, secret = await _build_app(with_mfa=True)
    try:
        async with _client(app) as client:
            login = await client.post(
                "/admin/login",
                data={"identifier": "root@example.com", "password": "hunter2"},
                follow_redirects=False,
            )
            # Password OK but redirected to the MFA challenge, not the app.
            assert login.status_code == 303
            assert login.headers["location"] == "/admin/mfa"

            # Pending session must not grant access to admin pages.
            blocked = await client.get("/admin/", follow_redirects=False)
            assert blocked.status_code == 303
            assert blocked.headers["location"] == "/admin/mfa"

            challenge = await client.get("/admin/mfa")
            assert challenge.status_code == 200
            assert "Two-factor" in challenge.text
            token = _csrf_from(challenge.text)

            # Wrong code → re-rendered challenge, still no access.
            bad = await client.post(
                "/admin/mfa",
                data={"csrf_token": token, "code": "000000"},
            )
            assert bad.status_code == 401

            # Correct code → upgraded session + access.
            good = await client.post(
                "/admin/mfa",
                data={"csrf_token": token, "code": pyotp.TOTP(secret).now()},
                follow_redirects=False,
            )
            assert good.status_code == 303
            assert good.headers["location"] == "/admin/"

            dash = await client.get("/admin/")
        assert dash.status_code == 200
    finally:
        await db.drop_tables()
        await db.disconnect()


@pytest.mark.asyncio
async def test_mfa_csrf_mismatch_403() -> None:
    app, db, secret = await _build_app(with_mfa=True)
    try:
        async with _client(app) as client:
            await client.post(
                "/admin/login",
                data={"identifier": "root@example.com", "password": "hunter2"},
            )
            response = await client.post(
                "/admin/mfa",
                data={"csrf_token": "bogus", "code": pyotp.TOTP(secret).now()},
            )
        assert response.status_code == 403
    finally:
        await db.drop_tables()
        await db.disconnect()


@pytest.mark.asyncio
async def test_mfa_challenge_without_session_redirects_login() -> None:
    app, db, _secret = await _build_app(with_mfa=True)
    try:
        async with _client(app) as client:
            response = await client.get("/admin/mfa", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"
    finally:
        await db.drop_tables()
        await db.disconnect()
