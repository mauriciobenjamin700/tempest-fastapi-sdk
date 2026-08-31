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

## Checkout Transparente: cobrando sem redirect

Pix e boleto são **inteiramente server-side** — nenhum redirecionamento:

```python
import uuid

from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    MercadoPagoClient,
    PaymentPayer,
    PaymentRequest,
)


async def cobrar_pix(client: MercadoPagoClient) -> str | None:
    """Charge over Pix without sending the buyer anywhere.

    Args:
        client (MercadoPagoClient): The configured client.

    Returns:
        str | None: The payment URL for the offline method, when present.
    """
    payment = await client.create_payment(
        body=PaymentRequest(
            transaction_amount=19.9,
            payment_method_id="pix",
            payer=PaymentPayer(email="comprador@example.com"),
            external_reference="pedido-1042",
        ),
        x_idempotency_key=uuid.uuid4(),
    )
    details = payment.transaction_details
    return details.external_resource_url if details is not None else None
```

!!! tip "`x_idempotency_key` é argumento da chamada"
    Uma chave por tentativa. Se a rede cair depois de o Mercado Pago receber
    a requisição, repetir **com a mesma chave** devolve o pagamento original
    em vez de criar um segundo.

    Ela é argumento — e não header default do `HTTPClient` — justamente por
    isso: um header default mandaria a mesma chave em toda cobrança, e a
    segunda venda seria deduplicada em cima da primeira.

!!! warning "Cartão tem uma parte obrigatória no cliente"
    `create_payment` recebe o cartão como `token`, nunca como número. Quem
    emite esse token é `POST /v1/card_tokens`, que a especificação declara
    com `security: publicKey` — chave **pública**, feita para rodar no
    browser ou no app.

    Chamar essa rota do servidor é tecnicamente possível e coloca o seu
    serviço no escopo do PCI DSS. Use o SDK JavaScript ou mobile do Mercado
    Pago para obter o token, e mande só o token para o seu backend.

## O QR do Pix, e o motivo de ele desaparecer

O `create_payment` gerado devolve o `Payment` que a especificação declara — e
a especificação **não** declara `point_of_interaction`, que é exatamente onde
o copia-e-cola e a imagem do QR chegam. Como o `BaseSchema` do SDK é
`extra="ignore"`, o objeto é descartado na validação: o QR chega no corpo
HTTP e some no modelo, sem erro e sem log.

Por isso existe `create_pix_payment`, que faz a **mesma** requisição e
devolve um modelo que tem onde guardar o QR:

```python
import uuid

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    PixPayment,
    create_pix_payment,
)


async def cobrar_pix_com_qr(access_token: str) -> PixPayment:
    """Charge over Pix and keep the QR the generated model drops.

    Args:
        access_token (str): The Mercado Pago access token.

    Returns:
        PixPayment: The pending payment, carrying ``qr_code`` and
        ``qr_code_base64``.
    """
    http: HTTPClient = HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {access_token}"},
    )
    return await create_pix_payment(
        http,
        body={
            "transaction_amount": 19.9,
            "payment_method_id": "pix",
            "payer": {"email": "comprador@example.com"},
            "external_reference": "pedido-1042",
        },
        idempotency_key=uuid.uuid4(),
    )
```

O que o `PixPayment` devolvido carrega:

```text
payment.qr_code         "00020126580014br.gov.bcb.pix0136..."   o copia-e-cola
payment.qr_code_base64  "iVBORw0KGgoAAAANSUhEUg..."             PNG, para <img src="data:...">
payment.ticket_url      "https://www.mercadopago.com.br/..."    página que já desenha o QR
payment.status          "pending"                               até o pagador pagar
```

As três são propriedades **None-safe**: pagamento de cartão, ou Pix já pago,
devolve `None` em vez de estourar — é a forma como o provedor responde
depois da liquidação.

!!! tip "Já tem o corpo em mãos? Use `parse_pix_payment`"
    Um webhook manda você buscar o pagamento; se você já chamou pelo cliente
    gerado e guardou o JSON cru, `parse_pix_payment(payload)` monta o mesmo
    `PixPayment` sem repetir a requisição. Para reler pelo id existe
    `get_pix_payment(http, payment_id)`.

!!! info "De onde vêm esses nomes de campo"
    Não da especificação, que os omite: do SDK Node oficial do Mercado Pago
    (`mercadopago/sdk-nodejs`, `src/clients/payment/commonTypes.ts`, commit
    `c2d3c6ae`), onde `PointOfInteraction` e `TransactionData` estão
    modelados. O conjunto de campos é fixado por teste, então uma mudança lá
    aparece aqui como falha e não como valor que sumiu.

