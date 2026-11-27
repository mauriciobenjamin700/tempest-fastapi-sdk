# Confiabilidade e observabilidade das tarefas

Use para ligar retry tipado, dead-letter, métricas Prometheus por tarefa e o painel de dead-letter no admin.

Parte da receita [Fila e Tarefas](../queue-tasks.md).

Tarefa que só roda no worker precisa de três coisas que o TaskIQ tem, mas espalhadas: **retry**, **dead-letter** e **métricas**. O `TaskQueue` expõe as três como middleware opt-in — chame **antes do `connect()`**, e nada toca a API de middleware do broker.

## Retry tipado

`RetryPolicy` carrega a config de retry como labels; `enable_retries()` instala o middleware do TaskIQ que as lê:

```python
from tempest_fastapi_sdk.tasks import RetryPolicy, TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.enable_retries(default_max_retries=3)


@tq.task(name="reports:nightly", retry=RetryPolicy(max_retries=5))
async def nightly() -> None:
    ...   # re-executada até 5x em caso de erro
```

## Dead-letter — pra onde vão as falhas terminais

Quando uma tarefa falha **sem retry configurado**, ou depois de esgotar os retries, a chamada vai pro seu `DeadLetterSink` exatamente uma vez. O destino é seu — um canal do `MessageBroker`, uma linha no banco, um alerta. O SDK não assume backend:

```python
from tempest_fastapi_sdk.tasks import DeadLetter, TaskQueue

from src.queue import mq


tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")


async def to_dlq(dead_letter: DeadLetter) -> None:
    await mq.publish("tasks.dead", {
        "task": dead_letter.task_name,
        "args": dead_letter.args,
        "error": str(dead_letter.exception),
        "retries": dead_letter.retries,
    })


tq.dead_letter(to_dlq, default_max_retries=3)
```

Qualquer função `async` que receba um `DeadLetter` serve de sink, com o nome de parâmetro que você quiser — `async def to_dlq(letter: DeadLetter)` passa no mypy e no basedpyright do mesmo jeito.

!!! tip "Combine com o retry"
    Passe o **mesmo** `default_max_retries` pro `enable_retries` e pro `dead_letter`: assim o ponto de "retries esgotados" bate pras tarefas que não definem `max_retries` próprio. A ordem de instalação não importa — o dead-letter decide sozinho lendo as labels da mensagem.

## Métricas Prometheus por tarefa

`TaskMetrics` conta execuções (por status) e histograma de duração, rotulados por tarefa, no **mesmo** `/metrics` do SDK (passe o `registry` compartilhado):

```python
from tempest_fastapi_sdk.tasks import TaskMetrics, TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.enable_metrics(TaskMetrics())   # tasks_runs_total{task,status} + tasks_duration_seconds{task}
```

## Painel de dead-letter no admin

O `DeadLetterSink` diz *o que* fazer com a falha; pra **ver e reprocessar** as falhas, persista-as numa tabela e mostre no admin. `DbDeadLetterSink` grava cada falha terminal; `make_dead_letter_admin_model` monta um `AdminModel` read-mostly (filtra por task, busca no erro, exporta) com uma ação em massa **requeue** opcional.

```python
from tempest_fastapi_sdk.admin import AdminSite
from tempest_fastapi_sdk.tasks import (
    DbDeadLetterSink,
    TaskQueue,
    make_dead_letter_admin_model,
    make_dead_letter_model,
)

from src.core.resources import db   # AsyncDatabaseManager


DeadLetterModel = make_dead_letter_model()   # ou herde BaseDeadLetterModel na mão

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.dead_letter(DbDeadLetterSink(db, DeadLetterModel))   # persiste falhas terminais

site: AdminSite = AdminSite(title="Ops")
site.register(make_dead_letter_admin_model(DeadLetterModel, tq=tq))   # painel + requeue
```

O `tq=` liga a ação **requeue**: o operador seleciona linhas, reenfileira cada chamada com os `args`/`kwargs` guardados e as linhas reprocessadas são apagadas.

!!! info "Sem clonar o Flower"
    O TaskIQ não expõe estado vivo da fila (o Flower é específico do Celery), então este painel **não** tenta mostrar jobs pendentes/em execução. Ele mostra o que é real e persistido: as falhas terminais.

Pra um inventário "quais tasks existem", `task_inventory(tq)` devolve `list[TaskInfo]` (nome / schedule / retry) lido direto do broker — sirva como JSON, log, ou sua própria página:

```python
from tempest_fastapi_sdk.tasks import task_inventory

from src.tasks import tq


for info in task_inventory(tq):
    print(info.name, info.schedule, info.retry_on_error, info.max_retries)
```
