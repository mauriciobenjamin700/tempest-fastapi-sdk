"""Tests for rate-limit stores and per-principal key extractors."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from tempest_fastapi_sdk import (
    JWTUtils,
    MemoryRateLimitStore,
    RateLimitMiddleware,
    RateLimitResult,
    RedisRateLimitStore,
    key_by_header,
    key_by_ip,
    key_by_jwt_claim,
    key_by_jwt_subject,
)


def _request(
    headers: dict[str, str] | None = None,
    *,
    client: tuple[str, int] | None = ("9.9.9.9", 0),
) -> Request:
    """Build a minimal Starlette request for key-extractor tests."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {
        "type": "http",
        "headers": raw_headers,
        "client": client,
    }
    return Request(scope)


# --------------------------------------------------------------------------- #
# Key extractors                                                              #
# --------------------------------------------------------------------------- #


def test_key_by_ip_uses_transport_peer() -> None:
    """The IP key uses the transport peer when no trusted header is set."""
    assert key_by_ip()(_request()) == "ip:9.9.9.9"


def test_key_by_ip_trusted_header() -> None:
    """A trusted header overrides the transport peer."""
    extractor = key_by_ip(trusted_header="x-real-ip")
    assert extractor(_request({"x-real-ip": "1.2.3.4"})) == "ip:1.2.3.4"


def test_key_by_header_uses_value() -> None:
    """The header value becomes the key, namespaced by scope."""
    extractor = key_by_header("x-api-key", scope="apikey")
    assert extractor(_request({"x-api-key": "abc"})) == "apikey:abc"


def test_key_by_header_falls_back_to_ip() -> None:
    """A missing header falls back to the client IP."""
    extractor = key_by_header("x-api-key", scope="apikey")
    assert extractor(_request()) == "ip:9.9.9.9"


def test_key_by_header_anonymous_without_fallback() -> None:
    """With fallback disabled, a missing header yields a shared bucket."""
    extractor = key_by_header("x-api-key", scope="apikey", fallback_to_ip=False)
    assert extractor(_request()) == "apikey:anonymous"


def test_key_by_jwt_subject_uses_sub_claim() -> None:
    """A valid bearer token keys on its ``sub`` claim."""
    jwt = JWTUtils(secret="s3cret")
    token = jwt.encode({"sub": "user-42"})
    extractor = key_by_jwt_subject(jwt)
    assert extractor(_request({"authorization": f"Bearer {token}"})) == "user:user-42"


def test_key_by_jwt_subject_falls_back_to_ip_when_anonymous() -> None:
    """No token falls back to the client IP."""
    jwt = JWTUtils(secret="s3cret")
    extractor = key_by_jwt_subject(jwt)
    assert extractor(_request()) == "ip:9.9.9.9"


def test_key_by_jwt_subject_falls_back_on_invalid_token() -> None:
    """A malformed/invalid token falls back to the client IP."""
    jwt = JWTUtils(secret="s3cret")
    extractor = key_by_jwt_subject(jwt)
    req = _request({"authorization": "Bearer not-a-jwt"})
    assert extractor(req) == "ip:9.9.9.9"


def test_key_by_jwt_claim_uses_named_claim() -> None:
    """An arbitrary claim (e.g. tenant) becomes the key."""
    jwt = JWTUtils(secret="s3cret")
    token = jwt.encode({"sub": "u1", "tenant_id": "acme"})
    extractor = key_by_jwt_claim(jwt, "tenant_id", scope="tenant")
    req = _request({"authorization": f"Bearer {token}"})
    assert extractor(req) == "tenant:acme"


def test_key_by_jwt_claim_missing_claim_falls_back() -> None:
    """A token lacking the claim falls back to the IP."""
    jwt = JWTUtils(secret="s3cret")
    token = jwt.encode({"sub": "u1"})
    extractor = key_by_jwt_claim(jwt, "tenant_id", scope="tenant")
    req = _request({"authorization": f"Bearer {token}"})
    assert extractor(req) == "ip:9.9.9.9"


# --------------------------------------------------------------------------- #
# RedisRateLimitStore (against a Lua-contract fake)                            #
# --------------------------------------------------------------------------- #


