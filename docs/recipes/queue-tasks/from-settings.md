# Transporte por settings e outbox

Use para escolher o transporte em uma linha com `from_settings` e publicar de forma atômica com o `INSERT` (outbox).

Parte da receita [Fila e Tarefas](../queue-tasks.md).

## Uma linha para o transporte — `from_settings`

O bloco que todo serviço reescrevia decidia três coisas: qual transporte a URL significa, se resultado é guardado, e o que uma URL vazia faz. `TaskQueue.from_settings` decide as três:

```python
# src/tasks/__init__.py

from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings

tq: TaskQueue = TaskQueue.from_settings(settings)
```

| `TASKIQ_BROKER_URL` | Broker | Resultado |
| --- | --- | --- |
| `redis://` / `rediss://` | Redis Streams | na mesma URL, com TTL |
| `amqp://` / `amqps://` | RabbitMQ | no `TASKIQ_RESULT_BACKEND_URL`, se houver, com TTL |
| vazio | in-memory — `@tq.task` continua registrando, e `enqueue` roda a tarefa neste processo em vez de entregar a um worker | in-memory |
| qualquer outro esquema | `ValueError` nomeando o esquema | — |

!!! check "Passa no `mypy --strict` sem `type: ignore`"
    Os campos de `TaskIQSettingsLike` são `@property` (somente leitura),
    então o `Settings` congelado que os mixins montam
    (`class Settings(TaskIQSettings, BaseAppSettings)`) é aceito — e um
    objeto simples de teste também. E `@tq.task` / `@tq.task(...)` devolvem
    `Task[P, R]` com a assinatura da função: sob `--strict`, `reveal_type`
    de uma `async def add(a: int, b: int) -> int` decorada mostra
    `Task[[a: int, b: int], int]`, e `await add.run("x", 1)` é erro de
    `arg-type`. Antes, `from_settings(Settings())` era recusado
    (`expected settable variable, got read-only attribute`) e
    `@tq.task(name=...)` era `untyped-decorator`.

!!! danger "Resultado guardado tem TTL — antes não tinha"
    O `taskiq-redis` grava resultado **sem expiração** por conta própria
    (`result_ex_time=None`, `result_px_time=None`, `keep_results=True`).
    Medido contra Redis 8.2.9, com todos os defaults dele:

    ```text
    chaves escritas: ['task-abc-123']      # o id da task, pelado
    TTL:             [-1]                  # -1 = nunca expira
    MEMORY USAGE:    [144] bytes           # task devolvendo None
    ```

    Uma task `@tq.cron(CronEnum.EVERY_MINUTE)` deixa **1440 chaves por dia**,
    crescendo sem teto. Num Redis compartilhado com `allkeys-lru`, esse
    crescimento passa a **evictar as chaves de quem está usando o Redis
    para trabalhar** — rate limit, marcador de idempotência, o lease do
    scheduler — e o serviço que nunca lê resultado nenhum paga a conta
    inteira.

    O SDK passa a aplicar `TASKIQ_RESULT_TTL_SECONDS` (default `86400`,
    um dia — o mesmo do `result_expires` do Celery) e o prefixo
    `tempest:results`, nos **dois** transportes. Medido depois:

    ```text
    chaves escritas: ['tempest:results:abc-123']
    TTL:             [86400]
    ```

    Quem precisa do comportamento antigo pede explicitamente:
    `TASKIQ_RESULT_TTL_SECONDS=0`, ou
    `TaskQueue.redis(url, result_ttl_seconds=0)`.

