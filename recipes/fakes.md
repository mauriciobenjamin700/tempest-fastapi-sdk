# Fakes: rode o fluxo inteiro sem provedor nenhum

Seu serviço cobra por Pix, manda email de ativação, dispara push, geocodifica
endereço e chama um modelo. Para rodar isso na sua máquina você precisaria de
cinco credenciais — e para **testar**, de cinco mocks escritos à mão.

Ou de nada disso.

```python
from tempest_fastapi_sdk.testing.fakes import FakePixProvider
```

Cada fake implementa uma costura do SDK e não fala com ninguém: sem
credencial, sem conta de sandbox, sem rede.

## Por que não é um mock

Um mock responde a chamada que você programou. Um fake **guarda estado** — e
estes deixam você mover esse estado:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatusEnum,
    PixChargeRequest,
    PixProvider,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider


async def main() -> None:
    """Run a checkout from open charge to paid, with nobody scanning a QR."""
    fake = FakePixProvider()
    provider: PixProvider = fake

    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="pedido-1042"),
    )
    print(charge.status.value)

    event = fake.advance(charge.provider_charge_id, PaymentStatusEnum.PAID)
    print(event.type.value)


if __name__ == "__main__":
    asyncio.run(main())
```

Saída:

```text
pending
charge_paid
```

`advance` é a parte que o provedor real não te dá. Chegar a `PAID` num
sandbox exige alguém escaneando um QR code; chegar a `CHARGED_BACK` exige
alguém abrindo uma disputa. Aqui é uma chamada.

!!! tip "O ramo que vale testar é o que falha"
    Todo fake de adapter da tabela abaixo aceita `fail_next(erro)`: a
    próxima chamada levanta, e só ela.

    ```python
    from tempest_fastapi_sdk.push.dispatcher import PushDeviceGoneError
    from tempest_fastapi_sdk.testing.fakes import FakePushDispatcher

    dispatcher = FakePushDispatcher()
    dispatcher.fail_next(PushDeviceGoneError("token retired"))
    ```

    Passe a exceção que o cliente real levanta — assim o caminho exercitado é
    o caminho que a produção toma. Enfileire várias para falhar várias
    chamadas, na ordem.

## O que existe

| Fake | Substitui | Steering próprio |
| --- | --- | --- |
| `FakePixProvider` | `PixProvider` (OpenPix, Mercado Pago) | `advance(id, status)`, `charges` |
| `FakeCardProvider` | `CardProvider` (cartão) | `decline_next(motivo)`, `advance(id, status)`, `charges` |
| `FakeObjectStorageClient` | `SubjectStorageClient` (MinIO / S3) | `fail_delete(chaves)`, `objects`, `deleted` |
| `FakePayoutProvider` | `PayoutProvider` (saque Pix) | `transfers`, `status=` no construtor |
| `FakeTextBackend` | `TextBackend` (modelo local) | `queue(...)`, `prompts` |
| `FakeStructuredBackend` | `chat_structured` / `generate_structured` do `OllamaGenerator` | `queue(instancia)`, `requests`, `prompts` |
| `FakeModerationBackend` | `ModerationBackend` | `flag(substring)`, `checked` |
| `FakePushDispatcher` | `PushDispatcher` (FCM/APNs/WebPush) | `sent`, `sent_to(token)` |
| `FakeEmailUtils` | `EmailUtils` (SMTP) | `outbox`, `sent_to(address)` |
| `FakeGeocodingBackend` | `GeocodingBackend` (Nominatim) | `add_place(...)`, `queries` |
| `FakeRoutingBackend` | `RoutingBackend` (OSRM) | `add_route(...)`, `routes` |
| `FakeWebSearchBackend` | `WebSearchBackend` (Searxng) | `add_results(...)`, `queries` |

Todos os da tabela expõem `fail_next(erro)`. Todos menos o `FakeEmailUtils` expõem também
`calls` — a lista de métodos que rodaram, em ordem; no `FakeEmailUtils` o
registro é o próprio `outbox`, porque `send` é o único método que ele
substitui.

### Um fake de API HTTP: `FakeMercadoPagoOrdersAPI`

Os fakes da tabela ficam **no lugar** do adapter. O
`FakeMercadoPagoOrdersAPI` fica um nível abaixo: ele é a API de Orders do
Mercado Pago, como handler do `httpx`, e o `MercadoPagoPixProvider` /
`MercadoPagoCardProvider` **de verdade** rodam sobre ele —
`MercadoPagoPixProvider(api.http_client())`. Assim o que fica testado é o seu
serviço **e** o adapter: idempotência pela referência, estorno parcial,
recusa de cartão (`402`), releitura da order no webhook.

O steering é o que só o provedor faz: `pay`, `expire`, `fail`, `cancel`,
`capture`, `refund`, `decline_next_card()` e `lose_next_answer()`. Cada
movimento devolve a notificação **assinada** que o anuncia, pronta para a sua
rota de webhook. Ele não tem `fail_next`, e `calls` guarda `(método, path)` de
cada requisição em vez de nomes de método. Receita completa, com o teste
manual contra o serviço rodando (`tempest mercado-pago fake-api` e
`simulate-webhook`):
[Mercado Pago: testando webhooks »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/mercado-pago-webhooks.md#nivel-1-simulado-pelo-sdk-sem-rede).

## Cartão: recusa é resultado, não exceção

`FakeCardProvider` segue o contrato `CardProvider`. A cobrança é aprovada,
a não ser que `decline_next(motivo)` tenha enfileirado uma recusa — que
volta como cobrança `FAILED`, do jeito que o adapter real devolve o `402` do
provedor:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment import (
    CardChargeRequest,
    PaymentStatusEnum,
)
from tempest_fastapi_sdk.testing.fakes import FakeCardProvider


async def main() -> None:
    """Decline one card, approve the next, refund it in two steps."""
    card = FakeCardProvider()
    request = CardChargeRequest(
        amount_cents=10000,
        reference="pedido-1",
        card_token="tok",
        payment_method_id="visa",
    )

    card.decline_next("rejected_by_issuer")
    declined = await card.create_card_charge(request)
    paid = await card.create_card_charge(request)
    partial = await card.refund_card_charge(paid.provider_charge_id, amount_cents=3000)
    full = await card.refund_card_charge(paid.provider_charge_id)

    assert declined.status is PaymentStatusEnum.FAILED
    print(declined.status_detail, partial.status.value, full.status.value)


asyncio.run(main())
```

