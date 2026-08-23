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
    PixEventType,
    PixPaymentEvent,
    PixPayer,
    PixProvider,
)
```

!!! info "Nenhum extra para o contrato"
    Medido numa instalação sem extra: o import acima e o
    `OpenPixPixProvider` resolvem do core. Extra é assunto do cliente HTTP
    de cada provedor, não do contrato.

`PixProvider` é um atributo e quatro métodos — é o arquivo
`integrations/payment/base.py`, sem simplificação:

```text
provider_name: str

async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge
async def get_pix_charge(self, charge_id: str) -> PixCharge
async def cancel_pix_charge(self, charge_id: str) -> PixCharge
def parse_webhook(self, event: Any) -> PixPaymentEvent
```

`Protocol`, não classe base: o adapter satisfaz **por forma**, sem herdar
nada — a mesma costura que o SDK usa em `RateLimitStore`, `QuotaStore`,
`ModerationBackend` e `PushDispatcher`.

!!! note "Por que não é `runtime_checkable`"
    `isinstance` contra protocolo runtime-checkable confere só que os
    **nomes** existem: um adapter cujo `create_pix_charge` recebe os
    argumentos errados passaria na checagem e falharia na cobrança. Quem
    confere de verdade é `tests/integrations/payment/test_contract.py`,
    comparando `inspect.signature` — e o seu type-checker, se você declarar
    o tipo como `PixProvider` (a próxima seção mostra onde).

### O que entra: `PixChargeRequest`

| campo | tipo | para quê |
| --- | --- | --- |
| `amount_cents` | `int` | valor em centavos — **inteiro**, nunca `float` |
| `reference` | `str` | seu identificador; volta em `PixCharge.reference` |
| `description` | `str` ou `None` | texto que o pagador vê |
| `expires_in` | `timedelta` ou `None` | janela de pagamento |
| `payer` | `PixPayer` ou `None` | dados do pagador, quando o provedor aceita |

### O que sai: `PixCharge`

| campo | tipo | para quê |
| --- | --- | --- |
| `provider` | `str` | quem emitiu (vem de `provider_name`) |
| `provider_charge_id` | `str` | **o id que você guarda** — é o argumento de `get_pix_charge` e `cancel_pix_charge` |
| `reference` | `str` | o seu identificador, de volta |
| `amount_cents` | `int` | valor, em centavos |
| `currency` | `str` | ISO 4217, default `BRL` |
| `status` | `PaymentStatus` | o estado sobre o qual você decide |
| `provider_status` | `str` | o estado como o provedor o nomeia, cru |
| `br_code` | `str` ou `None` | copia-e-cola EMV |
| `qr_code_image_url` | `str` ou `None` | QR como URL (é o que o OpenPix devolve) |
| `qr_code_base64` | `str` ou `None` | QR como Base64 (é o que o Mercado Pago devolve) |
| `end_to_end_id` | `str` ou `None` | identificador da liquidação no Pix |
| `expires_at` | `datetime` ou `None` | quando a janela fecha |
| `paid_at` | `datetime` ou `None` | quando liquidou |
| `raw` | `dict[str, Any]` | tudo que o provedor disse além disso |

!!! tip "O QR vem nos dois formatos porque os provedores discordam"
    O contrato carrega `qr_code_image_url` **e** `qr_code_base64`, e
    preenche o que o provedor entregar. Seu template lê o que existir, em
    vez de saber qual provedor está atrás.

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

## Consultando e cancelando

Criar é um quarto do contrato. O resto do ciclo usa o
`provider_charge_id` que a cobrança devolveu — guarde esse campo junto do
seu pedido, porque é o único jeito de voltar ao provedor:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixProvider,
)


async def cobrar_e_acompanhar(provider: PixProvider) -> PixCharge:
    """Create a charge, read it back, and withdraw it if it is still open.

    Args:
        provider (PixProvider): Any provider that implements the contract.

    Returns:
        PixCharge: The charge in its final observed state.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="pedido-1042"),
    )
    charge_id: str = charge.provider_charge_id

    current = await provider.get_pix_charge(charge_id)
    if current.status is PaymentStatus.PAID:
        return current

    return await provider.cancel_pix_charge(charge_id)
```

