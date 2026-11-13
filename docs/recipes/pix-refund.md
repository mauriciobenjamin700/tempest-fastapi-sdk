# Estorno de Pix (idempotente)

Um passageiro cancela a passagem. Seu serviço devolve o dinheiro por Pix,
numa rotina com retry. Na terceira tentativa, a primeira finalmente
responde: ela tinha estornado, só a resposta se perdeu no timeout.

Se a rotina não sabe disso, ela estorna de novo. O passageiro recebe duas
vezes, e o dinheiro sai da sua conta.

Esta receita mostra como estornar de um jeito em que o **retry é seguro por
construção** — sem lease, sem reler a order e comparar totais à mão.

## O contrato: `PixRefundProvider`

```python
from tempest_fastapi_sdk.integrations.payment import (
    PixRefund,
    PixRefundProvider,
    PixRefundStatusEnum,
)
```

`PixRefundProvider` é um `PixProvider` com dois métodos a mais:

```text
async def refund_pix_charge(
    self,
    charge_id: str,
    amount_cents: int | None = None,
    *,
    refund_reference: str,
) -> PixRefund

async def get_refunded_cents(self, charge_id: str) -> int
```

O que cada um garante, em todo provedor:

| Situação | Resposta |
| --- | --- |
| primeira chamada com uma `refund_reference` | estorna e devolve o `PixRefund` |
| mesma `refund_reference`, mesmo valor | devolve **o mesmo** estorno; o dinheiro não sai de novo |
| mesma `refund_reference`, outro valor | `PixRefundReferenceConflictException` (409) |
| valor acima do que sobrou | `PixRefundExceedsBalanceException` (409) |
| `amount_cents=None` | estorna o que sobrou; se nada sobrou, `PixRefundExceedsBalanceException` |
| `amount_cents` zero ou negativo | `ValueError`, antes de falar com o provedor |

`get_refunded_cents` soma os estornos que o provedor lista para a cobrança,
menos os que falharam. Estorno ainda em processamento **conta**: tratá-lo como
dinheiro devido é o que deixaria um segundo estorno sair enquanto o primeiro
liquida.

!!! tip "Uma `refund_reference` por estorno"
    Use um identificador seu, estável e único na conta inteira — o id da
    passagem cancelada, por exemplo (`"ticket-42-refund"`). É ele que torna
    o retry idempotente. A OpenPix grava esse valor como o `correlationID`
    do estorno, então não reaproveite a mesma referência em duas cobranças.

## Estornar com retry, sem lease

O exemplo inteiro, rodando sem rede com o `FakePixProvider`:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatusEnum,
    PixChargeRequest,
    PixRefund,
    PixRefundProvider,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider


async def refund_ticket(
    provider: PixRefundProvider,
    *,
    charge_id: str,
    ticket_id: str,
    amount_cents: int,
    attempts: int = 3,
) -> PixRefund:
    """Estorna uma passagem, repetindo em caso de timeout.

    Args:
        provider (PixRefundProvider): Qualquer provedor que estorne.
        charge_id (str): O `provider_charge_id` guardado na compra.
        ticket_id (str): A passagem cancelada.
        amount_cents (int): Quanto devolver.
        attempts (int): Quantas vezes tentar.

    Returns:
        PixRefund: O estorno — o mesmo em todas as tentativas.

    Raises:
        TimeoutError: Se todas as tentativas expiraram.
    """
    last_error: TimeoutError | None = None
    for _ in range(attempts):
        try:
            return await provider.refund_pix_charge(
                charge_id,
                amount_cents,
                refund_reference=f"ticket-{ticket_id}-refund",
            )
        except TimeoutError as error:
            last_error = error
    assert last_error is not None
    raise last_error


async def main() -> None:
    """Abre uma cobrança paga, perde a resposta do estorno e tenta de novo."""
    provider = FakePixProvider()
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=12000, reference="order-42"),
    )
    provider.advance(charge.provider_charge_id, PaymentStatusEnum.PAID)

    provider.fail_after_next_refund(TimeoutError("a resposta se perdeu"))
    refund = await refund_ticket(
        provider,
        charge_id=charge.provider_charge_id,
        ticket_id="42",
        amount_cents=4500,
    )

    print(refund.amount_cents, refund.refunded_cents)
    print(await provider.get_refunded_cents(charge.provider_charge_id))
    print(len(provider.refunds[charge.provider_charge_id]))


