# Mercado Pago: cobrando no gateway mais usado do Brasil

Pix, cartão, boleto e presencial, com a superfície inteira já gerada da
especificação oficial do provedor.

## Instalando e conectando

```python
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    MercadoPagoClient,
)

http: HTTPClient = HTTPClient(
    base_url=DEFAULT_BASE_URL,
    default_headers={"Authorization": "Bearer <seu access token>"},
)
client: MercadoPagoClient = MercadoPagoClient(http)
```

Ou pelo mixin de settings, que já resolve o prefixo:

```python
from tempest_fastapi_sdk import HTTPClient, MercadoPagoSettings
from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


def build_client(settings: MercadoPagoSettings) -> MercadoPagoClient:
    """Build the client from configuration.

    Args:
        settings (MercadoPagoSettings): The loaded settings.

    Returns:
        MercadoPagoClient: The configured client.
    """
    return MercadoPagoClient(HTTPClient(**settings.mercado_pago_kwargs()))
```

!!! danger "Não existe host de sandbox"
    Medido na especificação pinada: `servers` tem **uma** entrada,
    `https://api.mercadopago.com`. O que separa uma cobrança de teste de uma
    real é **qual token** você está segurando, não qual host você chama.

    É o oposto do OpenPix, onde o ambiente troca o domínio. Aqui um token de
    produção apontado para essa mesma URL move dinheiro de verdade, e não há
    configuração que o impeça.

## Dinheiro é em reais, não em centavos

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    from_cents,
    to_cents,
)


def exemplo() -> tuple[int, str]:
    """Convert both ways.

    Returns:
        tuple[int, str]: Cents parsed from reais, and reais rendered back.
    """
    cents: int = to_cents(19.9)
    return cents, str(from_cents(cents))
```

!!! warning "A armadilha de fator 100"
    Mercado Pago tipa dinheiro como `number` e o declara em **reais** — 39
    propriedades monetárias na especificação, entre elas
    `transaction_amount`, `unit_price` e `Refund.amount`.

    O OpenPix também usa `number`, mas declara em **centavos**. Mesmo tipo
    errado, unidade diferente. Trocar um pelo outro cobra R$ 1.990,00 por um
    item de R$ 19,90 — e o erro só aparece no extrato do cliente.

    Por isso `to_cents` **recusa** fração de centavo em vez de arredondar:
    arredondar esconderia a divergência atrás de um número plausível.

## Checkout Pro: a preferência

O comprador é redirecionado para uma tela do Mercado Pago:

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    MercadoPagoClient,
    PreferenceItem,
    PreferenceRequest,
)


async def criar_preferencia(client: MercadoPagoClient) -> str | None:
    """Create a Checkout Pro preference and return where to send the buyer.

    Args:
        client (MercadoPagoClient): The configured client.

    Returns:
        str | None: The ``init_point`` URL, when the provider returned one.
    """
    preference = await client.create_preference(
        body=PreferenceRequest(
            items=[
                PreferenceItem(
                    title="Pedido 1042",
                    quantity=1,
                    unit_price=19.9,
                )
            ],
            external_reference="pedido-1042",
        )
    )
    return preference.init_point
```

## Checkout Transparente: Pix e cartão pela API de Orders

Sem redirecionar o comprador, a cobrança passa pela **API de Orders**
(`/v1/orders`). A API de Payments (`/v1/payments`) aparece no painel do
Mercado Pago com o aviso *"Esta API será descontinuada em breve"*, e este SDK
deixou de modelá-la — o [guia de migração](../migration.md) diz o que trocar.

Os dois adapters falam Orders e entregam os contratos canônicos de
`integrations.payment`: `MercadoPagoPixProvider` (o `PixProvider`) e
`MercadoPagoCardProvider` (o `CardProvider`). O script abaixo roda contra o
sandbox com as credenciais da receita
[contas e credenciais de teste](mercado-pago-sandbox.md):

