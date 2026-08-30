"""Tests for :class:`HoneypotBanMiddleware` and its ban stores."""

from __future__ import annotations

import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    DEFAULT_HONEYPOT_PATTERNS,
    BanStore,
    HoneypotBanMiddleware,
    MemoryBanStore,
    RedisBanStore,
)


class BrokenBanStore:
    """A :class:`BanStore` whose every call fails, to prove fail-open."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.ban_attempts: list[str] = []

    async def is_banned(self, ip: str) -> bool:
        """Fail the lookup.

        Args:
            ip (str): The resolved client IP.

        Raises:
            ConnectionError: Always.
        """
        raise ConnectionError(f"redis is down (lookup for {ip})")

    async def ban(self, ip: str, *, ttl_seconds: int, reason: str) -> None:
        """Fail the write, after recording that it was attempted.

        Args:
            ip (str): The resolved client IP.
            ttl_seconds (int): Ignored.
            reason (str): Ignored.

        Raises:
            ConnectionError: Always.
        """
        del ttl_seconds, reason
        self.ban_attempts.append(ip)
        raise ConnectionError("redis is down (write)")


def build_app(store: BanStore, **options: object) -> FastAPI:
    """Build an app guarded by the honeypot middleware.

    Args:
        store (BanStore): Where bans are kept.
        **options (object): Forwarded to :class:`HoneypotBanMiddleware`.

    Returns:
        FastAPI: The configured application.
    """
    app: FastAPI = FastAPI()

    @app.get("/api/users")
    def users() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/security.txt")
    def security_txt() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/wp-admin/")
    def wp_admin() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(HoneypotBanMiddleware, store=store, **options)
    return app


class TestHoneypotMatching:
    """The curated list is the deliverable; these fix its edges."""

    @pytest.mark.parametrize(
        "target",
        [
            "/.env",
            "/.env.local",
            "/.git/config",
            "/.aws/credentials",
            "/.ssh/id_rsa",
            "/wp-admin/",
            "/wp-login.php",
            "/xmlrpc.php",
            "/phpmyadmin/",
            "/cgi-bin/php?-d+allow_url_include%3d1",
            "/info.php",
            "/server-status",
            "/backup/db.sql",
            "/config.json",
            "/dump.tar.gz",
            "/index.php.bak",
            "/app.py~",
        ],
    )
    def test_scanner_signature_matches(self, target: str) -> None:
        """Each of these is a path no honest client requests."""
        middleware = HoneypotBanMiddleware(
            app=lambda scope, receive, send: None,  # type: ignore[arg-type,return-value]
            store=MemoryBanStore(),
        )
        assert middleware.matches(target), target

    @pytest.mark.parametrize(
        "target",
        [
            "/",
            "/api/users",
            "/api/users?page=2",
            "/health/liveness",
            "/.well-known/security.txt",
            "/.well-known/acme-challenge/tokenvalue",
            "/docs",
            "/openapi.json",
            "/static/app.css",
            "/api/events/2026-08-30",
        ],
    )
    def test_honest_target_does_not_match(self, target: str) -> None:
        """A false positive here bans a real user, so these are pinned."""
        middleware = HoneypotBanMiddleware(
            app=lambda scope, receive, send: None,  # type: ignore[arg-type,return-value]
            store=MemoryBanStore(),
        )
        assert not middleware.matches(target), target

    def test_the_shipped_set_is_not_empty(self) -> None:
        """A silently empty list would disable the middleware."""
        assert len(DEFAULT_HONEYPOT_PATTERNS) >= 30
        assert all(isinstance(p, re.Pattern) for p in DEFAULT_HONEYPOT_PATTERNS)


class TestHoneypotBanning:
    """One probe earns a ban; the ban is what the next request meets."""

    def test_probe_is_refused_and_bans_the_caller(self) -> None:
        """The offending request gets 403, and so does the next one."""
        store = MemoryBanStore()
        with TestClient(build_app(store)) as client:
            assert client.get("/.env").status_code == 403
            assert client.get("/api/users").status_code == 403

    def test_honest_traffic_passes_through(self) -> None:
        """Nothing changes for a client that never probes."""
        with TestClient(build_app(MemoryBanStore())) as client:
            assert client.get("/api/users").status_code == 200

    def test_the_response_discloses_nothing(self) -> None:
        """A scanner that learns it is banned learns to rotate."""
        with TestClient(build_app(MemoryBanStore())) as client:
            response = client.get("/.env")
        assert response.json() == {"detail": "Forbidden"}
        assert "ban" not in response.text.lower()

    def test_status_code_is_configurable(self) -> None:
        """A service that prefers 404 to 403 can say so."""
        app = build_app(MemoryBanStore(), status_code=404)
        with TestClient(app) as client:
            assert client.get("/.env").status_code == 404

    def test_exempt_prefix_wins_over_the_pattern(self) -> None:
        """The service that legitimately serves a flagged path opts out."""
        app = build_app(MemoryBanStore(), exempt_paths=("/wp-admin",))
        with TestClient(app) as client:
            assert client.get("/wp-admin/").status_code == 200
            assert client.get("/api/users").status_code == 200

    def test_the_ban_is_logged_with_the_offending_target(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An operator can see what earned the ban."""
        logger_name = "tempest_fastapi_sdk.api.middlewares.honeypot"
        with (
            caplog.at_level(logging.WARNING, logger=logger_name),
            TestClient(build_app(MemoryBanStore())) as client,
        ):
            client.get("/.git/config")

        record = next(r for r in caplog.records if r.name == logger_name)
        assert record.http_target == "/.git/config"
        assert record.ban_seconds == 86_400


