"""Device table — one row per push target a user opted in from.

The unified counterpart of
:class:`~tempest_fastapi_sdk.db.BaseWebPushSubscriptionModel`: it holds
browsers *and* phones, because a fan-out that has to read two tables is a
fan-out every application re-implements. Web rows fill ``p256dh`` /
``auth`` with the browser's encryption material; mobile rows leave them
``NULL`` and carry an FCM registration token in ``token``.

Existing projects on ``BaseWebPushSubscriptionModel`` keep working
untouched — this is an additional table, not a migration of that one.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseDeviceTokenModel(BaseModel):
    """Abstract push device owned by one user.

    Concrete subclasses pick the ``__tablename__`` (``device_tokens`` by
    convention) and add the FK to the project's ``UserModel``. ``token``
    is unique, so a device that re-registers updates its row instead of
    creating a duplicate — the token is the device identity, exactly as
    the endpoint is for Web Push.

    Attributes:
        user_id (UUID): FK to the owning user (target set by the subclass).
        token (str): Push endpoint URL (web) or FCM registration token
            (mobile). Unique + indexed.
        platform (str): ``"web"``, ``"ios"`` or ``"android"`` — the value
            of a :class:`~tempest_fastapi_sdk.push.PushPlatform`.
        p256dh (str | None): Browser ECDH public key. Web rows only.
        auth (str | None): Browser auth secret. Web rows only.
        expiration_time (int | None): Browser ``expirationTime`` in
            milliseconds since epoch, when the subscription reported one.
        app_version (str | None): Optional client build identifier.
        last_seen_at (datetime | None): When the device last registered.
            Re-registration refreshes it, so a "devices" screen can show
            what is still in use and an operator can spot dormant rows.
    """

    __abstract__ = True

    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the user this device belongs to (set by subclass).",
    )
    token: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        unique=True,
        index=True,
        doc="Push endpoint URL (web) or FCM registration token (mobile).",
    )
    platform: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
        doc="Device platform: web, ios or android.",
    )
    p256dh: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        doc="Client ECDH P-256 public key (web rows only).",
    )
    auth: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        doc="Client auth secret (web rows only).",
    )
    expiration_time: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        default=None,
        doc="Browser expirationTime (ms since epoch), or NULL.",
    )
    app_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        doc="Optional client build identifier.",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        doc="When the device last registered itself.",
    )


def make_device_token_model(
    *,
    user_table: str,
    tablename: str = "device_tokens",
    class_name: str = "DeviceTokenModel",
) -> type[BaseDeviceTokenModel]:
    """Build a concrete ``DeviceTokenModel`` subclass at runtime.

    Used by tests and small scripts. Production projects ship a
    hand-written ``src/db/models/device_token.py`` instead, so the FK
    column is editable and the class is importable for refactors.

    Args:
        user_table (str): Table name of the concrete ``UserModel`` the FK
            should reference (usually ``"users"``).
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name; affects ``repr`` and Alembic
            identifiers.

    Returns:
        type[BaseDeviceTokenModel]: A concrete mapped class.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseDeviceTokenModel,), attrs)


__all__: list[str] = [
    "BaseDeviceTokenModel",
    "make_device_token_model",
]
