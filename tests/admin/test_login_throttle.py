"""``POST /admin/login`` failure budgets, per identifier and per client IP."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    AttemptThrottle,
    BaseModel,
    BaseUserModel,
    InMemoryThrottleBackend,
    UserModelAuthBackend,
    login_throttle_key,
    make_admin_router,
)
from tempest_fastapi_sdk.testing import admin_login

SECRET = "x" * 48


class ThrottleUser(BaseUserModel):
    __tablename__ = "admin_login_throttle_users"


class ThrottleThing(BaseModel):
    __tablename__ = "admin_login_throttle_thing"


class Harness:
    """An admin app plus the throttles it was built with.

    Attributes:
        app (FastAPI): The application.
        db (AsyncDatabaseManager): Its database.
        login_throttle (AttemptThrottle): Per-identifier budget.
        ip_throttle (AttemptThrottle): Per-IP budget.
    """

    def __init__(
        self,
        app: FastAPI,
        db: AsyncDatabaseManager,
        login_throttle: AttemptThrottle,
        ip_throttle: AttemptThrottle,
    ) -> None:
        """Store the parts.

        Args:
            app (FastAPI): The application.
            db (AsyncDatabaseManager): Its database.
            login_throttle (AttemptThrottle): Per-identifier budget.
            ip_throttle (AttemptThrottle): Per-IP budget.
        """
        self.app: FastAPI = app
        self.db: AsyncDatabaseManager = db
        self.login_throttle: AttemptThrottle = login_throttle
        self.ip_throttle: AttemptThrottle = ip_throttle


async def _build(
    *,
    identifier_budget: int = 3,
    ip_budget: int = 100,
    trusted_ip_header: str | None = None,
    login_throttle: AttemptThrottle | Literal[False] | None = None,
    login_ip_throttle: AttemptThrottle | Literal[False] | None = None,
) -> Harness:
    """Build an admin app with two admins and explicit budgets.

    Args:
        identifier_budget (int): Attempts per identifier.
        ip_budget (int): Failures per IP.
        trusted_ip_header (str | None): Forwarded to the router.
        login_throttle (AttemptThrottle | Literal[False] | None): Overrides
            the per-identifier throttle passed to the router.
        login_ip_throttle (AttemptThrottle | Literal[False] | None):
            Overrides the per-IP throttle passed to the router.

    Returns:
        Harness: The app and its throttles.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        for email in ("root@example.com", "ops@example.com"):
            user = ThrottleUser(email=email, hashed_password="", is_admin=True)
            user.set_password("hunter2")
            session.add(user)
        await session.commit()
    identifier_throttle = AttemptThrottle(
        InMemoryThrottleBackend(), max_attempts=identifier_budget, window_seconds=900
    )
    ip_throttle = AttemptThrottle(
        InMemoryThrottleBackend(), max_attempts=ip_budget, window_seconds=900
    )
    site = AdminSite(title="Throttle Admin")
    site.register(AdminModel(model=ThrottleThing, list_display=[ThrottleThing.id]))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ThrottleUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
            login_throttle=identifier_throttle
            if login_throttle is None
            else login_throttle,
            login_ip_throttle=ip_throttle
            if login_ip_throttle is None
            else login_ip_throttle,
            trusted_ip_header=trusted_ip_header,
        )
    )
    return Harness(app, db, identifier_throttle, ip_throttle)


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    built = await _build()
    yield built
    await built.db.drop_tables()
    await built.db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(
    client: AsyncClient,
    identifier: str,
    password: str,
    headers: dict[str, str] | None = None,
) -> Response:
    return await admin_login(client, identifier, password, headers=headers or {})


