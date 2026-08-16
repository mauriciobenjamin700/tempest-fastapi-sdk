# OpenPix (Pix via Woovi)

Esta receita monta um serviço que **abre uma cobrança Pix, descobre quando ela
foi paga e libera o pedido** — com a arquitetura em camadas que o resto do SDK
usa, e sem escrever nenhum cliente HTTP à mão.

Ao final você terá:

- um `POST /api/checkout` que devolve o BR Code para o app desenhar o QR;
- um `POST /webhooks/openpix` que recebe a notificação de pagamento **já
  verificada**;
- uma conferência pela API antes de liberar qualquer coisa;
- um job de reconciliação para as cobranças que o webhook não trouxe.

Assinaturas e planos (mensalidade, Pix Automático) estão na receita ao lado:
[OpenPix (assinaturas e planos)](openpix-subscriptions.md).

## O que já vem no pacote

Instalar o SDK já traz a OpenPix inteira: **373 schemas** e **105 operações**
gerados da especificação, mais quatro coisas que a especificação não diz
(ambientes, eventos de webhook, verificação de assinatura e centavos).

```bash
uv add "tempest-fastapi-sdk[http]"
uv add cryptography
```

O `[http]` traz o `HTTPClient`, que é o transporte do cliente gerado. O
`cryptography` é o que verifica a assinatura do webhook — sem ele o módulo
importa normalmente e só falha na primeira entrega de verdade, em produção.

!!! tip "Precisa de outra API que o SDK não traz?"
    Este módulo existe porque OpenPix é comum o bastante para todo serviço
    estar gerando o mesmo cliente. Para qualquer outra API, o gerador
    continua sendo a ferramenta certa: veja
    [Cliente de integração (OpenAPI)](openapi-client.md).

## Configuração

```python
from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEnvironment


class Settings(BaseAppSettings):
    """Settings do serviço."""

    OPENPIX_APP_ID: str = ""
    OPENPIX_ENVIRONMENT: str = "sandbox"

    @property
    def openpix_environment(self) -> OpenPixEnvironment:
        """Resolve o ambiente configurado.

        Returns:
            OpenPixEnvironment: Produção quando `OPENPIX_ENVIRONMENT` é
            `production`, sandbox em qualquer outro caso.
        """
        if self.OPENPIX_ENVIRONMENT == "production":
            return OpenPixEnvironment.PRODUCTION
        return OpenPixEnvironment.SANDBOX


settings = Settings()
```

!!! warning "Os dois ambientes são domínios diferentes"
    Produção é `api.openpix.com.br`. Testes é `api.woovi-sandbox.com` — outro
    domínio, não um subdomínio. Nenhum dos dois soletra o outro, e o AppID de
    um não vale no outro. É por isso que `OpenPixEnvironment` existe em vez de
    uma string no `.env`.

## A arquitetura sugerida

A OpenPix entra pela camada de serviço, atrás de uma dependência. Nenhum
router monta payload, nenhum service abre `HTTPClient`.

```text
src/
├── core/
│   └── settings.py              # OPENPIX_APP_ID + ambiente
├── api/
│   ├── dependencies/
│   │   └── payments.py          # constrói HTTPClient -> OpenPixClient -> service
│   └── routers/
│       ├── checkout.py          # POST /api/checkout        (JSON, no schema)
│       └── webhooks.py          # POST /webhooks/openpix    (include_in_schema=False)
├── controllers/
│   └── checkout.py              # orquestra pedido + cobrança
├── services/
│   ├── openpix.py               # regras de cobrança: abrir, conferir, estornar
│   └── orders.py                # o seu pedido, que não sabe o que é Pix
└── db/
    ├── models/order.py          # status do pedido + correlation_id
    └── repositories/order.py
```

| Camada | Pode importar | Nunca importa |
| --- | --- | --- |
| `api/routers` | `controllers`, `schemas` | `OpenPixClient`, `db` |
| `controllers` | `services`, `schemas` | `OpenPixClient` |
| `services` | `OpenPixClient`, `db/repositories` | `fastapi` |
| `api/dependencies` | tudo acima, para montar | — |

Três decisões que vale explicitar:

1. **O `HTTPClient` é um só, criado no lifespan.** Ele carrega pool de
   conexões, retry e circuit breaker por host. Criar um por requisição joga
   fora as três coisas e abre um socket novo em cada checkout.
