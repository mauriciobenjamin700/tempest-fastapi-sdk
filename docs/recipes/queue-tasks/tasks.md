# Tarefas em background com `TaskQueue`

Use para tirar trabalho lento do request: enfileirar, enfileirar pelo nome, recursos do worker e tarefas em classe.

Parte da receita [Fila e Tarefas](../queue-tasks.md).

Uma **fila de tarefas** tira trabalho lento do request e joga num worker. O TaskIQ faz isso, mas espalha a API entre broker, scheduler, schedule source e `.kiq()`. `TaskQueue` dobra tudo num objeto só, com vocabulário óbvio.

Instale com `[tasks]` (puxa `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk import EmailUtils
from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

email = EmailUtils(**settings.email_kwargs())


tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)


@tq.task
async def send_welcome(to: str, name: str) -> None:
    """Roda num worker, fora do request."""
    await email.send(to, "Bem-vindo!", f"Olá, {name}.")
```

!!! note "`email` é o seu mailer"
    `email` aqui é o seu enviador de e-mail — uma instância de `EmailUtils`
    (extra `[email]`) montada no módulo. Veja a
    [receita de e-mail](../email.md); troque pela sua própria dependência de
    envio.

`@tq.task` devolve um objeto `Task` tipado com **duas** ações claras:

```python
import asyncio

from src.db.models import UserModel
from src.tasks import send_welcome

email = "ana@example.com"
user = UserModel(name="Ana", email=email)


async def main() -> None:
    """Run this example."""
    # Enfileira pro worker e volta na hora (a resposta HTTP não espera):
    await send_welcome.enqueue(to=user.email, name=user.name)

    # Roda inline, aqui mesmo, e devolve o valor real (útil em testes / reuso):
    await send_welcome.run(to="a@b.com", name="Ana")


asyncio.run(main())
```

!!! tip "`enqueue` no lugar de `.kiq`"
    `enqueue()` deixa claro o que acontece: a chamada vai pro worker. `run()` executa o corpo localmente, sem broker. O nome críptico `.kiq` fica escondido (mas continua acessível em `send_welcome.taskiq_task` se precisar).

Lifespan igual ao do broker de mensagens:

```python
# src/api/app.py

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.tasks import tq


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await tq.connect()
    try:
        yield
    finally:
        await tq.disconnect()
```

!!! note "Testes sem broker"
    `TaskQueue.memory()` usa o broker in-memory do TaskIQ: `enqueue()` roda a tarefa **na hora, no mesmo processo**. Zero worker, zero conexão. `run()` funciona sempre, mesmo sem `connect()`.

## Enfileirar pelo nome — `enqueue_by_name`

`send_welcome.enqueue(...)` exige importar o módulo da tarefa no processo da
API — e com ele tudo o que a tarefa importa (mailer, cliente HTTP, modelo
pesado). Quando a API só precisa **mandar** o trabalho, mande pelo nome: a API
fica com uma string, e o worker resolve.

```python
# src/tasks/welcome.py — importado só pelo worker
from tempest_fastapi_sdk.tasks import RetryPolicy, TaskQueue

from src.core.settings import settings

tq: TaskQueue = TaskQueue.from_settings(settings)

WELCOME_TASK_NAME: str = "users:send_welcome"
"""Nome estável da tarefa; a API enfileira por ele."""


@tq.task(name=WELCOME_TASK_NAME, retry=RetryPolicy(max_retries=3))
async def send_welcome(to: str, name: str) -> None:
    """Roda num worker, fora do request."""
```

```python
# src/api/routers/users.py — a API nunca importa src.tasks.welcome
from fastapi import APIRouter

from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

tq: TaskQueue = TaskQueue.from_settings(settings)
router: APIRouter = APIRouter(prefix="/users")


@router.post("/{email}/welcome", status_code=202)
async def welcome(email: str) -> None:
    """Pede o e-mail de boas-vindas e responde sem esperar."""
    await tq.enqueue_by_name("users:send_welcome", to=email, name="Ana")
```

**A política de retry continua valendo.** O worker do TaskIQ lê só os labels
que vêm na mensagem, e quem manda pelo nome não conhece a tarefa — então a
mensagem chega sem `retry_on_error`, `max_retries`, `timeout`. Todo
`TaskQueue` instala, primeiro no broker, um middleware que repõe no worker os
labels com que a tarefa foi registrada (os da mensagem ainda vencem). Medido
com Redis real, worker `taskiq` num processo e o envio em outro que não
importou a tarefa: uma tarefa com `RetryPolicy(max_retries=2)` que falha na
primeira tentativa roda **2** vezes; com o middleware removido, roda **1**.

!!! warning "Nome que não existe"
    Quem manda não confere o nome — o processo da API não conhece as
    tarefas. No Redis ou no RabbitMQ, o worker registra
    `task "<nome>" is not found` e descarta a mensagem. No
    `TaskQueue.memory()`, que é o próprio worker, o envio levanta
    `SendTaskError` na hora. Guarde o nome numa constante ao lado da tarefa
    (`*_TASK_NAME`) e use a mesma string na API.

## Recursos do worker — `on_startup` / `on_shutdown`

O `lifespan` do FastAPI **não roda no worker**. Sem ele, o processo do
`taskiq worker` não tem onde abrir o banco, o broker de mensagens ou um
cliente HTTP — e não tem onde fechá-los: funciona por acidente, na
conexão preguiçosa da primeira consulta, e nunca dispõe o pool no
encerramento.

Os hooks são esse lugar:

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import TaskQueue

from src.api.dependencies.resources import db
from src.core.settings import settings

tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)


