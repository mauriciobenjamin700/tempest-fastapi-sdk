"""DB-backed refresh-token table for the bundled auth flow.

Stores **opaque** refresh tokens so the bundled auth flow can do
real rotation, revocation and reuse detection — features a
stateless JWT refresh token cannot offer:

* **Rotation** — every successful ``POST /auth/refresh`` marks the
  presented token ``used_at`` and mints a brand-new token in the
  same *family*. The old token is single-use.
* **Reuse detection** — replaying an already-rotated token is the
  classic stolen-token signal. The flow revokes the **entire
  family** (every token descended from the same login), forcing
  both the attacker and the victim to re-authenticate.
* **Revocation** — logout flips ``revoked_at`` on the family (or
  on every session of the user), so a refresh token can be killed
  before its natural expiry.

The SDK never stores the plaintext token — only its SHA-256 hash
via :func:`tempest_fastapi_sdk.hash_opaque_token`. The plaintext
is returned exactly once (at issuance) so the client can store it.

Concrete subclasses live in the consuming application so the
table joins the project's metadata and Alembic emits it under the
application's naming convention. The pattern mirrors
:class:`BaseUserTokenModel` and :class:`BaseUserRecoveryCodeModel`
— the SDK ships the abstract row, the project ships the concrete
table mapping. The whole flow is **opt-in**: pass the concrete
model as ``refresh_token_model=`` to
:class:`tempest_fastapi_sdk.UserAuthService`; omit it and the
service keeps issuing the legacy stateless JWT refresh token.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import TIMESTAMP, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseUserRefreshTokenModel(BaseModel):
    """Abstract opaque-refresh-token row used by the bundled auth flow.

    Concrete subclasses pick the ``__tablename__``
    (``user_refresh_tokens`` by convention) and add the FK to the
    project's concrete ``UserModel``. The SDK never sees the
    plaintext after the issuance response — only the hash is
    persisted.

    Attributes:
        user_id (UUID): Owner of the token. Concrete subclasses
            MUST declare this as a ``ForeignKey`` so cascading
            deletes wipe the tokens alongside the user.
        token_hash (str): SHA-256 hex digest of the plaintext
            refresh token. Indexed + unique so lookups by hash are
            fast and duplicates impossible.
        family_id (UUID): Rotation lineage. Every token minted by
            rotating an existing one inherits its ``family_id``; a
            brand-new login starts a fresh family. Reuse detection
            and logout operate on the whole family at once.
        expires_at (datetime): UTC timestamp the token becomes
            invalid. Defaults to ``JWT_REFRESH_TTL_SECONDS`` after
            issuance.
        used_at (datetime | None): UTC timestamp the token was
            rotated (consumed by a successful refresh). Non-null
            means the token is spent — replaying it triggers
            reuse detection.
        revoked_at (datetime | None): UTC timestamp the token was
            explicitly revoked (logout or family-wide reuse
            kill). Non-null means the token is dead regardless of
            its expiry.
    """

    __abstract__ = True

    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the user this token belongs to (set by subclass).",
    )
    token_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
        doc="SHA-256 hex digest of the plaintext refresh token.",
    )
    family_id: Mapped[UUID] = mapped_column(
        Uuid(),
        nullable=False,
        index=True,
        doc="Rotation lineage — reuse detection / logout act on the family.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        doc="UTC timestamp the token expires at.",
    )
    used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="UTC timestamp the token was rotated (single-use).",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="UTC timestamp the token was revoked (logout / reuse kill).",
    )


def make_user_refresh_token_model(
    *,
    user_table: str,
    tablename: str = "user_refresh_tokens",
    class_name: str = "UserRefreshTokenModel",
) -> type[BaseUserRefreshTokenModel]:
    """Build a concrete refresh-token model bound to ``user_table``.

    Mirrors :func:`tempest_fastapi_sdk.make_user_token_model` — a
    one-call helper for projects that do not need to subclass the
    abstract base manually. Production projects should still ship a
    hand-written ``src/db/models/user_refresh_token.py`` so the FK
    column is editable and the class is importable for refactors.

    Args:
        user_table (str): Name of the project's concrete user
            table (e.g. ``"users"``) — used as the FK target.
        tablename (str): Name of the refresh-token table.
            Defaults to ``"user_refresh_tokens"``.
        class_name (str): Python class name. Defaults to
            ``"UserRefreshTokenModel"``.

    Returns:
        type[BaseUserRefreshTokenModel]: Concrete SQLAlchemy
        mapping with the FK + cascade set up correctly.
    """
    namespace: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseUserRefreshTokenModel,), namespace)


__all__: list[str] = [
    "BaseUserRefreshTokenModel",
    "make_user_refresh_token_model",
]
