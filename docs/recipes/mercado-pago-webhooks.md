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
| `simulada` | id que a API não conhece → `404` na releitura → `200` sem cobrança | é provavelmente o que o painel manda no nível 3 (ainda não observado) |

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

    Supostos, não copiados: o `409 cannot_cancel_order` (e a mensagem *"The
    order cannot be canceled in status …"*), o código e a mensagem do `404`
    de order inexistente (`order_not_found`, *"Order … not found"* — só o
    status `404` foi medido) e o `400 invalid_refund_amount` do estorno acima
    do saldo, com a mensagem dele. Se o seu código lê `code` ou `message`
    dessas respostas, confira no sandbox.

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

### A rota pronta: `make_mercado_pago_router`

A rota de webhook acima é a mesma em todo serviço: verificar, reler, entregar
ao seu código, responder `200`. `make_mercado_pago_router` monta essa rota a
partir da dependência `make_mercado_pago_webhook_delivery_dependency` — a
verificação e a releitura são as mesmas — e você escreve só o handler:

`app.include_router(make_mercado_pago_router(provider, on_delivery=handle, settings=settings, simulate=settings.ENV == "development"))` — o serviço completo está no exemplo de *Front em desenvolvimento*, logo abaixo.

- `on_delivery(delivery: MercadoPagoOrderDelivery) -> Awaitable[None]` recebe
  a entrega já verificada, com a order relida. Retornou: a rota responde
  `200` com `{"ok": true, "data_id": ..., "order_status": ...}`. Levantou: a
  resposta é `500` — e, pela documentação do provedor, o Mercado Pago
  entrega de novo (reenvio esperado, não observado aqui).
- O segredo vem de `secret=`; sem ele, de `settings.MERCADOPAGO_WEBHOOK_SECRET`
  (ou do ambiente e do `.env`, quando `settings` também falta). Segredo vazio
  é recusado na construção (`ValueError`): ele recusaria toda entrega.
- `webhook_path` tem default `/webhooks/mercado-pago` — é a URL que você
  cadastra no painel.
- Quem já escreve a rota à mão com a dependência continua igual: nada mudou
  nela.

#### Front em desenvolvimento: `simulate=True`

Com `simulate=True`, o router monta também as rotas que o front usa para
fazer o que só o Mercado Pago faz — pagar, vencer, recusar, cancelar,
capturar, estornar:

| Rota | O que faz |
| --- | --- |
| `GET /dev/mercado-pago/orders` | todas as orders do simulador |
| `GET /dev/mercado-pago/orders/{order_id}` | uma order; `404` se não existe |
| `POST /dev/mercado-pago/orders/{order_id}/{transition}` | `pay`, `expire`, `fail`, `cancel`, `capture`, `refund` (`?amount_cents=` para estorno parcial) |

Cada transição muda a order na `FakeMercadoPagoOrdersAPI` **em memória sobre
a qual o seu provider roda**, assina a notificação com o segredo da rota e a
entrega à **rota de webhook do próprio serviço**, pelo app inteiro
(`httpx.ASGITransport(request.app)`): middleware, verificação da assinatura,
releitura e `on_delivery` rodam de verdade. A resposta traz a order depois da
transição e o que a rota de webhook respondeu. Transição que o estado não
permite (estornar antes de pagar) é `409`; order desconhecida, `404`;
transição desconhecida, `422`. No Swagger, essas rotas aparecem sob a tag
`mercado-pago (dev)`.

O provider se liga ao simulador por `mercado_pago_provider_for`: com
`simulate=False` ele devolve o `MercadoPagoPixProvider` sobre
`settings.mercado_pago_kwargs()`; com `simulate=True`, o **mesmo** adapter
sobre uma `FakeMercadoPagoOrdersAPI` em memória, e o router acha o simulador
sozinho (`simulation_api_for(provider)`). Para cartão,
`provider_class=MercadoPagoCardProvider`; para outro simulador,
`simulation_api=`.

```python
from typing import Any

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    make_mercado_pago_router,
    mercado_pago_provider_for,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings


class Settings(EnvironmentSettings, MercadoPagoSettings):
    """ENV plus the Mercado Pago credentials."""


settings = Settings()
simulate: bool = settings.ENV == "development"
provider = mercado_pago_provider_for(settings, simulate=simulate)


async def handle(delivery: MercadoPagoOrderDelivery) -> None:
    """Release or revert the order the delivery is about."""
    event = provider.parse_webhook(delivery)
    status = event.charge.status.value if event.charge else None
    print(f"on_delivery: {event.type.value} {status}", flush=True)


app = FastAPI()
app.include_router(
    make_mercado_pago_router(
        provider,
        on_delivery=handle,
        settings=settings,
        simulate=simulate,
    )
)


@app.post("/charges")
async def create_charge(reference: str) -> dict[str, Any]:
    """Create a R$ 19,90 Pix charge — the service's normal flow."""
    charge = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference=reference,
            payer=PixPayer(email="comprador@example.com"),
        )
    )
    return {"order_id": charge.provider_charge_id, "status": charge.status.value}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
```

```bash
ENV=development MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste python servico.py
```

O front cria o Pix pelo fluxo normal e chama "pagar". Saída medida em
2026-11-14 (o `order` da resposta cortado em `id`, `status` e
`status_detail`; a resposta inteira traz a order completa):

```text
$ curl -s -X POST "http://127.0.0.1:8001/charges?reference=pedido-42"
{"order_id":"ORDFAKE4CE323A800000000000002","status":"pending"}

$ curl -s -X POST http://127.0.0.1:8001/dev/mercado-pago/orders/ORDFAKE4CE323A800000000000002/pay
{"order": {"id": "ORDFAKE4CE323A800000000000002", "status": "processed", "status_detail": "accredited"}, "delivery": {"action": "order.processed", "status_code": 200, "body": {"ok": true, "data_id": "ORDFAKE4CE323A800000000000002", "order_status": "processed"}}}

$ curl -s -X POST "http://127.0.0.1:8001/dev/mercado-pago/orders/ORDFAKE4CE323A800000000000002/refund?amount_cents=500"
{"order": {"id": "ORDFAKE4CE323A800000000000002", "status": "processed", "status_detail": "partially_refunded"}, "delivery": {"action": "order.processed", "status_code": 200, "body": {"ok": true, "data_id": "ORDFAKE4CE323A800000000000002", "order_status": "processed"}}}

$ curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8001/dev/mercado-pago/orders/ORDFAKE4CE323A800000000000002/cancel
{"detail":"409 cannot_cancel_order: The order cannot be canceled in status 'processed'."}
HTTP 409
```

E no log do serviço, o `on_delivery` chamado pela rota de webhook:

```text
on_delivery: charge_paid paid
on_delivery: charge_paid paid
```

!!! danger "Simulação nunca sobe em produção"
    `simulate=True` — no router e no `mercado_pago_provider_for` — só
    constrói com `ENV=development` ou `ENV=test`. O `ENV` vem de
    `settings.ENV` (um `Settings` que compõe `EnvironmentSettings`) ou da
    variável de ambiente; **sem `ENV` conta como produção**. Fora disso, a
    construção levanta, e o serviço não sobe:

    ```text
    RuntimeError: mercado_pago_provider_for with simulate=True needs ENV=development or ENV=test (got 'production'); the simulation routes mark orders paid and must never reach production.
    ```

    Com `simulate=False` as rotas de simulação não são montadas, e o módulo
    do simulador nem é importado.

#### Saque manual

O payout do Mercado Pago não está liberado para a conta (#499). Enquanto
isso, o router faz o saque por um administrador: com `withdraw=`, ele monta
`POST /api/mercado-pago/withdraw`, que:

1. debita **todo** o saldo disponível do usuário pelo
   `WalletService.withdraw` — a rota não aceita valor, a carteira fica zerada;
2. grava a linha `PENDING` da tabela de saques manuais
   (`make_manual_payout_model`), com a chave **mascarada**;
3. manda ao administrador o e-mail com valor, chave Pix e tipo, `user_id`,
   `correlation_id` e o link da linha no painel.

O usuário e a chave vêm das mesmas dependências do `make_wallet_router`
(`current_user_id`, `pix_destination` — a chave vem do perfil, nunca do
corpo). O e-mail sai pelo `ManualPayoutProvider`, que aceita qualquer objeto
com o `send(to, subject, body)` do `EmailUtils`: em produção,
`EmailUtils(**settings.email_kwargs())`; em desenvolvimento, o
`FakeEmailUtils` guarda a mensagem em `outbox`.

```python
import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from fastapi import FastAPI, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    PixKeyType,
    UserModelAuthBackend,
    register_exception_handlers,
)
from tempest_fastapi_sdk.admin import AdminSite, make_admin_router, manual_payout_admin
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoWithdrawConfig,
    make_mercado_pago_router,
    mercado_pago_provider_for,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils
from tempest_fastapi_sdk.utils import LocalUploadStorage
from tempest_fastapi_sdk.wallet import (
    PixDestinationSchema,
    WalletBalanceMixin,
    WalletService,
    make_manual_payout_model,
    make_wallet_entry_model,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    """The service's user, with a wallet."""

    __tablename__ = "users"


WalletEntryModel = make_wallet_entry_model(user_table="users")
ManualPayoutModel = make_manual_payout_model(user_table="users")


class Settings(EnvironmentSettings, MercadoPagoSettings):
    """ENV plus the Mercado Pago credentials."""


settings = Settings()
db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
mailer = FakeEmailUtils()
provider = mercado_pago_provider_for(settings, simulate=settings.ENV == "development")


def wallet_service(session: AsyncSession) -> WalletService:
    """Build the request's wallet service."""
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


async def sessions() -> AsyncIterator[AsyncSession]:
    """Yield the request's session."""
    async with db.get_session_context() as session:
        yield session


def current_user_id(x_user_id: UUID = Header()) -> UUID:
    """Stand-in for the service's auth dependency."""
    return x_user_id


def pix_destination() -> PixDestinationSchema:
    """Stand-in for reading the key from the user's profile."""
    return PixDestinationSchema(
        pix_key="motorista@example.com", pix_key_type=PixKeyType.EMAIL
    )


async def handle(delivery: MercadoPagoOrderDelivery) -> None:
    """Credit the seller's wallet when an order is paid (omitted here)."""


app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_mercado_pago_router(
        provider,
        on_delivery=handle,
        settings=settings,
        simulate=settings.ENV == "development",
        withdraw=MercadoPagoWithdrawConfig(
            service_factory=wallet_service,
            session_factory=sessions,
            current_user_id=current_user_id,
            pix_destination=pix_destination,
            mailer=mailer,
            admin_emails=["financeiro@example.com"],
            record_model=ManualPayoutModel,
            record_url="http://127.0.0.1:8000/admin/m/manual_payouts/{id}",
        ),
    )
)
site = AdminSite(title="Admin")
site.register(
    manual_payout_admin(ManualPayoutModel, upload_storage=LocalUploadStorage("receipts"))
)
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key="troque-por-um-segredo-de-32-bytes-ou-mais",
    )
)


async def main() -> None:
    """Fund a wallet, withdraw twice, and show what the admin received."""
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="motorista@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        await wallet_service(session).credit(user.id, 12_345, kind="SALE")
        user_id = str(user.id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        headers = {"X-User-Id": user_id}
        first = await client.post("/api/mercado-pago/withdraw", headers=headers)
        print("saque     ", first.status_code, first.json()["payout"])
        second = await client.post("/api/mercado-pago/withdraw", headers=headers)
        print("de novo   ", second.status_code, second.json()["code"])
    async with db.get_session_context() as session:
        row = (await session.execute(select(ManualPayoutModel))).scalar_one()
        print("registro  ", row.status, row.amount_cents, row.pix_key_masked)
    print(mailer.outbox[0].subject)
    print(mailer.outbox[0].body)


asyncio.run(main())
```

```bash
ENV=development MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste python saque_manual.py
```

Saída medida em 2026-11-14:

```text
saque      200 {'correlation_id': 'fee3bada-4405-4265-91d5-ad5d28127a94', 'status': 'pending', 'provider': 'manual', 'provider_status': 'manual_review'}
de novo    409 WALLET_INSUFFICIENT_BALANCE
registro   pending 12345 mo***om
[Saque manual] R$ 123,45 para chave Pix
Um usuário pediu um saque e o valor já foi debitado da carteira.
Faça o Pix manualmente a partir da conta da plataforma:

Valor: R$ 123,45
Chave Pix: motorista@example.com
Tipo da chave: email
Usuário (user_id): 8016e5b7-2036-452a-b503-cfa5cd08ff7f
Identificador (correlation_id): fee3bada-4405-4265-91d5-ad5d28127a94
Comentário: Saque Pix

Use o identificador acima como descrição do Pix, para conciliar.
Se não for possível pagar, estorne o débito na carteira do usuário com o mesmo identificador.

Depois de pagar, anexe o comprovante e marque como pago: http://127.0.0.1:8000/admin/m/manual_payouts/4bcf96aa-c394-45fb-9e88-42e8cbbf3ba7
```

O que dá errado, e o que a rota responde:

| Caso | Resposta | A carteira |
| --- | --- | --- |
| nada disponível (inclusive o segundo saque) | `409 WALLET_INSUFFICIENT_BALANCE` | intocada |
| e-mail ao administrador falhou (`EmailDeliveryException`, `IntegrationDisabledException`) | `502 WALLET_PAYOUT_REJECTED`; a linha vira `CANCELED` | débito devolvido |
| a linha não pôde ser gravada | `502 WALLET_PAYOUT_REJECTED`; nenhum e-mail | débito devolvido |
| qualquer outro erro no envio | `502 WALLET_PAYOUT_UNCERTAIN`; a linha fica `PENDING` | débito mantido, log `CRITICAL` |
| usuário sem chave Pix | o que o seu `pix_destination` levantar | intocada |

!!! note "A linha é gravada depois do débito, antes do e-mail"
    O `WalletService.withdraw` comita o débito antes de chamar o provedor —
    é o que impede dois saques simultâneos de gastarem o mesmo saldo. O
    `ManualPayoutProvider` grava a linha logo em seguida e só então manda o
    e-mail; se a gravação falha, recusa e o débito volta. A única janela sem
    linha é o processo morrer entre o débito e a gravação: aí a linha
    `WITHDRAW` do ledger, com `reference_id` igual ao `correlation_id`, é o
    que concilia.

##### No painel: anexar o comprovante e marcar como pago

`manual_payout_admin(ManualPayoutModel, upload_storage=...)` registra a
tabela no painel do jeito que o trabalho pede:

- lista filtrável por `status` e buscável por `correlation_id`;
- valor, usuário, chave, tipo, `correlation_id`, status e quem concluiu são
  só leitura; não há criar, apagar nem importar;
- `receipt_key` é um upload que aceita **só PDF**: o tipo declarado tem de ser
  `application/pdf` **e** os bytes têm de começar com `%PDF-` — um PNG
  renomeado para `.pdf` é recusado com erro no campo, e nada é gravado;
- a página da linha tem o link **Download** do comprovante;
- a ação **Marcar como pago** conclui só linhas `PENDING` com comprovante,
  gravando `completed_at` e `completed_by` (o administrador logado); linha
  sem comprovante fica `PENDING` e a mensagem lista os `correlation_id`
  dela;
- linha `COMPLETED` não volta, e o comprovante dela não pode mais ser
  trocado (o envio é recusado com erro no campo).

!!! info "Cancelar um saque pelo painel não existe"
    Cancelar um `PENDING` teria de devolver o débito pela carteira, e a ação
    do painel não faz isso. `CANCELED` só aparece quando o e-mail falhou e o
    próprio saque já devolveu o débito.

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
| `simulada` | id de order que não existe → o Mercado Pago responde `404` na releitura, a rota responde `200` sem cobrança | um id que a API não conhece (provavelmente o que a simulação do painel manda; ainda não observado); responder erro faria o Mercado Pago reenviar, segundo a documentação do provedor (não observado aqui) |

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

O esperado é a sua rota responder `200` sem cobrança: a simulação
provavelmente traz um id que a API não conhece (ainda não observado), e os
níveis 1 e 2 mostraram o que acontece nesse caso.

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
- `make_mercado_pago_router` monta a rota de webhook; com `simulate=True`
  (só em `development`/`test`), o front paga e estorna pelas rotas
  `/dev/mercado-pago/...`, que entregam o webhook à rota de verdade; com
  `withdraw=`, o saque zera a carteira e vira um Pix manual que o
  administrador conclui no painel, com o comprovante em PDF.
- `sign_manifest` produz uma notificação que a sua rota aceita: teste o
  caminho inteiro sem URL pública.
- Assinatura errada é `401` antes de qualquer chamada; id inexistente é
  `200` sem cobrança, porque responder erro faria o Mercado Pago reenviar —
  é o que a documentação do provedor descreve; o reenvio não foi observado
  aqui.
- Para o painel e para a entrega real, um túnel (`cloudflared`, `ngrok`) dá a
  URL pública.
