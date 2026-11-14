# Mercado Pago: testando webhooks

Webhook é a parte mais difícil de testar de uma integração de pagamento: quem
chama é o Mercado Pago, não você, e ele precisa de uma URL pública para
chamar. Esta página mostra quatro níveis de teste, do que roda sem rede
nenhuma até a entrega real:

1. **Simulado pelo SDK, sem rede** — a API de Orders e a notificação são
   simuladas; o adapter de verdade cria, relê e estorna sobre elas. Nem
   credencial, nem sandbox. Comece por aqui — na suíte **e** no teste manual.
2. **Simulação assinada sobre o sandbox** — você assina uma notificação com o
   seu segredo e entrega na sua rota. A rota relê uma order **real** do
   sandbox. Não precisa de URL pública.
3. **"Simular notificação" do painel** — o Mercado Pago manda uma
   notificação de teste para a sua URL.
4. **Entrega real** — você cria uma order e o Mercado Pago avisa sozinho.

Antes, tenha em mãos as credenciais da receita
[contas e credenciais de teste](mercado-pago-sandbox.md) e entenda o que a
rota faz em [Mercado Pago »](mercado-pago.md#o-webhook-pelo-contrato-relendo-a-order):
ela verifica a assinatura e **relê a order**, porque a notificação não traz o
estado do pagamento.

## Nível 1 — simulado pelo SDK, sem rede

O SDK traz o Mercado Pago simulado em `tempest_fastapi_sdk.testing.fakes`:

- `FakeMercadoPagoOrdersAPI` responde `/v1/orders` com estado — cria
  (idempotente pela `X-Idempotency-Key`), relê, cancela, captura e estorna. É
  um handler do `httpx`, então o `MercadoPagoPixProvider` **de verdade** roda
  sobre ele sem mudança nenhuma: `MercadoPagoPixProvider(api.http_client())`.
- Os movimentos que só o provedor faz são métodos — `pay`, `expire`, `fail`,
  `cancel`, `capture`, `refund` — e cada um devolve a **notificação assinada**
  que anuncia o novo estado, pronta para `delivery.send(client, rota)`.
- `build_mercado_pago_delivery(order, secret=...)` monta a notificação
  sozinha, no formato da entrega real observada em produção, assinada pelo
  mesmo `sign_manifest` que o verificador do SDK usa.

O script cria cobranças pelo adapter, move cada uma e entrega a notificação
na rota montada como o seu serviço monta:

```python
import asyncio
from typing import Any

import httpx
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.testing.fakes import (
    FakeMercadoPagoOrdersAPI,
    MercadoPagoDelivery,
)

SECRET: str = "segredo-local-de-teste"


def build_app(provider: MercadoPagoPixProvider) -> FastAPI:
    """Mount the webhook route exactly as the service would."""
    app = FastAPI()
    dependency = make_mercado_pago_webhook_delivery_dependency(SECRET, provider)

    @app.post("/webhooks/mercado-pago")
    async def webhook(
        delivery: MercadoPagoOrderDelivery = Depends(dependency),
    ) -> dict[str, Any]:
        """Report what the re-read decided."""
        event = provider.parse_webhook(delivery)
        return {
            "type": event.type.value,
            "status": event.charge.status.value if event.charge else None,
        }

    return app


async def deliver(app: FastAPI, delivery: MercadoPagoDelivery) -> tuple[int, Any]:
    """Send one signed delivery to the route, in-process."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        answer = await delivery.send(client, "/webhooks/mercado-pago")
    return answer.status_code, answer.json()


async def main() -> None:
    """Run payment, refund, cancellation and expiry with no network."""
    api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
    provider = MercadoPagoPixProvider(api.http_client(), action_retry_delays=())
    app = build_app(provider)
    payer = PixPayer(email="comprador@example.com")

    async def new_charge(reference: str) -> str:
        """Create a R$ 19,90 Pix charge through the real adapter."""
        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=1990, reference=reference, payer=payer)
        )
        return charge.provider_charge_id

    paid = await new_charge("pedido-1")
    print("criada    ", await deliver(app, api.delivery(paid)))
    print("paga      ", await deliver(app, api.pay(paid)))
    await provider.refund_pix_charge(paid, 500, refund_reference="estorno-1")
    print("parcial   ", await deliver(app, api.delivery(paid)))
    await provider.refund_pix_charge(paid, refund_reference="estorno-2")
    print("estornada ", await deliver(app, api.delivery(paid)))

    cancelled = await new_charge("pedido-2")
    await provider.cancel_pix_charge(cancelled)
    print("cancelada ", await deliver(app, api.delivery(cancelled)))
    print("vencida   ", await deliver(app, api.expire(await new_charge("pedido-3"))))

    stranger = FakeMercadoPagoOrdersAPI(webhook_secret="outro-segredo")
    await MercadoPagoPixProvider(stranger.http_client()).create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="x", payer=payer)
    )
    print("forjada   ", await deliver(app, stranger.delivery(stranger.order_ids[0])))
    other = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
    await MercadoPagoPixProvider(other.http_client()).create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="y", payer=payer)
    )
    print("simulada  ", await deliver(app, other.delivery(other.order_ids[0])))


asyncio.run(main())
```

Nenhuma variável de ambiente, nenhuma rede:

```bash
python simular_offline.py
```

Saída medida em 2026-11-14:

```text
criada     (200, {'type': 'charge_created', 'status': 'pending'})
paga       (200, {'type': 'charge_paid', 'status': 'paid'})
parcial    (200, {'type': 'charge_paid', 'status': 'paid'})
estornada  (200, {'type': 'charge_refunded', 'status': 'refunded'})
cancelada  (200, {'type': 'charge_cancelled', 'status': 'cancelled'})
vencida    (200, {'type': 'charge_expired', 'status': 'expired'})
forjada    (401, {'detail': 'Invalid Mercado Pago webhook signature'})
simulada   (200, {'type': 'unknown', 'status': None})
```

| Linha | O que aconteceu | Por que importa |
| --- | --- | --- |
| `criada` | order recém-criada, relida `action_required` → `charge_created` | o tipo do evento sai da **releitura**, não do corpo |
| `paga` | `api.pay` → relida `processed` → `charge_paid` | é o caminho que libera o pedido; agora roda na suíte |
| `parcial` | estorno de R$ 5,00 → continua `processed` → `charge_paid` | estorno parcial não é estorno total |
| `estornada` | o resto estornado → `refunded` → `charge_refunded` | o adapter soma os estornos pela releitura |
| `cancelada` | cancelada pelo adapter → `charge_cancelled` | |
| `vencida` | `api.expire` → `canceled` / `expired` → `charge_expired` | vencer não é cancelar: são eventos distintos no contrato |
| `forjada` | outro segredo → `401`, sem chamada à API | quem não tem o segredo não chega na releitura |
| `simulada` | id que a API não conhece → `404` na releitura → `200` sem cobrança | é o caso de um id que não é seu, como o que o painel assina (nível 3) |

!!! tip "Na sua suíte"
    O mesmo código vira teste: troque os `print` por `assert`. A suíte do SDK
    faz exatamente isso em
    `tests/integrations/payment/adapters/test_mercado_pago_simulated_flow.py`
    — Pix pago, estornado em duas vezes, cancelado, vencido, e cartão
    aprovado, autorizado e capturado, e recusado
    (`api.decline_next_card()`).

!!! warning "O que a simulação copia, e o que ela supõe"
    Os estados de cada transição, os códigos de resposta (`201`, `200`,
    `402`, `404`), a regra de idempotência e o `409 cannot_refund_order` foram
    copiados do sandbox. O formato da notificação foi copiado de **uma**
    entrega real, `order.canceled`. As outras ações (`order.processed`,
    `order.refunded`, `order.failed`, `order.action_required`) **não foram
    observadas** e também não estão no documento que o SDK vendoriza, que só
    lista `order.created` / `order.updated`: o simulador as nomeia pelo
    status da order. A rota do SDK não decide pela ação — relê a order —, então
    isso não muda o resultado; muda só se o **seu** código ler `action`.

### Teste manual: o serviço rodando, o Mercado Pago simulado

O mesmo simulador sobe como servidor, e o serviço aponta para ele por
`MERCADOPAGO_BASE_URL` (campo do `MercadoPagoSettings`, default
`https://api.mercadopago.com`). Um serviço mínimo, com a rota de criar
cobrança e a de webhook:

```python
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.settings import MercadoPagoSettings

settings = MercadoPagoSettings()
provider = MercadoPagoPixProvider(HTTPClient(**settings.mercado_pago_kwargs()))
app = FastAPI()
webhook_dependency = make_mercado_pago_webhook_delivery_dependency(
    settings.MERCADOPAGO_WEBHOOK_SECRET, provider
)


@app.post("/charges")
async def create_charge(reference: str) -> dict[str, Any]:
    """Create a R$ 19,90 Pix charge."""
    charge = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference=reference,
            payer=PixPayer(email="comprador@example.com"),
        )
    )
    return {"order_id": charge.provider_charge_id, "status": charge.status.value}


@app.post("/webhooks/mercado-pago")
async def webhook(
    delivery: MercadoPagoOrderDelivery = Depends(webhook_dependency),
) -> dict[str, Any]:
    """Report what the re-read decided."""
    event = provider.parse_webhook(delivery)
    return {
        "type": event.type.value,
        "status": event.charge.status.value if event.charge else None,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
```

Em três terminais (a porta `8001` aqui só porque a `8000` estava ocupada na
medição; `--url` tem default `http://127.0.0.1:8000/webhooks/mercado-pago`):

```bash
tempest mercado-pago fake-api            # http://127.0.0.1:8090

export MERCADOPAGO_BASE_URL=http://127.0.0.1:8090
export MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste
export MERCADOPAGO_ACCESS_TOKEN=qualquer
python servico.py                        # http://127.0.0.1:8001

export MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste
```

e então crie a cobrança pelo serviço e mova a order com `--transition`
(`pay`, `expire`, `fail`, `cancel`, `capture`, `refund` com
`--amount-cents` opcional). O comando aplica a transição no `fake-api`,
assina a notificação e a entrega na rota. Saída medida em 2026-11-14:

```text
$ curl -s -X POST "http://127.0.0.1:8001/charges?reference=pedido-42"
{"order_id":"ORDFAKE5EBD2F1800000000000002","status":"pending"}

$ tempest mercado-pago simulate-webhook --url http://127.0.0.1:8001/webhooks/mercado-pago \
    --fake-api http://127.0.0.1:8090 --order-id ORDFAKE5EBD2F1800000000000002 --transition pay
POST http://127.0.0.1:8001/webhooks/mercado-pago?data.external_reference=pedido-42&data.id=ORDFAKE5EBD2F1800000000000002&type=order
action: order.processed
HTTP 200
{"type":"charge_paid","status":"paid"}

$ tempest mercado-pago simulate-webhook --url http://127.0.0.1:8001/webhooks/mercado-pago \
    --fake-api http://127.0.0.1:8090 --order-id ORDFAKE5EBD2F1800000000000002 --transition refund --amount-cents 500
POST http://127.0.0.1:8001/webhooks/mercado-pago?data.external_reference=pedido-42&data.id=ORDFAKE5EBD2F1800000000000002&type=order
action: order.processed
HTTP 200
{"type":"charge_paid","status":"paid"}

$ tempest mercado-pago simulate-webhook --url http://127.0.0.1:8001/webhooks/mercado-pago \
    --fake-api http://127.0.0.1:8090 --order-id ORDFAKE5EBD2F1800000000000002 --transition refund
POST http://127.0.0.1:8001/webhooks/mercado-pago?data.external_reference=pedido-42&data.id=ORDFAKE5EBD2F1800000000000002&type=order
action: order.refunded
HTTP 200
{"type":"charge_refunded","status":"refunded"}
```

Com outro segredo, a rota recusa e o comando sai com código `1`:

```text
$ MERCADOPAGO_WEBHOOK_SECRET=errado tempest mercado-pago simulate-webhook --url http://127.0.0.1:8001/webhooks/mercado-pago \
    --fake-api http://127.0.0.1:8090 --order-id ORDFAKE5EBD2F1800000000000002
POST http://127.0.0.1:8001/webhooks/mercado-pago?data.external_reference=pedido-42&data.id=ORDFAKE5EBD2F1800000000000002&type=order
action: order.refunded
HTTP 401
{"detail":"Invalid Mercado Pago webhook signature"}
```

!!! danger "O segredo nunca vai na linha de comando"
    `simulate-webhook` não tem opção `--secret`: o valor ficaria no histórico
    do shell e no `ps`. Ele lê de uma variável (`--secret-env`, default
    `MERCADOPAGO_WEBHOOK_SECRET`) ou de um arquivo (`--secret-file`), e nunca
    imprime o valor — sem ele, sai com código `2` dizendo **qual** variável
    faltou.

!!! warning "`MERCADOPAGO_BASE_URL` fora do default é violação em produção"
    O access token vai para o host que esse campo nomeia. Num `Settings` que
    compõe `EnvironmentSettings`, com `ENV=production`, qualquer valor
    diferente de `https://api.mercadopago.com` entra em
    `production_violations()` e a construção do `Settings` levanta
    `ValidationError` (`MERCADOPAGO_BASE_URL: not the Mercado Pago API host`).

Sem `--fake-api`, o comando assina só o id (`--order-id`) ou a order de um
arquivo (`--order-json`). Contra um serviço que relê a order no Mercado Pago
de verdade, um id inventado dá `404` na releitura — é o caso `simulada`
acima.

## Nível 2 — simulação assinada sobre o sandbox

A assinatura do Mercado Pago é um HMAC-SHA256 sobre `data.id`,
`x-request-id` e `ts`, com o segredo do webhook. O SDK expõe a mesma conta em
`sign_manifest`, então você consegue produzir uma notificação que a sua rota
aceita — e testar o caminho inteiro sem expor nada.

O script cria um Pix real no sandbox, monta a rota como o seu serviço monta e
entrega quatro notificações:

```python
import asyncio
import os
import time
import uuid
from typing import Any

from fastapi import Depends, FastAPI
import httpx

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    sign_manifest,
)

SECRET: str = os.environ.get("MERCADO_PAGO_WEBHOOK_SECRET", "segredo-local-de-teste")


def build_app(provider: MercadoPagoPixProvider) -> FastAPI:
    """Mount the webhook route exactly as the service would."""
    app = FastAPI()
    dependency = make_mercado_pago_webhook_delivery_dependency(SECRET, provider)

    @app.post("/webhooks/mercado-pago")
    async def webhook(
        delivery: MercadoPagoOrderDelivery = Depends(dependency),
    ) -> dict[str, Any]:
        """Report what the re-read decided."""
        event = provider.parse_webhook(delivery)
        return {
            "type": event.type.value,
            "status": event.charge.status.value if event.charge else None,
        }

    return app


def signed_headers(order_id: str) -> dict[str, str]:
    """Sign a notification for this order the way Mercado Pago does."""
    request_id = str(uuid.uuid4())
    ts = str(int(time.time()))
    digest = sign_manifest(
        secret=SECRET, data_id=order_id, request_id=request_id, timestamp=ts
    )
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": request_id}


async def notify(app: FastAPI, order_id: str, headers: dict[str, str]) -> tuple[int, Any]:
    """Deliver one notification to the local route, in-process."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        answer = await client.post(
            f"/webhooks/mercado-pago?data.id={order_id}&type=order",
            headers=headers,
            json={"action": "order.updated", "type": "order", "data": {"id": order_id}},
        )
    return answer.status_code, answer.json()


async def main() -> None:
    """Create a real sandbox order, then notify the local route about it."""
    token: str = os.environ["MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN"]
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as http:
        provider = MercadoPagoPixProvider(http)
        charge = await provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=1990, reference="webhook-teste-1", payer=PixPayer(email="comprador@example.com")
            )
        )
        app = build_app(provider)
        order_id = charge.provider_charge_id

        print("pendente  ", await notify(app, order_id, signed_headers(order_id)))
        await provider.cancel_pix_charge(order_id)
        print("cancelada ", await notify(app, order_id, signed_headers(order_id)))
        print("forjada   ", await notify(app, order_id, {"x-signature": "ts=1,v1=00", "x-request-id": "x"}))
        print("simulada  ", await notify(app, "ORD00000000000000000000000000", signed_headers("ORD00000000000000000000000000")))


asyncio.run(main())
```

Rode com as variáveis da receita de credenciais. `MERCADO_PAGO_WEBHOOK_SECRET`
é opcional aqui: o script assina e verifica com o mesmo valor.

```bash
set -a; . ~/.config/meu-servico/mercadopago-sandbox.env; set +a
python simular_webhook.py
```

Saída medida em 2026-10-09:

```text
pendente   (200, {'type': 'charge_created', 'status': 'pending'})
cancelada  (200, {'type': 'charge_cancelled', 'status': 'cancelled'})
forjada    (401, {'detail': 'Invalid Mercado Pago webhook signature'})
simulada   (200, {'type': 'unknown', 'status': None})
```

Cada linha é um caso que a rota precisa acertar:

| Linha | O que aconteceu | Por que importa |
| --- | --- | --- |
| `pendente` | assinatura válida, order relida `action_required` → `charge_created` | o tipo do evento sai da **releitura**, não do corpo |
| `cancelada` | a mesma notificação depois de cancelar → `charge_cancelled` | o mesmo corpo, outro estado relido, outro evento |
| `forjada` | assinatura errada → `401`, sem nenhuma chamada ao Mercado Pago | quem não tem o segredo não chega na releitura |
| `simulada` | id de order que não existe → o Mercado Pago responde `404` na releitura, a rota responde `200` sem cobrança | é o que a simulação do painel manda; responder erro faria o Mercado Pago reenviar para sempre |

!!! tip "Leve isto para a sua suíte"
    Para a suíte, troque o sandbox pelo nível 1: `FakeMercadoPagoOrdersAPI`
    no lugar do `HTTPClient` real e `api.pay(...)` no lugar de esperar o
    pagamento — o mesmo caminho, sem rede nem credencial.

## Nível 3 — "Simular notificação" no painel

Para o Mercado Pago chamar a sua máquina, ela precisa de uma URL pública. Um
túnel resolve: `cloudflared tunnel --url http://127.0.0.1:8000` ou
`ngrok http 8000` imprimem uma URL `https://…` que aponta para o seu servidor
local.

Logado como **vendedora de teste**, na aplicação dela:

1. Abra a configuração de **Webhooks** da aplicação e cole a URL do túnel
   seguida da rota (`https://…/webhooks/mercado-pago`).
2. Marque o evento de **Order** (ou "Pedidos").
3. Copie a **assinatura secreta** que o painel gera e use como
   `MERCADO_PAGO_WEBHOOK_SECRET` no seu serviço.
4. Use **Simular notificação**.

O esperado é a sua rota responder `200` sem cobrança: a simulação traz um id
que não é de nenhuma order sua, e os níveis 1 e 2 mostraram o que acontece
nesse caso.

!!! warning "O painel não foi validado aqui"
    Os passos deste nível descrevem o painel como ele costuma ser; este
    repositório ainda não observou o que o **Simular notificação** manda. Uma
    entrega real foi observada em produção em 2026-11-13 (nível 4). Se a
    assinatura de uma entrega for recusada, abra uma issue com os headers (sem
    o segredo).

## Nível 4 — entrega real

Com o túnel e o webhook configurados, crie uma order (o script de
[contas e credenciais de teste](mercado-pago-sandbox.md#passo-6-conferir-que-deu-certo)
serve) e acompanhe o log do seu serviço. A cada mudança da order, o Mercado
Pago chama a rota com `data.id` igual ao id da order.

!!! check "Observado em 2026-11-13"
    Uma entrega real em produção chegou como
    `POST …?data.id=ORD01…&type=order`, com `"action": "order.canceled"` no
    corpo, e só verificou com o `data.id` em minúsculas no manifesto, que é o
    que o SDK faz desde a v0.358.0. Ainda não observadas: as ações
    `order.updated` / `order.processed` de uma order paga.

## Recapitulando

- A notificação só diz **qual** order mudou; a rota relê para saber **o
  quê**.
- `FakeMercadoPagoOrdersAPI` + o adapter de verdade testam pagamento, estorno,
  cancelamento e vencimento sem rede; `tempest mercado-pago fake-api` e
  `simulate-webhook` fazem o mesmo com o serviço rodando.
- `sign_manifest` produz uma notificação que a sua rota aceita: teste o
  caminho inteiro sem URL pública.
- Assinatura errada é `401` antes de qualquer chamada; id inexistente é
  `200` sem cobrança, para o Mercado Pago não reenviar para sempre.
- Para o painel e para a entrega real, um túnel (`cloudflared`, `ngrok`) dá a
  URL pública.
