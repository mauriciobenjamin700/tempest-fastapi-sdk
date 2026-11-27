---
description: "Use para limitar requisições por IP, usuário, tenant ou plano, com estado no Redis, token bucket, headers `RateLimit-*` e limite numa rota só."
---

# Rate limit (middleware e por rota)

Use para limitar requisições por IP, usuário, tenant ou plano, com estado no Redis, token bucket, headers `RateLimit-*` e limite numa rota só.

Parte da receita [Camada HTTP](../http.md).

## Middleware de rate limit


`RateLimitMiddleware` é um limitador de janela deslizante — cada chave única (IP do cliente por padrão) é permitida no máximo `max_requests` requisições dentro de cada janela `window_seconds`. Requisições que excedem ganham um `429 Too Many Requests` com header `Retry-After` e o envelope de erro canônico do SDK no corpo (veja abaixo). Dois eixos são plugáveis: o **store** (memória ou Redis) e a **chave** (IP, usuário, tenant, API key) — veja abaixo.

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import RateLimitMiddleware


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=120,
        window_seconds=60.0,
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    ...
```

### Limite por usuário / tenant / API key

Por padrão a chave é o IP do cliente. Para limitar **por principal** (usuário autenticado, tenant, API key), passe um `key_func`. O SDK traz factories prontas:

| Factory | Chave gerada | Uso |
| --- | --- | --- |
| `key_by_ip(trusted_header=...)` | `ip:<addr>` | Por IP (default). |
| `key_by_jwt_subject(jwt)` | `user:<sub>` | Por usuário autenticado (claim `sub`). |
| `key_by_jwt_claim(jwt, "tenant_id", scope="tenant")` | `tenant:<id>` | Por claim arbitrária do token. |
| `key_by_header("x-api-key", scope="apikey", is_known=api_keys.__contains__)` | `apikey:<valor>` | Por valor de header **verificado**; valor desconhecido cai no IP. |

!!! warning "O middleware roda antes das dependencies"
    O `RateLimitMiddleware` executa **antes** das `Depends` do FastAPI resolverem — então o usuário autenticado pela sua dependency de auth ainda não existe quando a chave é calculada. Por isso as factories `key_by_jwt_*` decodificam o bearer **do request cru** (via `JWTUtils.decode_or_none`, sem levantar exceção). Tráfego anônimo cai de volta no IP, então continua limitado.

!!! note "`key_by_header` exige `is_known=`"
    O valor do header é o que o cliente mandou, e o middleware o lê antes de qualquer auth. Sem verificação, mandar um valor inventado por requisição daria um orçamento novo a cada uma. Por isso `is_known=` é obrigatório: só o valor que ele aceita (uma consulta ao conjunto de API keys emitidas, um checksum no formato da chave) ganha bucket próprio; o resto é tratado como header ausente e cai no IP. A checagem é síncrona e roda em toda requisição — mantenha-a barata.

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import RateLimitMiddleware, key_by_jwt_subject

from src.api.dependencies.resources import get_jwt_utils


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=600,
        window_seconds=60.0,
        key_func=key_by_jwt_subject(get_jwt_utils()),        # ← limite por usuário
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

### Estado distribuído com Redis

O store padrão (`MemoryRateLimitStore`) conta **em processo** — correto para um único worker. Para deploys multi-réplica, passe `store=RedisRateLimitStore(redis)`: cada chave vira um sorted set e um único script Lua poda os expirados, conta e adiciona o novo hit **atomicamente** (sem corrida entre contar e adicionar). Em erro do Redis, `fail_open=True` (default) libera a requisição em vez de derrubar todo mundo.

```python
# src/api/app.py

from fastapi import FastAPI
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    RateLimitMiddleware,
    RedisRateLimitStore,
    key_by_jwt_subject,
)

from src.api.dependencies.resources import get_jwt_utils
from src.core.settings import settings


def create_app() -> FastAPI:
    redis: Redis = Redis.from_url(settings.REDIS_URL)
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=600,
        window_seconds=60.0,
        key_func=key_by_jwt_subject(get_jwt_utils()),
        store=RedisRateLimitStore(redis),                    # ← compartilhado entre réplicas
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

A semântica de janela deslizante é idêntica nos dois stores; só muda onde os contadores vivem. Ainda dá para empurrar o rate limiting para a borda (nginx / Cloudflare / AWS WAF) quando preferir.

