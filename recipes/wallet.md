# Carteira e saque Pix

Plataforma que recebe por Pix e repassa para alguém — motorista, artista,
vendedor — precisa de três coisas: um saldo por pessoa, o histórico de cada
centavo que entrou e saiu, e um botão de saque. Parece pouco código, e é
exatamente por isso que dá errado do mesmo jeito em todo produto:

- **crédito perdido**: ler o saldo em Python, somar e gravar de volta perde
  um dos créditos quando duas vendas do mesmo motorista liquidam ao mesmo
  tempo;
- **saque em dobro**: ler o saldo, chamar o Pix e só então zerar paga duas
  vezes quando a pessoa aperta o botão duas vezes;
- **retenção furada**: calcular "o disponível" antes de debitar deixa dois
  saques simultâneos levarem dinheiro que ainda devia ficar retido;
- **dinheiro em `float`**: `33.30 - 30.13` vira `3.169999999999998`.

O módulo `tempest_fastapi_sdk.wallet` resolve as quatro uma vez. Todo valor
é **centavo inteiro**.

## Os modelos

O saldo mora na linha do seu usuário, e cada movimento vira uma linha de
extrato:

```python
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseUserModel
from tempest_fastapi_sdk.wallet import BaseWalletEntryModel, WalletBalanceMixin


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


class WalletEntryModel(BaseWalletEntryModel):
    __tablename__ = "wallet_entries"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
```

- `WalletBalanceMixin` acrescenta `wallet_cents` (`BIGINT`, default `0`).
- `BaseWalletEntryModel` traz `kind`, `amount_cents` (com sinal: positivo
  entra, negativo sai), `balance_after_cents`, `available_at` (quando o
  crédito pode ser sacado), `description`, `reference_type` e
  `reference_id`. Você só declara a FK do usuário e o nome da tabela.

!!! warning "`RESTRICT`, não `CASCADE`"
    Com `CASCADE`, apagar o usuário apaga o histórico financeiro junto. Com
    `RESTRICT`, o banco recusa — desative ou anonimize o usuário em vez de
    apagar. `make_wallet_entry_model()` (para teste e script) usa
    `RESTRICT` por padrão.

## Creditar, com e sem retenção

```python
import asyncio
from datetime import timedelta
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseRepository, BaseUserModel
from tempest_fastapi_sdk.wallet import (
    BaseWalletEntryModel,
    WalletBalanceMixin,
    WalletService,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


class WalletEntryModel(BaseWalletEntryModel):
    __tablename__ = "wallet_entries"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def wallet_service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="motorista@example.com", hashed_password="x")
        session.add(user)
        await session.commit()

        service = wallet_service(session)
        await service.credit(user.id, 9_320, kind="TICKET_SALE")
        await service.credit(
            user.id, 4_500, kind="TICKET_SALE", hold=timedelta(hours=48)
        )

        balance = await service.balance(user.id)
        print(balance.total_cents, balance.held_cents, balance.available_cents)
    await db.disconnect()


asyncio.run(main())
```

Saída:

```text
13820 4500 9320
```

- `WalletService` recebe **dois repositórios na mesma sessão**: o do dono do
  saldo e o do extrato. Sessões diferentes são recusadas no construtor,
  porque o saldo e a linha de extrato precisam entrar no mesmo commit.
- `credit()` faz um único `UPDATE ... SET wallet_cents = wallet_cents + :n
  RETURNING`, e grava a linha de extrato na mesma transação. Nada é lido em
  Python antes.
- `hold=timedelta(hours=48)` grava `available_at` 48 horas à frente. O
  crédito conta no total, mas não no disponível, até lá.
- `kind` é vocabulário seu (`"TICKET_SALE"`, `"ALO_SALE"`). Os que o
  service escreve sozinho estão em `WalletEntryKindEnum`.

!!! info "Medido com concorrência de verdade"
    `tests/wallet/test_wallet_live.py` roda 50 tarefas simultâneas, cada
    uma na própria sessão, contra Postgres num container. 50 créditos de
    `100` terminam em `5000`, sempre; e o teste de controle, rodando no
    mesmo cenário a versão ingênua (ler, somar, gravar), termina abaixo de
    `5000`. Medido em `postgres:16-alpine`: 5 execuções seguidas de
    `pytest tests/wallet/test_wallet_live.py -m docker`, os 4 testes
    passando nas cinco.

## Sacar

O saque é a parte com mais jeito de dar errado, então a ordem é fixa:

