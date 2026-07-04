# Fila e Tarefas

Trabalho em background — filas de mensagens at-least-once (FastStream/RabbitMQ), filas de tarefas (TaskIQ), schedulers periódicos e o padrão de outbox transacional.

## Filas de mensagens — FastStream


`AsyncBrokerManager` embrulha qualquer broker FastStream (RabbitMQ, Kafka, NATS, Redis Streams) com uma superfície uniforme de connect/disconnect/health-check. A instância do broker é injetada para que o SDK não fixe um único transporte.

Instale com `[queue]` (puxa `faststream[rabbit]`). Escolha o extra FastStream correspondente para outros transportes.

```python
# src/queue/__init__.py
from faststream.rabbit import RabbitBroker
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import AsyncBrokerManager

from src.core.settings import settings


broker = RabbitBroker(settings.RABBITMQ_URL)
queue = AsyncBrokerManager(broker)


class OrderMessage(BaseModel):
    order_id: str
    user_id: str


@broker.subscriber("orders.paid")
async def handle_order_paid(msg: OrderMessage) -> None:
    await mark_order_paid(msg.order_id, msg.user_id)


# src/api/app.py lifespan
await queue.connect()
...
await queue.disconnect()


# Publique de qualquer lugar da aplicação
await queue.publish(OrderMessage(order_id="abc", user_id="x"), queue="orders.paid")
```

O manager expõe:

- `connect()` / `disconnect()` — idempotentes; seguros de chamar a partir do lifespan do FastAPI.
- `publish(message, *args, **kwargs)` — passthrough para `broker.publish` com uma guarda `RuntimeError` quando o broker não está iniciado.
- `lifespan()` — context manager async que lida com start/stop, útil para scripts curtos.
- `broker_dependency` — `Depends` do FastAPI que entrega o broker ativo.
- `health_check()` / `is_connected` — verdadeiro enquanto o broker está iniciado.

Conecte-o no health router com `make_health_router(checks={"queue": queue.health_check})`.


## Tarefas em background — TaskIQ