!!! note "Por que `Redis.from_url` aqui, e não `AsyncRedisManager`?"
    Este client alimenta um **middleware**, montado no `create_app` (síncrono),
    antes de qualquer lifespan async rodar. `Redis.from_url()` é **lazy** —
    constrói sem abrir conexão, então serve nesse ponto. O `AsyncRedisManager`
    exige `await connect()` e cabe onde há contexto async: client via
    `Depends(cache.client_dependency)`, ou o `SSEBroker` montado no lifespan.
    Os dois precisam do extra `[cache]` (o pacote `redis`).

### Rajada tolerada: token bucket

A janela deslizante responde uma pergunta só: *essa chave passou de N
requisições nos últimos W segundos?* Um cliente que dispara 20 requisições de
uma vez e depois fica um minuto quieto é bem-comportado na média — e mesmo
assim a janela estrita o rejeita.

O **token bucket** separa as duas coisas: a taxa sustentada (tokens repostos
por segundo) e a rajada que ele absorve (capacidade do balde). Uma
`RateLimitRule` com `burst` vira token bucket; sem `burst`, continua janela
deslizante.

```python
from tempest_fastapi_sdk import RateLimitRule

# 10 req/s sustentadas (600 por minuto), absorvendo rajada de 100.
rule = RateLimitRule(max_requests=600, window_seconds=60.0, burst=100)
```

Regras vão para o middleware através de uma **policy**. Para um serviço de
tier único, `StaticRateLimitPolicy` aplica a mesma lista a todo mundo:

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    RateLimitMiddleware,
    RateLimitRule,
    StaticRateLimitPolicy,
)


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        policy=StaticRateLimitPolicy(
            [RateLimitRule(max_requests=600, window_seconds=60.0, burst=100)],
        ),
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

!!! info "`policy=` e `store=` são alternativas"
    `max_requests` / `window_seconds` / `store` descrevem **uma** janela
    deslizante. Com `policy=` você passa uma *lista* de limites, e os
    contadores vivem em `quota_store=` (`MemoryQuotaStore` por padrão,
    `RedisQuotaStore` para multi-réplica). Passar os dois levanta
    `ValueError` quando o Starlette monta a pilha de middleware — no startup
    do lifespan, não na linha do `add_middleware`, que retorna sem erro.
    Honrar um significaria ignorar o outro em silêncio.

### Cotas por plano

Uma lista de regras por tier, resolvida por requisição. As regras de um plano
são checadas **juntas**: um limite por minuto embaixo de um teto diário.

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    PlanRateLimitPolicy,
    RateLimitMiddleware,
    RateLimitRule,
    key_by_jwt_subject,
    key_by_plan_principal,
    plan_by_jwt_claim,
)

from src.api.dependencies.resources import get_jwt_utils


def create_app() -> FastAPI:
    jwt = get_jwt_utils()
    policy = PlanRateLimitPolicy(
        {
            "free": [
                RateLimitRule(60, 60.0, scope="minuto"),
                RateLimitRule(1_000, 86_400.0, scope="dia"),
            ],
            "pro": [
                RateLimitRule(600, 60.0, burst=100, scope="minuto"),
                RateLimitRule(100_000, 86_400.0, scope="dia"),
            ],
        },
        resolve=plan_by_jwt_claim(jwt, "plan"),
        default_plan="free",
    )
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        policy=policy,
        key_func=key_by_plan_principal(policy, key_by_jwt_subject(jwt)),
    )
    return app
