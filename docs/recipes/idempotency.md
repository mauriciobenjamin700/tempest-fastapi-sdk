# Idempotência

`IdempotencyMiddleware` implementa o padrão `Idempotency-Key` usado por Stripe, AWS, GitHub e Plaid: o cliente envia um header único e, **assim que a primeira requisição completa**, o servidor devolve a mesma resposta a qualquer retry — sem duplicar linha no banco / cobrar duas vezes.

!!! warning "Sem lock de requisição em andamento"
    A deduplicação só entra em ação **depois** que a primeira requisição termina e a resposta é cacheada. Retries concorrentes que chegam **enquanto a original ainda está em andamento** NÃO são deduplicados — o middleware não tem lock de in-progress (diferente do 409 "while in progress" do Stripe), então ambos rodam o handler. Mantenha os timeouts do cliente generosos em relação à latência do handler para evitar retries prematuros.

## Como funciona

1. Cliente envia `POST /charge` com `Idempotency-Key: chk_<uuid>`.
2. Middleware processa, salva a resposta completa indexada por `(method, path, key)`.
3. Cliente retentou? Middleware devolve a **mesma resposta cacheada**. Handler não roda de novo.

Só verbos mutantes (`POST` / `PUT` / `PATCH` / `DELETE`) são elegíveis — `GET` é naturalmente idempotente.

!!! warning "Opt-in por requisição"
    Sem o header, o middleware deixa passar normal. Endpoints existentes não quebram — só quem precisar da garantia envia o header.

## Setup mínimo (single-replica / dev)

```python
from fastapi import FastAPI
from tempest_fastapi_sdk import (
    IdempotencyMiddleware,
    MemoryIdempotencyStore,
)


app = FastAPI()
app.add_middleware(
    IdempotencyMiddleware,
    store=MemoryIdempotencyStore(),
    ttl_seconds=24 * 3600,
)
```

`MemoryIdempotencyStore` guarda em dict local — funciona só pra uma réplica. Pra produção use Redis.

!!! note "Estado process-local, volátil"
    O `MemoryIdempotencyStore` vive na memória do processo: cada réplica tem o seu próprio dict e ele é zerado em todo restart / redeploy. Chaves gravadas antes de reiniciar deixam de deduplicar depois. Use apenas em dev / single-replica; para persistência entre reinícios e entre réplicas, use o `RedisIdempotencyStore`.

## Setup produção (multi-réplica via Redis)

!!! info "Instalação"
    A idempotência in-memory já vem com `tempest-fastapi-sdk` — o setup
    mínimo não precisa de extra. O `RedisIdempotencyStore` depende do extra
    `[cache]` — `uv add "tempest-fastapi-sdk[cache]"` (traz `redis`).

```python
from fastapi import FastAPI
from redis.asyncio import Redis
from tempest_fastapi_sdk import (
    IdempotencyMiddleware,
    RedisIdempotencyStore,
)

from src.core.settings import settings


redis = Redis.from_url(settings.REDIS_URL)
app = FastAPI()
app.add_middleware(
    IdempotencyMiddleware,
    store=RedisIdempotencyStore(redis, prefix="idem:"),
    ttl_seconds=24 * 3600,
)
```

Stripe usa 24h por padrão — coerente com retry exponencial do lado do cliente.

## Cliente

```python
import uuid
import httpx


async def create_charge(amount_cents: int) -> dict[str, object]:
    """POST idempotente com retry automático."""
    key = uuid.uuid4().hex
    async with httpx.AsyncClient() as c:
        for _ in range(3):
            try:
                r = await c.post(
                    "https://api/charge",
                    json={"amount_cents": amount_cents},
                    headers={"Idempotency-Key": key},
                    timeout=10,
                )
                return r.json()
            except httpx.ReadTimeout:
                continue
        raise RuntimeError("3 retries failed")
```

Em qualquer das 3 tentativas que chegar ao servidor, o resultado final é o mesmo recurso criado uma única vez — réplicas restantes recebem a resposta cacheada.

## Quando usar

- Pagamento / cobrança
- Envio de webhook (cliente retenta com mesmo key)
- Operações de side-effect externo (envio de email, SMS)
- Qualquer `POST /create` cujo retry pode duplicar registro

## Quando NÃO usar

- `GET` (já idempotente)
- Operações trivialmente reentrantes (`PATCH` que reescreve mesmo valor)
- Quando a duplicação não tem consequência (logs, métricas)

## Backend customizado

Implemente o protocolo `IdempotencyStore`:

```python
from tempest_fastapi_sdk import CachedResponse, IdempotencyStore


class DynamoIdempotencyStore:
    """Exemplo de backend DynamoDB."""

    async def get(self, key: str) -> CachedResponse | None:
        ...

    async def set(
        self,
        key: str,
        response: CachedResponse,
        *,
        ttl_seconds: int,
    ) -> None:
        ...


# Funciona com o middleware igual aos backends nativos:
assert isinstance(DynamoIdempotencyStore(), IdempotencyStore)
```

## Recap

- O header `Idempotency-Key` faz o servidor devolver a mesma resposta a qualquer retry **assim que a primeira requisição completa** — sem duplicar registro.
- Só verbos mutantes (`POST` / `PUT` / `PATCH` / `DELETE`) com o header são elegíveis; o resto passa direto (opt-in por requisição).
- Não há lock de in-progress: retries concorrentes durante a requisição original rodam o handler — mantenha timeouts do cliente generosos.
- `MemoryIdempotencyStore` é process-local e volátil (dev / single-replica); `RedisIdempotencyStore` cobre multi-réplica e sobrevive a restart.
- Implemente o protocolo `IdempotencyStore` para plugar qualquer backend (ex.: DynamoDB).

Próximo passo: combine com o [`@cached`](cache.md) para acelerar leituras, ou com o [Outbox pattern](outbox.md) para garantir entrega confiável de side-effects disparados pelo handler.