asyncio.run(main())
```

Saída:

```text
4500 4500
4500
1
```

A primeira tentativa **estornou** e levantou `TimeoutError` — é isso que
`fail_after_next_refund` simula. A segunda usou a mesma `refund_reference` e
recebeu o estorno que já existia. Um estorno só, R$ 45,00 devolvidos uma vez.

Note o que **não** está no código: nenhum lock, nenhuma leitura prévia da
cobrança, nenhuma comparação de total. A referência faz esse trabalho.

!!! warning "Duas chamadas *simultâneas* com a mesma referência"
    A garantia acima é sobre chamadas **em sequência** (o retry). Duas
    chamadas concorrentes com a mesma referência podem chegar as duas ao
    provedor antes de qualquer uma ter terminado. No Mercado Pago, a
    `X-Idempotency-Key` cobre esse caso do lado de lá; na OpenPix, nenhum
    documento diz que um `correlationID` repetido é recusado. Se a sua
    rotina pode disparar o mesmo estorno em paralelo (dois workers pegando a
    mesma tarefa), serialize por referência — uma fila com um consumidor por
    chave, ou `claim_once` antes de chamar.

## Estorno parcial e o que já foi estornado

Uma cobrança com três passagens; duas são canceladas em momentos diferentes:

```python
import asyncio

from tempest_fastapi_sdk.exceptions import PixRefundExceedsBalanceException
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatusEnum,
    PixChargeRequest,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider


async def main() -> None:
    """Estorna duas de três passagens e tenta estornar demais."""
    provider = FakePixProvider()
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=9000, reference="order-7"),
    )
    charge_id = charge.provider_charge_id
    provider.advance(charge_id, PaymentStatusEnum.PAID)

    await provider.refund_pix_charge(charge_id, 3000, refund_reference="ticket-1")
    await provider.refund_pix_charge(charge_id, 3000, refund_reference="ticket-2")
    print(await provider.get_refunded_cents(charge_id))

    try:
        await provider.refund_pix_charge(
            charge_id, 4000, refund_reference="ticket-3"
        )
    except PixRefundExceedsBalanceException as error:
        print(error.requested_cents, error.refundable_cents)


asyncio.run(main())
```

Saída:

```text
6000
4000 3000
```

`get_refunded_cents` é a resposta para "quanto já devolvi desta cobrança?",
perguntada ao provedor — não ao seu banco. Use-a para reconciliar: se o seu
registro diz R$ 30,00 e o provedor diz R$ 60,00, alguém estornou por fora.

As duas exceções são `AppException`: com `register_exception_handlers`, uma
rota que as deixa subir responde `409` com `code`
`PIX_REFUND_EXCEEDS_BALANCE` ou `PIX_REFUND_REFERENCE_CONFLICT` e os
detalhes em `details`.

## Com o roteador de provedores

Se você usa o `PixProviderRouter` para migrar de provedor, o estorno vai
para o provedor que criou a cobrança, igual a `get` e `cancel`:

```python
from tempest_fastapi_sdk.integrations.payment import PixProviderRouter, PixRefund


async def refund_stored_charge(
    router: PixProviderRouter,
    *,
    provider_name: str,
    charge_id: str,
    ticket_id: str,
) -> PixRefund:
    """Estorna o que sobrou de uma cobrança guardada.

    Args:
        router (PixProviderRouter): O roteador do serviço.
        provider_name (str): O `PixCharge.provider` guardado.
        charge_id (str): O `PixCharge.provider_charge_id` guardado.
        ticket_id (str): A passagem cancelada.

    Returns:
        PixRefund: O estorno.
    """
    return await router.refund_pix_charge(
        provider_name,
        charge_id,
        refund_reference=f"ticket-{ticket_id}-refund",
    )
