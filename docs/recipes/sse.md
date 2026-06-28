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

## Broadcast pra vários clientes

`EventStream` é uma conexão. Pra mandar o mesmo evento pra todos (ou pros
devices de um usuário), mantenha um registro de streams e publique em
todos — um "hub" simples:

```python
# src/services/sse_hub.py
from uuid import UUID

from tempest_fastapi_sdk import EventStream


class SSEHub:
    """Registro em memória de streams SSE abertos, por usuário."""

    def __init__(self) -> None:
        self._streams: dict[UUID, set[EventStream]] = {}

    def register(self, user_id: UUID) -> EventStream:
        """Abre um stream para um cliente e o registra."""
        stream = EventStream(heartbeat_seconds=15.0)
        self._streams.setdefault(user_id, set()).add(stream)
        return stream

    def unregister(self, user_id: UUID, stream: EventStream) -> None:
        """Remove um stream fechado do registro."""
        streams = self._streams.get(user_id)
        if streams:
            streams.discard(stream)
            if not streams:
                del self._streams[user_id]

    async def publish_to_user(self, user_id: UUID, data: object, *, event: str) -> int:
        """Publica um evento em todos os streams abertos de um usuário."""
        streams = self._streams.get(user_id, set())
        for stream in streams:
            await stream.publish(data, event=event)
        return len(streams)
```

```python
# src/api/routers/feed.py
@router.get("/feed")
async def feed(
    user_id: UUID = Depends(get_current_user_id),
    hub: SSEHub = Depends(get_sse_hub),       # singleton no app.state
) -> StreamingResponse:
    """Inscreve o cliente no feed do seu usuário."""
    stream = hub.register(user_id)

    async def lifecycle_aware() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream.stream():
                yield chunk
        finally:
            hub.unregister(user_id, stream)

    return sse_response(lifecycle_aware())


# De qualquer lugar (handler de fila, outro endpoint):
# await hub.publish_to_user(user_id, {"text": "Novo pedido"}, event="notice")
```

!!! danger "Hub em memória = um processo só"
    O `SSEHub` acima vive na memória de **um** worker. Com vários workers
    (Gunicorn/Uvicorn `--workers N`), um publish só alcança os clientes
    presos naquele processo. Pra multi-processo, ligue o hub a um
    Pub/Sub (Redis `PUBLISH`/`SUBSCRIBE`): cada worker assina o canal e
    repassa pros seus streams locais.

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
- Broadcast = registro de streams (hub); multi-worker exige Pub/Sub (Redis).
- `tempest-react-sdk` `createEventStream`/`useEventStream` consome com reconnect; `namedEvents` ↔ `publish(event=)`.
