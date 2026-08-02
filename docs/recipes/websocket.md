# WebSocket router

Desde v0.33.0 o SDK fornece `make_websocket_router` + `WebSocketHub` — abstração equivalente a SSE mas **bidirecional**, com bearer auth no handshake, heartbeat ping/pong automático e registro centralizado pra broadcast / per-user / por tópico.

```bash
uv add "tempest-fastapi-sdk[websocket]"
```

!!! danger "Sem o extra o handshake devolve 404 — e a sua suíte passa mesmo assim"
    O código do router é core (`starlette` / `fastapi`, que você já tem),
    mas o **protocolo** não: o `uvicorn` puro não fala WebSocket. Sem uma
    implementação instalada o handshake responde **404**, e o motivo real
    (`No supported WebSocket library detected`) só aparece no log do
    servidor — nunca na resposta.

    A armadilha: o `TestClient` do Starlette implementa WS por conta
    própria, então **a suíte inteira passa** enquanto o servidor real
    recusa toda conexão. O extra `[websocket]` traz o `websockets`, que o
    uvicorn detecta sozinho.

    O exemplo de **bearer auth** com `JWTUtils` pede também o `[auth]` —
    `uv add "tempest-fastapi-sdk[auth,websocket]"`.

## O que o router resolve

WebSocket bare do FastAPI te dá `await ws.receive_json()` / `await ws.send_json()` — só isso. Tudo o que vem depois é boilerplate que **todo** projeto reimplementa:

1. **Auth no handshake** — browser não pode setar header `Authorization` no construtor `new WebSocket(...)`. Sobram dois caminhos: query param (`?token=`) ou subprotocol (`Sec-WebSocket-Protocol: bearer,<jwt>`). O SDK aceita os dois.
2. **Heartbeat** — load balancers (Nginx, AWS ALB) fecham conexões "ociosas" depois de 60s. Sem ping/pong, o cliente vê a conexão "viva" enquanto o servidor já a perdeu.
3. **Registry compartilhado** — pra fazer `broadcast("orders", payload)` ou `send_to(user_id, payload)` de qualquer handler HTTP, você precisa de uma estrutura global indexada por user_id + tópicos.
4. **Cleanup determinístico** — quando o cliente cai (refresh, fechou aba, perdeu wifi), as estruturas precisam ser limpas senão vazam memória.

O `make_websocket_router` resolve os 4 itens; seu handler só vê a conexão pronta + o hub pra fan-out.

## Conteúdo da receita