```

Um provedor registrado que só implementa `PixProvider` faz essa chamada
levantar `TypeError`, dizendo qual método falta — antes de qualquer
requisição.

## Como cada provedor garante a idempotência

| Provedor | Mecanismo |
| --- | --- |
| **Mercado Pago** | A `X-Idempotency-Key` do `POST /v1/orders/{id}/refund` é derivada da `refund_reference` (UUID5 do id da order + referência). Mesma referência, mesma chave: o Mercado Pago responde de novo o primeiro estorno. A order é lida antes para conferir o saldo; se não sobra o suficiente, o pedido **ainda sai** com a chave derivada, para que o retry de um estorno que já esvaziou a cobrança receba o replay em vez da exceção — e só uma recusa do provedor vira `PixRefundExceedsBalanceException`. `409 idempotency_key_already_used` vira `PixRefundReferenceConflictException`. |
| **OpenPix** | A `refund_reference` vira o `correlationID` do estorno. Antes do `POST`, o adapter lista os estornos da cobrança (`GET /api/v1/charge/{id}/refund`) e, se já existe um com essa referência, devolve-o sem estornar. Se o `POST` falha (timeout, 5xx), o adapter relista: achou o estorno, devolve-o — a resposta se perdeu, o estorno não. O valor sai sempre explícito, mesmo no estorno total. |
| **`FakePixProvider`** | Guarda os estornos por cobrança e responde a referência repetida com o estorno guardado. `fail_next` falha **antes** de estornar; `fail_after_next_refund` estorna e **depois** falha. |

!!! info "O que foi medido e o que não foi"
    No Mercado Pago, mesma chave e mesmo corpo devolvendo a mesma resposta,
    e mesma chave com outro corpo respondendo `409
    idempotency_key_already_used`, foram medidos no **create** da order
    (2026-10-10), não na rota de estorno. A resposta do estorno lista todos
    os estornos da order, do mais antigo ao mais novo (fixtures do sandbox),
    e o adapter lê o último como o que acabou de fazer. Na OpenPix, o que
    ela responde a um `correlationID` de estorno repetido não está na
    especificação e não foi medido — por isso a idempotência de lá é a
    leitura antes e depois do `POST`, não a do provedor.

!!! note "`end_to_end_id` vazio no Mercado Pago"
    O campo `e2e_id` dos estornos do sandbox traz um timestamp
    (`"2026-10-10 09:41:40.107"`), não um identificador fim-a-fim do Pix.
    O adapter deixa `PixRefund.end_to_end_id` em `None` e o valor cru fica
    em `raw`.

## Quem já chamava `MercadoPagoPixProvider.refund_pix_charge`

A forma antiga continua igual:

```python
from tempest_fastapi_sdk.integrations.payment import PixCharge
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoPixProvider,
)


async def refund_old_way(provider: MercadoPagoPixProvider, order_id: str) -> PixCharge:
    """Estorna R$ 5,00 sem referência, como antes do protocolo.

    Args:
        provider (MercadoPagoPixProvider): O adapter.
        order_id (str): O id da order paga.

    Returns:
        PixCharge: A order depois do estorno.
    """
    return await provider.refund_pix_charge(order_id, 500)
```

Sem `refund_reference`, o método devolve um `PixCharge`, com uma chave nova
por chamada — o retry da própria chamada é seguro, **uma segunda chamada é
um segundo estorno**. Com `refund_reference`, devolve um `PixRefund` e ganha
tudo desta receita. Os dois retornos estão tipados por `@overload`: o mypy
sabe qual você recebe.

## Recapitulando

- Dependa de `PixRefundProvider` quando o service estorna.
- Passe sempre uma `refund_reference` sua, estável, uma por estorno.
- Retry com a mesma referência é seguro; não precisa de lease nem de
  releitura.
- `get_refunded_cents` pergunta ao provedor quanto já saiu.
- Acima do saldo: `PixRefundExceedsBalanceException`. Referência reutilizada
  com outro valor: `PixRefundReferenceConflictException`.
- Para testar sem rede: `FakePixProvider` + `fail_after_next_refund`.

Veja também: [Protocolo de Pix](pix-protocol.md) ·
[Mercado Pago](mercado-pago.md) · [OpenPix](openpix.md) ·
[Fakes](fakes.md) · [Idempotência](idempotency.md)
