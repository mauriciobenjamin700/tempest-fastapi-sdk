# Observabilidade (tracing + slow queries)

Logs te dizem **o que** aconteceu num serviço; tracing distribuído te diz
**onde** o tempo foi gasto numa request que cruza vários serviços, e o
`SlowQueryLogger` te aponta **qual** query está arrastando o p99. Esta
receita cobre os dois.

!!! info "Onde isso encaixa"
    O [`RequestIDMiddleware`](http.md) correlaciona **logs** por request;
    o OpenTelemetry correlaciona **spans** entre serviços. Eles se
    complementam — use os dois juntos.

## Tracing distribuído com OpenTelemetry

`setup_tracing` instala um provider OpenTelemetry e auto-instrumenta as
camadas mais comuns de um serviço Tempest: FastAPI (requests de entrada),
SQLAlchemy (queries) e httpx (chamadas de saída). Requer o extra `[otel]`:

```bash
uv add "tempest-fastapi-sdk[otel]"
```

São **dois momentos**, e cada um tem a sua chamada:

1. `setup_tracing` no **nível do módulo**, logo depois de criar a app (ou
   dentro da sua `create_app`). É aqui que as requests passam a virar span.
2. `instrument_sqlalchemy_engine` no **lifespan**, logo depois do
   `db.connect()` — o engine do `AsyncDatabaseManager` só existe depois do
   `connect()`.

```python hl_lines="16 22"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import AsyncDatabaseManager, setup_tracing
from tempest_fastapi_sdk.api import instrument_sqlalchemy_engine

db: AsyncDatabaseManager = AsyncDatabaseManager("postgresql+asyncpg://...")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Conecta o banco e instrumenta o engine recém-criado."""
    await db.connect()
    instrument_sqlalchemy_engine(db.engine)
    yield
    await db.disconnect()


app: FastAPI = FastAPI(lifespan=lifespan)
setup_tracing(
    app,
    service_name="orders-api",
    otlp_endpoint="http://otel-collector:4317",
)
```

Pronto: cada request vira um span raiz, e as queries e as chamadas httpx
feitas dentro dela viram spans filhos. Medido com um exportador em memória e
o banco em SQLite em memória, uma request `GET /orders` que chama httpx e
roda um `SELECT 1` produz estes spans (`span <- pai`; o sufixo
`file:tempest_mem_<hex>` é o nome do banco em memória, encurtado aqui):

```text
GET <- GET /orders
connect <- GET /orders
BEGIN file:tempest_mem_<hex> <- GET /orders
SELECT file:tempest_mem_<hex> <- GET /orders
GET /orders http send <- GET /orders
GET /orders http send <- GET /orders
GET /orders <- None
```

O trace inteiro aparece no Jaeger / Tempo / Honeycomb sob o nome
`orders-api`.

!!! warning "Não chame `setup_tracing` dentro do lifespan"
    O span de request vem de embrulhar a pilha de middleware da app, e o
    Starlette monta essa pilha no **primeiro evento ASGI** que recebe — que é
    justamente o startup do lifespan. Chamado de lá, `setup_tracing` encontra
    a pilha já montada e **nenhuma request vira span**; as queries até saem,
    mas como raízes soltas, sem request pai. Desde a 0.303.0 a função detecta
    esse caso e emite um `RuntimeWarning` em vez de falhar em silêncio.

!!! tip "Engine que já existe no nível do módulo"
    Se o engine já existe antes do lifespan (um `create_async_engine`
    seu, no módulo), dá para passá-lo direto:
    `setup_tracing(app, ..., sqlalchemy_engine=engine)`. Para o engine do
    `AsyncDatabaseManager`, use `instrument_sqlalchemy_engine` depois do
    `connect()`. Um engine por processo: o instrumentador do SQLAlchemy é
    singleton, e uma segunda chamada (para outro engine) só loga
    `Attempting to instrument while already instrumented` — medido, as
    queries do segundo engine não geram span.

### Sem coletor (debug local)

Passe `otlp_endpoint=None` pra instalar um exportador de console — os spans
saem no stdout, sem precisar subir um coletor:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import setup_tracing