!!! warning "Consultar não substitui webhook, e webhook não substitui consultar"
    `get_pix_charge` é a fonte que você controla: ela responde quando você
    pergunta. O webhook é a que chega primeiro, e pode não chegar. Um
    serviço que só escuta webhook fica preso quando a entrega falha; um que
    só consulta paga latência em cada pedido. A receita
    [OpenPix »](openpix.md) mostra os dois lados montados — webhook para
    reagir, conferência para reconciliar.

## Trocando de provedor

O adapter é a única linha que muda. Concentre a escolha num lugar e devolva
o **contrato**, nunca o adapter:

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
produção. Num serviço FastAPI, essa função é o corpo do `Depends`: o router recebe
`PixProvider` e nunca sabe qual adapter chegou — esse é o ponto.

!!! info "Quantos adapters existem hoje: um"
    O SDK ships **um** adapter pronto — `OpenPixPixProvider`, em
    `integrations/payment/adapters/openpix.py`. O Mercado Pago tem cliente,
    schemas e `parse_pix_payment` em
    `integrations/payment/mercado_pago/`, mas **ainda não** um
    `PixProvider`; o Stripe entra por outro caminho, porque
    [não faz Pix](stripe.md). Então a troca de uma linha é o desenho, e é
    real assim que o segundo adapter existir — escrever um é a última
    seção desta página.

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

Os tipos canônicos são seis:

| `PixEventType` | disparado quando |
| --- | --- |
| `CHARGE_CREATED` | a cobrança foi aberta |
| `CHARGE_PAID` | o dinheiro entrou |
| `CHARGE_EXPIRED` | a janela fechou sem pagamento |
| `CHARGE_CANCELLED` | a cobrança foi retirada |
| `CHARGE_REFUNDED` | o valor foi devolvido |
| `UNKNOWN` | o provedor mandou algo que o SDK não mapeia |

!!! note "Por que `parse_webhook` recebe `Any`"
    Cada provedor entrega um tipo diferente: o OpenPix entrega
    `OpenPixWebhookEvent`, já verificado; outro provedor entregaria o dict
    do corpo, ou um objeto próprio. Tipar o parâmetro como o tipo de um
    provedor amarraria o contrato a ele — que é exatamente o acoplamento
    que esta página evita. O adapter conhece o seu tipo; o contrato conhece
    só o que **sai**, que é `PixPaymentEvent`.

    Verificar a assinatura vem **antes** e continua sendo do provedor:
    RSA-1024 no OpenPix, HMAC no Stripe. O passo a passo do lado OpenPix,
    incluindo como registrar a URL, está em [OpenPix »](openpix.md).

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

## Escrevendo um adapter

Um adapter é uma classe com `provider_name` e os quatro métodos. Nada a
herdar. O exemplo abaixo não fala com provedor nenhum — guarda cobranças num
dict — e por isso serve para **testar o seu service sem rede**, que é o
primeiro adapter que vale escrever:

