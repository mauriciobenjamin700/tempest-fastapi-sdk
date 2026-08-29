# Protocolo de Pix: um contrato, vários provedores

Seu serviço cobra por Pix. Hoje o provedor é o OpenPix; amanhã pode ser o
Mercado Pago, porque a taxa mudou ou porque o cliente já tem conta lá.

Se o seu service fala a língua do provedor, essa troca reescreve o service.
Esta receita mostra como não falar.

## O problema, concretamente

Os dois provedores que o SDK já traz discordam em quase tudo que importa:

| | OpenPix | Mercado Pago |
| --- | --- | --- |
| valor | centavos, num `int` | reais, num `float` |
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

!!! note "O que o adapter faz com um `payer` incompleto (v0.270.0)"
    Todo campo de `PixPayer` é opcional, mas provedor tem regra própria
    sobre o conjunto. A OpenPix exige `name` **e** pelo menos um de
    `taxID`, `email` ou `phone` — as três variantes do `oneOf` dela pedem
    `name` mais um contato, então `name` é necessário nas três e
    suficiente em nenhuma.

    O adapter **omite** o bloco quando o payer não alcança nenhuma
    variante, em vez de mandar um bloco que a especificação do provedor
    não aceita, e em vez de inventar um contato para preenchê-lo — dado
    inventado acabaria no comprovante do pagador. A cobrança é válida sem
    bloco de pagador.

!!! tip "`reference` pode ter caractere reservado"
    O `reference` é o identificador que **você** escolhe, e ele vira o
    `correlationID` da OpenPix, que por sua vez entra no path das rotas de
    leitura e de cancelamento. O SDK escapa o segmento
    (`quote(..., safe="")`), então `pedido#42` e `pedido/1042` endereçam a
    cobrança certa. Antes da v0.270.0 o `#` era interpretado como
    fragmento e a chamada acertava outro recurso — num `DELETE`.

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
produção. Num serviço FastAPI, essa função é o corpo do `Depends`: o router
recebe `PixProvider` e nunca sabe qual adapter chegou — esse é o ponto, e a
próxima seção monta o serviço inteiro em volta dele.

!!! info "Quantos adapters existem hoje: um"
    O SDK ships **um** adapter pronto — `OpenPixPixProvider`, em
    `integrations/payment/adapters/openpix.py`. O Mercado Pago tem cliente,
    schemas e `parse_pix_payment` em
    `integrations/payment/mercado_pago/`, mas **ainda não** um
    `PixProvider`; o Stripe entra por outro caminho, porque
    [não faz Pix](stripe.md). Então a troca de uma linha é o desenho, e é
    real assim que o segundo adapter existir — escrever um é a última
    seção desta página.

## Na arquitetura do serviço

Até aqui o provider chegou pronto, como argumento. Numa aplicação FastAPI
alguém precisa **construí-lo** — e é essa construção que decide se trocar de
provedor é uma linha ou uma refatoração.

Esta seção monta o caminho inteiro, de baixo para cima: cliente HTTP →
adapter → dependência → service → router. O serviço que sai daqui tem
**dois** arquivos que sabem o nome "OpenPix"; todo o resto fala contrato.

### Onde cada peça mora

```text
src/
├── core/
│   └── settings.py              # OPENPIX_APP_ID + ambiente
├── api/
│   ├── app.py                   # create_app() + lifespan
│   ├── dependencies/
│   │   ├── orders.py            # o repositório do seu pedido
│   │   └── payments.py          # HTTPClient -> OpenPixClient -> adapter
│   └── routers/
│       ├── checkout.py          # POST /api/checkout/{order_id}
│       └── webhooks.py          # POST /webhooks/pix (include_in_schema=False)
├── schemas/
│   └── checkout.py              # o que o seu cliente vê
├── services/
│   └── checkout.py              # regra de negócio, escrita só sobre o contrato
└── db/
    └── repositories/
        └── orders.py            # onde provider_charge_id fica guardado
```

| Camada | Pode importar | Nunca importa |
| --- | --- | --- |
| `api/dependencies` | `HTTPClient`, `OpenPixClient`, o adapter, services | — |
| `api/routers` | as dependências, `schemas` | o adapter, `OpenPixClient` |
| `services` | `PixProvider`, `PixCharge`, repositories | o adapter, `fastapi` |
| `schemas` | `BaseSchema` | o contrato e o adapter |
| `db/repositories` | — | qualquer coisa de pagamento |

