"""Web Push subscription table — one row per subscribed user device.

Stores the browser ``PushSubscription`` (endpoint + ECDH keys) so the
backend can deliver notifications to every device a user opted in from.
Mirrors :class:`BaseUserTokenModel`: the SDK ships the abstract row, the
project ships the concrete table (so the FK and ``__tablename__`` live in
the application's metadata and Alembic emits them under its naming
convention).

The wire shape matches ``PushSubscription.toJSON()`` produced by the
browser (and forwarded verbatim by ``tempest-react-sdk``'s
``WebPushClient``), so a row maps 1:1 to
:class:`~tempest_fastapi_sdk.webpush.WebPushSubscriptionSchema`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseWebPushSubscriptionModel(BaseModel):
    """Abstract Web Push subscription owned by one user device.

    Concrete subclasses pick the ``__tablename__``
    (``web_push_subscriptions`` by convention) and add the FK to the
    project's concrete ``UserModel``. ``endpoint`` is unique, so a device
    that re-subscribes updates its existing row instead of creating a
    duplicate (see
    :meth:`~tempest_fastapi_sdk.webpush.WebPushSubscriptionService.subscribe`).

    Attributes:
        user_id (UUID): FK to the user this device belongs to (the FK
            target is set by the subclass).
        endpoint (str): Push service endpoint URL — the per-device
            identity. Unique + indexed.
        p256dh (str): Client ECDH P-256 public key (URL-safe base64).
        auth (str): Client auth secret (URL-safe base64).
        expiration_time (int | None): Optional expiration timestamp in
            milliseconds since epoch (the browser's ``expirationTime``).
        user_agent (str | None): Optional device/browser label, handy for
            a "your devices" management screen.
    """

    __abstract__ = True

    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the user this subscription belongs to (set by subclass).",
    )
    endpoint: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        unique=True,
        index=True,
        doc="Push service endpoint URL (per-device identity).",
    )
    p256dh: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Client ECDH P-256 public key (URL-safe base64).",
    )
    auth: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Client auth secret (URL-safe base64).",
    )
    expiration_time: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        default=None,
        doc="Browser expirationTime (ms since epoch), or NULL.",
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        default=None,
        doc="Optional device/browser label for a 'your devices' screen.",
    )


def make_web_push_subscription_model(
    *,
    user_table: str,
    tablename: str = "web_push_subscriptions",
    class_name: str = "WebPushSubscriptionModel",
) -> type[BaseWebPushSubscriptionModel]:
    """Build a concrete ``WebPushSubscriptionModel`` subclass at runtime.

    Used by tests and lightweight scripts. Production projects should
    instead ship a hand-written
    ``src/db/models/web_push_subscription.py`` so the FK column is
    editable and the class is importable for refactors.

    Args:
        user_table (str): Table name of the concrete ``UserModel`` the FK
            should reference (usually ``"users"``).
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name; affects ``repr`` and Alembic
            identifiers.

    Returns:
        type[BaseWebPushSubscriptionModel]: A concrete mapped class.
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
    return type(class_name, (BaseWebPushSubscriptionModel,), attrs)


__all__: list[str] = [
    "BaseWebPushSubscriptionModel",
    "make_web_push_subscription_model",
]
