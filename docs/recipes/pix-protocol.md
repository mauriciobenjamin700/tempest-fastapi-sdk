# Protocolo de Pix: um contrato, vários provedores

Seu serviço cobra por Pix. Hoje o provedor é o OpenPix; amanhã pode ser o
Mercado Pago, porque a taxa mudou ou porque o cliente já tem conta lá.

Se o seu service fala a língua do provedor, essa troca reescreve o service.
Esta receita mostra como não falar.

## O problema, concretamente

Os dois provedores que o SDK já traz discordam em quase tudo que importa:

| | OpenPix | Mercado Pago |
| --- | --- | --- |
| valor | centavos, num `float` | reais, num `float` |
| estados | `ACTIVE`, `COMPLETED`, `EXPIRED` | 9 estados em `Payment`, 5 em `Order`, 4 em `OrderTransactionPayment` |
| copia-e-cola | `brCode` | `qr_code` |
| imagem do QR | uma **URL** | **Base64** |
| sua referência | `correlationID` | `external_reference` |

Escrever `if charge.status == "COMPLETED"` não acopla seu código ao Pix.
Acopla ao OpenPix.

## O contrato

```python
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixProvider,
)
```

`PixProvider` é um `Protocol` com quatro métodos: criar, ler, cancelar e
interpretar o webhook. Quem implementa devolve sempre um `PixCharge`, na
mesma forma, venha de onde vier.

## Cobrando

Um programa inteiro, do cliente HTTP à cobrança:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixChargeRequest,
    PixProvider,
)
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    OpenPixEnvironment,
)


async def cobrar(provider: PixProvider) -> None:
    """Emit a Pix charge and print what the payer needs to see.

    Args:
        provider (PixProvider): Any provider that implements the contract.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference="pedido-1042",
            description="Pedido 1042",
        )
    )

    print(charge.status is PaymentStatus.PENDING)
    print(charge.br_code)
    print(charge.provider)


async def main() -> None:
    """Wire the OpenPix client and charge through the contract."""
    http: HTTPClient = HTTPClient(
        base_url=OpenPixEnvironment.SANDBOX.base_url,
        default_headers={"Authorization": "<seu AppID>"},
    )
    await cobrar(OpenPixPixProvider(OpenPixClient(http)))


if __name__ == "__main__":
    asyncio.run(main())
```

Repare no tipo de `cobrar`: ele recebe `PixProvider`, não
`OpenPixPixProvider`. É só isso que separa um service portável de um
acoplado.

!!! tip "O valor é `int`, sempre"
    `amount_cents` é um inteiro de centavos. Os dois provedores tipam
    dinheiro como `number` na especificação deles — e valor que passou por
    `float` é valor que pode estar errado. A conversão para a unidade que
    cada provedor espera é problema do adapter.

## Trocando de provedor

O adapter é a única linha que muda:

```python
from tempest_fastapi_sdk.integrations.payment import PixProvider
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient


def build_provider(client: OpenPixClient) -> PixProvider:
    """Choose the Pix provider this deployment charges with.

    Args:
        client (OpenPixClient): The configured OpenPix client.

    Returns:
        PixProvider: The provider, seen through the contract.
    """
    return OpenPixPixProvider(client)
```

Como o retorno é o `Protocol`, o type-checker passa a cobrar de você que o
resto do service não use nada além do contrato — e é ele quem avisa, não a
produção.

## Estados

Você decide sobre `PaymentStatus`, não sobre a string do provedor:

| canônico | significa |
| --- | --- |
| `PENDING` | criada, esperando o pagador |
| `PAID` | liquidada |
| `EXPIRED` | a janela fechou sem pagamento |
| `CANCELLED` | retirada por você ou pelo provedor |
| `REFUNDED` | paga e devolvida |
| `CHARGED_BACK` | revertida pela instituição do pagador |
| `IN_ANALYSIS` | retida para revisão |
| `FAILED` | recusada |

A string original não se perde: ela fica em `provider_status`, que é o que
você põe no log e mostra no suporte.

```python
from tempest_fastapi_sdk.integrations.payment import PaymentStatus, PixCharge


def liberar_pedido(charge: PixCharge) -> bool:
    """Decide whether the order can be released.

    Args:
        charge (PixCharge): The charge, in canonical shape.

    Returns:
        bool: Whether the money is in.
    """
    return charge.status is PaymentStatus.PAID
```

!!! warning "`is` funciona aqui — e não funcionaria de graça"
    `PixCharge` desliga o `use_enum_values` que o `BaseSchema` liga. Sem
    isso, `charge.status` guardaria a **string** `"paid"` e
    `charge.status is PaymentStatus.PAID` seria `False` em toda cobrança,
    silenciosamente, enquanto `==` continuaria funcionando. É o tipo de
    defeito que sobrevive à revisão justamente porque o teste óbvio passa.

## Webhook

A verificação de assinatura continua sendo de cada provedor — RSA-1024 no
OpenPix, HMAC no Stripe. O que o contrato unifica é o que sai dela:

```python
from tempest_fastapi_sdk.integrations.payment import PixEventType, PixProvider
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixWebhookEvent


def tratar(provider: PixProvider, delivery: OpenPixWebhookEvent) -> str | None:
    """Turn a verified delivery into an action.

    Args:
        provider (PixProvider): The provider that verified the delivery.
        delivery (OpenPixWebhookEvent): The verified event.

    Returns:
        str | None: The reference of the order that was paid, if any.
    """
    event = provider.parse_webhook(delivery)
    if event.type is PixEventType.CHARGE_PAID and event.charge is not None:
        return event.charge.reference
    return None
```

Um evento que o SDK não classifica vira `PixEventType.UNKNOWN` **com o nome
original preservado** em `provider_event_name`. Ele fica visível, não
engolido.

## O que o provedor diz além do contrato

Fica em `raw`:

```python
from tempest_fastapi_sdk.integrations.payment import PixCharge


def link_de_pagamento(charge: PixCharge) -> object | None:
    """Read a provider-specific field the contract does not model.

    Args:
        charge (PixCharge): The charge.

    Returns:
        object | None: OpenPix's payment link, when present.
    """
    return charge.raw.get("paymentLinkUrl")
```

!!! info "Por que `raw` precisa existir"
    O `BaseSchema` do SDK é `extra="ignore"`. Sem esse campo, tudo que o
    provedor manda além do contrato sumiria na validação — sem erro, sem
    aviso. `raw` é o que garante que passar pelo contrato não perde
    informação.

    Uma diferença honesta: no caminho de API o `raw` é o payload **depois**
    de o schema gerado validar, então campos que a especificação do
    provedor não declara já foram descartados antes. No webhook, o corpo
    chega como dicionário e o `raw` é fiel. É por isso que `paid_at` só é
    preenchido pela entrega de webhook no OpenPix: `paidAt` aparece nos
    exemplos da especificação, mas não no schema `Charge`.

## Recap

- Seu service depende de `PixProvider` e recebe `PixCharge`.
- Dinheiro atravessa o contrato como `int` de centavos.
- O estado que você usa é `PaymentStatus`; o do provedor fica ao lado, em
  `provider_status`.
- Assinatura de webhook continua por provedor; o evento que sai dela é
  canônico.
- Nada se perde: o que o provedor diz a mais está em `raw`.