class _FakeRedis:
    """Fake async Redis that emulates the sliding-window Lua contract.

    Implements just enough of ``eval`` to behave like the real script:
    prune expired members, count, conditionally add, and report
    ``[allowed, remaining, retry_after_ms]``.
    """

    def __init__(self) -> None:
        self._zsets: dict[str, list[tuple[float, str]]] = {}
        self.calls: int = 0

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> list[int]:
        self.calls += 1
        key = str(keys_and_args[0])
        now = int(keys_and_args[1])
        window = int(keys_and_args[2])
        limit = int(keys_and_args[3])
        member = str(keys_and_args[4])
        entries = [e for e in self._zsets.get(key, []) if e[0] > now - window]
        if len(entries) < limit:
            entries.append((now, member))
            self._zsets[key] = entries
            return [1, limit - len(entries), 0]
        self._zsets[key] = entries
        oldest = entries[0][0]
        retry = max(1, (oldest + window) - now)
        return [0, 0, retry]


class _BrokenRedis:
    """Fake Redis whose ``eval`` always raises."""

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> list[int]:
        raise RuntimeError("redis down")


@pytest.mark.asyncio
async def test_redis_store_allows_then_rejects() -> None:
    """The store allows up to the limit, then rejects with retry_after."""
    store = RedisRateLimitStore(_FakeRedis())  # type: ignore[arg-type]
    first = await store.hit("user:1", max_requests=2, window_seconds=10.0)
    second = await store.hit("user:1", max_requests=2, window_seconds=10.0)
    third = await store.hit("user:1", max_requests=2, window_seconds=10.0)
    assert (first.allowed, second.allowed, third.allowed) == (True, True, False)
    assert first.remaining == 1
    assert third.retry_after >= 1


@pytest.mark.asyncio
async def test_redis_store_keys_are_independent() -> None:
    """Distinct keys do not share a bucket."""
    store = RedisRateLimitStore(_FakeRedis())  # type: ignore[arg-type]
    a = await store.hit("user:a", max_requests=1, window_seconds=10.0)
    b = await store.hit("user:b", max_requests=1, window_seconds=10.0)
    assert a.allowed is True
    assert b.allowed is True


@pytest.mark.asyncio
async def test_redis_store_namespaces_keys() -> None:
    """The configured namespace prefixes the Redis key."""
    fake = _FakeRedis()
    store = RedisRateLimitStore(fake, namespace="rl")  # type: ignore[arg-type]
    await store.hit("user:1", max_requests=5, window_seconds=10.0)
    assert "rl:user:1" in fake._zsets


@pytest.mark.asyncio
async def test_redis_store_fail_open_allows_on_error() -> None:
    """With fail_open, a backend error allows the request."""
    store = RedisRateLimitStore(_BrokenRedis())  # type: ignore[arg-type]
    result = await store.hit("user:1", max_requests=1, window_seconds=10.0)
    assert result.allowed is True


@pytest.mark.asyncio
async def test_redis_store_fail_closed_raises_on_error() -> None:
    """With fail_open disabled, a backend error propagates."""
    store = RedisRateLimitStore(_BrokenRedis(), fail_open=False)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="redis down"):
        await store.hit("user:1", max_requests=1, window_seconds=10.0)


# --------------------------------------------------------------------------- #
# Middleware integration with per-principal keys                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_middleware_per_user_buckets() -> None:
    """Two authenticated users get independent quotas; one user is capped."""
    jwt = JWTUtils(secret="s3cret")
    token_a = jwt.encode({"sub": "alice"})
    token_b = jwt.encode({"sub": "bob"})

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=1,
        window_seconds=10.0,
        key_func=key_by_jwt_subject(jwt),
    )

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        a1 = await client.get("/ping", headers={"authorization": f"Bearer {token_a}"})
        b1 = await client.get("/ping", headers={"authorization": f"Bearer {token_b}"})
        a2 = await client.get("/ping", headers={"authorization": f"Bearer {token_a}"})
    assert a1.status_code == 200
    assert b1.status_code == 200
    assert a2.status_code == 429


@pytest.mark.asyncio
async def test_middleware_accepts_redis_store() -> None:
    """The middleware delegates counting to an injected store."""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=1,
        window_seconds=10.0,
        store=RedisRateLimitStore(_FakeRedis()),  # type: ignore[arg-type]
    )

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/ping")
        second = await client.get("/ping")
    assert first.status_code == 200
    assert second.status_code == 429


def test_rate_limit_result_fields() -> None:
    """The result dataclass carries the decision fields."""
    result = RateLimitResult(allowed=True, remaining=4, retry_after=0)
    assert result.allowed is True
    assert result.remaining == 4
    assert result.retry_after == 0


@pytest.mark.asyncio
async def test_memory_store_direct() -> None:
    """The memory store can be used directly, independent of the middleware."""
    store = MemoryRateLimitStore()
    first = await store.hit("k", max_requests=1, window_seconds=10.0)
    second = await store.hit("k", max_requests=1, window_seconds=10.0)
    assert first.allowed is True
    assert second.allowed is False
    assert second.retry_after >= 1