```python
import asyncio
import os
from datetime import timedelta

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    CardChargeRequest,
    PixChargeRequest,
    PixPayer,
)
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoCardProvider,
    MercadoPagoPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import DEFAULT_BASE_URL


async def main() -> None:
    """Open a Pix, then charge, decline and refund a test card."""
    payer: PixPayer = PixPayer(email=os.environ["MERCADO_PAGO_TEST_BUYER_EMAIL"])
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={
            "Authorization": f"Bearer {os.environ['MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN']}"
        },
    ) as http:
        pix = MercadoPagoPixProvider(http)
        charge = await pix.create_pix_charge(
            PixChargeRequest(
                amount_cents=1990,
                reference="pedido-1042",
                expires_in=timedelta(minutes=30),
                payer=payer,
            ),
        )
        print("pix", charge.status.value, bool(charge.br_code))
        print("pix", (await pix.cancel_pix_charge(charge.provider_charge_id)).status.value)

        card = MercadoPagoCardProvider(http)
        token = (
            await http.request(
                "POST",
                "/v1/card_tokens",
                json={
                    "card_number": "4235647728025682",
                    "expiration_month": 11,
                    "expiration_year": 2030,
                    "security_code": "123",
                    "cardholder": {
                        "name": "APRO",
                        "identification": {"type": "CPF", "number": "12345678909"},
                    },
                },
            )
        ).json()["id"]
        paid = await card.create_card_charge(
            CardChargeRequest(
                amount_cents=10000,
                reference="pedido-1043",
                card_token=token,
                payment_method_id="visa",
                payer=payer,
            ),
        )
        print("card", paid.status.value, paid.status_detail)
        refunded = await card.refund_card_charge(paid.provider_charge_id, amount_cents=3000)
        print("card", refunded.status.value, refunded.refunded_cents)


asyncio.run(main())
```

Saída medida em 2026-10-09:

```text
pix pending True
pix cancelled
card paid accredited
card paid 3000
```

!!! warning "O número do cartão não passa pelo seu servidor"
    O script tokeniza o Visa **de teste** no servidor só porque ele não é um
    cartão. Em produção, o front tokeniza com a **Public Key** (MercadoPago.js
    ou o Card Payment Brick) e manda ao backend o token, a bandeira e as
    parcelas. Receber o número no servidor coloca o serviço no escopo do PCI
    DSS.

O que os adapters decidem por você, cada item medido no sandbox:

- **Dinheiro em centavos no contrato, decimal em string no fio.** Orders
  escreve `"19.90"`; `from_cents` / `to_cents` convertem sem passar por
  `float`.
- **Recusa de cartão é HTTP 402, e volta como resultado.** O corpo traz o
  motivo em `errors` e a order em `data`. `create_card_charge` devolve um
  `CardCharge` com `status` `FAILED` e o motivo em `status_detail`
  (`rejected_by_issuer`), em vez de levantar.
- **Autorizar e capturar depois.** `capture=False` manda
  `capture_mode: manual`; a cobrança volta `AUTHORIZED` (`waiting_capture`) e
  espera `capture_card_charge` ou `cancel_card_charge`.
- **Reembolso parcial endereça o pagamento.** Uma order tem um id (`ORD…`) e
  o pagamento dentro dela outro (`PAY…`); `refund_card_charge` acha o
  segundo sozinho. Sem valor, reembolsa o que sobrou. `refunded_cents`
  soma os reembolsos processados.
- **Capturar e reembolsar releem a order.** As duas respostas trazem só id,
  estado e transações, sem `total_amount`; o adapter faz um `GET` em seguida
  para devolver a cobrança inteira.
- **"Ainda não" é repetido.** Logo depois de criar, cancelar uma
  autorização respondeu `409 processor_communication_error` em 3 de 10
  tentativas, e reembolsar uma aprovação respondeu
  `422 unprocessable_entity` em 7 de 10 — a captura assíncrona ainda
  terminava. O adapter repete essas respostas, e só essas, com a mesma chave,
  após 1, 2 e 4 s (`action_retry_delays=` muda ou desliga); todas as medidas
  passaram em até ~5 s.
- **Expiração do Pix em segundos.** `expires_in` vira `PT1800S`; sem ela, a
  order expira em 24 horas.
- **Pagador é obrigatório.** Order sem `payer` volta
  `400 '$.payer' - minimum 1 properties allowed`.
