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
    PaymentStatus,
    PixChargeRequest,
    PixProvider,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider


async def main() -> None:
    """Run a checkout from open charge to paid, with nobody scanning a QR."""
    provider: PixProvider = FakePixProvider()

    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="pedido-1042"),
    )
    print(charge.status.value)

    event = provider.advance(charge.provider_charge_id, PaymentStatus.PAID)
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
    Todo fake aceita `fail_next(erro)`: a próxima chamada levanta, e só ela.

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
| `FakePixProvider` | `PixProvider` (OpenPix) | `advance(id, status)`, `charges` |
| `FakeTextBackend` | `TextBackend` (modelo local) | `queue(...)`, `prompts` |
| `FakeModerationBackend` | `ModerationBackend` | `flag(substring)`, `checked` |
| `FakePushDispatcher` | `PushDispatcher` (FCM/APNs/WebPush) | `sent`, `sent_to(token)` |
| `FakeEmailUtils` | `EmailUtils` (SMTP) | `outbox`, `sent_to(address)` |
| `FakeGeocodingBackend` | `GeocodingBackend` (Nominatim) | `add_place(...)`, `queries` |
| `FakeRoutingBackend` | `RoutingBackend` (OSRM) | `add_route(...)`, `routes` |
| `FakeWebSearchBackend` | `WebSearchBackend` (Searxng) | `add_results(...)`, `queries` |

Todos expõem `fail_next(erro)` e `calls` — a lista de métodos que rodaram, em
ordem.

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
    serviço recusar fake quando `settings.ENVIRONMENT == "production"`.

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
  falha o guard, em vez de shippar sem checagem.

Medido: com um parâmetro renomeado de `max_results` para `limit`, o guard
falha com `['self', 'query', 'limit'] == ['self', 'query', 'max_results']`.

## Recap

- `from tempest_fastapi_sdk.testing.fakes import Fake...` — oito costuras, sem
  credencial e sem rede.
- Fake não é mock: guarda estado, e você **move** esse estado
  (`advance`, `flag`, `add_place`, `queue`).
- `fail_next(erro)` alcança o ramo que falha, com a exceção que o cliente
  real levanta.
- `calls`, `outbox`, `sent`, `prompts`, `charges`, `queries`: o que aconteceu
  fica inspecionável.
- Resolução é lazy: pedir um fake de Pix não importa genai, push nem geo.
- Guard de assinatura impede o fake de divergir da costura.
