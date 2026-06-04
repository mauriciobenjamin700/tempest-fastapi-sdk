"""``SessionAuth`` — orchestrates login / logout / rotate on top of a store.

Pairs the password-verify path from
:class:`tempest_fastapi_sdk.PasswordUtils` with a pluggable
:class:`SessionStore` to model an end-to-end session lifecycle:

* ``authenticate(session, email, password)`` — bcrypt-verify the
  credentials against the project's ``UserModel``.
* ``login(user_id, ip=, user_agent=, previous_session_id=)`` —
  mint a fresh session, optionally evict an old one (session-id
  rotation against fixation).
* ``resolve(session_id_plaintext)`` — turn a cookie value into a
  live :class:`Session`, applying the sliding-TTL refresh when
  configured.
* ``revoke(session_id_plaintext)`` — invalidate one session.
* ``revoke_all(user_id)`` — invalidate every session a user owns
  (global logout, password-change wipe, etc.).
* ``list_sessions(user_id, current_session_id=)`` — produce the
  "active devices" summary the UI displays.

The service mints opaque session ids via
:func:`tempest_fastapi_sdk.generate_opaque_token` and stores only
the SHA-256 hash — a leak of the store yields no reusable
sessions. The plaintext is shown to the caller once, who is
responsible for sending it as a cookie.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.exceptions import (
    NotFoundException,
    UnauthorizedException,
)
from tempest_fastapi_sdk.sessions.schemas import Session, SessionSummarySchema
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.utils.opaque_token import (
    generate_opaque_token,
    hash_opaque_token,
)
from tempest_fastapi_sdk.utils.password import PasswordUtils

if TYPE_CHECKING:
    from uuid import UUID

    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.sessions.store import SessionStore
    from tempest_fastapi_sdk.settings.mixins import SessionSettings


class SessionAuth:
    """Server-side session lifecycle orchestrator.

    Mount one instance per ``FastAPI`` app. Stateless — the only
    state lives in the injected :class:`SessionStore` and the
    project's ``UserModel`` table.
    """

    def __init__(
        self,
        *,
        user_model: type[BaseUserModel],
        store: SessionStore,
        settings: SessionSettings,
        passwords: PasswordUtils | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            user_model (type[BaseUserModel]): Concrete user model
                (typically ``src.db.models.UserModel``) used to
                resolve email → password hash on ``authenticate``.
            store (SessionStore): Persistence backend
                (:class:`MemorySessionStore` or
                :class:`RedisSessionStore`).
            settings (SessionSettings): TTL / cookie / rotation
                flags driving the lifecycle.
            passwords (PasswordUtils | None): Override for tests;
                defaults to a fresh ``PasswordUtils()``.
        """
        self.user_model: type[BaseUserModel] = user_model
        self.store: SessionStore = store
        self.settings: SessionSettings = settings
        self.passwords: PasswordUtils = passwords or PasswordUtils()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
    ) -> BaseUserModel:
        """Validate credentials and return the matching user row.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Account email.
            password (str): Plaintext password.

        Returns:
            BaseUserModel: The authenticated user.

        Raises:
            UnauthorizedException: On any failure — wrong password,
                missing user, inactive user. The message is
                deliberately generic so attackers cannot enumerate
                accounts via timing or wording.
        """
        normalized = email.strip().lower()
        result = await session.execute(
            select(self.user_model).where(self.user_model.email == normalized),
        )
        user_obj = result.scalar_one_or_none()
        if user_obj is None or not user_obj.is_active:
            raise UnauthorizedException(message="invalid email or password")
        if not self.passwords.verify(password, user_obj.hashed_password):
            raise UnauthorizedException(message="invalid email or password")
        user_obj.last_login_at = utcnow()
        await session.flush()
        await session.refresh(user_obj)
        return user_obj

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def login(
        self,
        *,
        user_id: UUID,
        ip: str | None = None,
        user_agent: str | None = None,
        previous_session_id: str | None = None,
    ) -> tuple[Session, str]:
        """Mint a brand-new session for ``user_id``.

        When :attr:`SessionSettings.SESSION_ROTATE_ON_LOGIN` is
        ``True`` (default) and ``previous_session_id`` is provided,
        the previous session is evicted before the new one is
        issued — closes the session-fixation attack window.

        Args:
            user_id (UUID): The user the session belongs to.
            ip (str | None): Client IP (resolve via
                :func:`tempest_fastapi_sdk.get_client_ip`).
            user_agent (str | None): Raw User-Agent header.
            previous_session_id (str | None): Plaintext cookie value
                of the session being replaced. Pass it when a
                request already carries a session cookie — typical
                during step-up login. Ignored when
                ``SESSION_ROTATE_ON_LOGIN`` is ``False``.

        Returns:
            tuple[Session, str]: The persisted session row and the
            plaintext id to ship via ``Set-Cookie``. The plaintext
            is **not** persisted — losing it means logging the user
            out.
        """
        if self.settings.SESSION_ROTATE_ON_LOGIN and previous_session_id is not None:
            await self.revoke(previous_session_id)
        plaintext, session_hash = generate_opaque_token()
        now = utcnow()
        session = Session(
            session_id=session_hash,
            user_id=user_id,
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.SESSION_TTL_SECONDS),
            last_seen_at=now,
            ip=ip,
            user_agent=user_agent,
            data={},
        )
        await self.store.set(session)
        return session, plaintext

    async def resolve(self, session_id_plaintext: str) -> Session | None:
        """Look up + (optionally) refresh a session by its cookie value.

        Returns ``None`` when the cookie does not match a live
        session — middleware / dependencies treat that as
        "unauthenticated".

        Args:
            session_id_plaintext (str): Raw cookie value.

        Returns:
            Session | None: The resolved session, or ``None``.
        """
        session_hash = hash_opaque_token(session_id_plaintext)
        session = await self.store.get(session_hash)
        if session is None:
            return None
        now = utcnow()
        session.last_seen_at = now
        if self.settings.SESSION_SLIDING:
            session.expires_at = now + timedelta(
                seconds=self.settings.SESSION_TTL_SECONDS,
            )
        await self.store.set(session)
        return session

    async def revoke(self, session_id_plaintext: str) -> None:
        """Invalidate one session by its plaintext id. Idempotent."""
        session_hash = hash_opaque_token(session_id_plaintext)
        await self.store.delete(session_hash)

    async def revoke_all(self, user_id: UUID) -> int:
        """Invalidate every session for ``user_id``. Returns count revoked."""
        return await self.store.delete_by_user(user_id)

    async def list_sessions(
        self,
        user_id: UUID,
        *,
        current_session_id_plaintext: str | None = None,
    ) -> list[SessionSummarySchema]:
        """Return public-safe summaries of ``user_id``'s sessions.

        Args:
            user_id (UUID): Owner whose sessions to list.
            current_session_id_plaintext (str | None): Optional
                plaintext cookie of the session resolving the
                current request — used to set ``is_current=True``
                on the matching row.

        Returns:
            list[SessionSummarySchema]: One entry per live
            session, oldest first.
        """
        current_hash: str | None = None
        if current_session_id_plaintext is not None:
            current_hash = hash_opaque_token(current_session_id_plaintext)
        sessions = await self.store.list_by_user(user_id)
        summaries: list[SessionSummarySchema] = []
        for session in sessions:
            summaries.append(
                SessionSummarySchema(
                    id=session.session_id[:32],
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                    last_seen_at=session.last_seen_at,
                    ip=session.ip,
                    user_agent=session.user_agent,
                    is_current=(session.session_id == current_hash),
                )
            )
        return summaries

    async def revoke_by_public_id(self, user_id: UUID, public_id: str) -> None:
        """Revoke one session belonging to ``user_id`` via its public id.

        The public id is the 32-char prefix of the hashed session
        id (see :class:`SessionSummarySchema`). The match scans the
        user's live sessions — fast in practice because users
        rarely have more than a handful.

        Raises:
            NotFoundException: When no live session of ``user_id``
                matches ``public_id``.
        """
        sessions = await self.store.list_by_user(user_id)
        for session in sessions:
            if session.session_id.startswith(public_id):
                await self.store.delete(session.session_id)
                return
        raise NotFoundException(message="session not found")


__all__: list[str] = ["SessionAuth"]
