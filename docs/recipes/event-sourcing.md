# Event sourcing (event store)

Em vez de guardar o estado atual de um pedido numa linha que você atualiza,
você guarda **os fatos que aconteceram com ele** — `order.placed`,
`order.shipped` — numa sequência que só cresce. O estado é a soma dos fatos.
Você ganha histórico completo, auditoria de graça e read models que podem ser
reconstruídos do zero.

O SDK entrega isso em `tempest_fastapi_sdk.eventsourcing`, com **um
protocolo e dois backends**:

| Backend | Onde grava | Instalação |
| --- | --- | --- |
| `SqlEventStore` | o PostgreSQL / SQLite que o serviço já tem | nenhuma dependência nova |
| `KurrentDBEventStore` | KurrentDB (antigo EventStoreDB) | extra `[kurrentdb]` |

Código escrito contra `EventStore` — agregados, projeções — roda nos dois.

## Defina os eventos

Cada evento é uma classe Pydantic que herda `DomainEvent`. O registro
(`EventRegistry`) mapeia o nome gravado de volta para a classe, então uma
leitura devolve o mesmo tipo que foi escrito.

```python
from typing import ClassVar

from tempest_fastapi_sdk.eventsourcing import DomainEvent, EventRegistry


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class OrderShipped(DomainEvent):
    event_type: ClassVar[str] = "order.shipped"

    order_id: str


registry = EventRegistry(OrderPlaced, OrderShipped)
```

- **`event_type` é o nome no banco.** Sem ele, o nome da classe é usado —
  declare-o assim que houver evento gravado, para renomear a classe não
  deixar as linhas antigas órfãs.
- **Evento é imutável** (`frozen`). Campo desconhecido é ignorado ao ler,
  então remover um campo mantém eventos antigos legíveis; campo novo precisa
  de default pelo mesmo motivo.
- **Não é `BaseSchema` de propósito:** ela tira espaço das strings, e o
  evento gravado seria diferente do que você construiu.

## Grave e leia (backend SQL)

O backend SQL usa tabelas abstratas que você herda e nomeia, igual ao
`BaseOutboxModel` — elas entram no seu `metadata` e nas suas migrations.

```python
import asyncio
from typing import ClassVar

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    WrongExpectedVersionException,
)
from tempest_fastapi_sdk.eventsourcing import (
    BaseEventModel,
    BaseEventStreamModel,
    DomainEvent,
    EventMetadata,
    EventRegistry,
    SqlEventStore,
    StreamStateEnum,
)


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class OrderShipped(DomainEvent):
    event_type: ClassVar[str] = "order.shipped"

    order_id: str


class EventModel(BaseEventModel):
    __tablename__ = "events"


class EventStreamModel(BaseEventStreamModel):
    __tablename__ = "event_streams"


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
    await db.create_tables(BaseModel.metadata)
    store = SqlEventStore(
        db,
        registry=EventRegistry(OrderPlaced, OrderShipped),
        event_model=EventModel,
        stream_model=EventStreamModel,
    )

    result = await store.append(
        "order-42",
        [OrderPlaced(order_id="42", total_cents=990)],
        expected_version=StreamStateEnum.NO_STREAM,
        metadata=EventMetadata(correlation_id="req-1"),
    )
    print(result.version, result.position)  # 0 1

    await store.append(
        "order-42", [OrderShipped(order_id="42")], expected_version=result.version
    )
    try:
        await store.append(
            "order-42", [OrderShipped(order_id="42")], expected_version=result.version
        )
    except WrongExpectedVersionException as error:
        print(error.status_code, error.expected_version, error.actual_version)
        # 409 0 1

    for recorded in await store.read_stream("order-42"):
        print(recorded.version, recorded.event_type, recorded.event)
    # 0 order.placed order_id='42' total_cents=990
    # 1 order.shipped order_id='42'
    await db.disconnect()


asyncio.run(main())
```

Pedaço por pedaço:

- **Versões começam em `0`.** O primeiro evento de um stream é a versão `0`;
  um stream com três eventos está na versão `2`. É a mesma convenção do
  KurrentDB, então trocar de backend não muda número nenhum.
