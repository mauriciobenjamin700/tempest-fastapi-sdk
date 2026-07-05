# Fila e Tarefas

Trabalho em background sem dor. O SDK envelopa o **FastStream** (mensageria) e o **TaskIQ** (tarefas + agendamento) em classes tipadas com um vocabulário único — você **nunca importa** `faststream` nem `taskiq` no código da aplicação.

!!! tip "Qual ferramenta usar?"
    - **`MessageBroker`** (mensageria) — evento acontece, **vários** serviços/consumidores reagem. Fan-out, at-least-once, desacoplado do request. Ex.: "pedido pago" → estoque, e-mail, analytics.
    - **`TaskQueue`** (tarefas) — tirar trabalho lento **de um** handler de request pra um worker, mantendo a resposta HTTP rápida. Ex.: enviar e-mail, gerar PDF.
    - **`TaskQueue.cron` / `.interval`** (agendamento) — disparos periódicos.
    - **Outbox** — quando publicar *precisa* ser atômico com o `INSERT` no banco.

Todas as classes seguem o mesmo ciclo de vida: `connect()` / `disconnect()` / `lifespan()` / `health_check()` / `is_connected`, e expõem o objeto cru por baixo (`.broker`) como escape hatch.

## Mensageria — `MessageBroker`

O problema que o FastStream resolve mal: a API muda de forma conforme o transporte. Você assina com `@broker.subscriber("q")` e publica com `broker.publish(msg, queue="q")` no RabbitMQ, `topic=` no Kafka, `subject=` no NATS. Confuso e não-portável.

`MessageBroker` esconde isso atrás de **um** conceito: um **channel** (uma string). Você publica num channel e quem estiver inscrito nele recebe.

Instale com `[queue]` (puxa `faststream[rabbit]`).

```python
# src/queue/__init__.py
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import MessageBroker

from src.core.settings import settings


# Escolha o transporte por um construtor — sem importar faststream.
mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)


class OrderPaid(BaseModel):
    order_id: str
    user_id: str


@mq.on("orders.paid")
async def handle_order_paid(event: OrderPaid) -> None:
    """Recebe cada evento publicado no channel 'orders.paid'."""
    await mark_order_paid(event.order_id, event.user_id)
```

Repare no `event: OrderPaid`: **a anotação de tipo dirige a decodificação**. O FastStream valida o payload recebido nesse modelo Pydantic **antes** do seu handler rodar — mensagem malformada nunca chega no seu código.

Ligue o ciclo de vida no lifespan do FastAPI e publique de qualquer lugar:

```python
# src/api/app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.queue import mq, OrderPaid


@asynccontextmanager
async def lifespan(_: FastAPI):
    await mq.connect()
    try:
        yield
    finally:
        await mq.disconnect()


app = FastAPI(lifespan=lifespan)


# De qualquer service/handler — channel primeiro, mensagem depois:
await mq.publish("orders.paid", OrderPaid(order_id="abc", user_id="u1"))
```

!!! info "Transportes"
    `MessageBroker.rabbitmq(url)`, `.redis(url)`, `.kafka(*servers)`, `.nats(servers)`. Cada um faz lazy-import do backend certo do FastStream e erra com a mensagem de instalação exata se o extra faltar. Precisa injetar um broker customizado (ou de teste)? `MessageBroker(meu_broker)`.

!!! check "Recapitulando"
    - `MessageBroker.rabbitmq(url)` — escolhe o transporte, esconde o FastStream.
    - `@mq.on("channel")` — declara um consumidor; o tipo do parâmetro valida a mensagem.
    - `await mq.publish("channel", modelo)` — publica; channel primeiro.
    - `mq.publish(...)` só funciona depois de `connect()` (levanta `RuntimeError` antes).

Conecte no health router: `make_health_router(checks={"queue": mq.health_check})`.

## Tarefas em background — `TaskQueue`

Uma **fila de tarefas** tira trabalho lento do request e joga num worker. O TaskIQ faz isso, mas espalha a API entre broker, scheduler, schedule source e `.kiq()`. `TaskQueue` dobra tudo num objeto só, com vocabulário óbvio.

