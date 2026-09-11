"""Messenger building blocks — conversations, messages, media, receipts.

Reusable pieces over the SDK primitives (``BaseModel`` /
``BaseRepository`` / pagination / SSE): abstract tables + ``make_*``
factories, a :class:`ChatService`, and an opt-in
:func:`make_chat_router`. Import the pieces you need and mount the
router, or drive the service directly.

What the module owns is the set of decisions that go wrong the same way
in every product that takes them alone: read state as a **watermark** on
the participant rather than a receipt row per reader, **one** reaction
per person (replaced, not stacked), a client-generated ``client_id`` that
makes a retried send idempotent, a revoke that **clears** the body while
keeping the row so replies still have something to quote, and a direct
conversation that cannot be created twice for the same pair.
"""

from tempest_fastapi_sdk.chat.constants import (
    ConversationKind as ConversationKind,
)
from tempest_fastapi_sdk.chat.constants import (
    MessageKind as MessageKind,
)
from tempest_fastapi_sdk.chat.constants import (
    ParticipantRole as ParticipantRole,
)
from tempest_fastapi_sdk.chat.constants import (
    SystemEvent as SystemEvent,
)
from tempest_fastapi_sdk.chat.models import (
    BaseConversationModel as BaseConversationModel,
)
from tempest_fastapi_sdk.chat.models import (
    BaseConversationParticipantModel as BaseConversationParticipantModel,
)
from tempest_fastapi_sdk.chat.models import (
    BaseMessageAttachmentModel as BaseMessageAttachmentModel,
)
from tempest_fastapi_sdk.chat.models import (
    BaseMessageModel as BaseMessageModel,
)
from tempest_fastapi_sdk.chat.models import (
    BaseMessageReactionModel as BaseMessageReactionModel,
)
from tempest_fastapi_sdk.chat.models import (
    make_conversation_model as make_conversation_model,
)
from tempest_fastapi_sdk.chat.models import (
    make_conversation_participant_model as make_conversation_participant_model,
)
from tempest_fastapi_sdk.chat.models import (
    make_message_attachment_model as make_message_attachment_model,
)
from tempest_fastapi_sdk.chat.models import (
    make_message_model as make_message_model,
)
from tempest_fastapi_sdk.chat.models import (
    make_message_reaction_model as make_message_reaction_model,
)
from tempest_fastapi_sdk.chat.router import make_chat_router as make_chat_router
from tempest_fastapi_sdk.chat.schemas import (
    REPLY_EXCERPT_LENGTH as REPLY_EXCERPT_LENGTH,
)
from tempest_fastapi_sdk.chat.schemas import (
    AttachmentResponseSchema as AttachmentResponseSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ConversationCreateSchema as ConversationCreateSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ConversationResponseSchema as ConversationResponseSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ConversationUpdateSchema as ConversationUpdateSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ForwardSchema as ForwardSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    MarkReadSchema as MarkReadSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    MessageCreateSchema as MessageCreateSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    MessageEditSchema as MessageEditSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    MessageResponseSchema as MessageResponseSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ParticipantPreferencesSchema as ParticipantPreferencesSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ParticipantResponseSchema as ParticipantResponseSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ReactionCreateSchema as ReactionCreateSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ReactionSummarySchema as ReactionSummarySchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ReceiptCountsSchema as ReceiptCountsSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ReplyPreviewSchema as ReplyPreviewSchema,
)
from tempest_fastapi_sdk.chat.service import ChatService as ChatService

__all__: list[str] = [
    "REPLY_EXCERPT_LENGTH",
    "AttachmentResponseSchema",
    "BaseConversationModel",
    "BaseConversationParticipantModel",
    "BaseMessageAttachmentModel",
    "BaseMessageModel",
    "BaseMessageReactionModel",
    "ChatService",
    "ConversationCreateSchema",
    "ConversationKind",
    "ConversationResponseSchema",
    "ConversationUpdateSchema",
    "ForwardSchema",
    "MarkReadSchema",
    "MessageCreateSchema",
    "MessageEditSchema",
    "MessageKind",
    "MessageResponseSchema",
    "ParticipantPreferencesSchema",
    "ParticipantResponseSchema",
    "ParticipantRole",
    "ReactionCreateSchema",
    "ReactionSummarySchema",
    "ReceiptCountsSchema",
    "ReplyPreviewSchema",
    "SystemEvent",
    "make_chat_router",
    "make_conversation_model",
    "make_conversation_participant_model",
    "make_message_attachment_model",
    "make_message_model",
    "make_message_reaction_model",
]
