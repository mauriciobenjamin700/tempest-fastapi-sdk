# Kestra (orquestrador de workflows)

[Kestra](https://kestra.io) roda *flows* declarativos em YAML — chamar uma
API, esperar, tentar de novo, encadear passos. Seu serviço fala com ele em
**duas direções**, e as duas já cabem nas peças do SDK:

- **Serviço → Kestra:** o serviço dispara um flow (webhook ou API de
  execução) com o [`HTTPClient`](http-client.md).
- **Kestra → serviço:** o flow chama uma rota do serviço pela task
  `io.kestra.plugin.core.http.Request`, protegida por
  [`make_token_dependency`](http.md).

E, como disparar um flow a partir de uma escrita no banco é o caso clássico
de *dual write*, o fecho é o par [outbox](outbox.md) (entrega ao Kestra) +
`WebhookInbox` (recebe o resultado uma vez só).

!!! info "Sem módulo novo no SDK, de propósito"
    O contrato inteiro é "POST com um header" nos dois sentidos, e o
    `HTTPClient` já traz retry, circuit breaker e `default_headers`. Um
    `KestraClient` só valeria se dois serviços copiassem a mesma chamada.
    Também não há dependência nova: nem o SDK Python do Kestra, que seria um
    wrapper de HTTP carregando os limites de versão dele para o seu lock.

!!! check "Tudo aqui foi executado"
    Cada comando e cada saída desta página saiu de uma execução real contra
    **Kestra 2.0.6 OSS** (`kestra/kestra:v2.0.6-slim`, digest
    `sha256:82f7c84f00fb883a6ff93c6d07e31f30eaabd0c84d229fb56a80edeeecd8dbba`)
    com um serviço FastAPI local, Python 3.12. Outra versão do Kestra pode
    responder diferente — a [seção do tenant](#2-o-caminho-do-tenant-medido)
    existe por causa disso.

## 1. Subir o Kestra para testar

```bash
docker run -d --name lab-kestra -p 18080:8080 \
  --add-host=host.docker.internal:host-gateway \
  -e SECRET_SERVICE_TOKEN=dG9vbC1zZWNyZXQtMTIz \
  -e SECRET_WEBHOOK_KEY=aG9vay1rZXktYWJjMTIz \
  -e KESTRA_CONFIGURATION='
kestra:
  server:
    basic-auth:
      username: admin@example.com
      password: Kestra12345!
' \
  kestra/kestra:v2.0.6-slim server local
```

O que cada pedaço faz:

- `--add-host=host.docker.internal:host-gateway` deixa o flow (dentro do
  container) alcançar o seu serviço, que roda no host.
- `SECRET_<NOME>` vira `{{ secret('<NOME>') }}` no flow. O valor vai em
  **base64** (`echo -n 'tool-secret-123' | base64`). É assim que o token da
  rota protegida e a chave do webhook ficam fora do YAML.
- `basic-auth` liga a autenticação da API; sem credencial, ela responde `401`
  (medido na seção 5).

Espere o servidor subir e confira a versão:

```bash
curl -s -u admin@example.com:Kestra12345! localhost:18080/api/v1/configs
```

```text
{"uuid":"2lrxrR7eSFQ8QtCWti1TDa","version":"2.0.6","edition":"OSS","commitId":"b59444f", ...
```

!!! tip "Fixe a versão da imagem"
    A tag `latest-slim` aponta para um *nightly* (o rótulo
    `org.opencontainers.image.version` da imagem que baixei dizia
    `nightly`). Use `v2.0.6-slim`, ou a que você validar.

## 2. O caminho do tenant (medido)

A documentação do Kestra é inconsistente: há exemplos com
`/api/v1/main/...` e outros com `/api/v1/...`. Medi na 2.0.6, com Basic Auth:

| Chamada | Com `/main` | Sem `/main` |
| --- | --- | --- |
| `GET  /flows/search` | `200` | `200` |
| `POST /executions/{namespace}/{flow}` | `200` | `200` |
| `POST /executions/webhook/{namespace}/{flow}/{key}` | `200` | `200` |
| `GET  /outputs/executions/{id}` | `200` | **`400`** |
| `GET  /configs` | **`404`** | `200` |

O `400` diz o motivo, e vale para qualquer segmento que não seja `main`:

```text
{"type":"https://kestra.io/docs/api-reference/problems/bad-request","title":"Bad request","status":400,"detail":"Tenant must be 'main' for OSS version", ...
```

O `400` de `/api/v1/outputs/executions/<id>` é o mesmo de um tenant que não
é `main`, o que sugere que, sem o segmento, `outputs` foi lido como tenant.
Algumas rotas toleram a omissão, outras não — então **use sempre
`/api/v1/main/...`** nas rotas de tenant. A única exceção medida é
`/api/v1/configs`, que só respondeu sem `/main`.

## 3. O serviço

Um arquivo só, com tudo que as seções seguintes usam: a rota-ferramenta que
o flow chama, o callback que o flow manda ao terminar, e o pedido que grava
**o pedido e o evento na mesma transação**.

```python title="kestra_service.py"
import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseOutboxModel,
    BaseRepository,
    BaseSchema,
    BaseWebhookEventModel,
    HTTPClient,
    OutboxRelay,
    WebhookInbox,
    make_token_dependency,
    register_exception_handlers,
)

TOKEN_SECRET: str = os.environ.get("TOKEN_SECRET", "tool-secret-123")
KESTRA_URL: str = os.environ.get("KESTRA_URL", "http://127.0.0.1:18080")
KESTRA_WEBHOOK_PATH: str = os.environ.get(
    "KESTRA_WEBHOOK_PATH",
    "/api/v1/main/executions/webhook/tempest/order_created/hook-key-abc123",
)

db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///./kestra_demo.db")
require_token = make_token_dependency(TOKEN_SECRET)
Session = Annotated[AsyncSession, Depends(db.session_dependency)]


class OrderModel(BaseModel):
    """Pedido do serviço."""

    __tablename__ = "orders"

    reference: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="created")


class OutboxModel(BaseOutboxModel):
    """Eventos pendentes de entrega ao Kestra."""

    __tablename__ = "outbox"


class WebhookEventModel(BaseWebhookEventModel):
    """Callbacks recebidos do Kestra, deduplicados por execução."""

    __tablename__ = "webhook_events"


class OrderCreateSchema(BaseSchema):
    """Corpo de `POST /api/orders`."""

    reference: str


class EnrichRequestSchema(BaseSchema):
    """Corpo que o flow manda para a rota-ferramenta."""

    order_id: str
    source: str


class CallbackSchema(BaseSchema):
    """Corpo que o flow manda quando termina."""

    execution_id: str
    order_id: str
    enriched: dict[str, Any]


inbox: WebhookInbox[WebhookEventModel] = WebhookInbox(
    db, model=WebhookEventModel, backoff_base_seconds=1.0
)
kestra: HTTPClient = HTTPClient(base_url=KESTRA_URL, timeout=15.0)


async def publish(event: BaseOutboxModel) -> None:
    """Entrega um evento do outbox ao webhook do Kestra.

    Args:
        event (BaseOutboxModel): A linha do outbox.

    Raises:
        httpx.HTTPStatusError: Se o Kestra não responde 2xx; o relay agenda retry.
    """
    response = await kestra.post(KESTRA_WEBHOOK_PATH, json=event.payload)
    response.raise_for_status()


async def apply_callback(event: WebhookEventModel, session: AsyncSession) -> None:
    """Aplica o callback do Kestra: marca o pedido como enriquecido.

    Args:
        event (WebhookEventModel): O evento reivindicado.
        session (AsyncSession): A sessão da transação que também fecha a linha.
    """
    order = await session.get(OrderModel, UUID(event.payload["order_id"]))
    if order is not None:
        order.status = "enriched"


async def loop_forever(step: Any) -> None:
    """Roda `step()` a cada meio segundo até ser cancelada.

    Args:
        step (Any): Corrotina sem argumentos a repetir.
    """
    while True:
        await step()
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Cria as tabelas, sobe o relay do outbox e o sweep do inbox."""
    await db.connect()
    await db.create_tables()
    relay = OutboxRelay(db, model=OutboxModel, publish=publish, backoff_base_seconds=1.0)

    async def sweep() -> None:
        await inbox.drain_once(apply_callback)

    tasks = [
        asyncio.create_task(relay.run(poll_interval=0.5)),
        asyncio.create_task(loop_forever(sweep)),
    ]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
    await kestra.aclose()
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)


@app.post("/api/orders", status_code=201)
async def create_order(data: OrderCreateSchema, session: Session) -> dict[str, str]:
    """Cria o pedido e o evento `orders.created` na mesma transação."""
    repo: BaseRepository[OrderModel] = BaseRepository(session, model=OrderModel)
    order_id = uuid4()
    event = OutboxModel.new_event(
        "orders.created", {"order_id": str(order_id), "reference": data.reference}
    )
    order = await repo.save_with_outbox(
        OrderModel(id=order_id, reference=data.reference), event
    )
    return {"id": str(order.id), "status": order.status}


@app.get("/api/orders/{order_id}")
async def get_order(order_id: UUID, session: Session) -> dict[str, str]:
    """Devolve o estado atual do pedido."""
    order = await BaseRepository(session, model=OrderModel).get_by_id(order_id)
    return {"id": str(order.id), "status": order.status}


@app.post("/api/tools/enrich-order", dependencies=[Depends(require_token)])
async def enrich_order(data: EnrichRequestSchema) -> dict[str, str]:
    """Rota-ferramenta que o flow chama, protegida por `X-Token`."""
    return {"order_id": data.order_id, "caller": data.source, "tier": "gold"}


@app.post("/api/callbacks/kestra", dependencies=[Depends(require_token)])
async def kestra_callback(data: CallbackSchema) -> dict[str, bool]:
    """Grava o callback no inbox e responde 200, mesmo para repetição."""
    new_id = await inbox.record(
        "kestra", data.execution_id, "flow.finished", data.model_dump()
    )
    return {"recorded": new_id is not None}
```

Pedaço por pedaço:

- `make_token_dependency(TOKEN_SECRET)` compara o header `X-Token` com
  `hmac.compare_digest`. A rota-ferramenta e o callback usam a mesma
  dependência; `POST /api/orders` fica aberta porque é o serviço quem
  a chama.
- `create_order` grava `OrderModel` e a linha do outbox numa transação
  (`save_with_outbox`) — veja a [receita do outbox](outbox.md).
- `publish` é o adaptador `publish(event)` que o `OutboxRelay` exige: um
  argumento só, e `raise_for_status()` para que não-2xx vire retry.
- `kestra_callback` grava no `WebhookInbox` com `(provider, external_id)`
  e responde `200` **mesmo para repetição** — o provedor só precisa parar de
  tentar. O `external_id` é o id da execução do Kestra.
- `apply_callback` roda dentro da transação que também marca a linha do
  inbox como processada.

Suba o serviço (bind `0.0.0.0` aqui porque quem chama é um container, outra
origem):

```bash
uvicorn kestra_service:app --host 0.0.0.0 --port 18000
```

Confira a proteção da rota-ferramenta, com e sem o token:

```bash
curl -s -X POST localhost:18000/api/tools/enrich-order \
  -H 'X-Token: tool-secret-123' -H 'content-type: application/json' \
  -d '{"order_id":"1","source":"curl"}'
curl -s -X POST localhost:18000/api/tools/enrich-order \
  -H 'content-type: application/json' -d '{"order_id":"1","source":"curl"}' -w ' %{http_code}\n'
```

```text
{"order_id":"1","caller":"curl","tier":"gold"}
{"detail":"Invalid or missing token","code":"UNAUTHORIZED","details":{}} 401
```

## 4. Os flows

Dois flows, ambos chamando a rota protegida com o token vindo do
`secret(...)`. O primeiro é disparado pela **API de execução**, com um input:

```yaml title="flows/report.yml"
id: report
namespace: tempest

inputs:
  - id: order_id
    type: STRING

tasks:
  - id: enrich
    type: io.kestra.plugin.core.http.Request
    uri: http://host.docker.internal:18000/api/tools/enrich-order
    method: POST
    contentType: application/json
    headers:
      X-Token: "{{ secret('SERVICE_TOKEN') }}"
    body: |
      {"order_id": "{{ inputs.order_id }}", "source": "kestra"}

outputs:
  - id: enriched
    type: STRING
    value: "{{ outputs.enrich.body }}"
```

O segundo é disparado por **webhook**; o corpo chega em `trigger.body`, e a
`key` do trigger também vem de um secret. Ao terminar ele avisa o serviço
(`callback`), mandando `execution.id` — o id que o inbox usa para deduplicar:

```yaml title="flows/order_created.yml"
id: order_created
namespace: tempest

tasks:
  - id: enrich
    type: io.kestra.plugin.core.http.Request
    uri: http://host.docker.internal:18000/api/tools/enrich-order
    method: POST
    contentType: application/json
    headers:
      X-Token: "{{ secret('SERVICE_TOKEN') }}"
    body: |
      {"order_id": "{{ trigger.body.order_id }}", "source": "kestra"}

  - id: callback
    type: io.kestra.plugin.core.http.Request
    uri: http://host.docker.internal:18000/api/callbacks/kestra
    method: POST
    contentType: application/json
    headers:
      X-Token: "{{ secret('SERVICE_TOKEN') }}"
    body: |
      {
        "execution_id": "{{ execution.id }}",
        "order_id": "{{ trigger.body.order_id }}",
        "enriched": {{ outputs.enrich.body }}
      }

outputs:
  - id: callback_status
    type: STRING
    value: "{{ outputs.callback.code }}"

triggers:
  - id: webhook
    type: io.kestra.plugin.core.trigger.Webhook
    key: "{{ secret('WEBHOOK_KEY') }}"
```

Importe os dois (na 2.0.6 a resposta foi `200`):

```bash
curl -s -u admin@example.com:Kestra12345! -X POST localhost:18080/api/v1/main/flows \
  -H 'Content-Type: application/x-yaml' --data-binary @flows/report.yml -o /dev/null -w '%{http_code}\n'
curl -s -u admin@example.com:Kestra12345! -X POST localhost:18080/api/v1/main/flows \
  -H 'Content-Type: application/x-yaml' --data-binary @flows/order_created.yml -o /dev/null -w '%{http_code}\n'
```

```text
200
200
```

## 5. Disparar do serviço

```python title="kestra_trigger.py"
import asyncio
from base64 import b64encode
from typing import Any

from tempest_fastapi_sdk import HTTPClient, RetryPolicy

TENANT: str = "main"


def make_kestra_client(base_url: str, username: str, password: str) -> HTTPClient:
    """Monta o client do Kestra com Basic Auth, retry e circuit breaker.

    Args:
        base_url (str): Raiz do Kestra, sem barra final.
        username (str): Usuário do Basic Auth.
        password (str): Senha do Basic Auth.

    Returns:
        HTTPClient: Client pronto para a API e para os webhooks.
    """
    credentials: str = b64encode(f"{username}:{password}".encode()).decode()
    return HTTPClient(
        base_url=base_url,
        timeout=30.0,
        retry_policy=RetryPolicy(max_attempts=3),
        default_headers={"Authorization": f"Basic {credentials}"},
    )


async def trigger_webhook(
    client: HTTPClient,
    namespace: str,
    flow_id: str,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Dispara um flow pelo Webhook trigger.

    Args:
        client (HTTPClient): Client do Kestra.
        namespace (str): Namespace do flow.
        flow_id (str): Id do flow.
        key (str): A `key` do trigger; é a única checagem do webhook.
        payload (dict[str, Any]): Corpo JSON, que o flow lê em `trigger.body`.

    Returns:
        dict[str, Any]: A execução criada (`id`, `state`, ...).

    Raises:
        httpx.HTTPStatusError: Se o Kestra não responde 2xx.
    """
    response = await client.post(
        f"/api/v1/{TENANT}/executions/webhook/{namespace}/{flow_id}/{key}",
        json=payload,
    )
    response.raise_for_status()
    return dict(response.json())


async def start_execution(
    client: HTTPClient,
    namespace: str,
    flow_id: str,
    inputs: dict[str, str],
    *,
    wait: bool = False,
) -> dict[str, Any]:
    """Cria uma execução pela API, com os inputs do flow.

    Args:
        client (HTTPClient): Client do Kestra.
        namespace (str): Namespace do flow.
        flow_id (str): Id do flow.
        inputs (dict[str, str]): Inputs do flow; vão como `multipart/form-data`.
        wait (bool): Segura a conexão até a execução terminar.

    Returns:
        dict[str, Any]: A execução (`id`, `state`, ...).

    Raises:
        httpx.HTTPStatusError: Se o Kestra não responde 2xx.
    """
    response = await client.post(
        f"/api/v1/{TENANT}/executions/{namespace}/{flow_id}",
        params={"wait": str(wait).lower()},
        files={name: (None, value) for name, value in inputs.items()},
    )
    response.raise_for_status()
    return dict(response.json())


async def read_outputs(client: HTTPClient, execution_id: str) -> dict[str, Any]:
    """Lê os outputs do flow de uma execução já terminada.

    Args:
        client (HTTPClient): Client do Kestra.
        execution_id (str): Id da execução.

    Returns:
        dict[str, Any]: Os outputs declarados em `outputs:` no flow.

    Raises:
        httpx.HTTPStatusError: Se o Kestra não responde 2xx.
    """
    response = await client.get(f"/api/v1/{TENANT}/outputs/executions/{execution_id}")
    response.raise_for_status()
    return dict(response.json())


async def main() -> None:
    """Dispara o webhook e a API contra um Kestra local e imprime o resultado."""
    client = make_kestra_client("http://127.0.0.1:18080", "admin@example.com", "Kestra12345!")
    try:
        created = await trigger_webhook(
            client, "tempest", "order_created", "hook-key-abc123", {"order_id": "demo-1"}
        )
        print("webhook:", created["id"], created["state"]["current"])

        execution = await start_execution(
            client, "tempest", "report", {"order_id": "demo-2"}, wait=True
        )
        print("api:", execution["id"], execution["state"]["current"])
        print("outputs:", await read_outputs(client, execution["id"]))
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python kestra_trigger.py
```

```text
webhook: 49vM41z1Hsqrd2r0umfjdl CREATED
api: 2EvSyvxiQWdVrzX3Co2MqN SUCCESS
outputs: {'enriched': '{"order_id":"demo-2","caller":"kestra","tier":"gold"}'}
```

O que a medição mostrou, e que muda como você escreve o client:

- **O webhook responde na hora**, com a execução recém-criada
  (`state.current` = `CREATED`); o flow continua rodando por trás. Para
  saber do resultado, use o callback da seção 6 — ou `?wait=true` na API.
- **A `key` é a única checagem do webhook.** A chamada sem Basic Auth, só
  com a URL certa, deu `200`; com a chave errada deu `404`
  (`"detail":"Webhook not found"`). Trate a chave como segredo (por isso
  ela vem de `secret('WEBHOOK_KEY')`) e use uma longa.
- **A API de execução recebe os inputs como `multipart/form-data`.**
  `-d order_id=44` (urlencoded) deu `415` com
  `Content Type [application/x-www-form-urlencoded] not allowed. Allowed types: [multipart/form-data]`.
  Por isso o `start_execution` usa `files={nome: (None, valor)}` do httpx,
  que força `multipart` sem anexar arquivo.
- **Sem credencial, a API responde `401`.**
- **`wait=true` segura a conexão** e devolve a execução já terminada
  (`SUCCESS`); sem ele, o estado volta como `RUNNING`.
- **Os outputs do flow não vêm no JSON da execução** (o campo `outputs`
  não aparece na 2.0.6). Saem de `GET /api/v1/main/outputs/executions/{id}`,
  que é o que `read_outputs` chama.

## 6. Entrega garantida: outbox → Kestra, inbox ← Kestra

Dispare um pedido. O serviço responde `201` e **não** espera o Kestra:

```bash
curl -s -X POST localhost:18000/api/orders -H 'content-type: application/json' \
  -d '{"reference":"PED-001"}'
sleep 1
curl -s localhost:18000/api/orders/a88f7992-d764-4415-b2da-a045d7ee6647
```

```text
{"id":"a88f7992-d764-4415-b2da-a045d7ee6647","status":"created"}
{"id":"a88f7992-d764-4415-b2da-a045d7ee6647","status":"enriched"}
```

No log do serviço, a ordem completa — o relay entregou o evento, o flow
chamou a rota-ferramenta com o token e depois o callback:

```text
INFO:     127.0.0.1:47757 - "POST /api/orders HTTP/1.1" 201 Created
INFO:     172.17.0.2:58240 - "POST /api/tools/enrich-order HTTP/1.1" 200 OK
INFO:     172.17.0.2:58254 - "POST /api/callbacks/kestra HTTP/1.1" 200 OK
```

### O Kestra fora do ar

Com o container congelado (`docker pause lab-kestra`), um novo pedido
responde `201` do mesmo jeito. Vinte e cinco segundos depois, a linha do
outbox continua `pending` e o pedido `created`; descongelado
(`docker unpause lab-kestra`), a linha vira `sent` e o pedido `enriched`:

```text
{"id":"906c4c47-21b4-486f-af8d-6e308d53b19d","status":"created"}
('orders.created', 'pending', 0, None)          # linha do pedido novo, congelado, 25 s depois
{"id":"906c4c47-21b4-486f-af8d-6e308d53b19d","status":"created"}
('orders.created', 'sent', 0)                   # depois do unpause
{"id":"906c4c47-21b4-486f-af8d-6e308d53b19d","status":"enriched"}
```

!!! note "O que este teste mede e o que não mede"
    `docker pause` congela o processo; a conexão fica pendurada em vez de
    ser recusada, e aos 25 s o relay ainda não tinha registrado falha
    (`attempts` em `0`). Este teste mostra que o evento **espera, durável,
    na tabela** e sai quando o Kestra volta. O caminho com `attempts` subindo, `last_error`
    e backoff exponencial é o das falhas rápidas, descrito na
    [receita do outbox](outbox.md#falhas-e-retry).

### O callback repetido

O inbox responde `200` e grava uma vez só. Duas entregas com o mesmo
`execution_id`:

```text
{"recorded":true}
{"recorded":false}
```

```text
('kestra', 'dup-1', 'received')     # uma linha só, antes do sweep processar
```

!!! danger "É at-least-once nos dois sentidos"
    O outbox pode publicar o mesmo evento duas vezes (worker morto entre o
    envio e o `commit`), então o **flow** pode rodar duas vezes para o
    mesmo pedido. Faça o flow idempotente por `trigger.body.order_id`, ou
    guarde a chave de idempotência do pedido. Do lado de cá, o inbox já
    deduplica o callback por `(provider, external_id)`.

## Derrubar o ambiente

```bash
docker rm -f lab-kestra
```

## Recap

- **Serviço → Kestra:** `HTTPClient` + `POST /api/v1/main/executions/webhook/{ns}/{flow}/{key}`
  (resposta imediata, a `key` é a única checagem) ou
  `POST /api/v1/main/executions/{ns}/{flow}` com `multipart/form-data`
  (`?wait=true` para esperar).
- **Tenant:** use `/api/v1/main/...` sempre; na 2.0.6 outro segmento dá
  `400`, e omitir `/main` só funciona em algumas rotas.
- **Kestra → serviço:** task `http.Request` com
  `X-Token: "{{ secret('...') }}"` contra uma rota com
  `make_token_dependency`.
- **Entrega garantida:** `save_with_outbox` + `OutboxRelay` para ir;
  `WebhookInbox.record` com o id da execução para voltar.
- Nenhum módulo novo: tudo isto é `HTTPClient`, `make_token_dependency`,
  `OutboxRelay` e `WebhookInbox`.