`api/dependencies` é a única camada autorizada a conhecer o provedor porque
é a única cuja função é **montar**. É o composition root: o lugar onde o
concreto vira contrato, e o único que muda no dia da troca.

### Passo 1 — a configuração

```python
from tempest_fastapi_sdk import OpenPixSettings


class Settings(OpenPixSettings):
    """Settings do serviço."""


settings: Settings = Settings()
```

`OpenPixSettings` traz `OPENPIX_APP_ID` e `OPENPIX_ENVIRONMENT`, e
`settings.openpix_kwargs()` devolve `base_url` e o header `Authorization`
já resolvidos — os dois argumentos que o `HTTPClient` precisa. Detalhes dos
ambientes em [OpenPix »](openpix.md).

### Passo 2 — o cliente HTTP e o provider, montados uma vez

Este é o arquivo inteiro. Ele é o composition root:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import PixProvider
from tempest_fastapi_sdk.integrations.payment.adapters import OpenPixPixProvider
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    OpenPixWebhookEvent,
    make_openpix_webhook_dependency,
)

from src.api.dependencies.orders import OrderRepositoryDep
from src.core.settings import settings
from src.services.checkout import CheckoutService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build one HTTP client and one provider for the whole process.

    Args:
        app (FastAPI): The application the provider is stored on.

    Yields:
        None: While the application serves requests.
    """
    http: HTTPClient = HTTPClient(**settings.openpix_kwargs(), timeout=15.0)
    app.state.pix_provider = OpenPixPixProvider(OpenPixClient(http))
    try:
        yield
    finally:
        await http.aclose()


def get_pix_provider(request: Request) -> PixProvider:
    """Hand out the process-wide provider, seen through the contract.

    Args:
        request (Request): The request in flight.

    Returns:
        PixProvider: The configured provider.
    """
    provider: PixProvider = request.app.state.pix_provider
    return provider


PixProviderDep = Annotated[PixProvider, Depends(get_pix_provider)]
"""The contract, injected."""

verified_delivery = make_openpix_webhook_dependency()
"""The provider's own verifier, as a dependency tests can override."""

WebhookDeliveryDep = Annotated[OpenPixWebhookEvent, Depends(verified_delivery)]
"""A delivery whose signature the verifier already accepted."""


def get_checkout_service(
    provider: PixProviderDep,
    orders: OrderRepositoryDep,
) -> CheckoutService:
    """Assemble the service the routers call.

    Args:
        provider (PixProviderDep): The provider, through the contract.
        orders (OrderRepositoryDep): The order repository.

    Returns:
        CheckoutService: The service, ready to charge.
    """
    return CheckoutService(provider, orders)


CheckoutServiceDep = Annotated[CheckoutService, Depends(get_checkout_service)]
"""The checkout service, injected."""
```

Quatro coisas acontecem aí, e vale ler uma de cada vez.

**A montagem são três camadas numa linha.**
`OpenPixPixProvider(OpenPixClient(http))` empilha transporte → cliente do
provedor → adapter. Cada uma faz uma coisa: o `HTTPClient` tem retry,
timeout e circuit breaker por host; o `OpenPixClient` sabe as rotas; o
adapter traduz para o contrato.

**O `HTTPClient` é um só, criado no `lifespan`.** Ele é seguro para
compartilhar entre requisições do mesmo event loop, e o pool de conexões, o
retry e o breaker são estado *dele*. Criar um por requisição joga as três
coisas fora e abre um socket novo em cada checkout — e `aclose()` no
`finally` é o que fecha o pool no shutdown.

**A anotação de retorno é a costura.** `get_pix_provider` promete
`PixProvider`, não `OpenPixPixProvider`. Daí para cima, o type-checker
recusa qualquer uso de algo que não esteja no contrato.

**`verified_delivery` é uma variável nomeada de propósito.** Ela é o
callable que o `Depends` registra — e é a chave do
`app.dependency_overrides` no teste. Escrevendo
`Depends(make_openpix_webhook_dependency())` direto dentro do `Annotated`,
a função fica anônima e não há como substituí-la.

!!! warning "`app.state` é `Any` — reanote na leitura"
    `request.app.state.pix_provider` não tem tipo: `app.state` aceita
    qualquer atributo. Sem a linha `provider: PixProvider = ...`, o
    `mypy --strict` reprova o módulo:

    ```text
    src/api/dependencies/payments.py:50: error: Returning Any from function
    declared to return "PixProvider"  [no-any-return]
    ```

    A anotação não é decoração: é o ponto onde o valor volta a existir para
    o type-checker. Com ela, o serviço inteiro passa em `mypy --strict`.

### Passo 3 — o service, que só fala contrato

```python
from datetime import timedelta