- **Uma chave de idempotência por chamada**, que o `HTTPClient` reaproveita
  nos próprios retries (testado com transporte simulado; o provedor honrar a
  chave é o contrato do header, não observado aqui). Para colapsar duas
  chamadas do mesmo pedido, passe `idempotency_key=lambda reference: reference`.
- **Parcelas dependem da conta.** Para a vendedora de teste, a consulta de
  parcelas do Visa em R$ 100,00 ofereceu só 1x, e pedir 3x voltou
  `400 invalid_transaction_amount`. Consulte as parcelas
  (`get_installments`) e mande uma das oferecidas.

!!! note "O cliente gerado, para ir além"
    `MercadoPagoClient` traz a API de Orders inteira (`create_order`,
    `get_order`, `capture_order`, `refund_order`, `cancel_order`, transações).
    Os estados de `Order` e de `OrderTransactionPayment` aceitam qualquer
    string: o sandbox devolveu `failed`, `refunded`, `waiting_transfer` e
    `rejected_by_issuer`, que o documento não lista, e com o enum fechado
    `create_order` levantava `ValidationError` ao criar um Pix. Lá a recusa
    de cartão é um `402` que `raise_for_status()` transforma em exceção — é
    o adapter que a lê como resultado.

## Verificando o webhook

```python
from typing import Any

from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    MercadoPagoEvent,
    MercadoPagoWebhookEvent,
    make_mercado_pago_webhook_dependency,
)

from src.core.settings import settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
verified = make_mercado_pago_webhook_dependency(
    settings.MERCADOPAGO_WEBHOOK_SECRET,
    tolerance_seconds=300.0,
)


@router.post("/mercado-pago", include_in_schema=False)
async def mercado_pago_webhook(
    event: MercadoPagoWebhookEvent = Depends(verified),
) -> dict[str, Any]:
    """Recebe uma notificação já verificada."""
    if event.event is MercadoPagoEvent.PAYMENT:
        return {"handled": True, "payment": event.data_id}
    return {"handled": False, "topic": event.topic}
```

A fábrica lê `data.id` da query string e `x-signature` / `x-request-id` dos
headers, roda `verify_signature` e entrega um `MercadoPagoWebhookEvent`. Você
não extrai nada do request à mão, e não decide o que fazer com um `False`:

- Assinatura ausente, inválida, fora da janela de `tolerance_seconds`, ou um
  `data.id` / `x-request-id` que a assinatura cobria e o request não trouxe →
  **401** (`{"detail": "Invalid Mercado Pago webhook signature", "code":
  "UNAUTHORIZED", "details": {}}`), **antes** do seu handler.
- Segredo vazio recusa tudo — "nenhum segredo configurado" não vira rota
  aberta.
- Tópico que este SDK não nomeia **não** derruba a rota: `event` vira
  `MercadoPagoEvent.UNKNOWN` e `topic` guarda a string. Corpo que não é JSON
  também não: `payload` fica vazio e `body` traz os bytes.

!!! warning "A assinatura não cobre o corpo"
    O manifesto assinado é `data.id`, `x-request-id` e `ts` — o corpo fica
    de fora. Por isso `event.data_id` vem da **query**, que é o valor que o
    provedor assinou, e não do `data.id` do JSON. `payload` e `topic`
    chegaram sem assinatura: releia o recurso pela API usando `data_id`
    antes de agir sobre ele.

O algoritmo é **portado do validador do próprio Mercado Pago**
(`mercadopago/sdk-nodejs`, `src/utils/webhook/index.ts`, commit `99857f33`),
que é o módulo para o qual a documentação deles aponta o integrador. A
especificação vendorizada não modela nada disso:
`grep -c "x-signature" vendor/mercadopago-openapi.yaml` devolve `2`, e as duas
ocorrências são prosa dentro de `description` — **nenhum** parâmetro ou header
declarado leva esse nome, e o algoritmo de validação não está lá.

O manifesto assinado **omite par ausente**. Não é template fixo:

```text
tudo presente     id:<data.id>;request-id:<x-request-id>;ts:<ts>;
sem data.id       request-id:<x-request-id>;ts:<ts>;
sem os dois       ts:<ts>;
```

