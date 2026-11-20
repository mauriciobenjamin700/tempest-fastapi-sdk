"""Refusals a game room server answers with, each with a stable ``code``.

A client dispatches on ``code`` (sent as ``{"type": "error", "code": ...}``),
never on the message, which is for a log or a developer.
"""

from __future__ import annotations


class RoomError(Exception):
    """A request the room server refused.

    Attributes:
        code (str): Stable identifier the client dispatches on.
        message (str): Human-readable description.
    """

    code: str = "room_error"

    def __init__(self, message: str) -> None:
        """Build the refusal.

        Args:
            message (str): Human-readable description.
        """
        super().__init__(message)
        self.message: str = message


class RoomNotFoundError(RoomError):
    """No room has that code (or it closed)."""

    code = "room_not_found"


class RoomFullError(RoomError):
    """Every slot of the room is taken."""

    code = "room_full"


class RoomInMatchError(RoomError):
    """The room is playing and does not take new players."""

    code = "room_in_match"


class ServerFullError(RoomError):
    """The server already holds its maximum number of rooms."""

    code = "server_full"


class ProtocolVersionError(RoomError):
    """The client speaks another protocol version than the server."""

    code = "version_mismatch"


class NotHostError(RoomError):
    """Only the room's host may do that."""

    code = "not_host"


class NotInRoomError(RoomError):
    """The connection has not created, joined or resumed a room yet."""

    code = "not_in_room"


class AlreadyInRoomError(RoomError):
    """The connection is already in a room; leave it first."""

    code = "already_in_room"


class PlayerNotFoundError(RoomError):
    """The addressed player is not in the room."""

    code = "player_not_found"


class ResumeRejectedError(RoomError):
    """The resume token does not match, or the grace window ran out."""

    code = "resume_rejected"


class RateLimitedError(RoomError):
    """The player sent more messages than the policy allows."""

    code = "rate_limited"


class MessageNotAllowedError(RoomError):
    """The relayed message's ``type`` is not on the policy's allow list."""

    code = "message_not_allowed"


class UnauthorizedError(RoomError):
    """The server requires an identity token and none (or a bad one) came."""

    code = "unauthorized"


class BadRequestError(RoomError):
    """The frame is not a message the protocol defines."""

    code = "bad_request"


__all__: list[str] = [
    "AlreadyInRoomError",
    "BadRequestError",
    "MessageNotAllowedError",
    "NotHostError",
    "NotInRoomError",
    "PlayerNotFoundError",
    "ProtocolVersionError",
    "RateLimitedError",
    "ResumeRejectedError",
    "RoomError",
    "RoomFullError",
    "RoomInMatchError",
    "RoomNotFoundError",
    "ServerFullError",
    "UnauthorizedError",
]