1. **debita primeiro**, só do disponível — a retenção faz parte do `WHERE`
   do `UPDATE`, então dois saques simultâneos não passam os dois;
2. **chama o provedor** depois do commit do débito;
3. **devolve o dinheiro só se o provedor recusou de forma definitiva**.

```python
import asyncio
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    PayoutRejectedException,
)
from tempest_fastapi_sdk import PixKeyType
from tempest_fastapi_sdk.testing.fakes import FakePayoutProvider
from tempest_fastapi_sdk.wallet import (
    BaseWalletEntryModel,
    WalletBalanceMixin,
    WalletService,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


class WalletEntryModel(BaseWalletEntryModel):
    __tablename__ = "wallet_entries"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def wallet_service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="motorista@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        service = wallet_service(session)
        await service.credit(user.id, 9_320, kind="TICKET_SALE")

        payout = FakePayoutProvider()
        payout.fail_next(PayoutRejectedException("chave Pix inexistente"))
        try:
            await service.withdraw(
                user.id,
                payout=payout,
                pix_key="motorista@example.com",
                pix_key_type=PixKeyType.EMAIL,
            )
        except PayoutRejectedException:
            print("recusado:", (await service.balance(user.id)).available_cents)

        result = await service.withdraw(
            user.id,
            payout=payout,
            pix_key="motorista@example.com",
            pix_key_type=PixKeyType.EMAIL,
        )
        print("pago:", result.entry.amount_cents, result.payout.status)
        print("saldo:", (await service.balance(user.id)).total_cents)

        page = await service.statement(user.id)
        print([entry.kind for entry in page["items"]])
    await db.disconnect()


asyncio.run(main())
```

Saída:

```text
recusado: 9320
pago: -9320 confirmed
saldo: 0
['WITHDRAW', 'WITHDRAW_REFUND', 'WITHDRAW', 'TICKET_SALE']
```

A primeira tentativa foi recusada: o débito saiu, o provedor recusou, e o
`WITHDRAW_REFUND` devolveu o valor. A segunda pagou. O extrato mostra as
quatro linhas, da mais nova para a mais antiga.

O que acontece em cada desfecho:

| O provedor... | Exceção | O débito |
| --- | --- | --- |
| aceitou | — (devolve `WithdrawalSchema`) | fica |
| recusou de forma definitiva | `PayoutRejectedException` (502) | **volta** (`WITHDRAW_REFUND`) |
| deu timeout, caiu a conexão, respondeu algo ilegível | `PayoutUncertainException` (502) | **fica**, com log `CRITICAL` |
| — não havia disponível | `InsufficientBalanceException` (409) | nada foi debitado |

!!! danger "Por que o débito fica no caso incerto"
    Num timeout, o Pix pode ter saído. Devolver o saldo ali deixa a pessoa
    sacar de novo o mesmo dinheiro. O log `CRITICAL` traz o
    `correlation_id`: a conciliação é conferir esse id no provedor e, se o
    Pix não saiu, creditar de volta com `credit(..., kind="WITHDRAW_REFUND")`.

!!! tip "A chave Pix vem do perfil"
    `withdraw()` recebe `pix_key` de quem chama. Leia do cadastro do usuário,
    nunca do corpo da requisição — senão qualquer um saca para a própria
    chave a partir da carteira de outra pessoa. O router abaixo já faz assim.

## Sacar pela OpenPix

Em produção, troque o fake por `OpenPixPayoutProvider`:

```python
from tempest_fastapi_sdk import HTTPClient, RetryPolicy
from tempest_fastapi_sdk.integrations.payment.adapters import OpenPixPayoutProvider
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEnvironmentEnum

http: HTTPClient = HTTPClient(
    base_url=OpenPixEnvironmentEnum.SANDBOX.base_url,
    default_headers={"Authorization": "<seu AppID>"},
    retry_policy=RetryPolicy(max_attempts=1),
)
payout: OpenPixPayoutProvider = OpenPixPayoutProvider(http)
```

- O adapter manda `autoApprove: true`: cria e aprova o pagamento numa
  chamada só, sem a janela "criado, ainda não aprovado".
- HTTP 4xx, ou pagamento `DENIED`/`FAILED`, viram
  `PayoutRejectedException`. `CONFIRMED` vira `PayoutStatusEnum.CONFIRMED`.
  `CREATED`, `APPROVED` ou um estado que o adapter não conhece viram
  `PayoutStatusEnum.PENDING`, com o valor original em `provider_status` — a
  liquidação chega depois pelos webhooks `OPENPIX:MOVEMENT_*`.