!!! warning "Isto era um defeito até a v0.250.0"
    Até então este módulo renderizava um template fixo, então uma entrega sem
    `data.id` assinava `id:;request-id:...;ts:...;` — e nenhuma entrega desse
    tipo verificava. Se você tratava a rejeição como "notificação inválida",
    estava descartando notificação legítima.

`build_manifest` está exportado para você conferir o que seria assinado:

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import build_manifest


def manifesto_da_entrega(data_id: str, request_id: str, ts: str) -> str:
    """Show the exact string the signature covers.

    Args:
        data_id (str): The ``data.id`` query parameter, empty when absent.
        request_id (str): The ``x-request-id`` header, empty when absent.
        ts (str): The ``ts`` component of ``x-signature``.

    Returns:
        str: The manifest, with absent pairs left out.
    """
    return build_manifest(data_id=data_id, request_id=request_id, timestamp=ts)
```

!!! tip "Ligue a janela de tolerância"
    Sem `tolerance_seconds`, uma entrega capturada do fio verifica para
    sempre: a assinatura cobre um timestamp que ninguém confere. O upstream
    deixa a janela opcional e nós também, mas `300.0` é o que faz o `ts` do
    manifesto trabalhar. A unidade do `ts` é lida pela magnitude — os próprios
    artefatos do provedor discordam entre segundos e milissegundos, e a
    [issue #458 deles](https://github.com/mercadopago/sdk-nodejs/issues/458)
    foi exatamente essa confusão.

!!! info "Migração para `v2` não precisa de release"
    O header pode carregar mais de um hash (`ts=..,v1=..,v2=..`). O verificador
    usa a primeira versão que você aceitar, então
    `versions=("v2", "v1")` adota a nova antes de este pacote mudar. O default
    é `("v1",)` — falhar fechado é o comportamento certo para versão que o
    provedor ainda não mandou.

!!! danger "Ainda não foi medido contra uma entrega real"
    Portado da implementação do provedor não é o mesmo que verificado contra
    notificação que o provedor mandou. O que está medido: os manifestos, byte
    a byte, contra as regras que o upstream codifica; e os digests, contra
    vetores calculados com `openssl dgst -sha256 -hmac`, que é outra
    implementação de HMAC que não a do Python.

    O que continua sem medição: se as entregas reais seguem o SDK deles. Passe
    **uma** notificação real por `verify_signature` antes de isso guardar
    dinheiro, e abra uma issue se ela for rejeitada.

!!! warning "Notificação de QR Code não é assinada"
    O upstream diz isso explicitamente: essas entregas não carregam
    assinatura e vão falhar sempre. Não passe QR Code por aqui — proteja essa
    rota de outra forma.

## O webhook pelo contrato: relendo a order

A notificação do Mercado Pago assina só o `data.id` — o id da order — e não
diz se ela foi paga. Um `parse_webhook` que lesse só a notificação teria um
evento possível, `UNKNOWN`, e um serviço que libera pedido em `CHARGE_PAID`
nunca liberaria nada. Por isso a dependency verifica a assinatura **e** relê
a order antes de entregar ao seu handler:

```python
from typing import Any

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    PixEventType,
    confirm_pix_payment,
)
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoCardProvider,
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import DEFAULT_BASE_URL

http: HTTPClient = HTTPClient(
    base_url=DEFAULT_BASE_URL,
    default_headers={"Authorization": "Bearer <access token da vendedora>"},
)
pix: MercadoPagoPixProvider = MercadoPagoPixProvider(http)
delivery_dependency = make_mercado_pago_webhook_delivery_dependency(
    "<segredo do webhook>",
    pix,
    tolerance_seconds=300.0,
)

app: FastAPI = FastAPI()


@app.post("/webhooks/mercado-pago", include_in_schema=False)
async def webhook(
    delivery: MercadoPagoOrderDelivery = Depends(delivery_dependency),
) -> dict[str, Any]:
    """Libera o pedido quando a order relida está paga."""
    card = MercadoPagoCardProvider.charge_from_delivery(delivery)
    if card is not None:
        return {"card": card.status.value, "detail": card.status_detail}
    event = pix.parse_webhook(delivery)
    if event.type is not PixEventType.CHARGE_PAID or event.charge is None:
        return {"settled": None}
    confirmation = await confirm_pix_payment(
        pix,
        event.charge.provider_charge_id,
        reference=event.charge.reference,
        amount_cents=event.charge.amount_cents,
    )
    return {"settled": confirmation.paid}
