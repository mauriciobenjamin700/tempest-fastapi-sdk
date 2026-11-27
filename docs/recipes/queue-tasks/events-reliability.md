---
description: "Use para não perder evento que falha no consumidor; contém dead-letter, retry com atraso pelo broker, métricas, prefetch, idempotência e tracing pela fila."
---

# Confiabilidade do caminho de eventos

Use para não perder mensagem que falha: dead-letter, retry com atraso, métricas, prefetch, idempotência e tracing pela fila.

Parte da receita [Fila e Tarefas](../queue-tasks.md).

A política do consumidor é `REJECT_ON_ERROR`. Handler que levanta faz `basic.reject` com `requeue=False` — não entra em loop, e **some**. Três peças fecham isso, espelhando o que o `TaskQueue` já tem.

## Dead-letter: falha vira registro

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

## Retry com atraso, feito pelo broker

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

## Métricas

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


## Prefetch — quantas mensagens ficam em voo

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
    Sem `prefetch=`, o consumo é **sem limite** (o default é `None`) — o SDK expõe o botão, não escolhe o valor. Valor pequeno demais serializa o consumo e derruba o throughput; grande demais recria o problema. O número certo depende da latência do seu handler, e fixar um sem medir seria o mesmo erro que o `DEFAULT_INTRA_OP_THREADS` do modelops cometeu antes de ser rejustificado. Meça com um consumidor de latência conhecida antes de escolher.

!!! info "Prefetch não é concorrência do handler"
    Prefetch limita quantas mensagens o **broker entrega** sem ack. Quantas corrotinas rodam ao mesmo tempo é outra coisa. Confundir os dois é comum: `prefetch=1` não serializa o handler se você mesmo dispara tarefas em paralelo dentro dele.

!!! check "Publisher confirms já vêm ligados"
    O `Channel` do FastStream tem `publisher_confirms=True` por padrão, então um publish perdido em restart do broker não passa silencioso. Está pinado por teste.

## Idempotência no consumo

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

## Tracing e request id atravessando a fila

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
