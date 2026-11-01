# HTTP client (saída)

`HTTPClient` é um wrapper tipado sobre o `httpx.AsyncClient` para **chamar
serviços externos** com retry + backoff exponencial, circuit-breaker,
timeouts padrão e propagação do `X-Request-ID`. É a contraparte de saída do
[middleware HTTP](http.md) (que cuida do tráfego de entrada). O `httpx` já
vem no pacote base, então não precisa de extra; o `[http]` continua
existindo como forma explícita (e opcional) de declarar a dependência.

## Uso básico

O client é seguro pra compartilhar entre requests no mesmo event loop — ele
reusa o connection pool interno. Use como async context manager (ou guarde
um singleton em [`resources.py`](../architecture.md) e feche no lifespan).

```python
from typing import Any

from tempest_fastapi_sdk import HTTPClient


async def fetch_user(user_id: str) -> dict[str, Any]:
    """GET /users/{id} no serviço externo."""
    async with HTTPClient(base_url="https://api.example.com", timeout=10.0) as client:
        response = await client.get(f"/users/{user_id}")
        response.raise_for_status()
        return response.json()
```

!!! warning "Não reutilize um client já fechado"
    Sair do bloco `async with` chama `__aexit__`, que fecha o connection pool
    (`aclose()`). Para chamadas pontuais, construa o client dentro do `async with`
    como acima. Para reuso entre requests, guarde um singleton e feche **uma vez**
    no lifespan (veja o exemplo do circuit-breaker abaixo) — nunca envolva cada
    chamada de um singleton compartilhado em `async with`.

Métodos: `get` / `post` / `put` / `patch` / `delete` (e `request` genérico),
todos repassando kwargs pro httpx (`json=`, `params=`, `headers=`, ...) e
devolvendo um `httpx.Response`.

## Retry + backoff + circuit-breaker

Passe um `RetryPolicy` e ajuste os limites do breaker na construção:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import CircuitOpenError, HTTPClient, RetryPolicy

