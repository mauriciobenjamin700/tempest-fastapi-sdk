"""Online multiplayer rooms: a relay server for games, over WebSocket or TCP.

The server groups players into rooms joined by a short code, tells them who
the host is, relays their messages (JSON or opaque game bytes) stamped with
the sender it knows, and keeps a dropped player's seat for a grace window.
It never simulates the game: authority stays with the clients (a lockstep
simulation everyone runs, or a host that runs the world).

* :class:`RoomRegistry` + :class:`RoomPolicy` — the rooms, in this process.
* :func:`make_game_room_router` — the WebSocket endpoint (FastAPI).
* :func:`serve_game_rooms_tcp` — the same protocol over raw TCP.
"""

from tempest_fastapi_sdk.rooms.errors import AlreadyInRoomError as AlreadyInRoomError
from tempest_fastapi_sdk.rooms.errors import BadRequestError as BadRequestError
from tempest_fastapi_sdk.rooms.errors import (
    MessageNotAllowedError as MessageNotAllowedError,
)
from tempest_fastapi_sdk.rooms.errors import NotHostError as NotHostError
from tempest_fastapi_sdk.rooms.errors import NotInRoomError as NotInRoomError
from tempest_fastapi_sdk.rooms.errors import PlayerNotFoundError as PlayerNotFoundError
from tempest_fastapi_sdk.rooms.errors import (
    ProtocolVersionError as ProtocolVersionError,
)
from tempest_fastapi_sdk.rooms.errors import RateLimitedError as RateLimitedError
from tempest_fastapi_sdk.rooms.errors import ResumeRejectedError as ResumeRejectedError
from tempest_fastapi_sdk.rooms.errors import RoomError as RoomError
from tempest_fastapi_sdk.rooms.errors import RoomFullError as RoomFullError
from tempest_fastapi_sdk.rooms.errors import RoomInMatchError as RoomInMatchError
from tempest_fastapi_sdk.rooms.errors import RoomNotFoundError as RoomNotFoundError
from tempest_fastapi_sdk.rooms.errors import ServerFullError as ServerFullError
from tempest_fastapi_sdk.rooms.errors import UnauthorizedError as UnauthorizedError
from tempest_fastapi_sdk.rooms.registry import BROADCAST_SLOT as BROADCAST_SLOT
from tempest_fastapi_sdk.rooms.registry import (
    REPLACED_CLOSE_CODE as REPLACED_CLOSE_CODE,
)
from tempest_fastapi_sdk.rooms.registry import (
    ROOM_CODE_ALPHABET as ROOM_CODE_ALPHABET,
)
from tempest_fastapi_sdk.rooms.registry import IdentityResolver as IdentityResolver
from tempest_fastapi_sdk.rooms.registry import Room as Room
from tempest_fastapi_sdk.rooms.registry import RoomIdentity as RoomIdentity
from tempest_fastapi_sdk.rooms.registry import RoomMember as RoomMember
from tempest_fastapi_sdk.rooms.registry import RoomPeer as RoomPeer
from tempest_fastapi_sdk.rooms.registry import RoomPolicy as RoomPolicy
from tempest_fastapi_sdk.rooms.registry import RoomRegistry as RoomRegistry
from tempest_fastapi_sdk.rooms.registry import RoomState as RoomState
from tempest_fastapi_sdk.rooms.session import RoomSession as RoomSession
from tempest_fastapi_sdk.rooms.session import hello_message as hello_message
from tempest_fastapi_sdk.rooms.session import ping_message as ping_message
from tempest_fastapi_sdk.rooms.tcp import (
    DEFAULT_MAX_FRAME_BYTES as DEFAULT_MAX_FRAME_BYTES,
)
from tempest_fastapi_sdk.rooms.tcp import FRAME_BINARY as FRAME_BINARY
from tempest_fastapi_sdk.rooms.tcp import FRAME_HEADER as FRAME_HEADER
from tempest_fastapi_sdk.rooms.tcp import FRAME_JSON as FRAME_JSON
from tempest_fastapi_sdk.rooms.tcp import FrameTooLargeError as FrameTooLargeError
from tempest_fastapi_sdk.rooms.tcp import TCPRoomPeer as TCPRoomPeer
from tempest_fastapi_sdk.rooms.tcp import encode_frame as encode_frame
from tempest_fastapi_sdk.rooms.tcp import read_frame as read_frame
from tempest_fastapi_sdk.rooms.tcp import serve_game_rooms_tcp as serve_game_rooms_tcp
from tempest_fastapi_sdk.rooms.websocket import (
    DEFAULT_GAME_ROOM_PATH as DEFAULT_GAME_ROOM_PATH,
)
from tempest_fastapi_sdk.rooms.websocket import (
    UNAUTHORIZED_CLOSE_CODE as UNAUTHORIZED_CLOSE_CODE,
)
from tempest_fastapi_sdk.rooms.websocket import WebSocketRoomPeer as WebSocketRoomPeer
from tempest_fastapi_sdk.rooms.websocket import (
    make_game_room_router as make_game_room_router,
)
from tempest_fastapi_sdk.rooms.websocket import (
    make_jwt_room_identity_resolver as make_jwt_room_identity_resolver,
)

__all__: list[str] = [
    "BROADCAST_SLOT",
    "DEFAULT_GAME_ROOM_PATH",
    "DEFAULT_MAX_FRAME_BYTES",
    "FRAME_BINARY",
    "FRAME_HEADER",
    "FRAME_JSON",
    "REPLACED_CLOSE_CODE",
    "ROOM_CODE_ALPHABET",
    "UNAUTHORIZED_CLOSE_CODE",
    "AlreadyInRoomError",
    "BadRequestError",
    "FrameTooLargeError",
    "IdentityResolver",
    "MessageNotAllowedError",
    "NotHostError",
    "NotInRoomError",
    "PlayerNotFoundError",
    "ProtocolVersionError",
    "RateLimitedError",
    "ResumeRejectedError",
    "Room",
    "RoomError",
    "RoomFullError",
    "RoomIdentity",
    "RoomInMatchError",
    "RoomMember",
    "RoomNotFoundError",
    "RoomPeer",
    "RoomPolicy",
    "RoomRegistry",
    "RoomSession",
    "RoomState",
    "ServerFullError",
    "TCPRoomPeer",
    "UnauthorizedError",
    "WebSocketRoomPeer",
    "encode_frame",
    "hello_message",
    "make_game_room_router",
    "make_jwt_room_identity_resolver",
    "ping_message",
    "read_frame",
    "serve_game_rooms_tcp",
]
