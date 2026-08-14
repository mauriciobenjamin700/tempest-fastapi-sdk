"""Enrol voices, recognise them, and delete them on request.

The three verbs are equally load-bearing. Enrolment writes biometric
data, recognition reads it, and deletion is the one the LGPD gives the
person an unconditional right to (Art. 18, VI) — so it is a first-class
method here, not something a project is left to write against the table.

Two refusals are deliberate and cannot be configured away:

* **No consent, no enrolment.** ``consent_reference`` is required, and a
  blank one raises. Voice biometrics need specific, highlighted consent
  (Art. 11, I); a row without evidence of it is a liability that looks
  like a feature.
* **A profile from another model is never compared.** Similarity between
  vectors from different models is a number with no meaning, and a
  meaningless number that looks like a score is worse than an error.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from tempest_fastapi_sdk.exceptions import NotFoundException, ValidationException
from tempest_fastapi_sdk.genai.audio.voiceprint import (
    DEFAULT_MATCH_THRESHOLD,
    MIN_ENROLLMENT_SECONDS,
    VoiceEmbedder,
    cosine_similarity,
)
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.voice_profile_model import BaseVoiceProfileModel

DEFAULT_MODEL_NAME: str = "3dspeaker-eres2net-base"
"""Name recorded on every profile this service writes.

