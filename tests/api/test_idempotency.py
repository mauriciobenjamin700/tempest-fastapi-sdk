"""Tests for the idempotency middleware + stores."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
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


def _counting_app(**mw_kwargs: Any) -> tuple[FastAPI, dict[str, int]]:
    """App whose ``POST /charge`` counts executions and echoes the count."""
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, **mw_kwargs)
    counter = {"n": 0}

    @app.post("/charge")
    async def charge() -> dict[str, int]:
        counter["n"] += 1
        return {"call": counter["n"]}

    return app, counter


class TestCallerScoping:
    """A stored response must only ever be replayed to the caller that made it.

    The ``Idempotency-Key`` value is client-chosen, so on its own it does not
    identify anyone — two callers picking the same string would otherwise
    share one entry and the replay would hand over the other's response body
    and headers.
    """

    async def test_same_key_different_credentials_does_not_replay(self) -> None:
        app, counter = _counting_app(store=MemoryIdempotencyStore(), ttl_seconds=60)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            alice = await c.post(
                "/charge",
                headers={IDEMPOTENCY_HEADER: "k1", "Authorization": "Bearer alice"},
            )
            bob = await c.post(
                "/charge",
                headers={IDEMPOTENCY_HEADER: "k1", "Authorization": "Bearer bob"},
            )
        assert alice.json() == {"call": 1}
        assert bob.json() == {"call": 2}
        assert counter["n"] == 2

    async def test_same_key_same_credentials_replays(self) -> None:
        app, counter = _counting_app(store=MemoryIdempotencyStore(), ttl_seconds=60)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.post(
                "/charge",
                headers={IDEMPOTENCY_HEADER: "k1", "Authorization": "Bearer alice"},
            )
            retry = await c.post(
                "/charge",
                headers={IDEMPOTENCY_HEADER: "k1", "Authorization": "Bearer alice"},
            )
        assert first.json() == retry.json() == {"call": 1}
        assert counter["n"] == 1

    async def test_anonymous_caller_does_not_read_a_credentialed_entry(self) -> None:
        app, counter = _counting_app(store=MemoryIdempotencyStore(), ttl_seconds=60)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.post(
                "/charge",
                headers={IDEMPOTENCY_HEADER: "k1", "Authorization": "Bearer alice"},
            )
            anon = await c.post("/charge", headers={IDEMPOTENCY_HEADER: "k1"})
        assert anon.json() == {"call": 2}
        assert counter["n"] == 2

    async def test_custom_principal_resolver_defines_the_scope(self) -> None:
        app, counter = _counting_app(
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
            principal_resolver=lambda request: request.headers.get("x-tenant", ""),
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.post(
                "/charge", headers={IDEMPOTENCY_HEADER: "k1", "X-Tenant": "acme"}
            )
            same_tenant = await c.post(
                "/charge", headers={IDEMPOTENCY_HEADER: "k1", "X-Tenant": "acme"}
            )
            other_tenant = await c.post(
                "/charge", headers={IDEMPOTENCY_HEADER: "k1", "X-Tenant": "globex"}
            )
        assert first.json() == same_tenant.json() == {"call": 1}
        assert other_tenant.json() == {"call": 2}
        assert counter["n"] == 2


class TestUnreplayableContent:
    async def test_set_cookie_is_not_replayed(self) -> None:
        """The original caller keeps its cookie; a replay never re-issues it.

        ``principal_resolver`` is pinned to a constant here so the second
        request lands on the same entry — the point under test is the stored
        copy, not the key scoping covered above.
        """
        app = FastAPI()
        app.add_middleware(
            IdempotencyMiddleware,
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
            principal_resolver=lambda request: "fixed",
        )

        @app.post("/login")
        async def login() -> Response:
            return JSONResponse(
                {"ok": True}, headers={"Set-Cookie": "session=alice; Path=/"}
            )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.post("/login", headers={IDEMPOTENCY_HEADER: "k1"})
            replay = await c.post("/login", headers={IDEMPOTENCY_HEADER: "k1"})
        assert "set-cookie" in first.headers
        assert "set-cookie" not in replay.headers

    async def test_server_error_is_not_cached_by_default(self) -> None:
        app = FastAPI()
        app.add_middleware(
            IdempotencyMiddleware,
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
        )
        state = {"fail": True}

        @app.post("/flaky")
        async def flaky() -> Response:
            if state["fail"]:
                state["fail"] = False
                return JSONResponse({"detail": "boom"}, status_code=503)
            return JSONResponse({"ok": True})

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.post("/flaky", headers={IDEMPOTENCY_HEADER: "k1"})
            retry = await c.post("/flaky", headers={IDEMPOTENCY_HEADER: "k1"})
        assert first.status_code == 503
        assert retry.status_code == 200

    async def test_server_error_is_cached_when_opted_in(self) -> None:
        app = FastAPI()
        app.add_middleware(
            IdempotencyMiddleware,
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
            cache_server_errors=True,
        )
        state = {"fail": True}

        @app.post("/flaky")
        async def flaky() -> Response:
            if state["fail"]:
                state["fail"] = False
                return JSONResponse({"detail": "boom"}, status_code=503)
            return JSONResponse({"ok": True})

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            first = await c.post("/flaky", headers={IDEMPOTENCY_HEADER: "k1"})
            retry = await c.post("/flaky", headers={IDEMPOTENCY_HEADER: "k1"})
        assert first.status_code == retry.status_code == 503

    async def test_client_error_is_still_cached(self) -> None:
        app = FastAPI()
        app.add_middleware(
            IdempotencyMiddleware,
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
        )
        counter = {"n": 0}

        @app.post("/reject")
        async def reject() -> Response:
            counter["n"] += 1
            return JSONResponse({"detail": "nope"}, status_code=422)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.post("/reject", headers={IDEMPOTENCY_HEADER: "k1"})
            await c.post("/reject", headers={IDEMPOTENCY_HEADER: "k1"})
        assert counter["n"] == 1


class TestConcurrency:
    async def test_simultaneous_requests_run_the_handler_once(self) -> None:
        app = FastAPI()
        app.add_middleware(
            IdempotencyMiddleware,
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
        )
        counter = {"n": 0}

        @app.post("/slow")
        async def slow() -> dict[str, int]:
            counter["n"] += 1
            await asyncio.sleep(0.05)
            return {"call": counter["n"]}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            responses = await asyncio.gather(
                c.post("/slow", headers={IDEMPOTENCY_HEADER: "k1"}),
                c.post("/slow", headers={IDEMPOTENCY_HEADER: "k1"}),
            )
        assert counter["n"] == 1
        assert [r.json() for r in responses] == [{"call": 1}, {"call": 1}]

    async def test_lock_is_released_after_use(self) -> None:
        """Client-chosen keys must not grow the lock table without bound."""
        middleware = IdempotencyMiddleware(
            FastAPI(),
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
        )
        lock = await middleware._lock_for("k1")
        assert middleware._locks == {"k1": lock}
        await middleware._release_lock("k1")
        assert middleware._locks == {}

    async def test_held_lock_is_not_dropped(self) -> None:
        middleware = IdempotencyMiddleware(
            FastAPI(),
            store=MemoryIdempotencyStore(),
            ttl_seconds=60,
        )
        lock = await middleware._lock_for("k1")
        async with lock:
            await middleware._release_lock("k1")
            assert middleware._locks == {"k1": lock}
