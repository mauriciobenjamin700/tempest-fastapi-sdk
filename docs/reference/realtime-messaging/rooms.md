# `tempest_fastapi_sdk.rooms`

Salas multiplayer online: um servidor de relay para jogos, sobre WebSocket ou TCP.

[« Tempo real e mensageria](../realtime-messaging.md)

---

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