!!! warning "`RetryPolicy(max_attempts=1)` é obrigatório, e o adapter confere"
    O `HTTPClient` refaz **qualquer** método em 429, 5xx e timeout de
    leitura. Medido com a política padrão: um `POST` respondido `500` sai
    três vezes. Num saque, o primeiro `POST` pode ter criado o pagamento, e
    o segundo receberia uma resposta que o adapter leria como recusa — a
    carteira devolveria dinheiro que já saiu. Por isso
    `OpenPixPayoutProvider` levanta `ValueError` no construtor se o
    `HTTPClient` puder refazer a requisição.

## Sacar à mão (`ManualPayoutProvider`)

Sem payout automático — o do Mercado Pago não está liberado para a conta
(#499) —, `ManualPayoutProvider` implementa o mesmo `PayoutProvider`
mandando ao administrador um e-mail com o Pix a fazer: valor em reais, chave
e tipo, `user_id` (de `PayoutRequest.payee_id`, que o `withdraw` preenche),
`correlation_id` e comentário. Devolve `PayoutStatusEnum.PENDING` com
`provider_status="manual_review"`, e o débito segue a regra de sempre:

- o mailer levanta `EmailDeliveryException` ou `IntegrationDisabledException`
  → `PayoutRejectedException`: nenhum administrador recebeu o pedido, então
  ninguém vai pagar, e o débito volta;
- qualquer outro erro propaga, e o débito fica (`PayoutUncertainException`).

O log traz a chave mascarada (`mask_pix_key`), nunca inteira. Com
`records=` (um `BaseRepository` da tabela `make_manual_payout_model`), cada
saque também vira uma linha `PENDING` que o administrador conclui no painel
com o comprovante em PDF. A rota pronta que junta tudo — saque que zera a
carteira, linha, e-mail e painel — está em
[Mercado Pago: testando webhooks » Saque manual](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/mercado-pago-webhooks.md#saque-manual).

## Concluir ou recusar o saque à mão (`ManualPayoutService`)

A linha `PENDING` sai desse estado de duas formas, e as duas moram em
`ManualPayoutService` — a mesma classe que as ações do painel chamam, para o
serviço que expõe a fila pela própria API (por exemplo, `PATCH /api/admin/withdrawals`)
não reimplementar a regra:

- `mark_paid(row_id, end_to_end_id=None, *, actor=None, require_receipt=False)`
  → `COMPLETED`, com `completed_at`, `completed_by` e o `end_to_end_id` do
  Pix;
- `reject(row_id, reason, *, actor=None)` → `REJECTED`, com
  `rejection_reason`, `rejected_at` e `rejected_by`, **e** uma linha
  `WITHDRAW_REFUND` do valor na carteira (`reference_id` é o
  `correlation_id`, o mesmo da linha `WITHDRAW`), no mesmo commit.

Os dois mudam o status com um `UPDATE ... WHERE id = :id AND status =
'pending'`: dois administradores na mesma linha a movem uma vez, e o segundo
recebe `ManualPayoutNotPendingException` (`409
WALLET_MANUAL_PAYOUT_NOT_PENDING`) sem nada alterado — nem status, nem
carteira, nem hook. Id que não existe é `ManualPayoutNotFoundException`
(`404`); `require_receipt=True` sem comprovante é
`ManualPayoutReceiptMissingException` (`409`).

O histórico que o usuário vê no app é do serviço, não do SDK. Para ele
andar junto com o saque, `ManualPayoutHooks` recebe um callback por transição
— `on_requested`, `on_canceled`, `on_paid`, `on_rejected` —, cada um chamado
com a sessão e a linha **dentro** da transação da transição. Passe a mesma
instância ao `ManualPayoutProvider` (que roda `on_requested` e `on_canceled`)
e ao `ManualPayoutService` (`on_paid` e `on_rejected`):

```python
import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    PixKeyType,
)
from tempest_fastapi_sdk.exceptions import ManualPayoutNotPendingException
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils
from tempest_fastapi_sdk.wallet import (
    ManualPayoutHooks,
    ManualPayoutProvider,
    ManualPayoutService,
    WalletBalanceMixin,
    WalletService,
    make_manual_payout_model,
    make_wallet_entry_model,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


WalletEntryModel = make_wallet_entry_model(user_table="users")
ManualPayoutModel = make_manual_payout_model(user_table="users")


class WithdrawalHistoryModel(BaseModel):
    """The line the user sees in the app's history."""

    __tablename__ = "withdrawal_history"

    correlation_id: Mapped[str] = mapped_column(String(128), unique=True)
    label: Mapped[str] = mapped_column(String(32))


async def on_requested(session: AsyncSession, row: Any) -> None:
    session.add(
        WithdrawalHistoryModel(
            correlation_id=row.correlation_id, label="SAQUE SOLICITADO"
        )
    )


async def _relabel(session: AsyncSession, row: Any, label: str) -> None:
    line = (
        await session.execute(
            select(WithdrawalHistoryModel).where(
                WithdrawalHistoryModel.correlation_id == row.correlation_id
            )
        )
    ).scalar_one()
    line.label = label


async def on_paid(session: AsyncSession, row: Any) -> None:
    await _relabel(session, row, "SAQUE REALIZADO")


async def on_rejected(session: AsyncSession, row: Any) -> None:
    await _relabel(session, row, "SAQUE RECUSADO")


HOOKS = ManualPayoutHooks(
    on_requested=on_requested, on_paid=on_paid, on_rejected=on_rejected
)


def wallet(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


def payouts(session: AsyncSession) -> ManualPayoutService:
    return ManualPayoutService(
        records=BaseRepository(session, model=ManualPayoutModel),
        ledger=wallet(session).ledger,
        hooks=HOOKS,
    )


async def main() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="motorista@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        user_id: UUID = user.id
        await wallet(session).credit(user_id, 12_345, kind="SALE")
        provider = ManualPayoutProvider(
            FakeEmailUtils(),
            admin_emails=["financeiro@example.com"],
            records=BaseRepository(session, model=ManualPayoutModel),
            hooks=HOOKS,
        )
        await wallet(session).withdraw(
            user_id,
            payout=provider,
            pix_key="motorista@example.com",
            pix_key_type=PixKeyType.EMAIL,
            correlation_id="saque-1",
        )
        row = (await session.execute(select(ManualPayoutModel))).scalar_one()
        print("depois do saque  ", (await wallet(session).balance(user_id)).total_cents)

    async with db.get_session_context() as session:
        rejected = await payouts(session).reject(
            row.id, "chave Pix de outra pessoa", actor="admin-1"
        )
        print("status           ", rejected.status)
        print("depois da recusa ", (await wallet(session).balance(user_id)).total_cents)
        history = (await session.execute(select(WithdrawalHistoryModel))).scalar_one()
        print("histórico        ", history.label)

    async with db.get_session_context() as session:
        try:
            await payouts(session).mark_paid(row.id, actor="admin-2")
        except ManualPayoutNotPendingException as error:
            print("segundo admin    ", error.code)

    await db.disconnect()


asyncio.run(main())
```

Rodando:

```text
depois do saque   0
status            rejected
depois da recusa  12345
histórico         SAQUE RECUSADO
segundo admin     WALLET_MANUAL_PAYOUT_NOT_PENDING
```

!!! check "Hook que levanta desfaz a transição inteira"
    Se `on_rejected` levanta, o status continua `PENDING`, a linha
    `WITHDRAW_REFUND` não existe e o saldo não mudou — os testes de
    `tests/wallet/test_manual_payout_service.py` conferem os três. Em
    `on_requested`, levantar é recusar o saque: a linha não é gravada, o
    e-mail não sai e o débito volta. Em `on_canceled` (o e-mail falhou),
    levantar deixa a linha `PENDING` e o débito guardado
    (`PayoutUncertainException`), o mesmo de um erro desconhecido.

O `reject` precisa do `ledger` (o `WalletService.ledger` já serve) na mesma
sessão do repositório — sessão diferente é `ValueError` no construtor, porque
a devolução e o status commitariam separados. Sem `ledger`, só `mark_paid`
funciona. O painel monta tudo isso sozinho: veja
[Painel admin » Preset: saque manual](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/forms.md#preset-saque-manual-manual_payout_admin).

## Taxa e divisão

`openpix_fee_cents()` calcula a taxa da OpenPix, e `split_net()` divide o
total entre plataforma, gateway e quem recebe:

```python
from tempest_fastapi_sdk.wallet import openpix_fee_cents, split_net

total = 10_000

driver = split_net(
    total,
    platform_bps=500,
    gateway_fee_cents=openpix_fee_cents(total),
    residual_recipient="driver",
)
print(driver.platform_cents, driver.gateway_fee_cents, dict(driver.shares))

producer = split_net(
    total,
    platform_bps=1_500,
    gateway_fee_cents=openpix_fee_cents(total),
    residual_recipient="producer",
    shares_bps={"interlocutor": 2_000},
)
print(producer.platform_cents, producer.net_cents, dict(producer.shares))
```

Saída:

```text
500 180 {'driver': 9320}
1500 8320 {'interlocutor': 1664, 'producer': 6656}
```

- Percentual é **basis point**: `500` são 5 %, `10_000` são 100 %. Tudo em
  divisão inteira, arredondando para baixo.
- A plataforma tira a parte dela do **bruto**; a taxa do gateway sai em
  seguida; o que sobra é o líquido. Cada entrada de `shares_bps` leva a
  porcentagem dela **do líquido**, e `residual_recipient` fica com o resto
  — então o centavo que o arredondamento sobra cai numa pessoa conhecida, em
  vez de sumir. Quando as taxas passam do total, o líquido é `0`.
- `OPENPIX_FEE_TIERS` é a tabela que os serviços Tempest usam hoje (até
  R$ 62,50: R$ 0,50; até R$ 625,00: 0,8 %; acima: R$ 5,00; mais R$ 1,00
  fixo). Contrato diferente? Passe o seu `OpenPixFeeTiers` em `tiers=`.

??? note "Comparado com os serviços que já existiam"
    Medido em todo total de R$ 0,01 a R$ 2.000,00 (200 000 valores):
    `openpix_fee_cents` e `split_net` (15 % de plataforma, interlocutores
    sobre o líquido) dão exatamente o que o alofans-api calcula. Contra o
    transport-backend, que arredonda em reais com `round()`, o motorista
    recebe 0, 1 ou 2 centavos a mais aqui (87 928, 98 435 e 13 637 dos
    200 000 totais) — o SDK arredonda a parte da plataforma sempre para
    baixo.

## Liquidar uma vez só

O webhook da cobrança chega mais de uma vez, e pode chegar junto com a
confirmação manual. `claim_once()` marca a linha só se a coluna ainda estiver
vazia:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.db.transaction import transaction
from tempest_fastapi_sdk.wallet import WalletLedger, claim_once


class OrderModel(BaseModel):
    __tablename__ = "orders"

    seller_id: Mapped[UUID] = mapped_column(nullable=False)
    amount_cents: Mapped[int] = mapped_column(nullable=False)
    credited_at: Mapped[datetime | None] = mapped_column(nullable=True)


async def settle(
    session: AsyncSession, ledger: WalletLedger, order: OrderModel
) -> bool:
    async with transaction(session):
        if not await claim_once(session, OrderModel, order.id, "credited_at"):
            return False
        await ledger.credit(
            order.seller_id,
            order.amount_cents,
            kind="SALE",
            reference_type="order",
            reference_id=str(order.id),
        )
    return True
```

A segunda chamada recebe `False` e não credita de novo. `claim_once()` não
faz commit: ele entra no mesmo bloco que o crédito, então uma falha no
crédito desfaz a marcação também.

### O ledger, não o service — `WalletLedger`

Repare que o `settle` recebe um `WalletLedger`, não o `WalletService`. O
ledger é a camada de dados da carteira: `credit`, `debit_available`,
`reverse`, `balance` e `statement`, cada movimento um `UPDATE ... RETURNING`
mais a linha de extrato. Dentro de um bloco `transaction()` do chamador, ele
**entra no bloco** e não comita sozinho: o crédito e as escritas do chamador
gravam juntos, ou voltam juntos se algo levantar (os dois casos estão em
`tests/wallet/test_ledger.py`, em SQLite e PostgreSQL).

Por isso o service do seu projeto que precisa mexer em saldo na própria
transação — a liquidação de um pedido, o repasse ao fim de uma viagem —
recebe o **ledger**, como recebe qualquer repository:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.wallet import WalletLedger

from src.db.models import UserModel, WalletEntryModel


def build_wallet_ledger(session: AsyncSession) -> WalletLedger:
    """Monta o ledger da carteira sobre a sessão da requisição.

    Args:
        session (AsyncSession): A sessão que os dois repositories dividem.

    Returns:
        WalletLedger: O ledger.
    """
    return WalletLedger(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )
```

O `WalletService` continua sendo o que o `make_wallet_router` e o **saque**
usam: `withdraw` debita, chama o `PayoutProvider` e devolve o dinheiro na
recusa. Ele pode receber o ledger pronto (`WalletService(ledger=...)`) ou os
dois repositories, como sempre (monta o ledger por dentro), e continua
expondo `credit`, `balance` e os demais por delegação.

| Use | Quando |
| --- | --- |
| `WalletLedger` | um service do projeto credita, debita ou estorna dentro da própria transação |
| `WalletService` | saque Pix (`withdraw`) e o router da carteira |

Um service que recebe o `WalletService` é service dentro de service — o
`tempest lint-layers` acusa como `DI002`. Receber o ledger dispensa a exceção.

## O router

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    HTTPClient,
    RetryPolicy,
)
from tempest_fastapi_sdk import PixKeyType
from tempest_fastapi_sdk.integrations.payment.adapters import OpenPixPayoutProvider
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEnvironmentEnum
from tempest_fastapi_sdk.wallet import (
    BaseWalletEntryModel,
    PixDestinationSchema,
    WalletBalanceMixin,
    WalletService,
    make_wallet_router,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


class WalletEntryModel(BaseWalletEntryModel):
    __tablename__ = "wallet_entries"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
payout = OpenPixPayoutProvider(
    HTTPClient(
        base_url=OpenPixEnvironmentEnum.SANDBOX.base_url,
        default_headers={"Authorization": "<seu AppID>"},
        retry_policy=RetryPolicy(max_attempts=1),
    )
)


async def sessions() -> AsyncIterator[AsyncSession]:
    async with db.get_session_context() as session:
        yield session


def wallet_service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


def current_user_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


def pix_destination() -> PixDestinationSchema:
    return PixDestinationSchema(
        pix_key="motorista@example.com", pix_key_type=PixKeyType.EMAIL
    )


def payout_provider() -> OpenPixPayoutProvider:
    return payout


app = FastAPI()
app.include_router(
    make_wallet_router(
        service_factory=wallet_service,
        session_factory=sessions,
        current_user_id=current_user_id,
        payout_provider=payout_provider,
        pix_destination=pix_destination,
    )
)
```

| Rota | O que faz |
| --- | --- |
| `GET /api/wallet/balance` | `WalletBalanceSchema` de quem está logado |
| `GET /api/wallet/statement?page=1&page_size=20` | extrato paginado, mais novo primeiro |
| `POST /api/wallet/withdraw` `{"amount_cents": 500}` | saca (sem `amount_cents`, todo o disponível) |

`current_user_id` e `pix_destination` são os dois que você troca pelos
seus: o primeiro vem da sua autenticação; o segundo lê a chave do cadastro
do usuário e levanta erro quando não há chave cadastrada. Nenhuma rota
aceita chave Pix ou id de usuário no corpo.

## Modelo que já tem coluna de saldo

Serviço que já guarda o saldo numa coluna `wallet` (em centavos) não precisa
do mixin. Aponte o service para ela:

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.wallet import WalletService, make_wallet_entry_model


class LegacyUserModel(BaseModel):
    __tablename__ = "legacy_users"

    wallet: Mapped[int] = mapped_column(nullable=False, default=0)


LegacyEntryModel = make_wallet_entry_model(
    user_table="legacy_users",
    tablename="legacy_wallet_entries",
    class_name="LegacyEntryModel",
)


def wallet_service(session: AsyncSession) -> WalletService:
    return WalletService(
        balances=BaseRepository(session, model=LegacyUserModel),
        entries=BaseRepository(session, model=LegacyEntryModel),
        balance_attribute="wallet",
    )
```

Quem já tem saldo antes do extrato existir ganha uma primeira linha
`WalletEntryKindEnum.OPENING_BALANCE` na migração, para o extrato fechar com o
saldo.

## Recap

- O saldo é centavo inteiro na linha do usuário; cada movimento é uma linha
  de extrato na mesma transação.
- Todo movimento é um `UPDATE` só no banco: crédito não se perde e saque
  não paga duas vezes, medido com 50 tarefas simultâneas em Postgres.
- A retenção faz parte do débito, não de uma leitura anterior.
- Service do projeto que mexe em saldo na própria transação recebe o
  `WalletLedger`; o `WalletService` é o saque e o router.
- Saque debita antes, devolve só na recusa definitiva e guarda o débito
  quando o desfecho é incerto.
- `split_net` em basis points nunca perde nem inventa centavo.
- Saque manual sai de `PENDING` por `ManualPayoutService`: `mark_paid` ou
  `reject`, que devolve à carteira no mesmo commit; hooks gravam o
  histórico do usuário na mesma transação.
- Para testar sem credencial, use `FakePayoutProvider` (veja
  [Fakes](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/fakes.md)).
