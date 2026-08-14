"""Tests for voice enrolment, recognition and deletion.

The embedding model is not exercised here — that needs 40 MB of weights
and real audio, and lives in the ``model`` tier. What runs everywhere is
everything around it, which is where the consequences are: a profile
written without consent, a vector compared against one from another
model, and a deletion that has to actually delete.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import BaseModel, BaseUserModel, make_voice_profile_model
from tempest_fastapi_sdk.exceptions import NotFoundException
from tempest_fastapi_sdk.genai.audio import (
    ConsentRequired,
    VoiceProfileService,
    cosine_similarity,
    pack_embedding,
    unpack_embedding,
)


class _VoiceUser(BaseUserModel):
    __tablename__ = "voice_test_users"


_VoiceProfile = make_voice_profile_model(
    user_table="voice_test_users",
    tablename="voice_test_profiles",
    class_name="_VoiceProfile",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


class _StubEmbedder:
    """Embedder returning a fixed vector, so the DB path is the subject."""

    def __init__(self, vector: list[float], duration: float = 5.0) -> None:
        """Store what to return.

        Args:
            vector (list[float]): The voiceprint to hand back.
            duration (float): Pretended audio length, for the enrolment
                length check.
        """
        self.vector = vector
        self.duration = duration

    async def embed(self, audio: Any, **kwargs: Any) -> list[float]:
        """Return the fixed vector.

        Args:
            audio (Any): Ignored.
            **kwargs (Any): Ignored.

        Returns:
            list[float]: The configured vector.
        """
        return self.vector

    async def embed_for_enrollment(
        self,
        audio: Any,
        *,
        min_seconds: float = 3.0,
    ) -> list[float]:
        """Return the fixed vector, honoring the length floor.

        Args:
            audio (Any): Ignored.
            min_seconds (float): Shortest audio accepted.

        Returns:
            list[float]: The configured vector.

        Raises:
            ValueError: When the pretended duration is too short.
        """
        if self.duration < min_seconds:
            raise ValueError(f"enrollment needs at least {min_seconds:g}s of audio")
        return self.vector


def _service(vector: list[float], **kwargs: Any) -> VoiceProfileService:
    """Build a service over the stub embedder.

    Args:
        vector (list[float]): What the embedder returns.
        **kwargs (Any): Service keyword arguments.

    Returns:
        VoiceProfileService: The service.
    """
    return VoiceProfileService(
        profile_model=_VoiceProfile,
        embedder=_StubEmbedder(vector),  # type: ignore[arg-type]
        **kwargs,
    )


async def _user(session: AsyncSession, email: str = "ana@example.com") -> UUID:
    """Create a user and return their id.

    Args:
        session (AsyncSession): Active session.
        email (str): Their email.

    Returns:
        UUID: The user id.
    """
    user = _VoiceUser(email=email, hashed_password="x")
    session.add(user)
    await session.flush()
    return user.id


class TestPacking:
    def test_round_trips(self) -> None:
        """A stored vector must read back identical."""
        vector = [0.5, -0.25, 1.0]
        assert unpack_embedding(pack_embedding(vector), 3) == vector

    def test_a_truncated_row_is_refused(self) -> None:
        """Comparing half a vector would produce a plausible number."""
        with pytest.raises(ValueError, match="expected"):
            unpack_embedding(pack_embedding([1.0, 2.0]), 3)

    def test_byte_order_is_explicit(self) -> None:
        """A profile written on one machine is read on another."""
        assert pack_embedding([1.0]) == b"\x00\x00\x80\x3f"


class TestSimilarity:
    def test_identical_vectors_score_one(self) -> None:
        """The trivial anchor."""
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        """Different voices land far apart."""
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_an_empty_vector_scores_zero_rather_than_dividing(self) -> None:
        """Silence produces a zero vector; it must not raise."""
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_different_lengths_are_an_error_not_a_low_score(self) -> None:
        """Two models produce incomparable vectors.

        Returning a low similarity would read as 'not this person' when
        the truth is 'this comparison is meaningless'.
        """
        with pytest.raises(ValueError, match="different lengths"):
            cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


class TestEnrollment:
    async def test_stores_the_vector_and_the_consent(
        self,
        session: AsyncSession,
    ) -> None:
        """Consent lives next to the biometric data, not elsewhere."""
        user_id = await _user(session)
        service = _service([0.1] * 8)
        profile = await service.enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="politica-v3",
            label="onboarding",
        )
        assert profile.user_id == user_id
        assert profile.dimensions == 8
        assert profile.consent_reference == "politica-v3"
        assert profile.consent_at is not None
        assert profile.label == "onboarding"

    async def test_refuses_without_consent(self, session: AsyncSession) -> None:
        """A voiceprint needs specific, highlighted consent.

        The refusal is not configurable: a row without evidence is a
        liability that looks like a feature.
        """
        user_id = await _user(session)
        service = _service([0.1] * 8)
        for blank in ("", "   "):
            with pytest.raises(ConsentRequired):
                await service.enroll(
                    session,
                    user_id=user_id,
                    audio=b"",
                    consent_reference=blank,
                )

    async def test_refuses_audio_too_short(self, session: AsyncSession) -> None:
        """A profile from one word matches almost anyone."""
        user_id = await _user(session)
        service = VoiceProfileService(
            profile_model=_VoiceProfile,
            embedder=_StubEmbedder([0.1] * 8, duration=1.0),  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="at least"):
            await service.enroll(
                session,
                user_id=user_id,
                audio=b"",
                consent_reference="politica-v3",
            )

    async def test_an_earlier_consent_timestamp_is_kept(
        self,
        session: AsyncSession,
    ) -> None:
        """Consent collected last week is not consent collected now."""
        user_id = await _user(session)
        earlier = datetime(2026, 1, 1, tzinfo=UTC)
        profile = await _service([0.1] * 8).enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="doc-1",
            consent_at=earlier,
        )
        assert profile.consent_at.year == 2026
        assert profile.consent_at.month == 1


class TestIdentification:
    async def test_matches_the_enrolled_voice(self, session: AsyncSession) -> None:
        """The whole point."""
        user_id = await _user(session)
        vector = [1.0, 0.0, 0.0, 0.0]
        service = _service(vector)
        await service.enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="doc",
        )
        match = await service.identify(session, embedding=vector)
        assert match is not None
        assert match.user_id == user_id
        assert match.similarity == pytest.approx(1.0)

    async def test_an_unenrolled_voice_returns_none(
        self,
        session: AsyncSession,
    ) -> None:
        """``None`` means not recognised, not 'nobody spoke'."""
        user_id = await _user(session)
        service = _service([1.0, 0.0, 0.0, 0.0])
        await service.enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="doc",
        )
        assert await service.identify(session, embedding=[0.0, 1.0, 0.0, 0.0]) is None

    async def test_picks_the_closest_of_several(self, session: AsyncSession) -> None:
        """Two enrolled people, one voice: the nearer one wins."""
        ana = await _user(session, "ana@example.com")
        bruno = await _user(session, "bruno@example.com")
        service = _service([1.0, 0.0])
        await service.enroll(
            session,
            user_id=ana,
            audio=b"",
            consent_reference="doc",
        )
        service.embedder = _StubEmbedder([0.0, 1.0])  # type: ignore[assignment]
        await service.enroll(
            session,
            user_id=bruno,
            audio=b"",
            consent_reference="doc",
        )
        match = await service.identify(session, embedding=[0.9, 0.1])
        assert match is not None
        assert match.user_id == ana

    async def test_restricting_to_participants_excludes_everyone_else(
        self,
        session: AsyncSession,
    ) -> None:
        """Passing the meeting's participants is the cheap safety net."""
        ana = await _user(session, "ana@example.com")
        bruno = await _user(session, "bruno@example.com")
        service = _service([1.0, 0.0])
        await service.enroll(
            session,
            user_id=ana,
            audio=b"",
            consent_reference="doc",
        )
        assert await service.identify(session, embedding=[1.0, 0.0]) is not None
        assert (
            await service.identify(
                session,
                embedding=[1.0, 0.0],
                user_ids=[bruno],
            )
            is None
        )

    async def test_an_empty_participant_list_matches_nobody(
        self,
        session: AsyncSession,
    ) -> None:
        """An empty restriction must not silently mean 'no restriction'."""
        user_id = await _user(session)
        service = _service([1.0, 0.0])
        await service.enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="doc",
        )
        found = await service.identify(session, embedding=[1.0, 0.0], user_ids=[])
        assert found is None

    async def test_profiles_from_another_model_are_skipped(
        self,
        session: AsyncSession,
    ) -> None:
        """Their vectors are not comparable, so they are not compared."""
        user_id = await _user(session)
        service = _service([1.0, 0.0], model_name="model-a")
        await service.enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="doc",
        )
        other = _service([1.0, 0.0], model_name="model-b")
        assert await other.identify(session, embedding=[1.0, 0.0]) is None

    async def test_a_match_stamps_the_profile(self, session: AsyncSession) -> None:
        """Knowing a profile is in use is what makes pruning safe."""
        user_id = await _user(session)
        service = _service([1.0, 0.0])
        profile = await service.enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="doc",
        )
        assert profile.last_matched_at is None
        await service.identify(session, embedding=[1.0, 0.0])
        await session.refresh(profile)
        assert profile.last_matched_at is not None