- **`expected_version` é obrigatório.** Um número diz "o stream está nesta
  versão"; `StreamStateEnum.NO_STREAM` diz "o stream ainda não existe";
  `EXISTS` exige que exista; `ANY` desliga a checagem. Não há default — a
  checagem de concorrência é uma decisão, não um esquecimento. O valor puro
  do membro (`"no_stream"`, que é o que um campo com `use_enum_values`
  guarda) vale o mesmo que o membro; string fora do enum é `ValueError`.
- **`EventMetadata`** leva `correlation_id`, `causation_id` e `extra`. O
  horário vem do store: `recorded.recorded_at`, em UTC.
- **`read_stream`** lê para frente ou para trás (`backwards=True`), a partir
  de uma versão **inclusiva** (`from_version`), com `limit`. Stream que não
  existe devolve `[]`.

## Concorrência otimista

Dois processos carregam o pedido na versão `1` e decidem coisas diferentes.
O primeiro grava; o segundo recebe `WrongExpectedVersionException` (status
`409`, code `EVENTSOURCING_WRONG_EXPECTED_VERSION`) com `expected_version` e
`actual_version`. A reação certa é **recarregar e decidir de novo** —
repetir o mesmo append às cegas apagaria a decisão do outro.

No SQL, a checagem é um `UPDATE` condicional da linha do stream
(`... WHERE version = :expected`) feito **antes** de inserir qualquer
evento. Medido com transações de verdade, em conexões separadas:

- com a primeira transação **aberta**, a segunda fica bloqueada (confirmado
  no `pg_stat_activity` do PostgreSQL 16) e, quando a primeira faz commit, é
  recusada — no SQLite (arquivo) e no PostgreSQL;
- 8 escritores com a mesma expectativa, 20 rodadas: exatamente um grava por
  rodada, nos dois bancos (`tests/eventsourcing/test_sql_concurrency.py`).

!!! warning "Um append por vez no PostgreSQL"
    A posição global (`position`) é `max + 1` sob um advisory lock de
    transação, então os eventos são numerados **na ordem de commit, sem
    buraco**. Uma sequence não daria isso — e foi medido: a transação 1
    pega a posição `1` e fica aberta, a 2 pega `2` e faz commit, e um leitor
    enxerga `2` sem `1`. Uma projeção que salvasse `2` como checkpoint nunca
    leria o `1`. O preço é que appends no store inteiro rodam um de cada vez,
    e o lock fica preso até o commit: ao passar sua própria `session=`,
    mantenha a transação curta. Sem o lock, a suíte mede appends concorrentes
    em streams diferentes colidindo na unique de `position`.

## Na transação do seu caso de uso

Todo método do `SqlEventStore` aceita `session=`. Com ela, o append entra na
**sua** transação e o commit é seu — o evento e a atualização de um read
model, ou de outra tabela, valem juntos ou não valem.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.eventsourcing import (
    DomainEvent,
    SqlEventStore,
    StreamStateEnum,
)


async def place_order(
    store: SqlEventStore, session: AsyncSession, order_id: str, event: DomainEvent
) -> None:
    await store.append(
        f"order-{order_id}",
        [event],
        expected_version=StreamStateEnum.NO_STREAM,
        session=session,
    )
    await session.commit()
```

Um conflito de `NO_STREAM` dentro da sua sessão não estraga a transação: o
insert roda num savepoint, e a suíte confere que um append seguinte na mesma
sessão ainda faz commit.

!!! note "SQLite: grave antes de ler"
    No SQLite, uma transação que já leu e tenta gravar depois que outro
    escritor fez commit falha na hora com `database is locked` — medido com
    o busy timeout de 30 s do `AsyncDatabaseManager`. O `append` grava antes
    de ler por isso; ao passar a sua `session=`, faça o append antes de
    qualquer leitura na mesma transação.

## Publicar no broker: outbox

Passe `outbox_model=` e cada evento gravado ganha uma linha de
[outbox](outbox.md) **na mesma transação**, com o payload de
`RecordedEvent.to_payload()` e o tópico igual ao `event_type` (troque com
`outbox_topic=`). O `OutboxRelay` publica (at-least-once). Um append recusado
por `expected_version` não deixa linha no outbox — a suíte confere que, depois
de um append aceito com dois eventos e um recusado, o relay publica dois.

```python
from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.db.outbox import BaseOutboxModel, OutboxRelay
from tempest_fastapi_sdk.eventsourcing import (
    BaseEventModel,
    BaseEventStreamModel,
    EventRegistry,
    SqlEventStore,
)


class EventModel(BaseEventModel):
    __tablename__ = "events"


