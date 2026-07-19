# Cache

Bater no banco repetidamente para os mesmos dados caros custa latência e carga. Esta receita entrega três blocos apoiados em Redis: um cliente gerenciado (`AsyncRedisManager`) com lifespan e health-check, um decorator de memoização (`@cached`) que guarda o retorno de funções async, e a invalidação por tag/namespace (`CacheInvalidator`) para derrubar entradas antes do TTL. Os blocos aparecem nessa ordem — primeiro o cliente, depois a memoização, depois a invalidação. Requer o extra `[cache]`.

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
from fastapi import APIRouter, Depends
from redis.asyncio import Redis

router = APIRouter()


@router.get("/cached")
async def cached_endpoint(
    redis: Redis = Depends(cache.client_dependency),
) -> dict[str, str]:
    value = await redis.get("greeting") or "hello"
    return {"value": value}


app.include_router(router)
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

!!! warning "`pickle.loads` executa código arbitrário"
    `pickle.loads` desserializa qualquer payload, inclusive um que execute código no momento da carga. Só use `pickle` quando o Redis for de confiança e isolado (sem acesso de terceiros ou de outros serviços). O default seguro para modelos Pydantic é `model_dump_json` / `MyModel.model_validate_json` — mantenha o serializer em JSON sempre que possível.


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

## Receitas reais

### Cache-aside num service

O padrão mais comum: o service tenta o cache; no miss, lê do repositório
e grava. Com `@cached` você nem escreve o try/miss — ele faz isso:

```python
# src/services/catalog.py
from tempest_fastapi_sdk.cache import cached

from src.core.resources import redis      # AsyncRedisManager (singleton)


class CatalogService:
    def __init__(self, repo: ProductRepository) -> None:
        self.repo = repo

    @cached(redis, ttl=300, key_prefix="catalog:", namespace="products")
    async def get_product(self, product_id: str) -> dict[str, Any]:
        """Lê do banco no miss; serve do Redis por 5 min no hit."""
        product = await self.repo.get_by_id(product_id)
        return product.to_dict()

    async def update_product(self, product_id: str, data: dict[str, Any]) -> None:
        """Grava e derruba o cache do namespace inteiro."""
        await self.repo.update(product_id, data)
        await CacheInvalidator(redis, key_prefix="catalog:").invalidate_namespace(
            "products",
        )
```

!!! warning "Não decore métodos que dependem de `self` mutável"
    A chave do `@cached` inclui os argumentos — mas **não** o estado de
    `self`. Só cacheie métodos cujo resultado dependa apenas dos
    argumentos (como acima, onde `product_id` determina tudo). Se o
    resultado varia com o estado da instância, cacheie uma função pura.

### Um Redis pra tudo

O `AsyncRedisManager` entrega um `redis.asyncio.Redis` em `.client` — o
mesmo client alimenta os outros middlewares/stores do SDK, então você
mantém **uma** conexão:

```python
from tempest_fastapi_sdk.api import RateLimitMiddleware, RedisRateLimitStore
from tempest_fastapi_sdk.sessions import RedisSessionStore
from tempest_fastapi_sdk.sse import SSEBroker

# depois de cache.connect() no lifespan:
rate_store = RedisRateLimitStore(cache.client)          # rate limit
session_store = RedisSessionStore(cache.client)         # sessões server-side
broker = SSEBroker(redis=cache.client)                  # fan-out SSE multi-worker
```

Veja [Rate limit](http.md), [Sessões](sessions.md) e [SSE](sse.md) pros
detalhes de cada um.

### Negative caching (cachear o "não existe")

Uma consulta que sempre erra (id inexistente sondado em loop) bate no
banco toda vez. Cacheie o resultado vazio com um TTL curto:

```python
@cached(redis, ttl=30, key_prefix="lookup:")   # TTL curto pro negativo
async def find_user(email: str) -> dict[str, Any] | None:
    """Retorna None no miss — e o None fica em cache por 30s."""
    user = await repo.get_by_email(email)
    return user.to_dict() if user else None
```

!!! tip "Stampede / dogpile"
    Quando uma chave quente expira, N requests simultâneos podem rodar a
    função cara ao mesmo tempo. Mitigue com TTLs escalonados (jitter no
    `ttl`), um TTL maior pra chaves muito quentes, ou um lock curto no
    Redis (`SET NX EX`) em volta do recomputo. O `@cached` não faz isso
    sozinho — é uma decisão por caso.

## Recapitulando

- **`AsyncRedisManager`** — o cliente gerenciado: `connect`/`disconnect` no lifespan, `client` para uso direto, `client_dependency` como `Depends`, e `health_check` no `make_health_router`.
- **`@cached`** — memoiza funções async no Redis com TTL, `key_prefix`, `skip_cache` e serializers customizáveis (prefira JSON; `pickle` só em Redis de confiança).
- **`CacheInvalidator`** — derruba entradas antes do TTL por `namespace`, `tag`, `tags` ou chaves cruas, usando o **mesmo `key_prefix`** do decorator.

Próximo passo: veja a receita de [banco de dados](database.md) para conectar o cache aos repositórios que ele acelera.
