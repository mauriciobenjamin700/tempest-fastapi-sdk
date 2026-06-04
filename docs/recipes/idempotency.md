# Idempotência

`IdempotencyMiddleware` implementa o padrão `Idempotency-Key` usado por Stripe, AWS, GitHub e Plaid: o cliente envia um header único, o servidor processa **uma vez** e devolve a mesma resposta a qualquer retry, sem duplicar linha no banco / cobrar duas vezes.

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

## Setup produção (multi-réplica via Redis)

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
