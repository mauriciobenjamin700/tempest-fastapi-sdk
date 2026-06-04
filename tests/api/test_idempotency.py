"""Tests for the idempotency middleware + stores."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    IDEMPOTENCY_HEADER,
    CachedResponse,
    IdempotencyMiddleware,
    IdempotencyStore,
    MemoryIdempotencyStore,
    RedisIdempotencyStore,
)


class TestMemoryStore:
    async def test_get_returns_none_when_missing(self) -> None:
        store = MemoryIdempotencyStore()
        assert await store.get("nope") is None

    async def test_set_then_get(self) -> None:
        store = MemoryIdempotencyStore()
        cached = CachedResponse(
            status_code=201,
            headers=[("content-type", "application/json")],
            body=b'{"ok":true}',
            media_type="application/json",
        )
        await store.set("k", cached, ttl_seconds=10)
        retrieved = await store.get("k")
        assert retrieved == cached

    async def test_ttl_expires(self) -> None:
        store = MemoryIdempotencyStore()
        cached = CachedResponse(
            status_code=200,
            headers=[],
            body=b"",
            media_type=None,
        )
        await store.set("k", cached, ttl_seconds=-1)
        assert await store.get("k") is None

    def test_satisfies_protocol(self) -> None:
        assert isinstance(MemoryIdempotencyStore(), IdempotencyStore)


class _FakeRedis:
    """Tiny async stand-in for the methods RedisIdempotencyStore uses."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        del ex  # TTL not enforced in this fake
        self.data[key] = value


class TestRedisStore:
    async def test_roundtrip(self) -> None:
        fake = _FakeRedis()
        store = RedisIdempotencyStore(fake)
        cached = CachedResponse(
            status_code=200,
            headers=[("x-trace", "abc")],
            body=b"\x00\xff\x10",  # ensure base64 path covers binary
            media_type="application/octet-stream",
        )
        await store.set("k", cached, ttl_seconds=60)
        out = await store.get("k")
        assert out == cached
        assert "idem:k" in fake.data

    async def test_missing_returns_none(self) -> None:
        store = RedisIdempotencyStore(_FakeRedis())
        assert await store.get("ghost") is None


def _make_app(store: IdempotencyStore) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        IdempotencyMiddleware,
        store=store,
        ttl_seconds=60,
    )

    counter = {"n": 0}

    @app.post("/charge")
    async def charge() -> dict[str, Any]:
        counter["n"] += 1
        return {"call": counter["n"]}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        counter["n"] += 1
        return {"call": counter["n"]}

    return app


class TestIdempotencyMiddleware:
    async def test_replays_cached_response_for_same_key(self) -> None:
        app = _make_app(MemoryIdempotencyStore())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.post("/charge", headers={IDEMPOTENCY_HEADER: "k1"})
            r2 = await c.post("/charge", headers={IDEMPOTENCY_HEADER: "k1"})
        assert r1.status_code == 200
        assert r1.json() == {"call": 1}
        assert r2.json() == {"call": 1}  # replayed, handler not re-invoked

    async def test_different_keys_hit_handler(self) -> None:
        app = _make_app(MemoryIdempotencyStore())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.post("/charge", headers={IDEMPOTENCY_HEADER: "a"})
            r2 = await c.post("/charge", headers={IDEMPOTENCY_HEADER: "b"})
        assert r1.json() == {"call": 1}
        assert r2.json() == {"call": 2}

    async def test_get_requests_pass_through(self) -> None:
        app = _make_app(MemoryIdempotencyStore())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.get("/status", headers={IDEMPOTENCY_HEADER: "x"})
            r2 = await c.get("/status", headers={IDEMPOTENCY_HEADER: "x"})
        assert r1.json() == {"call": 1}
        assert r2.json() == {"call": 2}  # GET not cached

    async def test_missing_header_passes_through(self) -> None:
        app = _make_app(MemoryIdempotencyStore())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.post("/charge")
            r2 = await c.post("/charge")
        assert r1.json() == {"call": 1}
        assert r2.json() == {"call": 2}

    async def test_keys_are_scoped_per_path(self) -> None:
        app = FastAPI()
        app.add_middleware(
            IdempotencyMiddleware,
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
        )

        @app.post("/a")
        async def a() -> dict[str, str]:
            return {"endpoint": "a"}

        @app.post("/b")
        async def b() -> dict[str, str]:
            return {"endpoint": "b"}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            ra = await c.post("/a", headers={IDEMPOTENCY_HEADER: "same"})
            rb = await c.post("/b", headers={IDEMPOTENCY_HEADER: "same"})
        assert ra.json() == {"endpoint": "a"}
        assert rb.json() == {"endpoint": "b"}
