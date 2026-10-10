# Mercado Pago: testando webhooks

Webhook é a parte mais difícil de testar de uma integração de pagamento: quem
chama é o Mercado Pago, não você, e ele precisa de uma URL pública para
chamar. Esta página mostra três níveis de teste, do que roda na sua máquina
sem nada exposto até a entrega real:

1. **Simulação assinada, local** — você assina uma notificação com o seu
   segredo e entrega na sua rota. A rota relê uma order **real** do
   sandbox. Não precisa de URL pública. Comece por aqui.
2. **"Simular notificação" do painel** — o Mercado Pago manda uma
   notificação de teste para a sua URL.
3. **Entrega real** — você cria uma order e o Mercado Pago avisa sozinho.

Antes, tenha em mãos as credenciais da receita
[contas e credenciais de teste](mercado-pago-sandbox.md) e entenda o que a
rota faz em [Mercado Pago »](mercado-pago.md#o-webhook-pelo-contrato-relendo-a-order):
ela verifica a assinatura e **relê a order**, porque a notificação não traz o
estado do pagamento.

## Nível 1 — simulação assinada, na sua máquina

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
    buyer: str = os.environ["MERCADO_PAGO_TEST_BUYER_EMAIL"]
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as http:
        provider = MercadoPagoPixProvider(http)
        charge = await provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=1990, reference="webhook-teste-1", payer=PixPayer(email=buyer)
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
    O mesmo padrão — `sign_manifest` + `httpx.ASGITransport` — vira teste
    automatizado. Troque o `HTTPClient` real por um com
    `httpx.MockTransport` e você testa a rota sem rede, como a suíte do SDK
    faz em `tests/integrations/payment/adapters/test_mercado_pago_adapter.py`.

## Nível 2 — "Simular notificação" no painel

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
que não é de nenhuma order sua, e o nível 1 mostrou o que acontece nesse caso.

!!! warning "Não validado aqui"
    Os passos deste nível descrevem o painel como ele costuma ser; este
    repositório ainda não observou uma notificação vinda do painel nem de uma
    entrega real. A tabela do nível 1 é medida; os níveis 2 e 3 são o que se
    espera, a confirmar na sua primeira entrega. Se a assinatura de uma
    entrega real for recusada, abra uma issue com os headers (sem o segredo).

## Nível 3 — entrega real

Com o túnel e o webhook configurados, crie uma order (o script de
[contas e credenciais de teste](mercado-pago-sandbox.md#passo-7-conferir-que-deu-certo)
serve) e acompanhe o log do seu serviço. A cada mudança da order, o Mercado
Pago deve chamar a rota com `data.id` igual ao id da order.

## Recapitulando

- A notificação só diz **qual** order mudou; a rota relê para saber **o
  quê**.
- `sign_manifest` produz uma notificação que a sua rota aceita: teste o
  caminho inteiro sem URL pública.
- Assinatura errada é `401` antes de qualquer chamada; id inexistente é
  `200` sem cobrança, para o Mercado Pago não reenviar para sempre.
- Para o painel e para a entrega real, um túnel (`cloudflared`, `ngrok`) dá a
  URL pública.