```

Três decisões que valem entender:

- **Plano desconhecido cai no `default_plan`.** Um tier que a configuração não
  conhece precisa ser limitado, não servido sem limite — e uma requisição é o
  lugar errado para descobrir um erro de digitação. O que *é* validado na
  construção: `plans` vazio, `default_plan` fora do mapa, e plano sem nenhuma
  regra. Os três só apareceriam em produção como tráfego ilimitado.
- **Rejeição não gasta nada.** Uma requisição barrada pelo teto diário não pode
  queimar token do limite por minuto — senão o minuto drena sem uma única
  requisição servida. Os dois stores decidem todas as regras antes de escrever
  qualquer uma; o `RedisQuotaStore` faz isso dentro de **um** script Lua, que é
  a única forma de manter a lista inteira atômica.
- **`key_by_plan_principal` prefixa o plano na chave.** Quem sobe de tier passa
  a escrever em contadores novos em vez de herdar os esgotados. O preço é que
  quem *escolhe* o próprio plano (um header vindo do cliente) zeraria o
  contador só trocando de valor — use com um resolver que a sua borda controla.

### O corpo do 429 é o envelope de erro do SDK

O 429 sai no mesmo formato que `register_exception_handlers` escreve em todo
handler, então o cliente parseia **um** envelope para toda falha:

```json
{
    "detail": "Too many requests",
    "code": "TOO_MANY_REQUESTS",
    "details": {"retry_after_seconds": 60, "limit": 15}
}
```

`code` é o campo em que o cliente ramifica — nunca `detail`, que é prosa e muda
com a locale negociada quando existe um `MessageCatalog`. Troque os dois pelo
que a sua API usa:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import RateLimitMiddleware


app = FastAPI()

app.add_middleware(
    RateLimitMiddleware,
    max_requests=15,
    window_seconds=1.0,
    error_message="Calma parceiro, você está fazendo muitas requisições!",
    error_code="TOO_MANY_REQUESTS",
)
```

`details` carrega o que antes só existia em header: `retry_after_seconds` (o
mesmo número do `Retry-After`) e `limit` da regra que barrou — no modo policy,
a mais apertada.

!!! warning "Mudou na v0.256.0"
    Até a v0.255.0 o 429 saía como `text/plain` com o `error_message` cru no
    corpo. Quem lê esse corpo como texto precisa passar a ler JSON e pegar
    `detail`; quem já ramificava por `status === 429` não muda nada.

    A troca fecha uma contradição do próprio SDK: `error_responses()` sempre
    apontou o 429 para o `ErrorResponseSchema`, então cliente gerado a partir
    do OpenAPI quebrava ao desserializar o texto.

!!! tip "Documentando o 429 no OpenAPI"
    `error_responses(TooManyRequestsException)` descreve exatamente o que o
    middleware envia — o `error_code` default é o `code` dessa exceção. Se você
    trocar o `error_code`, troque também a exceção que documenta a rota, ou o
    schema volta a divergir do corpo.

!!! info "Por que o middleware não levanta a exceção"
    Seria a saída óbvia — levantar `TooManyRequestsException` e deixar o
    handler registrado formatar. Não funciona: `BaseHTTPMiddleware` adicionado
    por `add_middleware` fica **fora** do `ExceptionMiddleware` do Starlette,
    então exceção levantada no `dispatch` não encontra handler e vira 500. Por
    isso o middleware monta a resposta ele mesmo, lendo o `code` default da
    própria exceção para os dois não divergirem.

### Headers `RateLimit-*`

Toda resposta sai com `RateLimit-Limit` e `RateLimit-Remaining` (desligue com
`limit_headers=False`), descrevendo a regra **mais apertada** — a que o cliente
deve usar para se auto-regular. `RateLimit-Reset` só sai quando o número é
conhecido: sempre no modo policy, e apenas no 429 no modo janela simples. O
store de janela deslizante não reporta reset para requisição aceita, e um
número chutado é pior que header ausente.

### Multi-réplica

```python
# src/api/app.py

from fastapi import FastAPI
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    RateLimitMiddleware,
    RateLimitRule,
    RedisQuotaStore,
    StaticRateLimitPolicy,
)

from src.core.settings import settings


def create_app() -> FastAPI:
    redis: Redis = Redis.from_url(settings.REDIS_URL)
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        policy=StaticRateLimitPolicy(
            [RateLimitRule(600, 60.0, burst=100)],
        ),
        quota_store=RedisQuotaStore(redis),        # ← compartilhado entre réplicas
    )
    return app
```

Regra de janela vira sorted set de timestamps; regra de bucket vira um hash com
`tokens` + `ts`. O TTL da chave de bucket cobre o tempo de **encher o balde**,
não a janela: um bucket lento com rajada grande (10 tokens/minuto, `burst=1000`)
leva mais de uma hora para encher, e expirar antes disso devolveria um balde
cheio de graça. Em erro do Redis, `fail_open=True` (default) libera a
requisição.

### Relógio: o do Redis, não o da réplica

Recarga de bucket e poda de janela dependem de "quanto tempo passou". Com várias
réplicas, a pergunta é: **pelo relógio de quem?**

