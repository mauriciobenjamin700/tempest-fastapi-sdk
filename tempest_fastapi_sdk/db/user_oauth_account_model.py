"""Linked social-identity table for the bundled OAuth login flow.

One row per ``(provider, subject)`` pair the application has seen —
the join between a third-party identity (Google's ``sub``, GitHub's
``id``) and the local user row.

A **separate table**, rather than two columns on the user model, is
what lets the same person carry a Google login *and* a GitHub login
*and* a corporate OIDC login at once. It is also where the composite
``UNIQUE (provider, subject)`` lives, which is the constraint that
makes the identity — not the email — the thing the flow keys on. An
email can change hands; a provider subject cannot.

Concrete subclasses live in the consuming application so the table
joins the project's metadata and Alembic emits it under the
application's naming convention, mirroring
:class:`~tempest_fastapi_sdk.BaseUserRefreshTokenModel` and
:class:`~tempest_fastapi_sdk.BaseUserTokenModel`. The whole flow is
**opt-in**: pass the concrete model as ``oauth_account_model=`` to
:class:`~tempest_fastapi_sdk.UserAuthService`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import TIMESTAMP, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseUserOAuthAccountModel(BaseModel):
    """Abstract linked-identity row for the bundled OAuth login flow.

    Concrete subclasses pick the ``__tablename__``
    (``user_oauth_accounts`` by convention) and add the FK to the
    project's concrete ``UserModel``. Both unique constraints are
    declared here rather than left to the subclass, so a hand-written
    mapping cannot ship without them:

    * ``UNIQUE (provider, subject)`` — one local user per third-party
      identity. Without it two rows could claim the same Google
      account, and which user a callback resolves to would depend on
      row order.
    * ``UNIQUE (user_id, provider)`` — one link per provider per user,
      which is what makes ``POST /auth/oauth/accounts/unlink`` name a
      single row.

    Attributes:
        user_id (UUID): Owner of the link. Concrete subclasses MUST
            declare this as a ``ForeignKey`` so cascading deletes wipe
            the links alongside the user.
        provider (str): Provider key as
            :attr:`~tempest_fastapi_sdk.OAuthUser.provider` reports it
            (``"google"``, ``"github"``, ``"oidc:auth0"`` …).
        subject (str): Stable per-provider user id. Opaque; compare,
            never parse.
        email (str | None): Email the provider reported at link time.
            Stored for display and support, **not** used to resolve the
            login — the ``(provider, subject)`` pair is.
        email_verified (bool | None): Whether the provider stated it
            had verified that address. ``None`` means the provider said
            nothing either way, which is not the same as ``False``.
        name (str | None): Display name the provider reported.
        picture (str | None): Avatar URL the provider reported.
        last_login_at (datetime | None): Last time this link completed
            a callback. ``NULL`` until the second login through it.
    """

    __abstract__ = True

    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the user this identity is linked to (set by subclass).",
    )
    provider: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="Provider key ('google', 'github', 'oidc:auth0').",
    )
    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Stable per-provider user id. Opaque — compare, never parse.",
    )
    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        default=None,
        doc="Email the provider reported. Display only; not the login key.",
    )
    email_verified: Mapped[bool | None] = mapped_column(
        nullable=True,
        default=None,
        doc="Whether the provider stated it verified the email. NULL = unstated.",
    )
    name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        default=None,
        doc="Display name the provider reported.",
    )
    picture: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        default=None,
        doc="Avatar URL the provider reported.",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="Last time this link completed an OAuth callback.",
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:  # noqa: N805
        """Declare both unique constraints on every concrete subclass.

        Built per subclass rather than shared as a class-level tuple: a
        :class:`~sqlalchemy.UniqueConstraint` instance binds to exactly
        one ``Table``, so a single tuple on the abstract base would fail
        the moment a project mapped a second concrete subclass. The
        names come from the metadata naming convention, so Alembic sees
        the same identifiers on every machine.
        """
        return (
            UniqueConstraint("provider", "subject"),
            UniqueConstraint("user_id", "provider"),
        )


def make_user_oauth_account_model(
    *,
    user_table: str,
    tablename: str = "user_oauth_accounts",
    class_name: str = "UserOAuthAccountModel",
) -> type[BaseUserOAuthAccountModel]:
    """Build a concrete OAuth-account model bound to ``user_table``.

    Mirrors
    :func:`~tempest_fastapi_sdk.make_user_refresh_token_model` — a
    one-call helper for projects that do not need to subclass the
    abstract base manually. Production projects should still ship a
    hand-written ``src/db/models/user_oauth_account.py`` so the FK
    column is editable and the class is importable for refactors.

    Args:
        user_table (str): Name of the project's concrete user table
            (e.g. ``"users"``) — used as the FK target.
        tablename (str): Name of the linked-identity table. Defaults to
            ``"user_oauth_accounts"``.
        class_name (str): Python class name. Defaults to
            ``"UserOAuthAccountModel"``.

    Returns:
        type[BaseUserOAuthAccountModel]: Concrete SQLAlchemy mapping
        with the FK, the cascade and both unique constraints in place.
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
    return type(class_name, (BaseUserOAuthAccountModel,), namespace)


__all__: list[str] = [
    "BaseUserOAuthAccountModel",
    "make_user_oauth_account_model",
]
