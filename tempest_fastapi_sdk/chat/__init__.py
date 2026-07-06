"""Threaded chat module — conversations, messages and real-time fan-out.

Reusable building blocks over the SDK primitives (``BaseModel`` /
``BaseRepository`` / pagination / SSE): abstract tables + ``make_*``
factories, a :class:`ChatService`, and an opt-in
:func:`make_chat_router`. Import the pieces you need and mount the
router, or drive the service directly.
"""

from tempest_fastapi_sdk.chat.models import (
    BaseConversationModel as BaseConversationModel,
)
from tempest_fastapi_sdk.chat.models import (
    BaseConversationParticipantModel as BaseConversationParticipantModel,
)
from tempest_fastapi_sdk.chat.models import (
    BaseMessageModel as BaseMessageModel,
)
from tempest_fastapi_sdk.chat.models import (
    make_conversation_model as make_conversation_model,
)
from tempest_fastapi_sdk.chat.models import (
    make_conversation_participant_model as make_conversation_participant_model,
)
from tempest_fastapi_sdk.chat.models import (
    make_message_model as make_message_model,
)
from tempest_fastapi_sdk.chat.router import make_chat_router as make_chat_router
from tempest_fastapi_sdk.chat.schemas import (
    ConversationCreateSchema as ConversationCreateSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    ConversationResponseSchema as ConversationResponseSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    MessageCreateSchema as MessageCreateSchema,
)
from tempest_fastapi_sdk.chat.schemas import (
    MessageResponseSchema as MessageResponseSchema,
)
from tempest_fastapi_sdk.chat.service import ChatService as ChatService

__all__: list[str] = [
    "BaseConversationModel",
    "BaseConversationParticipantModel",
    "BaseMessageModel",
    "ChatService",
    "ConversationCreateSchema",
    "ConversationResponseSchema",
    "MessageCreateSchema",
    "MessageResponseSchema",
    "make_chat_router",
    "make_conversation_model",
    "make_conversation_participant_model",
    "make_message_model",
]
