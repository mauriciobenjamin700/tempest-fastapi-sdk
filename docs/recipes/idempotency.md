# Idempotência

`IdempotencyMiddleware` implementa o padrão `Idempotency-Key` usado por Stripe, AWS, GitHub e Plaid: o cliente envia um header único e, **assim que a primeira requisição completa**, o servidor devolve a mesma resposta a qualquer retry — sem duplicar linha no banco / cobrar duas vezes.

!!! warning "Lock de in-progress é por processo"
    Dentro de um processo, requisições concorrentes com a mesma chave são serializadas: a segunda espera a primeira terminar e replica a resposta dela. **Entre réplicas isso não vale** — o store deduplica retries (a primeira já terminou e gravou), mas duas requisições ao mesmo tempo em réplicas distintas passam ambas pelo handler. Mantenha os timeouts do cliente generosos em relação à latência do handler para evitar retries prematuros.

## Como funciona

1. Cliente envia `POST /charge` com `Idempotency-Key: chk_<uuid>`.
2. Middleware processa, salva a resposta completa indexada por `(chamador, method, path, key)`.
3. Cliente retentou? Middleware devolve a **mesma resposta cacheada**. Handler não roda de novo.

Só verbos mutantes (`POST` / `PUT` / `PATCH` / `DELETE`) são elegíveis — `GET` é naturalmente idempotente.

!!! warning "Opt-in por requisição"
    Sem o header, o middleware deixa passar normal. Endpoints existentes não quebram — só quem precisar da garantia envia o header.

## A chave é escopada por chamador

O valor do header é escolhido **pelo cliente**. Sozinho, ele não identifica ninguém: dois chamadores que escolhem a mesma string no mesmo endpoint dividiriam a entrada, e o replay entrega a **resposta** guardada — corpo e headers inclusos.

Por isso o middleware dobra um digest das credenciais da requisição na chave: `Authorization`, `Cookie`, `X-Token` (o header do `make_token_dependency`) e `X-API-Key`. Uma entrada só é replicada pra credencial que a criou. Se a sua auth lê outro header, passe-o em `credential_headers=` — um header fora da lista faz a requisição parecer anônima.

### Requisição anônima não é cacheada

Requisição **sem** credencial não tem identidade para escopar, então por padrão ela passa direto ao handler, como se não tivesse `Idempotency-Key`. Login, confirmação de reset de senha e troca de código OAuth carregam o segredo no corpo e devolvem token: um escopo anônimo compartilhado entregaria o par de tokens de um chamador a quem repetisse a chave. `allow_anonymous=True` restaura o escopo anônimo compartilhado para o serviço cujas mutações anônimas podem ser replicadas entre chamadores.

### A chave fica presa à requisição

A entrada guarda um digest de método, path, query e corpo. Repetir a chave com outra requisição — outro corpo, outra query — responde **422** com `code="IDEMPOTENCY_KEY_REUSED"`, em vez de replicar a resposta da primeira (o comportamento que a Stripe documenta). Entrada gravada por uma versão anterior, sem esse digest, também responde 422.

```python
from fastapi import FastAPI, Request

from tempest_fastapi_sdk import (
    IdempotencyMiddleware,
    JWTUtils,
    MemoryIdempotencyStore,
)

app = FastAPI()
tokens = JWTUtils("change-me")


def principal(request: Request) -> str:
    """Devolve o `sub` de um JWT verificado, ou vazio para anônimo."""
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
    claims = tokens.decode_or_none(bearer.strip())
    return "" if claims is None else str(claims.get("sub", ""))


app.add_middleware(
    IdempotencyMiddleware,
    store=MemoryIdempotencyStore(),
    ttl_seconds=24 * 3600,
    principal_resolver=principal,
)
```

Use `principal_resolver=` quando a identidade vive em outro lugar, e derive-a de algo que o resolver **verifica** — o middleware roda antes de qualquer dependência de auth, então um header que o cliente escreve (um `x-tenant-id`) não isola ninguém. String vazia significa anônimo. Devolver uma constante ali restaura o compartilhamento entre chamadores e é **inseguro** em endpoint multi-tenant.

!!! note "O que não é replicado"
    Um `Set-Cookie` da resposta original fica **fora** da cópia guardada — reemitir a sessão do primeiro chamador num replay entregaria a sessão dele. O chamador original recebe seu cookie normalmente; só o replay não.

!!! tip "Erro 5xx não é cacheado"
    Por padrão respostas `>= 500` não entram no store, então o retry do cliente realmente chega ao handler. Uma falha transitória cacheada por `ttl_seconds` prenderia aquela chave no erro pelo tempo todo da entrada. Passe `cache_server_errors=True` se o seu caso exige o oposto.

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

!!! note "Por que `Redis.from_url` aqui, e não `AsyncRedisManager`?"
    Este client alimenta um **middleware**, montado no `create_app` (síncrono),
    antes de qualquer lifespan async rodar. `Redis.from_url()` é **lazy** —
    constrói sem abrir conexão, então serve nesse ponto. O `AsyncRedisManager`
    exige `await connect()` e cabe onde há contexto async: client via
    `Depends(cache.client_dependency)`, ou o `SSEBroker` montado no lifespan.
    Os dois precisam do extra `[cache]` (o pacote `redis`).

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
- Há lock de in-progress **por processo**: dentro de uma réplica, requisições concorrentes com a mesma chave são serializadas e a segunda replica a resposta da primeira. Entre réplicas não vale — mantenha timeouts do cliente generosos.
- A chave é escopada pela credencial (`Authorization`, `Cookie`, `X-Token`, `X-API-Key`); requisição anônima não é cacheada sem `allow_anonymous=True`, e a mesma chave com outra requisição responde 422.
- `MemoryIdempotencyStore` é process-local e volátil (dev / single-replica); `RedisIdempotencyStore` cobre multi-réplica e sobrevive a restart.
- Implemente o protocolo `IdempotencyStore` para plugar qualquer backend (ex.: DynamoDB).

Próximo passo: combine com o [`@cached`](cache.md) para acelerar leituras, ou com o [Outbox pattern](outbox.md) para garantir entrega confiável de side-effects disparados pelo handler.
