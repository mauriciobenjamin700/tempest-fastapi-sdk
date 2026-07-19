# Server-Sent Events (SSE)

SSE empurra dados do servidor pro navegador por **uma conexão HTTP de
longa duração**, sem polling. É o caminho mais simples pra "tempo real
unidirecional": feed de notificações, barra de progresso, ticker de
preço, logs ao vivo.

!!! info "SSE vs WebSocket vs Web Push"
    - **SSE** — servidor → cliente, só texto, reconecta sozinho, roda
      sobre HTTP comum. Use quando o cliente só **recebe**.
    - **WebSocket** — bidirecional, binário, mais complexo. Use quando o
      cliente também **envia** com frequência. Veja [WebSocket](websocket.md).
    - **Web Push** — chega com a **página fechada** (Service Worker). Veja
      [Web Push](webpush.md).

O SDK traz três peças: `EventStream` (fila async em memória que alimenta
uma conexão), `ServerSentEvent` (codifica um frame no formato do spec) e
`sse_response` (embrulha o stream num `StreamingResponse` com os headers
certos — `Cache-Control: no-cache`, `Connection: keep-alive`,
`X-Accel-Buffering: no` pra desligar o buffer do nginx). No dia a dia você
chama os atalhos `EventStream.response(...)` / `SSEBroker.response(channel)`,
que já embrulham com o `sse_response` por baixo; use o `sse_response` cru só
quando quiser controlar o gerador na mão.

!!! info "Precisa instalar algo? SSE é nativo"
    `EventStream`, `ServerSentEvent`, `sse_response` e o `SSEBroker` em memória
    fazem parte do **core** — não têm extra próprio, já vêm com
    `tempest-fastapi-sdk` (dependem só de `starlette`, que o FastAPI já traz).
    Não existe extra `[sse]`. Só o **bridge Redis** (multi-worker) pede o extra
    `[cache]` — `uv add "tempest-fastapi-sdk[cache]"`, que traz o `redis`. A
    auth por cookie/query usa `JWTUtils`, do extra `[auth]`.