class TestIdentifierBudget:
    async def test_spent_budget_refuses_the_right_password(
        self, harness: Harness
    ) -> None:
        async with _client(harness.app) as client:
            for _ in range(3):
                assert (
                    await _login(client, "root@example.com", "wrong")
                ).status_code == 401
            blocked = await _login(client, "root@example.com", "hunter2")
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        assert "Too many failed logins" in blocked.text
        assert "tempest_admin=" not in blocked.headers.get("set-cookie", "")

    async def test_budget_is_keyed_on_the_normalized_identifier(
        self, harness: Harness
    ) -> None:
        async with _client(harness.app) as client:
            for variant in (
                "root@example.com",
                "ROOT@example.com",
                " root@example.com ",
            ):
                await _login(client, variant, "wrong")
            blocked = await _login(client, "Root@Example.com", "hunter2")
        assert blocked.status_code == 429
        status = await harness.login_throttle.status(
            login_throttle_key("root@example.com")
        )
        assert status.attempts == 4

    async def test_success_resets_the_identifier(self, harness: Harness) -> None:
        async with _client(harness.app) as client:
            for _ in range(2):
                await _login(client, "root@example.com", "wrong")
            ok = await _login(client, "root@example.com", "hunter2")
            assert ok.status_code == 303
            for _ in range(2):
                await _login(client, "root@example.com", "wrong")
            again = await _login(client, "root@example.com", "hunter2")
        assert again.status_code == 303

    async def test_unknown_identifier_spends_the_same_budget(
        self, harness: Harness
    ) -> None:
        """The ``429`` must not tell which addresses have an account."""
        async with _client(harness.app) as client:
            for _ in range(3):
                await _login(client, "ghost@example.com", "x")
            blocked = await _login(client, "ghost@example.com", "x")
        assert blocked.status_code == 429

    async def test_other_identifiers_are_unaffected(self, harness: Harness) -> None:
        async with _client(harness.app) as client:
            for _ in range(4):
                await _login(client, "root@example.com", "wrong")
            other = await _login(client, "ops@example.com", "hunter2")
        assert other.status_code == 303


class TestIpBudget:
    async def test_ip_budget_spans_identifiers(self) -> None:
        built = await _build(identifier_budget=100, ip_budget=3)
        try:
            async with _client(built.app) as client:
                await _login(client, "root@example.com", "wrong")
                await _login(client, "ops@example.com", "wrong")
                await _login(client, "ghost@example.com", "wrong")
                blocked = await _login(client, "ops@example.com", "hunter2")
            assert blocked.status_code == 429
            assert "Retry-After" in blocked.headers
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_success_does_not_reset_the_ip(self) -> None:
        built = await _build(identifier_budget=100, ip_budget=3)
        try:
            async with _client(built.app) as client:
                await _login(client, "root@example.com", "wrong")
                await _login(client, "root@example.com", "wrong")
                assert (
                    await _login(client, "ops@example.com", "hunter2")
                ).status_code == 303
                await _login(client, "root@example.com", "wrong")
                blocked = await _login(client, "root@example.com", "hunter2")
            assert blocked.status_code == 429
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_blocked_ip_does_not_spend_the_identifier_budget(self) -> None:
        built = await _build(identifier_budget=100, ip_budget=1)
        try:
            async with _client(built.app) as client:
                await _login(client, "ghost@example.com", "wrong")
                await _login(client, "root@example.com", "hunter2")
            status = await built.login_throttle.status(
                login_throttle_key("root@example.com")
            )
            assert status.attempts == 0
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_trusted_header_separates_clients(self) -> None:
        built = await _build(
            identifier_budget=100, ip_budget=2, trusted_ip_header="x-real-ip"
        )
        try:
            async with _client(built.app) as client:
                for _ in range(2):
                    await _login(
                        client, "ghost@example.com", "x", {"x-real-ip": "203.0.113.7"}
                    )
                blocked = await _login(
                    client, "root@example.com", "hunter2", {"x-real-ip": "203.0.113.7"}
                )
                other = await _login(
                    client, "root@example.com", "hunter2", {"x-real-ip": "198.51.100.9"}
                )
            assert blocked.status_code == 429
            assert other.status_code == 303
            assert (
                await built.ip_throttle.status("admin-login-ip:203.0.113.7")
            ).blocked
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_without_trusted_header_the_header_is_ignored(self) -> None:
        """A client cannot pick its own budget by sending the header."""
        built = await _build(identifier_budget=100, ip_budget=2)
        try:
            async with _client(built.app) as client:
                for ip in ("203.0.113.1", "203.0.113.2"):
                    await _login(client, "ghost@example.com", "x", {"x-real-ip": ip})
                blocked = await _login(
                    client, "root@example.com", "hunter2", {"x-real-ip": "203.0.113.3"}
                )
            assert blocked.status_code == 429
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()