1. **[Setup mínimo](#setup-minimo)** — wire de 3 objetos (`WebSocketHub`, `bearer_resolver`, `make_websocket_router`).
2. **[Bearer auth — query vs subprotocol](#bearer-auth)** — quando usar cada.
3. **[Cliente JavaScript / browser](#cliente-javascript)** — `new WebSocket(...)` com heartbeat + reconnect.
4. **[Broadcast / send_to / topics](#broadcast)** — fan-out via `WebSocketHub`.
5. **[Heartbeat e codes de fechamento](#heartbeat)** — códigos 1009/4401/4408/4429 e como o cliente reage.
6. **[Settings (`WebSocketSettings`)](#settings)** — flags + defaults.
7. **[Trade-offs e quando NÃO usar](#trade-offs)** — single-process, fan-out multi-replica, escolha SSE vs WS.

---

## Setup mínimo

Três objetos: o **hub** (estado em memória), o **resolver** (token → user UUID) e o **handler** (loop de mensagens).

```python
# src/api/app.py
from uuid import UUID

from fastapi import FastAPI, WebSocket

from tempest_fastapi_sdk import (
    JWTUtils,
    WSEnvelope,
    WebSocketConnection,
    WebSocketHub,
    WebSocketSettings,
    make_websocket_router,
)
from src.core.settings import settings

ws_settings = WebSocketSettings()
hub = WebSocketHub(max_per_user=ws_settings.WS_MAX_CONNECTIONS_PER_USER)
tokens = JWTUtils(secret=settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def bearer_resolver(token: str) -> UUID | None:
    """Decode JWT and return the subject (user id) — None on bad token."""
    try:
        payload = tokens.decode(token)
    except Exception:  # noqa: BLE001 — any decode failure = reject
        return None
    return UUID(payload["sub"])


async def handler(
    ws: WebSocket,
    connection: WebSocketConnection,
    hub: WebSocketHub,
) -> None:
    """Bidirectional loop — every connection runs this until disconnect."""
    while True:
        message = await ws.receive_json()
        envelope = WSEnvelope.model_validate(message)
        if envelope.type == "pong":
            continue          # heartbeat reply — nothing to do, just skip it
        if envelope.type == "subscribe":
            await hub.subscribe(connection.connection_id, envelope.data["topic"])
            continue
        if envelope.type == "chat.message":
            # Broadcast pra todo mundo subscrito em `chat:<room>`
            await hub.broadcast(
                WSEnvelope(
                    type="chat.message",
                    data={
                        "from": str(connection.user_id),
                        "text": envelope.data["text"],
                    },
                ),
                topic=envelope.data["room"],
            )


app = FastAPI()
app.include_router(
    make_websocket_router(
        handler,
        hub=hub,
        bearer_resolver=bearer_resolver,
        settings=ws_settings,
        path="/ws",
    )
)
```

Pronto. Agora `ws://localhost:8000/ws?token=<jwt>` aceita conexões; `hub.broadcast(...)` e `hub.send_to(...)` ficam disponíveis em **qualquer handler HTTP** do mesmo app pra empurrar eventos pros sockets.

---

## Bearer auth

O SDK aceita o token de dois lugares — em ordem de preferência:

| Mecanismo | Browser-friendly | Aparece em logs? | Quando usar |
|---|---|---|---|
| `Sec-WebSocket-Protocol: bearer,<jwt>` | **Sim** (via 2º arg do `new WebSocket(...)`) | Não (header) | **Preferido** — funciona no browser, esconde o token de logs de proxy. |
| `?token=<jwt>` query string | Sim (URL nativa) | Sim (request log, Referer, history) | Só quando precisa de fallback ou um cliente mais limitado. |

Quando ambos vêm, **subprotocol vence**.

!!! warning "Token na query vaza em logs"
    `?token=<jwt>` aparece nos access logs de proxy/Nginx, no header `Referer` e no histórico do browser — qualquer um deles pode reter o JWT em texto plano. Prefira sempre o subprotocol (`["bearer", jwt]`) quando o cliente for seu; use o query param só como fallback pra clientes que não conseguem setar subprotocol.

Resolver retornando `None` → o SDK fecha o socket com código `4401` antes do handler rodar.

---

## Cliente JavaScript

```javascript
// Preferido — subprotocol bearer
const ws = new WebSocket("wss://api.example.com/ws", ["bearer", jwtToken]);

ws.addEventListener("open", () => {
  ws.send(JSON.stringify({ type: "subscribe", data: { topic: "chat:lobby" } }));
});

ws.addEventListener("message", (event) => {
  const envelope = JSON.parse(event.data);

  // Heartbeat — obrigatório: sem o pong o servidor fecha com 4408
  if (envelope.type === "ping") {
    ws.send(JSON.stringify({ type: "pong", data: {} }));
    return;
  }

  // Sua app
  if (envelope.type === "chat.message") {
    console.log("got", envelope.data);
  }
});

// Reconnect com backoff exponencial
ws.addEventListener("close", (event) => {
  const code = event.code;
  if (code === 4401) {
    // token inválido/expirado → redirect pro login
    window.location.href = "/login";
    return;
  }
  setTimeout(() => connect(), Math.min(30_000, 1_000 * 2 ** attempts++));
});
```

---

## Broadcast

`WebSocketHub` expõe quatro patterns:

```python
import asyncio


async def main() -> None:
    """Run this example."""
    # 1. send_to — todos os sockets de um usuário (multi-tab)
    await hub.send_to(user_id, WSEnvelope(type="notification", data={"text": "..."}))

    # 2. send_many — payload DIFERENTE por usuário, em paralelo
    await hub.send_many(
        {
            player_a: WSEnvelope(type="duel.state", data={"hand": hand_a}),
            player_b: WSEnvelope(type="duel.state", data={"hand": hand_b}),
        }
    )

    # 3. broadcast com topic — só quem se inscreveu naquele tópico
    await hub.broadcast(
        WSEnvelope(type="order.paid", data={"id": str(order_id)}),
        topic=f"order:{order_id}",
    )

    # 4. broadcast sem topic — TODO mundo conectado (use raramente)
    await hub.broadcast(
        WSEnvelope(type="system.announcement", data={"text": "Servidor em manutenção"}),
    )


asyncio.run(main())
```

!!! tip "`send_many` existe por causa da informação oculta"
    Quando cada destinatário precisa ver um payload diferente — fog of
    war num jogo, feed personalizado, preço por contrato — o `broadcast`
    não serve e a alternativa era um `await hub.send_to(...)` por
    usuário, cada um esperando o socket anterior. O `send_many` despacha
    tudo com `asyncio.gather`: o custo é o socket mais lento, não a soma
    de todos.

Subscription lifecycle controlada pelo handler:

```python
import asyncio


async def main() -> None:
    """Run this example."""
    await hub.subscribe(connection.connection_id, "order:01HE...")
    # ... mais tarde
    await hub.unsubscribe(connection.connection_id, "order:01HE...")


asyncio.run(main())
```

Sockets mortos são detectados na hora do `send_to`/`broadcast` (a chamada `send_json` falha) — o hub remove automaticamente do registry.

---

## Heartbeat

A cada `WS_HEARTBEAT_SECONDS` (default 30s) o SDK envia:

```json
{"type": "ping", "data": {}, "request_id": null}
```

O cliente **deve** responder com `{"type": "pong", "data": {}}` — o pong é tráfego cliente → servidor que reseta o idle timer de load balancers e mantém a conexão saudável.

!!! warning "Responder `pong` é obrigatório desde a v0.197.0"
    O router agora mede o intervalo até o pong e **fecha com `4408`**
    quando ele passa de `WS_HEARTBEAT_TIMEOUT_SECONDS`. Antes disso um
    peer half-open — que nunca faz o `send` falhar — segurava o slot no
    hub para sempre, exatamente o que a documentação prometia evitar.

    Um cliente que não responde `pong` passa a ser desconectado uma vez
    por timeout. O `tempest-react-sdk` responde sozinho (`respondToPing`,
    ligado por default); qualquer outro cliente precisa ecoar
    `{"type": "pong", "data": {}}` ao receber o ping.

O pong é consumido pelo router: ele **não** chega no seu handler, porque responde ao ping que o próprio router mandou, não é dado de aplicação.

Frames maiores que `WS_MAX_MESSAGE_BYTES` também são recusados agora — o socket fecha com `1009` **antes** de o handler alocar o payload.

Códigos de fechamento que o router emite:

| Código | Quando |
|---|---|
| `1000` | Saída normal (handler retornou ou cliente desconectou limpo) |
| `1009` | Frame de entrada maior que `WS_MAX_MESSAGE_BYTES` |
| `4401` | Token inválido / expirado / faltando no handshake |
| `4408` | Nenhum `pong` dentro de `WS_HEARTBEAT_TIMEOUT_SECONDS` |
| `4429` | Limite `WS_MAX_CONNECTIONS_PER_USER` excedido — conexão **mais antiga** do user é evictada |

---

## Settings

Mixe `WebSocketSettings` na sua classe `Settings`:

```python
# src/core/settings.py
from tempest_fastapi_sdk import BaseAppSettings, WebSocketSettings


class Settings(WebSocketSettings, BaseAppSettings):
    pass
```

```bash
# .env
WS_HEARTBEAT_SECONDS=30                # default
WS_HEARTBEAT_TIMEOUT_SECONDS=60        # default — fecha com 4408 ao estourar
WS_MAX_CONNECTIONS_PER_USER=5          # default
WS_MAX_MESSAGE_BYTES=65536             # 64 KiB default — fecha com 1009 ao estourar
```

---

## Trade-offs

!!! warning "Broadcast é single-process — dev / single-replica only"
    `WebSocketHub` guarda o registry em memória do processo. `broadcast` / `send_to` só alcançam sockets conectados **nesta réplica** — num deploy multi-réplica cada processo tem seu próprio hub e os eventos não cruzam de um pro outro. Trate o fan-out cross-replica (sticky sessions ou pub/sub) como requisito **antes** de escalar horizontalmente.

**Single-process por design.** `WebSocketHub` guarda estado em memória do processo. Em deploy multi-réplica:

- **Opção 1 — Sticky sessions**: load balancer roteia o mesmo cliente sempre pra mesma réplica. Funciona, mas perde balanceamento.
- **Opção 2 — Fan-out via pub/sub** (futuro v0.34+): handler HTTP publica num Redis pub/sub / RabbitMQ topic, e cada réplica do hub re-emite pros seus sockets locais. Surface idêntica, plumbing transparente. **Não shipped ainda** — pra v0.33.0 use Opção 1 ou rode 1 réplica do serviço WS atrás de um balanceador HTTP separado.

**Quando preferir SSE em vez de WebSocket:**

- Só servidor → cliente (notificações, status de pedido, dashboards live).
- Cliente raramente envia (1 request/min).
- Quer reconnect automático "grátis" — `EventSource` reconecta sozinho com backoff; WebSocket exige código custom.
- Atrás de proxy/CDN que **não** suporta WebSocket bem (alguns ALBs / Cloudflare em planos baixos).

**Quando WebSocket é a escolha certa:**

- Bidirecional intenso (chat, colaboração simultânea, jogos, drawing apps).
- Latência ultra-baixa em ambas direções.
- Protocolo customizado por message-type que SSE não modela bem.
- Volume de mensagens cliente → servidor é alto.

## Próximos passos

- **[Auth flow »](auth-flow.md)** — o JWT que vai no `?token=` ou no subprotocol vem direto do `POST /auth/login` do `UserAuthService`.
- **[Tempo real (SSE) »](realtime.md)** — quando só servidor → cliente serve.
- **[Cache »](cache.md)** — Redis pub/sub futuro pra fan-out multi-réplica.
