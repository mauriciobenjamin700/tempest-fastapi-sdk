"""Tests for @cached tag/namespace invalidation."""

from __future__ import annotations

from typing import Any

import pytest

from tempest_fastapi_sdk.cache import (
    AsyncRedisManager,
    CacheInvalidator,
    cached,
    namespace_registry_key,
    tag_registry_key,
)

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
async def manager() -> AsyncRedisManager:
    """Return an AsyncRedisManager backed by an in-memory fake client."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mgr = AsyncRedisManager("redis://fake:6379/0")
    mgr._client = fake
    return mgr


async def test_namespace_registers_and_invalidates(
    manager: AsyncRedisManager,
) -> None:
    """Invalidating a namespace recomputes every entry under it."""
    calls: int = 0

    @cached(manager, ttl=60, key_prefix="users:", namespace="profiles")
    async def get_profile(user_id: int) -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"user_id": user_id}

    await get_profile(1)
    await get_profile(2)
    await get_profile(1)
    assert calls == 2  # 1 and 2 cached, second get_profile(1) is a hit

    invalidator = CacheInvalidator(manager, key_prefix="users:")
    deleted = await invalidator.invalidate_namespace("profiles")
    assert deleted == 2

    await get_profile(1)
    await get_profile(2)
    assert calls == 4  # both recomputed after invalidation


async def test_callable_tags_invalidate_single_entry(
    manager: AsyncRedisManager,
) -> None:
    """A per-call tag lets one entity's entry be dropped in isolation."""
    calls: list[int] = []

    @cached(
        manager,
        ttl=60,
        key_prefix="users:",
        tags=lambda args, kwargs: [f"user:{args[0]}"],
    )
    async def get_user(user_id: int) -> dict[str, int]:
        calls.append(user_id)
        return {"user_id": user_id}

    await get_user(1)
    await get_user(2)
    assert calls == [1, 2]

    invalidator = CacheInvalidator(manager, key_prefix="users:")
    deleted = await invalidator.invalidate_tag("user:1")
    assert deleted == 1

    await get_user(1)  # recomputed
    await get_user(2)  # still cached
    assert calls == [1, 2, 1]


async def test_static_tags_and_invalidate_tags(
    manager: AsyncRedisManager,
) -> None:
    """Static tags shared across entries clear together via invalidate_tags."""
    calls: int = 0

    @cached(manager, ttl=60, key_prefix="rep:", tags=["reports"])
    async def report(day: int) -> int:
        nonlocal calls
        calls += 1
        return day

    await report(1)
    await report(2)
    assert calls == 2

    invalidator = CacheInvalidator(manager, key_prefix="rep:")
    deleted = await invalidator.invalidate_tags("reports", "missing-tag")
    assert deleted == 2  # both report entries, deduped, missing tag adds nothing

    await report(1)
    await report(2)
    assert calls == 4


async def test_invalidate_keys_raw(manager: AsyncRedisManager) -> None:
    """Raw keys can be deleted directly."""
    client = manager.client
    await client.set("users:k1", "v1")
    await client.set("users:k2", "v2")
    invalidator = CacheInvalidator(manager, key_prefix="users:")
    deleted = await invalidator.invalidate_keys("users:k1", "users:k2", "users:absent")
    assert deleted == 2
    assert await client.get("users:k1") is None


async def test_no_labels_creates_no_registry_sets(
    manager: AsyncRedisManager,
) -> None:
    """Without namespace/tags, no registry sets are written (back-compat)."""

    @cached(manager, ttl=60, key_prefix="plain:")
    async def value(x: int) -> int:
        return x

    await value(1)
    client = manager.client
    keys = await client.keys("plain:__*__:*")
    assert keys == []


async def test_invalidate_empty_namespace_returns_zero(
    manager: AsyncRedisManager,
) -> None:
    """Invalidating an unknown namespace deletes nothing."""
    invalidator = CacheInvalidator(manager, key_prefix="users:")
    assert await invalidator.invalidate_namespace("nope") == 0
    assert await invalidator.invalidate_tag("nope") == 0
    assert await invalidator.invalidate_tags() == 0
    assert await invalidator.invalidate_keys() == 0


def test_registry_key_helpers() -> None:
    """The registry key helpers produce the documented shapes."""
    assert namespace_registry_key("users:", "profiles") == "users:__ns__:profiles"
    assert tag_registry_key("users:", "user:42") == "users:__tag__:user:42"


async def test_registry_set_carries_entry_key(
    manager: AsyncRedisManager,
) -> None:
    """The namespace registry set actually holds the entry key."""

    @cached(manager, ttl=60, key_prefix="users:", namespace="profiles")
    async def get_profile(user_id: int) -> dict[str, int]:
        return {"user_id": user_id}

    await get_profile(7)
    client = manager.client
    members: set[Any] = await client.smembers("users:__ns__:profiles")
    assert len(members) == 1
    assert next(iter(members)).startswith("users:")