```text
rejected_by_issuer paid refunded
```

`capture=False` devolve `AUTHORIZED`, à espera de `capture_card_charge` ou
`cancel_card_charge`. As transições que o provedor recusa — capturar o que
não está autorizado, estornar o que não foi pago, estornar mais do que
sobrou — levantam `ValueError`: o ramo de recusa é alcançável, mas o tipo da
exceção é do fake (o adapter real levanta `httpx.HTTPStatusError`).
`advance(id, PaymentStatusEnum.CHARGED_BACK)` chega ao chargeback, que nenhum
método do contrato alcança.

## Email: o outbox no lugar do SMTP

`FakeEmailUtils` é subclasse de `EmailUtils`, não implementação de um
protocolo — porque `UserAuthService` é tipado contra a classe concreta, e é a
herança que faz o fake passar por ali com o type-checker satisfeito:

```python
import asyncio

from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils


async def main() -> None:
    """Send nothing, and assert on what would have been sent."""
    mailer = FakeEmailUtils()

    await mailer.send("ana@example.test", "Ativação", "Seu link: ...")

    print(len(mailer.outbox))
    print(mailer.outbox[0].subject)
    print(mailer.sent_to("ana@example.test")[0].body)


if __name__ == "__main__":
    asyncio.run(main())
```

Saída:

```text
1
Ativação
Seu link: ...
```

Só o `send` é substituído. `render_template` continua sendo o do
`EmailUtils`, então um teste pode afirmar sobre o **mesmo** HTML que a
produção renderiza — passe `template_dir=` como você passa em produção.

## Modelo de texto: microssegundos no lugar de VRAM

```python
import asyncio

from tempest_fastapi_sdk.testing.fakes import FakeTextBackend


async def main() -> None:
    """Answer from a queue, then from the echo default."""
    backend = FakeTextBackend()
    backend.queue("Olá! Como posso ajudar?")

    print(await backend.generate("Cumprimente o cliente"))
    print(await backend.generate("Cumprimente o cliente"))

    chunks = [chunk async for chunk in backend.stream("conte até tres")]
    print(chunks)


if __name__ == "__main__":
    asyncio.run(main())
```

Saída:

```text
Olá! Como posso ajudar?
[fake] Cumprimente o cliente
['[fake]', ' conte', ' até', ' tres']
```

Esgotada a fila, a resposta default **ecoa o prompt** — assim uma asserção
que falha diz qual prompt a produziu, em vez de mostrar um texto genérico. O
`stream` entrega pedaço por pedaço, para o seu render incremental ser de fato
exercitado.

## Saída estruturada: `FakeStructuredBackend`

Use quando a rota chama `chat_structured` ou `generate_structured` e o teste
precisa escolher o objeto que o modelo "devolveu".

