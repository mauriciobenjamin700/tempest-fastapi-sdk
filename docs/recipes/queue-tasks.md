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
from src.services.orders import mark_order_paid


# Escolha o transporte por um construtor — sem importar faststream.
mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)


class OrderPaid(BaseModel):
    order_id: str
    user_id: str


class OrderCancelled(BaseModel):
    order_id: str
    reason: str


@mq.on("orders.paid")
async def handle_order_paid(event: OrderPaid) -> None:
    """Recebe cada evento publicado no channel 'orders.paid'."""
    await mark_order_paid(event.order_id, event.user_id)
```

Repare no `event: OrderPaid`: **a anotação de tipo dirige a decodificação**. O FastStream valida o payload recebido nesse modelo Pydantic **antes** do seu handler rodar — mensagem malformada nunca chega no seu código.

Ligue o ciclo de vida no lifespan do FastAPI e publique de qualquer lugar:

```python
# src/api/app.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.queue import mq, OrderPaid


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await mq.connect()
    try:
        yield
    finally:
        await mq.disconnect()


app = FastAPI(lifespan=lifespan)


@app.post("/orders/{order_id}/pay")
async def pay_order(order_id: str) -> dict[str, str]:
    """Publish from any service/handler — channel first, message second."""
    await mq.publish("orders.paid", OrderPaid(order_id=order_id, user_id="u1"))
    return {"status": "published"}
```

!!! info "Transportes"
    `MessageBroker.rabbitmq(url)`, `.redis(url)`, `.kafka(*servers)`, `.nats(servers)`. Cada um faz lazy-import do backend certo do FastStream e erra com a mensagem de instalação exata se o extra faltar. Precisa injetar um broker customizado (ou de teste)? `MessageBroker(meu_broker)`.

!!! check "Recapitulando"
    - `MessageBroker.rabbitmq(url)` — escolhe o transporte, esconde o FastStream.
    - `@mq.on("channel")` — declara um consumidor; o tipo do parâmetro valida a mensagem.
    - `await mq.publish("channel", modelo)` — publica; channel primeiro.
    - `mq.publish(...)` só funciona depois de `connect()` (levanta `RuntimeError` antes).

Conecte no health router: `make_health_router(checks={"queue": mq.health_check})`.

### Consumidores baseados em classe

Prefere agrupar handlers numa classe (setup compartilhado, herança) a
usar funções soltas? `Consumer` oferece **duas** formas, ambas explícitas
(nada é adivinhado do nome da classe). Registre com `mq.register(...)`.

**Forma construtor** — passe o canal e o schema Pydantic no construtor;
sobrescreva `handle`:

```python
from tempest_fastapi_sdk.queue import Consumer

from src.queue import OrderPaid, mq
from src.services.orders import mark_order_paid


class OrderPaidConsumer(Consumer):
    async def handle(self, event: OrderPaid) -> None:
        await mark_order_paid(event.order_id)


mq.register(OrderPaidConsumer(channel="orders.paid", schema=OrderPaid))
```

**Forma agrupada** — uma classe, vários canais, cada método marcado com
`@subscribe`; o schema é a anotação do próprio método:

```python
from tempest_fastapi_sdk.queue import Consumer, subscribe

from src.queue import OrderCancelled, OrderPaid, mq


# OrderPaid / OrderCancelled definidos no bloco `src/queue/__init__.py` acima.


class OrdersConsumer(Consumer):
    @subscribe("orders.paid")
    async def on_paid(self, event: OrderPaid) -> None: ...

    @subscribe("orders.cancelled")
    async def on_cancelled(self, event: OrderCancelled) -> None: ...


mq.register(OrdersConsumer())
```

!!! info "Transparente, sem mágica"
    Na forma construtor o schema vem explícito no `__init__` e é o que
    valida o payload — sem farejar anotações. Na forma agrupada o schema
    é a anotação visível do método. O `@mq.on(...)` (decorator em função)
    continua disponível — escolha o estilo que preferir.

O canal pode ser uma string ou um [`QueueSpec`](#topologia-da-fila-queuespec),
igual ao `@mq.on(...)` — declarar dead-letter ou fila quorum não te força de
volta ao decorator. O mesmo vale para o resto do que o `@mq.on(...)` aceita:
[`prefetch=`](#prefetch-quantas-mensagens-ficam-em-voo) e as opções do
transporte (`exchange=`, por exemplo) existem nas duas formas.

```python
from faststream.rabbit import RabbitExchange