class EventStreamModel(BaseEventStreamModel):
    __tablename__ = "event_streams"


class OutboxModel(BaseOutboxModel):
    __tablename__ = "outbox"


def build(
    db: AsyncDatabaseManager, registry: EventRegistry
) -> tuple[SqlEventStore, OutboxRelay]:
    store = SqlEventStore(
        db,
        registry=registry,
        event_model=EventModel,
        stream_model=EventStreamModel,
        outbox_model=OutboxModel,
    )

    async def publish(row: BaseOutboxModel) -> None:
        print(row.topic, row.payload["stream_id"], row.payload["version"])
        # order.placed order-42 0

    return store, OutboxRelay(db, model=OutboxModel, publish=publish)
```

## `$all`, posições e projeções

`read_all(after_position=...)` lê o log inteiro do store em ordem de posição,
**depois** da posição dada (exclusiva), e só os tipos registrados.
`subscribe_all()` faz o mesmo e continua esperando eventos novos (no SQL,
por polling; no KurrentDB, por push).

Para alimentar um read model, use `ProjectionRunner`: ele lê a partir do
checkpoint, entrega cada evento ao seu handler e avança o checkpoint.

```python
from typing import ClassVar

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.eventsourcing import (
    BaseProjectionCheckpointModel,
    DomainEvent,
    EventStore,
    ProjectionRunner,
    RecordedEvent,
    SqlCheckpointStore,
)


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class ProjectionCheckpointModel(BaseProjectionCheckpointModel):
    __tablename__ = "projection_checkpoints"


async def project_totals(store: EventStore, db: AsyncDatabaseManager) -> None:
    totals: dict[str, int] = {}

    async def project(event: RecordedEvent) -> None:
        if isinstance(event.event, OrderPlaced):
            totals[event.event.order_id] = event.event.total_cents

    runner = ProjectionRunner(
        store,
        name="order-totals",
        handler=project,
        checkpoints=SqlCheckpointStore(db, model=ProjectionCheckpointModel),
    )
    await runner.run_once()
```

- **Entrega é at-least-once.** Se o processo morrer depois do handler e antes
  do checkpoint, o evento volta. Faça o handler idempotente (upsert pelo
  `event_id` ou pela versão).
- Handler que levanta para o lote ali: o checkpoint é salvo no último evento
  que terminou, e a próxima execução recomeça no que falhou.
- `runner.run(poll_interval=..., stop_event=...)` roda em loop, no molde do
  `OutboxRelay.run`.
- Posição só se compara e se guarda. No SQL ela é `1, 2, 3...`; no KurrentDB
  é a commit position, que pula valores.

## Agregados

`Aggregate` é uma classe base fina: você implementa `apply` (como cada evento
muda o estado) e chama `record` nos métodos de comando.
`AggregateRepository` carrega reproduzindo o stream e salva com um append
cujo `expected_version` é a versão em que o agregado foi carregado.

```python
from typing import Any, ClassVar

from tempest_fastapi_sdk.eventsourcing import (
    Aggregate,
    AggregateRepository,
    DomainEvent,
    EventStore,
)


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


class OrderShipped(DomainEvent):
    event_type: ClassVar[str] = "order.shipped"

    order_id: str


