# Carteira (saldo e extrato)

Todo produto que repassa dinheiro para um usuário — motorista, vendedor,
criador — acaba escrevendo uma carteira: saldo, extrato, retenção. E os
passos que dão errado dão errado do mesmo jeito em todo produto:

- **crédito perdido em concorrência**: duas vendas liquidadas ao mesmo
  tempo leem o mesmo saldo, somam em Python e gravam o valor — uma delas
  some;
- **débito que paga duas vezes**: dois saques simultâneos leem o mesmo
  saldo e passam pela mesma checagem;
- **dinheiro retido gasto antes da hora**: o "disponível" é calculado em
  Python e o débito só confere `saldo >= valor`;
- **webhook entregue duas vezes credita duas vezes.**

O módulo `tempest_fastapi_sdk.wallet` resolve os quatro com um desenho só:

- o **saldo** é uma coluna inteira (centavos) na linha do usuário, movida
  **só** por `UPDATE` condicional com `RETURNING` — sem leitura antes;
- o **extrato** é uma tabela append-only: cada movimento grava o valor
  com sinal, o saldo resultante e o evento que o causou;
- o **retido** é a soma das linhas do extrato cuja liberação ainda está
  no futuro, calculada **dentro** do `WHERE` do débito.

!!! info "Sem extra"
    Só o núcleo do SDK: SQLAlchemy já vem na instalação base.

## O modelo de usuário ganha um saldo

Misture `WalletBalanceMixin` no seu modelo de usuário. Ele adiciona
`wallet_cents: int` (`NOT NULL`, default `0`) com um
`CHECK (wallet_cents >= 0)`:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.wallet import WalletBalanceMixin


class UserModel(WalletBalanceMixin, BaseModel):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(64))
```

O `CHECK` fica no banco, não no Python: nem um `UPDATE` escrito à mão fora
do serviço consegue deixar o saldo negativo. O nome da constraint segue a
convenção do SDK — `ck_users_wallet_cents_non_negative` — para uma
migration conseguir nomeá-la.

!!! tip "Precisa de saldo negativo?"
    Estorno de um crédito que o usuário já sacou (um chargeback, por
    exemplo) só tem duas saídas: recusar ou registrar a dívida. Com
    `WalletBalanceMixin`, `reverse` recusa com
    `WalletInsufficientFundsException`. Com `OverdraftWalletBalanceMixin`
    — a mesma coluna, sem o `CHECK` — o estorno é gravado e o saldo fica
    negativo. Os débitos normais continuam sem estourar nos dois casos:
    `debit` só casa a linha quando o **disponível** cobre o valor.

## O extrato

O SDK entrega a linha abstrata, `BaseWalletEntryModel`; o projeto entrega
a concreta, com a FK para o usuário. Para testes e scripts, a fábrica
monta uma:

```python
from tempest_fastapi_sdk.wallet import make_wallet_entry_model

WalletEntryModel = make_wallet_entry_model(
    user_table="users",
    tablename="wallet_entries",
)
```

Cada linha guarda:

| Coluna | O que é |
| --- | --- |
| `kind` | `"credit"`, `"debit"`, `"reversal"` ou um tipo do app |
| `amount_cents` | valor **com sinal**: positivo soma, negativo tira |
| `balance_after_cents` | o saldo logo depois deste movimento |
| `available_at` | quando o movimento deixa de estar retido |
| `reference_type` / `reference_id` | o evento que causou (`"order"` + id do pedido) |
| `description` | texto livre para o extrato |
| `idempotency_key` | chave opcional do chamador, única quando presente |

A tabela já sai com `UNIQUE (reference_type, reference_id, kind)` e um
índice em `(user_id, available_at)` — o formato exato da subquery do
retido. A FK da fábrica é `ON DELETE RESTRICT`: linha de extrato é
registro financeiro, e apagar um usuário que ainda tem uma é recusado em
vez de cascatear.

!!! warning "Subclasse à mão estende `__table_args__`"
    Escrevendo a classe concreta você mesmo, e precisando de mais
    `__table_args__`, some aos do pai (`super().__table_args__`) em vez de
    substituir. É a unique da referência que transforma um evento
    repetido em no-op.

## Juntando: crédito, retenção, débito, extrato

Um exemplo completo, que roda como está:

```python
import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseModel, BaseRepository
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletInsufficientFundsException,
    WalletRepository,
    WalletService,
    make_wallet_entry_model,
)


class UserModel(WalletBalanceMixin, BaseModel):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(64))


WalletEntryModel = make_wallet_entry_model(
    user_table="users",
    tablename="wallet_entries",
)