Stored per row so that swapping the embedding model does not silently
invalidate comparisons: profiles from the old model stop matching and
:meth:`VoiceProfileService.stale_profiles` can find them.
"""


class ConsentRequired(ValidationException):
    """Raised when enrolment was attempted without evidence of consent."""

    code: str = "VOICE_CONSENT_REQUIRED"
    message: str = "Voice enrolment requires recorded consent"


class VoiceMatch(BaseSchema):
    """A voice matched against an enrolled profile.

    Attributes:
        profile_id (UUID): The profile that matched.
        user_id (UUID): Who it belongs to.
        similarity (float): Cosine similarity, ``0..1`` in practice.
        label (str | None): The profile's label.
    """

    profile_id: UUID
    user_id: UUID
    similarity: float
    label: str | None = None


def pack_embedding(embedding: list[float]) -> bytes:
    """Serialize a voiceprint for storage.

    Little-endian float32, fixed by this function rather than by the
    platform: a profile written on one architecture has to be readable on
    another, and ``struct`` without an explicit byte order is not.

    Args:
        embedding (list[float]): The vector.

    Returns:
        bytes: The packed vector.
    """
    return struct.pack(f"<{len(embedding)}f", *embedding)


def unpack_embedding(payload: bytes, dimensions: int) -> list[float]:
    """Read a stored voiceprint back.

    Args:
        payload (bytes): The packed vector.
        dimensions (int): How many values it should hold.

    Returns:
        list[float]: The vector.

    Raises:
        ValueError: When the byte length does not match ``dimensions`` —
            a truncated or foreign row, which must not be compared.
    """
    expected = dimensions * 4
    if len(payload) != expected:
        raise ValueError(
            f"stored embedding is {len(payload)} bytes, expected {expected} "
            f"for {dimensions} dimensions",
        )
    return list(struct.unpack(f"<{dimensions}f", payload))


class VoiceProfileService:
    """Enrolment, recognition and deletion of voice profiles.

    Attributes:
        profile_model (type[BaseVoiceProfileModel]): The project's model.
        embedder (VoiceEmbedder): Produces the voiceprints.
        threshold (float): Similarity above which a voice is a match.
        model_name (str): Recorded on every profile written.
    """

    def __init__(
        self,
        *,
        profile_model: type[BaseVoiceProfileModel],
        embedder: VoiceEmbedder | None = None,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        model_name: str = DEFAULT_MODEL_NAME,
    ) -> None:
        """Initialize the service.

        Args:
            profile_model (type[BaseVoiceProfileModel]): Concrete model.
            embedder (VoiceEmbedder | None): Voiceprint extractor.
                ``None`` builds a default one.
            threshold (float): Similarity above which a voice matches.
                Raise it for anything that grants access; the default
                suits attributing lines in a transcript.
            model_name (str): Recorded on every profile written.

        Raises:
            ValueError: When ``threshold`` is outside ``0..1``.
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        self.profile_model: type[BaseVoiceProfileModel] = profile_model
        self.embedder: VoiceEmbedder = embedder or VoiceEmbedder()
        self.threshold: float = threshold
        self.model_name: str = model_name

    async def enroll(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        audio: str | Path | bytes,
        consent_reference: str,
        consent_at: datetime | None = None,
        label: str | None = None,
        min_seconds: float = MIN_ENROLLMENT_SECONDS,
    ) -> BaseVoiceProfileModel:
        """Record a voiceprint for ``user_id``.

        The audio is used and discarded — only the vector is stored, and
        nothing here writes the recording to disk.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (UUID): Who is enrolling.
            audio (str | Path | bytes): The enrolment recording.
            consent_reference (str): Evidence of the person's specific
                consent to voice biometrics — a policy version, a signed
                document id, a ticket. Required.
            consent_at (datetime | None): When they consented. Defaults
                to now, which is right when consent is collected in the
                same request and wrong when it was collected earlier —
                pass the real timestamp in that case.
            label (str | None): What this recording was.
            min_seconds (float): Shortest audio accepted.

        Returns:
            BaseVoiceProfileModel: The persisted profile.

        Raises:
            ConsentRequired: When ``consent_reference`` is blank.
            ValueError: When the recording is too short to enrol from.
        """
        if not consent_reference or not consent_reference.strip():
            raise ConsentRequired(
                message=(
                    "consent_reference is required: a voiceprint is sensitive "
                    "personal data and needs specific, highlighted consent"
                ),
            )
        embedding = await self.embedder.embed_for_enrollment(
            audio,
            min_seconds=min_seconds,
        )
        profile = self.profile_model(
            user_id=user_id,
            label=label,
            embedding=pack_embedding(embedding),
            dimensions=len(embedding),
            model_name=self.model_name,
            consent_at=consent_at or utcnow(),
            consent_reference=consent_reference.strip(),
        )
        session.add(profile)
        await session.flush()
        await session.refresh(profile)
        return profile

    async def identify(
        self,
        session: AsyncSession,
        *,
        embedding: list[float],
        user_ids: list[UUID] | None = None,
    ) -> VoiceMatch | None:
        """Find the enrolled profile a voiceprint belongs to.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            embedding (list[float]): The voiceprint to identify.
            user_ids (list[UUID] | None): Restrict the search to these
                people. Pass the meeting's participants and the search
                stops being "who in the database is this" — smaller,
                faster, and far less likely to match a stranger.

        Returns:
            VoiceMatch | None: The best match above the threshold, or
            ``None``. ``None`` means *not recognised*, which is not the
            same as *nobody spoke*.
        """
        profiles = await self._candidates(session, user_ids=user_ids)
        best: VoiceMatch | None = None
        for profile in profiles:
            if profile.model_name != self.model_name:
                continue
            try:
                stored = unpack_embedding(profile.embedding, profile.dimensions)
            except ValueError:
                continue
            try:
                score = cosine_similarity(embedding, stored)
            except ValueError:
                continue
            if score < self.threshold:
                continue
            if best is None or score > best.similarity:
                best = VoiceMatch(
                    profile_id=profile.id,
                    user_id=profile.user_id,
                    similarity=score,
                    label=profile.label,
                )
        if best is not None:
            await self._stamp_match(session, best.profile_id)
        return best

    async def identify_audio(
        self,
        session: AsyncSession,
        *,
        audio: str | Path | bytes,
        start: float | None = None,
        end: float | None = None,
        user_ids: list[UUID] | None = None,
    ) -> VoiceMatch | None:
        """Identify the speaker in a recording, or a span of one.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            audio (str | Path | bytes): The recording.
            start (float | None): Span start in seconds.
            end (float | None): Span end in seconds.
            user_ids (list[UUID] | None): Restrict the search.

        Returns:
            VoiceMatch | None: The best match, or ``None``.
        """
        embedding = await self.embedder.embed(audio, start=start, end=end)
        return await self.identify(session, embedding=embedding, user_ids=user_ids)

    async def list_profiles(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> list[BaseVoiceProfileModel]:
        """Return every profile enrolled by one person.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (UUID): Whose profiles to list.

        Returns:
            list[BaseVoiceProfileModel]: The profiles, empty when there
            are none.
        """
        result = await session.execute(
            select(self.profile_model)
            .where(self.profile_model.user_id == user_id)
            .order_by(self.profile_model.created_at),
        )
        return list(result.scalars().all())

    async def delete_profile(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        profile_id: UUID,
    ) -> None:
        """Delete one profile.

        Scoped to its owner, so a profile belonging to somebody else
        answers 404 exactly like one that does not exist.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (UUID): The owner.
            profile_id (UUID): The profile to delete.

        Raises:
            NotFoundException: When that person holds no such profile.
        """
        result = await session.execute(
            select(self.profile_model).where(
                self.profile_model.id == profile_id,
                self.profile_model.user_id == user_id,
            ),
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            raise NotFoundException(message="voice profile not found")
        await session.delete(profile)
        await session.flush()

    async def forget_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> int:
        """Delete every voiceprint a person has enrolled.

        This is the request the LGPD gives them an unconditional right to
        make (Art. 18, VI). It is a method rather than an example in the
        docs because "delete my biometric data" must not depend on each
        project writing the query correctly.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (UUID): Whose data to erase.

        Returns:
            int: How many profiles were deleted; ``0`` when there were
            none, which is a successful outcome, not an error.
        """
        profiles = await self.list_profiles(session, user_id=user_id)
        for profile in profiles:
            await session.delete(profile)
        await session.flush()
        return len(profiles)

    async def stale_profiles(
        self,
        session: AsyncSession,
    ) -> list[BaseVoiceProfileModel]:
        """Return profiles written by a different embedding model.

        Swapping the model invalidates every existing profile: their
        vectors are no longer comparable, so those people silently stop
        being recognised. This is how you find them to re-enrol.

        Args:
            session (AsyncSession): Active SQLAlchemy session.

        Returns:
            list[BaseVoiceProfileModel]: Profiles from another model.
        """
        result = await session.execute(
            select(self.profile_model).where(
                self.profile_model.model_name != self.model_name,
            ),
        )
        return list(result.scalars().all())

    async def _candidates(
        self,
        session: AsyncSession,
        *,
        user_ids: list[UUID] | None,
    ) -> list[BaseVoiceProfileModel]:
        """Load the profiles a search should consider.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_ids (list[UUID] | None): Restrict to these people.

        Returns:
            list[BaseVoiceProfileModel]: The candidate profiles.
        """
        statement: Any = select(self.profile_model)
        if user_ids is not None:
            if not user_ids:
                return []
            statement = statement.where(self.profile_model.user_id.in_(user_ids))
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def _stamp_match(self, session: AsyncSession, profile_id: UUID) -> None:
        """Record that a profile matched just now.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            profile_id (UUID): The profile that matched.
        """
        profile = await session.get(self.profile_model, profile_id)
        if profile is not None:
            profile.last_matched_at = utcnow()
            await session.flush()


__all__: list[str] = [
    "DEFAULT_MODEL_NAME",
    "ConsentRequired",
    "VoiceMatch",
    "VoiceProfileService",
    "pack_embedding",
    "unpack_embedding",
]