Os dois stores de Redis (`RedisQuotaStore` e `RedisRateLimitStore`) respondem
"pelo do Redis": o script Lua lê `TIME` do próprio servidor, e nenhuma réplica
manda timestamp. Assim, réplicas com relógio de parede dessincronizado — ou uma
VM que acabou de dar um salto de NTP — continuam vendo **um** balde e **uma**
janela.

!!! info "O que isso evita, medido"
    Até a v0.301.0 cada réplica mandava o `time.time()` dela. Contra Redis 4 e 7
    reais, com dois clientes — um esgotando o limite, outro sondando:

    - um cliente **uma hora adiantado** passou num bucket (1 token/hora,
      `burst=3`) e numa janela (3/hora) que o outro tinha acabado de esgotar;
    - um cliente **uma hora atrasado** gravou timestamps que deixaram o cliente
      no horário passar do mesmo jeito.

    Com o `TIME` do servidor, os oito casos são negados. Eles ficam em
    `tests/api/test_quota_redis_live.py` (`make test-docker`).

Duas consequências práticas:

- **O relógio que importa é o do host do Redis.** Um salto nele ainda move todas
  as chaves de uma vez — mas é um relógio só, em vez de um por réplica.
- **Redis 4 funciona**, porque o script liga a replicação por efeito
  (`redis.replicate_commands()`) antes de escrever. Sem isso o Redis 4.0.14
  recusa a escrita depois do `TIME`, e com `fail_open=True` um limite de 1
  requisição deixou passar 10 de 10.

!!! warning "redis-py 8 e Redis anterior ao 6"
    O `redis-py` 8.1.0 — o piso do extra `[cache]` — abre a conexão com
    `HELLO 3`, que o Redis 4 e o 5 recusam como comando desconhecido. Contra esses servidores, crie o client com
    `protocol=2`: `Redis.from_url(settings.REDIS_URL, protocol=2)`.

### Testando sem depender da velocidade da máquina

O `MemoryQuotaStore` aceita `clock=`: qualquer callable que devolve segundos.
Com um relógio que só anda quando o teste manda, "o burst se esgota" e "o token
volta depois de uma hora" viram asserções exatas — não uma corrida contra a
taxa de recarga:

```python
import asyncio

from tempest_fastapi_sdk import MemoryQuotaStore, RateLimitRule


class ManualClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now


async def main() -> None:
    clock = ManualClock()
    store = MemoryQuotaStore(clock=clock)
    rules = [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=2)]

    print([(await store.consume("k", rules)).allowed for _ in range(3)])
    clock.now += 3600.0
    print((await store.consume("k", rules)).allowed)


asyncio.run(main())
```

Saída:

```text
[True, True, False]
True
```

