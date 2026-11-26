# Referência: Tempo real e mensageria

Fila e tarefas, WebSocket, SSE, Web Push, chat, comentários, event sourcing, feature flags e MCP.

[« Índice da referência](../reference.md)

---

## Chat

### `tempest_fastapi_sdk.chat`

::: tempest_fastapi_sdk.chat
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Comentários e avaliações

### `tempest_fastapi_sdk.reviews`

::: tempest_fastapi_sdk.reviews
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Event sourcing

### `tempest_fastapi_sdk.eventsourcing`

::: tempest_fastapi_sdk.eventsourcing
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Feature flags

::: tempest_fastapi_sdk.flags.service.FeatureFlags
::: tempest_fastapi_sdk.flags.backends.FeatureFlagBackend
::: tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend
::: tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend
::: tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend
::: tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend
::: tempest_fastapi_sdk.flags.dependencies.make_flag_dependency

---

## Fila e tarefas

### `tempest_fastapi_sdk.queue`

::: tempest_fastapi_sdk.queue
::: tempest_fastapi_sdk.queue.broker.PublishResult
::: tempest_fastapi_sdk.queue.broker.QueuePublisher
::: tempest_fastapi_sdk.queue.publisher.Publisher
::: tempest_fastapi_sdk.queue.consumer.Subscription
::: tempest_fastapi_sdk.queue.topology.QueueSpec
::: tempest_fastapi_sdk.queue.topology.DeadLetterSpec
::: tempest_fastapi_sdk.queue.topology.ExchangeSpec
::: tempest_fastapi_sdk.queue.topology.ExchangeBinding
::: tempest_fastapi_sdk.queue.topology.ExchangeKindEnum
::: tempest_fastapi_sdk.queue.topology.OffsetResetEnum
::: tempest_fastapi_sdk.queue.topology.QueueTypeEnum
::: tempest_fastapi_sdk.queue.topology.TransportEnum
::: tempest_fastapi_sdk.queue.topology.UnsupportedTopologyError
::: tempest_fastapi_sdk.queue.reliability.ConsumerRetryPolicy
::: tempest_fastapi_sdk.queue.reliability.RetryTopology
::: tempest_fastapi_sdk.queue.reliability.retry_queues
::: tempest_fastapi_sdk.queue.reliability.delivery_attempt
::: tempest_fastapi_sdk.queue.reliability.QueueMetrics
::: tempest_fastapi_sdk.queue.dedup.DedupStore
::: tempest_fastapi_sdk.queue.dedup.DedupStateEnum
::: tempest_fastapi_sdk.queue.dedup.MemoryDedupStore
::: tempest_fastapi_sdk.queue.dedup.RedisDedupStore
::: tempest_fastapi_sdk.queue.dedup.ConcurrentDeliveryError
::: tempest_fastapi_sdk.queue.tracing.consume_span
::: tempest_fastapi_sdk.queue.tracing.inject_context
::: tempest_fastapi_sdk.queue.tracing.extract_context
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

### `tempest_fastapi_sdk.tasks`

::: tempest_fastapi_sdk.tasks
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.tasks.lock.SchedulerLock
::: tempest_fastapi_sdk.tasks.lock.RedisSchedulerLock
::: tempest_fastapi_sdk.tasks.queue.TaskIQSettingsLike
::: tempest_fastapi_sdk.tasks.queue.LifecycleResource
::: tempest_fastapi_sdk.tasks.queue.LifecycleScope
::: tempest_fastapi_sdk.tasks.queue.ResultSerializer
::: tempest_fastapi_sdk.tasks.queue.Hook
::: tempest_fastapi_sdk.tasks.jobs.TERMINAL_JOB_STATUSES
::: tempest_fastapi_sdk.tasks.jobs.STALE_JOB_ERROR
::: tempest_fastapi_sdk.tasks.jobs.STORE_OWNED_JOB_COLUMNS
::: tempest_fastapi_sdk.tasks.jobs.make_job_admin_model

---

## MCP (Model Context Protocol)

### `tempest_fastapi_sdk.mcp`

::: tempest_fastapi_sdk.mcp
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.mcp.client.DEFAULT_MCP_CALL_TIMEOUT
::: tempest_fastapi_sdk.mcp.server.DEFAULT_FORWARDED_HEADERS
::: tempest_fastapi_sdk.mcp.server.DEFAULT_MCP_TOOL_TIMEOUT
::: tempest_fastapi_sdk.mcp.server.PRINCIPAL_STATE_KEY

---

## Server-Sent Events

### `tempest_fastapi_sdk.sse`

::: tempest_fastapi_sdk.sse.event_stream.EventStream
::: tempest_fastapi_sdk.sse.broker.SSEBroker
::: tempest_fastapi_sdk.sse.event_stream.ServerSentEvent
::: tempest_fastapi_sdk.sse.event_stream.sse_response
::: tempest_fastapi_sdk.sse.router.make_sse_router
::: tempest_fastapi_sdk.sse.router.DEFAULT_SSE_TICKET_TTL
::: tempest_fastapi_sdk.sse.router.SSE_CHANNEL_CLAIM
::: tempest_fastapi_sdk.sse.router.SSE_TICKET_ERROR
::: tempest_fastapi_sdk.sse.schemas.SSETicketResponseSchema

