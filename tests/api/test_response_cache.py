"""Tests for ResponseCacheMiddleware (ETag / conditional-GET / server cache)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    MemoryResponseCacheStore,
    ResponseCacheMiddleware,
    ResponseCacheStore,
)


def _make_app(**mw_kwargs: Any) -> tuple[FastAPI, dict[str, int]]:
    """Build a tiny app; returns (app, call-counter)."""
    app = FastAPI()
    app.add_middleware(ResponseCacheMiddleware, **mw_kwargs)
    counter = {"n": 0}

    @app.get("/data")
    async def data() -> dict[str, int]:
        counter["n"] += 1
        return {"call": counter["n"]}

    @app.get("/const")
    async def const() -> dict[str, int]:
        return {"v": 1}

    @app.post("/write")
    async def write() -> dict[str, str]:
        counter["n"] += 1
        return {"ok": "yes"}

    @app.get("/private")
    async def private() -> PlainTextResponse:
        return PlainTextResponse(
            "secret", headers={"Cache-Control": "private", "Set-Cookie": "s=1"}
        )

    return app, counter


class TestEtag:
    async def test_sets_etag_and_cache_control(self) -> None:
        app, _ = _make_app(ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/data")
        assert r.status_code == 200
        assert r.headers["etag"].startswith('"')
        assert r.headers["cache-control"] == "public, max-age=30"

    async def test_conditional_get_returns_304(self) -> None:
        app, _ = _make_app(ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.get("/const")
            etag = first.headers["etag"]
            second = await c.get("/const", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == etag

    async def test_star_if_none_match_matches(self) -> None:
        app, _ = _make_app(ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/data", headers={"If-None-Match": "*"})
        assert r.status_code == 304


class TestServerCache:
    async def test_hit_skips_handler(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.get("/data")
            r2 = await c.get("/data")
        assert r1.json() == {"call": 1}
        assert r2.json() == {"call": 1}
        assert counter["n"] == 1
        assert r1.headers["x-cache"] == "MISS"
        assert r2.headers["x-cache"] == "HIT"

    async def test_cached_hit_honors_conditional_get(self) -> None:
        store = MemoryResponseCacheStore()
        app, _ = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.get("/data")
            etag = first.headers["etag"]
            second = await c.get("/data", headers={"If-None-Match": etag})
        assert second.status_code == 304

    async def test_vary_key_separates_entries(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30, vary=("Accept-Encoding",))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/data", headers={"Accept-Encoding": "gzip"})
            await c.get("/data", headers={"Accept-Encoding": "br"})
        assert counter["n"] == 2

    async def test_query_string_separates_entries(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/data?p=1")
            await c.get("/data?p=2")
        assert counter["n"] == 2


class TestSkips:
    async def test_post_not_cached(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.post("/write")
            await c.post("/write")
        assert counter["n"] == 2

    async def test_private_and_cookie_not_cached(self) -> None:
        store = MemoryResponseCacheStore()
        app, _ = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.get("/private")
            r2 = await c.get("/private")
        assert "x-cache" not in r1.headers
        assert "x-cache" not in r2.headers

    async def test_exempt_path_bypasses(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30, exempt_paths=("/data",))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.get("/data")
            r2 = await c.get("/data")
        assert counter["n"] == 2
        assert "etag" not in r1.headers
        assert "x-cache" not in r2.headers


class TestStore:
    def test_memory_store_is_protocol(self) -> None:
        assert isinstance(MemoryResponseCacheStore(), ResponseCacheStore)