async def _build_defaults(trusted_ip_header: str | None = None) -> Harness:
    """Build an admin app with the router's default throttles.

    Args:
        trusted_ip_header (str | None): Forwarded to the router.

    Returns:
        Harness: The app; its throttle attributes are unused placeholders.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        for email in ("root@example.com", "ops@example.com"):
            user = ThrottleUser(email=email, hashed_password="", is_admin=True)
            user.set_password("hunter2")
            session.add(user)
        await session.commit()
    site = AdminSite(title="Defaults")
    site.register(AdminModel(model=ThrottleThing, list_display=[ThrottleThing.id]))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ThrottleUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
            trusted_ip_header=trusted_ip_header,
        )
    )
    unused = AttemptThrottle(
        InMemoryThrottleBackend(), max_attempts=1, window_seconds=1
    )
    return Harness(app, db, unused, unused)


class TestDefaults:
    async def test_default_identifier_budget_is_five(self) -> None:
        built = await _build_defaults()
        try:
            async with _client(built.app) as client:
                statuses = [
                    (await _login(client, "root@example.com", "wrong")).status_code
                    for _ in range(5)
                ]
                blocked = await _login(client, "root@example.com", "hunter2")
            assert statuses == [401] * 5
            assert blocked.status_code == 429
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_ip_budget_is_off_without_a_trusted_header(self) -> None:
        """Behind a proxy every operator shares one peer address.

        A default-on IP budget there let anyone lock every operator out
        with twenty bad logins.
        """
        built = await _build_defaults()
        try:
            async with _client(built.app) as client:
                for n in range(25):
                    await _login(client, f"ghost{n}@example.com", "x")
                ok = await _login(client, "ops@example.com", "hunter2")
            assert ok.status_code == 303
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_ip_budget_defaults_to_twenty_with_a_trusted_header(self) -> None:
        built = await _build_defaults(trusted_ip_header="x-real-ip")
        headers = {"x-real-ip": "203.0.113.7"}
        try:
            async with _client(built.app) as client:
                for n in range(20):
                    await _login(client, f"ghost{n}@example.com", "x", headers)
                blocked = await _login(client, "ops@example.com", "hunter2", headers)
                other = await _login(
                    client, "ops@example.com", "hunter2", {"x-real-ip": "198.51.100.1"}
                )
            assert blocked.status_code == 429
            assert other.status_code == 303
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_garbage_header_counts_against_the_peer(self) -> None:
        built = await _build(
            identifier_budget=100, ip_budget=2, trusted_ip_header="x-real-ip"
        )
        try:
            async with _client(built.app) as client:
                for n in range(2):
                    await _login(
                        client, "ghost@example.com", "x", {"x-real-ip": f"junk-{n}"}
                    )
                blocked = await _login(
                    client, "root@example.com", "hunter2", {"x-real-ip": "junk-9"}
                )
            assert blocked.status_code == 429
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()

    async def test_false_turns_both_budgets_off(self) -> None:
        built = await _build(login_throttle=False, login_ip_throttle=False)
        try:
            async with _client(built.app) as client:
                for _ in range(30):
                    await _login(client, "root@example.com", "wrong")
                ok = await _login(client, "root@example.com", "hunter2")
            assert ok.status_code == 303
        finally:
            await built.db.drop_tables()
            await built.db.disconnect()


def test_key_matches_the_auth_router_shape() -> None:
    assert login_throttle_key(" Root@Example.com ") == login_throttle_key(
        "root@example.com"
    )
    assert login_throttle_key("root@example.com").startswith("login:")
    assert "root" not in login_throttle_key("root@example.com")