```

!!! danger "No seu serviço, o id e o valor vêm do seu banco"
    O exemplo confirma contra os dados da própria order relida, para caber
    numa página. No serviço, `confirm_pix_payment` recebe o
    `provider_charge_id` e o valor que **você** guardou ao abrir a cobrança —
    é o que impede uma order de outro pedido de liberar este. O
    [protocolo de Pix](pix-protocol.md#passo-4-o-service-que-so-fala-contrato)
    monta o service completo.

O que a dependency faz, nesta ordem:

1. **Verifica a assinatura** (`x-signature` sobre `data.id`, `x-request-id` e
   `ts`). Assinatura ausente ou errada é `401` antes do handler, sem
   nenhuma requisição ao Mercado Pago.
2. **Decide se a notificação é de uma order**: tópico `order`, ou ação
   começando com `order.`. O documento do provedor lista `order.created` e
   `order.updated`; uma entrega real de Orders ainda não foi observada aqui.
3. **Confere o formato do id** (`[A-Za-z0-9]+`) antes de pôr no path.
4. **Relê a order** por esse id. `404` é resposta, não falha: a
   "simulação de notificação" do painel assina um id inventado, e levantar
   ali faria o Mercado Pago reenviar para sempre. Outros erros sobem, a rota
   responde 5xx e o Mercado Pago tenta de novo.

Depois, `parse_webhook` tira o tipo do evento do **estado relido**
(`processed` → `CHARGE_PAID`, `canceled` → `CHARGE_CANCELLED`, `refunded` →
`CHARGE_REFUNDED`, pendente → `CHARGE_CREATED`). Passar a notificação crua
levanta `TypeError` com a dica da dependency. Estados sem evento canônico
(`failed`, chargeback) viram `UNKNOWN`, e o estado fica em
`event.charge.status`.

Para testar tudo isso localmente, com notificações simuladas e assinadas,
veja [Mercado Pago: testando webhooks](mercado-pago-webhooks.md).

## Como saber se uma operação é confiável

O documento que este SDK usa vem do provedor: é, byte a byte, o `spec3.yaml`
de [`github.com/mercadopago/openapi`](https://github.com/mercadopago/openapi),
o repositório de especificação da própria empresa. `make mercadopago-fetch`
rebaixa.

Mas o documento **não é completo**: medido em 2026-08-30, ele omite sete
operações que o SDK oficial do próprio Mercado Pago chama, e três operações que
ele carrega responderam `404` quando sondadas. Rebaixar responde *"o documento
mudou?"*, não *"esta operação existe?"*.

O cliente também **não carrega o que o provedor está aposentando**: as 8
operações da API de Payments e as 7 de QR presencial que a própria spec marca
`deprecated: true`. O SDK oficial ainda chama 7 delas (as de Payments), e é a
única lacuna permitida na regra "o que o SDK chama, a gente modela".

Então nem toda operação do `MercadoPagoClient` tem o mesmo lastro. Das 132:

| Balde | Qtd | O que responde por ela |
| --- | --- | --- |
| O SDK oficial chama | 58 | O provedor, no próprio `mercadopago` do PyPI (65 chamadas na 3.5.0 e na 3.6.0, menos as 7 da API de Payments) |
| Sondada viva | 34 | `GET` sem credencial respondeu `401`/`403`/`400` (2026-08-28); 11 delas não se sustentam e 2 respondem como não roteadas, ver nota abaixo |
| Separada no sandbox | 27 | Requisição que não pode dar certo respondeu diferente de um path inventado no mesmo prefixo (2026-10-09) |
| Não roteada | 2 | O sandbox respondeu como responde a um path que não existe |
| Nada responde | 11 | Resposta igual à do path inventado: nenhuma sonda distingue |

!!! warning "As sondadas vivas foram reavaliadas"
    O balde "sondada viva" vem de uma regra que a sondagem de 2026-10-09
    mostrou fraca: em vários prefixos `401`/`403` sai antes do roteamento.
    Reavaliadas com `GET` contra um path inventado no mesmo prefixo, 11 das
    34 respondem igual ao path inventado (`/terminals/v1`, refunds de
    `/point/integration-api`, `/users/{id}/pos`, seis subpaths de
    `/post-purchase/v1/claims/{id}` e `GET /v1/account/release_report/{id}`)
    e duas respondem como não roteadas (`GET /v1/account/release_report` e
    `GET /v1/account/settlement_report`). Elas ainda não carregam marcador na
    docstring; a decisão está na issue #488.

**As 11 dizem isso na própria docstring:**

```
**Unverified.** Neither the provider's SDK nor an unauthenticated probe
covers this operation, so nothing here confirms the API routes it.
```

**E as 2 não roteadas também**, com a medição: `update_chargeback` e
`create_qr_integrator_config` carregam `**Not routed.**` e o que o sandbox
respondeu. Elas continuam no cliente, porque remover método público é outra
decisão, mas não espere que funcionem.

!!! warning "Status diferente de `404` não prova rota"
    Medido no sandbox em 2026-10-09: em vários prefixos um gate de política
    responde **antes** do roteamento. `POST /terminals/v1/<qualquer-coisa>`
    responde `401` e `POST /post-purchase/v1/claims/<id>/<qualquer-coisa>`
    responde `403`, para paths que não existem. Por isso o balde "separada no
    sandbox" só conta uma operação quando a resposta dela difere da de um path
    inventado sob o mesmo prefixo, e as 11 que não diferiram ficam marcadas.

    A sonda também é por **método e path**: `GET /v1/customers` responde `404`
    enquanto `POST /v1/customers` é onde o SDK oficial cria cliente.

Se você usa uma das 11 e ela funciona, isso é evidência que o repositório não
tem. Vale abrir issue com o que você observou.

### `get_authenticated_user` devolve um modelo

`GET /users/me` foi observado no sandbox, e o método responde
`AuthenticatedUser` em vez de `dict[str, Any]`. Todo campo declarado veio com
valor na resposta observada, e nenhum é obrigatório. O que não foi declarado
(reputação, `status`, campos que vieram `null`) fica em `model_extra`, sem
perda:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    AuthenticatedUser,
    MercadoPagoClient,
)


async def main() -> None:
    """Mostra a conta dona do token."""
    http: HTTPClient = HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": "Bearer <seu access token>"},
    )
    async with http:
        user: AuthenticatedUser = await MercadoPagoClient(http).get_authenticated_user()
    print(user.id, user.site_id, user.tags)


asyncio.run(main())
```

