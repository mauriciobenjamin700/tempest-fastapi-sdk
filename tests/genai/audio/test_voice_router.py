"""Tests for the voice router and the ``tempest voice`` commands.

The engines are stubbed here on purpose: what these routes have to get
right is not transcription quality but who they let write biometric data
and whose data they return.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from typer.testing import CliRunner

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    make_voice_profile_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.cli.main import app as cli_app
from tempest_fastapi_sdk.genai.audio import (
    DiarizedTranscription,
    SpeakerTurn,
    VoiceProfileService,
    make_voice_router,
)


class _RouterUser(BaseUserModel):
    __tablename__ = "voice_router_users"


_RouterProfile = make_voice_profile_model(
    user_table="voice_router_users",
    tablename="voice_router_profiles",
    class_name="_RouterProfile",
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
    """Returns a fixed vector without touching a model."""

    async def embed(self, audio: Any, **kwargs: Any) -> list[float]:
        """Return the fixed vector.

        Args:
            audio (Any): Ignored.
            **kwargs (Any): Ignored.

        Returns:
            list[float]: A constant voiceprint.
        """
        return [1.0, 0.0, 0.0]

    async def embed_for_enrollment(
        self,
        audio: Any,
        *,
        min_seconds: float = 3.0,
    ) -> list[float]:
        """Return the fixed vector.

        Args:
            audio (Any): Ignored.
            min_seconds (float): Ignored.

        Returns:
            list[float]: A constant voiceprint.
        """
        return [1.0, 0.0, 0.0]


class _StubTranscriber:
    """Returns a fixed conversation without loading Whisper."""

    def __init__(self) -> None:
        """Record what it was called with, for assertions."""
        self.calls: list[dict[str, Any]] = []

    async def transcribe(self, audio: Any, **kwargs: Any) -> DiarizedTranscription:
        """Return a fixed two-speaker conversation.

        Args:
            audio (Any): The uploaded bytes.
            **kwargs (Any): Recorded for assertions.

        Returns:
            DiarizedTranscription: The canned result.
        """
        self.calls.append({"size": len(audio), **kwargs})
        return DiarizedTranscription(
            text="oi tudo bem",
            language="pt",
            duration=4.0,
            num_speakers=2,
            turns=[
                SpeakerTurn(start=0.0, end=2.0, speaker=0, text="oi"),
                SpeakerTurn(start=2.0, end=4.0, speaker=1, text="tudo bem"),
            ],
        )


def _app(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
    with_profiles: bool = True,
    with_transcriber: bool = True,
) -> tuple[FastAPI, _StubTranscriber]:
    """Build an app with the voice router mounted.

    Args:
        session (AsyncSession): Session the routes will use.
        user_id (UUID | None): Who the caller is.
        with_profiles (bool): Mount the profile routes.
        with_transcriber (bool): Mount the transcription route.

    Returns:
        tuple[FastAPI, _StubTranscriber]: The app and the stub.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    def _current_user() -> UUID:
        return user_id or uuid4()

    transcriber = _StubTranscriber()
    service = VoiceProfileService(
        profile_model=_RouterProfile,
        embedder=_StubEmbedder(),  # type: ignore[arg-type]
    )
    app = FastAPI()
    app.include_router(
        make_voice_router(
            session_factory=_factory,
            transcriber=transcriber if with_transcriber else None,  # type: ignore[arg-type]
            profiles=service if with_profiles else None,
            current_user_id=_current_user if with_profiles else None,
        ),
    )
    register_exception_handlers(app)
    return app, transcriber


async def _user(session: AsyncSession, email: str = "ana@example.com") -> UUID:
    """Create a user.

    Args:
        session (AsyncSession): Active session.
        email (str): Their email.

    Returns:
        UUID: The user id.
    """
    user = _RouterUser(email=email, hashed_password="x")
    session.add(user)
    await session.flush()
    return user.id


class TestWiring:
    def test_profiles_without_a_user_dependency_is_refused(self) -> None:
        """Taking the user id from the body would let anyone enrol as anyone.

        The refusal is at wiring time because a request-time failure
        would mean the misconfiguration already shipped.
        """

        async def _factory() -> AsyncIterator[AsyncSession]:
            raise NotImplementedError

        service = VoiceProfileService(
            profile_model=_RouterProfile,
            embedder=_StubEmbedder(),  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="current_user_id"):
            make_voice_router(session_factory=_factory, profiles=service)

    def test_an_empty_router_is_refused(self) -> None:
        """Mounting nothing is a wiring mistake, not a configuration."""

        async def _factory() -> AsyncIterator[AsyncSession]:
            raise NotImplementedError

        with pytest.raises(ValueError, match="transcriber, profiles, or both"):
            make_voice_router(session_factory=_factory)

    async def test_dependencies_reach_every_route(
        self,
        session: AsyncSession,
    ) -> None:
        """These routes accept audio and read biometrics — auth matters."""
        from fastapi import HTTPException

        def _deny() -> None:
            raise HTTPException(status_code=403, detail="nope")

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_voice_router(
                session_factory=_factory,
                transcriber=_StubTranscriber(),  # type: ignore[arg-type]
                dependencies=[Depends(_deny)],
            ),
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/voice/transcribe",
                files={"audio": ("a.wav", b"x", "audio/wav")},
            )
        assert response.status_code == 403


