# n8n (automação de workflows)

[n8n](https://n8n.io) monta *workflows* ligando nós: um **Webhook node** que
recebe uma chamada, um **HTTP Request node** que chama uma API, e assim por
diante. Seu serviço fala com ele em **duas direções**, e as duas já cabem nas
peças do SDK:

- **Serviço → n8n:** o serviço chama o Webhook node com o
  [`HTTPClient`](http-client.md) e um header de autenticação.
- **n8n → serviço:** o HTTP Request node chama uma rota do serviço com
  *Header Auth*, e a rota é protegida por
  [`make_token_dependency`](http.md).

E, como disparar um workflow a partir de uma escrita no banco é *dual
write*, o fecho é o par [outbox](outbox.md) (entrega ao n8n) +
`WebhookInbox` (recebe o resultado uma vez só).

!!! info "Sem módulo novo no SDK, de propósito"
    O contrato é "POST com um header" nos dois sentidos, e o `HTTPClient` já
    traz retry, circuit breaker e `default_headers`. Um `N8nClient` teria
    quase nenhum valor sobre `HTTPClient.post`.

!!! note "Por que não `WebhookSender`"
    O `WebhookSender` assina a entrega com HMAC, mas o Webhook node do n8n
    autentica por Basic, Header ou JWT — não verifica HMAC. O encaixe é o
    Header Auth.

!!! check "Tudo aqui foi executado"
    Cada comando e cada saída desta página saiu de uma execução real contra
    **n8n 2.43.7** (`n8nio/n8n:2.43.7`, digest
    `sha256:3db21366be1d3c77c1708963129c051948573019659e8873e0294be035ffc1b2`)
    com um serviço FastAPI local, Python 3.12. As mensagens de resposta abaixo
    são as dessa versão.

## 1. Subir o n8n para testar

```bash
docker run -d --name lab-n8n -p 15678:5678 \
  --add-host=host.docker.internal:host-gateway \
  -e N8N_SECURE_COOKIE=false \
  n8nio/n8n:2.43.7
```

`--add-host=host.docker.internal:host-gateway` deixa o workflow (dentro do
container) alcançar o seu serviço, que roda no host. Crie o dono da
instância e confira que ela está de pé:

```bash
curl -s -X POST localhost:15678/rest/owner/setup -H 'content-type: application/json' \
  -d '{"email":"owner@example.com","firstName":"Lab","lastName":"Owner","password":"Lab12345678"}'
curl -s -o /dev/null -w '%{http_code}\n' localhost:15678/healthz
```

```text
{"data":{"createdAt":"2026-10-17T19:16:18.597Z","id":"3d49df4b-8f1a-409e-b39f-3007a19ccd30","email":"owner@example.com","firstName":"Lab","lastName":"Owner", ... "role":"global:owner", ...
200
```

## 2. O serviço

Um arquivo só, com tudo que as seções seguintes usam: a rota-ferramenta que
o workflow chama, o callback que ele manda ao terminar, e o pedido que grava
**o pedido e o evento na mesma transação**.

```python title="n8n_service.py"
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
N8N_URL: str = os.environ.get("N8N_URL", "http://127.0.0.1:15678")
N8N_WEBHOOK_PATH: str = os.environ.get("N8N_WEBHOOK_PATH", "/webhook/order-created")
N8N_WEBHOOK_TOKEN: str = os.environ.get("N8N_WEBHOOK_TOKEN", "n8n-hook-secret-456")

db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///./n8n_demo.db")
require_token = make_token_dependency(TOKEN_SECRET)
Session = Annotated[AsyncSession, Depends(db.session_dependency)]


class OrderModel(BaseModel):
    """Pedido do serviço."""

    __tablename__ = "orders"

    reference: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="created")


class OutboxModel(BaseOutboxModel):
    """Eventos pendentes de entrega ao n8n."""

    __tablename__ = "outbox"


class WebhookEventModel(BaseWebhookEventModel):
    """Callbacks recebidos do n8n, deduplicados por execução."""

    __tablename__ = "webhook_events"


class OrderCreateSchema(BaseSchema):
    """Corpo de `POST /api/orders`."""

    reference: str


class EnrichRequestSchema(BaseSchema):
    """Corpo que o workflow manda para a rota-ferramenta."""

    order_id: str
    source: str


class CallbackSchema(BaseSchema):
    """Corpo que o workflow manda quando termina."""

    execution_id: str
    order_id: str
    enriched: dict[str, Any]


inbox: WebhookInbox[WebhookEventModel] = WebhookInbox(
    db, model=WebhookEventModel, backoff_base_seconds=1.0
)
n8n: HTTPClient = HTTPClient(
    base_url=N8N_URL,
    timeout=15.0,
    default_headers={"X-Webhook-Token": N8N_WEBHOOK_TOKEN},
)


async def publish(event: BaseOutboxModel) -> None:
    """Entrega um evento do outbox ao Webhook node do n8n.

    Args:
        event (BaseOutboxModel): A linha do outbox.

    Raises:
        httpx.HTTPStatusError: Se o n8n não responde 2xx; o relay agenda retry.
    """
    response = await n8n.post(N8N_WEBHOOK_PATH, json=event.payload)
    response.raise_for_status()


async def apply_callback(event: WebhookEventModel, session: AsyncSession) -> None:
    """Aplica o callback do n8n: marca o pedido como enriquecido.

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
    await n8n.aclose()
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
    """Rota-ferramenta que o workflow chama, protegida por `X-Token`."""
    return {"order_id": data.order_id, "caller": data.source, "tier": "gold"}


@app.post("/api/callbacks/n8n", dependencies=[Depends(require_token)])
async def n8n_callback(data: CallbackSchema) -> dict[str, bool]:
    """Grava o callback no inbox e responde 200, mesmo para repetição."""
    new_id = await inbox.record(
        "n8n", data.execution_id, "workflow.finished", data.model_dump()
    )
    return {"recorded": new_id is not None}
```

Pedaço por pedaço:

- `make_token_dependency(TOKEN_SECRET)` compara o header `X-Token` com
  `hmac.compare_digest`. A rota-ferramenta e o callback usam a mesma
  dependência — é a contraparte do HTTP Request node com *Header Auth*.
- `n8n` é o `HTTPClient` de saída, com o header do Webhook node em
  `default_headers`.
- `publish` é o adaptador `publish(event)` que o `OutboxRelay` exige: um
  argumento só, e `raise_for_status()` para que não-2xx vire retry.
- `n8n_callback` grava no `WebhookInbox` com `(provider, external_id)` e
  responde `200` **mesmo para repetição**. O `external_id` é o
  `$execution.id` do n8n.

Suba o serviço (bind `0.0.0.0` aqui porque quem chama é um container, outra
origem):

```bash
uvicorn n8n_service:app --host 0.0.0.0 --port 18001
```

## 3. Credenciais e workflows

Duas credenciais *Header Auth* (`httpHeaderAuth`): uma que o Webhook node
**exige** de quem chama, outra que o HTTP Request node **manda** ao serviço.

```json title="credentials.json"
[
  {
    "id": "credWebhookIn",
    "name": "Webhook de entrada (Header Auth)",
    "type": "httpHeaderAuth",
    "data": {
      "name": "X-Webhook-Token",
      "value": "n8n-hook-secret-456"
    }
  },
  {
    "id": "credServiceOut",
    "name": "Servico FastAPI (Header Auth)",
    "type": "httpHeaderAuth",
    "data": {
      "name": "X-Token",
      "value": "tool-secret-123"
    }
  }
]
```

O primeiro workflow responde **na hora** (`responseMode: onReceived`) e
termina avisando o serviço:

```json title="order_created.json"
{
  "id": "orderCreated01",
  "name": "order-created",
  "nodes": [
    {
      "id": "0b1a0000-0000-4000-8000-000000000001",
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2.1,
      "position": [0, 0],
      "webhookId": "5d1f0000-0000-4000-8000-000000000001",
      "parameters": {
        "httpMethod": "POST",
        "path": "order-created",
        "authentication": "headerAuth",
        "responseMode": "onReceived"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credWebhookIn", "name": "Webhook de entrada (Header Auth)" }
      }
    },
    {
      "id": "0b1a0000-0000-4000-8000-000000000002",
      "name": "Enriquecer pedido",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [260, 0],
      "parameters": {
        "method": "POST",
        "url": "http://host.docker.internal:18001/api/tools/enrich-order",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ { order_id: $('Webhook').item.json.body.order_id, source: 'n8n' } }}"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credServiceOut", "name": "Servico FastAPI (Header Auth)" }
      }
    },
    {
      "id": "0b1a0000-0000-4000-8000-000000000003",
      "name": "Avisar o servico",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [520, 0],
      "parameters": {
        "method": "POST",
        "url": "http://host.docker.internal:18001/api/callbacks/n8n",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ { execution_id: $execution.id, order_id: $('Webhook').item.json.body.order_id, enriched: $json } }}"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credServiceOut", "name": "Servico FastAPI (Header Auth)" }
      }
    }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "Enriquecer pedido", "type": "main", "index": 0 }]] },
    "Enriquecer pedido": { "main": [[{ "node": "Avisar o servico", "type": "main", "index": 0 }]] }
  },
  "settings": { "executionOrder": "v1" },
  "active": true
}
```

O segundo usa **Respond to Webhook** (`responseMode: responseNode`): quem
chamou espera e recebe o que o workflow devolve.

```json title="order_sync.json"
{
  "id": "orderSync0001",
  "name": "order-sync",
  "nodes": [
    {
      "id": "0b1a0000-0000-4000-8000-000000000011",
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2.1,
      "position": [0, 0],
      "webhookId": "5d1f0000-0000-4000-8000-000000000011",
      "parameters": {
        "httpMethod": "POST",
        "path": "order-sync",
        "authentication": "headerAuth",
        "responseMode": "responseNode"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credWebhookIn", "name": "Webhook de entrada (Header Auth)" }
      }
    },
    {
      "id": "0b1a0000-0000-4000-8000-000000000012",
      "name": "Enriquecer pedido",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [260, 0],
      "parameters": {
        "method": "POST",
        "url": "http://host.docker.internal:18001/api/tools/enrich-order",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ { order_id: $('Webhook').item.json.body.order_id, source: 'n8n' } }}"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credServiceOut", "name": "Servico FastAPI (Header Auth)" }
      }
    },
    {
      "id": "0b1a0000-0000-4000-8000-000000000013",
      "name": "Responder",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1.5,
      "position": [520, 0],
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{ $json }}"
      }
    }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "Enriquecer pedido", "type": "main", "index": 0 }]] },
    "Enriquecer pedido": { "main": [[{ "node": "Responder", "type": "main", "index": 0 }]] }
  },
  "settings": { "executionOrder": "v1" },
  "active": true
}
```

Importe pela CLI do próprio container (a ordem importa: credenciais antes):

```bash
docker cp n8n lab-n8n:/tmp/wf
docker exec lab-n8n n8n import:credentials --input=/tmp/wf/credentials.json
docker exec lab-n8n sh -c 'mkdir -p /tmp/wfs && cp /tmp/wf/order_*.json /tmp/wfs/'
docker exec lab-n8n n8n import:workflow --separate --input=/tmp/wfs
```

```text
Successfully imported 2 credentials.
Deactivating workflow "order-created".
Deactivating workflow "order-sync".
Successfully imported 2 workflows.
```

O import **desativa** os workflows. Tentei `--activeState=fromJson` e o n8n
recusou:

```text
The "--activeState=fromJson" flag can only be used when n8n is running in queue or multi-main mode. In regular deployment mode, workflow activation is not supported.
```

No modo comum, publique pelo id e **reinicie** — a própria CLI avisa
"Changes will not take effect if n8n is running":

```bash
docker exec lab-n8n n8n publish:workflow --id=orderCreated01
docker exec lab-n8n n8n publish:workflow --id=orderSync0001
docker restart lab-n8n
```

Depois do restart, o log fecha com
`Processed 0 draft workflows, 2 published workflows.`

## 4. Disparar do serviço

```python title="n8n_trigger.py"
import asyncio
from typing import Any

from tempest_fastapi_sdk import HTTPClient, RetryPolicy

PRODUCTION_PREFIX: str = "/webhook"
TEST_PREFIX: str = "/webhook-test"


def make_n8n_client(base_url: str, header_name: str, header_value: str) -> HTTPClient:
    """Monta o client do n8n com o Header Auth do Webhook node.

    Args:
        base_url (str): Raiz do n8n, sem barra final.
        header_name (str): Nome do header configurado na credencial Header Auth.
        header_value (str): Valor do header (o segredo).

    Returns:
        HTTPClient: Client com retry, circuit breaker e o header de auth.
    """
    return HTTPClient(
        base_url=base_url,
        timeout=30.0,
        retry_policy=RetryPolicy(max_attempts=3),
        default_headers={header_name: header_value},
    )


async def call_webhook(
    client: HTTPClient,
    path: str,
    payload: dict[str, Any],
    *,
    test: bool = False,
) -> dict[str, Any]:
    """Chama um Webhook node do n8n.

    Args:
        client (HTTPClient): Client do n8n.
        path (str): O `path` configurado no Webhook node.
        payload (dict[str, Any]): Corpo JSON, que o workflow lê em `body`.
        test (bool): Usa a URL de teste (`/webhook-test/...`) em vez da de produção.

    Returns:
        dict[str, Any]: O JSON da resposta. No modo imediato é só um aviso de
        que o workflow começou; com "Respond to Webhook" é o que o workflow devolve.

    Raises:
        httpx.HTTPStatusError: Se o n8n não responde 2xx.
    """
    prefix: str = TEST_PREFIX if test else PRODUCTION_PREFIX
    response = await client.post(f"{prefix}/{path}", json=payload)
    response.raise_for_status()
    return dict(response.json())


async def main() -> None:
    """Chama os dois modos de resposta contra um n8n local e imprime o resultado."""
    client = make_n8n_client("http://127.0.0.1:15678", "X-Webhook-Token", "n8n-hook-secret-456")
    try:
        immediate = await call_webhook(
            client, "order-created", {"order_id": "22222222-2222-4222-8222-222222222222"}
        )
        print("immediate:", immediate)
        synced = await call_webhook(client, "order-sync", {"order_id": "demo-2"})
        print("respond-to-webhook:", synced)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python n8n_trigger.py
```

```text
immediate: {'message': 'Workflow was started'}
respond-to-webhook: {'order_id': 'demo-2', 'caller': 'n8n', 'tier': 'gold'}
```

### A autenticação do Webhook node

Sem o header, ou com ele errado, o n8n recusa a chamada:

```bash
curl -s -X POST localhost:15678/webhook/order-sync -H 'content-type: application/json' \
  -d '{"order_id":"1"}' -w ' HTTP %{http_code}\n'
curl -s -X POST localhost:15678/webhook/order-sync -H 'X-Webhook-Token: x' \
  -H 'content-type: application/json' -d '{"order_id":"1"}' -w ' HTTP %{http_code}\n'
```

```text
Authorization data is wrong! HTTP 403
Authorization data is wrong! HTTP 403
```

### Resposta imediata ou "Respond to Webhook"

| Modo | Webhook node | Resposta medida |
| --- | --- | --- |
| Imediato | `responseMode: onReceived` | `200` `{"message":"Workflow was started"}` |
| Respond to Webhook | `responseMode: responseNode` | `200` com o JSON do workflow, ex. `{"order_id":"1","caller":"n8n","tier":"gold"}` |

Imediato é o que o outbox quer: a entrega termina quando o n8n aceita, e o
resultado volta pelo callback. Respond to Webhook prende a conexão até o
último nó — e o erro do workflow vira o erro da chamada. Com o serviço
ainda fora do ar, o `order-sync` respondeu:

```text
{"message":"Error in workflow"} HTTP 500
```

O `HTTPClient` refaz respostas 5xx (veja a [receita](http-client.md)), então
esse `500` pode repetir a chamada; um workflow que não é idempotente vai
rodar de novo.

### URL de teste e URL de produção

O Webhook node tem duas URLs: `/webhook/<path>` (produção, vale para o
workflow **publicado**) e `/webhook-test/<path>` (só funciona enquanto o
editor está escutando um evento de teste). Chamar a de teste sem o editor
escutando:

```text
{"code":404,"message":"The requested webhook \"order-created\" is not registered.","hint":"Click the 'Execute workflow' button on the canvas, then try again. (In test mode, the webhook only works for one call after you click this button)"} HTTP 404
```

Por isso o `call_webhook` tem o parâmetro `test` e o padrão é produção.

## 5. Entrega garantida: outbox → n8n, inbox ← n8n

Dispare um pedido. O serviço responde `201` e **não** espera o n8n:

```bash
curl -s -X POST localhost:18001/api/orders -H 'content-type: application/json' \
  -d '{"reference":"PED-N8N-1"}'
sleep 4
curl -s localhost:18001/api/orders/c492aece-3554-460b-945f-0b871237c6e7
```

```text
{"id":"c492aece-3554-460b-945f-0b871237c6e7","status":"created"}
{"id":"c492aece-3554-460b-945f-0b871237c6e7","status":"enriched"}
```

No log do serviço, o workflow chamou a rota-ferramenta e o callback, os dois
com o token do *Header Auth*:

```text
INFO:     172.17.0.3:57964 - "POST /api/tools/enrich-order HTTP/1.1" 200 OK
INFO:     172.17.0.3:57966 - "POST /api/callbacks/n8n HTTP/1.1" 200 OK
```

### O n8n fora do ar

Com o container congelado (`docker pause lab-n8n`), um novo pedido responde
`201` do mesmo jeito. Vinte e cinco segundos depois, a linha do outbox
continua `pending` e o pedido `created`; descongelado
(`docker unpause lab-n8n`), a linha vira `sent` e o pedido `enriched`:

```text
{"id":"5352a463-d1ab-422a-a3dd-646bf0ae1f6b","status":"created"}
('orders.created', 'pending', 0, None)          # linha do pedido novo, congelado, 25 s depois
{"id":"5352a463-d1ab-422a-a3dd-646bf0ae1f6b","status":"created"}
('orders.created', 'sent', 0)                   # depois do unpause
{"id":"5352a463-d1ab-422a-a3dd-646bf0ae1f6b","status":"enriched"}
```

!!! note "O que este teste mede e o que não mede"
    `docker pause` congela o processo; a conexão fica pendurada em vez de
    ser recusada, e aos 25 s o relay ainda não tinha registrado falha
    (`attempts` em `0`). Este teste mostra que o evento **espera, durável,
    na tabela** e sai quando o n8n volta. O caminho com `attempts` subindo,
    `last_error` e backoff exponencial é o das falhas rápidas, descrito na
    [receita do outbox](outbox.md#falhas-e-retry).

### O callback repetido

O inbox responde `200` e grava uma vez só. Duas entregas com o mesmo
`execution_id`:

```text
{"recorded":true}
{"recorded":false}
```

```text
('n8n', 'dup-1', 'received')     # uma linha só, antes do sweep processar
```

!!! danger "É at-least-once nos dois sentidos"
    O outbox pode publicar o mesmo evento duas vezes (worker morto entre o
    envio e o `commit`), então o **workflow** pode rodar duas vezes para o
    mesmo pedido. Faça-o idempotente por `body.order_id`. Do lado de cá, o
    inbox já deduplica o callback por `(provider, external_id)`.

## Derrubar o ambiente

```bash
docker rm -f lab-n8n
```

## Recap

- **Serviço → n8n:** `HTTPClient` com o header do *Header Auth* em
  `default_headers` + `POST /webhook/<path>`. Sem o header (ou errado):
  `403`.
- **Dois modos de resposta:** `onReceived` (`{"message":"Workflow was
  started"}`) para o outbox; *Respond to Webhook* para quando o chamador
  precisa do resultado — e então o erro do workflow é `500` na chamada.
- **Produção vs teste:** `/webhook/...` exige workflow publicado;
  `/webhook-test/...` dá `404` sem o editor escutando.
- **n8n → serviço:** HTTP Request node com credencial *Header Auth*
  (`X-Token`) contra uma rota com `make_token_dependency`.
- **Entrega garantida:** `save_with_outbox` + `OutboxRelay` para ir;
  `WebhookInbox.record` com o `$execution.id` para voltar.
- Nenhum módulo novo: tudo isto é `HTTPClient`, `make_token_dependency`,
  `OutboxRelay` e `WebhookInbox`.
