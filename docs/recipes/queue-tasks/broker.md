---
description: "Use para publicar e consumir eventos por channel com `MessageBroker`; contém consumidores e publicadores em classe e Kafka com chave, grupo, commit e `KafkaSettings`."
---

# Mensageria com `MessageBroker`

Use para publicar e consumir eventos por channel, com consumidores e publicadores em classe e Kafka (chave, grupo e commit).

Parte da receita [Fila e Tarefas](../queue-tasks.md).

O problema que o FastStream resolve mal: a API muda de forma conforme o transporte. Você assina com `@broker.subscriber("q")` e publica com `broker.publish(msg, queue="q")` no RabbitMQ, `topic=` no Kafka, `subject=` no NATS. Confuso e não-portável.

`MessageBroker` esconde isso atrás de **um** conceito: um **channel** (uma string). Você publica num channel e quem estiver inscrito nele recebe.

Instale com `[queue]` (puxa `faststream[rabbit]`). Os outros transportes têm extra próprio, porque o `[queue]` traz só o cliente do RabbitMQ: `[queue-kafka]`, `[queue-nats]`, `[queue-redis]`.

```python
# src/queue/broker.py

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

O `src/queue/__init__.py` só reexporta — `from .broker import OrderPaid as OrderPaid`, o mesmo para `OrderCancelled` e `mq`, mais o `__all__` — e é por ele que o resto do código importa (`from src.queue import mq`). Definição ou chamada no `__init__.py` acusa `INIT001` no `tempest check`.

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
    `MessageBroker.rabbitmq(url)`, `.redis(url)`, `.kafka(*servers)`, `.nats(servers)`. Cada um faz lazy-import do backend certo do FastStream e, se ele faltar, erra nomeando o extra que o instala (`[queue]`, `[queue-redis]`, `[queue-kafka]`, `[queue-nats]`). No Kafka, veja [Kafka: chave, grupo e commit](#kafka-chave-grupo-e-commit). Precisa injetar um broker customizado (ou de teste)? `MessageBroker(meu_broker)`.

!!! check "Recapitulando"
    - `MessageBroker.rabbitmq(url)` — escolhe o transporte, esconde o FastStream.
    - `@mq.on("channel")` — declara um consumidor; o tipo do parâmetro valida a mensagem.
    - `await mq.publish("channel", modelo)` — publica; channel primeiro.
    - `mq.publish(...)` só funciona depois de `connect()` (levanta `RuntimeError` antes).

Conecte no health router: `make_health_router(checks={"queue": mq.health_check})`. O `health_check()` faz o `ping` real do FastStream (`timeout=2.0` por default) e devolve `False` antes do `connect()` ou com a conexão caída — veja [Liveness e readiness](../http/bootstrap.md#liveness-e-readiness).

## Consumidores baseados em classe

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


# OrderPaid / OrderCancelled definidos no bloco `src/queue/broker.py` acima.


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

O canal pode ser uma string ou um [`QueueSpec`](topology.md),
igual ao `@mq.on(...)` — declarar dead-letter ou fila quorum não te força de
volta ao decorator. O mesmo vale para o resto do que o `@mq.on(...)` aceita:
[`prefetch=`](events-reliability.md#prefetch-quantas-mensagens-ficam-em-voo) e as opções do
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

## Publicadores baseados em classe

`Consumer` cobria o consumo; o publish continuava solto — `await mq.publish("orders.paid", event)`, com o canal como string qualquer e o payload tipado `Any`. Nada ligava as duas pontas de um contrato que, na prática, é **um** contrato.

`Publisher` é essa metade. Carrega canal e modelo como atributos de classe, e o `publish` aceita exatamente o tipo declarado:

```python
from tempest_fastapi_sdk.queue import Publisher

from src.queue import OrderPaid, mq


class OrderPaidPublisher(Publisher[OrderPaid]):
    channel = "orders.paid"
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

## Kafka: chave, grupo e commit

