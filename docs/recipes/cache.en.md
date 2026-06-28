# Cache

Redis-backed caching primitives. Requires `[cache]` extra.

## AsyncRedisManager


`AsyncRedisManager` wraps `redis.asyncio` with the same connect/disconnect/health-check surface as `AsyncDatabaseManager`. Install with `[cache]`.

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings

cache = AsyncRedisManager(**settings.redis_kwargs())   # url + decode_responses


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await cache.connect()           # without this first call, .client raises RuntimeError
    try:
        yield
    finally:
        await cache.disconnect()


app = FastAPI(lifespan=lifespan)


# Direct use (inside a handler, after the lifespan startup ran)
await cache.client.set("user:123:name", "Ana", ex=300)
name = await cache.client.get("user:123:name")

# FastAPI dependency — yields the live client.
from fastapi import Depends
from redis.asyncio import Redis


@router.get("/cached")
async def cached_endpoint(
    redis: Redis = Depends(cache.client_dependency),
) -> dict[str, str]:
    value = await redis.get("greeting") or "hello"
    return {"value": value}
```

Wire the health check on the canonical router with `make_health_router(checks={"redis": cache.health_check})` so readiness probes fail when Redis is down.


## @cached decorator


`@cached(redis, ttl=..., key_prefix=...)` memoizes the result of an async function in Redis. Cache keys are derived from the function's `__qualname__` plus a SHA-256 of args/kwargs; pass `key_prefix=` to namespace entries. To invalidate **before** the TTL, use tags/namespace (below) rather than a prefix scan.

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

from src.core.settings import settings


redis = AsyncRedisManager(settings.REDIS_URL)


@cached(redis, ttl=300, key_prefix="users:")
async def get_user_profile(user_id: str) -> dict[str, str]:
    """Hits Redis on warm cache; runs the body once every 5 minutes."""
    return await load_from_db(user_id)


# Selectively bypass the cache (read AND write) for some calls
@cached(
    redis,
    ttl=60,
    skip_cache=lambda args, kwargs: kwargs.get("fresh") is True,
)
async def list_orders(user_id: str, *, fresh: bool = False) -> list[dict]:
    ...
```

Defaults: `ttl=300` seconds (`0` disables expiry), `serializer=json.dumps` / `deserializer=json.loads`. Override `serializer` / `deserializer` for non-JSON payloads (Pydantic models — pass `model_dump_json` / `MyModel.model_validate_json`, or use `pickle.dumps` / `pickle.loads` for arbitrary objects). Corrupt cached values fall back to running the wrapped function and warn on the SDK logger.


### Tag / namespace invalidation

Waiting out the TTL is wrong when data **changes**. Label each entry with a `namespace` (one bucket per decorator) and/or `tags` (fine-grained labels, static or derived from the call arguments). On write the entry key is also added to a Redis set per label; `CacheInvalidator` drops every entry under a label at once.

```python
from typing import Any

from tempest_fastapi_sdk.cache import AsyncRedisManager, CacheInvalidator, cached

redis = AsyncRedisManager(settings.REDIS_URL)


@cached(
    redis,
    ttl=300,
    key_prefix="users:",
    namespace="profiles",                                    # coarse bucket
    tags=lambda args, kwargs: [f"user:{kwargs['user_id']}"],  # per-user label
)
async def get_profile(*, user_id: int) -> dict[str, Any]:
    """Reads the DB; cached per user."""
    return await load_profile(user_id)
```

When the user changes, the mutating service drops only that user's entries:

```python
async def update_profile(user_id: int, data: dict[str, Any]) -> None:
    """Update the profile and invalidate only that user's cache.

    Args:
        user_id (int): The changed user.
        data (dict[str, Any]): The new fields.
    """
    await save_profile(user_id, data)
    invalidator = CacheInvalidator(redis, key_prefix="users:")    # same prefix!
    await invalidator.invalidate_tag(f"user:{user_id}")
```

`CacheInvalidator` exposes `invalidate_namespace(ns)`, `invalidate_tag(tag)`, `invalidate_tags(*tags)` (deduped across tags) and `invalidate_keys(*keys)` (raw keys) — each returns the number of entries deleted.

!!! warning "Use the same `key_prefix`"
    `CacheInvalidator` needs the **same `key_prefix`** as the `@cached` decorators it invalidates — that is how the registry set names line up. The registry sets inherit the entry TTL, so they self-prune after their newest member expires; deleting an already-expired key is a harmless no-op.

