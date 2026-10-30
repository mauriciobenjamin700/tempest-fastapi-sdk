"""A copied admin cookie stops working at logout and at a password change.

The default admin session is a stateless signed cookie. Clearing it on
logout only asked the browser to forget it, and nothing tied it to the
credential, so a copy taken before ``POST /logout`` or before a password
change kept full access until ``max_age`` (8h). Each test here captures the
cookie first, performs the remediation the operator would, and replays the
copy from a different client — the order the defect had.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any

import itsdangerous
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
)
from tempest_fastapi_sdk.admin import AdminSessionRevocations
from tempest_fastapi_sdk.admin.router import make_admin_router
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login_sync

SECRET = "s" * 48
COOKIE = "tempest_admin"


class RevocationUser(BaseUserModel):
    """The admin whose cookie the tests copy."""

    __tablename__ = "admin_session_revocation_users"


SLUG: str = AdminModel(model=RevocationUser).get_slug()


class _UnboundBackend(UserModelAuthBackend):
    """A backend that opts out of credential binding."""

    def credential_fingerprint(self, principal: BaseUserModel) -> str | None:
        """Bind nothing.

        Args:
            principal (BaseUserModel): The authenticated row.

        Returns:
            str | None: Always ``None``.
        """
        return None


@dataclass
class Harness:
    """What every test drives.

    Attributes:
        db (AsyncDatabaseManager): The admin's database.
        apps (list[FastAPI]): One app per simulated replica.
    """

    db: AsyncDatabaseManager
    apps: list[FastAPI]

    def client(self, replica: int = 0) -> TestClient:
        """Return a fresh client on one replica, not following redirects.

        Args:
            replica (int): Index into :attr:`apps`.

        Returns:
            TestClient: The client.
        """
        return TestClient(self.apps[replica], follow_redirects=False)

    def login(self, replica: int = 0) -> tuple[TestClient, str]:
        """Sign in and return the client plus a copy of its cookie.

        Args:
            replica (int): Index into :attr:`apps`.

        Returns:
            tuple[TestClient, str]: The signed-in client and the cookie.
        """
        client = self.client(replica)
        response = admin_login_sync(client, "root@example.com", "hunter2")
        assert response.status_code == 303, response.text
        cookie = client.cookies.get(COOKIE)
        assert cookie
        return client, cookie

    def replay(self, cookie: str, path: str = "/admin/", replica: int = 0) -> Any:
        """Send ``cookie`` from a client that never logged in.

        Args:
            cookie (str): The copied cookie value.
            path (str): The page to request.
            replica (int): Index into :attr:`apps`.

        Returns:
            Any: The response.
        """
        attacker = self.client(replica)
        attacker.cookies.set(COOKIE, cookie, path="/admin")
        return attacker.get(path)


async def _build(
    *,
    backend: UserModelAuthBackend | None = None,
    revocations: list[AdminSessionRevocations | None] | None = None,
) -> Harness:
    """Seed one admin and mount one router per replica.

    Args:
        backend (UserModelAuthBackend | None): Auth backend; defaults to
            the stock :class:`UserModelAuthBackend`.
        revocations (list[AdminSessionRevocations | None] | None): One
            entry per replica; ``None`` keeps the router default.

    Returns:
        Harness: The harness.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = RevocationUser(email="root@example.com", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
    apps: list[FastAPI] = []
    for entry in revocations or [None]:
        site = AdminSite(title="Revocation Admin")
        site.register(AdminModel(model=RevocationUser))
        app = FastAPI()
        app.include_router(
            make_admin_router(
                site,
                db=db,
                auth_backend=backend or UserModelAuthBackend(RevocationUser),
                secret_key=SECRET,
                cookie_secure=False,
                session_revocations=entry,
            )
        )
        apps.append(app)
    return Harness(db=db, apps=apps)


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    """Yield a single-replica harness.

    Yields:
        Harness: The harness, torn down with its database.
    """
    built = await _build()
    yield built
    await built.db.drop_tables()
    await built.db.disconnect()


async def _change_password(db: AsyncDatabaseManager, password: str) -> None:
    """Write a new password on the seeded admin.

    Args:
        db (AsyncDatabaseManager): The admin's database.
        password (str): The new plaintext password.
    """
    async with db.get_session_context() as session:
        row = (await session.execute(select(RevocationUser))).scalar_one()
        row.set_password(password)


def _cookie_value(cookie: str) -> str:
    """Undo the cookie jar's quoting so the signed payload can be read.

    Args:
        cookie (str): The value as the client jar stores it.

    Returns:
        str: The raw signed value the router wrote.
    """
    jar: SimpleCookie = SimpleCookie()
    jar.load(f"{COOKIE}={cookie}")
    return jar[COOKIE].value


def _logout(client: TestClient) -> None:
    """Submit the dashboard's logout form.

    Args:
        client (TestClient): The signed-in client.
    """
    token = admin_csrf_token(client.get("/admin/").text)
    response = client.post("/admin/logout", data={"csrf_token": token})
    assert response.status_code == 303


class TestLogout:
    async def test_copied_cookie_is_refused_after_logout(
        self,
        harness: Harness,
    ) -> None:
        victim, cookie = harness.login()
        assert harness.replay(cookie).status_code == 200

        _logout(victim)
        replayed = harness.replay(cookie)

        assert replayed.status_code == 303
        assert replayed.headers["location"] == "/admin/login"

    async def test_logout_ends_only_its_own_session(self, harness: Harness) -> None:
        laptop, _ = harness.login()
        _, phone_cookie = harness.login()

        _logout(laptop)

        assert harness.replay(phone_cookie).status_code == 200

    async def test_logged_out_cookie_does_not_reach_the_mfa_step(
        self,
        harness: Harness,
    ) -> None:
        victim, cookie = harness.login()
        _logout(victim)

        replayed = harness.replay(cookie, "/admin/mfa")

        assert replayed.headers["location"] == "/admin/login"

    async def test_shared_backend_carries_logout_across_replicas(self) -> None:
        fake_aioredis = pytest.importorskip("fakeredis.aioredis")
        redis = fake_aioredis.FakeRedis()
        built = await _build(
            revocations=[
                AdminSessionRevocations(redis),
                AdminSessionRevocations(redis),
            ],
        )
        try:
            victim, cookie = built.login(replica=0)
            assert built.replay(cookie, replica=1).status_code == 200

            _logout(victim)

            assert built.replay(cookie, replica=1).status_code == 303
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()


class TestCredentialBinding:
    async def test_copied_cookie_is_refused_after_password_change(
        self,
        harness: Harness,
    ) -> None:
        _, cookie = harness.login()

        await _change_password(harness.db, "a-brand-new-password")

        for path in (
            "/admin/",
            f"/admin/m/{SLUG}/",
        ):
            replayed = harness.replay(cookie, path)
            assert replayed.status_code == 303, path
            assert replayed.headers["location"] == "/admin/login"

    async def test_new_login_after_password_change_works(
        self,
        harness: Harness,
    ) -> None:
        await _change_password(harness.db, "hunter2")
        _, cookie = harness.login()

        assert harness.replay(cookie).status_code == 200

    async def test_cookie_without_digest_is_refused(self, harness: Harness) -> None:
        _, cookie = harness.login()
        signer = itsdangerous.TimestampSigner(
            SECRET,
            salt="tempest-admin-session",
        )
        payload = json.loads(signer.unsign(_cookie_value(cookie)).decode("utf-8"))
        legacy = {key: payload[key] for key in ("pid", "iat", "csrf", "mfap")}
        forged = signer.sign(json.dumps(legacy).encode("utf-8")).decode("ascii")

        assert harness.replay(forged).status_code == 303

    async def test_backend_without_fingerprint_keeps_sessions(self) -> None:
        built = await _build(backend=_UnboundBackend(RevocationUser))
        try:
            _, cookie = built.login()

            await _change_password(built.db, "a-brand-new-password")

            assert built.replay(cookie).status_code == 200
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_fingerprint_never_reaches_the_cookie(
        self,
        harness: Harness,
    ) -> None:
        _, cookie = harness.login()
        async with harness.db.get_session_context() as session:
            row = (await session.execute(select(RevocationUser))).scalar_one()
            hashed = row.hashed_password

        assert hashed not in cookie
        signer = itsdangerous.TimestampSigner(
            SECRET,
            salt="tempest-admin-session",
        )
        payload: dict[str, Any] = json.loads(
            signer.unsign(_cookie_value(cookie)).decode("utf-8")
        )
        assert len(payload["cfp"]) == 32
        assert hashed not in json.dumps(payload)
