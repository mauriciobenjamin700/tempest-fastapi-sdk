# Cache

Primitivos de cache apoiados em Redis. Requer o extra `[cache]`.

## AsyncRedisManager


`AsyncRedisManager` embrulha `redis.asyncio` com a mesma superfície de connect/disconnect/health-check do `AsyncDatabaseManager`. Instale com `[cache]`.

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


`@cached(redis, ttl=..., key_prefix=...)` memoiza o resultado de uma função async no Redis. As chaves de cache são derivadas do `__qualname__` da função mais um SHA-256 de args/kwargs; passe `key_prefix=` para dar namespace às entradas. Para invalidar **antes** do TTL, use tags/namespace (abaixo) em vez de scan de prefixo.

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


### Invalidação por tag / namespace

Esperar o TTL não serve quando um dado **muda**. Marque cada entrada com um `namespace` (um balde por decorator) e/ou `tags` (rótulos finos, estáticos ou derivados dos argumentos da chamada). No write, a chave da entrada também entra num SET Redis por rótulo; o `CacheInvalidator` apaga todas as entradas de um rótulo de uma vez.

```python
from typing import Any

from tempest_fastapi_sdk.cache import AsyncRedisManager, CacheInvalidator, cached

redis = AsyncRedisManager(settings.REDIS_URL)


@cached(
    redis,
    ttl=300,
    key_prefix="users:",
    namespace="profiles",                                    # balde coarse
    tags=lambda args, kwargs: [f"user:{kwargs['user_id']}"],  # rótulo por usuário
)
async def get_profile(*, user_id: int) -> dict[str, Any]:
    """Lê do banco; fica em cache por usuário."""
    return await load_profile(user_id)
```

Quando o usuário muda, o service que faz a mutação derruba só as entradas dele:

```python
async def update_profile(user_id: int, data: dict[str, Any]) -> None:
    """Atualiza o perfil e invalida o cache só desse usuário.

    Args:
        user_id (int): O usuário alterado.
        data (dict[str, Any]): Os campos novos.
    """
    await save_profile(user_id, data)
    invalidator = CacheInvalidator(redis, key_prefix="users:")    # mesmo prefixo!
    await invalidator.invalidate_tag(f"user:{user_id}")
```

O `CacheInvalidator` expõe `invalidate_namespace(ns)`, `invalidate_tag(tag)`, `invalidate_tags(*tags)` (dedupe entre tags) e `invalidate_keys(*keys)` (chaves cruas) — cada um devolve o número de entradas apagadas.

!!! warning "Use o mesmo `key_prefix`"
    O `CacheInvalidator` precisa do **mesmo `key_prefix`** dos decorators `@cached` que ele invalida — é assim que os nomes dos SETs de registro batem. Os SETs de registro herdam o TTL da entrada, então se auto-limpam depois do membro mais novo expirar; apagar uma chave já expirada é no-op inofensivo.