class TestDeletion:
    async def test_deletes_one_profile(self, session: AsyncSession) -> None:
        """The ordinary case."""
        user_id = await _user(session)
        service = _service([1.0, 0.0])
        profile = await service.enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="doc",
        )
        await service.delete_profile(
            session,
            user_id=user_id,
            profile_id=profile.id,
        )
        assert await service.list_profiles(session, user_id=user_id) == []

    async def test_cannot_delete_somebody_elses(self, session: AsyncSession) -> None:
        """A foreign id answers like one that does not exist."""
        ana = await _user(session, "ana@example.com")
        bruno = await _user(session, "bruno@example.com")
        service = _service([1.0, 0.0])
        profile = await service.enroll(
            session,
            user_id=ana,
            audio=b"",
            consent_reference="doc",
        )
        with pytest.raises(NotFoundException):
            await service.delete_profile(
                session,
                user_id=bruno,
                profile_id=profile.id,
            )
        assert await service.list_profiles(session, user_id=ana) != []

    async def test_forget_user_erases_everything(self, session: AsyncSession) -> None:
        """The right the LGPD makes unconditional.

        A method rather than a documented query, because "delete my
        biometric data" must not depend on each project getting the
        ``WHERE`` right.
        """
        user_id = await _user(session)
        service = _service([1.0, 0.0])
        for _ in range(3):
            await service.enroll(
                session,
                user_id=user_id,
                audio=b"",
                consent_reference="doc",
            )
        assert await service.forget_user(session, user_id=user_id) == 3
        assert await service.list_profiles(session, user_id=user_id) == []

    async def test_forgetting_nobody_is_not_an_error(
        self,
        session: AsyncSession,
    ) -> None:
        """Erasing data that is not there is a successful outcome."""
        service = _service([1.0, 0.0])
        assert await service.forget_user(session, user_id=uuid4()) == 0


class TestModelMigration:
    async def test_stale_profiles_are_findable(self, session: AsyncSession) -> None:
        """Swapping models silently stops recognising people.

        Without this, the failure is invisible: enrolled users simply
        stop matching and nobody knows which ones to re-enrol.
        """
        user_id = await _user(session)
        old = _service([1.0, 0.0], model_name="model-a")
        await old.enroll(
            session,
            user_id=user_id,
            audio=b"",
            consent_reference="doc",
        )
        new = _service([1.0, 0.0], model_name="model-b")
        stale = await new.stale_profiles(session)
        assert [p.model_name for p in stale] == ["model-a"]
        assert await old.stale_profiles(session) == []


class TestConfiguration:
    def test_threshold_must_be_a_similarity(self) -> None:
        """Outside 0..1 it can only be a mistake."""
        for bad in (-0.1, 1.5):
            with pytest.raises(ValueError, match="threshold"):
                VoiceProfileService(profile_model=_VoiceProfile, threshold=bad)