app: FastAPI = FastAPI()
setup_tracing(app, service_name="orders-api", otlp_endpoint=None)
```

### Amostragem (sampling)

Em produção com tráfego alto, traçar 100% das requests é caro. Passe
`sample_ratio` pra amostrar uma fração (decisão head-based, propagada pra
spans filhos):

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import setup_tracing

app: FastAPI = FastAPI()
setup_tracing(
    app,
    service_name="orders-api",
    otlp_endpoint="http://otel-collector:4317",
    sample_ratio=0.1,  # ~10% das requests
    resource_attributes={"deployment.environment": "prod"},
)
```

!!! tip "Argumentos, não env vars"
    O endpoint, o sampling e os atributos vêm dos **argumentos** da função —
    o call site é a única fonte de verdade. Nada de configurar metade no
    código e metade em `OTEL_*` env vars.

!!! note "Instrumentação best-effort"
    SQLAlchemy e httpx só são instrumentados se os pacotes
    `opentelemetry-instrumentation-sqlalchemy` /
    `...-httpx` estiverem instalados (o extra `[otel]` já traz os dois). Se
    faltarem, `setup_tracing` pula essa instrumentação em silêncio em vez de
    quebrar o boot. `instrument_sqlalchemy_engine`, por ser um pedido
    explícito, levanta `ImportError` nesse caso.

## Slow query logger

`SlowQueryLogger` registra um listener nos eventos do engine SQLAlchemy e
emite uma linha de log toda vez que uma statement passa de um limite
configurável. É a forma mais barata de achar o N+1 ou o índice faltando.
**Não precisa de extra** — usa só SQLAlchemy.

```python
import logging

from tempest_fastapi_sdk import AsyncDatabaseManager, SlowQueryLogger

db: AsyncDatabaseManager = AsyncDatabaseManager("postgresql+asyncpg://...")


async def wire_slow_query_log() -> None:
    """Liga o log de queries lentas no startup."""
    await db.connect()
    slow: SlowQueryLogger = SlowQueryLogger(
        db.engine,
        threshold_ms=200.0,       # loga queries >= 200ms
        level=logging.WARNING,
    )
    slow.attach()
```

Cada query lenta vira uma linha tipo:

```text
WARNING ... slow query: 312.4ms >= 200.0ms threshold | SELECT users.id, ...
```

### Parâmetros e EXPLAIN (só em dev)

Por padrão os bind parameters **não** entram no log (costumam carregar
PII/segredos). Em desenvolvimento, ligue `log_parameters=True` e/ou
`explain=True` pra ver o plano de execução:

```python
import logging

from tempest_fastapi_sdk import SlowQueryLogger

from src.api.dependencies.resources import db


slow: SlowQueryLogger = SlowQueryLogger(
    db.engine,
    threshold_ms=50.0,
    log_parameters=True,  # inclui os binds — dev only
    explain=True,         # roda EXPLAIN e anexa o plano — custa 1 round-trip
)
slow.attach()
```

!!! warning "EXPLAIN custa um round-trip"
    Com `explain=True` cada query lenta dispara um `EXPLAIN` extra. Deixe
    desligado em produção, ligue só quando estiver caçando um plano ruim.

Pra desligar (ex.: num shutdown ou teste), chame `slow.detach()`.

## Recap

- `setup_tracing(app, service_name=..., otlp_endpoint=...)` liga tracing
  distribuído com auto-instrumentação de FastAPI/SQLAlchemy/httpx — extra
  `[otel]`. Chame no nível do módulo, nunca no lifespan.
- `instrument_sqlalchemy_engine(db.engine)` no lifespan, depois do
  `connect()`, põe as queries do `AsyncDatabaseManager` sob o span da
  request.
- `otlp_endpoint=None` exporta spans pro console (debug local);
  `sample_ratio` controla a amostragem.
- `SlowQueryLogger(engine, threshold_ms=...).attach()` loga queries lentas
  sem extra nenhum; parâmetros e `EXPLAIN` ficam atrás de flags opt-in.