```python
import asyncio

from pydantic import BaseModel

from tempest_fastapi_sdk.testing.fakes import FakeStructuredBackend


class Invoice(BaseModel):
    """The fields the model fills."""

    number: str
    total_cents: int


async def main() -> None:
    """Answer with a queued instance, then show what was recorded."""
    backend = FakeStructuredBackend()
    backend.queue(Invoice(number="42", total_cents=1990))

    invoice = await backend.generate_structured(
        "Nota 42, total R$ 19,90",
        Invoice,
        system="Extraia o número e o total.",
        think="low",
    )
    print(invoice)
    print(backend.requests[0].messages)
    print(backend.requests[0].think)

    try:
        await backend.chat_structured([], Invoice)
    except LookupError as error:
        print(error)


if __name__ == "__main__":
    asyncio.run(main())
```

Saída:

```text
number='42' total_cents=1990
[{'role': 'system', 'content': 'Extraia o número e o total.'}, {'role': 'user', 'content': 'Nota 42, total R$ 19,90'}]
low
FakeStructuredBackend has no reply queued for Invoice; call queue(Invoice(...)) first
```

| Superfície | O que faz |
| --- | --- |
| `queue(*instancias)` | Enfileira instâncias Pydantic, uma por chamada, em ordem. |
| `chat_structured(messages, schema, *, config, think, truncate, **kwargs)` | Mesma assinatura do `OllamaGenerator`; devolve a próxima da fila. |
| `generate_structured(prompt, schema, *, system, config, think, truncate, **kwargs)` | Idem; registra `system` e `prompt` como turnos, como o `OllamaGenerator` monta. |
| `requests` | Lista de `StructuredRequest`: `method`, `messages`, `schema`, `config`, `think`, `truncate`, `options`. |
| `prompts` | O conteúdo do último turno de cada chamada. |
| fila vazia | `LookupError` com o nome do schema — o fake não inventa instância. |
| instância de outro schema | `TypeError` nomeando os dois. |