from tempest_fastapi_sdk.queue import Consumer, subscribe

from src.queue import OrderCancelled, OrderPaid, mq


class OrdersConsumer(Consumer):
    """Um teto para a classe inteira; um método pesado com o seu."""

    prefetch = 32

    @subscribe("relatorios.gerar", prefetch=1)
    async def gerar(self, event: OrderPaid) -> None: ...

    @subscribe("orders.cancelled", exchange=RabbitExchange("events", durable=True))
    async def on_cancelled(self, event: OrderCancelled) -> None: ...


mq.register(OrdersConsumer())
```

### Publicadores baseados em classe

`Consumer` cobria o consumo; o publish continuava solto — `await mq.publish("orders.paid", event)`, com o canal como string qualquer e o payload tipado `Any`. Nada ligava as duas pontas de um contrato que, na prática, é **um** contrato.

`Publisher` é essa metade. Carrega canal e modelo como atributos de classe, e o `publish` aceita exatamente o tipo declarado:

```python
from tempest_fastapi_sdk.queue import Publisher

from src.queue import ORDERS_PAID, OrderPaid, mq


class OrderPaidPublisher(Publisher[OrderPaid]):
    channel = ORDERS_PAID
    schema = OrderPaid


orders = mq.publisher_for(OrderPaidPublisher)


async def confirm_order(order_id: str) -> None:
    """Anuncia que o pedido foi pago.

    Args:
        order_id (str): O pedido confirmado.
    """
    await orders.publish(OrderPaid(order_id=order_id))
```

Três coisas que a chamada solta não dava:

- **O type-checker enxerga o payload.** `Publisher[OrderPaid]` faz o `publish` receber um `OrderPaid`; publicar o modelo errado vira rabisco vermelho no editor em vez de mensagem que o consumidor rejeita em produção.
- **O schema é cobrado na saída.** Se o objeto não for instância do modelo declarado, `publish` levanta `TypeError` — o consumidor está a um processo de distância e só consegue rejeitar o que já saiu.
- **A topologia é registrada.** Um `QueueSpec` no `channel` passa pelo mesmo binding do `@mq.on(...)`, então um serviço que **só publica** ainda declara a dead-letter exchange que ele nomeia.

**Canal e schema não precisam ser atributos de classe.** Os dois também
entram no `__init__` — útil quando o canal só é conhecido em runtime (por
tenant, por ambiente) e você não quer uma subclasse por valor:

```python
from tempest_fastapi_sdk.queue import Publisher

from src.queue import OrderPaid, mq

orders: Publisher[OrderPaid] = Publisher(mq, channel="orders.paid", schema=OrderPaid)
```

O `publisher_for` aceita os mesmos dois, e eles **vencem** o que a classe
declara:

```python
from tempest_fastapi_sdk.queue import Publisher

from src.queue import OrderPaid, mq


class TenantPublisher(Publisher[OrderPaid]):
    schema = OrderPaid


def publisher_for_tenant(tenant: str) -> Publisher[OrderPaid]:
    """Devolve o publicador do canal desse tenant.

    Args:
        tenant (str): O identificador do tenant.

    Returns:
        O publicador ligado a `orders.paid.<tenant>`.
    """
    return mq.publisher_for(TenantPublisher, channel=f"orders.paid.{tenant}")
