---
description: "Índice dos módulos: fila e tarefas, WebSocket e salas, SSE, Web Push e push unificado, chat, comentários, event sourcing, feature flags e MCP."
---

# Referência: Tempo real e mensageria

Fila e tarefas, WebSocket e salas, SSE, Web Push e push unificado, chat, comentários, event sourcing, feature flags e MCP.

[« Índice da referência](../reference.md)

---

## Módulos

Uma página por módulo; cada uma renderiza a API pública dele a partir das docstrings.

| Módulo | O que cobre |
| --- | --- |
| [`chat`](realtime-messaging/chat.md) | Blocos de mensageria: conversas, mensagens, mídia e confirmações de leitura. |
| [`eventsourcing`](realtime-messaging/eventsourcing.md) | Event sourcing: o protocolo de event store com backends SQL e KurrentDB. |
| [`flags`](realtime-messaging/flags.md) | Feature flags: o serviço, os backends (memória, env, Redis, composto) e a dependência do FastAPI. |
| [`mcp`](realtime-messaging/mcp.md) | Model Context Protocol nas duas direções, sobre o FastMCP. |
| [`push`](realtime-messaging/push.md) | Push unificado: uma API para navegador e celular, com o model de token de dispositivo. |
| [`queue`](realtime-messaging/queue.md) | Fila de mensagens sobre o FastStream: publisher, consumer, topologia, retry, dedup e tracing. |
| [`reviews`](realtime-messaging/reviews.md) | Comentários e avaliações de 0 a 5 estrelas sobre qualquer alvo. |
| [`rooms`](realtime-messaging/rooms.md) | Salas multiplayer online: um servidor de relay para jogos, sobre WebSocket ou TCP. |
| [`sse`](realtime-messaging/sse.md) | Server-Sent Events sobre o streaming do Starlette. |
| [`tasks`](realtime-messaging/tasks.md) | Tarefas em background sobre o TaskIQ: fila, agendador com lock e jobs persistidos. |
| [`webpush`](realtime-messaging/webpush.md) | Web Push (VAPID): envio e schemas de inscrição. |
| [`websockets`](realtime-messaging/websockets.md) | WebSocket: router autenticado, hub de conexões e o envelope de mensagem. |
