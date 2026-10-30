"""The default ``@cached`` key encodes arguments from their data, not ``str()``.

``str()`` of a Pydantic model masks every ``SecretStr`` and drops every
``Field(repr=False)`` field, so two callers whose arguments differed only
there used to share one cache entry, and the second got the first one's
result without the function running.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

import pytest
from pydantic import BaseModel, Field, SecretStr

from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

fakeredis = pytest.importorskip("fakeredis")


class _Credentials(BaseModel):
    """A per-tenant credential whose distinguishing field is a secret."""

    provider: str
    api_key: SecretStr


class _Scope(BaseModel):
    """A scope whose distinguishing field is hidden from ``repr``."""

    region: str
    tenant_id: str = Field(repr=False)


@dataclass
class _Point:
    """A plain dataclass argument."""

    x: int
    y: int


class _Opaque:
    """An object with no faithful encoding."""

    def __str__(self) -> str:
        """Return the same text for every instance.

        Returns:
            str: A constant.
        """
        return "opaque"


@pytest.fixture
async def manager() -> AsyncRedisManager:
    """Build a manager over an in-memory fake Redis.

    Returns:
        AsyncRedisManager: The manager under test.
    """
    mgr = AsyncRedisManager("redis://fake:6379/0")
    mgr._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return mgr


class TestFaithfulKeys:
    async def test_secret_str_fields_get_distinct_keys(
        self, manager: AsyncRedisManager
    ) -> None:
        """Before the fix tenant B received tenant A's cached result."""
        calls: list[str] = []

        @cached(manager, ttl=60, key_prefix="acct:")
        async def fetch_account(creds: _Credentials) -> dict[str, str]:
            calls.append(creds.api_key.get_secret_value())
            return {"owner": creds.api_key.get_secret_value()}

        a = _Credentials(provider="psp", api_key=SecretStr("tenant-A-key"))
        b = _Credentials(provider="psp", api_key=SecretStr("tenant-B-key"))

        assert await fetch_account(a) == {"owner": "tenant-A-key"}
        assert await fetch_account(b) == {"owner": "tenant-B-key"}
        assert await fetch_account(creds=b) == {"owner": "tenant-B-key"}
        assert await fetch_account(a) == {"owner": "tenant-A-key"}
        assert calls == ["tenant-A-key", "tenant-B-key", "tenant-B-key"]

    async def test_repr_false_fields_get_distinct_keys(
        self, manager: AsyncRedisManager
    ) -> None:
        @cached(manager, ttl=60)
        async def list_docs(scope: _Scope) -> list[str]:
            return [f"doc-of-{scope.tenant_id}"]

        assert await list_docs(_Scope(region="br", tenant_id="t1")) == ["doc-of-t1"]
        assert await list_docs(_Scope(region="br", tenant_id="t2")) == ["doc-of-t2"]

    async def test_models_nested_in_lists_are_encoded(
        self, manager: AsyncRedisManager
    ) -> None:
        @cached(manager, ttl=60)
        async def first_key(items: list[_Credentials]) -> str:
            return items[0].api_key.get_secret_value()

        one = [_Credentials(provider="p", api_key=SecretStr("k1"))]
        two = [_Credentials(provider="p", api_key=SecretStr("k2"))]
        assert await first_key(one) == "k1"
        assert await first_key(two) == "k2"

    async def test_equal_arguments_still_hit(self, manager: AsyncRedisManager) -> None:
        calls: int = 0
        tenant = uuid4()

        @cached(manager, ttl=60)
        async def lookup(creds: _Credentials, point: _Point, tenant_id: object) -> int:
            nonlocal calls
            calls += 1
            return point.x + point.y

        for _ in range(2):
            assert (
                await lookup(
                    _Credentials(provider="p", api_key=SecretStr("k")),
                    _Point(1, 2),
                    tenant,
                )
                == 3
            )
        assert calls == 1

    async def test_unencodable_argument_bypasses_the_cache(
        self, manager: AsyncRedisManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An object with only a display form runs uncached instead of colliding."""
        calls: int = 0

        @cached(manager, ttl=60)
        async def handle(item: _Opaque) -> int:
            nonlocal calls
            calls += 1
            return calls

        with caplog.at_level(logging.WARNING):
            assert await handle(_Opaque()) == 1
            assert await handle(_Opaque()) == 2
        assert "cached_key_unencodable" in caplog.text
        assert await manager.client.keys("*") == []