from tempest_fastapi_sdk.integrations.payment import (
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPaymentEvent,
    PixProvider,
)

from src.db.repositories import OrderRepository


class CheckoutService:
    """Open and settle Pix charges for orders."""

    def __init__(self, provider: PixProvider, orders: OrderRepository) -> None:
        """Take the contract and the repository.

        Args:
            provider (PixProvider): Any provider that implements the contract.
            orders (OrderRepository): Where the charge id is persisted.
        """
        self._provider: PixProvider = provider
        self._orders: OrderRepository = orders

    async def open_charge(self, order_id: str, amount_cents: int) -> PixCharge:
        """Charge an order and remember how to address the charge later.

        Args:
            order_id (str): The order's identifier, sent as the reference.
            amount_cents (int): The amount, in cents.

        Returns:
            PixCharge: The created charge, in canonical shape.
        """
        charge = await self._provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=amount_cents,
                reference=order_id,
                description=f"Pedido {order_id}",
                expires_in=timedelta(minutes=30),
            ),
        )
        await self._orders.attach_charge(order_id, charge.provider_charge_id)
        return charge

    async def settle(self, event: PixPaymentEvent) -> str | None:
        """Act on a canonical event, whichever provider produced it.

        Args:
            event (PixPaymentEvent): The parsed event.

        Returns:
            str | None: The order that was settled, or None when the event
            says something else.
        """
        if event.type is not PixEventType.CHARGE_PAID or event.charge is None:
            return None
        await self._orders.mark_paid(event.charge.reference)
        return event.charge.reference
```

Repare no bloco de imports: nada de `adapters`, nada de `openpix`. É a
regra que dá para verificar com `grep` em vez de com revisão.

O `reference` é o id do **seu** pedido, e o `provider_charge_id` é gravado
na mesma transação em que a cobrança nasce. Um é como o webhook te encontra;
o outro é como você volta ao provedor para consultar ou cancelar. Perder o
segundo significa uma cobrança que existe no provedor e que o seu serviço
não sabe mais endereçar.

### Passo 4 — o router devolve schema seu, não `PixCharge`

```python
from fastapi import APIRouter, status

from src.api.dependencies import CheckoutServiceDep
from src.schemas import CheckoutCreateSchema, CheckoutResponseSchema

router: APIRouter = APIRouter(prefix="/api/checkout", tags=["checkout"])


@router.post("/{order_id}", status_code=status.HTTP_201_CREATED)
async def open_checkout(
    order_id: str,
    payload: CheckoutCreateSchema,
    service: CheckoutServiceDep,
) -> CheckoutResponseSchema:
    """Open a Pix charge for an order.

    Args:
        order_id (str): The order to charge.
        payload (CheckoutCreateSchema): How much to charge.
        service (CheckoutServiceDep): The checkout service.

    Returns:
        CheckoutResponseSchema: What the payment screen needs.
    """
    charge = await service.open_charge(order_id, payload.amount_cents)
    return CheckoutResponseSchema(
        order_id=charge.reference,
        amount_cents=charge.amount_cents,
        br_code=charge.br_code,
        qr_code_image_url=charge.qr_code_image_url,
        qr_code_base64=charge.qr_code_base64,
    )