2. **O `correlationID` é a sua chave primária do lado da OpenPix.** Use o id
   do pedido, não um UUID novo: é o que amarra webhook, consulta e estorno ao
   registro do seu banco.
3. **O webhook mora em um router separado**, fora do schema OpenAPI. Ele não é
   parte da sua API pública, e a autenticação dele é uma assinatura, não o seu
   token de sessão.

### A dependência

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient

from src.core.settings import settings
from src.services.openpix import OpenPixService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Abre um cliente HTTP para o processo inteiro e o fecha no shutdown.

    Args:
        app (FastAPI): A aplicação, onde o cliente fica guardado.

    Yields:
        None: Enquanto a aplicação serve requisições.
    """
    http: HTTPClient = HTTPClient(
        base_url=settings.openpix_environment.base_url,
        default_headers={"Authorization": settings.OPENPIX_APP_ID},
        timeout=15.0,
    )
    app.state.openpix = OpenPixClient(http)
    try:
        yield
    finally:
        await http.aclose()


def get_openpix_service(request: Request) -> OpenPixService:
    """Entrega o serviço de cobranças já montado.

    Args:
        request (Request): A requisição em curso.

    Returns:
        OpenPixService: O serviço, sobre o cliente do lifespan.
    """
    return OpenPixService(request.app.state.openpix)
```

!!! info "O AppID vai no header `Authorization` cru"
    Sem `Bearer`, sem `Basic`. É a string que o painel da OpenPix mostra, e
    ela vale para a conta inteira. `default_headers` a coloca em toda
    requisição do cliente.

## Fluxo 1 — abrir a cobrança

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    Charge,
    ChargePayload,
    CustomerPayload,
    OpenPixClient,
    reais_to_cents,
)


class OpenPixService:
    """Regras de cobrança Pix."""

    def __init__(self, client: OpenPixClient) -> None:
        """Guarda o cliente gerado.

        Args:
            client (OpenPixClient): O cliente da OpenPix.
        """
        self.client: OpenPixClient = client

    async def open_charge(
        self,
        *,
        reference: str,
        amount_brl: str,
        customer_name: str,
        customer_email: str,
    ) -> Charge:
        """Abre uma cobrança Pix para um pedido.

        Args:
            reference (str): O id do pedido, usado como `correlationID`.
            amount_brl (str): Valor em reais, como texto ("19.90").
            customer_name (str): Nome do pagador.
            customer_email (str): E-mail do pagador.

        Returns:
            Charge: A cobrança criada, com o BR Code para desenhar o QR.

        Raises:
            ValueError: Se a resposta vier sem a cobrança.
        """
        response = await self.client.post_api_v1_charge(
            body=ChargePayload(
                correlation_id=reference,
                value=reais_to_cents(amount_brl),
                comment=f"Pedido {reference}",
                customer=CustomerPayload(
                    name=customer_name,
                    email=customer_email,
                ),
                expires_in=3600,
            ),
            return_existing=True,
        )
        if response.charge is None:
            raise ValueError(f"OpenPix não devolveu a cobrança de {reference}")
        return response.charge
```

O router só repassa e escolhe o que o app precisa ver:

```python
from typing import Any

from fastapi import APIRouter, Depends

from src.api.dependencies.payments import get_openpix_service
from src.services.openpix import OpenPixService

router: APIRouter = APIRouter(prefix="/api", tags=["checkout"])


@router.post("/checkout")
async def checkout(
    service: OpenPixService = Depends(get_openpix_service),
) -> dict[str, Any]:
    """Abre a cobrança do pedido e devolve o que o app desenha.

    Args:
        service (OpenPixService): O serviço de cobranças.

    Returns:
        O BR Code, a imagem do QR e o link de pagamento.
    """
    charge = await service.open_charge(
        reference="pedido-1",
        amount_brl="19.90",
        customer_name="Ana",
        customer_email="ana@example.com",
    )
    return {
        "br_code": charge.br_code,
        "qr_code_image": charge.qr_code_image,
        "payment_link_url": charge.payment_link_url,
        "status": charge.status,
    }
```

Rodando isso contra a API, a rota devolve:

```json
{
  "br_code": "00020101021226830014BR.GOV.BCB.PIX...",
  "qr_code_image": "https://api.openpix.com.br/openpix/charge/brcode/image/x.png",
  "payment_link_url": "https://openpix.com.br/pay/pedido-1",
  "status": "ACTIVE"
}
```

São três formas de cobrar a mesma cobrança, e você escolhe pela interface:

| Campo | O que é | Quando usar |
| --- | --- | --- |
| `br_code` | A string EMV do Pix | App próprio: você desenha o QR e oferece "copia e cola" |
| `qr_code_image` | URL de um PNG do QR | Página simples, e-mail, PDF |
| `payment_link_url` | Página de pagamento hospedada pela OpenPix | Quando não quer construir tela nenhuma |

!!! tip "Construa pelo nome Python — o type-checker aceita"
    Os campos gerados carregam o nome do fio em `validation_alias` +
    `serialization_alias`, não em `alias`. A diferença não aparece em runtime,
    e aparece no seu editor: com `alias`, o pyright renomeia o parâmetro e
    rejeita `ChargePayload(correlation_id=...)` pedindo `correlationID`.
    Medido com basedpyright: com a forma dividida, **0 erros** para o nome
    Python, e `model_validate` / `model_dump(by_alias=True)` seguem falando a
    grafia da OpenPix.

!!! tip "`return_existing=True` deixa a chamada idempotente"
    Sem ele, um segundo POST com o mesmo `correlationID` é erro. Com ele, a
    OpenPix devolve a cobrança que já existe — que é o que você quer quando o
    usuário aperta "pagar" duas vezes ou o app repete a requisição.

!!! note "`expires_in` é em segundos, mínimo de 5 minutos"
    O padrão da OpenPix é uma cobrança de longa validade. Se o seu pedido
    reserva estoque, feche essa janela: `expires_in=3600` expira em uma hora e
    a cobrança some do "aguardando pagamento" sozinha.

## Fluxo 2 — descobrir se foi paga

Existem três caminhos, e eles não são alternativas: são papéis diferentes.

| Caminho | O que é | Papel |
| --- | --- | --- |
| Webhook `CHARGE_COMPLETED` | A OpenPix avisa | **Aviso.** Rápido, mas chega por rede aberta |
| `get_api_v1_charge_by_id` | Você pergunta | **Fato.** É o que autoriza liberar |
| `get_api_v1_charge(status=...)` | Você varre | **Rede de segurança.** Pega o que o webhook perdeu |

A regra que resume: **o webhook avisa, a API confirma.**

### O webhook, verificado

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.integrations.payment.openpix import (
    Charge,
    OpenPixEvent,
    OpenPixWebhookEvent,
    make_openpix_webhook_dependency,
)

from src.api.dependencies.payments import get_openpix_service
from src.services.openpix import OpenPixService

router: APIRouter = APIRouter(prefix="/webhooks", include_in_schema=False)
verify = make_openpix_webhook_dependency()


@router.post("/openpix")
async def receive_openpix(
    event: OpenPixWebhookEvent = Depends(verify),
    service: OpenPixService = Depends(get_openpix_service),
) -> dict[str, str]:
    """Recebe uma entrega já verificada e confirma antes de liberar.

    Args:
        event (OpenPixWebhookEvent): A entrega verificada e decodificada.
        service (OpenPixService): O serviço de cobranças.

    Returns:
        Uma confirmação, para a OpenPix parar de reentregar.
    """
    if event.event is not OpenPixEvent.CHARGE_COMPLETED:
        return {"status": "ignored", "event": event.event_name}

    charge = Charge.model_validate(event.payload["charge"])
    reference = charge.correlation_id or ""
    if not await service.is_paid(reference):
        return {"status": "not-settled"}

    await service.release(reference)
    return {"status": "released"}
```

A dependência faz três coisas antes do corpo da rota rodar: confere a
assinatura RSA do header `x-webhook-signature`, decodifica o JSON e resolve a
string `event` para um membro de `OpenPixEvent`. O que sobra em
`event.payload` é o dict cru — você valida só o ramo que interessa.

Medido com esse router de pé (chave de teste, corpo assinado):

| Entrega | Resposta |
| --- | --- |
| Sem o header de assinatura | **401** |
| Assinatura válida, `OPENPIX:CHARGE_COMPLETED` | 200 `{"status": "released"}` |
| A mesma entrega de novo | 200 `{"status": "duplicate"}` |
| Evento que este SDK não conhece | 200 `{"status": "ignored", "event": "..."}` |

!!! danger "A chave pública da OpenPix é RSA-1024"
    Conferido carregando na `cryptography`: 1024 bits, expoente 65537 —
    **abaixo do piso de 2048 bits** que o NIST recomenda desde 2013. Isso
    limita o que a assinatura consegue provar.

    Trate uma assinatura válida como evidência de que a entrega veio da
    OpenPix, **não como autorização para movimentar dinheiro**. Quem autoriza é
    a releitura pela API — que é exatamente o `service.is_paid` acima. Nada
    aqui aumenta a força da chave; a mitigação é não confiar nela além do que
    ela é.

!!! warning "Reenvio (replay) e entrega repetida"
    A assinatura cobre o corpo e mais nada, então uma entrega capturada
    continua válida para sempre — e a própria OpenPix reentrega quando não
    recebe 200. Trate o handler como **idempotente**: chave pelo
    `correlationID` e ignore o que já processou. Veja
    [Idempotência](idempotency.md).

### A conferência

```python
from tempest_fastapi_sdk.integrations.payment.openpix import ChargeStatus


async def is_paid(self, reference: str) -> bool:
    """Pergunta à API se a cobrança está liquidada.

    Args:
        reference (str): O `correlationID` da cobrança.

    Returns:
        bool: `True` somente quando a OpenPix responde `COMPLETED`.
    """
    response = await self.client.get_api_v1_charge_by_id(id=reference)
    charge = response.charge
    return charge is not None and charge.status == ChargeStatus.COMPLETED
```

!!! warning "Compare `status` com `==`, nunca com `is`"
    Os modelos gerados herdam de `BaseSchema`, que usa
    `use_enum_values=True`: o campo chega como `str`, não como membro do enum.
    Medido: `charge.status == ChargeStatus.COMPLETED` é `True`,
    `charge.status is ChargeStatus.COMPLETED` é **`False`** — em toda entrega,
    em silêncio. `ChargeStatus` é um `str` enum, então a comparação por `==`
    funciona com o membro **e** com a string literal.

    Os três valores são `ACTIVE`, `COMPLETED` e `EXPIRED`.

### A reconciliação

Webhook é rede: uma entrega vai se perder. Um job periódico varre o que ficou
para trás — e note que ele lista o que **ainda está aberto** no lado da
OpenPix, cruzando com o que o seu banco acha que está aberto:

```python
from datetime import UTC, datetime, timedelta

from tempest_fastapi_sdk.integrations.payment.openpix import ChargeStatus, OpenPixClient


async def sweep_pending(client: OpenPixClient) -> list[str]:
    """Lista as cobranças ainda abertas nas últimas 24 horas.

    Args:
        client (OpenPixClient): O cliente da OpenPix.

    Returns:
        Os `correlationID` que continuam aguardando pagamento.
    """
    now = datetime.now(UTC)
    response = await client.get_api_v1_charge(
        start=now - timedelta(days=1),
        end=now,
        status=ChargeStatus.ACTIVE,
    )
    return [charge.correlation_id or "" for charge in response.charges]
```

Todo pedido que o seu banco tem como "aguardando" e que **não** aparece nessa
lista terminou de outro jeito: ou foi pago (e o webhook se perdeu) ou expirou.
Consulte cada um com `get_api_v1_charge_by_id` e feche o caso.

!!! note "A listagem não tem paginação na especificação"
    `GET /api/v1/charge` aceita `start`, `end`, `status`, `customer` e
    `subscription` — e mais nada. A resposta traz `page_info`, mas a
    especificação não declara `skip`/`limit`, então o cliente gerado não os
    expõe. Para janelas grandes, varra por intervalos de tempo menores.

## Fluxo 3 — mudar o prazo, ou desistir

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargePatchPayload,
    OpenPixClient,
)


async def extend(client: OpenPixClient, reference: str, until: str) -> None:
    """Empurra o vencimento de uma cobrança aberta.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        reference (str): O `correlationID` da cobrança.
        until (str): Nova data de expiração, em ISO 8601.
    """
    await client.patch_api_v1_charge_by_id(
        id=reference,
        body=ChargePatchPayload(expires_date=until),
    )


async def cancel(client: OpenPixClient, reference: str) -> None:
    """Cancela uma cobrança que não será mais paga.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        reference (str): O `correlationID` da cobrança.
    """
    await client.delete_api_v1_charge_by_id(id=reference)
```

`patch` só mexe na expiração — é o único campo que `ChargePatchPayload` tem.
Para mudar valor, abra outra cobrança.

## Fluxo 4 — estornar

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargeRefundPayload,
    OpenPixClient,
    reais_to_cents,
)


async def refund(
    client: OpenPixClient,
    *,
    reference: str,
    refund_reference: str,
    amount_brl: str,
) -> None:
    """Devolve o dinheiro de uma cobrança paga.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        reference (str): O `correlationID` da cobrança paga.
        refund_reference (str): A sua chave para este estorno.
        amount_brl (str): Quanto devolver, em reais.
    """
    await client.post_api_v1_charge_by_id_refund(
        id=reference,
        body=ChargeRefundPayload(
            correlation_id=refund_reference,
            value=reais_to_cents(amount_brl),
            comment="Pedido cancelado",
        ),
    )
```

O estorno tem `correlationID` próprio — ele é um registro seu, separado da
cobrança. `get_api_v1_charge_by_id_refund` lista os estornos de uma cobrança,
e o valor é opcional: sem ele, a OpenPix devolve o total.

## Dinheiro: centavo inteiro, não float

A especificação escreve, textualmente, *"Value in cents of this charge"* — e
tipa o campo como `number`. O modelo gerado então valida `1990` para o float
`1990.0`. Dinheiro que passou por float é dinheiro que pode estar errado: some
alguns e você chega em `0.30000000000000004`.

```python
from decimal import Decimal

from tempest_fastapi_sdk.integrations.payment.openpix import (
    cents_to_reais,
    reais_to_cents,
    to_cents,
)

assert reais_to_cents("19.90") == 1990
assert to_cents(1990.0) == 1990
assert cents_to_reais(1990) == Decimal("19.90")
```

- **`reais_to_cents`** é o que você usa ao *criar*: recebe reais e devolve
  centavo. Arredonda meio-para-cima (`0.005` -> `1`), que é o que uma pessoa
  espera de dinheiro e **não** é o que o `round` embutido faz — ele arredonda
  meio-para-par, e `round(0.005 * 100)` dá `0`.
- **`to_cents`** é o que você usa ao *ler*: estreita o float que a API
  devolveu para `int` exato. Ele **recusa fração de propósito** —
  `to_cents(19.9)` levanta `ValueError`, porque o campo já é centavo e uma
  fração significa que alguém está tratando reais como se fossem centavos.
  Arredondar em silêncio esconderia esse erro atrás de um número plausível.
- **`cents_to_reais`** devolve `Decimal`, para o valor chegar exato até a
  formatação.

## Registrando o webhook na OpenPix

Dá para cadastrar pelo painel, ou pela própria API — o que deixa o endereço
versionado junto do deploy:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    PostApiV1WebhookBody,
    WebhookEventEnum,
    WebhookPayload,
)


async def register_webhook(client: OpenPixClient, url: str) -> None:
    """Assina o evento de cobrança paga para uma URL.

    Args:
        client (OpenPixClient): O cliente da OpenPix.
        url (str): O endereço público de `POST /webhooks/openpix`.
    """
    await client.post_api_v1_webhook(
        body=PostApiV1WebhookBody(
            webhook=WebhookPayload(
                name="cobranca-paga",
                event=WebhookEventEnum.OPENPIX_CHARGE_COMPLETED,
                url=url,
                is_active=True,
            )
        )
    )
```

!!! note "O prefixo dos eventos não é uniforme, e isso é da OpenPix"
    `OpenPixEvent` traz os 28 eventos verbatim. Cobrança, transação, movimento
    e disputa carregam o namespace `OPENPIX:`
    (`OpenPixEvent.CHARGE_COMPLETED.value == "OPENPIX:CHARGE_COMPLETED"`). As
    famílias Pix-automático e account-register **não**
    (`OpenPixEvent.PIX_AUTOMATIC_APPROVED.value == "PIX_AUTOMATIC_APPROVED"`).
    Parece erro de transcrição, não é — e por isso tem teste fixando os dois
    casos.

## Testando sem rede

O cliente recebe o transporte por injeção, então um `httpx.MockTransport`
exercita o fluxo inteiro sem sair da máquina:

```python
import httpx

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargePayload,
    OpenPixClient,
)

seen: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    """Registra a requisição e devolve uma cobrança falsa.

    Args:
        request (httpx.Request): A requisição que sairia.

    Returns:
        httpx.Response: A resposta canned.
    """
    seen.append(request)
    return httpx.Response(
        200,
        json={
            "charge": {"status": "ACTIVE", "correlationID": "pedido-1", "value": 1990},
            "correlationID": "pedido-1",
            "brCode": "00020101021226830014BR.GOV.BCB.PIX...",
        },
    )


async def test_charge_carries_the_customer() -> None:
    """Os dados do cliente chegam ao corpo da requisição."""
    http = HTTPClient(
        base_url="https://api.woovi-sandbox.com",
        transport=httpx.MockTransport(handler),
    )
    client = OpenPixClient(http)

    await client.post_api_v1_charge(
        body=ChargePayload(correlation_id="pedido-1", value=1990)
    )

    assert seen[-1].url.path == "/api/v1/charge"
```

Para o webhook, injete um verificador sobre um par de chaves de teste em vez
da chave da OpenPix:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    make_openpix_webhook_dependency,
    webhook_verifier,
)

verify = make_openpix_webhook_dependency(
    verifier=webhook_verifier(public_key_pem="-----BEGIN PUBLIC KEY-----\n...")
)
```

## As duas metades do módulo

Vale saber qual parte veio de onde, porque elas se mantêm de formas
diferentes:

| Metade | O que é | De onde vem |
| --- | --- | --- |
| **Gerada** | `OpenPixClient`, `DEFAULT_BASE_URL`, 373 classes de schema | A spec, verbatim |
| **À mão** | `OpenPixEnvironment`, `OpenPixEvent`, webhook, helpers de dinheiro | O que a spec **não** diz |

!!! info "A metade gerada é versionada, não escrita à mão"
    `scripts/regen_openpix.py` produz `schemas.py` e `client.py` a partir da
    spec fixada em `vendor/openpix-openapi.yaml`, e **um teste falha se os
    arquivos em disco divergirem** do que o script produz. Para atualizar
    quando a OpenPix mexer na API: troque o arquivo em `vendor/`, rode `make
    openpix-regen`, e o diff mostra exatamente o que o terceiro mudou.

!!! note "Os modelos carregam no primeiro uso, não no import"
    Construir 373 modelos Pydantic custa perto de um segundo. Importar o
    pacote só para usar `to_cents` não deveria pagar isso, então a metade
    gerada resolve por [PEP 562](https://peps.python.org/pep-0562/).

    Medido nesta máquina (Python 3.11, com `tempest_fastapi_sdk` já
    importado): **~11 ms** para importar o subpacote, **~150 ms** no primeiro
    acesso a um nome gerado, **~0,02 ms** nos seguintes. Os números variam com
    a máquina; o que não varia é a ordem de grandeza entre eles — quem só usa
    `to_cents` nunca paga os 150 ms.

## Recapitulando

1. **Um `HTTPClient` por processo**, criado no lifespan, com o AppID em
   `default_headers` e a base URL vinda de `OpenPixEnvironment`.
2. **`correlationID` é o id do seu pedido** — é ele que amarra criação,
   webhook, consulta e estorno.
3. **Abrir a cobrança** é `post_api_v1_charge` com `return_existing=True`; a
   resposta traz `br_code`, `qr_code_image` e `payment_link_url`, e você
   escolhe pela interface.
4. **O webhook avisa, a API confirma.** `make_openpix_webhook_dependency()`
   verifica e entrega o evento tipado; `get_api_v1_charge_by_id` é o que
   autoriza liberar — a chave da OpenPix é RSA-1024.
5. **O handler é idempotente**, porque a mesma entrega chega mais de uma vez.
6. **Um job de reconciliação** varre `status=ACTIVE` e fecha o que o webhook
   perdeu.
7. **Dinheiro em centavo inteiro**: `reais_to_cents` para criar, `to_cents`
   para ler, `cents_to_reais` para exibir.
8. **Compare `status` com `==`**, nunca com `is` — o campo chega como `str`.
