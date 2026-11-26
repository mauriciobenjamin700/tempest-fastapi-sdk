"""``SignedCookieSessionStore`` checks ``same_site`` when it is built (#837).

Before the fix the constructor took any ``str`` and the value reached
Starlette's ``set_cookie`` behind a ``cast``: ``"foo"`` was accepted at
construction and raised ``AssertionError`` on the first login.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import SignedCookieSessionStore
from tempest_fastapi_sdk.testing import admin_login

SECRET = "s" * 48


class SameSiteUser(BaseUserModel):
    """Admin accounts of this module."""

    __tablename__ = "admin_same_site_users"


def test_unknown_same_site_fails_at_construction() -> None:
    bad: Any = "foo"
    with pytest.raises(ValueError, match="same_site must be one of"):
        SignedCookieSessionStore(SECRET, same_site=bad)


@pytest.mark.parametrize(
    ("given", "stored"),
    [("lax", "lax"), ("Strict", "strict"), ("NONE", "none")],
)
def test_same_site_is_matched_case_insensitively(given: str, stored: str) -> None:
    value: Any = given
    store = SignedCookieSessionStore(SECRET, same_site=value)
    assert store.same_site == stored


@pytest.fixture
async def strict_app() -> AsyncIterator[FastAPI]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = SameSiteUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
    mixed: Any = "Strict"
    app = FastAPI()
    app.include_router(
        make_admin_router(
            AdminSite(title="SameSite"),
            db=db,
            auth_backend=UserModelAuthBackend(SameSiteUser),
            secret_key=SECRET,
            cookie_secure=False,
            session_store=SignedCookieSessionStore(
                SECRET, secure=False, same_site=mixed
            ),
        )
    )
    yield app
    await db.drop_tables()
    await db.disconnect()


@pytest.mark.asyncio
async def test_login_sets_the_normalized_policy(strict_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=strict_app), base_url="http://test"
    ) as client:
        response = await admin_login(client, "root@example.com", "hunter2")
    assert response.status_code == 303
    cookies = [
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith("tempest_admin=")
    ]
    assert len(cookies) == 1
    assert "SameSite=strict" in cookies[0]