!!! tip "Novidades da v0.91"
    - **Backpressure** — a fila do `EventStream` agora é **limitada**
      (`max_queue`, default `1000`): cliente lento não faz a memória
      crescer sem limite. A política `overflow` decide o que cai. Veja
      [Backpressure](#backpressure-fila-limitada).
    - **Lifecycle sem boilerplate** — `sse_response(..., on_disconnect=)`,
      `EventStream.response(...)` e `SSEBroker.response(channel)` fecham o
      produtor / desregistram o canal sozinhos quando o cliente cai.
    - **Auth por query string** pra clientes cookieless (`EventSource`).
      Veja [Autenticação](#autenticacao-cookie-ou-query-string).

## Um endpoint SSE

Crie um `EventStream` por requisição, publique de um produtor, e ligue o
ciclo de vida do produtor à conexão do cliente — se o cliente cai, o
produtor para.

```python
# src/api/routers/events.py
import asyncio

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import EventStream

router = APIRouter()


@router.get("/events")
async def events() -> StreamingResponse:
    """Emite 3 frames SSE e fecha o stream."""
    stream = EventStream(heartbeat_seconds=15.0)

    async def producer() -> None:
        try:
            for n in range(1, 4):
                await stream.publish({"n": n}, event="counter", id=str(n))
                await asyncio.sleep(1)
        finally:
            await stream.close()

    task = asyncio.create_task(producer())

    # on_disconnect roda quando o cliente cai OU o stream termina:
    # é onde você cancela o produtor pra não vazar.
    return stream.response(on_disconnect=task.cancel)
```

!!! warning "Sempre amarre o produtor à conexão"
    Stream SSE é longo. Se o cliente desconecta no meio, você não quer o
    produtor rodando pra sempre. Passe `on_disconnect=` pro
    `EventStream.response` (ou pro `sse_response`) — ele roda no `finally`
    do gerador da resposta, o único ponto que dispara na desconexão.

Suba a API e veja os frames crus no terminal — `curl -N` desliga o buffer
e imprime cada frame assim que chega:

```bash
curl -N http://127.0.0.1:8000/events
```

```text
event: counter
id: 1
data: {"n": 1}

event: counter
id: 2
data: {"n": 2}

event: counter
id: 3
data: {"n": 3}
```

Repare no formato do spec: cada frame é um bloco de linhas `campo: valor`
(`event`, `id`, depois `data`), e a **linha em branco** (`\n\n`) separa um
frame do próximo. Como você passou um dict, o `data` saiu JSON-serializado
sozinho.

??? note "Antes da v0.91: `try/finally` na mão"
    Até a v0.90 você embrulhava o `stream()` num gerador externo só pra
    ter o `finally`. `on_disconnect=` substitui esse boilerplate:

    ```python
    from collections.abc import AsyncIterator
    from tempest_fastapi_sdk import sse_response

    async def lifecycle_aware() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream.stream():
                yield chunk
        finally:
            task.cancel()

    return sse_response(lifecycle_aware())
    ```

## Anatomia de um evento

`publish()` aceita os quatro campos do spec:

```python
await stream.publish(
    {"orderId": "abc", "status": "paid"},  # data: vira JSON automático
    event="order_update",                  # event: nome do listener no front
    id="42",                               # id: vira Last-Event-ID (resume)
    retry=3000,                            # retry: dica de reconexão (ms)
)
```

| Campo | Pro quê serve |
| --- | --- |
| `data` | Payload. String/bytes vão crus; qualquer objeto vira JSON. |
| `event` | Nome do evento — o front escuta com `addEventListener(name)`. Sem isso, cai no `"message"`. |
| `id` | Vira `Last-Event-ID`; o navegador reenvia no reconnect pra você retomar. |
| `retry` | Atraso de reconexão sugerido (ms). |

O tipo do `data` é o alias exportado **`SSEData`**
(`str | bytes | Mapping | Sequence | int | float | bool | None`) — as formas de
valor JSON, mais `str`/`bytes` crus. Pra mandar um objeto que só serializa via
`str()` (ex.: um `UUID` solto), embrulhe em `str(...)` ou num dict antes.

`heartbeat_seconds` emite um **comentário** SSE (`: keepalive`) quando o
stream fica ocioso, pra load-balancers não cortarem a conexão.
Comentários são **invisíveis** ao `EventSource` — não disparam nenhum
listener, só mantêm o socket vivo. `None` desliga o heartbeat.

## Backpressure (fila limitada)

Se um cliente **para de ler** (aba em segundo plano, rede ruim) mas o
produtor continua publicando, a fila do `EventStream` cresceria pra
sempre — vazamento de memória clássico. Por isso a fila é **limitada**:
`max_queue` (default `1000`) e uma política `overflow` que decide o que
fazer quando enche.

```python
from tempest_fastapi_sdk import EventStream

# Ticker ao vivo: frame velho não vale nada -> descarta o mais antigo.
stream = EventStream(max_queue=500, overflow="drop_oldest")
```

| `overflow` | Quando a fila enche | Use quando |
| --- | --- | --- |
| `"drop_oldest"` (default) | Evicta o evento mais **antigo** | Dados vivos: ticker, progresso, telemetria — só o estado recente importa. |
| `"drop_newest"` | Descarta o evento que **chegou** | O começo do fluxo importa mais que o fim. |
| `"block"` | Segura o `publish()` até abrir vaga | Produtor dedicado a **uma** conexão e perder evento é inaceitável. |

!!! danger "`block` pode travar um produtor compartilhado"
    Com `overflow="block"`, um cliente lento **segura** o `publish()`. Se
    o mesmo produtor alimenta vários clientes (fan-out), um cliente ruim
    trava todos. Só use `block` quando o produtor serve **uma** conexão.

O sentinela de `close()` **nunca** é descartado nem bloqueado — o stream
sempre encerra. `stream.dropped_events` conta quantos eventos caíram por
overflow, pra você jogar em métrica/log:

```python
if stream.dropped_events:
    logger.warning("SSE lento: %d eventos descartados", stream.dropped_events)
```

!!! tip "Voltar ao comportamento antigo"
    `max_queue=0` desliga o limite (fila ilimitada, igual pré-0.91). Só
    faça isso se você tiver certeza de que o produtor para junto com a
    conexão.

## Broadcast pra vários clientes (`SSEBroker`)

`EventStream` é **uma** conexão. Pra mandar o mesmo evento pra todos os
clientes de um canal (ex.: os devices de um usuário, ou um tópico), o SDK
traz o `SSEBroker` — registro de streams por canal + fan-out. O canal é
uma string qualquer (id de usuário, slug de sala...).

São **três passos**: criar o broker uma vez, guardar essa instância no app
(pra todo mundo usar a mesma), e injetar nos endpoints.

#### Passo 1 — criar o broker e a fiação

O `SSEBroker()` é um **singleton do processo**: todos os canais e streams
abertos vivem dentro dele. Por isso precisa ser **um só**, compartilhado pela
app inteira — se cada requisição criasse o seu, um `publish` num broker não
alcançaria os streams presos em outro.

```python
# src/api/dependencies/resources.py
from fastapi import FastAPI, Request

from tempest_fastapi_sdk import SSEBroker

broker = SSEBroker()


def register_broker(app: FastAPI) -> None:
    """Store the singleton broker on app.state (call it in create_app)."""
    app.state.broker = broker


def get_broker(request: Request) -> SSEBroker:
    """Return the shared broker from app.state for use in Depends()."""
    return request.app.state.broker
```

O que cada parte faz:

- `broker = SSEBroker()` — cria o broker no **import** do módulo. Como o módulo
  é importado uma vez, esse é o mesmo objeto pra qualquer um que o use.
- `register_broker(app)` — pendura o broker em `app.state.broker`. Você chama
  isso **uma vez** ao montar a app (dentro do `create_app` ou do lifespan):
  `register_broker(app)`.
- `get_broker(request)` — devolve `request.app.state.broker`. É o que os
  endpoints recebem via `Depends(get_broker)`, garantindo que todos falam com
  a **mesma** instância.

#### Passo 2 — o endpoint de inscrição

O cliente abre `GET /feed`; o endpoint o inscreve no canal do próprio usuário
e devolve o stream. Uma linha resolve tudo: `broker.response(canal)`.

```python
# src/api/routers/feed.py
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import SSEBroker

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.resources import get_broker

router = APIRouter()


@router.get("/feed")
async def feed(
    user_id: UUID = Depends(get_current_user_id),
    broker: SSEBroker = Depends(get_broker),
) -> StreamingResponse:
    """Subscribe the caller to their own user channel and stream it."""
    return broker.response(str(user_id))
```

Passo a passo do que acontece a cada `GET /feed`:

1. `Depends(get_current_user_id)` resolve **quem** é o cliente. O id dele vira o
   nome do canal — cada usuário tem o seu, isolado dos outros.
2. `Depends(get_broker)` entrega o broker compartilhado (o do Passo 1).
3. `broker.response(str(user_id))` faz **três coisas numa chamada só**:
     - **register** — cria um `EventStream` novo e o inscreve no canal `user_id`;
     - **stream** — devolve um `StreamingResponse` com os headers de SSE já
       prontos (o cliente começa a receber);
     - **unregister** — liga um `on_disconnect` que remove esse stream do canal
       quando o cliente cai.

!!! tip "Por que `broker.response()` não vaza stream"
    Os três passos (register → stream → unregister) ficam amarrados numa
    chamada. O `unregister` roda no `finally` do gerador da resposta — o único
    ponto que dispara na desconexão — então não tem `try/finally` pra você
    esquecer: cada cliente que sai limpa o próprio registro. E
    `SSEBroker(max_queue=..., overflow=...)` aplica a mesma política de
    backpressure (veja acima) a todo stream que o broker abre.

### Disparando do domínio (controller)

O Passo 2 mostrou o lado da **inscrição** (o cliente entra num canal). Falta o
lado da **publicação** — o *broadcast* propriamente dito.

**O que "broadcast" quer dizer aqui:** o broker guarda, por canal, a lista de
streams inscritos. Quando você chama `broker.publish("<canal>", ...)`, ele
percorre **todos** os streams daquele canal e entrega o mesmo evento a cada um.
Um `publish` → N clientes. Se o canal é o id de um usuário e ele está com duas
abas abertas, as duas recebem; se não há ninguém inscrito, o `publish` não faz
nada (não dá erro).

Quem dispara o `publish` é o **controller**, quando um evento de negócio
acontece (pedido criado, pagamento confirmado, mensagem nova). Ele orquestra o
service (regra de negócio) e o broker (notificação ao vivo), e usa como canal o
id do usuário que deve ser avisado.

```python
# src/controllers/order.py
from tempest_fastapi_sdk import SSEBroker

from src.schemas import OrderCreateSchema, OrderResponseSchema
from src.services import OrderService


class OrderController:
    """Orchestrates order creation and the live seller notification."""

    def __init__(self, order_service: OrderService, broker: SSEBroker) -> None:
        """Wire the order service and the SSE broker.

        Args:
            order_service (OrderService): Order business logic.
            broker (SSEBroker): Fan-out broker for live notifications.
        """
        self.order_service = order_service
        self.broker = broker

    async def create_order(self, data: OrderCreateSchema) -> OrderResponseSchema:
        """Create an order and notify the seller in real time.

        Args:
            data (OrderCreateSchema): The order creation payload.

        Returns:
            The created order.
        """
        order = await self.order_service.create(data)
        await self.broker.publish(
            str(order.seller_id),   # canal = id de quem recebe a notificação
            {"order_id": str(order.id), "total": str(order.total)},
            event="order_created",
            id=str(order.id),
        )
        return order
```

O coração é a chamada `broker.publish(...)`, campo por campo:

- **1º argumento (`str(order.seller_id)`)** — o **canal**. Manda o evento pra
  todos os streams inscritos nesse id (aqui, os devices do vendedor). É por isso
  que o endpoint de inscrição usa o id do usuário como canal: os dois lados
  precisam combinar na mesma string.
- **2º argumento (o dict)** — o **payload** (`data` do SSE). Objeto não-string
  vira JSON automático (viu em [Anatomia](#anatomia-de-um-evento)).
- **`event="order_created"`** — o **nome** do evento; o front escuta com
  `addEventListener("order_created", ...)`.
- **`id=str(order.id)`** — vira `Last-Event-ID`, pro cliente retomar do ponto
  certo se reconectar.

Repare que o controller **não** toca em `EventStream` nem em resposta HTTP: ele
só entrega o evento ao broker, que cuida do fan-out. Publicar é fire-and-forget.

O provider monta o controller com o service e o broker injetados — o mesmo
`get_broker` do Passo 1:

```python
# src/api/dependencies/controllers.py
from fastapi import Depends

from tempest_fastapi_sdk import SSEBroker

from src.api.dependencies.resources import get_broker
from src.api.dependencies.services import get_order_service
from src.controllers import OrderController
from src.services import OrderService


def get_order_controller(
    order_service: OrderService = Depends(get_order_service),
    broker: SSEBroker = Depends(get_broker),
) -> OrderController:
    """Build an OrderController with its service and the SSE broker."""
    return OrderController(order_service, broker)
```

O router só recebe o controller por `Depends` e delega — nada de regra de
negócio nem `publish` solto na rota:

```python
# src/api/routers/orders.py
from fastapi import APIRouter, Depends

from src.api.dependencies.controllers import get_order_controller
from src.controllers import OrderController
from src.schemas import OrderCreateSchema, OrderResponseSchema

router = APIRouter()


@router.post("/orders")
async def create_order(
    data: OrderCreateSchema,
    controller: OrderController = Depends(get_order_controller),
) -> OrderResponseSchema:
    """Create an order; the seller gets a live SSE notification."""
    return await controller.create_order(data)
```

O comprador que estiver com o `GET /feed` aberto recebe na hora:

```text
event: order_created
id: 9f3a...
data: {"order_id": "9f3a...", "total": "149.90"}
```

!!! tip "Publicar de fora do request (fila, task, webhook)"
    `broker.publish` é só uma coroutine — chame de qualquer lugar que tenha o
    broker: um consumer FastStream, uma task TaskIQ, um webhook. Ele só alcança
    quem está **conectado no momento** (o registro do canal some na desconexão);
    pra notificação **durável**, persista no banco e trate o SSE como a camada
    ao vivo por cima. Em multi-worker (Redis), o `publish` chega ao worker onde
    o cliente está preso — veja abaixo.

### Multi-worker: bridge Redis (pronto, sem código extra)

O `SSEBroker` em memória vive em **um** worker — com `--workers N`, um
`publish` só alcança os clientes presos naquele processo. Dê um client Redis ao
broker e o **mesmo `broker`** passa a publicar via Redis `PUBLISH`; uma task de
fundo (`run()`) faz `PSUBSCRIBE` e repassa pros streams locais de **cada**
worker. Mesmo call site, agora horizontal.

Use o **`AsyncRedisManager`** do SDK (extra `[cache]`) pra abrir a conexão — é o
mesmo client gerenciado (connect/disconnect/health-check) que cache, sessões e
feature flags usam; nada de `redis.asyncio` cru:

```python
# src/api/app.py
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import SSEBroker
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings

cache = AsyncRedisManager(**settings.redis_kwargs())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect Redis, wire the cross-worker broker, run its fan-out loop."""
    await cache.connect()
    broker = SSEBroker(redis=cache.client, channel_prefix="sse")
    app.state.broker = broker
    task = asyncio.create_task(broker.run())
    try:
        yield
    finally:
        task.cancel()
        await broker.aclose()
        await cache.disconnect()


app = FastAPI(lifespan=lifespan)
# broker.publish(...) em qualquer worker -> chega em TODOS os workers
```

O que cada parte faz:

- `AsyncRedisManager(**settings.redis_kwargs())` — o client Redis gerenciado do
  SDK. O `redis_kwargs()` vem do mixin `RedisSettings` (URL + `decode_responses`).
- `await cache.connect()` **primeiro** — antes disso, `cache.client` levanta
  `RuntimeError`. Por isso o broker é montado **dentro do lifespan**, não no
  import do módulo.
- `SSEBroker(redis=cache.client, ...)` — `cache.client` é o `redis.asyncio.Redis`
  cru por baixo; o broker usa ele pra `PUBLISH`/`PSUBSCRIBE`.
- `app.state.broker = broker` — mesma fiação do **Passo 1**, então o `get_broker`
  dos endpoints continua idêntico.
- `asyncio.create_task(broker.run())` — task de fundo que assina o Redis e
  repassa cada evento pros streams locais do worker. No teardown: cancela a
  task, fecha o broker, desconecta o Redis.

!!! tip "Comece simples, escale depois"
    Sem Redis, `SSEBroker()` já resolve um processo. Quando precisar de
    múltiplos workers/hosts, só passe o `cache.client` do `AsyncRedisManager` e
    suba o `run()` no lifespan — nenhum endpoint muda, o `publish` vira
    cross-process de graça. O `AsyncRedisManager` vem do extra `[cache]`
    (`uv add "tempest-fastapi-sdk[cache]"`).

### Multi-worker sem Redis: bridge via RabbitMQ

O bridge embutido (`SSEBroker.run()`) é **Redis-only** — o param `redis=` não
aceita outro transporte. Se você **já roda RabbitMQ** e não quer Redis só pra
isso, dá pra montar o fan-out entre workers à mão: cada worker mantém um
`SSEBroker()` **em memória** (sem `redis=`), e um subscriber RabbitMQ em cada
worker repassa o evento pro `broker.publish` **local**.

A peça que faz o broadcast funcionar é o **exchange fanout**: toda mensagem
publicada nele é copiada pra **todas** as filas ligadas. Cada worker declara uma
fila **exclusiva** (some quando o worker cai), então todos recebem cada evento —
diferente do default do RabbitMQ (work-queue), em que só **um** consumidor
pegaria a mensagem.

```python
# src/api/app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

from tempest_fastapi_sdk import SSEBroker
from tempest_fastapi_sdk.queue import MessageBroker

from src.core.settings import settings

broker = SSEBroker()                                 # em memória, um por worker
mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)   # extra [queue]

sse_exchange = RabbitExchange("sse.fanout", type=ExchangeType.FANOUT)
worker_queue = RabbitQueue("", exclusive=True, auto_delete=True)


@mq.broker.subscriber(worker_queue, sse_exchange)    # mq.broker = RabbitBroker do FastStream
async def _fan(evt: dict) -> None:
    """Relay one fanned-out event to this worker's local SSE streams."""
    await broker.publish(evt["channel"], evt.get("data", ""), event=evt.get("event"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await mq.connect()          # abre a conexão e sobe o subscriber
    app.state.broker = broker   # o mesmo get_broker dos endpoints resolve isso
    try:
        yield
    finally:
        await mq.disconnect()


app = FastAPI(lifespan=lifespan)
```

O que cada parte faz:

- `SSEBroker()` sem `redis=` — fan-out **local** ao worker (só os streams presos nele).
- `RabbitExchange(..., type=FANOUT)` + `RabbitQueue("", exclusive=True, auto_delete=True)`
  — o exchange copia pra toda fila ligada; cada worker declara a sua, então
  todos recebem cada evento.
- `@mq.broker.subscriber(worker_queue, sse_exchange)` — o `mq.broker` é o
  `RabbitBroker` do FastStream (escape hatch). A fachada `MessageBroker.on(...)`
  não expõe fanout, por isso descemos pro broker cru aqui.
- `await mq.connect()` no lifespan sobe o consumidor; `mq.disconnect()` fecha.

O domínio publica **no exchange** (não no `broker.publish` direto) — aí chega em
todos os workers:

```python
# de qualquer worker/handler:
await mq.broker.publish(
    {"channel": str(user_id), "event": "order_created", "data": {"order_id": str(order.id)}},
    exchange=sse_exchange,
)
```

!!! tip "Redis continua o caminho mais simples"
    O RabbitMQ precisa desse exchange fanout + fila exclusiva por worker pra
    replicar o que o Redis pub/sub (`SSEBroker.run()`) faz numa linha. Prefira
    RabbitMQ quando ele **já é** sua infra; senão, o Redis (`[cache]`) é menos
    peça móvel.

## Autenticação (cookie ou query string)

Aqui mora a pegadinha do SSE: o `EventSource` nativo do navegador **não
deixa** você mandar header. Nada de `Authorization: Bearer` no handshake.
Então existem dois caminhos pra autenticar o stream.

### Caminho preferido: cookie de sessão

Se o front está **na mesma origem** da API, use um cookie `HttpOnly`. O
navegador o manda sozinho quando você abre com `withCredentials`:

```javascript
const es = new EventSource("/api/feed", { withCredentials: true });
```

No backend, o SDK já lê o token do cookie — é o mesmo seam do
`make_auth_router` (modo de entrega por cookie):

```python
# src/api/dependencies/auth.py
from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency

tokens = JWTUtils(secret=settings.JWT_SECRET)

current_user = make_jwt_user_dependency(
    tokens,
    load_user,
    cookie_name="access_token",   # <- EventSource + withCredentials
)
```

!!! check "Por que cookie é melhor"
    O token some da URL: não vaza em log de acesso, histórico do
    navegador nem header `Referer`. `HttpOnly` ainda tira o token do
    alcance de JavaScript (defesa contra XSS). Prefira esse caminho
    **sempre** que a origem for compartilhada.

### Alternativa cookieless: token na query string

Sem cookie de sessão (front em **outra origem**, app mobile abrindo um
`EventSource` cru, ambiente onde `withCredentials` não rola), passe o
**access token** na query string. A partir da v0.91 o dependency aceita
isso via `query_param`:

```python
# src/api/dependencies/auth.py
from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency

tokens = JWTUtils(secret=settings.JWT_SECRET)

# Ordem de busca: header -> cookie -> query string.
current_user = make_jwt_user_dependency(
    tokens,
    load_user,
    query_param="access_token",   # <- ?access_token=<jwt>
)
```

```python
# src/api/routers/feed.py
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import SSEBroker

router = APIRouter()


@router.get("/feed")
async def feed(
    user: User = Depends(current_user),      # resolve o JWT da query string
    broker: SSEBroker = Depends(get_broker),
) -> StreamingResponse:
    """Stream autenticado sem cookie — token vem na URL."""
    return broker.response(str(user.id))
```

No front:

```javascript
// O access token curto entra na URL — nunca o refresh token.
const es = new EventSource(`/api/feed?access_token=${accessToken}`);
```

!!! danger "Query string vaza — trate o token como descartável"
    Um token na URL aparece em **log de acesso**, **histórico** e no
    header **`Referer`**. Regras inegociáveis:

    - Só **access token de vida curta** (minutos). **Nunca** o refresh
      token.
    - Sempre sobre **TLS** (HTTPS).
    - Remova o valor do formato de log do seu proxy/servidor.
    - Renove via um endpoint normal (com header/cookie), não pela query.

!!! info "`query_param` também existe no dependency de baixo nível"
    `make_bearer_token_dependency(tokens, query_param="access_token")`
    devolve só as claims decodificadas — use quando você monta o
    `get_current_user` na mão. Mesma ordem header → cookie → query.

## Alinhado com o tempest-react-sdk

O `createEventStream` / `useEventStream` do
[`tempest-react-sdk`](https://github.com/mauriciobenjamin700/tempest-react-sdk)
consome esses endpoints com reconnect (backoff exponencial) embutido:

```typescript
import { createEventStream } from "@mauriciobenjamin700/tempest-react-sdk";

const stream = createEventStream<{ text: string }>("/feed", {
    withCredentials: true,        // manda cookie de auth no handshake
    namedEvents: ["notice"],      // <- bate com publish(event="notice")
    onMessage: (m) => console.log(m.event, m.data),  // data já vem JSON-parseado
});
// stream.close() pra encerrar; stream.reconnect() pra forçar reconexão
```

!!! tip "Heartbeat: comentário vs evento `ping`"
    O heartbeat do `EventStream` é um **comentário** — o `EventSource`
    ignora, então o react-sdk nem precisa de `heartbeatEvents`. Se você
    preferir um heartbeat **nomeado** visível, publique
    `await stream.publish("", event="ping")` e configure
    `heartbeatEvents: ["ping"]` no front (default dele).

Pontos de alinhamento:

- `publish(event="x")` ↔ `namedEvents: ["x"]` + `onMessage`.
- `data` não-string vira JSON ↔ o parser default do react decodifica JSON.
- `id=` ↔ `Last-Event-ID` reenviado no reconnect (retome de onde parou).
- Auth por cookie ↔ `withCredentials: true`.

## Recap

- `EventStream` (1 por conexão) + `.response()` — endpoint SSE com headers prontos (`sse_response` é a versão low-level por baixo).
- Amarre o produtor à conexão com `on_disconnect=` (em `EventStream.response`, `sse_response` ou `broker.response`) — sem `try/finally` na mão.
- Fila **limitada** (`max_queue`, default `1000`) + `overflow` (`drop_oldest`/`drop_newest`/`block`) evita vazamento por cliente lento; `dropped_events` conta o descarte.
- `publish(data, event=, id=, retry=)` cobre os 4 campos do spec; `data` não-string vira JSON.
- Heartbeat é comentário (invisível ao EventSource); `None` desliga.
- Broadcast = `SSEBroker`; `broker.response(channel)` faz register + response + unregister; publique de controllers/tasks/filas com `broker.publish(channel, ...)`; multi-worker = passe um client Redis + suba `broker.run()` no lifespan.
- Auth: **cookie** (`cookie_name` + `withCredentials`) na mesma origem; **query string** (`query_param`, só access token curto sobre TLS) pra clientes cookieless.
- `tempest-react-sdk` `createEventStream`/`useEventStream` consome com reconnect; `namedEvents` ↔ `publish(event=)`.
