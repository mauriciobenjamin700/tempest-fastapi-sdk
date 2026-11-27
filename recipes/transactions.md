# Transações (commit e savepoint)

Todo método de escrita do `BaseRepository` faz `COMMIT` sozinho. Para uma
escrita só isso é o certo — e para uma regra de negócio com duas escritas
é exatamente o problema:

```python
from src.db.models import OrderItemModel, OrderModel
from tempest_fastapi_sdk import BaseRepository


async def create_order_unsafe(
    orders: BaseRepository[OrderModel],
    items: BaseRepository[OrderItemModel],
    order: OrderModel,
    rows: list[OrderItemModel],
) -> None:
    """Grava pedido e itens sem atomicidade — o problema, não a solução.

    Args:
        orders (BaseRepository[OrderModel]): Repositório de pedidos.
        items (BaseRepository[OrderItemModel]): Repositório de itens.
        order (OrderModel): O pedido a gravar.
        rows (list[OrderItemModel]): Os itens do pedido.
    """
    await orders.add(order)      # já está no banco
    await items.add_all(rows)    # se isto falhar, o pedido ficou órfão
```

Esta página mostra como fechar esse buraco.

## O bloco `transaction()`

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderItemModel, OrderModel
from tempest_fastapi_sdk import BaseRepository, transaction


async def create_order(
    session: AsyncSession,
    orders: BaseRepository[OrderModel],
    items: BaseRepository[OrderItemModel],
    order: OrderModel,
    rows: list[OrderItemModel],
) -> OrderModel:
    """Grava pedido e itens de forma atômica.

    Args:
        session (AsyncSession): A sessão compartilhada pelos repositórios.
        orders (BaseRepository[OrderModel]): Repositório de pedidos.
        items (BaseRepository[OrderItemModel]): Repositório de itens.
        order (OrderModel): O pedido a gravar.
        rows (list[OrderItemModel]): Os itens do pedido.

    Returns:
        OrderModel: O pedido gravado.
    """
    async with transaction(session):
        await orders.add(order)
        await items.add_all(rows)
    return order
```

Dentro do bloco, cada escrita faz `flush` em vez de `COMMIT`. Saída limpa
resulta em **um** `COMMIT`; qualquer exceção resulta em **um** `ROLLBACK`
e a exceção continua subindo.

!!! tip "O contador vive na sessão, não no repositório"
    Repare que `items` também respeita o bloco, mesmo sem ninguém ter
    chamado nada nele. O controle é guardado em `session.info`, então
    **todo** repositório ligado à mesma `AsyncSession` participa do mesmo
    bloco. É isso que faz um serviço orquestrando vários repositórios
    funcionar sem passar contexto de um para o outro.

O repositório também expõe o bloco como açúcar:

```python
from src.db.models import OrderItemModel, OrderModel
from tempest_fastapi_sdk import BaseRepository


async def create_order_with_sugar(
    orders: BaseRepository[OrderModel],
    items: BaseRepository[OrderItemModel],
    order: OrderModel,
    rows: list[OrderItemModel],
) -> None:
    """Abre o bloco pelo repositório em vez de pela sessão.

    Args:
        orders (BaseRepository[OrderModel]): Repositório de pedidos.
        items (BaseRepository[OrderItemModel]): Repositório de itens.
        order (OrderModel): O pedido a gravar.
        rows (list[OrderItemModel]): Os itens do pedido.
    """
    async with orders.transaction():
        await orders.add(order)
        await items.add_all(rows)   # mesma sessão, mesmo bloco
```

## `commit()` explícito

Às vezes o ponto durável é uma decisão da regra de negócio, e chamar
`update()` só pelo efeito colateral do commit é desonesto. Use
`commit()`:

```python
from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository


async def persist(
    orders: BaseRepository[OrderModel],
    order: OrderModel,
) -> None:
    """Declara o ponto durável em vez de deduzi-lo de um `update`.

    Args:
        orders (BaseRepository[OrderModel]): Repositório de pedidos.
        order (OrderModel): O pedido a gravar.
    """
    await orders.add(order)
    await orders.commit()
