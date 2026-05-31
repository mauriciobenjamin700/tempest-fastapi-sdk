# Cache

Redis-backed caching primitives. Requires `[cache]` extra.

## AsyncRedisManager


`AsyncRedisManager` wraps `redis.asyncio` with the same connect/disconnect/health-check surface as `AsyncDatabaseManager`. Install with `[cache]`.

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager

cache = AsyncRedisManager(settings.REDIS_URL, decode_responses=True)

# Lifespan
await cache.connect()
...
await cache.disconnect()

# Direct use
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


`@cached(redis, ttl=..., key_prefix=...)` memoizes the result of an async function in Redis. Cache keys are derived from the function's `__qualname__` plus a SHA-256 of args/kwargs; pass `key_prefix=` to namespace entries so invalidation works by prefix scan.

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

