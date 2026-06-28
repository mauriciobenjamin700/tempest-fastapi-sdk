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
`X-Accel-Buffering: no` pra desligar o buffer do nginx).

## Um endpoint SSE

Crie um `EventStream` por requisição, publique de um produtor, e ligue o
ciclo de vida do produtor à conexão do cliente — se o cliente cai, o
produtor para.

```python
# src/api/routers/events.py
import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import EventStream, sse_response

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

    async def lifecycle_aware() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream.stream():
                yield chunk
        finally:
            task.cancel()  # cliente desconectou -> não vaza o produtor

    return sse_response(lifecycle_aware())
```

!!! warning "Sempre amarre o produtor à conexão"
    Stream SSE é longo. Se o cliente desconecta no meio, você não quer o
    produtor rodando pra sempre. O `finally` do gerador externo roda
    quando a resposta fecha — cancele o produtor ali.

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

`heartbeat_seconds` emite um **comentário** SSE (`: keepalive`) quando o
stream fica ocioso, pra load-balancers não cortarem a conexão.
Comentários são **invisíveis** ao `EventSource` — não disparam nenhum
listener, só mantêm o socket vivo. `None` desliga o heartbeat.

## Broadcast pra vários clientes (`SSEBroker`)

`EventStream` é **uma** conexão. Pra mandar o mesmo evento pra todos os
clientes de um canal (ex.: os devices de um usuário, ou um tópico), o SDK
traz o `SSEBroker` — registro de streams por canal + fan-out. O canal é
uma string qualquer (id de usuário, slug de sala...).

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import SSEBroker

broker = SSEBroker()   # singleton — guarde em app.state e injete via Depends
```

```python
# src/api/routers/feed.py
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import sse_response

router = APIRouter()


@router.get("/feed")
async def feed(
    user_id: UUID = Depends(get_current_user_id),
    broker: SSEBroker = Depends(get_broker),
) -> StreamingResponse:
    """Inscreve o cliente no canal do seu usuário."""
    channel = str(user_id)
    stream = broker.register(channel)

    async def lifecycle_aware() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream.stream():
                yield chunk
        finally:
            broker.unregister(channel, stream)   # cliente saiu

    return sse_response(lifecycle_aware())


# De qualquer lugar (handler de fila, outro endpoint):
# await broker.publish(str(user_id), {"text": "Novo pedido"}, event="notice")
```

### Multi-worker: bridge Redis (pronto, sem código extra)

O `SSEBroker` em memória vive em **um** worker — com `--workers N`, um
`publish` só alcança os clientes presos naquele processo. Passe um client
Redis e o **mesmo `broker`** passa a publicar via Redis `PUBLISH`; uma
task de fundo (`run()`) faz `PSUBSCRIBE` e repassa pros streams locais de
**cada** worker. Mesmo call site, agora horizontal:

```python
# src/api/app.py
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from tempest_fastapi_sdk import SSEBroker

redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
broker = SSEBroker(redis=redis, channel_prefix="sse")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(broker.run())   # assina o Redis e faz o fan-out
    try:
        yield
    finally:
        await broker.aclose()
        task.cancel()


app = FastAPI(lifespan=lifespan)
# broker.publish(...) em qualquer worker -> chega em TODOS os workers
```

!!! tip "Comece simples, escale depois"
    Sem Redis, `SSEBroker()` já resolve um processo. Quando precisar de
    múltiplos workers/hosts, só injete o client Redis e suba o `run()` no
    lifespan — nenhum endpoint muda. O `publish` se torna cross-process de
    graça.

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

- `EventStream` (1 por conexão) + `sse_response` — endpoint SSE com headers prontos.
- Amarre o produtor ao ciclo de vida da conexão (`finally` → cancela/desregistra).
- `publish(data, event=, id=, retry=)` cobre os 4 campos do spec; `data` não-string vira JSON.
- Heartbeat é comentário (invisível ao EventSource); `None` desliga.
- Broadcast = `SSEBroker` (registro de streams por canal); multi-worker = passe um client Redis + suba `broker.run()` no lifespan (mesmo call site).
- `tempest-react-sdk` `createEventStream`/`useEventStream` consome com reconnect; `namedEvents` ↔ `publish(event=)`.
