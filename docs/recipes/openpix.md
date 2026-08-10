# OpenPix (Pix via Woovi)

A OpenPix inteira já vem no SDK. Sem gerador para rodar, sem spec para baixar:

```python
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.openpix import (
    Charge,
    OpenPixClient,
    OpenPixEnvironment,
)

http: HTTPClient = HTTPClient(
    base_url=OpenPixEnvironment.SANDBOX.base_url,
    default_headers={"Authorization": "<seu AppID>"},
)
client: OpenPixClient = OpenPixClient(http)
```

São **358 schemas** e **105 operações**, mais as quatro coisas que a
especificação não diz. 🚀

## As duas metades

O módulo tem uma metade gerada e uma escrita à mão, e vale saber qual é qual:

| Metade | O que é | De onde vem |
| --- | --- | --- |
| **Gerada** | `OpenPixClient`, `DEFAULT_BASE_URL`, 358 classes de schema | A spec, verbatim |
| **À mão** | `OpenPixEnvironment`, `OpenPixEvent`, webhook, helpers de dinheiro | O que a spec **não** diz |

!!! info "A metade gerada é versionada, não escrita à mão"
    `scripts/regen_openpix.py` produz `schemas.py` e `client.py` a partir da
    spec fixada em `vendor/openpix-openapi.yaml`, e **um teste falha se os
    arquivos em disco divergirem** do que o script produz. Editar à mão é como
    código gerado versionado apodrece; aqui isso quebra a suíte.

    Para atualizar quando a OpenPix mexer na API: troque o arquivo em
    `vendor/`, rode `make openpix-regen`, e o diff mostra exatamente o que o
    terceiro mudou.