class TestTranscription:
    async def test_returns_the_conversation(self, session: AsyncSession) -> None:
        """The route's whole job."""
        app, _ = _app(session, with_profiles=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/voice/transcribe",
                files={"audio": ("a.wav", b"0123456789", "audio/wav")},
                data={"num_speakers": "2", "language": "pt"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["num_speakers"] == 2
        assert [turn["text"] for turn in body["turns"]] == ["oi", "tudo bem"]

    async def test_passes_the_speaker_count_through(
        self,
        session: AsyncSession,
    ) -> None:
        """It is the setting that most changes the result."""
        app, stub = _app(session, with_profiles=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await client.post(
                "/voice/transcribe",
                files={"audio": ("a.wav", b"x", "audio/wav")},
                data={"num_speakers": "3"},
            )
        assert stub.calls[0]["num_speakers"] == 3

    async def test_an_oversized_upload_is_refused(
        self,
        session: AsyncSession,
    ) -> None:
        """Audio is held in memory while decoding; unbounded is an outage."""

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_voice_router(
                session_factory=_factory,
                transcriber=_StubTranscriber(),  # type: ignore[arg-type]
                max_upload_bytes=16,
            ),
        )
        register_exception_handlers(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/voice/transcribe",
                files={"audio": ("a.wav", b"x" * 64, "audio/wav")},
            )
        assert response.status_code == 422
        assert "larger than" in response.json()["detail"]


class TestProfiles:
    async def test_enrols_the_caller_not_a_body_field(
        self,
        session: AsyncSession,
    ) -> None:
        """The user id comes from the session, never from the request."""
        user_id = await _user(session)
        app, _ = _app(session, user_id=user_id, with_transcriber=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/voice/profiles",
                files={"audio": ("a.wav", b"x" * 100, "audio/wav")},
                data={"consent_reference": "politica-v3", "label": "onboarding"},
            )
        assert response.status_code == 201
        profile = response.json()["profile"]
        assert profile["consent_reference"] == "politica-v3"
        assert profile["label"] == "onboarding"

    async def test_refuses_enrolment_without_consent(
        self,
        session: AsyncSession,
    ) -> None:
        """A voiceprint needs specific, highlighted consent."""
        user_id = await _user(session)
        app, _ = _app(session, user_id=user_id, with_transcriber=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/voice/profiles",
                files={"audio": ("a.wav", b"x" * 100, "audio/wav")},
                data={"consent_reference": "   "},
            )
        assert response.status_code == 422
        assert response.json()["code"] == "VOICE_CONSENT_REQUIRED"

    async def test_listing_never_returns_the_embedding(
        self,
        session: AsyncSession,
    ) -> None:
        """The person needs to know a profile exists, not to receive it.

        Handing the biometric template back over HTTP would turn a
        transparency feature into a second copy of the data.
        """
        user_id = await _user(session)
        app, _ = _app(session, user_id=user_id, with_transcriber=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await client.post(
                "/voice/profiles",
                files={"audio": ("a.wav", b"x" * 100, "audio/wav")},
                data={"consent_reference": "doc"},
            )
            response = await client.get("/voice/profiles")
        assert response.status_code == 200
        row = response.json()[0]
        assert "embedding" not in row
        assert "dimensions" not in row
        assert row["consent_reference"] == "doc"

    async def test_erasure_deletes_and_reports(self, session: AsyncSession) -> None:
        """The right the LGPD makes unconditional, as an endpoint."""
        user_id = await _user(session)
        app, _ = _app(session, user_id=user_id, with_transcriber=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            for _ in range(2):
                await client.post(
                    "/voice/profiles",
                    files={"audio": ("a.wav", b"x" * 100, "audio/wav")},
                    data={"consent_reference": "doc"},
                )
            erased = await client.delete("/voice/profiles")
            remaining = await client.get("/voice/profiles")
        assert erased.json() == {"deleted": 2}
        assert remaining.json() == []

    async def test_erasing_nothing_is_success(self, session: AsyncSession) -> None:
        """Deleting data that is not there is not an error."""
        user_id = await _user(session)
        app, _ = _app(session, user_id=user_id, with_transcriber=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.delete("/voice/profiles")
        assert response.status_code == 200
        assert response.json() == {"deleted": 0}

    async def test_a_caller_only_sees_their_own(
        self,
        session: AsyncSession,
    ) -> None:
        """Biometric data must not leak across accounts."""
        ana = await _user(session, "ana@example.com")
        bruno = await _user(session, "bruno@example.com")
        app_ana, _ = _app(session, user_id=ana, with_transcriber=False)
        transport = ASGITransport(app=app_ana)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await client.post(
                "/voice/profiles",
                files={"audio": ("a.wav", b"x" * 100, "audio/wav")},
                data={"consent_reference": "doc"},
            )
        app_bruno, _ = _app(session, user_id=bruno, with_transcriber=False)
        transport = ASGITransport(app=app_bruno)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/voice/profiles")
        assert response.json() == []


class TestCli:
    def test_help_lists_the_commands(self) -> None:
        """The entry point every other command is discovered from."""
        result = CliRunner().invoke(cli_app, ["voice", "--help"])
        assert result.exit_code == 0
        for command in ("models", "diarize", "transcribe"):
            assert command in result.output

    def test_diarize_reports_a_missing_file(self) -> None:
        """The most common mistake gets the clearest message."""
        result = CliRunner().invoke(cli_app, ["voice", "diarize", "/nope/absent.wav"])
        assert result.exit_code == 2
        assert "not found" in result.output
