"""Business logic for a threaded messenger.

:class:`ChatService` ties the chat repositories into the operations a
messenger needs: start (or re-find) a conversation, post a message
idempotently, quote a reply, attach files, react, edit, revoke, forward,
and move the read watermarks. When an
:class:`~tempest_fastapi_sdk.sse.SSEBroker` is injected, every change is
also published to the conversation's channel, reusing the SDK's existing
SSE fan-out rather than adding a transport.

The service owns the decisions that are wrong the same way in every
service that takes them alone — watermarks instead of a receipt row per
reader, one reaction per person, a revoke that actually clears the body,
a direct conversation that cannot be created twice for the same pair.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tempest_fastapi_sdk.chat.constants import (
    ConversationKind,
    MessageKind,
    ParticipantRole,
    SystemEvent,
)
from tempest_fastapi_sdk.chat.schemas import (
    REPLY_EXCERPT_LENGTH,
    AttachmentResponseSchema,
    ConversationResponseSchema,
    MessageCreateSchema,
    MessageResponseSchema,
    ParticipantResponseSchema,
    ReactionSummarySchema,
    ReceiptCountsSchema,
    ReplyPreviewSchema,
)
from tempest_fastapi_sdk.exceptions.forbidden import ForbiddenException
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from datetime import datetime

    from tempest_fastapi_sdk.db.repository import BaseRepository
    from tempest_fastapi_sdk.sse import SSEBroker


class ChatService:
    """Create conversations, post messages, and keep read state.

    Attributes:
        conversations (BaseRepository[Any]): Conversation table.
        participants (BaseRepository[Any]): Participant join table.
        messages (BaseRepository[Any]): Message table.
        attachments (BaseRepository[Any] | None): Attachment table.
            ``None`` disables the media path — posting with
            ``attachment_ids`` then raises rather than silently dropping
            the files.
        reactions (BaseRepository[Any] | None): Reaction table. ``None``
            disables reacting the same way.
        broker (SSEBroker | None): Optional SSE fan-out; when set, every
            change is published to channel ``str(conversation_id)``.
    """

    def __init__(
        self,
        *,
        conversations: BaseRepository[Any],
        participants: BaseRepository[Any],
        messages: BaseRepository[Any],
        attachments: BaseRepository[Any] | None = None,
        reactions: BaseRepository[Any] | None = None,
        broker: SSEBroker | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            conversations (BaseRepository[Any]): Conversation repository.
            participants (BaseRepository[Any]): Participant repository.
            messages (BaseRepository[Any]): Message repository.
            attachments (BaseRepository[Any] | None): Attachment
                repository, when the product carries media.
            reactions (BaseRepository[Any] | None): Reaction repository,
                when the product has reactions.
            broker (SSEBroker | None): Optional real-time fan-out broker.
        """
        self.conversations: BaseRepository[Any] = conversations
        self.participants: BaseRepository[Any] = participants
        self.messages: BaseRepository[Any] = messages
        self.attachments: BaseRepository[Any] | None = attachments
        self.reactions: BaseRepository[Any] | None = reactions
        self.broker: SSEBroker | None = broker

    async def start_conversation(
        self,
        creator_id: UUID,
        participant_ids: list[UUID],
        *,
        kind: ConversationKind | str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> ConversationResponseSchema:
        """Create a conversation, or return the pair's existing one.

        The creator is always a participant — their id is merged into
        ``participant_ids`` — and takes the ``OWNER`` role in a group.

        A ``DIRECT`` conversation is **idempotent per pair**: asking
        twice for the same two people returns the conversation that
        already exists. Two threads between the same two people is a
        state the user cannot repair from the UI, and it is what happens
        whenever two clients open the same chat at once.

        Args:
            creator_id (UUID): The user starting the conversation.
            participant_ids (list[UUID]): The other users to add.
            kind (ConversationKind | str | None): Direct thread or
                group. ``None`` infers it from the headcount — up to two
                people is a direct thread, more is a group — which is
                what a caller that never heard of the distinction
                means, and keeps the pre-``kind`` behaviour intact.
                Asking for ``DIRECT`` **explicitly** with any other
                headcount is refused.
            title (str | None): Group title.
            description (str | None): Group subject text.

        Returns:
            ConversationResponseSchema: The created — or existing —
            conversation.

        Raises:
            ValidationException: When a direct conversation is asked for
                with anything other than exactly two distinct people.
        """
        member_ids = {creator_id, *participant_ids}
        inferred = kind is None
        resolved = (
            ConversationKind.DIRECT
            if inferred and len(member_ids) <= 2
            else ConversationKind.GROUP
            if inferred
            else ConversationKind(kind)
        )
        if resolved is ConversationKind.DIRECT:
            if not inferred and len(member_ids) != 2:
                raise ValidationException(
                    message="a direct conversation needs exactly two people",
                    details={"participants": sorted(str(i) for i in member_ids)},
                    field="participant_ids",
                )
            existing = await self._find_direct(member_ids)
            if existing is not None:
                return await self._conversation_response(existing)

        conversation = await self.conversations.add(
            self.conversations.model(
                kind=resolved,
                title=title,
                description=description,
                created_by=creator_id,
            ),
        )
        now = utcnow()
        for user_id in sorted(member_ids, key=str):
            await self.participants.add(
                self.participants.model(
                    conversation_id=conversation.id,
                    user_id=user_id,
                    role=(
                        ParticipantRole.OWNER
                        if user_id == creator_id and resolved is ConversationKind.GROUP
                        else ParticipantRole.MEMBER
                    ),
                    joined_at=now,
                ),
            )
        if resolved is ConversationKind.GROUP:
            await self._system_message(
                conversation.id,
                actor_id=creator_id,
                event=SystemEvent.CONVERSATION_CREATED,
                user_ids=sorted(member_ids - {creator_id}, key=str),
            )
        return await self._conversation_response(conversation)

    async def _find_direct(self, member_ids: set[UUID]) -> Any | None:
        """Return the existing direct conversation for a pair, if any.

        Args:
            member_ids (set[UUID]): The two user ids.

        Returns:
            Any | None: The conversation row, or ``None``.
        """
        rows = await self.participants.list(
            filters={"user_id": sorted(member_ids, key=str)},
        )
        counts: dict[UUID, set[UUID]] = {}
        for row in rows:
            counts.setdefault(row.conversation_id, set()).add(row.user_id)
        candidates = [cid for cid, users in counts.items() if users == member_ids]
        if not candidates:
            return None
        conversations = await self.conversations.list(
            filters={"id": candidates, "kind": ConversationKind.DIRECT},
        )
        return conversations[0] if conversations else None

    async def get_conversation(
        self,
        conversation_id: UUID,
    ) -> ConversationResponseSchema:
        """Return a single conversation by id.

        Args:
            conversation_id (UUID): The conversation id.

        Returns:
            ConversationResponseSchema: The conversation, with its
            participants.

        Raises:
            AppException: The repository's not-found exception when no
                conversation matches.
        """
        conversation = await self.conversations.get_by_id(conversation_id)
        return await self._conversation_response(conversation)

    async def is_participant(self, conversation_id: UUID, user_id: UUID) -> bool:
        """Return whether ``user_id`` is an **active** member.

        Someone who left keeps their row — old messages still have to
        resolve a sender name — so membership is ``left_at IS NULL``,
        not mere existence of the row.

        Args:
            conversation_id (UUID): The conversation to check.
            user_id (UUID): The user to check.

        Returns:
            bool: ``True`` when the user may read and post.
        """
        row = await self._membership(conversation_id, user_id)
        return row is not None and row.left_at is None

    async def _membership(self, conversation_id: UUID, user_id: UUID) -> Any | None:
        """Return the participant row for a pair, active or not.

        Args:
            conversation_id (UUID): The conversation.
            user_id (UUID): The user.

        Returns:
            Any | None: The row, or ``None`` when they were never in it.
        """
        return await self.participants.get_or_none(
            {"conversation_id": conversation_id, "user_id": user_id},
        )

    async def _require_membership(self, conversation_id: UUID, user_id: UUID) -> Any:
        """Return the active participant row or refuse.

        Args:
            conversation_id (UUID): The conversation.
            user_id (UUID): The user.

        Returns:
            Any: The participant row.

        Raises:
            ForbiddenException: When the user is not an active member.
        """
        row = await self._membership(conversation_id, user_id)
        if row is None or row.left_at is not None:
            raise ForbiddenException(
                message="not a participant of this conversation",
                details={"conversation_id": str(conversation_id)},
            )
        return row

    async def list_conversations(
        self,
        user_id: UUID,
        *,
        include_archived: bool = False,
    ) -> list[ConversationResponseSchema]:
        """Return the conversations ``user_id`` is in, newest first.

        Pinned threads come first, then by newest message. Returns ``[]``
        when the user is in none, per the SDK collection convention.

        Args:
            user_id (UUID): The user whose conversations to list.
            include_archived (bool): Whether archived threads are
                included.

        Returns:
            list[ConversationResponseSchema]: The user's conversations,
            each carrying that user's ``unread_count``.
        """
        memberships = [
            row
            for row in await self.participants.list(filters={"user_id": user_id})
            if row.left_at is None and (include_archived or not row.is_archived)
        ]
        if not memberships:
            return []
        by_conversation = {row.conversation_id: row for row in memberships}
        rows = await self.conversations.list(
            filters={"id": list(by_conversation)},
        )
        responses: list[ConversationResponseSchema] = []
        for row in rows:
            membership = by_conversation[row.id]
            response = await self._conversation_response(row)
            response.unread_count = await self._unread_count(row.id, membership)
            responses.append(response)
        responses.sort(
            key=lambda item: (
                not by_conversation[item.id].is_pinned,
                -(item.last_message_at or item.created_at).timestamp(),
            ),
        )
        return responses

    async def _unread_count(self, conversation_id: UUID, membership: Any) -> int:
        """Count messages past this participant's read watermark.

        What *you* sent is never unread for you, so the sender's own
        messages are excluded — otherwise the badge lights up on the
        thread the moment you post in it, which is the one place a user
        is certain nothing is unread.

        Args:
            conversation_id (UUID): The conversation.
            membership (Any): That user's participant row.

        Returns:
            int: How many messages they have not read.
        """
        filters: dict[str, Any] = {
            "conversation_id": conversation_id,
            "sender_id__ne": membership.user_id,
        }
        if membership.last_read_at is not None:
            filters["created_at__gt"] = membership.last_read_at
        if membership.history_from is not None:
            filters["created_at__gte"] = membership.history_from
        return await self.messages.count(filters=filters)

    async def _conversation_response(
        self,
        conversation: Any,
    ) -> ConversationResponseSchema:
        """Build the response for one conversation row.

        Args:
            conversation (Any): The conversation row.

        Returns:
            ConversationResponseSchema: With participants attached.
        """
        response = ConversationResponseSchema.model_validate(conversation)
        rows = await self.participants.list(
            filters={"conversation_id": conversation.id},
        )
        response.participants = [
            ParticipantResponseSchema.model_validate(row) for row in rows
        ]
        return response

    async def post_message(
        self,
        conversation_id: UUID,
        sender_id: UUID,
        data: MessageCreateSchema | str,
    ) -> MessageResponseSchema:
        """Persist a message and publish it, idempotently.

        A retry carrying the ``client_id`` of a message that was already
        stored returns that message — the same row, not a second copy.
        That is the whole reason the id exists: a ``POST`` whose ``201``
        is lost leaves the client unable to tell whether the message
        landed.

        The id is unique per **sender**, not per conversation, because
        that is the uniqueness the table can enforce. So reusing one in a
        different conversation is refused rather than answered: returning
        the stored message would hand back a row belonging to another
        thread, with a ``201``, while the message the caller actually
        sent was never written.

        Args:
            conversation_id (UUID): The target conversation.
            sender_id (UUID): The sending user.
            data (MessageCreateSchema | str): The payload. A bare string
                is accepted as the body of a text message, which is what
                the previous signature took.

        Returns:
            MessageResponseSchema: The persisted message.

        Raises:
            ValidationException: When the message would carry nothing —
                no body and no attachments — or names attachments while
                the service has no attachment repository.
            NotFoundException: When ``reply_to_id`` names a message that
                is not in this conversation.
        """
        payload = MessageCreateSchema(body=data) if isinstance(data, str) else data
        if payload.client_id:
            existing = await self.messages.get_or_none(
                {"sender_id": sender_id, "client_id": payload.client_id},
            )
            if existing is not None:
                if existing.conversation_id != conversation_id:
                    raise ValidationException(
                        message="this client_id was already used for a message "
                        "in another conversation",
                        details={
                            "client_id": payload.client_id,
                            "conversation_id": str(existing.conversation_id),
                        },
                        field="client_id",
                    )
                return await self._message_response(existing)

        kind = MessageKind(payload.kind)
        if not payload.body and not payload.attachment_ids and kind is MessageKind.TEXT:
            raise ValidationException(
                message="a text message needs a body",
                field="body",
            )
        if payload.attachment_ids and self.attachments is None:
            raise ValidationException(
                message="this chat service was built without an attachment "
                "repository, so it cannot accept attachment_ids",
                field="attachment_ids",
            )
        if payload.reply_to_id is not None:
            parent = await self.messages.get_or_none({"id": payload.reply_to_id})
            if parent is None or parent.conversation_id != conversation_id:
                raise NotFoundException(
                    message="the message being replied to is not in this conversation",
                    details={"reply_to_id": str(payload.reply_to_id)},
                    field="reply_to_id",
                )

        row = await self.messages.add(
            self.messages.model(
                conversation_id=conversation_id,
                sender_id=sender_id,
                kind=kind,
                body=payload.body,
                client_id=payload.client_id,
                reply_to_id=payload.reply_to_id,
                payload=payload.payload,
            ),
        )
        await self._claim_attachments(row, payload.attachment_ids)
        await self._touch_conversation(conversation_id, row)
        message = await self._message_response(row)
        await self._publish(conversation_id, "message", message)
        return message

    async def _claim_attachments(self, message: Any, ids: list[UUID]) -> None:
        """Attach already-uploaded files to a freshly posted message.

        Upload and post are two calls on purpose: a 40 MB video that
        fails on the last byte must not take the caption with it, and a
        retried post must not re-send the file.

        Args:
            message (Any): The message row that claims them.
            ids (list[UUID]): Attachment ids, in render order.

        Raises:
            NotFoundException: When an id names no unclaimed attachment.
        """
        if not ids or self.attachments is None:
            return
        for position, attachment_id in enumerate(ids):
            row = await self.attachments.get_or_none({"id": attachment_id})
            if row is None or row.message_id is not None:
                raise NotFoundException(
                    message="unknown or already claimed attachment",
                    details={"attachment_id": str(attachment_id)},
                    field="attachment_ids",
                )
            row.message_id = message.id
            row.position = position
            await self.attachments.update(row)

    async def _touch_conversation(self, conversation_id: UUID, message: Any) -> None:
        """Move the conversation's denormalized newest-message pointer.

        Args:
            conversation_id (UUID): The conversation to move.
            message (Any): The message that moved it.
        """
        conversation = await self.conversations.get_or_none({"id": conversation_id})
        if conversation is None:
            return
        conversation.last_message_at = message.created_at
        conversation.last_message_id = message.id
        await self.conversations.update(conversation)

    async def edit_message(
        self,
        message_id: UUID,
        editor_id: UUID,
        body: str,
    ) -> MessageResponseSchema:
        """Replace a message's body and stamp ``edited_at``.

        Args:
            message_id (UUID): The message to edit.
            editor_id (UUID): Who is editing; must be the sender.
            body (str): The new text.

        Returns:
            MessageResponseSchema: The edited message.

        Raises:
            ForbiddenException: When the editor is not the sender.
            ValidationException: When the message was already revoked.
        """
        row = await self.messages.get_by_id(message_id)
        if row.sender_id != editor_id:
            raise ForbiddenException(message="only the sender can edit a message")
        if row.revoked_at is not None:
            raise ValidationException(
                message="a revoked message cannot be edited",
                field="body",
            )
        row.body = body
        row.edited_at = utcnow()
        await self.messages.update(row)
        message = await self._message_response(row)
        await self._publish(row.conversation_id, "message.edited", message)
        return message

    async def revoke_message(
        self,
        message_id: UUID,
        actor_id: UUID,
    ) -> tuple[MessageResponseSchema, list[str]]:
        """Delete a message for everyone, keeping it as a tombstone.

        The row survives so the thread keeps its shape and replies still
        have something to quote — but the body is **cleared**, not hidden
        behind a flag the next query forgets to filter, and the
        attachment rows go with it.

        Args:
            message_id (UUID): The message to revoke.
            actor_id (UUID): Who is revoking; must be the sender.

        Returns:
            tuple[MessageResponseSchema, list[str]]: The tombstone, and
            the storage keys the caller must now delete from storage —
            the SDK does not own the bucket, and a key left behind is a
            file that outlives the message that justified it.

        Raises:
            ForbiddenException: When the actor is not the sender.
        """
        row = await self.messages.get_by_id(message_id)
        if row.sender_id != actor_id:
            raise ForbiddenException(message="only the sender can revoke a message")
        row.body = ""
        row.payload = None
        row.revoked_at = utcnow()
        await self.messages.update(row)

        orphaned: list[str] = []
        if self.attachments is not None:
            for attachment in await self.attachments.list(
                filters={"message_id": message_id},
            ):
                orphaned.append(attachment.storage_key)
                if attachment.thumbnail_key:
                    orphaned.append(attachment.thumbnail_key)
                await self.attachments.delete(attachment.id)
        if self.reactions is not None:
            stale = await self.reactions.list(filters={"message_id": message_id})
            for reaction in stale:
                await self.reactions.delete(reaction.id)

        message = await self._message_response(row)
        await self._publish(row.conversation_id, "message.revoked", message)
        return message, orphaned

    async def react(
        self,
        message_id: UUID,
        user_id: UUID,
        emoji: str,
    ) -> MessageResponseSchema:
        """Set this user's reaction, replacing any previous one.

        One reaction per person: reacting again **replaces**. That is
        the ``(message_id, user_id)`` uniqueness — widening it to
        include the emoji is what turns a double-tap into two reactions.

        Args:
            message_id (UUID): The message reacted to.
            user_id (UUID): Who is reacting.
            emoji (str): The emoji.

        Returns:
            MessageResponseSchema: The message, with reactions rebuilt.

        Raises:
            ValidationException: When the service has no reaction
                repository.
        """
        if self.reactions is None:
            raise ValidationException(
                message="this chat service was built without a reaction repository",
                field="emoji",
            )
        row = await self.messages.get_by_id(message_id)
        existing = await self.reactions.get_or_none(
            {"message_id": message_id, "user_id": user_id},
        )
        if existing is not None:
            existing.emoji = emoji
            await self.reactions.update(existing)
        else:
            await self.reactions.add(
                self.reactions.model(
                    message_id=message_id,
                    user_id=user_id,
                    emoji=emoji,
                ),
            )
        message = await self._message_response(row)
        await self._publish(row.conversation_id, "message.reaction", message)
        return message

    async def unreact(self, message_id: UUID, user_id: UUID) -> MessageResponseSchema:
        """Remove this user's reaction, if any.

        Args:
            message_id (UUID): The message.
            user_id (UUID): Who is un-reacting.

        Returns:
            MessageResponseSchema: The message, with reactions rebuilt.

        Raises:
            ValidationException: When the service has no reaction
                repository.
        """
        if self.reactions is None:
            raise ValidationException(
                message="this chat service was built without a reaction repository",
            )
        row = await self.messages.get_by_id(message_id)
        existing = await self.reactions.get_or_none(
            {"message_id": message_id, "user_id": user_id},
        )
        if existing is not None:
            await self.reactions.delete(existing.id)
        message = await self._message_response(row)
        await self._publish(row.conversation_id, "message.reaction", message)
        return message

    async def forward(
        self,
        message_id: UUID,
        sender_id: UUID,
        conversation_ids: list[UUID],
    ) -> list[MessageResponseSchema]:
        """Copy a message into other conversations.

        The copy carries ``forwarded_from_id`` and a ``forward_score``
        one higher than the original's, which is what lets a client
        label content that has travelled far.

        Args:
            message_id (UUID): The message to forward.
            sender_id (UUID): Who is forwarding.
            conversation_ids (list[UUID]): Where to forward it.

        Returns:
            list[MessageResponseSchema]: One message per target, in the
            order given. ``[]`` when no target was given.

        Raises:
            ForbiddenException: When the sender is not a participant of
                a target conversation.
        """
        original = await self.messages.get_by_id(message_id)
        forwarded: list[MessageResponseSchema] = []
        for conversation_id in conversation_ids:
            await self._require_membership(conversation_id, sender_id)
            row = await self.messages.add(
                self.messages.model(
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    kind=MessageKind(original.kind),
                    body=original.body,
                    payload=original.payload,
                    forwarded_from_id=original.id,
                    forward_score=original.forward_score + 1,
                ),
            )
            await self._copy_attachments(original.id, row.id)
            await self._touch_conversation(conversation_id, row)
            message = await self._message_response(row)
            await self._publish(conversation_id, "message", message)
            forwarded.append(message)
        return forwarded

    async def _copy_attachments(self, source_id: UUID, target_id: UUID) -> None:
        """Point a forwarded message at the same stored files.

        A forward of an image that carried no attachment row renders as
        a media message with no media — the kind survives the copy and
        the file does not. New rows reference the **same**
        ``storage_key``: forwarding copies the message, never the bytes.

        Args:
            source_id (UUID): The message being forwarded.
            target_id (UUID): The copy that must carry the same files.
        """
        if self.attachments is None:
            return
        for item in await self.attachments.list(filters={"message_id": source_id}):
            await self.attachments.add(
                self.attachments.model(
                    message_id=target_id,
                    position=item.position,
                    storage_key=item.storage_key,
                    thumbnail_key=item.thumbnail_key,
                    filename=item.filename,
                    mime_type=item.mime_type,
                    size_bytes=item.size_bytes,
                    width=item.width,
                    height=item.height,
                    duration_ms=item.duration_ms,
                    waveform=item.waveform,
                ),
            )

    async def mark_read(
        self,
        conversation_id: UUID,
        user_id: UUID,
        *,
        message_id: UUID | None = None,
    ) -> ParticipantResponseSchema:
        """Move this participant's read watermark.

        One ``UPDATE`` on one row, not a receipt row per message per
        reader: in a group of 200 that would be 200 inserts per message.

        Args:
            conversation_id (UUID): The conversation being read.
            user_id (UUID): The reader.
            message_id (UUID | None): Read up to and including this
                message; ``None`` reads everything as of now.

        Returns:
            ParticipantResponseSchema: The updated membership row.

        Raises:
            ForbiddenException: When the user is not an active member.
            NotFoundException: When ``message_id`` is not in this
                conversation.
        """
        membership = await self._require_membership(conversation_id, user_id)
        read_at: datetime = utcnow()
        if message_id is not None:
            message = await self.messages.get_or_none({"id": message_id})
            if message is None or message.conversation_id != conversation_id:
                raise NotFoundException(
                    message="that message is not in this conversation",
                    details={"message_id": str(message_id)},
                    field="message_id",
                )
            read_at = message.created_at
            membership.last_read_message_id = message.id
        if membership.last_read_at is None or membership.last_read_at < read_at:
            membership.last_read_at = read_at
        delivered = membership.last_delivered_at
        if delivered is None or delivered < read_at:
            membership.last_delivered_at = read_at
        await self.participants.update(membership)
        response = ParticipantResponseSchema.model_validate(membership)
        await self._publish(conversation_id, "conversation.read", response)
        return response

    async def mark_delivered(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> ParticipantResponseSchema:
        """Move this participant's delivery watermark to now.

        Args:
            conversation_id (UUID): The conversation.
            user_id (UUID): The recipient whose client just connected.

        Returns:
            ParticipantResponseSchema: The updated membership row.

        Raises:
            ForbiddenException: When the user is not an active member.
        """
        membership = await self._require_membership(conversation_id, user_id)
        membership.last_delivered_at = utcnow()
        await self.participants.update(membership)
        return ParticipantResponseSchema.model_validate(membership)

    async def receipt_counts(self, message: Any) -> ReceiptCountsSchema:
        """Count who has received and read one message.

        Derived from the watermarks — a participant has read the message
        when ``last_read_at >= message.created_at`` — so there is no
        per-message, per-reader row to count.

        Args:
            message (Any): The message row.

        Returns:
            ReceiptCountsSchema: Delivered / read / total counts,
            excluding the sender.
        """
        members = await self.participants.list(
            filters={"conversation_id": message.conversation_id},
        )
        return self._compose_receipts(message, list(members))

    def _compose_receipts(
        self,
        message: Any,
        members: list[Any],
    ) -> ReceiptCountsSchema:
        """Count receipts from participant rows already loaded.

        Args:
            message (Any): The message row.
            members (list[Any]): Every participant row of its
                conversation, sender included — it is filtered here.

        Returns:
            ReceiptCountsSchema: Delivered / read / total counts.
        """
        rows = [row for row in members if row.user_id != message.sender_id]
        delivered = sum(
            1
            for row in rows
            if row.last_delivered_at is not None
            and row.last_delivered_at >= message.created_at
        )
        read = sum(
            1
            for row in rows
            if row.last_read_at is not None and row.last_read_at >= message.created_at
        )
        return ReceiptCountsSchema(delivered=delivered, read=read, total=len(rows))

    async def list_messages(
        self,
        conversation_id: UUID,
        *,
        user_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
        ascending: bool = True,
        with_receipts: bool = False,
    ) -> dict[str, Any]:
        """Return an offset page of a conversation's messages.

        When ``user_id`` is given, the page respects that participant's
        ``history_from`` — someone added to a group later does not
        inherit the backlog unless the product says so.

        Args:
            conversation_id (UUID): The conversation to page.
            user_id (UUID | None): The reader, for ``history_from``.
            page (int): 1-indexed page number.
            page_size (int): Messages per page.
            ascending (bool): Oldest-first when ``True`` (chat order).
            with_receipts (bool): Attach delivery/read counts to each
                message.

        Returns:
            dict[str, Any]: ``items`` (mapped
            :class:`MessageResponseSchema`), ``total``, ``page``,
            ``page_size`` and ``pages``.
        """
        filters: dict[str, Any] = {"conversation_id": conversation_id}
        if user_id is not None:
            membership = await self._membership(conversation_id, user_id)
            if membership is not None and membership.history_from is not None:
                filters["created_at__gte"] = membership.history_from
        result = await self.messages.paginate(
            filters=filters,
            order_by="created_at",
            page=page,
            page_size=page_size,
            ascending=ascending,
        )
        items = await self._message_responses(
            result["items"],
            with_receipts=with_receipts,
        )
        return {**result, "items": items}

    async def _message_responses(
        self,
        rows: Sequence[Any],
        *,
        with_receipts: bool = False,
    ) -> list[MessageResponseSchema]:
        """Build the responses for a whole page in a fixed number of queries.

        Resolving each message on its own is four queries per row — its
        attachments, its reactions, the parent it quotes and (with
        receipts) the participant list. Measured on a 20-message page
        that was **42 statements**; batched it is four, whatever the page
        size, because every lookup is one ``IN`` over the page's ids.

        Args:
            rows (Sequence[Any]): The message rows of one page, all from
                the same conversation.
            with_receipts (bool): Attach delivery/read counts.

        Returns:
            list[MessageResponseSchema]: The responses, in input order.
        """
        if not rows:
            return []
        ids = [row.id for row in rows]
        attachments: dict[UUID, list[Any]] = {}
        if self.attachments is not None:
            for item in await self.attachments.list(filters={"message_id": ids}):
                attachments.setdefault(item.message_id, []).append(item)
        reactions: dict[UUID, list[Any]] = {}
        if self.reactions is not None:
            for item in await self.reactions.list(filters={"message_id": ids}):
                reactions.setdefault(item.message_id, []).append(item)
        parent_ids = [row.reply_to_id for row in rows if row.reply_to_id is not None]
        parents: dict[UUID, Any] = {}
        if parent_ids:
            for parent in await self.messages.list(filters={"id": parent_ids}):
                parents[parent.id] = parent
        members: list[Any] = []
        if with_receipts:
            members = await self.participants.list(
                filters={"conversation_id": rows[0].conversation_id},
            )
        return [
            self._compose_message(
                row,
                attachments=attachments.get(row.id, []),
                reactions=reactions.get(row.id, []),
                parent=parents.get(row.reply_to_id),
                members=members if with_receipts else None,
            )
            for row in rows
        ]

    def _compose_message(
        self,
        row: Any,
        *,
        attachments: list[Any],
        reactions: list[Any],
        parent: Any | None,
        members: list[Any] | None,
    ) -> MessageResponseSchema:
        """Assemble one response from rows already in memory.

        Args:
            row (Any): The message row.
            attachments (list[Any]): Its attachment rows.
            reactions (list[Any]): Its reaction rows.
            parent (Any | None): The message it quotes, if any.
            members (list[Any] | None): Participant rows, when receipts
                were asked for.

        Returns:
            MessageResponseSchema: The complete response.
        """
        message = MessageResponseSchema.model_validate(row)
        message.reply_to = self._compose_reply_preview(row.reply_to_id, parent)
        if self.attachments is not None:
            message.attachments = [
                AttachmentResponseSchema.model_validate(item)
                for item in sorted(attachments, key=lambda item: item.position)
            ]
        if self.reactions is not None:
            message.reactions = self._compose_reactions(reactions)
        if members is not None:
            message.receipts = self._compose_receipts(row, members)
        return message

    async def _message_response(
        self,
        row: Any,
        *,
        with_receipts: bool = False,
    ) -> MessageResponseSchema:
        """Build the full response for one message row.

        Args:
            row (Any): The message row.
            with_receipts (bool): Whether to attach receipt counts.

        Returns:
            MessageResponseSchema: With reply stub, attachments and
            reactions resolved.
        """
        attachments: list[Any] = []
        if self.attachments is not None:
            attachments = list(
                await self.attachments.list(filters={"message_id": row.id}),
            )
        reactions: list[Any] = []
        if self.reactions is not None:
            reactions = list(await self.reactions.list(filters={"message_id": row.id}))
        parent = (
            None
            if row.reply_to_id is None
            else await self.messages.get_or_none({"id": row.reply_to_id})
        )
        members: list[Any] | None = None
        if with_receipts:
            members = await self.participants.list(
                filters={"conversation_id": row.conversation_id},
            )
        return self._compose_message(
            row,
            attachments=attachments,
            reactions=reactions,
            parent=parent,
            members=members,
        )

    def _compose_reply_preview(
        self,
        reply_to_id: UUID | None,
        parent: Any | None,
    ) -> ReplyPreviewSchema | None:
        """Build the stub a reply quotes, from a row already loaded.

        A revoked parent yields an empty excerpt with ``revoked=True``:
        deleting the quoted message has to delete the quote too, or the
        text survives inside every reply to it.

        Args:
            reply_to_id (UUID | None): The quoted message's id.
            parent (Any | None): That message's row, when it still
                exists.

        Returns:
            ReplyPreviewSchema | None: The stub, or ``None``.
        """
        if reply_to_id is None or parent is None:
            return None
        revoked = parent.revoked_at is not None
        return ReplyPreviewSchema(
            id=parent.id,
            sender_id=parent.sender_id,
            kind=MessageKind(parent.kind),
            excerpt="" if revoked else parent.body[:REPLY_EXCERPT_LENGTH],
            revoked=revoked,
        )

    def _compose_reactions(self, rows: list[Any]) -> list[ReactionSummarySchema]:
        """Group reaction rows by emoji.

        Args:
            rows (list[Any]): The reaction rows of one message.

        Returns:
            list[ReactionSummarySchema]: One entry per emoji, most
            reacted first.
        """
        grouped: dict[str, list[UUID]] = {}
        for row in rows:
            grouped.setdefault(row.emoji, []).append(row.user_id)
        summaries = [
            ReactionSummarySchema(emoji=emoji, count=len(users), user_ids=users)
            for emoji, users in grouped.items()
        ]
        summaries.sort(key=lambda item: (-item.count, item.emoji))
        return summaries

    async def _system_message(
        self,
        conversation_id: UUID,
        *,
        actor_id: UUID,
        event: SystemEvent,
        user_ids: list[UUID] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> MessageResponseSchema:
        """Post a ``SYSTEM`` notice with a structured payload.

        The client renders from ``payload`` — so the notice is localized
        and the names are links — and the ``body`` the server writes is
        the fallback for push and search, never the primary form.

        Args:
            conversation_id (UUID): Where to post it.
            actor_id (UUID): Who caused the event.
            event (SystemEvent): What happened.
            user_ids (list[UUID] | None): Who it happened to.
            extra (dict[str, Any] | None): Event-specific extras.

        Returns:
            MessageResponseSchema: The posted notice.
        """
        payload: dict[str, Any] = {
            "event": str(event),
            "actor_id": str(actor_id),
            "user_ids": [str(user_id) for user_id in (user_ids or [])],
        }
        if extra:
            payload.update(extra)
        row = await self.messages.add(
            self.messages.model(
                conversation_id=conversation_id,
                sender_id=actor_id,
                kind=MessageKind.SYSTEM,
                body=str(event),
                payload=payload,
            ),
        )
        await self._touch_conversation(conversation_id, row)
        message = await self._message_response(row)
        await self._publish(conversation_id, "message", message)
        return message

    async def add_participants(
        self,
        conversation_id: UUID,
        actor_id: UUID,
        user_ids: list[UUID],
        *,
        share_history: bool = False,
    ) -> ConversationResponseSchema:
        """Add members to a group and post the system notice.

        A member who joins does **not** inherit the backlog by default:
        ``history_from`` is stamped at the moment they join, so the group
        decides what a newcomer can read rather than the absence of a
        column deciding it.

        Args:
            conversation_id (UUID): The group.
            actor_id (UUID): Who is adding; must be admin or owner.
            user_ids (list[UUID]): Who to add.
            share_history (bool): Hand over the whole backlog instead.

        Returns:
            ConversationResponseSchema: The updated conversation.

        Raises:
            ForbiddenException: When the actor may not add members.
            ValidationException: When the conversation is a direct
                thread.
        """
        conversation = await self.conversations.get_by_id(conversation_id)
        if ConversationKind(conversation.kind) is ConversationKind.DIRECT:
            raise ValidationException(
                message="a direct conversation cannot take more participants",
            )
        await self._require_admin(conversation_id, actor_id)
        now = utcnow()
        added: list[UUID] = []
        for user_id in user_ids:
            existing = await self._membership(conversation_id, user_id)
            if existing is not None and existing.left_at is None:
                continue
            if existing is not None:
                existing.left_at = None
                existing.joined_at = now
                existing.history_from = None if share_history else now
                await self.participants.update(existing)
            else:
                await self.participants.add(
                    self.participants.model(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        role=ParticipantRole.MEMBER,
                        joined_at=now,
                        history_from=None if share_history else now,
                    ),
                )
            added.append(user_id)
        if added:
            await self._system_message(
                conversation_id,
                actor_id=actor_id,
                event=SystemEvent.PARTICIPANTS_ADDED,
                user_ids=added,
            )
        return await self.get_conversation(conversation_id)

    async def remove_participant(
        self,
        conversation_id: UUID,
        actor_id: UUID,
        user_id: UUID,
    ) -> ConversationResponseSchema:
        """Remove a member, keeping their row as a tombstone.

        Args:
            conversation_id (UUID): The group.
            actor_id (UUID): Who is removing; must be admin or owner.
            user_id (UUID): Who to remove.

        Returns:
            ConversationResponseSchema: The updated conversation.

        Raises:
            ForbiddenException: When the actor may not remove members,
                or the target is the owner.
        """
        await self._require_admin(conversation_id, actor_id)
        membership = await self._require_membership(conversation_id, user_id)
        if ParticipantRole(membership.role) is ParticipantRole.OWNER:
            raise ForbiddenException(message="the owner cannot be removed")
        membership.left_at = utcnow()
        await self.participants.update(membership)
        await self._system_message(
            conversation_id,
            actor_id=actor_id,
            event=SystemEvent.PARTICIPANT_REMOVED,
            user_ids=[user_id],
        )
        return await self.get_conversation(conversation_id)

    async def leave(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> ConversationResponseSchema:
        """Leave a conversation on your own.

        Args:
            conversation_id (UUID): The conversation to leave.
            user_id (UUID): Who is leaving.

        Returns:
            ConversationResponseSchema: The updated conversation.

        Raises:
            ForbiddenException: When the user is not an active member.
        """
        membership = await self._require_membership(conversation_id, user_id)
        membership.left_at = utcnow()
        await self.participants.update(membership)
        await self._system_message(
            conversation_id,
            actor_id=user_id,
            event=SystemEvent.PARTICIPANT_LEFT,
            user_ids=[user_id],
        )
        return await self.get_conversation(conversation_id)

    async def set_role(
        self,
        conversation_id: UUID,
        actor_id: UUID,
        user_id: UUID,
        role: ParticipantRole | str,
    ) -> ParticipantResponseSchema:
        """Promote or demote a participant.

        Args:
            conversation_id (UUID): The group.
            actor_id (UUID): Who is changing it; must be admin or owner.
            user_id (UUID): Whose role changes.
            role (ParticipantRole | str): The new role.

        Returns:
            ParticipantResponseSchema: The updated membership.

        Raises:
            ForbiddenException: When the actor may not change roles.
        """
        await self._require_admin(conversation_id, actor_id)
        membership = await self._require_membership(conversation_id, user_id)
        membership.role = ParticipantRole(role)
        await self.participants.update(membership)
        await self._system_message(
            conversation_id,
            actor_id=actor_id,
            event=SystemEvent.ROLE_CHANGED,
            user_ids=[user_id],
            extra={"role": str(ParticipantRole(role))},
        )
        return ParticipantResponseSchema.model_validate(membership)

    async def _require_admin(self, conversation_id: UUID, user_id: UUID) -> Any:
        """Return the participant row when it may administer the group.

        Args:
            conversation_id (UUID): The group.
            user_id (UUID): The acting user.

        Returns:
            Any: The participant row.

        Raises:
            ForbiddenException: When the user is only a member.
        """
        membership = await self._require_membership(conversation_id, user_id)
        if ParticipantRole(membership.role) is ParticipantRole.MEMBER:
            raise ForbiddenException(
                message="only an admin or the owner can do that",
                details={"conversation_id": str(conversation_id)},
            )
        return membership

    async def update_conversation(
        self,
        conversation_id: UUID,
        actor_id: UUID,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> ConversationResponseSchema:
        """Edit a group's title or description.

        Args:
            conversation_id (UUID): The group.
            actor_id (UUID): Who is editing; must be admin or owner.
            title (str | None): New title, when given.
            description (str | None): New description, when given.

        Returns:
            ConversationResponseSchema: The updated conversation.

        Raises:
            ForbiddenException: When the actor may not edit the group.
        """
        await self._require_admin(conversation_id, actor_id)
        conversation = await self.conversations.get_by_id(conversation_id)
        if title is not None:
            conversation.title = title
            await self.conversations.update(conversation)
            await self._system_message(
                conversation_id,
                actor_id=actor_id,
                event=SystemEvent.TITLE_CHANGED,
                extra={"title": title},
            )
        if description is not None:
            conversation.description = description
            await self.conversations.update(conversation)
            await self._system_message(
                conversation_id,
                actor_id=actor_id,
                event=SystemEvent.DESCRIPTION_CHANGED,
            )
        return await self.get_conversation(conversation_id)

    async def set_preferences(
        self,
        conversation_id: UUID,
        user_id: UUID,
        *,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
        muted_until: datetime | None = None,
    ) -> ParticipantResponseSchema:
        """Change this user's own view of a conversation.

        Pin, archive and mute live on the membership row, so they apply
        to one inbox. Written on the conversation they would mute the
        thread for everyone in it.

        Args:
            conversation_id (UUID): The conversation.
            user_id (UUID): Whose preferences change.
            is_pinned (bool | None): Pin or unpin.
            is_archived (bool | None): Archive or unarchive.
            muted_until (datetime | None): Mute until this instant.

        Returns:
            ParticipantResponseSchema: The updated membership row.

        Raises:
            ForbiddenException: When the user is not an active member.
        """
        membership = await self._require_membership(conversation_id, user_id)
        if is_pinned is not None:
            membership.is_pinned = is_pinned
        if is_archived is not None:
            membership.is_archived = is_archived
        if muted_until is not None:
            membership.muted_until = muted_until
        await self.participants.update(membership)
        return ParticipantResponseSchema.model_validate(membership)

    async def _publish(self, conversation_id: UUID, event: str, data: Any) -> None:
        """Publish one change to the conversation's SSE channel.

        Args:
            conversation_id (UUID): The channel.
            event (str): The SSE event name.
            data (Any): A schema to serialize.
        """
        if self.broker is None:
            return
        await self.broker.publish(
            str(conversation_id),
            data=data.model_dump(mode="json"),
            event=event,
        )


__all__: list[str] = [
    "ChatService",
]
