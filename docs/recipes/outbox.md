# Outbox transacional (eventos confiáveis)

Quando um handler **grava uma linha** e **publica um evento**, fazer as
duas coisas como operações independentes é frágil: se o processo morre
*depois* do commit mas *antes* do publish, o evento some; se morre depois
do publish mas antes do commit, sobra um evento fantasma apontando pra uma
linha que nunca existiu. Isso é o **dual-write problem**.

O padrão **outbox** resolve: grave a linha de negócio **e** uma linha
`outbox` na **mesma transação**. Ou as duas comitam, ou nenhuma. Um
*relay* separado lê as linhas pendentes e publica no broker, marcando cada
uma como enviada. O broker pode ficar minutos fora do ar — os eventos
esperam, duráveis, na tabela.

!!! info "Onde isso encaixa"
    Complementa o [`AsyncBrokerManager`](queue-tasks.md): o broker
    *publica*, o outbox *garante* que o evento existe pra ser publicado. O
    relay usa qualquer callable de publish — então funciona com FastStream,
    webhook, o que for.

## 1. A tabela outbox

`BaseOutboxModel` é abstrata — o projeto cria a tabela concreta (igual a
`BaseUserModel`):

```python
from tempest_fastapi_sdk import BaseOutboxModel


class OutboxModel(BaseOutboxModel):
    """Tabela de eventos pendentes do serviço."""

    __tablename__ = "outbox"
```

Ela já traz `topic`, `payload` (JSON), `status`, `attempts`,
`max_attempts`, `available_at`, `sent_at` e `last_error` — além das quatro
colunas canônicas do `BaseModel` (`id` / `is_active` / `created_at` /
`updated_at`). Gere a migration com o [`AlembicHelper`](database.md) como
qualquer outra tabela.

## 2. Gravar de forma atômica

No service/repository, use `save_with_outbox` em vez de `add`: ele insere o
modelo de negócio **e** o evento numa transação só.

```python
from tempest_fastapi_sdk import BaseRepository

from src.db.models import OrderModel, OutboxModel


async def place_order(repo: BaseRepository[OrderModel], data: dict[str, object]) -> OrderModel:
    """Cria o pedido e enfileira o evento na mesma transação."""
    order = OrderModel(**data)
    event = OutboxModel.new_event("orders.created", {"order": data})
    return await repo.save_with_outbox(order, event)
```

Se o `commit` falhar (ex.: constraint única), **as duas** linhas são
revertidas — nunca sobra evento órfão.

## 3. Drenar e publicar (o relay)

`OutboxRelay` lê as linhas pendentes e chama o seu callable de publish. Ele
não importa nenhum broker específico — você passa a função:

```python
import asyncio

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseOutboxModel, OutboxRelay

from src.db.models import OutboxModel


async def run_relay(db: AsyncDatabaseManager, broker: object) -> None:
    """Publica eventos pendentes continuamente."""

    async def publish(event: BaseOutboxModel) -> None:
        """Encaminha um evento pro broker."""
        await broker.publish(event.payload, event.topic)  # type: ignore[attr-defined]

    relay: OutboxRelay = OutboxRelay(db, model=OutboxModel, publish=publish)
    await relay.run(poll_interval=1.0)  # loop até a task ser cancelada
```

Rode o relay como um processo/worker separado (ou uma task no lifespan).
Cada evento publicado vira `status="sent"` com `sent_at` preenchido.

### Falhas e retry

Se o `publish` levanta exceção, o relay **não** marca como enviado: ele
incrementa `attempts`, guarda o erro em `last_error` e reagenda o evento
com backoff exponencial (`available_at` no futuro). Quando `attempts`
chega em `max_attempts`, a linha vira `status="failed"` e fica na tabela
pra inspeção manual (nunca mais é retentada automaticamente).

!!! tip "Múltiplos workers"
    Em PostgreSQL/MySQL o relay trava o lote com `FOR UPDATE SKIP LOCKED`,
    então você pode rodar **vários** workers de relay sem publicar o mesmo
    evento duas vezes. Em SQLite (sem lock de linha) ele cai pra um
    `SELECT` simples — use um worker só.

### Drenar uma vez (testes / cron)

Pra cenários sem loop (um teste, um cron job), chame `drain_once()`, que
devolve quantos eventos foram publicados:

```python
published: int = await relay.drain_once()
```

## Recap

- `BaseOutboxModel` → tabela concreta `OutboxModel(__tablename__="outbox")`.
- `repo.save_with_outbox(model, event)` grava negócio + evento **atômico**.
- `OutboxRelay(db, model=..., publish=...).run()` publica os pendentes,
  com retry/backoff e marcação `sent` / `failed`.
- `OutboxModel.new_event(topic, payload)` monta o evento; `drain_once()`
  drena um lote pra testes/cron.
