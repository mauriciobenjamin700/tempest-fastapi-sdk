# Stripe (cartão, assinatura, Checkout)

Todo serviço nosso que cobra cartão reescrevia a mesma camada Stripe:
cliente HTTP síncrono chamado de dentro de rota async, schemas
redigitados à mão, e uma verificação de webhook que cada um derivava do
seu jeito. `tempest_fastapi_sdk.integrations.payment.stripe` entrega essa
camada pronta.

!!! info "Instalação"
    Nenhum extra. A integração usa o `HTTPClient` que o SDK já tem —
    `uv add "tempest-fastapi-sdk[http]"` se você ainda não puxou o
    `httpx`.

!!! info "Quando usar isto"
    Cobrança internacional com cartão, assinatura ou Checkout hospedado.
    Para **Pix**, use [OpenPix](openpix.md), que é gerada da spec do
    provedor.

## O caminho mínimo

```python
import asyncio
from decimal import Decimal

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
    to_minor_units,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    intent = await client.payment_intents.create(
        {
            "amount": to_minor_units(Decimal("199.90"), "brl"),
            "currency": "brl",
            "automatic_payment_methods": {"enabled": True},
            "metadata": {"order_id": "1042"},
        }
    )
    print(intent.id, intent.status, intent.client_secret)


asyncio.run(main())
```

Três coisas aconteceram sem você pedir: o corpo saiu **form-encoded** com
notação de colchetes (`metadata[order_id]=1042`), a requisição levou um
`Idempotency-Key`, e o header `Stripe-Version` fixou a versão da API.

!!! danger "`client_secret` é credencial"
    Ele autoriza confirmar **aquele** pagamento. Vai na resposta para o
    dono do pedido e em mais lugar nenhum — nunca em log, nunca em
    telemetria.

## Como funciona, peça por peça

### O corpo é form-encoded, não JSON

A Stripe **não aceita JSON** em escrita: todas as 588 operações de
escrita da spec declaram `application/x-www-form-urlencoded`. Aninhamento
vira colchete:

```python
from tempest_fastapi_sdk import form_encode

print(
    form_encode(
        {
            "mode": "payment",
            "line_items": [{"price": "price_123", "quantity": 2}],
            "metadata": {"order_id": "1042"},
        }
    )
)
```

```text
{'mode': 'payment',
 'line_items[0][price]': 'price_123',
 'line_items[0][quantity]': '2',
 'metadata[order_id]': '1042'}
```

`form_encode` é público e serve a qualquer API form-encoded — e o
**gerador de integrações do SDK agora usa a mesma função**: uma operação
cujo `requestBody` declara form sai com `data=form_encode(payload)` em
vez de `json=payload`. Antes disso, um cliente gerado contra a Stripe
tinha 100% das escritas rejeitadas.

Detalhes que o `form_encode` decide por você:

| Valor | Vai como | Por quê |
| --- | --- | --- |
| `True` / `False` | `true` / `false` | `str(True)` mandaria `"True"` |
| `None` | **não vai** | string vazia é valor real, ela *limpa* o campo |
| `Decimal("10.50")` | `10.50` | passar por `float` perde centavo |
| `Enum` | o `.value` | `str()` mandaria `"Classe.MEMBRO"` |
| `datetime` | ISO-8601 | |

### Dinheiro: moeda de zero decimais existe

A Stripe cobra em **menor unidade**. Para a maioria isso é centavo, mas
para JPY, KRW, VND e outras 13 a menor unidade **é** a unidade — e
dividir tudo por 100 é um bug de cobrança silencioso que nenhum teste em
BRL pega.

```python
from decimal import Decimal

from tempest_fastapi_sdk.integrations.payment.stripe import (
    from_minor_units,
    to_minor_units,
)

print(to_minor_units(Decimal("10.50"), "brl"))    # 1050
print(to_minor_units(Decimal("1050"), "jpy"))     # 1050  <- não 105000
print(from_minor_units(1050, "jpy"))              # Decimal('1050')
print(to_minor_units(Decimal("10.505"), "bhd"))   # 10505 (três decimais)
```

