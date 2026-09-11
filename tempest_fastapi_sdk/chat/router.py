"""Opt-in FastAPI router for the chat module.

:func:`make_chat_router` wires :class:`ChatService` onto the HTTP
endpoints a chat product needs — start a conversation, list yours, post
and page messages, and (when the service has an
:class:`~tempest_fastapi_sdk.sse.SSEBroker`) subscribe to live messages
over SSE. Same factory shape as
:func:`tempest_fastapi_sdk.make_web_push_router`: the caller supplies
how a request-scoped service and the current user are resolved; the
router owns only the HTTP surface and the participant guard.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.chat.schemas import (
    ConversationCreateSchema,
    ConversationResponseSchema,
    ConversationUpdateSchema,
    ForwardSchema,
    MarkReadSchema,
    MessageCreateSchema,
    MessageEditSchema,
    MessageResponseSchema,
    ParticipantPreferencesSchema,
    ParticipantResponseSchema,
    ReactionCreateSchema,
)
from tempest_fastapi_sdk.chat.service import ChatService
from tempest_fastapi_sdk.exceptions import ForbiddenException, NotFoundException


def make_chat_router(
    *,
    service_factory: Callable[[AsyncSession], ChatService],
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    current_user_id: Callable[..., Any],
    prefix: str = "/api/chat",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the chat router.

    Endpoints (all require authentication via ``current_user_id``):

    * ``POST {prefix}/conversations`` -> start a conversation (creator is
      auto-added as a participant).
    * ``GET {prefix}/conversations`` -> list the caller's conversations.
    * ``POST {prefix}/conversations/{id}/messages`` -> post a message
      (caller must be a participant).
    * ``GET {prefix}/conversations/{id}/messages`` -> page the history
      (participant only).
    * ``GET {prefix}/conversations/{id}/stream`` -> SSE stream of new
      messages (participant only; requires the service to carry an
      ``SSEBroker``).

    Args:
        service_factory (Callable[[AsyncSession], ChatService]): Builds a
            request-scoped :class:`ChatService` from the yielded session.
        session_factory (Callable[[], AsyncIterator[AsyncSession]]):
            Yields a request-scoped DB session (the project's
            ``get_session``).
        current_user_id (Callable[..., Any]): FastAPI dependency resolving
            the authenticated user's :class:`~uuid.UUID`.
        prefix (str): URL prefix. Defaults to ``"/api/chat"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["chat"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["chat"]))

    async def _session() -> AsyncIterator[AsyncSession]:
        async for session in session_factory():
            yield session

    def _service(session: AsyncSession = Depends(_session)) -> ChatService:
        return service_factory(session)

    async def _require_participant(
        service: ChatService,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        if not await service.is_participant(conversation_id, user_id):
            raise ForbiddenException(message="You are not in this conversation.")

    @router.post(
        "/conversations",
        response_model=ConversationResponseSchema,
        status_code=status.HTTP_201_CREATED,
    )
    async def start_conversation(
        body: ConversationCreateSchema,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> ConversationResponseSchema:
        """Start a conversation with the caller as a participant.

        Args:
            body (ConversationCreateSchema): Participants + optional title.
            user_id (UUID): The authenticated creator.
            service (ChatService): Request-scoped service.

        Returns:
            ConversationResponseSchema: The created conversation.
        """
        return await service.start_conversation(
            user_id,
            body.participant_ids,
            kind=body.kind,
            title=body.title,
            description=body.description,
        )

    @router.get("/conversations", response_model=list[ConversationResponseSchema])
    async def list_conversations(
        include_archived: bool = False,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> list[ConversationResponseSchema]:
        """List the caller's conversations, pinned first then newest.

        Args:
            include_archived (bool): Include archived threads.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            list[ConversationResponseSchema]: The caller's conversations,
            each carrying their own ``unread_count``.
        """
        return await service.list_conversations(
            user_id,
            include_archived=include_archived,
        )

    @router.get(
        "/conversations/{conversation_id}",
        response_model=ConversationResponseSchema,
    )
    async def get_conversation(
        conversation_id: UUID,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> ConversationResponseSchema:
        """Return one conversation with its participants.

        Args:
            conversation_id (UUID): The conversation to read.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            ConversationResponseSchema: The conversation.

        Raises:
            ForbiddenException: When the caller is not a participant.
        """
        await _require_participant(service, conversation_id, user_id)
        return await service.get_conversation(conversation_id)

    @router.patch(
        "/conversations/{conversation_id}",
        response_model=ConversationResponseSchema,
    )
    async def update_conversation(
        conversation_id: UUID,
        body: ConversationUpdateSchema,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> ConversationResponseSchema:
        """Edit a group's title or description.

        Args:
            conversation_id (UUID): The group.
            body (ConversationUpdateSchema): The new values.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            ConversationResponseSchema: The updated conversation.

        Raises:
            ForbiddenException: When the caller is not an admin or owner.
        """
        return await service.update_conversation(
            conversation_id,
            user_id,
            title=body.title,
            description=body.description,
        )

    @router.put(
        "/conversations/{conversation_id}/preferences",
        response_model=ParticipantResponseSchema,
    )
    async def set_preferences(
        conversation_id: UUID,
        body: ParticipantPreferencesSchema,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> ParticipantResponseSchema:
        """Pin, archive or mute the conversation for the caller only.

        Args:
            conversation_id (UUID): The conversation.
            body (ParticipantPreferencesSchema): What to change.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            ParticipantResponseSchema: The caller's updated membership.

        Raises:
            ForbiddenException: When the caller is not a participant.
        """
        return await service.set_preferences(
            conversation_id,
            user_id,
            is_pinned=body.is_pinned,
            is_archived=body.is_archived,
            muted_until=body.muted_until,
        )

    @router.post(
        "/conversations/{conversation_id}/participants",
        response_model=ConversationResponseSchema,
    )
    async def add_participants(
        conversation_id: UUID,
        body: ConversationCreateSchema,
        share_history: bool = False,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> ConversationResponseSchema:
        """Add members to a group.

        Args:
            conversation_id (UUID): The group.
            body (ConversationCreateSchema): Carries
                ``participant_ids``; the other fields are ignored here.
            share_history (bool): Hand the backlog to the newcomers.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            ConversationResponseSchema: The updated conversation.

        Raises:
            ForbiddenException: When the caller is not an admin or owner.
        """
        return await service.add_participants(
            conversation_id,
            user_id,
            body.participant_ids,
            share_history=share_history,
        )

    @router.delete(
        "/conversations/{conversation_id}/participants/{participant_id}",
        response_model=ConversationResponseSchema,
    )
    async def remove_participant(
        conversation_id: UUID,
        participant_id: UUID,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> ConversationResponseSchema:
        """Remove a member from a group.

        Args:
            conversation_id (UUID): The group.
            participant_id (UUID): Who to remove.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            ConversationResponseSchema: The updated conversation.

        Raises:
            ForbiddenException: When the caller is not an admin or owner.
        """
        return await service.remove_participant(
            conversation_id,
            user_id,
            participant_id,
        )

    @router.post(
        "/conversations/{conversation_id}/leave",
        response_model=ConversationResponseSchema,
    )
    async def leave_conversation(
        conversation_id: UUID,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> ConversationResponseSchema:
        """Leave a conversation.

        Args:
            conversation_id (UUID): The conversation to leave.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            ConversationResponseSchema: The updated conversation.

        Raises:
            ForbiddenException: When the caller is not a participant.
        """
        return await service.leave(conversation_id, user_id)

    @router.post(
        "/conversations/{conversation_id}/read",
        response_model=ParticipantResponseSchema,
    )
    async def mark_read(
        conversation_id: UUID,
        body: MarkReadSchema,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> ParticipantResponseSchema:
        """Move the caller's read watermark.

        Args:
            conversation_id (UUID): The conversation being read.
            body (MarkReadSchema): Up to which message.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            ParticipantResponseSchema: The caller's updated membership.

        Raises:
            ForbiddenException: When the caller is not a participant.
        """
        return await service.mark_read(
            conversation_id,
            user_id,
            message_id=body.message_id,
        )

    @router.post(
        "/conversations/{conversation_id}/messages",
        response_model=MessageResponseSchema,
        status_code=status.HTTP_201_CREATED,
    )
    async def post_message(
        conversation_id: UUID,
        body: MessageCreateSchema,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> MessageResponseSchema:
        """Post a message to a conversation.

        Args:
            conversation_id (UUID): The target conversation.
            body (MessageCreateSchema): The message text.
            user_id (UUID): The authenticated sender.
            service (ChatService): Request-scoped service.

        Returns:
            MessageResponseSchema: The persisted message.

        Raises:
            ForbiddenException: When the caller is not a participant.
        """
        await _require_participant(service, conversation_id, user_id)
        return await service.post_message(conversation_id, user_id, body)

    @router.get("/conversations/{conversation_id}/messages")
    async def list_messages(
        conversation_id: UUID,
        page: int = 1,
        page_size: int = 20,
        with_receipts: bool = False,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> dict[str, Any]:
        """Page a conversation's message history (oldest first).

        Args:
            conversation_id (UUID): The conversation to read.
            page (int): 1-indexed page number.
            page_size (int): Messages per page.
            with_receipts (bool): Attach delivery/read counts.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            dict[str, Any]: The paginated message payload.

        Raises:
            ForbiddenException: When the caller is not a participant.
        """
        await _require_participant(service, conversation_id, user_id)
        return await service.list_messages(
            conversation_id,
            user_id=user_id,
            page=page,
            page_size=page_size,
            with_receipts=with_receipts,
        )

    @router.patch(
        "/messages/{message_id}",
        response_model=MessageResponseSchema,
    )
    async def edit_message(
        message_id: UUID,
        body: MessageEditSchema,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> MessageResponseSchema:
        """Edit a message you sent.

        Args:
            message_id (UUID): The message to edit.
            body (MessageEditSchema): The new text.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            MessageResponseSchema: The edited message.

        Raises:
            ForbiddenException: When the caller is not the sender.
        """
        return await service.edit_message(message_id, user_id, body.body)

    @router.delete(
        "/messages/{message_id}",
        response_model=MessageResponseSchema,
    )
    async def revoke_message(
        message_id: UUID,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> MessageResponseSchema:
        """Delete a message for everyone, leaving a tombstone.

        The storage keys the revoke orphaned are returned by the service
        for the caller to delete from storage; this router drops them,
        because a router that deletes objects would need the bucket.

        Args:
            message_id (UUID): The message to revoke.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            MessageResponseSchema: The tombstone.

        Raises:
            ForbiddenException: When the caller is not the sender.
        """
        message, _orphaned_keys = await service.revoke_message(message_id, user_id)
        return message

    @router.put(
        "/messages/{message_id}/reaction",
        response_model=MessageResponseSchema,
    )
    async def react(
        message_id: UUID,
        body: ReactionCreateSchema,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> MessageResponseSchema:
        """React to a message, replacing your previous reaction.

        Args:
            message_id (UUID): The message.
            body (ReactionCreateSchema): The emoji.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            MessageResponseSchema: The message with reactions rebuilt.
        """
        return await service.react(message_id, user_id, body.emoji)

    @router.delete(
        "/messages/{message_id}/reaction",
        response_model=MessageResponseSchema,
    )
    async def unreact(
        message_id: UUID,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> MessageResponseSchema:
        """Remove your reaction from a message.

        Args:
            message_id (UUID): The message.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            MessageResponseSchema: The message with reactions rebuilt.
        """
        return await service.unreact(message_id, user_id)

    @router.post(
        "/messages/{message_id}/forward",
        response_model=list[MessageResponseSchema],
    )
    async def forward_message(
        message_id: UUID,
        body: ForwardSchema,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> list[MessageResponseSchema]:
        """Forward a message into other conversations.

        Args:
            message_id (UUID): The message to forward.
            body (ForwardSchema): Where to forward it.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            list[MessageResponseSchema]: One message per target.

        Raises:
            ForbiddenException: When the caller is not a participant of
                a target conversation.
        """
        return await service.forward(message_id, user_id, body.conversation_ids)

    @router.get("/conversations/{conversation_id}/stream")
    async def stream_messages(
        conversation_id: UUID,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> Any:
        """Subscribe to live messages for a conversation over SSE.

        Args:
            conversation_id (UUID): The conversation to subscribe to.
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            Any: A ``text/event-stream`` response fanned by the broker.

        Raises:
            ForbiddenException: When the caller is not a participant.
            NotFoundException: When the service has no SSE broker.
        """
        await _require_participant(service, conversation_id, user_id)
        if service.broker is None:
            raise NotFoundException(message="Real-time streaming is not enabled.")
        return service.broker.response(str(conversation_id))

    return router


__all__: list[str] = [
    "make_chat_router",
]
