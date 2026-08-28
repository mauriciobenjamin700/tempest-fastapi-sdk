# OpenPix (assinaturas e planos)

Esta receita cobre cobrança **recorrente**: mensalidade, plano anual, Pix
Automático. Ela continua de onde a receita de [cobrança avulsa](openpix.md)
parou — a configuração, a arquitetura em camadas e a regra "o webhook avisa, a
API confirma" são as mesmas, e não se repetem aqui.

## A primeira coisa a entender: plano é seu, assinatura é deles

A OpenPix **não tem recurso de plano**. Não existe `POST /api/v1/plan`, não
existe catálogo do lado do fornecedor. O que existe é `subscription`: um
acordo com **um** cliente, por **um** valor, em **uma** frequência.

Isso decide a sua modelagem:

| Conceito | Onde vive | Por quê |
| --- | --- | --- |
| **Plano** ("Pro, R$ 49,90/mês") | Seu banco | É catálogo, preço e regra de negócio — nada disso a OpenPix conhece |
| **Assinatura** (Ana no Pro) | Seu banco **e** a OpenPix | Você guarda o vínculo e o estado; eles geram as cobranças |
| **Cobrança do ciclo** | A OpenPix gera | Cada período vira uma `Charge` comum, com o mesmo webhook |

```text
plans (seu)                 subscriptions (seu)              OpenPix
┌──────────────┐            ┌───────────────────┐            ┌──────────────┐
│ id           │◄───────────│ plan_id           │            │ subscription │
│ name  "Pro"  │            │ user_id           │            │ correlationID│
│ value 4990   │            │ correlation_id  ──┼───────────►│ globalID     │
│ frequency    │            │ status            │            │ ...          │
└──────────────┘            └───────────────────┘            └──────────────┘
```

O `correlationID` da assinatura é a sua chave: use o id da linha em
`subscriptions`, não o id do plano — o mesmo plano tem milhares de assinantes.

## Criando a assinatura

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    Subscription,
    SubscriptionFrequency,
    SubscriptionPayload,
    SubscriptionPayloadCustomer,
    SubscriptionPayloadType,
    reais_to_cents,
)


class SubscriptionService:
    """Regras de assinatura recorrente."""

    def __init__(self, client: OpenPixClient) -> None:
        """Guarda o cliente gerado.

        Args:
            client (OpenPixClient): O cliente da OpenPix.
        """
        self.client: OpenPixClient = client

    async def subscribe(
        self,
        *,
        reference: str,
        plan_name: str,
        amount_brl: str,
        customer_name: str,
        customer_email: str,
        customer_tax_id: str,
        charge_day: int,
    ) -> Subscription:
        """Assina um plano para um cliente.

        Args:
            reference (str): Id da assinatura no seu banco (`correlationID`).
            plan_name (str): Nome do plano, que o pagador vê na cobrança.
            amount_brl (str): Valor do ciclo, em reais.
            customer_name (str): Nome do assinante.
            customer_email (str): E-mail do assinante.
            customer_tax_id (str): CPF ou CNPJ do assinante.
            charge_day (int): Dia do mês em que a cobrança é gerada.

        Returns:
            Subscription: A assinatura criada.

        Raises:
            ValueError: Se a resposta vier sem a assinatura.
        """
        response = await self.client.create_subscription(
            body=SubscriptionPayload(
                correlation_id=reference,
                name=plan_name,
                value=reais_to_cents(amount_brl),
                type=SubscriptionPayloadType.RECURRENT,
                frequency=SubscriptionFrequency.MONTHLY,
                day_generate_charge=charge_day,
                day_due=5,
                customer=SubscriptionPayloadCustomer(
                    name=customer_name,
                    email=customer_email,
                    tax_id=customer_tax_id,
                ),
            )
        )
        if response.subscription is None:
            raise ValueError(f"OpenPix não devolveu a assinatura {reference}")
        return response.subscription
```

O corpo que sai no fio (medido, com `MockTransport`):

```json
{
  "customer": {"name": "Ana", "email": "ana@example.com", "taxID": "11111111111"},
  "value": 4990.0,
  "name": "Plano Pro",
  "dayGenerateCharge": 10.0,
  "frequency": "MONTHLY",
  "type": "RECURRENT",
  "dayDue": 5.0,
  "correlationID": "assinatura-1",
  "additionalInfo": []
}
```

E a resposta traz o `payment_link_url` — a página onde o assinante paga cada
ciclo — junto do `global_id` que a OpenPix usa internamente.

!!! note "Os números saem como float, e isso é a especificação"
    `value`, `dayGenerateCharge` e `dayDue` são `type: number` na spec, então
    o modelo gerado os serializa como `4990.0`, `10.0` e `5.0`. É JSON válido
    e o mesmo valor — mas se você comparar corpos byte a byte em teste, é isso
    que vai ver. Do lado da leitura, `to_cents` desfaz o float para centavo
    inteiro.

### Os campos que decidem o comportamento

| Campo | O que faz |
| --- | --- |
| `frequency` | Intervalo entre ciclos: `WEEKLY`, `MONTHLY`, `BIMONTHLY`, `QUARTERLY`, `SEMIANNUALLY`, `ANNUALLY`. Omitido, vira `MONTHLY` |
| `day_generate_charge` | Dia do mês em que a cobrança do ciclo **nasce** |
| `day_due` | Quantos dias depois disso ela **vence** |
| `installment_count` | Número total de ciclos. Sem ele, a assinatura não tem fim |
| `charge_type` | Como cada cobrança é emitida: `DYNAMIC` (Pix comum), `OVERDUE` (com juros e multa) ou `BOLETO` |
| `type` | `RECURRENT` ou `PIX_RECURRING` — a diferença está logo abaixo |

!!! tip "`installment_count` é o que separa assinatura de parcelamento"
    Sem ele você tem mensalidade: cobra até alguém cancelar. Com
    `installment_count=12`, você tem um parcelado em 12 vezes que se encerra
    sozinho — e `installments_count` na resposta volta `None` justamente
    quando a assinatura é aberta.

## `RECURRENT` ou `PIX_RECURRING`: a escolha que muda o produto

```python
from tempest_fastapi_sdk.integrations.payment.openpix import SubscriptionPayloadType