Instale com `[tasks]` (puxa `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py
from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings


tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)


@tq.task
async def send_welcome(to: str, name: str) -> None:
    """Roda num worker, fora do request."""
    await email.send(to, "Bem-vindo!", f"Olá, {name}.")
```

`@tq.task` devolve um objeto `Task` tipado com **duas** ações claras:

```python
# Enfileira pro worker e volta na hora (a resposta HTTP não espera):
await send_welcome.enqueue(to=user.email, name=user.name)

# Roda inline, aqui mesmo, e devolve o valor real (útil em testes / reuso):
await send_welcome.run(to="a@b.com", name="Ana")
```

!!! tip "`enqueue` no lugar de `.kiq`"
    `enqueue()` deixa claro o que acontece: a chamada vai pro worker. `run()` executa o corpo localmente, sem broker. O nome críptico `.kiq` fica escondido (mas continua acessível em `send_welcome.taskiq_task` se precisar).

Lifespan igual ao do broker de mensagens:

```python
# src/api/app.py
@asynccontextmanager
async def lifespan(_: FastAPI):
    await tq.connect()
    try:
        yield
    finally:
        await tq.disconnect()
```

!!! note "Testes sem broker"
    `TaskQueue.memory()` usa o broker in-memory do TaskIQ: `enqueue()` roda a tarefa **na hora, no mesmo processo**. Zero worker, zero conexão. `run()` funciona sempre, mesmo sem `connect()`.

## Tarefas periódicas — `cron` / `interval`

Agendar é parte do mesmo `TaskQueue` — sem scheduler separado no seu código:

```python
# src/tasks/__init__.py
from datetime import timedelta


@tq.cron("*/5 * * * *")                       # a cada 5 minutos
async def heartbeat() -> None:
    ...


@tq.cron("0 9 * * MON-FRI", cron_offset="-03:00")  # 09:00 BRT, dias úteis
async def daily_digest() -> None:
    ...


@tq.interval(seconds=30)                       # a cada 30s
async def poll_remote() -> None:
    ...


@tq.interval(timedelta(minutes=15))
async def warm_cache() -> None:
    ...
```

Em dev / processo único, rode o scheduler dentro do app:

```python
@asynccontextmanager
async def lifespan(_: FastAPI):
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

## Workers em produção

O worker e o scheduler são processos separados apontando pros objetos crus expostos pelo `TaskQueue`:

```bash
# consome e executa as tarefas
taskiq worker    src.tasks:tq.broker

# um único processo de scheduler pro cluster inteiro
taskiq scheduler src.tasks:tq.scheduler
```

`tq.broker` é o broker TaskIQ (conhece todas as tarefas registradas); `tq.scheduler` é o `TaskiqScheduler` interno.

## Outbox transacional

Quando um handler **escreve uma linha E publica um evento**, fazer os dois separados é inseguro: um crash entre o commit e o publish perde o evento; entre o publish e o commit cria um evento fantasma. O padrão outbox grava a linha de negócio **e** uma linha de outbox na **mesma transação** — ou as duas comitam, ou nenhuma. Um relay lê o outbox e publica no broker depois.

!!! check "O SDK já traz o primitivo"
    Diferente do que dizia a versão antiga desta página, o outbox **é** um primitivo do SDK: `BaseOutboxModel` (a tabela), `OutboxRelay` (o worker que drena e publica, com backoff exponencial e `FOR UPDATE SKIP LOCKED` no Postgres) e `BaseRepository.save_with_outbox` (o lado escritor). O relay recebe um `publish` async qualquer — encaixa direto no `MessageBroker`:

```python
# src/tasks/__init__.py — relay do outbox
from tempest_fastapi_sdk import OutboxRelay

from src.db.models import OutboxModel
from src.queue import mq          # MessageBroker
from src.core.resources import db  # AsyncDatabaseManager


relay = OutboxRelay(
    db,
    model=OutboxModel,
    # channel primeiro, payload depois — a mesma assinatura do publish:
    publish=lambda event: mq.publish(event.topic, event.payload),
)

# No lifespan (ou como processo dedicado): drena até ser cancelado.
# asyncio.create_task(relay.run(poll_interval=1.0))
```

O guia completo — modelo, service produtor com `save_with_outbox`, retenção e concorrência — está na receita dedicada em **[Outbox](outbox.md)**.

## Recap / próximos passos

- **`MessageBroker`** — pub/sub tipado e transport-agnostic sobre FastStream: `@mq.on("channel")` + `await mq.publish("channel", modelo)`. Fan-out at-least-once entre serviços.
- **`TaskQueue`** — tarefas sobre TaskIQ: `@tq.task` → `await task.enqueue(...)` (pro worker) ou `await task.run(...)` (inline). `.memory()` pra testes.
- **`@tq.cron` / `@tq.interval`** — periódicos no mesmo objeto; `start_scheduler()` em dev, CLI standalone em produção.
- **Outbox** — `BaseOutboxModel` + `OutboxRelay` + `save_with_outbox`, com o `publish` do relay apontando pro `MessageBroker`. Veja [Outbox](outbox.md).
- As classes legadas `AsyncBrokerManager` / `AsyncTaskBrokerManager` / `AsyncTaskScheduler` continuam funcionando, mas os facades acima são o caminho recomendado.