```

!!! danger "`PixCharge` é schema Pydantic — e é por isso que devolvê-lo vaza"
    Nada impede um router de anotar `-> PixCharge`: ele serializa. O
    problema é **o que** serializa. Um `model_dump(mode="json")` de uma
    cobrança tem 14 campos, e dois deles não são do seu cliente:

    ```text
    ['amount_cents', 'br_code', 'currency', 'end_to_end_id', 'expires_at',
     'paid_at', 'provider', 'provider_charge_id', 'provider_status',
     'qr_code_base64', 'qr_code_image_url', 'raw', 'reference', 'status']
    ```

    `raw` é o payload cru do provedor — no caminho da OpenPix é o `Charge`
    inteiro, `customer` incluído, com `name`, `email` e `taxID` do pagador
    (a grafia do fio, que é a que o `raw` usa).
    `provider_charge_id` é a sua chave de escrita no provedor. Um schema de
    resposta próprio, com os campos que a tela usa, é o que separa a sua
    API do payload de um terceiro.

### Passo 5 — o webhook: verificação na borda, contrato dentro

```python
from fastapi import APIRouter

from src.api.dependencies import CheckoutServiceDep, PixProviderDep, WebhookDeliveryDep

router: APIRouter = APIRouter(prefix="/webhooks", include_in_schema=False)


@router.post("/pix")
async def receive_pix(
    delivery: WebhookDeliveryDep,
    provider: PixProviderDep,
    service: CheckoutServiceDep,
) -> dict[str, str | None]:
    """Turn a verified delivery into a settled order.

    Args:
        delivery (WebhookDeliveryDep): The verified delivery.
        provider (PixProviderDep): The provider that parses it.
        service (CheckoutServiceDep): The service that acts on it.

    Returns:
        dict[str, str | None]: The order settled by this delivery, if any.
    """
    event = provider.parse_webhook(delivery)
    return {"settled": await service.settle(event)}
```

O router não importa `OpenPixWebhookEvent`, e não sabe que existe RSA no
caminho: ele recebe `WebhookDeliveryDep`, entrega ao `parse_webhook` do
provider e age sobre o `PixPaymentEvent` que sai. A verificação de
assinatura — a parte que nenhum contrato unifica — ficou inteira dentro do
`Annotated` do composition root.

!!! note "`include_in_schema=False` não é cosmético"
    Webhook não é endpoint da sua API pública: quem se autentica ali é uma
    assinatura, não o token do seu usuário. Com o router fora do schema, o
    `app.openapi()` deste serviço lista uma rota só:

    ```text
    ['/api/checkout/{order_id}']
    ```

### Passo 6 — nos testes, o fake entra pela dependência

O adapter in-memory da última seção desta página não serve só para script:
ele entra no lugar do provedor por `dependency_overrides`, e a suíte inteira
roda sem rede.

```python
from typing import Any

from fastapi.testclient import TestClient

from tempest_fastapi_sdk.integrations.payment import PixProvider

from src.api.app import create_app
from src.api.dependencies import get_pix_provider, verified_delivery
from src.db.repositories import OrderRepository
from tests.fakes import FakePixProvider


def test_checkout_and_webhook() -> None:
    """Charge and settle through the whole stack, on the fake."""
    app = create_app()
    provider: PixProvider = FakePixProvider()
    orders = OrderRepository()
    app.state.orders = orders
    app.dependency_overrides[get_pix_provider] = lambda: provider

    with TestClient(app) as client:
        created = client.post("/api/checkout/pedido-1042", json={"amount_cents": 1990})
        assert created.status_code == 201
        assert created.json()["br_code"] == "000201fake-1"
        assert orders.charge_ids == {"pedido-1042": "fake-1"}

        def fake_delivery() -> Any:
            """Stand in for the verified delivery.

            Returns:
                Any: What this provider's parse_webhook reads.
            """
            return {"charge_id": "fake-1"}

        app.dependency_overrides[verified_delivery] = fake_delivery
        settled = client.post("/webhooks/pix")
        assert settled.json() == {"settled": "pedido-1042"}
        assert orders.paid == {"pedido-1042"}