Ele também satisfaz o protocolo `StructuredTextBackend`. Para trocar o
gerador numa rota pelo `dependency_overrides`, com login e lifespan, veja
[Testes: trocar o LLM por um fake numa rota](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/testing.md#trocar-o-llm-por-um-fake-numa-rota).

## Geo: sem Nominatim, sem OSRM

```python
import asyncio

from tempest_fastapi_sdk.geo import Coordinate
from tempest_fastapi_sdk.testing.fakes import (
    FakeGeocodingBackend,
    FakeRoutingBackend,
)


async def main() -> None:
    """Resolve a place from a table, and estimate a trip offline."""
    geocoder = FakeGeocodingBackend()
    geocoder.add_place("Recife", Coordinate(latitude=-8.05, longitude=-34.9))

    print(await geocoder.geocode("recife") is not None)
    print(await geocoder.geocode("rua que nao existe"))

    router = FakeRoutingBackend()
    estimate = await router.route(
        Coordinate(latitude=-8.05, longitude=-34.9),
        Coordinate(latitude=-7.99, longitude=-34.85),
    )
    print(estimate.source, round(estimate.distance_km, 1))


if __name__ == "__main__":
    asyncio.run(main())
```

Saída:

```text
True
None
fake 11.2
```

Duas decisões que valem explicar:

- **Endereço não cadastrado devolve `None`**, que é o que o backend real
  responde para um endereço que ninguém acha — e é o ramo que o serviço
  esquece de tratar.
- **A rota não inventa aritmética**: o `FakeRoutingBackend` delega para
  `estimate_travel`, o estimador offline que o SDK já ships. Os números que
  seu teste afirma são os do próprio SDK, não uma conta paralela que pode
  divergir dele.

`reverse` devolve o **mais próximo** dos lugares cadastrados, não o de
coordenada exata: um serviço reverte uma leitura de GPS, e leitura de GPS não
cai em cima do decimal que você guardou.

## Arquivo: download sem MinIO

`FakeObjectStorageClient` serve download pelo mesmo
`object_download_response` que o `AsyncMinIOClient` e o
`LocalObjectStorageClient` usam: `Range`, `206`, `416` e os headers saem
iguais, e os chunks têm o tamanho do `chunk_size` pedido.

```python
import asyncio

from starlette.requests import Request

from tempest_fastapi_sdk.storage import object_download_response
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient


async def main() -> None:
    """Serve a stored object whole, then one byte range of it."""
    storage = FakeObjectStorageClient()
    await storage.put_object("docs/report.txt", b"0123456789" * 3)

    whole = await object_download_response(storage, "docs/report.txt", chunk_size=8)
    print(whole.status_code, [chunk async for chunk in whole.body_iterator])

    ranged = Request(
        {"type": "http", "method": "GET", "headers": [(b"range", b"bytes=5-14")]}
    )
    partial = await object_download_response(
        storage, "docs/report.txt", request=ranged, chunk_size=4
    )
    print(partial.status_code, partial.headers["content-range"])
    print([chunk async for chunk in partial.body_iterator])


if __name__ == "__main__":
    asyncio.run(main())
```

Saída:

```text
200 [b'01234567', b'89012345', b'67890123', b'456789']
206 bytes 5-14/30
[b'5678', b'9012', b'34']
```

!!! note "Os três clientes passam pelo mesmo teste"
    `tests/storage/test_download_contract.py` roda o download, com e sem
    `Range`, contra MinIO (com um `minio.Minio` falso), disco local e o fake.
    `tests/test_storage_client_signature_guard.py` compara a assinatura de
    cada método dos protocolos de storage com a dos três clientes — foi a
    ausência de `chunk_size` no fake que derrubava esse exemplo com
    `TypeError` até a #757.

## Ligando no serviço

O fake entra onde o real entrava — no `Depends`:

```python
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixProvider
from tempest_fastapi_sdk.testing.fakes import FakePixProvider

app = FastAPI()
_FAKE_PROVIDER = FakePixProvider()


def get_pix_provider() -> PixProvider:
    """Provide the Pix provider this deployment charges with.

    Returns:
        PixProvider: The provider, seen through the contract.
    """
    return _FAKE_PROVIDER


@app.post("/charges")
async def create_charge(
    provider: PixProvider = Depends(get_pix_provider),
) -> dict[str, str]:
    """Open a charge.

    Args:
        provider (PixProvider): Injected provider.

    Returns:
        dict[str, str]: The copy-and-paste code, for the payer.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="pedido-1042"),
    )
    return {"br_code": charge.br_code or ""}
```

Em teste, use `app.dependency_overrides[get_pix_provider]` para injetar um
fake novo por teste — instância nova é estado limpo.

!!! warning "Fake é para desenvolvimento e teste, não para produção"
    Nada aqui persiste: o estado vive no processo e morre com ele. E nada
    aqui cobra, entrega ou notifica de verdade. Ligar um fake em produção
    significa um checkout que aceita pagamento que nunca entrou.

    A defesa é a de sempre — o provedor vem de configuração, e o ambiente
    decide. Se quiser um alarme explícito, faça o `build_provider` do seu
    serviço recusar fake quando o ambiente for produção — com um campo que o
    **seu** `Settings` declara (`ENVIRONMENT`, por exemplo): nenhum mixin de
    settings do SDK traz esse campo.

## Garantia: o fake não pode divergir da costura

Um fake com assinatura diferente da costura é pior que fake nenhum — o
serviço passa contra ele e falha contra o provedor real, que é exatamente o
defeito que o fake existe para evitar.

Por isso `tests/testing/test_fakes_contract.py` compara, para cada fake:

- que todo callable da costura existe no fake;
- que os **nomes de parâmetro** e a anotação de retorno batem, por
  `inspect.signature`;
- que é `async` exatamente onde a costura é `async` — um substituto sync
  passaria numa checagem por nome e bloquearia o event loop;
- e que todo fake exportado está coberto: fake novo sem entrada na tabela
  falha o guard, em vez de shippar sem checagem;
- e, para o `FakeStructuredBackend`, que `chat_structured` e
  `generate_structured` batem com os do `OllamaGenerator` parâmetro a
  parâmetro, com tipo de parâmetro (posicional, keyword-only) e default.

Medido: com um parâmetro renomeado de `max_results` para `limit`, o guard
falha com `['self', 'query', 'limit'] == ['self', 'query', 'max_results']`.

## Recap

- `from tempest_fastapi_sdk.testing.fakes import Fake...` — doze costuras, sem
  credencial e sem rede.
- Fake não é mock: guarda estado, e você **move** esse estado
  (`advance`, `flag`, `add_place`, `queue`).
- `fail_next(erro)` — em todo fake da tabela, não no
  `FakeMercadoPagoOrdersAPI` — alcança o ramo que falha, com a exceção que o
  cliente real levanta.
- `calls`, `outbox`, `sent`, `prompts`, `charges`, `queries`: o que aconteceu
  fica inspecionável.
- Resolução é lazy: pedir um fake de Pix não importa genai nem geo — nem
  carrega nada além do que o `import tempest_fastapi_sdk` já carregou (o
  `push` entra por ali, não pelo fake).
- Guard de assinatura impede o fake de divergir da costura.
