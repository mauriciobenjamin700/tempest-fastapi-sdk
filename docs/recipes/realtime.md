# Tempo real

Empurre dados para os clientes sem que o cliente fique fazendo polling. SSE para broadcasts servidor→navegador com a página aberta; para notificações que chegam mesmo com a página fechada, veja a receita dedicada de [Web Push](webpush.md).

## Server-Sent Events (SSE)


`EventStream` é uma fila async em memória que alimenta uma conexão HTTP SSE. `ServerSentEvent` codifica um frame; `sse_response` embrulha o stream de bytes em um `StreamingResponse` do Starlette com headers amigáveis ao SSE.

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
    """Emit three SSE frames then close the stream."""
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
            # Client closed the connection (or producer finished + close()
            # was called) — cancel the producer so it doesn't leak.
            task.cancel()

    return sse_response(lifecycle_aware())
```

!!! note "Pattern: link producer to client connection lifecycle"
    SSE streams are long-lived. If the client disconnects mid-stream, you don't want the producer running forever. Wrapping `stream.stream()` in an outer async generator gives a `finally` block that runs when the underlying response closes — cancel the producer there.

No navegador:

```javascript
const es = new EventSource("/events");
es.addEventListener("counter", (e) => console.log("got", JSON.parse(e.data)));
```

`heartbeat_seconds` emite um comentário SSE `: keepalive` quando ocioso, para que load-balancers não fechem conexões de longa duração. `ServerSentEvent.data` aceita strings, bytes ou qualquer objeto Python serializável em JSON — não-strings são codificados em JSON automaticamente. Passe `retry=` para sugerir ao navegador o atraso de reconexão (em milissegundos).


## Notificações Web Push

Web Push (notificações que chegam mesmo com a página fechada) tem
receita própria — veja **[Web Push »](webpush.md)**.