client = HTTPClient(
    base_url="https://api.example.com",
    timeout=5.0,
    retry_policy=RetryPolicy(
        max_attempts=3,                # 1 tentativa + 2 retries
        backoff_initial_seconds=0.5,   # 0.5s, 1s, 2s... (exponencial)
        backoff_max_seconds=8.0,       # teto por espera
    ),
    failure_threshold=5,               # abre o circuito após 5 falhas seguidas
    recovery_seconds=30.0,             # meio-aberto após 30s
    default_headers={"X-Api-Key": "..."},
    propagate_request_id=True,         # encaminha o X-Request-ID do request atual
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Fecha o connection pool compartilhado no shutdown."""
    yield
    await client.aclose()


async def call() -> None:
    # Singleton compartilhado — chame direto, sem `async with` por chamada
    # (isso fecharia o pool). O pool é fechado uma vez no lifespan acima.
    try:
        await client.post("/charge", json={"amount": 100})
    except CircuitOpenError:
        # O circuito está aberto — não martele o upstream caído.
        ...
```

- **Retry**: refeito em erros transitórios (timeouts, 5xx, falhas de conexão)
  até `max_attempts`, com backoff exponencial limitado por `backoff_max_seconds`.
  `RetryPolicy(retry_on_read_timeout=False)` tira o `ReadTimeout` dessa lista
  e mantém o resto — veja o aviso abaixo.
- **Circuit-breaker**: após `failure_threshold` falhas consecutivas o circuito
  **abre** e as chamadas levantam `CircuitOpenError` imediatamente (sem tocar
  a rede) até passar `recovery_seconds`, quando entra em meio-aberto pra testar.
- **Request-ID**: com `propagate_request_id=True`, o `X-Request-ID` do request
  em curso (via `RequestIDMiddleware`) é repassado ao upstream, costurando os
  logs ponta-a-ponta.

!!! warning "`stream()` só refaz antes da primeira linha"
    Em `client.stream(...)` o retry e o circuit-breaker cobrem só a
    **abertura** do stream. Depois que a primeira linha foi entregue, uma
    falha no meio (inclusive um `ReadTimeout` entre dois chunks) propaga
    pro chamador — refazer o POST reemitiria desde o começo linhas que você
    já consumiu. Até esta versão o `ReadTimeout` no meio do stream era
    refeito, e o chamador via `["Hello ", "Hello ", "world"]` de dois POSTs.

!!! info "Corpo binário: `stream_bytes()`"
    `client.stream_bytes(...)` tem o mesmo contrato de retry e
    circuit-breaker do `stream()`, mas entrega o corpo em chunks de `bytes`
    conforme o servidor descarrega — áudio, download de arquivo. Nos dois,
    um status de erro tem o corpo lido antes do `raise`, então
    `exc.response.content` traz a mensagem do servidor.

!!! warning "Timeout de leitura numa chamada cara"
    Um `ReadTimeout` quer dizer que o request **chegou** ao servidor e a
    resposta não veio a tempo. Numa chamada rápida isso é conexão travada, e
    refazer resolve. Numa chamada cujo custo é o trabalho do servidor — uma
    geração de modelo — o servidor aborta o trabalho quando o cliente
    desiste, e a nova tentativa recomeça do zero com o mesmo timeout: a
    falha chega depois de `max_attempts` × timeout em vez de um. Para essas,
    passe `RetryPolicy(retry_on_read_timeout=False)`; o `ConnectError`
    continua sendo refeito, porque nenhum trabalho começou. Os geradores de
    [`genai`](genai.md) já usam esse default no client que criam.

!!! tip "Guarde como singleton em resources.py"
    Crie o `HTTPClient` uma vez (em `src/api/dependencies/resources.py`),
    exponha um `get_http_client`, e feche no lifespan com `await client.aclose()`
    — assim o connection pool é reaproveitado entre requests.

### O nome do header de saída: `request_id_header=`

O `RequestIDMiddleware(header_name=...)` decide de onde o id é **lido**. O
nome com que ele **sai** é do `HTTPClient`: `X-Request-ID` por padrão
(a constante `REQUEST_ID_HEADER`), ou o que você passar em
`request_id_header=`. São independentes de propósito: cada upstream espera o
seu nome.

```python
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import HTTPClient, RequestIDMiddleware

seen: list[dict[str, str | None]] = []


def upstream(request: httpx.Request) -> httpx.Response:
    seen.append(
        {
            "X-Request-ID": request.headers.get("X-Request-ID"),
            "X-Correlation-ID": request.headers.get("X-Correlation-ID"),
        }
    )
    return httpx.Response(200, json={})


default_client: HTTPClient = HTTPClient(base_url="http://upstream", transport=httpx.MockTransport(upstream))
partner_client: HTTPClient = HTTPClient(
    base_url="http://upstream",
    request_id_header="X-Correlation-ID",
    transport=httpx.MockTransport(upstream),
)
app: FastAPI = FastAPI()
app.add_middleware(RequestIDMiddleware, header_name="X-Correlation-ID")


@app.get("/call")
async def call() -> list[dict[str, str | None]]:
    seen.clear()
    await default_client.get("/")
    await partner_client.get("/")
    await partner_client.get("/", headers={"x-correlation-id": "manual"})
    return list(seen)


for row in TestClient(app).get("/call", headers={"X-Correlation-ID": "abc-123"}).json():
    print(row)
```

```text
{'X-Request-ID': 'abc-123', 'X-Correlation-ID': None}
{'X-Request-ID': None, 'X-Correlation-ID': 'abc-123'}
{'X-Request-ID': None, 'X-Correlation-ID': 'manual'}
```

- `default_client` repassa o id no nome padrão, `X-Request-ID`, mesmo com o
  middleware lendo `X-Correlation-ID`.
- `partner_client` repassa no nome que o parceiro espera.
- Header passado na chamada vence o propagado, e a comparação ignora
  maiúsculas: `x-correlation-id` explícito substitui o id em vez de mandar
  dois valores.

!!! check "Desde a próxima versão"
    Até a 0.340.0 o nome de saída era fixo em `X-Request-ID`, e um
    `x-request-id` explícito em minúsculas era enviado **junto** com o id
    propagado, como dois valores no mesmo header.

## A mesma curva fora do HTTP — `async_retry`

`RetryPolicy` descreve a espera: quantas tentativas, quanto tempo entre elas,
com teto. Isso não é uma ideia de HTTP — um `connect` no RabbitMQ durante o
boot, um dispatch de push e uma chamada a terceiro querem a mesma curva. Só o
`HTTPClient` se importa com `retry_statuses`.

`async_retry` aplica a curva a qualquer corrotina:

```python
from tempest_fastapi_sdk import RetryPolicy, async_retry

BOOT: RetryPolicy = RetryPolicy(max_attempts=5, backoff_initial_seconds=0.5)


@async_retry(BOOT, (ConnectionError, TimeoutError))
async def connect_broker(url: str) -> None:
    """Conecta ao broker, tolerando um servidor que ainda está subindo."""
    raise ConnectionError(f"{url} indisponível")
```

Três decisões que um laço escrito à mão costuma errar, e que aqui são
explícitas:

* **O que conta como transiente é seu.** O segundo argumento é a tupla de
  exceções que consome tentativa. Tudo fora dela propaga na hora, sem gastar
  budget — um `TypeError` retentado três vezes é o mesmo bug, mais tarde.
* **A última exceção propaga**, não um wrapper. Quem sabia reagir a um
  `ConnectionError` permanente continua vendo um `ConnectionError`.
* **Cancelamento é honrado.** O default `(Exception,)` deixa
  `asyncio.CancelledError` passar, porque ela deriva de `BaseException` desde
  o 3.8 — retentar uma task cancelada é brigar com o cancelamento.

!!! warning "`max_attempts` conta a primeira tentativa"
    `max_attempts=3` é uma tentativa e dois retries, com duas esperas. Zero é
    recusado na decoração: rodaria a função nenhuma vez e devolveria `None` de
    algo anotado para devolver `T`.


### Para onde vão os registros — `logger=`

`async_retry` escreve exatamente um WARNING por retry e um ERROR quando o
budget acaba. O parâmetro `logger=` aceita qualquer coisa que saiba fazer essas
duas chamadas — o `logging.Logger` da stdlib, ou o `LogUtils` que este próprio
SDK entrega ao serviço:

```python
from tempest_fastapi_sdk import LogUtils, RetryPolicy, async_retry

logger: LogUtils = LogUtils(__name__)
BROKER: RetryPolicy = RetryPolicy(max_attempts=4, backoff_initial_seconds=0.2)


@async_retry(BROKER, (ConnectionError,), logger=logger)
async def publish(payload: dict[str, str]) -> None:
    """Publica no broker, tolerando uma reconexão em andamento."""
    raise ConnectionError(f"broker indisponível para {payload}")
```

!!! info "O contrato é `RetryLogger`, não `logging.Logger`"
    Até a 0.280.0 a anotação era `logging.Logger | None`, e `LogUtils` **não é**
    um `logging.Logger` — ele embrulha um. O código acima rodava, porque as
    formas batem, e falhava só no type check:

    ```text
    error: Argument "logger" to "async_retry" has incompatible type "LogUtils";
           expected "Logger | None"  [arg-type]
    ```

    Quem não roda type-checker nunca descobria; quem roda escrevia
    `logger=logger.logger`, alcançando por baixo da fachada. Hoje o parâmetro é
    tipado como `RetryLogger` — um `Protocol` com `warning` e `error`, que é
    tudo o que a função usa — e os dois passam. Os parâmetros obrigatórios do
    protocolo são posicionais-only de propósito: `logging.Logger` chama o
    primeiro de `msg` e `LogUtils` chama de `message`, e um protocolo que
    nomeasse o parâmetro recusaria um dos dois no basedpyright.


## Recap

- `HTTPClient` = `httpx.AsyncClient` tipado + retry/backoff/circuit-breaker + X-Request-ID (nome de saída em `request_id_header=`).
- Sem extra obrigatório (`httpx` vem no pacote base; `[http]` é opcional). Métodos `get/post/put/patch/delete/request` → `httpx.Response`.
- `RetryPolicy(max_attempts, backoff_initial_seconds, backoff_max_seconds)` controla o retry; `retry_on_read_timeout=False` para chamada cara (geração).
- `async_retry(policy, exceptions, logger=)` aplica a curva a qualquer corrotina; `logger=` aceita `logging.Logger` ou `LogUtils`.
- `failure_threshold` / `recovery_seconds` controlam o breaker; `CircuitOpenError` quando aberto.
- Compartilhe um singleton e feche com `aclose()` no shutdown.
