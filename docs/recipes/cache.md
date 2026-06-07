# Cache

Primitivos de cache apoiados em Redis. Requer o extra `[cache]`.

## AsyncRedisManager


`AsyncRedisManager` embrulha `redis.asyncio` com a mesma superfície de connect/disconnect/health-check do `AsyncDatabaseManager`. Instale com `[cache]`.

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import AsyncRedisManager
from src.core.settings import settings

cache = AsyncRedisManager(**settings.redis_kwargs())   # url + decode_responses


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await cache.connect()           # primeira chamada — sem isso, .client levanta RuntimeError
    try:
        yield
    finally:
        await cache.disconnect()


app = FastAPI(lifespan=lifespan)

# Uso direto (dentro de um handler, depois do startup do lifespan)
await cache.client.set("user:123:name", "Ana", ex=300)
name = await cache.client.get("user:123:name")

# Dependência FastAPI — entrega o client ativo.
from fastapi import Depends
from redis.asyncio import Redis


@router.get("/cached")
async def cached_endpoint(
    redis: Redis = Depends(cache.client_dependency),
) -> dict[str, str]:
    value = await redis.get("greeting") or "hello"
    return {"value": value}
```

Conecte o health check no router canônico com `make_health_router(checks={"redis": cache.health_check})` para que as readiness probes falhem quando o Redis cair.


## Decorator @cached


`@cached(redis, ttl=..., key_prefix=...)` memoiza o resultado de uma função async no Redis. As chaves de cache são derivadas do `__qualname__` da função mais um SHA-256 de args/kwargs; passe `key_prefix=` para dar namespace às entradas, de modo que a invalidação funcione por scan de prefixo.

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

from src.core.settings import settings


redis = AsyncRedisManager(settings.REDIS_URL)


@cached(redis, ttl=300, key_prefix="users:")
async def get_user_profile(user_id: str) -> dict[str, str]:
    """Hits Redis on warm cache; runs the body once every 5 minutes."""
    return await load_from_db(user_id)


# Pula o cache (leitura E escrita) seletivamente em algumas chamadas
@cached(
    redis,
    ttl=60,
    skip_cache=lambda args, kwargs: kwargs.get("fresh") is True,
)
async def list_orders(user_id: str, *, fresh: bool = False) -> list[dict]:
    ...
```

Defaults: `ttl=300` segundos (`0` desabilita a expiração), `serializer=json.dumps` / `deserializer=json.loads`. Sobrescreva `serializer` / `deserializer` para payloads não-JSON (modelos Pydantic — passe `model_dump_json` / `MyModel.model_validate_json`, ou use `pickle.dumps` / `pickle.loads` para objetos arbitrários). Valores corrompidos no cache caem de volta para rodar a função embrulhada e emitem um warning no logger do SDK.