!!! tip "Por que não `sleep`?"
    Uma regra de 100 por segundo repõe um token a cada 10 ms. Quatro chamadas
    seguidas numa máquina carregada passam disso, o token volta, e o teste
    "o burst se esgota" falha: foi o que aconteceu com a própria suíte do SDK
    (#339). Taxa desprezível na janela do teste (1 por hora) ou relógio
    injetado tiram a velocidade da máquina da asserção.


## Rate limit por rota (`make_rate_limit_dependency`)

O `RateLimitMiddleware` limita o app inteiro, e o escopo dele só diminui por
subtração (`exempt_paths`). Duas perguntas ficam de fora:

- **"Só `POST /api/invites`."** Uma rota cara merece um teto que o resto do app
  não tem.
- **"Por e-mail convidado, não só por IP."** A chave está no **corpo**, e o
  `key_func` do middleware recebe só o `Request`, de forma síncrona, antes de o
  corpo ser lido.

`make_rate_limit_dependency` responde as duas: é uma dependency que você pendura
em uma rota, conta no mesmo `RateLimitStore` do middleware (memória ou Redis) e
devolve o mesmo 429.

### Limitar um endpoint por IP e por campo do corpo

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tempest_fastapi_sdk import (
    MemoryRateLimitStore,
    key_by_body_field,
    key_by_ip,
    make_rate_limit_dependency,
    register_exception_handlers,
)


class InviteCreateSchema(BaseModel):
    email: str


invite_limit = make_rate_limit_dependency(
    MemoryRateLimitStore(),
    max_requests=3,
    window_seconds=3600.0,
    key=[
        key_by_ip(trusted_header="x-real-ip"),
        key_by_body_field("email"),
    ],
)

app = FastAPI()
register_exception_handlers(app)


@app.post("/api/invites", status_code=202, dependencies=[Depends(invite_limit)])
async def create_invite(payload: InviteCreateSchema) -> dict[str, str]:
    return {"invited": payload.email}


@app.get("/api/invites")
async def list_invites() -> list[str]:
    return []


client = TestClient(app)
for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"):
    response = client.post(
        "/api/invites",
        json={"email": "ana@example.com"},
        headers={"x-real-ip": ip},
    )
    print(response.status_code, response.json())
print(response.headers["retry-after"], response.headers["ratelimit-limit"])
print(client.get("/api/invites").status_code)
```

Saída:

```text
202 {'invited': 'ana@example.com'}
202 {'invited': 'ana@example.com'}
202 {'invited': 'ana@example.com'}
429 {'detail': 'Too many requests', 'code': 'TOO_MANY_REQUESTS', 'details': {'retry_after_seconds': 3600, 'limit': 3}}
3600 3
200
```

Quatro IPs diferentes, o mesmo e-mail: o quarto pedido cai pela chave do e-mail,
e o `GET` da mesma URL continua livre. Pedaço por pedaço:

- **`key=[...]`** — cada função devolve uma chave (ou várias, ou nenhuma), e
  cada chave conta no mesmo store. A primeira que estoura recusa o pedido; as
  seguintes nem são contadas, para um pedido recusado não gastar o orçamento
  delas. Funções síncronas (`key_by_ip`, `key_by_header`, `key_by_jwt_*`) e
  assíncronas valem igual.
- **`key_by_body_field("email")`** — lê o campo do corpo JSON. O FastAPI lê o
  corpo **antes** de resolver as dependencies e o Starlette guarda os bytes no
  `Request`, então o endpoint continua recebendo o `payload` validado — a saída
  acima é essa prova. O valor é normalizado (`strip` + minúsculas) e guardado
  como SHA-256, para o Redis não acumular e-mail em texto puro
  (`normalize=False`, `hash_value=False` desligam). Corpo que não é JSON, ou
  sem o campo, não gera chave: o 422 do próprio endpoint responde esse pedido.
- **`trusted_ip_header`** — sem `key`, a chave default é o IP resolvido por
  esse header (`make_rate_limit_dependency(store, ..., trusted_ip_header="x-real-ip")`).
  Com `key`, passe o header para o `key_by_ip` de dentro da lista; passar os
  dois levanta `ValueError` na construção, em vez de ignorar o header calado.
- **O balde é da rota.** O prefixo default de cada chave é o método e o path
  template (`"POST /api/invites"`), então a mesma dependency em duas rotas dá um
  orçamento para cada, `/users/1` e `/users/2` dividem o de `/users/{user_id}`,
  e o contador nunca colide com o do middleware no mesmo Redis. Passe
  `scope="invites"` para várias rotas dividirem um orçamento.

!!! info "O mesmo 429 do middleware"
    O corpo (`detail`, `code`, `details.retry_after_seconds`, `details.limit`) e
    os headers (`Retry-After`, `RateLimit-Limit`, `RateLimit-Remaining`,
    `RateLimit-Reset`) saem dos mesmos helpers que o `RateLimitMiddleware` usa
    — `tests/api/test_rate_limit_dependency.py` compara os dois. A diferença é
    o caminho: a dependency **levanta** `TooManyRequestsException`, então
    precisa do `register_exception_handlers` no app, e com um `MessageCatalog`
    registrado ali o `detail` sai traduzido pelo `code`
    (`"Requisições em excesso"` em `pt-BR`), o que o middleware — que responde
    fora dos handlers — não faz.

!!! tip "Store criado no `create_app`"
    Passe uma função que recebe o `Request` no lugar do store —
    `make_rate_limit_dependency(store_of, ...)`, com `store_of(request)`
    devolvendo `request.app.state.rate_limit_store` — e a dependency declarada
    no import conta no store do app que atende o request. Medido com dois apps
    e a mesma dependency: cada um conta no seu.

!!! tip "Multi-réplica"
    Troque `MemoryRateLimitStore()` por `RedisRateLimitStore(redis)` e todas as
    réplicas dividem os contadores, exatamente como no middleware.
