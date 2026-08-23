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
from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


async def criar_preferencia(client: MercadoPagoClient) -> str | None:
    """Create a Checkout Pro preference and return where to send the buyer.

    Args:
        client (MercadoPagoClient): The configured client.

    Returns:
        str | None: The ``init_point`` URL, when the provider returned one.
    """
    preference = await client.create_preference(
        body={
            "items": [
                {
                    "title": "Pedido 1042",
                    "quantity": 1,
                    "unit_price": 19.9,
                }
            ],
            "external_reference": "pedido-1042",
        }
    )
    return preference.init_point
```

## Checkout Transparente: cobrando sem redirect

Pix e boleto são **inteiramente server-side** — nenhum redirecionamento:

```python
import uuid

from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


async def cobrar_pix(client: MercadoPagoClient) -> str | None:
    """Charge over Pix without sending the buyer anywhere.

    Args:
        client (MercadoPagoClient): The configured client.

    Returns:
        str | None: The payment URL for the offline method, when present.
    """
    payment = await client.create_payment(
        body={
            "transaction_amount": 19.9,
            "payment_method_id": "pix",
            "payer": {"email": "comprador@example.com"},
            "external_reference": "pedido-1042",
        },
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
    propósito: ler um QR não paga os 0,76 s que construir os 324 modelos
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
    )
```

!!! danger "Esta parte ainda não foi medida contra o provedor"
    A especificação vendorizada **não descreve** a assinatura de webhook: não
    há seção `webhooks` nem security scheme de notificação, e
    `grep -c "x-signature" vendor/mercadopago-openapi.yaml` devolve `0`.

    O que está testado é o HMAC: assinar e verificar batem, e adulterar o
    `data_id`, o timestamp ou o segredo derruba a verificação. O que **não**
    está testado é se o manifesto que assinamos é byte a byte o que o Mercado
    Pago assina.

    Antes de isso guardar dinheiro em produção, passe **uma** notificação
    real por `verify_signature` e confirme que ela é aceita. Se for
    rejeitada, o manifesto é o que precisa mudar — passe o seu por
    `manifest_template=`, e abra uma issue para o default ser corrigido.

## Recapitulando

- Um único host: o que separa teste de produção é o token.
- Dinheiro em reais; converta na fronteira com `to_cents` / `from_cents`.
- Pix e boleto são server-side; cartão exige tokenização no cliente.
- `x_idempotency_key` é argumento por chamada, nunca header default.
- O `Payment` gerado descarta o QR do Pix em silêncio; use
  `create_pix_payment` / `parse_pix_payment`, ou a API de Orders.
- A verificação de webhook está implementada e testada como HMAC, mas o
  manifesto ainda espera uma notificação real para ser confirmado.