Instale com `[queue-kafka]`. Ele puxa `faststream[kafka]` e declara `aiokafka>=0.10.0` por conta própria: o FastStream aceita `aiokafka>=0.9`, e com o 0.9.0 todo consumidor falha ao subir com `TypeError: AIOKafkaConsumer.__init__() got an unexpected keyword argument 'group_instance_id'` (medido com o FastStream 0.7.5).

No Kafka não existe fila: o channel é um **tópico**, e quem recebe uma cópia é decidido pelo **grupo de consumidores**. Três knobs cobrem isso, todos pela fachada tipada:

```python
# src/queue/billing.py

from pydantic import BaseModel

from tempest_fastapi_sdk.queue import (
    Consumer,
    MessageBroker,
    OffsetResetEnum,
    Publisher,
)

mq = MessageBroker.kafka("localhost:9092")


class OrderPaid(BaseModel):
    order_id: str


@mq.on(
    "orders.paid",
    group_id="billing",
    auto_offset_reset=OffsetResetEnum.EARLIEST,
)
async def bill(event: OrderPaid) -> None:
    """Uma réplica do grupo `billing` recebe cada pedido."""
    print(f"faturando {event.order_id}")


class AuditConsumer(Consumer):
    """O mesmo tópico, outro grupo: o audit lê a sua própria cópia."""

    group_id = "audit"
    auto_offset_reset = OffsetResetEnum.EARLIEST

    async def handle(self, event: OrderPaid) -> None:
        print(f"auditando {event.order_id}")


class OrderPaidPublisher(Publisher[OrderPaid]):
    channel = "orders.paid"
    schema = OrderPaid


mq.register(AuditConsumer(channel="orders.paid", schema=OrderPaid))
orders = mq.publisher_for(OrderPaidPublisher)


async def pay(order_id: str) -> None:
    """A chave escolhe a partição: os eventos de um pedido chegam em ordem."""
    await orders.publish(OrderPaid(order_id=order_id), key=order_id)
```

| Knob | Onde | O que faz |
| --- | --- | --- |
| `key=` | `mq.publish`, `Publisher.publish` | Mesma chave, mesma partição, lida na ordem em que foi escrita. `str` vira UTF-8 — o `aiokafka` só aceita `bytes` e levanta `TypeError: a bytes-like object is required, not 'str'`. |
| `group_id=` | `@mq.on`, `@subscribe`, `Consumer` | Réplicas com o mesmo grupo dividem as partições; cada grupo distinto lê tudo. Sem grupo não há onde commitar offset (veja a tabela de `ack_policy` abaixo). |
| `auto_offset_reset=` | `@mq.on`, `@subscribe`, `Consumer` | Onde um grupo **sem offset commitado** começa. O default do Kafka é `LATEST`: o consumidor novo não vê o que foi publicado antes do primeiro deploy. Grupo que já commitou retoma do commit, diga isso o que disser. |

`Consumer` e `@subscribe` seguem a mesma regra de `prefetch` e `ack_policy`: o valor da classe vale para todo binding, e o do `@subscribe` sobrescreve para aquele método.

O que foi medido contra um Kafka 3.9.1 real (`tests/queue/test_kafka_live.py`, no `make test-docker`, com o FastStream 0.7.5 + `aiokafka` 0.10.0 e com o FastStream 0.7.8):

- 6 chaves × 8 registros num tópico de 3 partições: cada chave caiu numa partição só, a mesma que o `publish` devolveu no `RecordMetadata`, e foi lida na ordem 0..7; as 6 chaves ocuparam mais de uma partição.
- 40 registros num tópico de 2 partições, duas réplicas no grupo `workers` e uma no grupo `audit`: cada réplica recebeu uma parte não vazia, sem registro repetido entre elas, e as duas juntas somaram os 40; o `audit` recebeu os 40.

### `ack_policy` no Kafka é a política de **commit**