class Order(Aggregate):
    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.total_cents = 0
        self.shipped = False

    def place(self, total_cents: int) -> None:
        self.record(OrderPlaced(order_id=self.aggregate_id, total_cents=total_cents))

    def ship(self) -> None:
        if self.shipped:
            raise ValueError("already shipped")
        self.record(OrderShipped(order_id=self.aggregate_id))

    def apply(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self.total_cents = event.total_cents
        elif isinstance(event, OrderShipped):
            self.shipped = True

    def snapshot(self) -> dict[str, Any] | None:
        return {"total_cents": self.total_cents, "shipped": self.shipped}

    def restore(self, state: dict[str, Any]) -> None:
        self.total_cents = int(state["total_cents"])
        self.shipped = bool(state["shipped"])


async def ship(store: EventStore, order_id: str) -> None:
    orders = AggregateRepository(store, Order, stream_prefix="order-", snapshot_every=50)
    order = await orders.load(order_id)
    if order is None:
        raise LookupError(order_id)
    order.ship()
    await orders.save(order)
```

- `load` devolve `None` para stream inexistente; um agregado novo (`Order("43")`)
  é salvo com `NO_STREAM`, então criar duas vezes o mesmo id dá `409`.
- `save` com conflito mantém os eventos pendentes no agregado.
- **Snapshots são opt-in:** `snapshot_every=50` grava um snapshot sempre que o
  stream cruza um múltiplo de 50 eventos, e `load` aplica só o que veio depois
  dele. No SQL, passe `snapshot_model=` (uma subclasse de
  `BaseEventSnapshotModel`) ao store. Um snapshot mais antigo que o gravado é
  ignorado.

## KurrentDB

```bash
uv add "tempest-fastapi-sdk[kurrentdb]"
docker run -d --name kurrentdb -p 127.0.0.1:2113:2113 \
  -e KURRENTDB_INSECURE=true kurrentplatform/kurrentdb:26.2.3
```

```python
import asyncio
from typing import ClassVar

from tempest_fastapi_sdk import WrongExpectedVersionException
from tempest_fastapi_sdk.eventsourcing import (
    DomainEvent,
    EventRegistry,
    KurrentDBEventStore,
    StreamStateEnum,
)


class OrderPlaced(DomainEvent):
    event_type: ClassVar[str] = "order.placed"

    order_id: str
    total_cents: int


async def main() -> None:
    store = KurrentDBEventStore.from_uri(
        "kurrentdb://127.0.0.1:2113?Tls=false",
        registry=EventRegistry(OrderPlaced),
    )
    try:
        first = await store.append(
            "order-42",
            [OrderPlaced(order_id="42", total_cents=990)],
            expected_version=StreamStateEnum.NO_STREAM,
        )
        print(first.version)  # 0
        try:
            await store.append(
                "order-42",
                [OrderPlaced(order_id="42", total_cents=1)],
                expected_version=StreamStateEnum.NO_STREAM,
            )
        except WrongExpectedVersionException as error:
            print(error.expected_version, error.actual_version)  # no_stream 0

        async for recorded in store.subscribe_all():
            print(recorded.stream_id, recorded.version, recorded.event)
            break
    finally:
        await store.close()


asyncio.run(main())
```

É o mesmo protocolo; o que muda por baixo:

- **Cliente oficial `kurrentdbclient`** (o `esdbclient` parou na 1.1.7, de
  abril de 2025, preso em `grpcio<1.72`). Importado só quando usado: o
  pacote `eventsourcing` importa sem o extra.
- **`WrongCurrentVersionError` do cliente vira `WrongExpectedVersionException`**,
  com o mesmo `actual_version`.
- **`after_position` é exclusivo nos dois métodos.** No cliente,
  `read_all(commit_position=p)` inclui o evento em `p` e
  `subscribe_to_all(commit_position=p)` começa depois dele — medido; o store
  esconde a diferença.
- **Só tipos registrados em `$all`.** O filtro do cliente monta
  `"^" + "|".join(padrões) + "$"`, que ancora só a primeira e a última
  alternativa; o store passa um padrão agrupado. Sem isso, registrar
  `order.placed` trazia um `order.placed.v2` não registrado do `$all`
  (medido no KurrentDB 26.2.3).
- **Metadata** usa as chaves `$correlationId` e `$causationId` do próprio
  KurrentDB, que a projeção de sistema `$by_correlation_id` lê.
- **Snapshots** moram no stream `snapshot-<stream>`, com `$maxCount: 1`.
- Subscriptions persistentes (consumidores concorrentes) não são embrulhadas:
  use `store.client` direto.

Medido com KurrentDB 26.2.3 (imagem `kurrentplatform/kurrentdb:26.2.3`) e
`kurrentdbclient` 1.3.0 (o piso declarado) e 1.3.6. Outra versão do servidor
não foi exercitada.

## Recap

- `DomainEvent` + `EventRegistry`: eventos tipados, gravados por nome.
- `EventStore.append(..., expected_version=...)`: tudo ou nada, com
  `WrongExpectedVersionException` (409) quando outro escritor chegou antes.
- `SqlEventStore`: sem dependência nova, `session=` para a sua transação,
  `outbox_model=` para publicar; no PostgreSQL, appends serializados para que
  `$all` não tenha buraco.
- `KurrentDBEventStore`: mesmo protocolo, extra `[kurrentdb]`.
- `AggregateRepository` (replay + snapshot) e `ProjectionRunner` (checkpoint
  sobre `$all`) funcionam com qualquer um dos dois.