As duas tabelas (`ZERO_DECIMAL_CURRENCIES`, `THREE_DECIMAL_CURRENCIES`)
vêm da documentação da Stripe e são travadas por teste, então uma mudança
lá aparece como falha aqui — não como cobrança cem vezes maior.

### Webhook: o que a Stripe assina não é o corpo

O header é `t=<timestamp>,v1=<hmac>`, e o HMAC cobre `f"{t}.{body}"`.
Assinar só o corpo é o erro mais comum de verificação escrita à mão — e
ele falha *silenciosamente* como 401 em toda entrega.

```python
from typing import Any

from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeEvent,
    StripeWebhookEvent,
    make_stripe_webhook_dependency,
)

from src.core.settings import settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
verified = make_stripe_webhook_dependency(settings.STRIPE_WEBHOOK_SECRET)


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(
    event: StripeWebhookEvent = Depends(verified),
) -> dict[str, Any]:
    """Recebe uma entrega já verificada e tipada."""
    if event.event is StripeEvent.PAYMENT_INTENT_SUCCEEDED:
        return {"handled": True, "intent": event.data_object["id"]}
    return {"handled": False, "type": event.event_type}
```

- Assinatura inválida, ausente ou fora da janela de 5 minutos → **401**,
  antes do seu handler.
- Rotação de segredo funciona: a Stripe manda um `v1` por segredo ativo e
  qualquer um que bata é aceito.
- Tipo de evento desconhecido **não** derruba a rota — `event` fica
  `None` e `event_type` guarda a string. Um release da Stripe não vira
  incidente seu.
- `event_id` existe para **deduplicar**: entregas repetem por desenho.

!!! warning "Assinatura verificada não é autorização"
    Ela prova que a entrega veio da Stripe. Não prova que o estado que
    você vai mudar é o atual — entregas repetem e chegam fora de ordem.
    Antes de liberar mercadoria em `payment_intent.succeeded`, releia o
    intent pela API.

Para testar sem inventar a assinatura errada, o módulo exporta o
assinador:

```python
import json

from tempest_fastapi_sdk.integrations.payment.stripe import sign_payload

body = json.dumps({"id": "evt_1", "type": "payment_intent.succeeded"}).encode()
header = sign_payload(body, "whsec_test", timestamp=1_770_000_000)
```

### Os 265 tipos de evento, como enum

`StripeEvent` é **gerado** da spec — do parâmetro `enabled_events` de
`POST /v1/webhook_endpoints`, que é onde a Stripe enumera os tipos (o
objeto `event` não os enumera). Um teste de drift falha se o arquivo for
editado à mão.

```python
from tempest_fastapi_sdk.integrations.payment.stripe import StripeEvent

print(StripeEvent.INVOICE_PAID.value)               # invoice.paid
print(StripeEvent.has_value("customer.created"))    # True
```

### Idempotência: por que toda escrita leva chave

Um timeout no `POST /v1/payment_intents` deixa você sem saber se o
dinheiro se moveu. Retry sem chave cria um segundo pagamento; com chave,
a Stripe repete a resposta original por 24 horas. O cliente põe um UUID4
em toda escrita — e você troca por uma chave sua quando quiser que a
janela cubra o seu fluxo:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    order_id = "1042"
    await client.payment_intents.create(
        {"amount": 19990, "currency": "brl"},
        idempotency_key=f"order-{order_id}",
    )


asyncio.run(main())
```

### Paginação por cursor

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    filters = {"created": {"gte": 1_770_000_000}}
    async for customer in client.customers.auto_paginate(filters):
        print(customer.id, customer.email)


asyncio.run(main())
```

O cursor é o **id do último item**, não um offset, então a varredura
continua correta enquanto objetos são criados por baixo dela.

