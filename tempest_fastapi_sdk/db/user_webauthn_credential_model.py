"""Credential table for the bundled WebAuthn / passkey flow.

A WebAuthn credential is a public key the authenticator (a security
key, or the platform's biometric store) generated for one account on
one relying party. The private half never leaves the device, so this
table holds nothing that authenticates anyone on its own — unlike a
password hash, a full leak of it grants no login.

Concrete subclasses live in the consuming application so the table
joins the project's metadata and Alembic emits it under the
application's naming convention. The pattern mirrors
:class:`~tempest_fastapi_sdk.db.user_recovery_code_model.BaseUserRecoveryCodeModel`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import TIMESTAMP, Boolean, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseWebAuthnCredentialModel(BaseModel):
    """Abstract WebAuthn credential owned by one user.

    Concrete subclasses pick the ``__tablename__``
    (``user_webauthn_credentials`` by convention) and add the FK to the
    project's concrete ``UserModel``.

    Attributes:
        user_id (UUID): Owner of the credential. Concrete subclasses
            MUST declare this as a ``ForeignKey`` so cascading deletes
            wipe the credentials alongside the user.
        credential_id (bytes): The authenticator's credential ID, raw.
            Unique across the table — an assertion arrives carrying only
            this, so it is how the login flow finds the account without
            a username.
        credential_data (bytes): The serialized
            ``AttestedCredentialData`` (AAGUID + credential ID + COSE
            public key) exactly as ``fido2`` produced it. Stored as the
            opaque blob the library round-trips, so a format change
            upstream never needs a migration of parsed columns.
        sign_count (int): Signature counter reported by the
            authenticator at the last successful assertion. A counter
            that fails to advance is the spec's cloned-authenticator
            signal.
        name (str | None): User-supplied label ("YubiKey 5",
            "iPhone"), so a person managing several passkeys can tell
            them apart.
        transports (str | None): Comma-separated transport hints
            (``usb``, ``nfc``, ``ble``, ``internal``, ``hybrid``)
            reported at registration. Sent back in the assertion
            options so the browser can prompt for the right device.
        aaguid (str | None): Authenticator model identifier, hex. Useful
            for support ("which key is this?"), never for authorization.
        backed_up (bool): Whether the authenticator reported the
            credential as backed up (synced passkey) at registration. A
            device-bound credential is lost with the device; a synced
            one is not, which changes what account recovery must offer.
        last_used_at (datetime | None): Timestamp of the last successful
            assertion. ``NULL`` until the credential is first used.
    """

    __abstract__ = True

    user_id: Mapped[UUID]
    credential_id: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        unique=True,
        index=True,
        doc="Raw credential ID reported by the authenticator.",
    )
    credential_data: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        doc="Serialized AttestedCredentialData (AAGUID + id + COSE key).",
    )
    sign_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Signature counter at the last successful assertion.",
    )
    name: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        default=None,
        doc="User-supplied label for this authenticator.",
    )
    transports: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        default=None,
        doc="Comma-separated transport hints reported at registration.",
    )
    aaguid: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        default=None,
        doc="Authenticator model identifier (hex). Informational only.",
    )
    backed_up: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether the authenticator reported the credential as backed up.",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="Timestamp of the last successful assertion.",
    )


def make_web_authn_credential_model(
    *,
    user_table: str,
    tablename: str = "user_webauthn_credentials",
    class_name: str = "UserWebAuthnCredentialModel",
) -> type[BaseWebAuthnCredentialModel]:
    """Build a concrete credential model bound to ``user_table``.

    Mirrors :func:`tempest_fastapi_sdk.make_user_recovery_code_model` — a
    one-call helper for projects that do not need to subclass the
    abstract base manually.

    Args:
        user_table (str): Name of the project's concrete user table
            (e.g. ``"users"``) — used as the FK target.
        tablename (str): Name of the credential table. Defaults to
            ``"user_webauthn_credentials"``.
        class_name (str): Python class name. Defaults to
            ``"UserWebAuthnCredentialModel"``.

    Returns:
        type[BaseWebAuthnCredentialModel]: Concrete SQLAlchemy mapping
        with the FK + cascade set up correctly.
    """
    from sqlalchemy import ForeignKey

    namespace: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    }
    return type(class_name, (BaseWebAuthnCredentialModel,), namespace)


__all__: list[str] = [
    "BaseWebAuthnCredentialModel",
    "make_web_authn_credential_model",
]