```

`channel` e `schema` são parâmetros nomeados, não `**options` — para que o
type-checker os enxergue e para que uma opção de publish com esse mesmo
nome não seja engolida.

!!! warning "Não confunda com `mq.publisher(canal)`"
    `mq.publisher(...)` devolve o objeto publisher do próprio FastStream — escape hatch, útil sobretudo porque faz o canal aparecer no AsyncAPI gerado. O `Publisher` passa por `mq.publish()`, então mantém o `message_id` de que a deduplicação depende e os headers `traceparent` / `x-request-id` de que o tracing depende. Um publicador que contornasse isso pareceria idêntico e quebraria os dois em silêncio.

## Topologia da fila — `QueueSpec`

O canal como string resolve a maioria dos casos. O que ele **não** expressa é justamente o que decide se a fila sobrevive a um restart, para onde vai uma mensagem rejeitada e quanto tempo ela vive. No RabbitMQ isso mora na declaração da fila, não no nome.

`QueueSpec` carrega essa topologia como dado tipado, e é aceito em qualquer lugar onde a string era:

```python
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import DeadLetterSpec, MessageBroker, QueueSpec, QueueType

mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")


class OrderPaid(BaseModel):
    order_id: str
    amount_cents: int

ORDERS_PAID = QueueSpec(
    name="orders.paid",
    dead_letter=DeadLetterSpec(exchange="dlx"),
    message_ttl_ms=60_000,
    queue_type=QueueType.QUORUM,
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

### O exchange precisa existir

O RabbitMQ aceita declarar uma fila apontando para um `x-dead-letter-exchange` que não existe — e aí descarta no roteamento, em silêncio. Por isso o `connect()` declara os exchanges nomeados pelos `QueueSpec` registrados, como `topic` durável:

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

### Campo que o transporte não expressa **levanta**

`MessageBroker` é multi-transporte, e `dead_letter` / TTL / prioridade são AMQP. Pedir isso num broker que não tem o conceito não é ignorado:

```text
UnsupportedTopologyError: QueueSpec('orders.paid') sets dead_letter, which the
kafka transport cannot express. Remove the field, or use a bare channel name
and configure the topology outside the SDK.
```

Ignorar em silêncio produziria uma fila que **parece** configurada e descarta toda falha — exatamente o defeito que o `QueueSpec` existe para evitar. A escolha é a mesma do `op.replace_enum`, que levanta em dialeto não suportado em vez de emitir DDL que não faz nada.

Um `QueueSpec(name=...)` sem mais nada continua portátil em qualquer transporte: ele não pede nada além do nome.


## Confiabilidade do caminho de eventos

A política do consumidor é `REJECT_ON_ERROR`. Handler que levanta faz `basic.reject` com `requeue=False` — não entra em loop, e **some**. Três peças fecham isso, espelhando o que o `TaskQueue` já tem.

### Dead-letter: falha vira registro

```python
from tempest_fastapi_sdk.queue import MessageBroker
from tempest_fastapi_sdk.tasks import DbDeadLetterSink


def wire_dead_letter(mq: MessageBroker, sink: DbDeadLetterSink) -> None:
    """Manda toda falha terminal do consumidor para o sink.

    Args:
        mq (MessageBroker): O broker, antes do connect().
        sink (DbDeadLetterSink): Onde o evento morto é gravado.
    """
    mq.dead_letter(sink, max_attempts=3)
```

O sink é o **mesmo** protocolo do caminho de tarefas, então `DbDeadLetterSink`, o painel do admin e o `make_requeue_action` funcionam sem mudança — tarefa morta e evento morto na mesma tela.

O mapeamento é deliberado: `task_name` carrega o **canal**, `task_id` o message id do broker, e `kwargs["body"]` o corpo bruto.

!!! tip "Reporta uma vez, não a cada tentativa"
    O sink é chamado só na entrega que esgota o `max_attempts`, lido do header `x-death`. Alertar em toda tentativa transforma uma mensagem ruim num fluxo de alertas.

### Retry com atraso, feito pelo broker

O AMQP não tem atraso por mensagem. O jeito portátil é um par de filas: a principal manda a rejeitada para uma fila que só a segura, e o TTL dessa fila a devolve para a exchange principal ao expirar.

```python
from tempest_fastapi_sdk.queue import ConsumerRetryPolicy, retry_queues

from tempest_fastapi_sdk.queue import (
    ConsumerRetryPolicy,
    MessageBroker,
    retry_queues,
)

TOPOLOGY = retry_queues(
    "orders.paid",
    ConsumerRetryPolicy(max_attempts=3, delay_ms=30_000),
    retry_exchange="orders.retry",
    main_exchange="orders",
    dead_exchange="orders.dead",
)


async def wire_retry(mq: MessageBroker) -> None:
    """Declara e liga as três filas da cadeia de retry.

    Args:
        mq (MessageBroker): O broker, já conectado.
    """
    await mq.declare_retry_topology(TOPOLOGY)
```

!!! danger "Declarar sem ligar descarta a mensagem"
    Só declarar as filas não basta: cada uma precisa estar **ligada** à sua exchange. Sem os bindings, a rejeitada é roteada para uma exchange sem nada atrás — e o RabbitMQ descarta em silêncio. Medido contra um broker real: com os bindings a mensagem volta pontualmente (intervalos de 1,5s para um TTL de 1,5s); sem eles, é entregue uma vez e some. `declare_retry_topology()` faz as duas coisas.

Quem espera é o **broker**, então restart do worker no meio não muda nada. A alternativa é o plugin `rabbitmq_delayed_message_exchange`, mais simples de declarar e que **exige o plugin** — indisponível em várias ofertas gerenciadas, incluindo o plano free do CloudAMQP.

!!! warning "A topologia sozinha retenta para sempre"
    O AMQP conta redelivery no `x-death` mas não para sozinho. Quem enforce o `max_attempts` é o middleware do `dead_letter()`. Declarar a topologia sem instalar o middleware dá retry infinito — por isso os dois estão documentados juntos.

??? info "Como o `x-death` é lido (e por que a entrada `expired` não conta)"
    O RabbitMQ guarda **uma entrada por par (fila, motivo)**, e a cadeia de retry dead-letta duas vezes por rodada: `rejected` saindo da fila principal e `expired` saindo da fila de espera quando o TTL dispara. Somar todas as entradas avança o contador de dois em dois — com `max_attempts=3` a mensagem era descartada depois de **duas** execuções. `delivery_attempt()` conta só o que é entrega falha, ignorando `expired`.

### Métricas

```python
from tempest_fastapi_sdk.queue import MessageBroker, QueueMetrics


def wire_metrics(mq: MessageBroker) -> None:
    """Publica contagem e duração de consumo no /metrics compartilhado.

    Args:
        mq (MessageBroker): O broker, antes do connect().
    """
    mq.enable_metrics(QueueMetrics())
```

Gera `queue_messages_total{channel,status}` e `queue_message_duration_seconds{channel}`. Sem isso a taxa de falha do consumidor é invisível — a mensagem é rejeitada, o broker descarta, e nada conta.

O `status` é `ok`, `error` ou `duplicate`. O último é a entrega que o `deduplicate()` rejeitou porque outro worker segurava a claim: rótulo próprio justamente para que a deduplicação funcionando não engorde a taxa de erro em que o alerta se apoia.


### Prefetch — quantas mensagens ficam em voo

Sem limite, o broker entrega tão rápido quanto o consumidor acka. Três consequências, todas em produção: um handler lento acumula mensagens na memória do processo; a primeira réplica a conectar puxa o lote e as outras ficam ociosas; e o backlog não-ackado fica em RAM até o pod morrer por OOM, devolvendo tudo para a fila.

```python
from tempest_fastapi_sdk.queue import MessageBroker

mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/", prefetch=32)


@mq.on("relatorios.gerar", prefetch=1)
async def gerar(pedido: dict[str, str]) -> None:
    """Handler pesado: teto baixo, sem estrangular os vizinhos."""
```

O valor do broker vale para a conexão; o do consumidor sobrescreve só para ele.

No caminho de classe o botão é o mesmo, em três alturas — construtor,
classe e método:

```python
from tempest_fastapi_sdk.queue import Consumer, subscribe

from src.queue import OrderPaid, mq


class RelatoriosConsumer(Consumer):
    prefetch = 32                       # vale para todo binding da classe

    @subscribe("relatorios.gerar", prefetch=1)
    async def gerar(self, event: OrderPaid) -> None:
        """Handler pesado: o teto do método ganha do da classe."""


class OrderPaidConsumer(Consumer):
    async def handle(self, message: OrderPaid) -> None: ...


mq.register(RelatoriosConsumer())
mq.register(OrderPaidConsumer(channel="orders.paid", schema=OrderPaid, prefetch=8))
```

!!! note "Por que `prefetch` é um parâmetro nomeado, e não mais um `**options`"
    O FastStream não tem keyword `prefetch` — ele carrega o `basic.qos`
    num objeto `Channel`. Repassar a palavra crua levanta
    `TypeError: RabbitRegistrator.subscriber() got an unexpected keyword
    argument 'prefetch'`, que era exatamente o que acontecia no caminho
    de classe até a v0.209.0. Nomear o parâmetro é o que permite traduzir
    — e o que o type checker enxerga.

!!! warning "Não existe default bom que eu possa chutar"
    O comportamento atual é **sem limite**, e este PR não muda isso — expõe o botão. Valor pequeno demais serializa o consumo e derruba o throughput; grande demais recria o problema. O número certo depende da latência do seu handler, e fixar um sem medir seria o mesmo erro que o `DEFAULT_INTRA_OP_THREADS` do modelops cometeu antes de ser rejustificado. Meça com um consumidor de latência conhecida antes de escolher.

!!! info "Prefetch não é concorrência do handler"
    Prefetch limita quantas mensagens o **broker entrega** sem ack. Quantas corrotinas rodam ao mesmo tempo é outra coisa. Confundir os dois é comum: `prefetch=1` não serializa o handler se você mesmo dispara tarefas em paralelo dentro dele.

!!! check "Publisher confirms já vêm ligados"
    O `Channel` do FastStream tem `publisher_confirms=True` por padrão, então um publish perdido em restart do broker não passa silencioso. Está pinado por teste.

### Idempotência no consumo

A entrega é at-least-once: restart do worker, nack com requeue, ack perdido na rede. Redelivery não é caso raro, é o modo normal.

```python
from tempest_fastapi_sdk.queue import MessageBroker, RedisDedupStore


def wire_dedup(mq: MessageBroker, redis: object) -> None:
    """Roda cada message id no máximo uma vez.

    Args:
        mq (MessageBroker): O broker, antes do connect().
        redis (object): Cliente async do Redis.
    """
    mq.deduplicate(RedisDedupStore(redis), ttl_seconds=86_400)
```

O `publish()` passou a gerar `message_id` quando você não passa — sem id estável não existe chave para deduplicar, e redelivery fica indistinguível de evento novo.

Marcação em **duas fases**: a primeira entrega marca `in_flight` e roda; sucesso marca `done` e a próxima é pulada; **falha libera a chave**, para que o retry realmente retente. Sem a terceira parte, trocar "processa duas vezes" por "não processa nenhuma" seria pior que o problema original.

!!! danger "Isto não é exactly-once — nada aqui é"
    A marca e o efeito do handler não são atômicos. Crash entre os dois deixa uma chave `in_flight` que expira, e a mensagem roda de novo. É at-least-once com janela muito menor, não exactly-once.

!!! tip "Quando o banco já resolve, use o banco"
    Se o efeito do handler for uma linha com chave natural do domínio, `INSERT ... ON CONFLICT DO NOTHING` é idempotente **sem peça móvel nenhuma** — sem TTL para calibrar, sem segundo store para operar. Este middleware é para efeito que não é linha: e-mail, chamada a terceiro, publicação downstream.

!!! warning "Entrega concorrente é rejeitada, não ackada"
    Se outro worker está com a claim, a cópia levanta `ConcurrentDeliveryError` e o broker rejeita. Ackar seria perigoso: o worker em voo ainda pode falhar, e a cópia que poderia retentar teria sumido.

### Tracing e request id atravessando a fila

A requisição abre um trace, publica um evento e responde 201 — e o consumidor que cobra o cartão, escreve no ledger e manda o e-mail aparecia como **três traces órfãos**, sem pai e sem relação entre si.

```python
from tempest_fastapi_sdk.queue import MessageBroker


def wire_tracing(mq: MessageBroker) -> None:
    """Abre um span por mensagem consumida, ligado ao publish.

    Args:
        mq (MessageBroker): O broker, antes do connect().
    """
    mq.enable_tracing()
```

O `publish()` já injeta o `traceparent` e o **request id** corrente nos headers; o `enable_tracing()` é a outra metade.

!!! info "Link, não filho"
    O span do consumidor referencia o do publish como **link**, não como pai. A semconv recomenda isso para consumo assíncrono, e o motivo é prático: o consumidor pode rodar minutos depois, e um span filho dessa duração esticaria o trace da requisição e tornaria a latência dela ilegível.

!!! tip "O request id vale mais que o span no dia a dia"
    O `RequestIDMiddleware` já põe o id em toda linha de log HTTP. Agora o worker adota o id do publisher enquanto processa, então `grep` sozinho correlaciona requisição e consumo — sem abrir o Jaeger.

Sem o extra `[otel]` tudo isso é no-op; a propagação do request id funciona de qualquer jeito, porque não depende de OpenTelemetry.

## Tarefas em background — `TaskQueue`

Uma **fila de tarefas** tira trabalho lento do request e joga num worker. O TaskIQ faz isso, mas espalha a API entre broker, scheduler, schedule source e `.kiq()`. `TaskQueue` dobra tudo num objeto só, com vocabulário óbvio.

Instale com `[tasks]` (puxa `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

email = "ana@example.com"


tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)


@tq.task
async def send_welcome(to: str, name: str) -> None:
    """Roda num worker, fora do request."""
    await email.send(to, "Bem-vindo!", f"Olá, {name}.")
```

!!! note "`email` é o seu mailer"
    `email` aqui é o seu enviador de e-mail — uma instância de `EmailUtils`
    (extra `[email]`) montada no módulo. Veja a
    [receita de e-mail](email.md); troque pela sua própria dependência de
    envio.

`@tq.task` devolve um objeto `Task` tipado com **duas** ações claras:

```python
import asyncio

from src.db.models import UserModel
from src.tasks import send_welcome

user = UserModel(name="Ana", email=email)
email = "ana@example.com"


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

### Recursos do worker — `on_startup` / `on_shutdown`

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

### Tarefas baseadas em classe

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

## Tarefas periódicas — `cron` / `interval`

Agendar é parte do mesmo `TaskQueue` — sem scheduler separado no seu código.

!!! tip "Não sabe cron? Use os enums e helpers (v0.94.0)"
    Ninguém precisa decorar `"0 9 * * MON-FRI"`. O módulo
    `tempest_fastapi_sdk.tasks` traz **`Cron`** (expressões prontas),
    **`CronOffset`** (fusos por lugar, não por dígitos), **`Weekday`** e
    **funções construtoras** (`daily`, `weekdays`, `hourly`,
    `every_n_minutes`, `weekly`, `weekends`, `monthly`). Todas viram uma
    string cron simples que entra direto no `@tq.cron(...)`.

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import Cron, CronOffset, Weekday, daily, weekdays

from src.tasks import tq


# Legível, sem sintaxe cron:
@tq.cron(Cron.EVERY_WEEKDAY_9AM, cron_offset=CronOffset.BRASILIA)
async def daily_digest() -> None:
    ...


@tq.cron(daily(hour=9), cron_offset=CronOffset.BRASILIA)   # 09:00 BRT
async def other_digest() -> None:
    ...


@tq.cron(weekdays(hour=8, minute=30), cron_offset=CronOffset.BRASILIA)
async def morning_sync() -> None:
    ...


@tq.cron(Cron.EVERY_5_MINUTES)
async def heartbeat() -> None:
    ...
```

| Quero rodar… | Escreva |
| --- | --- |
| A cada 5 min | `Cron.EVERY_5_MINUTES` ou `every_n_minutes(5)` |
| Todo dia às 9h | `daily(hour=9)` |
| Dias úteis às 8h30 | `weekdays(hour=8, minute=30)` |
| Toda segunda | `weekly(Weekday.MON)` |
| Todo dia 1º | `monthly(day=1)` |
| No fuso de Brasília | `cron_offset=CronOffset.BRASILIA` |

`CronOffset` cobre os fusos do Brasil por nome — `BRASILIA` (-03:00),
`FERNANDO_DE_NORONHA` (-02:00), `MANAUS` (-04:00), `ACRE` (-05:00) — mais
`UTC`. Prefere cron cru ou intervalos? Continua valendo:

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

## Confiabilidade e observabilidade das tarefas

Tarefa que só roda no worker precisa de três coisas que o TaskIQ tem, mas espalhadas: **retry**, **dead-letter** e **métricas**. O `TaskQueue` expõe as três como middleware opt-in — chame **antes do `connect()`**, e nada toca a API de middleware do broker.

### Retry tipado

`RetryPolicy` carrega a config de retry como labels; `enable_retries()` instala o middleware do TaskIQ que as lê:

```python
from tempest_fastapi_sdk.tasks import RetryPolicy, TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.enable_retries(default_max_retries=3)


@tq.task(name="reports:nightly", retry=RetryPolicy(max_retries=5))
async def nightly() -> None:
    ...   # re-executada até 5x em caso de erro
```

### Dead-letter — pra onde vão as falhas terminais

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

!!! tip "Combine com o retry"
    Passe o **mesmo** `default_max_retries` pro `enable_retries` e pro `dead_letter`: assim o ponto de "retries esgotados" bate pras tarefas que não definem `max_retries` próprio. A ordem de instalação não importa — o dead-letter decide sozinho lendo as labels da mensagem.

### Métricas Prometheus por tarefa

`TaskMetrics` conta execuções (por status) e histograma de duração, rotulados por tarefa, no **mesmo** `/metrics` do SDK (passe o `registry` compartilhado):

```python
from tempest_fastapi_sdk.tasks import TaskMetrics, TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")
tq.enable_metrics(TaskMetrics())   # tasks_runs_total{task,status} + tasks_duration_seconds{task}
```

### Painel de dead-letter no admin

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

## Workers em produção

O worker e o scheduler são processos separados apontando pros objetos crus expostos pelo `TaskQueue`. A CLI do TaskIQ resolve `módulo:atributo` com um `getattr` simples, então **exponha os dois como nomes de módulo** — um caminho com ponto não é resolvido:

```python
# src/tasks.py — depois de registrar as tarefas
from tempest_fastapi_sdk.tasks import TaskQueue

tq: TaskQueue = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")

broker = tq.broker
scheduler = tq.scheduler
```

```bash
# consome e executa as tarefas
taskiq worker    src.tasks:broker

# um único processo de scheduler pro cluster inteiro
taskiq scheduler src.tasks:scheduler
```

`tq.broker` é o broker TaskIQ (conhece todas as tarefas registradas); `tq.scheduler` é o `TaskiqScheduler` interno.

!!! warning "`src.tasks:tq.broker` não funciona"
    A CLI faz `getattr(module, "tq.broker")` e levanta
    `AttributeError: module 'src.tasks' has no attribute 'tq.broker'` —
    o processo nem sobe. Vale para qualquer caminho com ponto
    (`tq.scheduler`, `scheduler.scheduler`). Daí o `broker = tq.broker`
    acima.

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
- **Cron sem sintaxe** — `Cron` / `CronOffset` / `Weekday` + helpers (`daily`, `weekdays`, `every_n_minutes`, …) pra agendar por nome; `CronOffset.BRASILIA` no lugar de `"-03:00"`.
- **Estilos** — decorators (`@mq.on`, `@tq.task`, `@tq.cron`) **ou** classes (`Consumer` + `mq.register`, `TaskDef` + `tq.register`); as duas formas coexistem.
- **Outbox** — `BaseOutboxModel` + `OutboxRelay` + `save_with_outbox`, com o `publish` do relay apontando pro `MessageBroker`. Veja [Outbox](outbox.md).
- **Renome (v0.94.0)** — `AsyncBrokerManager` → **`AsyncQueueManager`** (wrapper fino; alias antigo mantido). Os facades `MessageBroker` / `TaskQueue` seguem recomendados; `AsyncTaskBrokerManager` / `AsyncTaskScheduler` continuam como legado funcional.
