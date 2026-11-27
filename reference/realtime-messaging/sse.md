# `tempest_fastapi_sdk.sse`

Server-Sent Events sobre o streaming do Starlette.

[« Tempo real e mensageria](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging.md)

---

- [`tempest_fastapi_sdk.sse.event_stream.EventStream`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.EventStream)
- [`tempest_fastapi_sdk.sse.event_stream.EventStream.dropped_events`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.EventStream.dropped_events)
- [`tempest_fastapi_sdk.sse.event_stream.EventStream.publish`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.EventStream.publish)
- [`tempest_fastapi_sdk.sse.event_stream.EventStream.publish_event`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.EventStream.publish_event)
- [`tempest_fastapi_sdk.sse.event_stream.EventStream.close`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.EventStream.close)
- [`tempest_fastapi_sdk.sse.event_stream.EventStream.response`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.EventStream.response)
- [`tempest_fastapi_sdk.sse.event_stream.EventStream.stream`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.EventStream.stream)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.register`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.register)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.response`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.response)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.unregister`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.unregister)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.local_subscribers`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.local_subscribers)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.local_channels`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.local_channels)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.publish`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.publish)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.broadcast`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.broadcast)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.run`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.run)
- [`tempest_fastapi_sdk.sse.broker.SSEBroker.aclose`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.broker.SSEBroker.aclose)
- [`tempest_fastapi_sdk.sse.event_stream.ServerSentEvent`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.ServerSentEvent)
- [`tempest_fastapi_sdk.sse.event_stream.ServerSentEvent.encode`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.ServerSentEvent.encode)
- [`tempest_fastapi_sdk.sse.event_stream.sse_response`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.sse_response)
- [`tempest_fastapi_sdk.sse.router.make_sse_router`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.router.make_sse_router)
- [`tempest_fastapi_sdk.sse.router.DEFAULT_SSE_TICKET_TTL`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.router.DEFAULT_SSE_TICKET_TTL)
- [`tempest_fastapi_sdk.sse.router.SSE_CHANNEL_CLAIM`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.router.SSE_CHANNEL_CLAIM)
- [`tempest_fastapi_sdk.sse.router.SSE_TICKET_ERROR`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.router.SSE_TICKET_ERROR)
- [`tempest_fastapi_sdk.sse.schemas.SSETicketResponseSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.schemas.SSETicketResponseSchema)
- [`tempest_fastapi_sdk.sse.event_stream.OverflowPolicy`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.OverflowPolicy)
- [`tempest_fastapi_sdk.sse.event_stream.SSEData`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/sse/#tempest_fastapi_sdk.sse.event_stream.SSEData)

