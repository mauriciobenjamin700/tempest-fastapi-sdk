"""Enums the chat tables and schemas share.

All three are :class:`~tempest_fastapi_sdk.BaseStrEnum`, and that is
load-bearing rather than stylistic: ``BaseSchema`` sets
``use_enum_values=True``, so a schema field holds the enum's *value*.
With a ``str``-based enum ``field == Member`` is still ``True``; with a
plain ``Enum`` it is ``False`` and the branch silently never runs.
"""

from __future__ import annotations

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class ConversationKind(BaseStrEnum):
    """Whether a conversation is a private pair or a named group.

    The split is a column rather than "a group is one with a title",
    because a direct thread has no title by design — it is labelled by
    the other person, which is a per-reader answer and never a stored
    one.

    Attributes:
        DIRECT (str): Exactly two people, created once per pair.
        GROUP (str): Any number of people, with a title and roles.
    """

    DIRECT = "direct"
    GROUP = "group"


class ParticipantRole(BaseStrEnum):
    """What a participant may do inside a conversation.

    Attributes:
        MEMBER (str): Can read and post.
        ADMIN (str): Can also add/remove members and edit group data.
        OWNER (str): The creator; cannot be removed by an admin.
    """

    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


class MessageKind(BaseStrEnum):
    """What a message carries.

    ``body`` holds the text of a :attr:`TEXT` message and the caption of
    every media kind, so a caption never needs a second column and an
    empty caption is an empty string.

    Attributes:
        TEXT (str): Plain text.
        IMAGE (str): One or more images, in the attachment table.
        VIDEO (str): A video.
        AUDIO (str): An audio file.
        VOICE (str): A recorded voice note (drawn with a waveform).
        DOCUMENT (str): An arbitrary file.
        LOCATION (str): Coordinates, in ``payload``.
        CONTACT (str): A contact card, in ``payload``.
        SYSTEM (str): A notice the server wrote ("Ana criou o grupo"),
            with its structured event in ``payload``.
    """

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"
    LOCATION = "location"
    CONTACT = "contact"
    SYSTEM = "system"


class SystemEvent(BaseStrEnum):
    """The structured event behind a :attr:`MessageKind.SYSTEM` message.

    The client renders from this plus ``payload``'s ids, so the notice
    is localized and the names are links. The rendered ``body`` the
    server writes is the fallback for push notifications and search,
    never the primary representation.

    Attributes:
        CONVERSATION_CREATED (str): The group was created.
        PARTICIPANTS_ADDED (str): Someone added members.
        PARTICIPANT_REMOVED (str): Someone was removed.
        PARTICIPANT_LEFT (str): Someone left on their own.
        TITLE_CHANGED (str): The group title changed.
        DESCRIPTION_CHANGED (str): The group description changed.
        ROLE_CHANGED (str): A participant's role changed.
    """

    CONVERSATION_CREATED = "conversation_created"
    PARTICIPANTS_ADDED = "participants_added"
    PARTICIPANT_REMOVED = "participant_removed"
    PARTICIPANT_LEFT = "participant_left"
    TITLE_CHANGED = "title_changed"
    DESCRIPTION_CHANGED = "description_changed"
    ROLE_CHANGED = "role_changed"


__all__: list[str] = [
    "ConversationKind",
    "MessageKind",
    "ParticipantRole",
    "SystemEvent",
]
