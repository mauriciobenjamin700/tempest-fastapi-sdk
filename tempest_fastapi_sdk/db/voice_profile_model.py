"""Voice profile table — an enrolled voiceprint and its consent record.

A voiceprint identifies a person as reliably as a fingerprint template.
Under the LGPD that makes it *dado pessoal sensível* (Art. 5, II), whose
processing needs consent that is **specific and highlighted** for that
purpose (Art. 11, I) — general terms of service do not cover it.

So this table stores the consent next to the vector rather than trusting
a flag somewhere else, and
:class:`~tempest_fastapi_sdk.genai.audio.profiles.VoiceProfileService`
refuses to enrol without it. The columns are not decoration: when
somebody exercises their right to deletion, the row is what has to go,
and when an auditor asks what a person agreed to, ``consent_reference``
is the answer.

The raw audio is deliberately absent. Nothing here keeps a recording:
the vector cannot be played back, which makes a leak of this table far
less damaging than a leak of the enrolment clips.

Concrete subclasses live in the consuming application, mirroring
:class:`~tempest_fastapi_sdk.db.user_webauthn_credential_model.BaseWebAuthnCredentialModel`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import TIMESTAMP, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseVoiceProfileModel(BaseModel):
    """Abstract enrolled voiceprint belonging to one user.

    Concrete subclasses pick the ``__tablename__``
    (``user_voice_profiles`` by convention) and add the FK to the
    project's concrete ``UserModel``.

    Attributes:
        user_id (UUID): Owner of the voiceprint. Concrete subclasses
            MUST declare this as a ``ForeignKey`` so a cascading delete
            takes the biometric data with the account.
        label (str | None): What this recording was — ``"cadastro no
            onboarding"``, ``"ligação de 12/08"``. A person may hold
            several profiles, and knowing which is which is what makes
            it possible to delete the bad one.
        embedding (bytes): The voiceprint, float32 little-endian. Stored
            as bytes rather than an array column so the table works on
            SQLite and Postgres alike; ``dimensions`` says how many
            values it holds.
        dimensions (int): Length of the vector. Checked on read — a
            profile written by a different model must not be silently
            compared against one from the current model, because the
            similarity would be a number without meaning.
        model_name (str): Which embedding model produced it. Swapping
            models invalidates every profile, and this column is how you
            find the ones that need re-enrolling.
        consent_at (datetime): When the person consented to voice
            biometrics. Required — the service refuses to write a row
            without it.
        consent_reference (str): What they consented to: a policy
            version, a signed document id, a ticket. Free text because
            what counts as evidence differs per product, but not
            nullable, because "we have consent" with nothing behind it
            is not a record.
        last_matched_at (datetime | None): Last time this profile
            matched a voice. ``NULL`` until it does.
    """

    __abstract__ = True

    user_id: Mapped[UUID]
    label: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        default=None,
        doc="What this enrolment recording was.",
    )
    embedding: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        doc="Voiceprint as float32 little-endian bytes.",
    )
    dimensions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Number of values in the embedding.",
    )
    model_name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        doc="Embedding model that produced this vector.",
    )
    consent_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        doc="When the person consented to voice biometrics.",
    )
    consent_reference: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Policy version, document id or ticket evidencing the consent.",
    )
    last_matched_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="Last time this profile matched a voice.",
    )


def make_voice_profile_model(
    *,
    user_table: str,
    tablename: str = "user_voice_profiles",
    class_name: str = "UserVoiceProfileModel",
) -> type[BaseVoiceProfileModel]:
    """Build a concrete voice-profile model bound to ``user_table``.

    Args:
        user_table (str): Name of the project's concrete user table.
        tablename (str): Name of the profile table.
        class_name (str): Python class name.

    Returns:
        type[BaseVoiceProfileModel]: Concrete SQLAlchemy mapping with the
        FK and cascade set up, so deleting a user deletes their
        biometric data rather than orphaning it.
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
    return type(class_name, (BaseVoiceProfileModel,), namespace)


__all__: list[str] = [
    "BaseVoiceProfileModel",
    "make_voice_profile_model",
]