def build_wallet(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=WalletRepository(
            session,
            model=UserModel,
            entry_model=WalletEntryModel,
        ),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        driver = UserModel(name="Ana")
        session.add(driver)
    driver_id: UUID = driver.id

    async with db.get_session_context() as session:
        wallet = build_wallet(session)
        order_id = uuid4()
        await wallet.credit(
            driver_id,
            3_330,
            reference_type="order",
            reference_id=order_id,
            hold=timedelta(hours=48),
            description="Order paid",
        )
        replay = await wallet.credit(
            driver_id,
            3_330,
            reference_type="order",
            reference_id=order_id,
            hold=timedelta(hours=48),
        )
        print("replay balance_after:", replay.balance_after_cents)

        balance = await wallet.balance(driver_id)
        print("total:", balance.total_cents)
        print("held:", balance.held_cents)
        print("available:", balance.available_cents)

        try:
            await wallet.debit(
                driver_id,
                1_000,
                reference_type="withdraw",
                reference_id=uuid4(),
            )
        except WalletInsufficientFundsException as exc:
            print("refused:", exc.code)

        page = await wallet.statement(driver_id)
        print("lines:", page.total)

    await db.disconnect()


asyncio.run(main())
```

Saída:

```text
replay balance_after: 3330
total: 3330
held: 3330
available: 0
refused: WALLET_INSUFFICIENT_FUNDS
lines: 1
```

Pedaço por pedaço:

- **`credit(..., hold=timedelta(hours=48))`** soma R$ 33,30 ao saldo e
  grava a linha com `available_at` 48 horas à frente. O dinheiro está na
  carteira, mas retido.
- **O segundo `credit` com o mesmo `reference_id`** é o webhook entregue
  de novo. Ele devolve a linha que o primeiro gravou e não move nada — o
  saldo continua 3330 e o extrato tem **uma** linha.
- **`balance`** separa o total (3330) do retido (3330) e do disponível
  (0), numa consulta só. `next_release_at` diz quando o próximo crédito
  retido libera.
- **`debit` de R$ 10,00** é recusado: o disponível é zero. O
  `WalletInsufficientFundsException` responde 409 com o code
  `WALLET_INSUFFICIENT_FUNDS`, traduzido em PT-BR e EN-US pelo catálogo
  do SDK.
- **`statement`** devolve um `BasePaginationSchema[WalletEntrySchema]`,
  do mais novo para o mais antigo. Carteira sem movimento devolve
  `items=[]`, nunca erro.

!!! note "O replay deixa um `WARNING` no log"
    O caminho de replay passa pelo `INSERT` recusado pela unique — é ele
    que torna o replay seguro também em concorrência (abaixo). O
    `BaseRepository.add` registra cada recusa em `WARNING`
    (`IntegrityError on WalletEntryModel.add: unique violation ...`), então
    um webhook reentregue aparece no log uma vez por reentrega.

## Mesma referência, outro valor: conflito

Replay é a **mesma** referência com o **mesmo** dono e o **mesmo** valor.
A mesma referência com outro valor, ou para outra carteira, não é replay —
são dois eventos disputando uma identidade, e o serviço não escolhe um no
chute: levanta `WalletReferenceConflictException` (409,
`WALLET_REFERENCE_CONFLICT`) e não move nada.

O `idempotency_key` opcional é uma segunda chave de replay, para o
movimento que não tem evento natural — um ajuste manual vindo de uma
requisição com header `Idempotency-Key`, por exemplo.

## Estorno

`reverse(entry_id)` desfaz uma linha com uma linha oposta, de
`kind="reversal"`, que referencia a original. O extrato continua
append-only — a original fica. Estornar a mesma linha duas vezes é replay:

- **estorno de débito** (o reembolso de um saque recusado) devolve o
  valor, disponível na hora;
- **estorno de crédito** é um débito **forçado**: ignora a retenção,
  porque o dinheiro que ele tira é exatamente o que o crédito pôs. Crédito
  ainda retido é estornado com o mesmo `available_at` dele, e sai do
  retido junto.

## Junto com o seu domínio: `claim_once`

O crédito de uma venda não deveria acontecer duas vezes, e quem sabe
disso é a linha do **seu** pedido. `claim_once` marca uma coluna anulável
só se ela ainda está `NULL`, num `UPDATE` só:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository, transaction
from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletRepository,
    WalletService,
    make_wallet_entry_model,
)


class SellerModel(WalletBalanceMixin, BaseModel):
    __tablename__ = "sellers"


class OrderModel(BaseModel):
    __tablename__ = "orders"

    seller_id: Mapped[UUID]
    total_cents: Mapped[int]
    credited_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


SellerEntryModel = make_wallet_entry_model(
    user_table="sellers",
    tablename="seller_wallet_entries",
)


async def settle(session: AsyncSession, order: OrderModel) -> bool:
    balances = WalletRepository(
        session,
        model=SellerModel,
        entry_model=SellerEntryModel,
    )
    wallet = WalletService(
        balances=balances,
        entries=BaseRepository(session, model=SellerEntryModel),
    )
    async with transaction(session):
        if not await balances.claim_once(OrderModel, order.id, "credited_at"):
            return False
        await wallet.credit(
            order.seller_id,
            order.total_cents,
            reference_type="order",
            reference_id=order.id,
        )
    return True
```

A primeira chamada marca `credited_at` e credita; toda chamada depois —
webhook reentregue, job repetido — recebe `False` e não faz nada. As duas
escritas estão no mesmo `transaction()`, então uma falha no crédito
devolve o claim junto.

## Por que um `UPDATE` só

Cada método de escrita do `WalletRepository` é **um**
`UPDATE ... WHERE ... RETURNING`. O débito chega ao PostgreSQL 17 neste
formato — capturado com `before_cursor_execute` sobre as tabelas da
suíte, com os nomes trocados pelos do exemplo e reindentado; `$1` é o
valor já com sinal negativo:

```sql
UPDATE users
SET wallet_cents = (users.wallet_cents + $1::INTEGER),
    updated_at = $2::TIMESTAMP WITH TIME ZONE
WHERE users.id = $3::UUID
  AND users.wallet_cents - (
      SELECT coalesce(sum(wallet_entries.amount_cents), $4::INTEGER)
      FROM wallet_entries
      WHERE wallet_entries.user_id = users.id
        AND wallet_entries.available_at > $5::TIMESTAMP WITH TIME ZONE
  ) >= $6::INTEGER
RETURNING users.wallet_cents, users.id
```

Não existe leitura antes, então não existe janela entre "conferir" e
"gravar" para outra requisição cair dentro. Nenhuma linha devolvida
significa recusa, e o serviço só então pergunta se a carteira existe,
para responder 404 em vez de 409.

Medido com a intercalação **forçada** — a primeira requisição escreve e
segura a transação aberta por 20 ms; a segunda só começa depois do sinal
da primeira, então sempre roda com a primeira ainda sem commit —, 50
corridas por cenário, em SQLite e em PostgreSQL 17:

| Formato | SQLite | PostgreSQL |
| --- | --- | --- |
| lê o saldo, soma em Python, grava o valor | 50/50 créditos perdidos | 50/50 créditos perdidos |
| o mesmo com `SELECT ... FOR UPDATE` | 50/50 créditos perdidos | 0/50 |
| `WalletService.credit` × 2 | 0/50 | 0/50 |
| `WalletService.debit` × 2 contra o mesmo saldo | 0/50 pagos duas vezes | 0/50 pagos duas vezes |
| `debit` × 2 de 500 com 1000 no saldo, 500 retidos | 0/50 gastaram o retido | 0/50 gastaram o retido |
| o mesmo webhook entregue duas vezes ao mesmo tempo | 0/50 creditados duas vezes | 0/50 creditados duas vezes |

As linhas de SQLite com perda são do engine na configuração padrão do
driver, onde a perda é calada: as duas requisições fazem commit e um
crédito some. Com o `BEGIN` explícito que o `AsyncDatabaseManager` liga, a
mesma corrida (sem lock) falha alto: em 46 de 50 corridas uma das
requisições recebeu `database is locked` e o crédito dela não entrou; nas
outras 4 a conexão devolvida ao pool ficou inutilizável para o próximo
`BEGIN`. Mais seguro que calado, mas o crédito continua perdido.

!!! danger "`FOR UPDATE` não existe no SQLite"
    O dialeto SQLite do SQLAlchemy compila `select(...).with_for_update()`
    **sem** a cláusula, sem erro e sem aviso. Um desenho que depende do
    lock está certo em produção e errado no banco de teste — exatamente
    onde ninguém olha. O `UPDATE` condicional se comporta igual nos dois,
    por isso a carteira não usa lock.

Os números saem de `tests/test_wallet_concurrency_guard.py`, que roda em
SQLite sempre e em PostgreSQL no `make test-docker` (ou com
`TEST_POSTGRES_URL`). O `tests/test_wallet_update_shape_guard.py` fixa a
propriedade por trás deles: todo método de escrita emite exatamente um
`UPDATE` com `WHERE`.

## O que vem depois

Este é o primeiro passo da issue #400. Os próximos, **ainda não
entregues**, são o saque PIX (debitar o disponível, criar e aprovar a
transferência, devolver o saldo com uma linha de estorno quando o PIX
recusar), as faixas de taxa e a divisão em basis points, e um router
HTTP opcional. Até lá, o fluxo de saque se monta com `debit` +
`reverse`.

## Recap

- Saldo é `wallet_cents` na linha do usuário (`WalletBalanceMixin`, ou
  `OverdraftWalletBalanceMixin` para aceitar dívida no estorno).
- Extrato é append-only (`BaseWalletEntryModel` /
  `make_wallet_entry_model`), com `UNIQUE (reference_type, reference_id,
  kind)`.
- `WalletService.credit` / `debit` / `reverse` movem o saldo e gravam a
  linha na mesma transação; o mesmo evento repetido devolve a linha que
  já existe.
- O retido é descontado **dentro** do `UPDATE` do débito.
- `claim_once` marca a linha do seu domínio uma vez só.
- Tudo em centavos inteiros.