```

`commit()` dentro de um bloco `transaction()` aberto faz `flush` em vez de
commitar, porque commitar ali quebraria a garantia de tudo-ou-nada do
bloco. Isso é o que torna a chamada segura de deixar no lugar quando
alguém, depois, envolver esse código num bloco — um `session.commit()`
cru não tem essa propriedade.

Também existem `flush()` (torna a linha visível para as próximas queries
da mesma transação, sem commitar) e `rollback()`.

!!! warning "`rollback()` dentro de um bloco é recusado"
    Um `rollback()` ali descartaria o bloco inteiro — inclusive escritas
    de outros repositórios da mesma sessão — enquanto quem chamou acredita
    estar desfazendo só o próprio passo. O método levanta `RuntimeError`
    explicando isso. Para abortar o bloco, deixe a exceção subir; para um
    passo do qual você pretende se recuperar, use `savepoint()`.

## Repositório sempre explícito: `autocommit=False`

Quando um repositório inteiro pertence a uma unidade de trabalho do
chamador:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository


async def persist_explicitly(session: AsyncSession, order: OrderModel) -> None:
    """Constrói um repositório cujo commit é sempre do chamador.

    Args:
        session (AsyncSession): A sessão a usar.
        order (OrderModel): O pedido a gravar.
    """
    repository: BaseRepository[OrderModel] = BaseRepository(
        session, model=OrderModel, autocommit=False
    )

    await repository.add(order)   # só flush
    await repository.commit()     # você decide quando
```

O flag desliga apenas o commit **implícito** de dentro dos métodos de
escrita. Um `commit()` explícito continua commitando.

## `savepoint()`: falhar sem perder o resto

Um `SAVEPOINT` de verdade. A falha reverte só o trecho aninhado e a
transação em volta continua utilizável:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AccountModel, AuditEntryModel, NicknameModel
from tempest_fastapi_sdk import (
    BaseRepository,
    ConflictException,
    savepoint,
    transaction,
)


async def open_account(
    session: AsyncSession,
    accounts: BaseRepository[AccountModel],
    nicknames: BaseRepository[NicknameModel],
    audit: BaseRepository[AuditEntryModel],
    account: AccountModel,
    nickname: NicknameModel,
    entry: AuditEntryModel,
) -> None:
    """Abre a conta mesmo quando o apelido já está em uso.

    Args:
        session (AsyncSession): A sessão compartilhada.
        accounts (BaseRepository[AccountModel]): Repositório de contas.
        nicknames (BaseRepository[NicknameModel]): Repositório de apelidos.
        audit (BaseRepository[AuditEntryModel]): Repositório de auditoria.
        account (AccountModel): A conta a criar.
        nickname (NicknameModel): O apelido pretendido.
        entry (AuditEntryModel): O registro de auditoria.
    """
    async with transaction(session):
        await accounts.add(account)
        try:
            async with savepoint(session):
                await nicknames.add(nickname)
        except ConflictException:
            pass          # a conta sobrevive; só o apelido foi revertido
        await audit.add(entry)
```

Sem o savepoint, o `ConflictException` capturado deixaria a sessão em
estado inválido — o SQLAlchemy exige um rollback depois de um flush que
falhou.

!!! info "SQLite precisa de uma configuração, e o SDK já aplica"
    O driver `pysqlite` sob o `aiosqlite` abre transações implicitamente e
    não emite `BEGIN`. O SQLite então enxerga o `SAVEPOINT` como a
    transação mais externa e o `RELEASE SAVEPOINT` correspondente vira um
    **commit** — um bloco aninhado que sai sem erro fica durável mesmo que
    o bloco externo seja revertido depois. O
    `AsyncDatabaseManager` aplica o remédio documentado do SQLAlchemy em
    toda engine SQLite que cria. Se você monta a engine na mão, chame
    `enable_sqlite_savepoints(engine)`.

## Aninhamento

Blocos `transaction()` são reentrantes: o interno entra no externo e não
commita sozinho. Só a saída do mais externo commita. Isso deixa um
serviço chamar outro sem nenhum dos dois saber quem abriu o bloco.

## Recapitulando

- `transaction(session)` agrupa tudo em um `COMMIT`; o contador fica na
  sessão, então todos os repositórios dela participam.
- `commit()` / `flush()` / `rollback()` no repositório evitam que o
  serviço toque em `session`; `commit()` vira `flush` dentro de um bloco.
- `autocommit=False` deixa um repositório inteiro explícito.
- `savepoint()` isola um passo do qual você quer se recuperar.
- `rollback()` dentro de um bloco levanta `RuntimeError` de propósito.
