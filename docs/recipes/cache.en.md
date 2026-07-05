# Cache

Hitting the database over and over for the same expensive data costs latency and load. This recipe delivers three Redis-backed blocks: a managed client (`AsyncRedisManager`) with lifespan and health-check, a memoization decorator (`@cached`) that stores async function results, and tag/namespace invalidation (`CacheInvalidator`) to drop entries before their TTL. The blocks appear in that order — the client first, then memoization, then invalidation. Requires the `[cache]` extra.

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

!!! warning "`pickle.loads` runs arbitrary code"
    `pickle.loads` deserializes any payload, including one crafted to execute code at load time. Only use `pickle` when the Redis is trusted and isolated (no third-party or cross-service access). The safe default for Pydantic models is `model_dump_json` / `MyModel.model_validate_json` — keep the serializer on JSON whenever you can.


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

## Real-world recipes

### Cache-aside in a service

The most common pattern: the service tries the cache; on a miss it reads
from the repository and writes. With `@cached` you don't even write the
try/miss — it does it for you:

```python
# src/services/catalog.py
from tempest_fastapi_sdk.cache import cached

from src.core.resources import redis      # AsyncRedisManager (singleton)


class CatalogService:
    def __init__(self, repo: ProductRepository) -> None:
        self.repo = repo

    @cached(redis, ttl=300, key_prefix="catalog:", namespace="products")
    async def get_product(self, product_id: str) -> dict[str, Any]:
        """Reads from the DB on a miss; serves from Redis for 5 min on a hit."""
        product = await self.repo.get_by_id(product_id)
        return product.to_dict()

    async def update_product(self, product_id: str, data: dict[str, Any]) -> None:
        """Writes and drops the whole namespace's cache."""
        await self.repo.update(product_id, data)
        await CacheInvalidator(redis, key_prefix="catalog:").invalidate_namespace(
            "products",
        )
```

!!! warning "Don't cache methods that depend on mutable `self`"
    The `@cached` key includes the arguments — but **not** `self`'s state.
    Only cache methods whose result depends solely on the arguments (like
    above, where `product_id` determines everything). If the result varies
    with instance state, cache a pure function instead.

### One Redis for everything

`AsyncRedisManager` hands out a `redis.asyncio.Redis` at `.client` — the
same client feeds the SDK's other middlewares/stores, so you keep **one**
connection:

```python
from tempest_fastapi_sdk.api import RateLimitMiddleware, RedisRateLimitStore
from tempest_fastapi_sdk.sessions import RedisSessionStore
from tempest_fastapi_sdk.sse import SSEBroker

# after cache.connect() in the lifespan:
rate_store = RedisRateLimitStore(cache.client)          # rate limiting
session_store = RedisSessionStore(cache.client)         # server-side sessions
broker = SSEBroker(redis=cache.client)                  # multi-worker SSE fan-out
```

See [Rate limit](http.md), [Sessions](sessions.md) and [SSE](sse.md) for
each one's details.

### Negative caching (cache the "not found")

A lookup that always misses (a non-existent id probed in a loop) hits the
DB every time. Cache the empty result with a short TTL:

```python
@cached(redis, ttl=30, key_prefix="lookup:")   # short TTL for the negative
async def find_user(email: str) -> dict[str, Any] | None:
    """Returns None on a miss — and that None is cached for 30s."""
    user = await repo.get_by_email(email)
    return user.to_dict() if user else None
```

!!! tip "Stampede / dogpile"
    When a hot key expires, N concurrent requests can run the expensive
    function at once. Mitigate with staggered TTLs (jitter the `ttl`), a
    longer TTL for very hot keys, or a short Redis lock (`SET NX EX`)
    around the recompute. `@cached` doesn't do this on its own — it's a
    case-by-case call.

## Recap

- **`AsyncRedisManager`** — the managed client: `connect`/`disconnect` in the lifespan, `client` for direct use, `client_dependency` as a `Depends`, and `health_check` on `make_health_router`.
- **`@cached`** — memoizes async functions in Redis with TTL, `key_prefix`, `skip_cache`, and customizable serializers (prefer JSON; use `pickle` only on a trusted Redis).
- **`CacheInvalidator`** — drops entries before their TTL by `namespace`, `tag`, `tags`, or raw keys, using the **same `key_prefix`** as the decorator.

Next step: see the [database](database.md) recipe to connect the cache to the repositories it speeds up.