| `ack_policy` | Comportamento |
| --- | --- |
| `None` (`ACK_FIRST`) | O FastStream liga o `enable_auto_commit` do `aiokafka`: o offset é commitado por timer, tenha o handler dado certo ou não. |
| `ACK` | Commit depois do handler — também quando ele levantou. Medido: o handler levantou no registro 1 de 3, a réplica parou, outra subiu no mesmo grupo e leu só os registros novos. |
| `NACK_ON_ERROR` | Volta o consumidor para o registro que falhou e o lê de novo. Medido: um handler que levanta na primeira vez rodou duas vezes com o mesmo registro. |
| `REJECT_ON_ERROR` | O mesmo que `ACK` no Kafka; o FastStream avisa com um `UserWarning` (`has the same effect as AckPolicy.ACK`) — que **levanta** em quem roda `filterwarnings = ["error"]`. Prefira `ACK`. |
| `MANUAL` | O handler commita. |

Qualquer política que não seja `ACK_FIRST` exige `group_id`, porque o Kafka guarda offset por grupo. Sem grupo, o `aiokafka` recusa o commit com `IllegalOperation('Requires group_id')` em **toda** mensagem, depois de o handler já ter rodado. A fachada recusa antes, na hora de registrar:

```text
ValueError: Consumer of 'orders.paid' runs under ack_policy=ACK, which commits
the offset after the handler, and Kafka commits only for a consumer group.
Pass group_id=..., or leave ack_policy unset (ACK_FIRST: offsets
auto-committed on a timer).
```

A política vale também quando vem do broker inteiro (`MessageBroker.kafka(..., ack_policy=AckPolicy.ACK)`).

### Deduplicação no Kafka

O Kafka não tem o campo `message_id` do AMQP, e o `publish` do FastStream para Kafka não aceita esse argumento. O `mq.publish` põe o id no header `message_id` (`MESSAGE_ID_HEADER`), e o `mq.deduplicate(...)` lê dali primeiro — o seu `message_id=` explícito vai pelo mesmo caminho. Medido: dois `publish` com o mesmo `message_id` viram dois registros, em dois offsets, e o handler rodou uma vez.

Registro publicado por outro produtor, sem o header, é deduplicado por `tópico:partição:offset`. O id que o FastStream dá a uma mensagem Kafka é `<offset>-<timestamp>` (`0-1794491612333`, por exemplo), e o offset é por partição: dois registros no mesmo offset de partições diferentes, escritos no mesmo milissegundo, têm o mesmo id. Medido: dois registros no offset 0 das partições 0 e 1, com o mesmo `timestamp_ms`, deduplicados por esse id — o segundo era pulado; por coordenada, os dois rodam.

### O que o Kafka não expressa **levanta**

`prefetch` é o `basic.qos` do AMQP: no Kafka, NATS e Redis levanta `UnsupportedTopologyError`. Antes ele chegava ao FastStream como um `Channel` do RabbitMQ e falhava com `TypeError: KafkaRegistrator.subscriber() got an unexpected keyword argument 'channel'`, sem nomear o knob. No sentido contrário, `group_id`, `auto_offset_reset` e `key=` fora do Kafka também levantam `UnsupportedTopologyError` — em `@mq.on`, em `mq.register` e em `mq.publish` —, porque descartá-los trocaria "uma cópia por grupo" pelo que o outro transporte faz por default.

### Conexão pelo ambiente — `KafkaSettings`

O exemplo acima escreve `"localhost:9092"` no código. Em produção o cluster pede TLS e SASL, e montar isso à mão significa escolher a classe certa de `faststream.security` e criar um `ssl.SSLContext` em cada serviço. O mixin `KafkaSettings` lê tudo do ambiente e o `MessageBroker.kafka_from_settings` monta o broker:

```python
# src/queue/broker.py

from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.queue import MessageBroker
from tempest_fastapi_sdk.settings import KafkaSettings


class Settings(KafkaSettings, BaseAppSettings):
    """Configuração do serviço."""


settings: Settings = Settings()
mq: MessageBroker = MessageBroker.kafka_from_settings(settings)
```

Com este `.env`:

```text
KAFKA_BOOTSTRAP_SERVERS=["kafka-1:9093","kafka-2:9093"]
KAFKA_CLIENT_ID=billing-api
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISM=SCRAM-SHA-512
KAFKA_SASL_USERNAME=billing
KAFKA_SASL_PASSWORD=troque-me
```