```python
import asyncio
from typing import Any

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPaymentEvent,
    PixProvider,
)


class FakePixProvider:
    """A provider that keeps charges in a dict instead of calling anyone.

    Attributes:
        provider_name (str): The identifier copied into
            ``PixCharge.provider``.
    """

    provider_name: str = "fake"

    def __init__(self) -> None:
        """Start with no charges."""
        self._charges: dict[str, PixCharge] = {}
        self._next_id: int = 1

    async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge:
        """Create a charge in memory.

        Args:
            request (PixChargeRequest): What the service asked to charge.

        Returns:
            PixCharge: The charge, in canonical shape.
        """
        charge_id = f"fake-{self._next_id}"
        self._next_id += 1
        charge = PixCharge(
            provider=self.provider_name,
            provider_charge_id=charge_id,
            reference=request.reference,
            amount_cents=request.amount_cents,
            status=PaymentStatus.PENDING,
            provider_status="created",
            br_code=f"000201{charge_id}",
        )
        self._charges[charge_id] = charge
        return charge

    async def get_pix_charge(self, charge_id: str) -> PixCharge:
        """Read a charge back.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            PixCharge: The stored charge.

        Raises:
            KeyError: When no charge carries that id.
        """
        return self._charges[charge_id]

    async def cancel_pix_charge(self, charge_id: str) -> PixCharge:
        """Withdraw an unpaid charge.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            PixCharge: The charge in its cancelled shape.
        """
        charge = self._charges[charge_id]
        cancelled = charge.model_copy(
            update={
                "status": PaymentStatus.CANCELLED,
                "provider_status": "cancelled",
            },
        )
        self._charges[charge_id] = cancelled
        return cancelled

    def parse_webhook(self, event: Any) -> PixPaymentEvent:
        """Turn a delivery into a canonical event.

        Args:
            event (Any): Whatever this provider delivers.

        Returns:
            PixPaymentEvent: The canonical event.
        """
        charge = self._charges[str(event["charge_id"])]
        paid = charge.model_copy(
            update={"status": PaymentStatus.PAID, "provider_status": "paid"},
        )
        self._charges[paid.provider_charge_id] = paid
        return PixPaymentEvent(
            provider=self.provider_name,
            type=PixEventType.CHARGE_PAID,
            provider_event_name="fake.paid",
            charge=paid,
            raw=dict(event),
        )


async def main() -> None:
    """Exercise the whole contract against the fake."""
    provider: PixProvider = FakePixProvider()

    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="pedido-1042"),
    )
    print(charge.provider_charge_id, charge.status.value)

    event = provider.parse_webhook({"charge_id": charge.provider_charge_id})
    print(event.type.value, event.charge is not None)


if __name__ == "__main__":
    asyncio.run(main())
```

Rodando, isso imprime:

```text
fake-1 pending
charge_paid True
```

A linha que faz o trabalho de verificação é `provider: PixProvider =
FakePixProvider()`. Ela não muda nada em tempo de execução — muda o que o
type-checker exige de você. Se um método sair com a assinatura errada,
`mypy --strict` reprova ali, e não no dia da primeira cobrança.

!!! tip "Três coisas que um adapter de provedor real faz a mais"
    1. **Converte a unidade.** O contrato é centavo inteiro; o provedor pode
       querer reais decimais. A conversão é do adapter, e é por isso que ela
       fica num lugar só.
    2. **Mapeia o estado.** A string do provedor vira `PaymentStatus`, e a
       original é copiada em `provider_status` — sem descartar nada.
    3. **Preenche `raw`.** Tudo que o provedor diz além do contrato vai para
       lá, para nenhuma informação morrer na tradução.

    O `OpenPixPixProvider` é a referência de como as três ficam juntas:
    `integrations/payment/adapters/openpix.py`.

## Recap

- Seu service depende de `PixProvider` e recebe `PixCharge`.
- O contrato tem quatro métodos: criar, ler, cancelar e interpretar webhook.
  Guarde `provider_charge_id` — é o argumento dos dois do meio.
- Dinheiro atravessa o contrato como `int` de centavos.
- O estado que você usa é `PaymentStatus`; o do provedor fica ao lado, em
  `provider_status`.
- Assinatura de webhook continua por provedor; o evento que sai dela é
  canônico, e o que o SDK não mapeia chega como `UNKNOWN` com o nome
  original.
- Nada se perde: o que o provedor diz a mais está em `raw`.
- Adapter é classe com `provider_name` e os quatro métodos, sem herança.
  Hoje o SDK ships um (OpenPix); o fake in-memory acima é o que você escreve
  primeiro, para testar sem rede.