!!! note "Os 358 modelos carregam no primeiro uso, não no import"
    Construir 358 modelos Pydantic custa perto de um segundo. Importar o
    pacote só para usar `to_cents` não deveria pagar isso, então a metade
    gerada resolve por [PEP 562](https://peps.python.org/pep-0562/).

    Medido: **2 ms** para importar o pacote, ~200 ms no primeiro acesso a um
    nome gerado, ~0,03 ms nos seguintes.

!!! tip "Precisa de outra API que o SDK não traz?"
    O `tempest openapi-client` continua existindo e é a ferramenta certa para
    isso. Veja [Cliente de integração (OpenAPI)](openapi-client.md). O que mora
    aqui são as integrações comuns o bastante para que todo serviço estivesse
    rodando a mesma geração e mantendo a mesma camada à mão por cima.

## O que a spec não diz

### 1. Os dois ambientes são domínios diferentes

Produção é `api.openpix.com.br`. Testes é `api.woovi-sandbox.com` — outro
domínio, não um subdomínio. Nenhum dos dois soletra o outro.

```python
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEnvironment

from src.core.settings import settings
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient

environment: OpenPixEnvironment = (
    OpenPixEnvironment.PRODUCTION
    if settings.ENVIRONMENT == "production"
    else OpenPixEnvironment.SANDBOX
)

http: HTTPClient = HTTPClient(
    base_url=environment.base_url,
    default_headers={"Authorization": settings.OPENPIX_APP_ID},
)
openpix: OpenPixClient = OpenPixClient(http)
```

### 2. `value` é centavo, mas chega como float

A especificação escreve, textualmente, *"Value in cents of this charge"* — e
tipa o campo como `number`. O modelo gerado então valida `1990` para o float
`1990.0`.

Dinheiro que passou por float é dinheiro que pode estar errado: some alguns e
você chega em `0.30000000000000004`. Centavo existe justamente para evitar
isso, e a camada JSON desfaz.

```python
from tempest_fastapi_sdk.integrations.payment.openpix import cents_to_reais, reais_to_cents, to_cents

to_cents(1990.0)          # 1990  (int, exato)
reais_to_cents("19.90")   # 1990
cents_to_reais(1990)      # Decimal("19.90")
```

!!! warning "`to_cents` recusa fração de propósito"
    `to_cents(19.9)` levanta `ValueError`. O campo **já é** centavo, então uma
    fração significa que quem chamou está tratando um valor em reais como se
    fosse centavo. Arredondar em silêncio esconderia esse erro atrás de um
    número plausível.

!!! tip "`reais_to_cents` arredonda meio-para-cima"
    É o que uma pessoa espera de dinheiro (`0.005` → `1` centavo) e **não** é
    o que o `round` embutido faz: ele arredonda meio-para-par, e
    `round(0.005 * 100)` dá `0`.

### 3. Os 28 eventos de webhook

Portados literalmente do `WebhookEventEnum` da especificação:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixEvent

OpenPixEvent.CHARGE_COMPLETED.value    # "OPENPIX:CHARGE_COMPLETED"
OpenPixEvent.PIX_AUTOMATIC_APPROVED.value  # "PIX_AUTOMATIC_APPROVED"
```

!!! note "O prefixo não é uniforme, e isso é da OpenPix"
    Eventos de cobrança, transação, movimento e disputa carregam o namespace
    `OPENPIX:`. As famílias Pix-automático e account-register **não**. Parece
    erro de transcrição, não é — e por isso tem teste fixando os dois casos.

### 4. Como validar o webhook

A OpenPix assina cada entrega com a chave privada dela e publica a pública. O
SDK já tinha o `RSAWebhookSignatureVerifier`; o que faltava era amarrar os
três fatos — qual header, qual chave, o que a string `event` significa.

!!! warning "Verificar assinatura precisa de `cryptography`"
    O módulo **importa** numa instalação mínima, mas `verify()` levanta
    `ImportError` na primeira entrega de verdade — em produção, não no boot.
    Instale antes:

    ```bash
    uv add cryptography
    # ou, pelo extra do SDK que já o traz:
    uv add "tempest-fastapi-sdk[webpush]"
    ```

    O nome `[webpush]` não tem relação com pagamento; ele é só o extra que
    empacota `cryptography` hoje. Se você só quer validar webhook da OpenPix,
    instalar `cryptography` direto é mais honesto.

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixEvent,
    OpenPixWebhookEvent,
    make_openpix_webhook_dependency,
    to_cents,
)

from tempest_fastapi_sdk.integrations.payment.openpix import Charge

router: APIRouter = APIRouter(prefix="/webhooks", tags=["webhooks"])
verify = make_openpix_webhook_dependency()


@router.post("/openpix")
async def receive_openpix(
    event: OpenPixWebhookEvent = Depends(verify),
) -> dict[str, str]:
    """Recebe uma entrega já verificada da OpenPix.

    Args:
        event (OpenPixWebhookEvent): A entrega verificada e decodificada.

    Returns:
        Confirmação para a OpenPix parar de reentregar.
    """
    if event.event is OpenPixEvent.CHARGE_COMPLETED:
        charge: Charge = Charge.model_validate(event.payload["charge"])
        cents: int = to_cents(charge.value)
        print(charge.correlation_id, cents)
    return {"status": "ok"}
```

A dependência verifica a assinatura, decodifica o corpo e entrega o evento já
resolvido. `event.payload` continua sendo o dict cru — os schemas gerados
moram no seu serviço, então você valida só o ramo que interessa.

## A parte de segurança, sem suavizar

!!! danger "A chave da OpenPix é RSA-1024"
    Conferido carregando na `cryptography`: 1024 bits, expoente 65537. Está
    **abaixo do piso de 2048 bits** que o NIST recomenda desde 2013, e isso
    limita o que a assinatura consegue provar.

    **Trate uma assinatura válida como evidência de que a entrega veio da
    OpenPix, não como autorização para movimentar dinheiro.** Antes de agir
    num `CHARGE_COMPLETED`, releia a cobrança pela API:

    ```python
    if event.event is OpenPixEvent.CHARGE_COMPLETED:
        confirmed = await openpix.get_api_v1_charge_by_id(
            id=event.payload["charge"]["correlationID"]
        )
        if confirmed.charge.status == "COMPLETED":
            await liberar_pedido(...)
    ```

    A assinatura é um filtro contra ruído de entrada. A leitura pela API é o
    fato. Nada aqui aumenta a força da chave — a mitigação é não confiar nela
    além do que ela é.

!!! warning "Reenvio (replay)"
    A assinatura cobre o corpo e mais nada, então uma entrega capturada
    continua válida para sempre. Trate seu handler como **idempotente** —
    chave pelo `correlationID` da cobrança e ignore o que já processou. Veja
    [Idempotência](idempotency.md).

### Se a OpenPix rodar a chave

A chave vem embutida, mas é sobrescrevível — uma constante fixa deixaria todo
consumidor preso esperando release do SDK:

```python
from tempest_fastapi_sdk.integrations.payment.openpix import (
    decode_public_key,
    make_openpix_webhook_dependency,
    webhook_verifier,
)

nova = decode_public_key("LS0tLS1CRUdJTiBQVUJMSUMgS0VZ...")
verify = make_openpix_webhook_dependency(
    verifier=webhook_verifier(public_key_pem=nova)
)
```

A OpenPix publica a chave em base64, não em PEM. `decode_public_key` decodifica
e **confere que o resultado é mesmo um PEM** — uma colagem truncada falharia,
sem isso, só lá na frente, como assinatura inválida numa entrega de verdade.

## Duas decisões que evitam derrubar seu serviço

!!! check "Evento desconhecido não quebra a requisição"
    A OpenPix adiciona eventos. Um serviço que devolve 500 num evento que
    nunca viu transforma a release do fornecedor em indisponibilidade própria.
    O nome fica em `event.event_name` e `event.event` fica `None`.

!!! check "Corpo que não é JSON, mas verificou, continua entregue"
    Se verificou, veio da OpenPix. Rejeitar descartaria uma entrega que o
    fornecedor considera enviada. `payload` fica vazio e `body` carrega os
    bytes.

## Recapitulando

1. **Tudo já vem instalado** — `OpenPixClient`, 358 schemas, 105 operações.
   Nenhum gerador para rodar.
2. **A metade gerada é regenerável e testada contra drift** (`make
   openpix-regen`); editar à mão quebra a suíte.
3. **Carga preguiçosa**: 2 ms para importar, ~200 ms no primeiro nome gerado.
4. **`OpenPixEnvironment`** resolve produção vs sandbox — domínios diferentes.
5. **`to_cents` / `reais_to_cents` / `cents_to_reais`** desfazem o float que a
   spec impõe, e recusam fração em vez de arredondar escondido.
6. **`OpenPixEvent`** traz os 28 eventos verbatim, prefixo irregular incluído.
7. **`make_openpix_webhook_dependency()`** verifica, decodifica e entrega o
   evento tipado; evento novo e corpo não-JSON não derrubam a rota.
8. **A chave é RSA-1024** — assinatura válida prova origem, não autoriza
   movimentar dinheiro. Releia a cobrança pela API e mantenha o handler
   idempotente.
