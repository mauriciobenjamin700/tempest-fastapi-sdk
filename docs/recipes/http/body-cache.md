---
description: "Use para recusar corpo grande com 413 antes do parse e responder 304 com ETag sem misturar requisições autenticadas."
---

# Limite de body e cache HTTP (ETag / 304)

Use para recusar corpo grande com 413 antes do parse e responder 304 com ETag sem misturar requisições autenticadas.

Parte da receita [Camada HTTP](../http.md).

## Limite de tamanho do body (`BodySizeLimitMiddleware`)

Um upload de 2 GB num endpoint que espera JSON derruba o worker antes de
qualquer validação do Pydantic. `BodySizeLimitMiddleware` é ASGI puro e corta
isso na porta:

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import BodySizeLimitMiddleware

app: FastAPI = FastAPI()

app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=2 * 1024 * 1024,             # 2 MiB pra qualquer request
    exclude_paths=("/api/files/upload",),  # rota de upload tem limite próprio
)
```

São **duas** checagens:

1. **Header** — `Content-Length` acima do teto responde `413` na hora, sem ler
   um byte do corpo. Pega o caso comum, em que o cliente sabe o tamanho.
2. **Streaming** — em upload `chunked` (sem `Content-Length`), o middleware
   conta os bytes das mensagens `http.request` e aborta ao cruzar o teto.

A resposta é o envelope canônico do SDK:
`{"detail": "Request body too large.", "code": "REQUEST_BODY_TOO_LARGE", "details": {"max_bytes": 1048576}}`
com HTTP 413.

!!! info "`exclude_paths` casa por prefixo"
    O match é `startswith`, então quanto mais específico o prefixo, melhor —
    `("/api/files",)` libera tudo abaixo de `/api/files`. Use pra rota que
    aceita arquivo grande de propósito e aplica o **próprio** limite (via
    `UploadUtils(max_size=...)`, por exemplo). `max_bytes=0` desliga a
    checagem — não suba isso pra produção.

## Cache de resposta HTTP (ETag / 304)

`ResponseCacheMiddleware` entrega dois ganhos de performance em camadas.

**ETag + GET condicional (sempre ligado).** Toda resposta cacheável ganha um `ETag` forte (hash do corpo) e um `Cache-Control`. Quando o cliente manda um `If-None-Match` que bate, o middleware responde `304 Not Modified` sem corpo — o handler ainda roda, mas os bytes não vão pela rede.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import ResponseCacheMiddleware


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ResponseCacheMiddleware, ttl_seconds=30)   # só ETag/304
    return app
```

**Cache server-side (opt-in via `store=`).** Com um store ligado, uma resposta `GET`/`HEAD` cacheável é guardada por `ttl_seconds`; uma requisição igual depois é servida **sem rodar o handler** (`X-Cache: HIT`), e um `If-None-Match` que bate no ETag guardado ainda curto-circuita pro `304`.

```python
from typing import Any

from fastapi import FastAPI

from tempest_fastapi_sdk import RedisResponseCacheStore, ResponseCacheMiddleware


def create_app(redis: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        ResponseCacheMiddleware,
        store=RedisResponseCacheStore(redis),   # compartilhado entre réplicas
        ttl_seconds=60,
        vary=("Accept-Encoding",),               # varia a chave e emite Vary
    )
    return app
```

Só métodos seguros (`GET`/`HEAD`) e respostas de sucesso (`200` por padrão) são cacheados. Respostas que optam por fora — `Cache-Control: no-store`/`private` ou com `Set-Cookie` (personalizadas) — **nunca** são guardadas.

!!! tip "Chave de cache"
    A chave é `método|path|query` mais os headers listados em `vary=`. Passe `cacheable=<predicado>` pra excluir requisições específicas, ou `exempt_paths=(...)` pra pular paths exatos. O store espelha o do idempotency (memória ou Redis com client cru), então compõe com o mesmo Redis do serviço.

### Stream nunca é drenado (e `exempt_paths` é igualdade)

Cachear significa **ler o corpo até o fim** para hashear e guardar. Num
`text/event-stream` o corpo só acaba quando o cliente desconecta, então
até a v0.285.0 uma rota SSE atrás deste middleware nunca respondia: a
requisição pendurava até o `proxy_read_timeout` do nginx e virava `504`.

