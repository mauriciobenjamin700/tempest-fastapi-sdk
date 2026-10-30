"""Tests for ResponseCacheMiddleware (ETag / conditional-GET / server cache)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    MemoryResponseCacheStore,
    ResponseCacheMiddleware,
    ResponseCacheStore,
    make_logs_router,
    make_token_dependency,
    register_exception_handlers,
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
        assert r.headers["cache-control"] == "private, max-age=30"

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


class TestCredentialedRequests:
    """A response tied to one caller must never be served to another.

    The key is method + path + varied headers, which says nothing about who
    asked. So a credentialed request bypasses the shared store unless the
    deployment opts in, in which case the credentials join the key.
    """

    async def test_authorization_header_bypasses_the_store(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.get("/data", headers={"Authorization": "Bearer alice"})
            r2 = await c.get("/data", headers={"Authorization": "Bearer bob"})
        assert counter["n"] == 2
        assert r1.json() == {"call": 1}
        assert r2.json() == {"call": 2}
        assert "x-cache" not in r2.headers

    async def test_cookie_bypasses_the_store(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/data", headers={"Cookie": "session=alice"})
            await c.get("/data", headers={"Cookie": "session=bob"})
        assert counter["n"] == 2

    async def test_anonymous_request_never_reads_a_credentialed_entry(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/data", headers={"Authorization": "Bearer alice"})
            anon = await c.get("/data")
        assert counter["n"] == 2
        assert anon.json() == {"call": 2}

    async def test_opt_in_scopes_the_entry_per_credential(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30, cache_credentialed=True)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            alice1 = await c.get("/data", headers={"Authorization": "Bearer alice"})
            alice2 = await c.get("/data", headers={"Authorization": "Bearer alice"})
            bob = await c.get("/data", headers={"Authorization": "Bearer bob"})
        assert alice1.json() == {"call": 1}
        assert alice2.json() == {"call": 1}
        assert alice2.headers["x-cache"] == "HIT"
        assert bob.json() == {"call": 2}
        assert counter["n"] == 2

    async def test_anonymous_traffic_still_shares_the_store(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/data")
            second = await c.get("/data")
        assert counter["n"] == 1
        assert second.headers["x-cache"] == "HIT"

    async def test_credentialed_request_still_gets_etag_and_304(self) -> None:
        store = MemoryResponseCacheStore()
        app, _ = _make_app(store=store, ttl_seconds=30)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.get("/const", headers={"Authorization": "Bearer alice"})
            second = await c.get(
                "/const",
                headers={
                    "Authorization": "Bearer alice",
                    "If-None-Match": first.headers["etag"],
                },
            )
        assert first.headers["cache-control"] == "private, max-age=30"
        assert second.status_code == 304

    async def test_explicit_cache_control_still_wins(self) -> None:
        app, _ = _make_app(ttl_seconds=30, cache_control="public, max-age=120")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            response = await c.get("/const")
        assert response.headers["cache-control"] == "public, max-age=120"


class TestTokenGatedRoutes:
    """A route gated by a non-``Authorization`` header must never be shared.

    ``make_token_dependency`` reads ``X-Token``. A cache that only treats
    ``Authorization`` / ``Cookie`` as credentials stores the gated response
    under the anonymous key and serves it before the gate runs.
    """

    async def test_logs_router_is_not_served_to_an_anonymous_caller(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "info.log").write_text(
            json.dumps(
                {
                    "timestamp": "2026-01-01T00:00:00.000Z",
                    "level": "INFO",
                    "logger": "test",
                    "message": "operator-only record",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(
            ResponseCacheMiddleware, store=MemoryResponseCacheStore(), ttl_seconds=60
        )
        app.include_router(make_logs_router(log_dir=tmp_path, token_secret="s3cret"))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            authed = await c.get("/logs", headers={"X-Token": "s3cret"})
            anonymous = await c.get("/logs")
            wrong = await c.get("/logs", headers={"X-Token": "wrong"})
        assert authed.status_code == 200
        assert "operator-only record" in authed.text
        assert anonymous.status_code == 401
        assert wrong.status_code == 401
        assert "operator-only record" not in anonymous.text + wrong.text

    async def test_custom_token_header_is_kept_out_of_the_store(self) -> None:
        """A header outside the default list is covered by ``no-store``."""
        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(
            ResponseCacheMiddleware, store=MemoryResponseCacheStore(), ttl_seconds=60
        )
        gate = make_token_dependency("s3cret", header_name="X-Ops-Key")

        @app.get("/ops", dependencies=[Depends(gate)])
        async def ops() -> dict[str, str]:
            return {"secret": "ops-data"}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            authed = await c.get("/ops", headers={"X-Ops-Key": "s3cret"})
            anonymous = await c.get("/ops")
        assert authed.status_code == 200
        assert authed.headers["cache-control"] == "no-store"
        assert anonymous.status_code == 401

    async def test_configured_credential_header_bypasses_the_store(self) -> None:
        store = MemoryResponseCacheStore()
        app, counter = _make_app(
            store=store,
            ttl_seconds=30,
            credential_headers=("authorization", "x-tenant-key"),
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.get("/data", headers={"X-Tenant-Key": "k"})
            second = await c.get("/data")
        assert counter["n"] == 2
        assert "x-cache" not in first.headers
        assert second.json() == {"call": 2}

    def test_empty_credential_header_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="credential_headers"):
            ResponseCacheMiddleware(FastAPI(), credential_headers=())
