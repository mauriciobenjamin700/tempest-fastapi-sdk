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
pip install "tempest-fastapi-sdk[otel]"
```

Chame uma vez no startup, depois que a app existe e (quando quiser tracear
queries) depois que o banco conectou:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import AsyncDatabaseManager, setup_tracing

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager("postgresql+asyncpg://...")


@app.on_event("startup")
async def _startup() -> None:
    """Conecta o banco e liga o tracing."""
    await db.connect()
    setup_tracing(
        app,
        service_name="orders-api",
        otlp_endpoint="http://otel-collector:4317",
        sqlalchemy_engine=db.engine,
    )
```

Pronto: cada request vira um span pai, cada query e cada chamada httpx vira
um span filho, e o trace inteiro aparece no Jaeger / Tempo / Honeycomb sob o
nome `orders-api`.

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
    faltarem, a instrumentação é pulada em silêncio em vez de quebrar o boot.

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
  `[otel]`.
- `otlp_endpoint=None` exporta spans pro console (debug local);
  `sample_ratio` controla a amostragem.
- `SlowQueryLogger(engine, threshold_ms=...).attach()` loga queries lentas
  sem extra nenhum; parâmetros e `EXPLAIN` ficam atrás de flags opt-in.
