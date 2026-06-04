"""Schemas for the server-side session module."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import EmailStr, Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class Session(BaseSchema):
    """A live server-side session.

    Stored in the configured :class:`SessionStore` keyed by the
    SHA-256 hash of the session id (the plaintext lives only in the
    cookie). Mirrors what every session-backed auth flow needs:
    user identity, lifetime bounds, originating client metadata for
    revocation UX ("you're signed in on Chrome from São Paulo"),
    and a free-form ``data`` bag for app-level state.

    Attributes:
        session_id (str): SHA-256 hex digest of the cookie value —
            **NOT** the plaintext. The plaintext leaves over
            ``Set-Cookie`` exactly once.
        user_id (UUID): Owner of the session.
        created_at (datetime): UTC timestamp when the session was
            issued.
        expires_at (datetime): UTC timestamp after which the
            session is rejected. Refreshed by
            :meth:`SessionAuth.touch` when sliding TTLs are in
            effect.
        last_seen_at (datetime): UTC timestamp of the last request
            that resolved the session. Updated by the middleware on
            every hit.
        ip (str | None): Client IP recorded at session creation.
            Useful for the "list active sessions" UI.
        user_agent (str | None): User-Agent header recorded at
            session creation.
        data (dict[str, Any]): Arbitrary JSON-serializable bag —
            shopping cart id, last-seen route, locale preference,
            etc.
    """

    session_id: str = Field(
        ...,
        title="Session id (hashed)",
        description=(
            "SHA-256 hex digest of the cookie value. The plaintext "
            "is shown to the client exactly once via ``Set-Cookie``."
        ),
        examples=["3b1a…(64-char hex)"],
    )
    user_id: UUID = Field(
        ...,
        title="Owner user id",
        description="UUID of the user the session belongs to.",
        examples=["0193e9ea-7c4b-7c8e-bc05-2a3a8d9f7e10"],
    )
    created_at: datetime = Field(
        ...,
        title="Created at",
        description="UTC timestamp when the session was issued.",
    )
    expires_at: datetime = Field(
        ...,
        title="Expires at",
        description=(
            "UTC timestamp after which the session is rejected. "
            "Touched by :meth:`SessionAuth.touch` when sliding TTL "
            "is enabled."
        ),
    )
    last_seen_at: datetime = Field(
        ...,
        title="Last seen at",
        description="UTC timestamp of the last request that resolved the session.",
    )
    ip: str | None = Field(
        default=None,
        title="Client IP",
        description="IP captured at session creation (None when not available).",
        examples=[None, "203.0.113.10"],
    )
    user_agent: str | None = Field(
        default=None,
        title="Client User-Agent",
        description=(
            "User-Agent header captured at session creation (None when not available)."
        ),
        examples=[None, "Mozilla/5.0 (Macintosh; …)"],
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        title="App-level data bag",
        description=(
            "Free-form JSON payload owned by the application — cart "
            "ID, locale preference, etc. The SDK never reads or "
            "writes the contents."
        ),
        examples=[{}, {"cart_id": "01HE…", "locale": "pt-BR"}],
    )


class SessionLoginSchema(BaseSchema):
    """Payload for ``POST /auth/session/login``."""

    email: EmailStr = Field(
        ...,
        title="Email",
        description="Account email — normalized to lowercase server-side.",
        examples=["ana@example.com"],
    )
    password: str = Field(
        ...,
        min_length=1,
        title="Password",
        description=(
            "Plaintext password. Validated server-side against the bcrypt hash."
        ),
        examples=["strong-pass-12-chars"],
    )


class SessionResponseSchema(BaseSchema):
    """Body returned by ``POST /auth/session/login``.

    The session id itself is delivered via ``Set-Cookie`` —
    deliberately NOT in this body — so JavaScript cannot read it
    (HttpOnly cookies). The body carries everything the frontend
    actually needs to render an authenticated state.
    """

    user_id: UUID = Field(
        ...,
        title="User id",
        description="UUID of the authenticated user.",
        examples=["0193e9ea-7c4b-7c8e-bc05-2a3a8d9f7e10"],
    )
    expires_at: datetime = Field(
        ...,
        title="Session expiry",
        description="UTC timestamp when the cookie stops being accepted.",
    )


class SessionSummarySchema(BaseSchema):
    """Public-safe projection of a :class:`Session` used by list endpoints.

    Drops ``session_id`` (so revealing the list does NOT leak any
    secret) and renames the visible identifier to ``id`` — a stable
    UUID derived from the hashed session id by truncation, suitable
    for ``DELETE /auth/session/{id}`` revocation calls.
    """

    id: str = Field(
        ...,
        title="Public session id (revocation handle)",
        description=(
            "Stable identifier safe to expose to the client — "
            "first 32 hex chars of the hashed session id. "
            "Use it as the path param of ``DELETE /auth/session/{id}``."
        ),
        examples=["3b1a8d9c2e7f4a6b8d0e1f3a5c7b9d2e"],
    )
    created_at: datetime = Field(..., title="Created at")
    expires_at: datetime = Field(..., title="Expires at")
    last_seen_at: datetime = Field(..., title="Last seen at")
    ip: str | None = Field(default=None, title="Client IP")
    user_agent: str | None = Field(default=None, title="Client User-Agent")
    is_current: bool = Field(
        ...,
        title="Is the request's own session",
        description=(
            "``True`` when the listed session is the one resolving "
            "this very request — handy for UIs that mark the "
            "current device."
        ),
        examples=[True, False],
    )


__all__: list[str] = [
    "Session",
    "SessionLoginSchema",
    "SessionResponseSchema",
    "SessionSummarySchema",
]