```

As duas respostas, rodando:

```text
POST /api/checkout/pedido-1042 -> 201 {'order_id': 'pedido-1042', 'amount_cents': 1990, 'br_code': '000201fake-1', 'qr_code_image_url': None, 'qr_code_base64': None}
POST /webhooks/pix -> 200 {'settled': 'pedido-1042'}
```

São dois overrides, e eles são diferentes de propósito. O **provider**
troca o provedor inteiro pelo fake. A **entrega verificada** troca só o
verificador — porque assinar é a parte que o fake não tem como imitar, e
fingir a verificação em teste é melhor do que desligá-la em produção.

!!! tip "O que o type-checker cobra do seu fake"
    A linha `provider: PixProvider = FakePixProvider()` é o que faz o
    `mypy --strict` conferir o fake contra o contrato. Trocando o parâmetro
    de `create_pix_charge` para `str`, a reprovação é imediata e aponta o
    campo:

    ```text
    tests/fakes.py:39: error: "str" has no attribute "amount_cents"  [attr-defined]
    tests/test_checkout.py:18: error: Incompatible types in assignment (expression has type "FakePixProvider", variable has type "PixProvider")  [assignment]
    tests/test_checkout.py:18: note: Following member(s) of "FakePixProvider" have conflicts:
    tests/test_checkout.py:18: note:     Expected:
    tests/test_checkout.py:18: note:         def create_pix_charge(self, request: PixChargeRequest) -> Coroutine[Any, Any, PixCharge]
    tests/test_checkout.py:18: note:     Got:
    tests/test_checkout.py:18: note:         def create_pix_charge(self, request: str) -> Coroutine[Any, Any, PixCharge]
    ```

    Sem a anotação, `dependency_overrides` aceita qualquer callable e o
    defeito só aparece na primeira cobrança.

### O que a troca de provedor custa neste serviço

Uma varredura pelo nome do provedor no serviço acima acha **dois** arquivos:

```text
src/core/settings.py
src/api/dependencies/payments.py
```

O primeiro só porque as credenciais são mesmo do provedor. O segundo é o
composition root — e é a linha `OpenPixPixProvider(OpenPixClient(http))` que
muda quando o adapter for outro. Nem o service, nem os routers, nem os
schemas aparecem nessa lista: é assim que se mede se a costura está no
lugar.

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
| `UNKNOWN` | um estado que esta versão do SDK não classifica |

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

!!! tip "O provedor pode inventar um estado — e isso não derruba a leitura"
    `UNKNOWN` existe porque um provedor adiciona estado sem avisar. As
    duas formas de esconder isso são piores: cair em `PENDING` afirma que
    a cobrança espera pagamento logo depois de o provedor dizer que não; e
    recusar a leitura transforma um estado que o SDK não conhece numa
    requisição falhada, com o estado real em lugar nenhum que você veja.

    A string original fica em `provider_status`, então dá para ramificar
    nela enquanto o mapeamento canônico não existe:

    ```python
    if charge.status is PaymentStatus.UNKNOWN:
        logger.warning("estado novo da OpenPix: %s", charge.provider_status)
    ```

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

    **O `raw` usa a grafia do fio nos dois caminhos**, e é por isso que a
    busca acima é `raw["paymentLinkUrl"]` e não `raw["payment_link_url"]`.
    O nome que o provedor documenta é o único que você consegue prever a
    partir da documentação dele.

!!! warning "Mudou na v0.270.0"
    Até a v0.269.0, o caminho de API dumpava sem `by_alias`, então campo
    **declarado** saía em `snake_case` enquanto campo não-declarado (que
    `extra="allow"` preserva) saía em `camelCase` — o `raw` era uma
    mistura, e `raw["paymentLinkUrl"]` devolvia `None` em toda cobrança
    lida pela API. Serviço que já lia `raw` com `snake_case` precisa
    trocar para o nome do fio.

    A admonition anterior também explicava isso errado. Ela dizia que no
    caminho de API o `raw` era o payload "depois de o schema validar,
    então campos não declarados já foram descartados". Isso deixou de
    valer quando os modelos de resposta viraram `extra="allow"`
    (v0.259/v0.260): `paidAt` **sobrevive** à validação e está no `raw`
    dos dois caminhos. O que continua diferente é só que o adapter da
    OpenPix lê `paid_at` na entrega de webhook e não no caminho de API.

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
- Na arquitetura: o provider é montado em `api/dependencies` e sai de lá como
  `PixProvider`. Um `HTTPClient` por processo, no `lifespan` — e `app.state`
  é `Any`, então reanote o tipo na leitura.
- O router devolve schema seu, não `PixCharge`: a cobrança canônica carrega
  `raw` (o payload do provedor, `customer` incluído) e `provider_charge_id`.
- No teste, `dependency_overrides` troca o provider pelo fake e a entrega
  verificada por um stub — dois overrides, e a suíte roda sem rede.