@tq.on_startup
async def _open_resources() -> None:
    """Abre o banco quando o worker sobe."""
    await db.connect()


@tq.on_shutdown
async def _close_resources() -> None:
    """Dispõe o pool quando o worker encerra."""
    await db.disconnect()
```

Para o caso comum — recursos que já falam `connect` / `disconnect` — a
mesma coisa cabe numa linha:

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import TaskQueue

from src.api.dependencies.resources import broker, db
from src.core.settings import settings

tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL, resources=[db, broker])
```

`AsyncDatabaseManager`, `MessageBroker` e `AsyncMinIOClient` satisfazem o
protocolo `LifecycleResource`; qualquer objeto seu com os dois métodos
também serve. São abertos da esquerda para a direita e fechados da
direita para a esquerda, então quem depende de outro ainda o encontra
vivo ao fechar. Depois da construção, `tq.use(db)` faz o mesmo.

!!! info "Escopo: por padrão é o worker"
    O hook é registrado no `WORKER_STARTUP` do TaskIQ, então **não**
    dispara no processo web — que já tem o `lifespan` dele. Passe
    `scope="client"` (ou `"both"`) quando quiser o contrário:
    `@tq.on_startup(scope="both")`.

!!! tip "Testável sem worker"
    O broker in-memory roda os eventos dos **dois** lados no mesmo
    processo, então `TaskQueue.memory(resources=[db])` executa os hooks
    de worker em `connect()` / `disconnect()`. É como o teste dessa
    seção verifica a ordem de abertura e fechamento.

## Tarefas baseadas em classe

Simétrico aos consumidores: agrupe tarefas numa classe com `TaskDef`.
`tq.register(...)` devolve um `Task` (forma construtor) ou um dict de
`Task` por método (forma agrupada).

```python
import asyncio

from tempest_fastapi_sdk.tasks import TaskDef, task_method

from src.tasks import tq


# Forma construtor — uma tarefa; nome no construtor, sobrescreve run:
class NightlyReport(TaskDef):
    def __init__(self) -> None:
        super().__init__(name="reports:nightly")

    async def run(self, day: str) -> None:
        ...


nightly = tq.register(NightlyReport())        # -> Task


async def main() -> None:
    """Run this example."""
    await nightly.enqueue(day="2026-07-05")


    # Forma agrupada — várias tarefas, cada método marcado com @task_method:
    class ReportTasks(TaskDef):
        @task_method(name="reports:nightly")
        async def nightly(self, day: str) -> None: ...

        @task_method()
        async def weekly(self) -> None: ...


    tasks = tq.register(ReportTasks())            # -> {"nightly": Task, "weekly": Task}
    await tasks["nightly"].enqueue(day="2026-07-05")


asyncio.run(main())
```

O `@tq.task` (decorator em função) segue disponível — as duas formas
coexistem.
