---
description: "Índice de tempo real: use para escolher como empurrar dados ao cliente sem polling; aponta para as receitas de SSE (página aberta) e de Web Push (página fechada)."
---

# Tempo real

Empurre dados para os clientes sem que o cliente fique fazendo polling. SSE para broadcasts servidor→navegador com a página aberta; para notificações que chegam mesmo com a página fechada, veja a receita dedicada de [Web Push](webpush.md).

## Server-Sent Events (SSE)

SSE (servidor → navegador, uma conexão HTTP de longa duração) tem
**receita própria** agora — endpoint, broadcast pra vários clientes e
alinhamento com o `tempest-react-sdk`:

**[Server-Sent Events (SSE) »](sse.md)**

## Notificações Web Push

Web Push (notificações que chegam mesmo com a página fechada) tem
receita própria — veja **[Web Push »](webpush.md)**.