`AsyncTaskBrokerManager` embrulha qualquer broker TaskIQ (AioPika para RabbitMQ, Redis, in-memory para testes). Instale com `[tasks]` (puxa `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py
from taskiq_aio_pika import AioPikaBroker

from tempest_fastapi_sdk.tasks import AsyncTaskBrokerManager

from src.core.settings import settings


tasks = AsyncTaskBrokerManager(AioPikaBroker(settings.TASKIQ_BROKER_URL))


@tasks.task
async def send_welcome_email(to: str, name: str) -> None:
    await email_utils.send(
        to=to,
        subject="Bem-vindo!",
        body=f"Olá, {name} — sua conta foi criada.",
    )


# src/api/app.py lifespan
await tasks.connect()
...
await tasks.disconnect()


# Enfileire de um handler de request
await send_welcome_email.kiq(to=user.email, name=user.name)
```

`register_task(callable, task_name=..., **kwargs)` registra uma função sem a sintaxe de decorator — útil ao conectar callables de terceiros que você não pode decorar no ponto de definição. Para testes, troque o broker por `taskiq.InMemoryBroker()` para que as tarefas executem de forma síncrona.

As mesmas guarda de lifespan do manager de fila se aplicam: `connect()`/`disconnect()`/`lifespan()`/`broker_dependency`/`health_check()`/`is_connected`.


## Scheduler de tarefas periódicas


`AsyncTaskScheduler` embrulha `taskiq.TaskiqScheduler` + `LabelScheduleSource` para que tarefas periódicas sejam declaradas com decorators ao lado de tarefas normais e o scheduler seja dirigido pelo lifespan do FastAPI. Requer o extra `[tasks]`.

!!! warning "O scheduler só enfileira — não executa"
    O `AsyncTaskScheduler` **não executa os corpos das tarefas** — ele as enfileira no mesmo broker que o `AsyncTaskBrokerManager` embrulha, então um processo worker precisa estar rodando para consumi-las. Sem um worker ativo, os disparos periódicos acumulam na fila sem nunca rodar.

```python
# src/tasks/__init__.py
from datetime import timedelta

from taskiq_aio_pika import AioPikaBroker

from tempest_fastapi_sdk.tasks import AsyncTaskBrokerManager, AsyncTaskScheduler

from src.core.settings import settings


# Use TASKIQ_BROKER_URL (de TaskIQSettings) quando o scheduler /
# broker de tarefas for um broker diferente da fila FastStream
# (RABBITMQ_URL). Reutilize a mesma URL do RabbitMQ quando
# compartilharem o broker — ambas env vars podem apontar pro mesmo valor.
broker = AioPikaBroker(settings.TASKIQ_BROKER_URL)
tasks = AsyncTaskBrokerManager(broker)
scheduler = AsyncTaskScheduler(broker)


@tasks.task
async def reconcile_invoices(batch_size: int = 100) -> None:
    """Background task — kicked by handlers or the scheduler."""
    ...


@scheduler.cron("*/5 * * * *")          # every five minutes
async def heartbeat() -> None:
    """Liveness ping written to the audit log."""
    ...


@scheduler.cron("0 9 * * MON-FRI", cron_offset="-03:00")  # 09:00 BRT, weekdays
async def daily_digest() -> None:
    ...


@scheduler.interval(seconds=30)         # every 30s
async def poll_remote_queue() -> None:
    ...


@scheduler.interval(timedelta(minutes=15))
async def warm_cache() -> None:
    ...
```

Conecte-o ao lifespan do app, ao lado do manager de broker:

```python
# src/api/app.py
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await tasks.connect()
    await scheduler.connect()
    await scheduler.run_in_background()   # dev / single-process services
    try:
        yield
    finally:
        await scheduler.disconnect()
        await tasks.disconnect()
```

!!! warning "Somente dev"
    `run_in_background()` roda o scheduler dentro do processo do FastAPI — ok para desenvolvimento e serviços de processo único. Em produção com múltiplos workers, cada réplica rodaria o próprio scheduler e duplicaria os disparos; use a CLI standalone descrita abaixo em vez disso.

Superfície de decorators:

| Método | Quando usar |
| --- | --- |
| `@scheduler.cron("*/5 * * * *", cron_offset=None)` | Expressão cron; passe `cron_offset` (string como `"-03:00"` ou `timedelta`) para ancorar a um timezone diferente de UTC. |
| `@scheduler.interval(seconds=30)` / `@scheduler.interval(timedelta(...))` | Recorrência em intervalo fixo. |
| `@scheduler.schedule([{...}, {...}])` | Lista crua de schedule do TaskIQ — combine triggers, use `time` de uma vez só, etc. |
| `scheduler.register(func, schedule=[...], task_name=...)` | Registro sem sintaxe de decorator (callables de terceiros). |

Deploys de produção com múltiplos workers devem rodar a CLI standalone do scheduler em vez de `run_in_background()`, para que só um scheduler esteja ativo no cluster:

```bash
taskiq scheduler src.tasks:scheduler.scheduler
```

(`scheduler.scheduler` é a instância interna `TaskiqScheduler` exposta em `AsyncTaskScheduler`.) O processo worker continua o mesmo:

```bash
taskiq worker src.tasks:tasks.broker
```

Os controles de ciclo de vida espelham o manager de broker: `connect()` / `disconnect()` / `lifespan()` / `run_in_background()` / `health_check()` / `is_connected`.


## Padrão outbox dispatcher


O padrão de outbox transacional mantém uma tabela "a publicar" no mesmo banco das linhas de domínio, para que escrever a linha e registrar o efeito colateral aconteçam em uma única transação. Um worker lê o outbox em ordem e publica no RabbitMQ (FastStream) / TaskIQ, marcando cada linha como despachada só depois que o broker dá ACK. Crashes entre o commit e o publish reproduzem com segurança no próximo poll.

O SDK **não** traz um primitivo dedicado `OutboxDispatcher` — a implementação é curta, opinativa e se beneficia de ficar na fronteira `db/models/` + `tasks/` do serviço. Use a receita abaixo.

```python
# src/db/models/outbox.py
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class OutboxEventModel(BaseModel):
    """One row per domain event waiting to be published."""

    topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        index=True,
    )
    # is_active / created_at / updated_at come from BaseModel.
```

```python
# src/db/repositories/outbox.py
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository

from src.db.models import OutboxEventModel


class OutboxRepository(BaseRepository[OutboxEventModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=OutboxEventModel)

    async def claim_pending(self, *, limit: int = 100) -> list[OutboxEventModel]:
        """Lock-free claim — fine for single-worker dispatcher."""
        stmt = (
            select(OutboxEventModel)
            .where(OutboxEventModel.status == "pending")
            .order_by(OutboxEventModel.created_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_dispatched(self, ids: list[str]) -> None:
        await self.session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id.in_(ids))
            .values(status="dispatched"),
        )
        await self.session.commit()
```

```python
# src/services/orders.py — lado produtor
from src.db.models import OrderModel, OutboxEventModel


class OrderService:
    async def place_order(self, data: OrderCreateSchema) -> OrderResponseSchema:
        order = OrderModel(**data.to_dict())
        self.repo.session.add(order)
        # Same transaction as the order row.
        self.repo.session.add(
            OutboxEventModel(
                topic="orders.placed",
                payload={"order_id": str(order.id), "amount": order.amount},
            ),
        )
        await self.repo.session.flush()
        await self.repo.session.commit()
        return self.repo.map_to_response(order)
```

```python
# src/tasks/__init__.py — lado dispatcher
from tempest_fastapi_sdk.tasks import AsyncTaskScheduler

from src.api.app import broker as queue_broker  # FastStream AsyncBrokerManager
from src.api.app import db, taskiq_broker

scheduler = AsyncTaskScheduler(taskiq_broker)


@scheduler.interval(seconds=5)
async def dispatch_outbox() -> None:
    """Poll the outbox and publish each pending event."""
    async with db.get_session_context() as session:
        repo = OutboxRepository(session)
        events = await repo.claim_pending(limit=100)
        if not events:
            return
        dispatched: list[str] = []
        for event in events:
            try:
                await queue_broker.publish(event.payload, event.topic)
                dispatched.append(str(event.id))
            except Exception:  # noqa: BLE001 — retry on next tick
                continue
        if dispatched:
            await repo.mark_dispatched(dispatched)
```

Trade-offs para ter em mente:

- **A ordem é best-effort.** Quando um lote contém um publish que falha, todo evento posterior no mesmo lote ainda roda — mas eles continuam sendo publicados em ordem de `created_at`. Se a ordem estrita importa, pare na primeira falha.
- **Retenção.** Adicione um job periódico no estilo `TRUNCATE` para apagar linhas `dispatched` mais antigas que N dias, senão a tabela de outbox cresce sem limite.
- **At-least-once.** Os consumidores devem ser idempotentes — o dispatcher pode crashar depois de publicar mas antes do `mark_dispatched`.

!!! danger "Dispatcher único — rodar vários publica em duplicidade"
    O `claim_pending` ingênuo não trava linhas: dois workers dispatcher rodando ao mesmo tempo reivindicam o mesmo lote e **publicam cada evento em duplicidade**. Mantenha exatamente um processo dispatcher, ou use `SELECT ... FOR UPDATE SKIP LOCKED` no PostgreSQL antes de escalar horizontalmente.

## Recap / próximos passos

Escolha a ferramenta pelo que você precisa garantir:

- **Fila de mensagens (`AsyncBrokerManager`)** — fan-out event-driven entre serviços/consumidores via FastStream; entrega at-least-once, sem acoplamento com o request.
- **Fila de tarefas (`AsyncTaskBrokerManager`)** — descarregar trabalho de um handler de request para um worker (`.kiq(...)`), mantendo a resposta HTTP rápida.
- **Scheduler (`AsyncTaskScheduler`)** — disparos periódicos (cron/interval); lembre que ele só enfileira — um worker precisa consumir.
- **Outbox dispatcher** — quando publicar *precisa* ser atômico com a escrita no banco (sem eventos fantasma nem perdidos em um crash). Para o modelo, o service produtor e a estratégia de retenção completos, veja a receita dedicada em [Outbox](outbox.md).