!!! danger "Resultado é gravado em JSON, não em pickle — mudança de comportamento"
    Sozinho, o `taskiq_redis` grava resultado e progresso com
    `PickleSerializer`, e ler um pickle executa o código que os bytes
    guardados nomeiam: quem consegue escrever nas chaves
    `tempest:results:*` (outro serviço no mesmo Redis, uma credencial
    vazada) ganha execução de código no processo que chama
    `get_result()` / `wait_result()`. Por isso o `TaskQueue` usa
    `JSONSerializer` em `redis()`, `from_settings()` e no backend de
    resultado do RabbitMQ.

    O valor que volta é o mesmo nos dois: o TaskIQ converte o resultado com
    `model_dump(mode="json")` **antes** de serializar, então `datetime` e
    `UUID` já voltavam como `str`, `set`/`tuple` como `list`, e um objeto
    que o pydantic não serializa já falhava. Exceção de task também volta
    com a classe original. O que muda:

    - resultado gravado por uma versão anterior (pickle) não é lido pela
      nova até expirar (`TASKIQ_RESULT_TTL_SECONDS`, 24 h por padrão) —
      `get_result()` falha ao decodificar;
    - valor que não vira JSON continua falhando ao gravar: medido com
      taskiq 0.12.4 e taskiq-redis 1.2.3, um objeto arbitrário levanta
      `PydanticSerializationError` no `model_dump` com os **dois**
      serializers, e `bytes` que não são UTF-8 levantam
      `UnicodeDecodeError` com os dois. Quem dependia do pickle para outra
      forma de valor passa `result_serializer="pickle"`.

    Quem tem um Redis que só o próprio serviço escreve e precisa do pickle
    pede explicitamente: `TaskQueue.redis(url, result_serializer="pickle")`
    ou `TaskQueue.from_settings(settings, result_serializer="pickle")`.

!!! tip "Serviço só de cron não precisa guardar resultado"
    `TASKIQ_STORE_RESULTS=False` deixa o `DummyResultBackend` do TaskIQ
    no lugar, nos dois transportes. É a configuração de um serviço que só
    roda cron: não há chamador esperando retorno, então cada resultado
    guardado é custo sem leitor.

    Isso também acerta uma assimetria entre os transportes.
    `TASKIQ_RESULT_BACKEND_URL` vazio significava "sem resultado" no
    RabbitMQ e "resultado na URL do broker" no Redis — o mesmo ambiente,
    duas leituras. Agora quem decide é o flag, igual nos dois.

!!! warning "Argumento de result backend não vai pelo `**options`"
    `TaskQueue.redis(url, result_ex_time=3600)` é **aceito na
    construção** — o `RedisStreamBroker` engole chave desconhecida nos
    `**connection_kwargs` dele — e só explode depois, no `connect()`:

    ```text
    TypeError: AbstractConnection.__init__() got an unexpected keyword
    argument 'result_ex_time'
    ```

    Medido: o backend fica com `result_ex_time=None`. Por isso TTL e
    prefixo são parâmetros nomeados (`result_ttl_seconds`,
    `result_prefix`), não chave de `**options`.

!!! warning "O default do mixin é uma URL AMQP, não vazio"
    `TaskIQSettings.TASKIQ_BROKER_URL` tem default
    `"amqp://guest:guest@localhost:5672/"`. Quem quer o fallback
    in-memory em dev **declara `TASKIQ_BROKER_URL=` vazio** no `.env` —
    não basta omitir. E esquema desconhecido levanta em vez de cair para
    memória de propósito: uma URL com typo num ambiente deployado viraria
    uma fila que parece saudável enquanto nada é enfileirado.

!!! check "Redis agora guarda resultado"
    Até a v0.281.0 o `TaskQueue.redis()` deixava o `DummyResultBackend`
    do TaskIQ no lugar e não oferecia jeito de pedir outro, então ler
    resultado pela fachada era impossível — medido:

    ```text
    broker: RedisStreamBroker
    result backend: DummyResultBackend
    ```

    Agora o default é guardar na URL do broker. `results="redis://..."`
    manda pra outro lugar, `results=False` restaura o comportamento
    anterior. Requer o extra **`[tasks-redis]`** (novo na v0.282.0) —
    `taskiq-redis` não estava declarado em extra nenhum.

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

O guia completo — modelo, service produtor com `save_with_outbox`, retenção e concorrência — está na receita dedicada em **[Outbox](../outbox.md)**.
