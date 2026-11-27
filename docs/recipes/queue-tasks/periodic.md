# Tarefas periódicas: `cron` e `interval`

Use para agendar tarefas periódicas no mesmo `TaskQueue`, sem scheduler separado no seu código.

Parte da receita [Fila e Tarefas](../queue-tasks.md).

Agendar é parte do mesmo `TaskQueue` — sem scheduler separado no seu código.

!!! tip "Não sabe cron? Use os enums e helpers (v0.94.0)"
    Ninguém precisa decorar `"0 9 * * MON-FRI"`. O módulo
    `tempest_fastapi_sdk.tasks` traz **`CronEnum`** (expressões prontas),
    **`CronOffsetEnum`** (fusos por lugar, não por dígitos), **`WeekdayEnum`** e
    **funções construtoras** (`daily`, `weekdays`, `hourly`,
    `every_n_minutes`, `weekly`, `weekends`, `monthly`). Todas viram uma
    string cron simples que entra direto no `@tq.cron(...)`.

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import CronEnum, CronOffsetEnum, WeekdayEnum, daily, weekdays

from src.tasks import tq


# Legível, sem sintaxe cron:
@tq.cron(CronEnum.EVERY_WEEKDAY_9AM, cron_offset=CronOffsetEnum.BRASILIA)
async def daily_digest() -> None:
    ...


@tq.cron(daily(hour=9), cron_offset=CronOffsetEnum.BRASILIA)   # 09:00 BRT
async def other_digest() -> None:
    ...


@tq.cron(weekdays(hour=8, minute=30), cron_offset=CronOffsetEnum.BRASILIA)
async def morning_sync() -> None:
    ...


@tq.cron(CronEnum.EVERY_5_MINUTES)
async def heartbeat() -> None:
    ...
```

| Quero rodar… | Escreva |
| --- | --- |
| A cada 5 min | `CronEnum.EVERY_5_MINUTES` ou `every_n_minutes(5)` |
| Todo dia às 9h | `daily(hour=9)` |
| Dias úteis às 8h30 | `weekdays(hour=8, minute=30)` |
| Toda segunda | `weekly(WeekdayEnum.MON)` |
| Todo dia 1º | `monthly(day=1)` |
| No fuso de Brasília | `cron_offset=CronOffsetEnum.BRASILIA` |

`CronOffsetEnum` cobre os fusos do Brasil por nome — `BRASILIA` (-03:00),
`FERNANDO_DE_NORONHA` (-02:00), `MANAUS` (-04:00), `ACRE` (-05:00) — mais
`UTC`.

!!! info "Duas formas de fuso, e o que cada uma faz"
    O TaskIQ lê `cron_offset` de dois jeitos diferentes
    (`taskiq/cli/scheduler/run.py`): um `timedelta` é **somado** ao instante
    atual, e uma `str` vai para `ZoneInfo` como **chave IANA**. O SDK
    normaliza na declaração, então você escolhe pelo que precisa:

    | Você escreve | Chega ao scheduler como | Segue horário de verão? | Precisa de tz database? |
    | --- | --- | --- | --- |
    | `CronOffsetEnum.BRASILIA` | `timedelta(hours=-3)` | não | não |
    | `"-03:00"` | `timedelta(hours=-3)` | não | não |
    | `"America/Sao_Paulo"` | a chave, já validada | sim | **sim** |

    Para o Brasil o offset fixo é o certo: o horário de verão foi extinto
    depois de 2019 e, medido no tz database do sistema, `America/Sao_Paulo`
    fica em `-03:00` em todos os 252 meses de 2020 a 2040.

    Chave IANA precisa de um tz database na imagem. Sem ele até
    `ZoneInfo("UTC")` levanta `ZoneInfoNotFoundError`, então numa imagem
    slim instale o pacote `tzdata`.

!!! warning "String que não é fuso é recusada na declaração"
    `cron_offset="-0300"` (sem os dois-pontos) levanta `ValueError` na hora,
    nomeando as duas formas aceitas.

    A recusa existe porque o erro não era local antes. O loop do scheduler
    guarda cada tick com `except CronValueError`, então um
    `ZoneInfoNotFoundError` escapava e encerrava o `while True` — parando
    **todas** as tasks do processo, não só a que declarou o offset. Medido
    na 0.283.1: o loop terminava 0,363 s depois do startup e
    `health_check()` passava a devolver `False`. Recusar na declaração troca
    um serviço silenciosamente parado por um traceback que aponta a linha.

Prefere cron cru ou intervalos? Continua valendo:

```python
from datetime import timedelta

from src.tasks import tq


@tq.cron("*/5 * * * *")                        # string cron crua
async def raw_cron() -> None:
    ...


@tq.interval(seconds=30)                        # a cada 30s
async def poll_remote() -> None:
    ...


@tq.interval(timedelta(minutes=15))
async def warm_cache() -> None:
    ...
```

Em dev / processo único, rode o scheduler dentro do app:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.tasks import tq


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await tq.connect()
    await tq.start_scheduler()     # dev / single-process
    try:
        yield
    finally:
        await tq.stop_scheduler()
        await tq.disconnect()
```

!!! warning "O scheduler só enfileira — não executa"
    `cron`/`interval` **enfileiram** a tarefa no mesmo broker; um **worker** precisa estar rodando pra consumir. Sem worker, os disparos acumulam na fila.

!!! danger "Produção: um scheduler só"
    `start_scheduler()` roda dentro do processo do FastAPI — ok pra dev. Com múltiplos workers, cada réplica rodaria o próprio scheduler e **duplicaria** cada disparo. Em produção rode o scheduler standalone (um só) e os workers separados.
