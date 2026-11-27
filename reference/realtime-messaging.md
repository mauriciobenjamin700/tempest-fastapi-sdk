# Referência: Tempo real e mensageria

Fila e tarefas, WebSocket e salas, SSE, Web Push e push unificado, chat, comentários, event sourcing, feature flags e MCP.

[« Índice da referência](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference.md)

---

## Módulos

Uma página por módulo; cada uma renderiza a API pública dele a partir das docstrings.

| Módulo | O que cobre |
| --- | --- |
| [`chat`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/chat.md) | Blocos de mensageria: conversas, mensagens, mídia e confirmações de leitura. |
| [`eventsourcing`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/eventsourcing.md) | Event sourcing: o protocolo de event store com backends SQL e KurrentDB. |
| [`flags`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/flags.md) | Feature flags: o serviço, os backends (memória, env, Redis, composto) e a dependência do FastAPI. |
| [`mcp`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/mcp.md) | Model Context Protocol nas duas direções, sobre o FastMCP. |
| [`push`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push.md) | Push unificado: uma API para navegador e celular, com o model de token de dispositivo. |
| [`queue`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/queue.md) | Fila de mensagens sobre o FastStream: publisher, consumer, topologia, retry, dedup e tracing. |
| [`reviews`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/reviews.md) | Comentários e avaliações de 0 a 5 estrelas sobre qualquer alvo. |
| [`rooms`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/rooms.md) | Salas multiplayer online: um servidor de relay para jogos, sobre WebSocket ou TCP. |
| [`sse`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse.md) | Server-Sent Events sobre o streaming do Starlette. |
| [`tasks`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/tasks.md) | Tarefas em background sobre o TaskIQ: fila, agendador com lock e jobs persistidos. |
| [`webpush`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/webpush.md) | Web Push (VAPID): envio e schemas de inscrição. |
| [`websockets`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/websockets.md) | WebSocket: router autenticado, hub de conexões e o envelope de mensagem. |