manual = SubscriptionPayloadType.RECURRENT
automatic = SubscriptionPayloadType.PIX_RECURRING
```

| | `RECURRENT` | `PIX_RECURRING` (Pix Automático) |
| --- | --- | --- |
| Como o dinheiro sai | O assinante paga cada cobrança | Debitado da conta dele, sem ação |
| Autorização | Nenhuma, é uma cobrança por ciclo | O pagador autoriza uma vez, no banco dele |
| Inadimplência | Cobrança expira | O banco tenta de novo, conforme a `retryPolicy` |
| Frequências | As seis | Sem `BIMONTHLY` — o Banco Central não permite |
| Eventos de webhook | `OPENPIX:CHARGE_*` | `PIX_AUTOMATIC_*`, **sem** o prefixo `OPENPIX:` |

O Pix Automático leva opções próprias:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    SubscriptionFrequency,
    SubscriptionPayload,
    SubscriptionPayloadCustomer,
    SubscriptionPayloadPixRecurringOptions,
    SubscriptionPayloadType,
)

payload = SubscriptionPayload(
    correlation_id="assinatura-2",
    name="Plano Pro",
    value=4990,
    type=SubscriptionPayloadType.PIX_RECURRING,
    frequency=SubscriptionFrequency.MONTHLY,
    customer=SubscriptionPayloadCustomer(name="Ana", tax_id="11111111111"),
    pix_recurring_options=SubscriptionPayloadPixRecurringOptions(
        minimum_value=1000,
    ),
)
```

!!! warning "Os eventos do Pix Automático não têm o prefixo `OPENPIX:`"
    Se o seu handler filtra por `event_name.startswith("OPENPIX:")`, ele
    descarta a família inteira em silêncio. Compare com os membros de
    `OpenPixEvent`, não com strings:
    `OpenPixEvent.PIX_AUTOMATIC_APPROVED.value == "PIX_AUTOMATIC_APPROVED"`.

## Recebendo o dinheiro de cada ciclo

Aqui não há API nova: **cada ciclo vira uma cobrança comum**, com o mesmo
`OPENPIX:CHARGE_COMPLETED` da receita de cobrança avulsa. O que muda é que a
cobrança carrega a assinatura.

```python
from tempest_fastapi_sdk.integrations.payment.openpix import Charge, OpenPixEvent
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixWebhookEvent


def subscription_of(event: OpenPixWebhookEvent) -> str | None:
    """Descobre a que assinatura uma cobrança paga pertence.

    Args:
        event (OpenPixWebhookEvent): A entrega verificada.

    Returns:
        str | None: O `correlationID` da assinatura, ou `None` quando a
        cobrança é avulsa.
    """
    if event.event is not OpenPixEvent.CHARGE_COMPLETED:
        return None
    charge = Charge.model_validate(event.payload["charge"])
    if charge.subscription is None:
        return None
    return charge.subscription.correlation_id
```

Uma cobrança sem `subscription` é avulsa — trate pelo caminho da outra
receita. Com `subscription`, o que você está recebendo é a mensalidade: marque
o ciclo como pago e empurre a data de renovação.

Para listar o histórico de uma assinatura:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient


async def charges_of(client: OpenPixClient, reference: str) -> list[str]:
    """Lista as cobranças geradas por uma assinatura.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        reference (str): O `correlationID` da assinatura.

    Returns:
        O status de cada cobrança do ciclo, da mais antiga à mais recente.
    """
    response = await client.list_charges(subscription=reference)
    return [str(charge.status) for charge in response.charges]
