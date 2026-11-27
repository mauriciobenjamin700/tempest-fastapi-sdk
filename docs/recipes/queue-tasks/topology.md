# Topologia da fila com `QueueSpec`

Use quando a fila precisa sobreviver a restart, ter fan-out, dead-letter reprocessável e exchange declarado.

Parte da receita [Fila e Tarefas](../queue-tasks.md).

O canal como string resolve a maioria dos casos. O que ele **não** expressa é justamente o que decide se a fila sobrevive a um restart, para onde vai uma mensagem rejeitada e quanto tempo ela vive. No RabbitMQ isso mora na declaração da fila, não no nome.

`QueueSpec` carrega essa topologia como dado tipado, e é aceito em qualquer lugar onde a string era:

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import DeadLetterSpec, MessageBroker, QueueSpec, QueueTypeEnum

mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")


class OrderPaid(BaseModel):
    order_id: str
    amount_cents: int

ORDERS_PAID = QueueSpec(
    name="orders.paid",
    dead_letter=DeadLetterSpec(exchange="dlx"),
    message_ttl_ms=60_000,
    queue_type=QueueTypeEnum.QUORUM,
)


@mq.on(ORDERS_PAID)
async def handle(event: OrderPaid) -> None:
    """Consome de uma fila durável, quorum, com dead-letter."""
```

Traduz para os argumentos que o AMQP espera:

```text
{"x-queue-type": "quorum", "x-dead-letter-exchange": "dlx", "x-message-ttl": 60000}
```

!!! danger "Sem `dead_letter`, falha é descarte silencioso"
    A política do consumidor é `REJECT_ON_ERROR`: handler que levanta faz `basic.reject` com `requeue=False`. Isso evita poison message em loop — mas **sem `x-dead-letter-exchange` o RabbitMQ joga a mensagem fora**. Sem erro, sem fila morta, sem métrica. É o motivo de `DeadLetterSpec` existir.

!!! check "Sem `AckPolicy` no seu código"
    O consumidor de um `QueueSpec` com dead-letter já sai com
    `ack_policy=REJECT_ON_ERROR` fixado pelo `on()` (e pelo `register()` do
    `Consumer`): não importe `faststream.AckPolicy` para isso. O valor é fixado
    no próprio assinante, então nem um `ack_policy` no broker inteiro desvia a
    falha da DLQ — medido em `tests/queue/test_fanout_live.py` com o broker em
    `NACK_ON_ERROR`. Passar `ACK`, `ACK_FIRST` ou `NACK_ON_ERROR` num spec com
    dead-letter levanta `ValueError`: nenhuma das três manda a falha para a DLQ
    (as duas primeiras confirmam a mensagem, a última a reenfileira em laço).
    `MANUAL` continua aceito.

## O exchange precisa existir

O RabbitMQ aceita declarar uma fila apontando para um `x-dead-letter-exchange` que não existe — e aí descarta no roteamento, em silêncio. Exchange que existe **sem fila ligada** descarta igual: o roteamento não acha destino. Por isso o `connect()` declara os exchanges nomeados pelos `QueueSpec` registrados (como `topic` durável) e, quando o `DeadLetterSpec` nomeia uma `queue`, essa fila e o binding dela — tudo **antes** de os consumidores subirem:

```python
from tempest_fastapi_sdk.queue import MessageBroker


async def startup(mq: MessageBroker) -> None:
    """Sobe o broker; os DLX dos specs registrados são declarados aqui."""
    await mq.connect()
```

Onde o broker é gerenciado e a aplicação não tem permissão de declarar, desligue e cuide da topologia fora:

```python
from tempest_fastapi_sdk.queue import MessageBroker

mq = MessageBroker.rabbitmq(
    "amqp://guest:guest@localhost:5672/",
    declare_topology=False,
)
```

!!! info "Por que antes dos consumidores"
    Até a 0.322.0 a declaração vinha depois do `start()`. Medido contra um RabbitMQ 4: uma mensagem que já esperava na fila e era rejeitada na primeira entrega ia para um exchange que ainda não existia e se perdia (3 de 3 execuções). `tests/queue/test_fanout_live.py` fixa a ordem nova.

## Fan-out: uma cópia por consumidor

Dois módulos que reagem ao mesmo evento não podem assinar a mesma fila: eles dividiriam as mensagens em vez de cada um receber a sua. O formato certo no AMQP é **uma fila por consumidor**, todas ligadas ao mesmo exchange pela chave do evento. `QueueSpec.consumer` monta isso:

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import ExchangeSpec, MessageBroker, QueueSpec

mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")

EVENTS = ExchangeSpec("homework.events")
DEAD_LETTERS = "homework.dlx"


class TaskCompleted(BaseModel):
    task_id: str
    user_id: str


GAMIFICATION = QueueSpec.consumer(
    "gamification",
    "task.completed",
    exchange=EVENTS,
    dead_letter_exchange=DEAD_LETTERS,
)
RULES = QueueSpec.consumer(
    "rules",
    "task.completed",
    exchange=EVENTS,
    dead_letter_exchange=DEAD_LETTERS,
)


@mq.on(GAMIFICATION)
async def award_points(event: TaskCompleted) -> None:
    """Dá os pontos da tarefa concluída."""


@mq.on(RULES)
async def evaluate_rules(event: TaskCompleted) -> None:
    """Roda o motor de regras sobre o mesmo evento."""


async def emit(event: TaskCompleted) -> None:
    """Publica uma vez; cada fila ligada a `task.completed` recebe a sua cópia."""
    await mq.publish("task.completed", event, exchange=EVENTS, message_id=event.task_id)
```

O que sai no broker:

```text
homework.events --task.completed--> gamification.task.completed --rejeição--> homework.dlx --gamification.task.completed--> gamification.task.completed.dlq
homework.events --task.completed--> rules.task.completed        --rejeição--> homework.dlx --rules.task.completed-->        rules.task.completed.dlq
```

- **A fila se chama `<consumidor>.<canal>`** e é ligada ao exchange pela chave `task.completed`. O `@mq.on` entrega o exchange ao FastStream, que o declara e faz o binding quando o consumidor sobe.
- **A dead-letter é chaveada pelo nome da fila**, não pela chave do evento — a chave do evento é a mesma para todos os consumidores, e uma DLQ ligada por ela juntaria as falhas de todos. A falha da gamificação cai só em `gamification.task.completed.dlq`.
- **`publish(..., exchange=...)`** usa o canal como routing key (passar o próprio `QueueSpec` do consumidor publica pela chave do evento, não pelo nome da fila). Um `ExchangeSpec` é declarado na primeira publicação da conexão e não de novo até o `disconnect()` — o relay do outbox pode rodar antes de qualquer consumidor ter declarado o exchange. Um nome solto (`exchange="amq.topic"`) passa **sem** declarar: o exchange já existe e o tipo dele não é nosso para afirmar.

!!! tip "Sem `dead_letter_exchange`, sem dead-letter"
    `QueueSpec.consumer("rules", "task.completed", exchange=EVENTS)` monta só a fila e o binding. Como em qualquer `QueueSpec` sem `dead_letter`, a rejeição é descartada.

## Reprocessar a dead-letter queue

Consertado o handler, as mensagens que morreram voltam com `replay_dead_letters`:

```python
from tempest_fastapi_sdk.queue import ExchangeSpec, MessageBroker, QueueSpec

GAMIFICATION = QueueSpec.consumer(
    "gamification",
    "task.completed",
    exchange=ExchangeSpec("homework.events"),
    dead_letter_exchange="homework.dlx",
)


async def replay(mq: MessageBroker) -> int:
    """Devolve até 100 mensagens mortas à fila da gamificação, e só a ela."""
    return await mq.replay_dead_letters(GAMIFICATION, limit=100)
```

- **Volta só para o dono.** A mensagem é republicada direto na fila de origem pelo exchange padrão, nunca no exchange compartilhado — que entregaria uma segunda cópia ao motor de regras.
- **O `ack` vem depois do publish.** Se o publish falha, a mensagem volta para a DLQ (`nack(requeue=True)`) e a chamada levanta. A fila de origem é conferida antes da primeira leitura: medido, publicar pelo exchange padrão numa fila que não existe **não levanta** — o broker devolve a mensagem como não roteável —, e o `ack` depois disso a perderia.
- **O que viaja:** corpo, `message_id` (a chave do `deduplicate`), `content_type`, `content_encoding`, `correlation_id`, `priority`, `timestamp`, `type`, `reply_to` e os headers, com `persist=True`. `expiration`, `app_id` e `user_id` ficam: o replay é uma entrega nova. Os headers `x-death` / `x-first-death-*` / `x-last-death-*` que o broker escreve ao fazer o dead-letter **ficam para trás**: o middleware de retry conta tentativas por eles, e a mensagem reprocessada ganha orçamento novo.
- **Pelo menos uma vez, não exatamente uma.** Se o publish passa e o `ack` seguinte falha (a conexão cai entre os dois), a mensagem fica na fila de origem **e** na DLQ, e o próximo replay a entrega de novo. Consumidor com `deduplicate` pula a segunda cópia pelo `message_id`.
- **`limit` limita uma rodada.** Mensagem que falha de novo volta para o fim da DLQ; sem teto, um consumidor ainda quebrado transformaria o replay num laço.

A mesma chamada aceita a `RetryTopology` do `retry_queues` (lê a `.dead`, devolve para a fila principal) ou o nome cru da DLQ com o destino explícito:

```python
from tempest_fastapi_sdk.queue import MessageBroker, retry_queues

ORDERS = retry_queues(
    "orders.paid",
    retry_exchange="orders.retry",
    main_exchange="orders",
    dead_exchange="orders.dead",
)


async def replay_orders(mq: MessageBroker) -> int:
    """Esvazia a `.dead` do retry de volta para `orders.paid`."""
    return await mq.replay_dead_letters(ORDERS)


async def replay_by_name(mq: MessageBroker) -> int:
    """Topologia montada fora do SDK: diga de onde e para onde."""
    return await mq.replay_dead_letters("legacy.dlq", into="legacy")
```

!!! warning "Fila que já existe com outros argumentos"
    Declarar é idempotente só com os **mesmos** argumentos. Uma DLQ criada à mão antes (outro `x-queue-type`, um `x-message-ttl`) faz o `connect()` falhar com `PRECONDITION_FAILED - inequivalent arg ...` antes de qualquer consumidor subir. Ou alinhe o `QueueSpec` com o que existe, ou apague a fila, ou use `declare_topology=False` e cuide da topologia fora.

## Campo que o transporte não expressa **levanta**

`MessageBroker` é multi-transporte, e `dead_letter` / TTL / prioridade são AMQP. Pedir isso num broker que não tem o conceito não é ignorado:

```text
UnsupportedTopologyError: QueueSpec('orders.paid') sets dead_letter, which the
kafka transport cannot express. Remove the field, or use a bare channel name
and configure the topology outside the SDK.
```

Ignorar em silêncio produziria uma fila que **parece** configurada e descarta toda falha — exatamente o defeito que o `QueueSpec` existe para evitar. A escolha é a mesma do `op.replace_enum`, que levanta em dialeto não suportado em vez de emitir DDL que não faz nada.

Um `QueueSpec(name=...)` sem mais nada continua portátil em qualquer transporte: ele não pede nada além do nome.
