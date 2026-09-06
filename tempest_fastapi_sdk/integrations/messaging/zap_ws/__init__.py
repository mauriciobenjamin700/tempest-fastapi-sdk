"""Generated client for zap-api WebSocket.

Do not edit by hand — rerun the generator to refresh.
"""

from .schemas import AckFrame as AckFrame
from .schemas import AckFrameEvent as AckFrameEvent
from .schemas import AckFrameType as AckFrameType
from .schemas import ErrorFrame as ErrorFrame
from .schemas import ErrorFrameType as ErrorFrameType
from .schemas import SendFrame as SendFrame
from .schemas import SendFrameAction as SendFrameAction
from .schemas import ServerMessageFrame as ServerMessageFrame
from .schemas import ServerMessageFramePayload as ServerMessageFramePayload
from .schemas import (
    ServerMessageFramePayloadDirection as ServerMessageFramePayloadDirection,
)
from .schemas import ServerMessageFrameType as ServerMessageFrameType
from .schemas import SocketHeaders as SocketHeaders
from .schemas import SubscribeFrame as SubscribeFrame
from .schemas import SubscribeFrameAction as SubscribeFrameAction
from .schemas import UnsubscribeFrame as UnsubscribeFrame
from .schemas import UnsubscribeFrameAction as UnsubscribeFrameAction
from .stream import DEFAULT_URL as DEFAULT_URL
from .stream import ZapStream as ZapStream
from .stream import ZapStreamClientFrame as ZapStreamClientFrame
from .stream import ZapStreamFrameError as ZapStreamFrameError
from .stream import ZapStreamServerFrame as ZapStreamServerFrame

__all__: list[str] = [
    "DEFAULT_URL",
    "AckFrame",
    "AckFrameEvent",
    "AckFrameType",
    "ErrorFrame",
    "ErrorFrameType",
    "SendFrame",
    "SendFrameAction",
    "ServerMessageFrame",
    "ServerMessageFramePayload",
    "ServerMessageFramePayloadDirection",
    "ServerMessageFrameType",
    "SocketHeaders",
    "SubscribeFrame",
    "SubscribeFrameAction",
    "UnsubscribeFrame",
    "UnsubscribeFrameAction",
    "ZapStream",
    "ZapStreamClientFrame",
    "ZapStreamFrameError",
    "ZapStreamServerFrame",
]
