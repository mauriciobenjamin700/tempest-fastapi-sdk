"""Business logic for threaded chat — conversations and messages.

:class:`ChatService` ties three repositories (conversations,
participants, messages) into the operations a chat product needs:
start a conversation, post a message, list a user's conversations, and
page a conversation's message history. When an
:class:`~tempest_fastapi_sdk.sse.SSEBroker` is injected, every posted
message is also published to the conversation's channel so connected
clients receive it in real time — reusing the SDK's existing SSE
fan-out rather than adding a new transport.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from tempest_fastapi_sdk.chat.schemas import (
    ConversationResponseSchema,
    MessageResponseSchema,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.repository import BaseRepository
    from tempest_fastapi_sdk.sse import SSEBroker


class ChatService:
    """Create conversations, post messages, and list history.

    Attributes:
        conversations (BaseRepository[Any]): Repository for the
            conversation table.
        participants (BaseRepository[Any]): Repository for the
            participant join table.
        messages (BaseRepository[Any]): Repository for the message table.
        broker (SSEBroker | None): Optional SSE fan-out; when set, posted
            messages are published to channel ``str(conversation_id)``.
    """

    def __init__(
        self,
        *,
        conversations: BaseRepository[Any],
        participants: BaseRepository[Any],
        messages: BaseRepository[Any],
        broker: SSEBroker | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            conversations (BaseRepository[Any]): Conversation repository.
            participants (BaseRepository[Any]): Participant repository.
            messages (BaseRepository[Any]): Message repository.
            broker (SSEBroker | None): Optional real-time fan-out broker.
        """
        self.conversations: BaseRepository[Any] = conversations
        self.participants: BaseRepository[Any] = participants
        self.messages: BaseRepository[Any] = messages
        self.broker: SSEBroker | None = broker

    async def start_conversation(
        self,
        creator_id: UUID,
        participant_ids: list[UUID],
        *,
        title: str | None = None,
    ) -> ConversationResponseSchema:
        """Create a conversation and add its participants.

        The creator is always a participant — their id is merged into
        ``participant_ids`` (de-duplicated), so a caller can pass only the
        other members.

        Args:
            creator_id (UUID): The user starting the conversation.
            participant_ids (list[UUID]): Other users to add.
            title (str | None): Optional conversation title.

        Returns:
            ConversationResponseSchema: The created conversation.
        """
        conversation = await self.conversations.add(
            self.conversations.model(title=title),
        )
        member_ids = {creator_id, *participant_ids}
        for user_id in member_ids:
            await self.participants.add(
                self.participants.model(
                    conversation_id=conversation.id,
                    user_id=user_id,
                ),
            )
        return ConversationResponseSchema.model_validate(conversation)

    async def get_conversation(
        self,
        conversation_id: UUID,
    ) -> ConversationResponseSchema:
        """Return a single conversation by id.

        Args:
            conversation_id (UUID): The conversation id.

        Returns:
            ConversationResponseSchema: The conversation.

        Raises:
            AppException: The repository's not-found exception when no
                conversation matches.
        """
        conversation = await self.conversations.get_by_id(conversation_id)
        return ConversationResponseSchema.model_validate(conversation)

    async def is_participant(self, conversation_id: UUID, user_id: UUID) -> bool:
        """Return whether ``user_id`` belongs to ``conversation_id``.

        Args:
            conversation_id (UUID): The conversation to check.
            user_id (UUID): The user to check.

        Returns:
            bool: ``True`` when the user is a participant.
        """
        return await self.participants.exists(
            {"conversation_id": conversation_id, "user_id": user_id},
        )

    async def list_conversations(
        self,
        user_id: UUID,
    ) -> list[ConversationResponseSchema]:
        """Return every conversation ``user_id`` participates in.

        Returns ``[]`` when the user is in no conversations, per the SDK
        collection convention.

        Args:
            user_id (UUID): The user whose conversations to list.

        Returns:
            list[ConversationResponseSchema]: The user's conversations.
        """
        memberships = await self.participants.list(filters={"user_id": user_id})
        conversation_ids = [m.conversation_id for m in memberships]
        if not conversation_ids:
            return []
        rows = await self.conversations.list(filters={"id": conversation_ids})
        return [ConversationResponseSchema.model_validate(row) for row in rows]

    async def post_message(
        self,
        conversation_id: UUID,
        sender_id: UUID,
        body: str,
    ) -> MessageResponseSchema:
        """Persist a message and (optionally) publish it in real time.

        Args:
            conversation_id (UUID): The target conversation.
            sender_id (UUID): The sending user.
            body (str): The message text.

        Returns:
            MessageResponseSchema: The persisted message.
        """
        row = await self.messages.add(
            self.messages.model(
                conversation_id=conversation_id,
                sender_id=sender_id,
                body=body,
            ),
        )
        message = MessageResponseSchema.model_validate(row)
        if self.broker is not None:
            await self.broker.publish(
                str(conversation_id),
                data=message.model_dump(mode="json"),
                event="message",
            )
        return message

    async def list_messages(
        self,
        conversation_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        ascending: bool = True,
    ) -> dict[str, Any]:
        """Return an offset page of a conversation's messages.

        Args:
            conversation_id (UUID): The conversation whose messages to
                page.
            page (int): 1-indexed page number.
            page_size (int): Messages per page.
            ascending (bool): Oldest-first when ``True`` (chat order).

        Returns:
            dict[str, Any]: ``items`` (mapped
            :class:`MessageResponseSchema`), ``total``, ``page``,
            ``size`` and ``pages``.
        """
        result = await self.messages.paginate(
            filters={"conversation_id": conversation_id},
            order_by="created_at",
            page=page,
            page_size=page_size,
            ascending=ascending,
        )
        items = [MessageResponseSchema.model_validate(row) for row in result["items"]]
        return {**result, "items": items}


__all__: list[str] = [
    "ChatService",
]
