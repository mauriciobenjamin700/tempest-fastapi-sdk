# Painel de tasks — o que está agendado e o que rodou

Use para ver no painel o que está agendado no broker e o que os workers já rodaram, ligando um `TaskPanelService`.

Parte da receita [Painel admin](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin.md).

`TaskPanelService` liga o admin ao que os workers já produzem: a **agenda**
declarada no broker e as **execuções** persistidas por um `JobStore`.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import TaskPanelService
from tempest_fastapi_sdk.tasks import JobStore, TaskQueue

from src.core.settings import settings
from src.db.models import JobModel, UserModel

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
tq: TaskQueue = TaskQueue.rabbitmq(settings.RABBITMQ_URL)
jobs: JobStore[JobModel] = JobStore(db, model=JobModel)

app.include_router(
    make_admin_router(
        AdminSite(title="Ops"),
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,
        tasks=TaskPanelService(queue=tq, job_store=jobs),
    )
)
```

Isso monta três rotas e um item "Tasks" na barra lateral:

| Rota | O que faz |
| --- | --- |
| `GET /admin/tasks` | execuções recentes (filtráveis por status) + agenda declarada |
| `GET /admin/tasks/{job_id}` | uma execução: progresso, estágio, tentativas, erro |
| `POST /admin/tasks/{job_id}/cancel` | pede parada, quando o status ainda permite |

!!! warning "O painel não mostra a fila do broker"
    Ele mostra o que está **persistido** (linhas de `JobStore`) e o que está
    **declarado** (registro do broker). Profundidade de fila não aparece
    porque a TaskIQ não expõe — e uma tela que insinuasse isso seria pior que
    tela nenhuma.

!!! tip "Qualquer uma das metades serve"
    Só `queue=` mostra a agenda; só `job_store=` mostra as execuções. A seção
    sem fonte não é renderizada, em vez de aparecer vazia e sugerir que não
    há nada para ver. Passar nenhuma das duas levanta `ValueError` na
    construção.

!!! info "A coluna `Schedule` carrega cada gatilho declarado"
    Uma task pode declarar vários gatilhos, e nem todo gatilho é cron: a
    TaskIQ aceita `cron`, `interval` e `time` (disparo único) na mesma lista.
    A coluna mostra uma linha por gatilho, com o fuso ao lado da expressão
    quando a task declarou um:

    | Declarado | Aparece como |
    | --- | --- |
    | `@tq.cron(daily(hour=2), cron_offset=CronOffsetEnum.BRASILIA)` | `0 2 * * *` `-03:00` |
    | `@tq.cron(daily(hour=2))` | `0 2 * * *` |
    | `@tq.interval(timedelta(seconds=30))` | `every 30s` |
    | `schedule=[{"time": quando}]` | `once at 2026-01-01 12:00:00+00:00` |
    | `@tq.task` | `on demand` |

    Mostrar o fuso não é enfeite. Uma expressão cron **parece completa** sem
    ele, então nada convida o leitor a conferir — e `0 2 * * *` com
    `cron_offset=CronOffsetEnum.BRASILIA` dispara às 05:00 UTC. Quem abre o
    painel para saber quando o backup roda erraria por três horas, num lugar
    onde a única outra fonte é o código.

    O painel mostra o que foi **declarado**, não o default da TaskIQ:
    expressão sem fuso aparece sem fuso, não como `(UTC)`.

!!! tip "Renderizando a agenda por conta própria"
    `ScheduledTask.triggers` é a tupla de `TaskTrigger`, na ordem do
    registro, e `TaskTrigger.cron_offset_label` formata o fuso
    (`"-03:00"` para offset fixo, a chave IANA para zona nomeada). Os campos
    planos — `cron`, `interval_seconds`, `cron_offset`, `run_at` — resumem o
    primeiro gatilho de cada tipo, para quem só precisa de um deles. Chave de
    schedule que o SDK ainda não modela fica em `TaskTrigger.extra` em vez de
    ser descartada.

!!! note "Por que não existe coluna \"próxima execução\""
    O `pycron`, que chega junto com a TaskIQ, expõe `is_now` e `has_been` — e
    nenhum `next()`. Calcular a próxima execução exigiria varrer minuto a
    minuto a cada render (até ~44 mil iterações para um cron mensal) ou
    adicionar dependência nova por uma coluna. O painel mostra a expressão —
    com o fuso ao lado, porque sem ele a expressão não é legível.

Para a lista com paginação, filtro e export que o admin já sabe fazer,
registre também o model de jobs — simétrico ao painel de dead-letter:

```python
from tempest_fastapi_sdk import AdminSite
from tempest_fastapi_sdk.tasks import make_job_admin_model

from src.db.models import JobModel

site: AdminSite = AdminSite(title="Ops")
site.register(make_job_admin_model(JobModel))
```
