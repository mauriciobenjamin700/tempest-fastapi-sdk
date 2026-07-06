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
    MessageCreateSchema,
    MessageResponseSchema,
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
            title=body.title,
        )

    @router.get("/conversations", response_model=list[ConversationResponseSchema])
    async def list_conversations(
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> list[ConversationResponseSchema]:
        """List the caller's conversations.

        Args:
            user_id (UUID): The authenticated user.
            service (ChatService): Request-scoped service.

        Returns:
            list[ConversationResponseSchema]: The caller's conversations.
        """
        return await service.list_conversations(user_id)

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
        return await service.post_message(conversation_id, user_id, body.body)

    @router.get("/conversations/{conversation_id}/messages")
    async def list_messages(
        conversation_id: UUID,
        page: int = 1,
        page_size: int = 20,
        user_id: UUID = Depends(current_user_id),
        service: ChatService = Depends(_service),
    ) -> dict[str, Any]:
        """Page a conversation's message history (oldest first).

        Args:
            conversation_id (UUID): The conversation to read.
            page (int): 1-indexed page number.
            page_size (int): Messages per page.
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
            page=page,
            page_size=page_size,
        )

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