class TestHoneypotFailsOpen:
    """A blocklist that cannot reach its store must not take the API down."""

    def test_lookup_failure_lets_honest_traffic_through(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A store outage is a warning, not a 500."""
        logger_name = "tempest_fastapi_sdk.api.middlewares.honeypot"
        with (
            caplog.at_level(logging.WARNING, logger=logger_name),
            TestClient(build_app(BrokenBanStore())) as client,
        ):
            assert client.get("/api/users").status_code == 200

        assert any(
            "letting request through" in r.getMessage()
            for r in caplog.records
            if r.name == logger_name
        )

    def test_write_failure_still_refuses_the_probe(self) -> None:
        """The ban is what is lost to an outage, not the rejection."""
        store = BrokenBanStore()
        with TestClient(build_app(store)) as client:
            assert client.get("/.env").status_code == 403
        assert store.ban_attempts


class TestHoneypotClientIp:
    """Banning the wrong IP is worse than not banning at all."""

    def test_spoofed_forwarded_header_is_ignored_by_default(self) -> None:
        """With no trusted header, the transport peer is what counts."""
        store = MemoryBanStore()
        with TestClient(build_app(store)) as client:
            client.get("/.env", headers={"X-Forwarded-For": "8.8.8.8"})
        assert not (await_sync(store.is_banned("8.8.8.8")))
        assert await_sync(store.is_banned("testclient"))

    def test_trusted_header_selects_the_banned_address(self) -> None:
        """The edge-set header is the one the ban is keyed on."""
        store = MemoryBanStore()
        app = build_app(store, trusted_ip_header="x-real-ip")
        with TestClient(app) as client:
            client.get("/.env", headers={"X-Real-IP": "203.0.113.7"})
        assert await_sync(store.is_banned("203.0.113.7"))
        assert not (await_sync(store.is_banned("testclient")))


class TestMemoryBanStore:
    """The in-process store, including the expiry it owns."""

    async def test_ban_expires(self) -> None:
        """A ban whose TTL has passed reports as absent."""
        store = MemoryBanStore()
        await store.ban("1.2.3.4", ttl_seconds=0, reason="/.env")
        assert await store.is_banned("1.2.3.4") is False

    async def test_unknown_ip_is_not_banned(self) -> None:
        """An address never seen is free to call."""
        assert await MemoryBanStore().is_banned("1.2.3.4") is False


class TestRedisBanStore:
    """The store a real deployment uses, against a real client shape."""

    async def test_round_trip_against_fakeredis(self) -> None:
        """``fakeredis`` is one of the two clients the docs name."""
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.aioredis.FakeRedis()
        store = RedisBanStore(client)
        assert await store.is_banned("1.2.3.4") is False
        await store.ban("1.2.3.4", ttl_seconds=60, reason="/.env")
        assert await store.is_banned("1.2.3.4") is True
        assert await client.ttl("honeypot:ban:1.2.3.4") > 0

    async def test_prefix_is_configurable(self) -> None:
        """Ban keys stay out of the way of other cached data."""
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.aioredis.FakeRedis()
        store = RedisBanStore(client, prefix="abuse:")
        await store.ban("1.2.3.4", ttl_seconds=60, reason="/.env")
        assert await client.get("abuse:1.2.3.4") == b"/.env"


def await_sync(awaitable: object) -> bool:
    """Run a store coroutine from a synchronous test.

    The HTTP assertions above drive :class:`TestClient`, which owns its own
    loop, so the store has to be inspected outside it.

    Args:
        awaitable (object): The coroutine returned by a store method.

    Returns:
        bool: Whatever the coroutine resolved to.
    """
    import asyncio
    import typing

    coro = typing.cast(typing.Coroutine[object, object, bool], awaitable)
    return asyncio.run(coro)