Desde a v0.286.0 o middleware **passa o stream adiante intacto**. Não há
o que configurar: uma resposta com `Content-Type: text/event-stream`, ou
sem `Content-Length`, não é cacheada nem ganha `ETag`.

!!! warning "O mesmo valia para `IdempotencyMiddleware`"
    Ele drena o corpo para poder reproduzir a resposta num retry, e não
    tinha lista de isenção nenhuma. Um `POST` que devolve stream — como o
    `/generate/stream` do `make_genai_router` — pendurava do mesmo jeito
    quando o cliente mandava `Idempotency-Key`. Também passa adiante
    agora: um stream não é reproduzível a partir de store nenhum.

**`exempt_paths` mudou de significado em dois middlewares.** Ele era
igualdade em `ResponseCacheMiddleware`, `RateLimitMiddleware` e
`GracefulShutdownMiddleware`, e prefixo em `AccessLogMiddleware` e
`HoneypotBanMiddleware` — o mesmo nome de argumento, dois comportamentos.
Um serviço que isentava sua rota SSE dos dois passava a **mesma tupla**
para os dois e ganhava isenção num e nada no outro.

Agora `exempt_paths` é **igualdade nos cinco**, e prefixo tem nome
próprio:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import AccessLogMiddleware, ResponseCacheMiddleware


def create_app() -> FastAPI:
    """Build an app whose SSE subtree is exempt from both middlewares."""
    app: FastAPI = FastAPI()
    app.add_middleware(
        ResponseCacheMiddleware,
        ttl_seconds=30,
        exempt_paths=("/health/liveness",),      # este path, e só ele
        exempt_prefixes=("/api/sse",),           # e tudo sob /api/sse
    )
    app.add_middleware(
        AccessLogMiddleware,
        exempt_prefixes=("/api/sse",),           # antes: exempt_paths
    )
    return app
```

!!! danger "Migração: `exempt_paths` de prefixo vira `exempt_prefixes`"
    Só `AccessLogMiddleware` e `HoneypotBanMiddleware` mudam de
    comportamento. Se você passava `exempt_paths=("/api/sse",)` a um
    deles contando que cobrisse `/api/sse/stream`, **renomeie para
    `exempt_prefixes`** — caso contrário a isenção deixa de valer para as
    rotas filhas.

    O alargamento contrário — tornar tudo prefixo — foi recusado de
    propósito: em `RateLimitMiddleware` uma isenção é um **buraco no rate
    limit**, e `exempt_paths=("/health",)` viraria isenção de
    `/health-admin` também, sem ninguém pedir.


### Requisição autenticada não divide entrada de cache

Repare no que a chave **não** tem: quem pediu. Se `GET /api/me` de duas pessoas cai na mesma chave, a primeira resposta guardada é servida pra segunda — e pra qualquer anônimo que peça o mesmo path.

Por isso uma requisição que carrega `Authorization`, `Cookie`, `X-Token` ou `X-API-Key` **passa por fora do store compartilhado**. Ela continua ganhando `ETag` e `304` (que são por resposta, e seguros); só não entra num cache que outra pessoa pode ler. Se a sua auth lê outro header, passe a lista em `credential_headers=`. Rota protegida por `make_token_dependency` também responde `Cache-Control: no-store`, então fica fora do store mesmo com um `header_name=` que a lista não conhece.

```python
from typing import Any

from fastapi import FastAPI

from tempest_fastapi_sdk import RedisResponseCacheStore, ResponseCacheMiddleware


def create_app(redis: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        ResponseCacheMiddleware,
        store=RedisResponseCacheStore(redis),
        ttl_seconds=60,
        cache_credentialed=True,   # opt-in: chave passa a incluir a credencial
    )
    return app
```

Com `cache_credentialed=True` um digest dos headers de credencial entra na chave, então cada chamador ganha a sua entrada e o cache volta a valer pra rota autenticada. Ligue só depois de conferir que as rotas cacheadas não carregam uma **segunda** identidade que a credencial não representa (um header de tenant, uma sessão independente do path) — nesse caso a credencial sozinha não separa o suficiente.

!!! warning "`Cache-Control` padrão é `private`"
    O default emitido é `private, max-age=<max_age>`. O middleware não tem como saber se o corpo de uma rota é personalizado, e um `public` errado ali faz um proxy compartilhado servir a resposta de um usuário pra outro. Passe `cache_control="public, max-age=…"` **de propósito**, num router que só serve conteúdo compartilhado.