!!! note "`PixPayment` é uma vista, não um substituto"
    Para tudo que a especificação declara, use o `Payment` gerado. O
    `PixPayment` carrega só o que um fluxo Pix lê — id, status, valor,
    expiração — mais o objeto do QR. Ele não importa os schemas gerados, de
    propósito: ler um QR não paga os 0,76 s que construir os 323 modelos
    custa.

### A rota alternativa: Orders API

A especificação modela o QR num lugar só, `OrderTransactionPayment`, da API
de Orders — lá `qr_code`, `qr_code_base64`, `digitable_line` e `e2e_id` são
declarados de verdade:

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


async def qr_do_pedido(client: MercadoPagoClient, order_id: str) -> object:
    """Read the Pix QR data of an order.

    Args:
        client (MercadoPagoClient): The configured client.
        order_id (str): The order identifier.

    Returns:
        object: The order, whose transactions carry ``qr_code`` and
        ``qr_code_base64``.
    """
    return await client.get_order(order_id)
```

Use Orders quando a integração é nova — é a recomendação do próprio
provedor, e o caminho tipado direto pela especificação. Use
`create_pix_payment` quando a cobrança já roda em `/v1/payments` e trocar de
API não está em discussão.

## Verificando o webhook

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import verify_signature


def notificacao_e_autentica(
    secret: str, signature: str, data_id: str, request_id: str
) -> bool:
    """Check that a notification really came from Mercado Pago.

    Args:
        secret (str): The webhook secret from the dashboard.
        signature (str): The ``x-signature`` header.
        data_id (str): The ``data.id`` query parameter.
        request_id (str): The ``x-request-id`` header.

    Returns:
        bool: Whether the signature matches.
    """
    return verify_signature(
        secret=secret,
        signature_header=signature,
        data_id=data_id,
        request_id=request_id,
        tolerance_seconds=300.0,
    )
```

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

## Como saber se uma operação é confiável

O documento que este SDK usa vem do provedor: é, byte a byte, o `spec3.yaml`
de [`github.com/mercadopago/openapi`](https://github.com/mercadopago/openapi),
o repositório de especificação da própria empresa. `make mercadopago-fetch`
rebaixa.

Mas o documento **não é completo**: medido em 2026-08-30, ele omite sete
operações que o SDK oficial do próprio Mercado Pago chama, e três operações que
ele carrega responderam `404` quando sondadas. Rebaixar responde *"o documento
mudou?"*, não *"esta operação existe?"*.

Então nem toda operação do `MercadoPagoClient` tem o mesmo lastro. Das 147:

| Balde | Qtd | O que responde por ela |
| --- | --- | --- |
| O SDK oficial chama | 65 | O provedor, no próprio `mercadopago` do PyPI |
| Sondada viva | 35 | Requisição sem credencial respondeu `401`/`403`/`400` |
| Nada responde | 47 | Só o documento vendorizado |

**As 47 dizem isso na própria docstring:**

```
**Unverified.** Neither the provider's SDK nor an unauthenticated probe
covers this operation, so nothing here confirms the API routes it.
```

!!! warning "Não quer dizer que estão erradas"
    Quer dizer que ninguém verificou. São todas `POST`/`PUT`/`PATCH`/`DELETE`,
    e isso não é coincidência: a sonda que separa rota viva de rota morta é por
    **método e path**. Um `404` em `GET` não fala pelo `DELETE` no mesmo path —
    medido, `GET /v1/customers` responde `404` enquanto
    `POST /v1/customers` é onde o SDK oficial cria cliente.

    Mandar `POST`, `PUT` ou `DELETE` para uma API de pagamento em produção só
    para descobrir se rotea não é forma aceitável de responder a pergunta. Elas
    ficam marcadas em vez de adivinhadas.

Se você usa uma dessas e ela funciona, isso é evidência que o repositório não
tem — vale abrir issue com o que você observou.

Para ver os três baldes:

```bash
make mercadopago-diff
```


## Recapitulando

- Um único host: o que separa teste de produção é o token.
- Dinheiro em reais; converta na fronteira com `to_cents` / `from_cents`.
- Pix e boleto são server-side; cartão exige tokenização no cliente.
- `x_idempotency_key` é argumento por chamada, nunca header default.
- O `Payment` gerado descarta o QR do Pix em silêncio; use
  `create_pix_payment` / `parse_pix_payment`, ou a API de Orders.
- A verificação de webhook é portada do validador do provedor, com o
  manifesto omitindo par ausente e digests conferidos contra `openssl`;
  falta só uma entrega real para confirmar. Ligue `tolerance_seconds`.
- Notificação de QR Code não é assinada — não passe por `verify_signature`.
