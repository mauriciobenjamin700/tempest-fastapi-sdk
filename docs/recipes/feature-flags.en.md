# Feature flags

Turn features on and off **without a redeploy**: gradual rollouts, kill-switches, beta behind a flag. The SDK ships a `FeatureFlags` service over pluggable backends (static env, runtime Redis, or both layered) plus a FastAPI dependency that gates routes.

## Quick start

```python
from tempest_fastapi_sdk import FeatureFlags, MemoryFeatureFlagBackend

flags = FeatureFlags(MemoryFeatureFlagBackend({"new_checkout": True}))

if await flags.is_enabled("new_checkout"):
    ...                                                       # new path
```

`is_enabled(name)` returns the flag value, or the service `default` (`False`) when the flag is unset. Pass `default=True` per call to flip that locally. Toggle with `enable` / `disable` / `set`, and list everything with `all()`.

## Backends

| Backend | When to use |
| --- | --- |
| `MemoryFeatureFlagBackend(initial=...)` | Tests and dev (in-process). |
| `EnvFeatureFlagBackend(prefix="FEATURE_")` | **Static** config — `new_checkout` reads `FEATURE_NEW_CHECKOUT`. Read-only (`set` raises). |
| `RedisFeatureFlagBackend(redis_client, key="feature_flags")` | **Runtime** toggling, shared across replicas (one Redis hash). |
| `CompositeFeatureFlagBackend([redis, env])` | Layered: the Redis override beats the env default. |

!!! info "Values read as truthy"
    `1`, `true`, `yes`, `on`, `t`, `y` (case-insensitive) become `True`; anything else is `False`. Applies to both env and Redis.

### Production: Redis over env

The recommended pattern uses env as the static default (shipped with the deploy) and Redis as the runtime override (the team toggles without cutting a release):

```python
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    CompositeFeatureFlagBackend,
    EnvFeatureFlagBackend,
    FeatureFlags,
    RedisFeatureFlagBackend,
)


def build_flags(redis: Redis) -> FeatureFlags:
    """Build the flags service with a Redis override over an env default.

    Args:
        redis (Redis): A connected async Redis client.

    Returns:
        FeatureFlags: The service ready to inject.
    """
    backend = CompositeFeatureFlagBackend(
        [
            RedisFeatureFlagBackend(redis, key="feature_flags"),  # runtime
            EnvFeatureFlagBackend(prefix="FEATURE_"),             # default
        ]
    )
    return FeatureFlags(backend)
```

## Gating a route

`make_flag_dependency(flags, name)` returns an async dependency that lets the route through only when the flag is on. Otherwise it raises an `AppException` in the SDK envelope — `404` by default, so the feature simply "doesn't exist":

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import make_flag_dependency

from src.api.dependencies.resources import get_flags

router = APIRouter()
flags = get_flags()


@router.get(
    "/checkout/v2",
    dependencies=[Depends(make_flag_dependency(flags, "new_checkout"))],
)
async def checkout_v2() -> dict[str, bool]:
    """Only answers while ``new_checkout`` is on."""
    return {"ok": True}
```

For a **kill-switch** on something legacy, invert the gate with `enabled=False` (the route answers only while the flag is off):

```python
@router.get(
    "/legacy",
    dependencies=[
        Depends(make_flag_dependency(flags, "legacy_disabled", enabled=False)),
    ],
)
async def legacy() -> dict[str, bool]:
    """Stops answering the moment ``legacy_disabled`` is turned on."""
    return {"ok": True}
```

`status_code`, `detail` and `code` are configurable — use `status_code=403` to signal "exists but forbidden" instead of hiding it with `404`.

## Recap

- `FeatureFlags(backend, default=False)` — `is_enabled` / `enable` / `disable` / `set` / `all`.
- Backends: `Memory` (dev), `Env` (static read-only), `Redis` (runtime), `Composite` (layered).
- `make_flag_dependency(flags, name, enabled=True, status_code=404)` gates routes.
- Redis override over an env default is the production pattern — toggle without a redeploy.