`print(settings)` mostra a senha mascarada, e o broker sai com um `SASLScram512` (medido):

```text
KAFKA_BOOTSTRAP_SERVERS=['kafka-1:9093', 'kafka-2:9093'] KAFKA_CLIENT_ID='billing-api' KAFKA_SECURITY_PROTOCOL=<KafkaSecurityProtocolEnum.SASL_SSL: 'SASL_SSL'> KAFKA_SASL_MECHANISM=<KafkaSaslMechanismEnum.SCRAM_SHA_512: 'SCRAM-SHA-512'> KAFKA_SASL_USERNAME='billing' KAFKA_SASL_PASSWORD=SecretStr('**********') KAFKA_SSL_CAFILE=None KAFKA_SSL_CERTFILE=None KAFKA_SSL_KEYFILE=None KAFKA_SSL_KEY_PASSWORD=SecretStr('')
```

Sem nada no ambiente, os defaults apontam para `localhost:9092` em `PLAINTEXT` — o broker de desenvolvimento.

| `KAFKA_SECURITY_PROTOCOL` | O que o `kafka_kwargs()` põe em `security` |
| --- | --- |
| `PLAINTEXT` (default) | `None`: sem TLS, sem autenticação |
| `SSL` | `BaseSecurity` com um `ssl.SSLContext` que verifica certificado e host; `KAFKA_SSL_CAFILE` troca a loja do sistema, `KAFKA_SSL_CERTFILE` + `KAFKA_SSL_KEYFILE` + `KAFKA_SSL_KEY_PASSWORD` acrescentam o certificado de cliente (mTLS) |
| `SASL_PLAINTEXT` | `SASLPlaintext`, `SASLScram256` ou `SASLScram512`, pelo `KAFKA_SASL_MECHANISM` (`PLAIN`, `SCRAM-SHA-256`, `SCRAM-SHA-512`), sem TLS |
| `SASL_SSL` | a mesma classe SASL, com o `ssl.SSLContext` do `SSL` |

`kafka_kwargs()` devolve só o que vai por keyword (`security`, e `client_id` quando `KAFKA_CLIENT_ID` está definido): os seeds são posicionais no `MessageBroker.kafka`. Por isso `MessageBroker.kafka(**settings.kafka_kwargs())` sozinho é recusado, em vez de subir um broker sem seed que só falharia no `connect()` com `KafkaConnectionError: Unable to bootstrap from []`:

```text
ValueError: MessageBroker.kafka() needs at least one bootstrap server. From a KafkaSettings, use MessageBroker.kafka_from_settings(settings), or pass *settings.KAFKA_BOOTSTRAP_SERVERS before **settings.kafka_kwargs().
```

Combinação que o protocolo ignoraria falha no boot, não no primeiro `connect()`: `SASL_*` sem `KAFKA_SASL_USERNAME`, arquivo `KAFKA_SSL_*` sob um protocolo sem TLS, e `KAFKA_SSL_KEYFILE` sem `KAFKA_SSL_CERTFILE`:

```text
1 validation error for KafkaSettings
  Value error, KAFKA_SSL_CAFILE set, but KAFKA_SECURITY_PROTOCOL=PLAINTEXT has no TLS; use SSL or SASL_SSL, or drop the file. [type=value_error]
```

Composto com `EnvironmentSettings` e `ENV=production`, o mixin recusa `SASL_PLAINTEXT` com `PLAIN` (a senha atravessa a rede em claro; SCRAM não manda a senha e passa) e senha SASL vazia:

```text
1 validation error for Settings
  Value error, ENV=production refuses development values:
- KAFKA_SASL_PASSWORD: empty under a SASL security protocol
- KAFKA_SECURITY_PROTOCOL: SASL_PLAINTEXT with the PLAIN mechanism sends the password unencrypted; use SASL_SSL [type=value_error]
```

!!! tip "Opção do FastStream que o mixin não modela"
    `MessageBroker.kafka_from_settings(settings, acks="all", enable_idempotence=True)` repassa o resto ao `KafkaBroker`. Uma chave que o `kafka_kwargs()` também devolve (`security`, `client_id`) vence a das settings.