```

## Ciclo de vida

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    Installment,
    OpenPixClient,
    Subscription,
)


async def read(client: OpenPixClient, reference: str) -> Subscription | None:
    """Lê o estado atual de uma assinatura.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        reference (str): O `correlationID` ou o `globalID` da assinatura.

    Returns:
        Subscription | None: A assinatura, ou `None` se não existir.
    """
    response = await client.get_subscription(id=reference)
    return response.subscription


async def installments(client: OpenPixClient, global_id: str) -> list[Installment]:
    """Lista as parcelas já geradas.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        global_id (str): O `globalID` da assinatura — este endpoint **não**
            aceita o `correlationID`.

    Returns:
        As parcelas, com número, valor, status e data de geração.
    """
    response = await client.list_subscription_installments(id=global_id)
    return response.installments


async def cancel(client: OpenPixClient, reference: str) -> None:
    """Encerra a assinatura.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        reference (str): O `correlationID` ou o `globalID` da assinatura.
    """
    await client.cancel_subscription(id=reference)
```

!!! warning "`installments` pede o `globalID`, os outros aceitam os dois"
    Está na própria especificação: `get_subscription` e
    `cancel_subscription` documentam *"the globalID or
    correlationID"*, enquanto o de parcelas documenta *"the globalID"*. Guarde
    o `global_id` da resposta de criação no seu banco — sem ele, você precisa
    de uma leitura extra só para listar parcelas.

### Mudar o valor: a operação que a especificação deixou incompleta

`PUT /api/v1/subscriptions/{id}/value` existe e serve para reajustar as
próximas parcelas de uma assinatura de Pix Automático com valor dinâmico. Mas
a especificação **não declara corpo nenhum** para ela — conferido no
`vendor/openpix-openapi.json`: a operação tem só o parâmetro de path. O
cliente gerado reflete isso fielmente:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient


async def bump(client: OpenPixClient, reference: str) -> None:
    """Chama o endpoint exatamente como a especificação o descreve.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        reference (str): O `correlationID` da assinatura.
    """
    await client.update_subscription_value(id=reference)
```

Se a sua conta precisa mandar o valor novo, envie pelo `HTTPClient` — é o
mesmo transporte, com os mesmos headers, retry e circuit breaker:

```python
from tempest_fastapi_sdk import HTTPClient


async def bump_to(http: HTTPClient, reference: str, cents: int) -> None:
    """Reajusta o valor das próximas parcelas.

    Args:
        http (HTTPClient): O transporte já autenticado.
        reference (str): O `correlationID` da assinatura.
        cents (int): O novo valor, em centavos.
    """
    response = await http.request(
        "PUT",
        f"/api/v1/subscriptions/{reference}/value",
        json={"value": cents},
    )
    response.raise_for_status()
```

O gerador não inventa o que a especificação não diz — se ele adivinhasse um
corpo, você descobriria o erro em produção, não aqui.

## O estado que fica do seu lado

A OpenPix sabe se a cobrança do ciclo foi paga. Ela não sabe se o **seu**
usuário tem acesso. Essa máquina de estados é sua:

```text
                 cobrança do ciclo paga
    ┌────────┐ ─────────────────────────► ┌────────┐
    │ criada │                            │ ativa  │
    └────────┘ ◄───────────────────────── └────────┘
        │        cobrança do próximo ciclo    │
        │                                     │ ciclo venceu sem pagamento
        │                                     ▼
        │                              ┌──────────────┐
        │                              │ inadimplente │
        │                              └──────────────┘
        │  cancelamento                       │
        └──────────────► ┌───────────┐ ◄──────┘
                         │ cancelada │
                         └───────────┘
```

Duas regras que evitam o bug clássico de assinatura:

1. **Acesso vence por data, não por evento.** Guarde `access_until` e empurre
   a data quando o ciclo é pago. Se você guardar só um booleano `is_active`,
   um webhook perdido deixa o usuário sem acesso mesmo tendo pagado — ou com
   acesso eterno depois de cancelar.
2. **Reconcilie por parcela.** O job periódico compara as parcelas da OpenPix
   com os ciclos que você registrou; o que estiver pago lá e aberto aqui é
   webhook perdido.

```python
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient


async def unpaid_cycles(client: OpenPixClient, global_id: str) -> list[float]:
    """Lista os números das parcelas que ainda não foram pagas.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        global_id (str): O `globalID` da assinatura.

    Returns:
        Os `installment_number` das parcelas em aberto.
    """
    response = await client.list_subscription_installments(id=global_id)
    return [
        parcel.installment_number or 0.0
        for parcel in response.installments
        if parcel.status != "COMPLETED"
    ]
```

## Recapitulando

1. **A OpenPix não tem planos.** O catálogo é seu; a assinatura é o vínculo de
   um cliente com um valor e uma frequência.
2. **`correlationID` da assinatura é a linha do seu banco**, não o plano.
   Guarde também o `global_id` que volta na criação — o endpoint de parcelas
   só aceita ele.
3. **`RECURRENT` cobra, `PIX_RECURRING` debita.** A segunda opção tem
   frequências restritas pelo Banco Central e eventos de webhook **sem** o
   prefixo `OPENPIX:`.
4. **Cada ciclo é uma cobrança comum** — mesmo webhook, mesma conferência pela
   API, e `charge.subscription` é o que diz de qual assinatura ela veio.
5. **`update_subscription_value` não tem corpo na especificação.**
   Mande pelo `HTTPClient` se a sua conta precisar.
6. **O acesso do usuário vence por data**, empurrada a cada ciclo pago, e é
   reconciliado pela lista de parcelas.