### Erros que dizem o que houve

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    StripeError,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    try:
        await client.payment_intents.create({"amount": 19990, "currency": "brl"})
    except StripeError as error:
        print(error.status_code)    # 402
        print(error.error_type)     # card_error
        print(error.code)           # card_declined
        print(error.decline_code)   # insufficient_funds
        print(error.request_id)     # req_... (o que o suporte da Stripe pede)


asyncio.run(main())
```

### Modelos finos, e nada se perde

Os modelos nomeiam os campos que carregam decisão — `status`, `amount`,
`currency`, os ids que ligam objetos — e usam `extra="allow"`. O resto do
objeto continua acessível:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    intent = await client.payment_intents.retrieve("pi_123")
    print(intent.status)                                  # tipado
    print((intent.model_extra or {}).get("next_action"))  # preservado


asyncio.run(main())
```

Campo expansível (`customer`, `payment_intent`, …) é `str`: sem
`expand`, a Stripe manda o id. Peça o objeto e leia do `model_extra`:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


async def main() -> None:
    """Run this example."""
    client = StripeClient(stripe_http_client("sk_test_..."))
    intent = await client.payment_intents.retrieve(
        "pi_123", params={"expand": ["customer"]}
    )
    print(intent.customer)


asyncio.run(main())
```

!!! note "Por que este cliente é escrito à mão, e não gerado"
    O SDK gera integrações de OpenAPI — a OpenPix é gerada. A spec da
    Stripe não sobrevive à viagem, e isso foi **medido** na
    `2026-07-29.dahlia`:

    - gerar a superfície inteira dá `schemas.py` com **3,3 MB / 81 mil
      linhas**, e importar custa **5,8 s e 492 MB de RSS**;
    - fatiar por recurso não resolve: `/v1/prices` **sozinho** alcança 864
      dos 1440 schemas, e os dez recursos centrais juntos, 1020. Não
      existe subconjunto pequeno.

    Então o que vem da spec continua vindo dela — versão da API, base URL
    e os 265 eventos, via `make stripe-fetch` — e o resto é código que
    cabe na cabeça. Os números e como refazê-los estão em
    `scripts/regen_stripe.py`.

## Configuração

```python
from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)


class Settings(BaseAppSettings):
    """Settings do serviço."""

    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""


settings = Settings()
client = StripeClient(stripe_http_client(settings.STRIPE_API_KEY))
```

O segredo do webhook é **por endpoint**, não por conta: um serviço com
endpoint de teste e de produção tem dois.

## Testando

Injete um `httpx.MockTransport` e inspecione a requisição que sairia:

```python
from urllib.parse import parse_qs

import httpx

from tempest_fastapi_sdk.integrations.payment.stripe import (
    StripeClient,
    stripe_http_client,
)

requests: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    """Registra a requisição e devolve um cliente falso."""
    requests.append(request)
    return httpx.Response(200, json={"id": "cus_1", "object": "customer"})


async def test_create_customer() -> None:
    """A escrita sai form-encoded com colchetes."""
    client = StripeClient(
        stripe_http_client("sk_test_x", transport=httpx.MockTransport(handler))
    )

    await client.customers.create({"email": "ana@example.com", "metadata": {"o": "1"}})

    assert parse_qs(requests[-1].content.decode()) == {
        "email": ["ana@example.com"],
        "metadata[o]": ["1"],
    }
```

## Recap

- `StripeClient` sobre o `HTTPClient` do SDK: retry, circuit breaker e
  `Stripe-Version` fixo vêm de um lugar só.
- Escrita é form-encoded com colchetes (`form_encode`, público e usado
  também pelo gerador) e leva `Idempotency-Key` por padrão.
- Dinheiro respeita moeda de zero e de três decimais.
- Webhook verifica sobre `t.body`, com janela de replay, rotação de
  segredo e evento desconhecido que não derruba a rota.
- 265 eventos como enum, gerados da spec com teste de drift.
- Modelos finos com `extra="allow"` — o cliente é escrito à mão porque
  gerar a spec inteira custa 3,3 MB e 492 MB de RSS, medidos.