As outras seis operações que o SDK oficial chama e o documento omitia
(advanced payments e `search_chargebacks`) continuam `dict[str, Any]`. O token
de teste recebeu `403` nelas, então não houve resposta para observar.

Para ver os baldes:

```bash
make mercadopago-diff
```


## Recapitulando

- Um único host: o que separa teste de produção é o token.
- Dinheiro em reais; converta na fronteira com `to_cents` / `from_cents`.
- Pix e cartão passam pela API de Orders; a de Payments saiu do SDK porque o
  provedor a está descontinuando.
- `MercadoPagoPixProvider` e `MercadoPagoCardProvider` entregam os contratos
  canônicos: centavos, estados canônicos, recusa de cartão como resultado
  (`402`), autorizar e capturar, reembolso parcial.
- Cartão exige tokenização no cliente, com a Public Key.
- A verificação de webhook é portada do validador do provedor, com o
  manifesto omitindo par ausente e digests conferidos contra `openssl`;
  falta só uma entrega real para confirmar. Ligue `tolerance_seconds`.
- `make_mercado_pago_webhook_dependency` monta a rota: lê `data.id`,
  `x-signature` e `x-request-id`, recusa com 401 antes do handler e entrega o
  `data_id` assinado — o corpo não é assinado.
- Notificação de QR Code não é assinada — não passe por `verify_signature`.
- Nem toda operação tem o mesmo lastro: 11 dizem `**Unverified.**` e 2 dizem
  `**Not routed.**` na docstring. `get_authenticated_user` devolve
  `AuthenticatedUser`, observado no sandbox.
- O webhook entra por `make_mercado_pago_webhook_delivery_dependency`, que
  verifica a assinatura e relê a order antes de virar evento.