---

## Web Push

### `tempest_fastapi_sdk.webpush`

::: tempest_fastapi_sdk.webpush.dispatcher.WebPushDispatcher
::: tempest_fastapi_sdk.webpush.dispatcher.WebPushEndpointRefusedError
::: tempest_fastapi_sdk.webpush.dispatcher.WebPushConfigurationError
::: tempest_fastapi_sdk.webpush.service.WebPushSubscriptionService
::: tempest_fastapi_sdk.webpush.router.make_web_push_router
::: tempest_fastapi_sdk.db.webpush_subscription_model.BaseWebPushSubscriptionModel
::: tempest_fastapi_sdk.webpush.schemas.WebPushSubscriptionSchema
::: tempest_fastapi_sdk.webpush.schemas.WebPushPayloadSchema
::: tempest_fastapi_sdk.webpush.schemas.WebPushActionSchema

---

## WebSocket

### `tempest_fastapi_sdk.websockets`

::: tempest_fastapi_sdk.websockets.hub.WebSocketHub
::: tempest_fastapi_sdk.websockets.hub.WebSocketConnection
::: tempest_fastapi_sdk.websockets.router.make_websocket_router
::: tempest_fastapi_sdk.websockets.router.make_jwt_bearer_resolver
::: tempest_fastapi_sdk.websockets.heartbeat.heartbeat
::: tempest_fastapi_sdk.websockets.heartbeat.Liveness

### `tempest_fastapi_sdk.rooms`

::: tempest_fastapi_sdk.rooms.registry.RoomRegistry
::: tempest_fastapi_sdk.rooms.registry.RoomPolicy
::: tempest_fastapi_sdk.rooms.registry.RoomIdentity
::: tempest_fastapi_sdk.rooms.registry.IdentityResolver
::: tempest_fastapi_sdk.rooms.registry.Room
::: tempest_fastapi_sdk.rooms.registry.RoomMember
::: tempest_fastapi_sdk.rooms.registry.RoomState
::: tempest_fastapi_sdk.rooms.registry.RoomPeer
::: tempest_fastapi_sdk.rooms.registry.BROADCAST_SLOT
::: tempest_fastapi_sdk.rooms.registry.REPLACED_CLOSE_CODE
::: tempest_fastapi_sdk.rooms.registry.ROOM_CODE_ALPHABET
::: tempest_fastapi_sdk.rooms.session.RoomSession
::: tempest_fastapi_sdk.rooms.session.hello_message
::: tempest_fastapi_sdk.rooms.session.ping_message
::: tempest_fastapi_sdk.rooms.websocket.make_game_room_router
::: tempest_fastapi_sdk.rooms.websocket.make_jwt_room_identity_resolver
::: tempest_fastapi_sdk.rooms.websocket.WebSocketRoomPeer
::: tempest_fastapi_sdk.rooms.websocket.DEFAULT_GAME_ROOM_PATH
::: tempest_fastapi_sdk.rooms.websocket.UNAUTHORIZED_CLOSE_CODE
::: tempest_fastapi_sdk.rooms.tcp.serve_game_rooms_tcp
::: tempest_fastapi_sdk.rooms.tcp.encode_frame
::: tempest_fastapi_sdk.rooms.tcp.read_frame
::: tempest_fastapi_sdk.rooms.tcp.TCPRoomPeer
::: tempest_fastapi_sdk.rooms.tcp.FrameTooLargeError
::: tempest_fastapi_sdk.rooms.tcp.FRAME_HEADER
::: tempest_fastapi_sdk.rooms.tcp.FRAME_JSON
::: tempest_fastapi_sdk.rooms.tcp.FRAME_BINARY
::: tempest_fastapi_sdk.rooms.tcp.DEFAULT_MAX_FRAME_BYTES
::: tempest_fastapi_sdk.rooms.errors.RoomError
::: tempest_fastapi_sdk.rooms.errors.RoomNotFoundError
::: tempest_fastapi_sdk.rooms.errors.RoomFullError
::: tempest_fastapi_sdk.rooms.errors.RoomInMatchError
::: tempest_fastapi_sdk.rooms.errors.ServerFullError
::: tempest_fastapi_sdk.rooms.errors.ProtocolVersionError
::: tempest_fastapi_sdk.rooms.errors.NotHostError
::: tempest_fastapi_sdk.rooms.errors.NotInRoomError
::: tempest_fastapi_sdk.rooms.errors.AlreadyInRoomError
::: tempest_fastapi_sdk.rooms.errors.PlayerNotFoundError
::: tempest_fastapi_sdk.rooms.errors.ResumeRejectedError
::: tempest_fastapi_sdk.rooms.errors.RateLimitedError
::: tempest_fastapi_sdk.rooms.errors.MessageNotAllowedError
::: tempest_fastapi_sdk.rooms.errors.UnauthorizedError
::: tempest_fastapi_sdk.rooms.errors.BadRequestError
::: tempest_fastapi_sdk.websockets.schemas.WSEnvelope
