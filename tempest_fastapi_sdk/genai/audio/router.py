"""An opt-in FastAPI router for transcription, enrolment and erasure.

Every route here is gated behind ``dependencies=`` that the project
supplies. That is not the usual "wire your auth here" boilerplate: these
endpoints accept uploaded audio, read biometric data and write it, and
an unauthenticated route doing any of the three is a different kind of
mistake from an unauthenticated read.

Two routes exist purely because the law says the person owns this data:
``GET /voice/profiles`` lets them see what is enrolled and
``DELETE /voice/profiles`` erases it. Shipping the enrolment endpoint
without them would be shipping half a feature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile, status
from pydantic import Field

from tempest_fastapi_sdk.genai.audio.schemas import DiarizedTranscription
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.genai.audio.conversation import ConversationTranscriber
    from tempest_fastapi_sdk.genai.audio.profiles import VoiceProfileService

DEFAULT_MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
"""Largest upload accepted, in bytes.

Audio arrives whole and is held in memory while it is decoded, so an
unbounded upload is a way to exhaust the worker. 25 MiB is roughly
half an hour of 16 kHz mono WAV — beyond that the request belongs in a
background job, not a synchronous endpoint.
"""


class VoiceProfileSchema(BaseSchema):
    """One enrolled voice profile, as the owner sees it.

    The embedding itself is deliberately absent: the person needs to
    know *that* a profile exists and when it was made, not to receive a
    copy of their own biometric template over HTTP.

    Attributes:
        id (UUID): Profile identifier, for deletion.
        label (str | None): What the enrolment recording was.
        model_name (str): Embedding model that produced it.
        consent_at (str): When consent was recorded, ISO-8601.
        consent_reference (str): What they consented to.
        created_at (str): When the profile was enrolled, ISO-8601.
        last_matched_at (str | None): Last time it matched, ISO-8601.
    """

    id: UUID = Field(title="ID do perfil", description="Use it to delete this profile.")
    label: str | None = Field(default=None, title="Rótulo")
    model_name: str = Field(title="Modelo", description="Embedding model used.")
    consent_at: str = Field(title="Consentimento em")
    consent_reference: str = Field(title="Referência do consentimento")
    created_at: str = Field(title="Cadastrado em")
    last_matched_at: str | None = Field(default=None, title="Último uso")


class EnrollmentResponseSchema(BaseSchema):
    """Result of enrolling a voice.

    Attributes:
        profile (VoiceProfileSchema): The stored profile.
    """

    profile: VoiceProfileSchema = Field(title="Perfil cadastrado")


class ErasureResponseSchema(BaseSchema):
    """Result of erasing someone's voice profiles.

    Attributes:
        deleted (int): How many profiles were removed. ``0`` is a
            successful outcome — erasing data that is not there is not
            an error.
    """

    deleted: int = Field(
        title="Perfis apagados",
        description="Zero is success, not failure.",
        examples=[0, 3],
    )


def _render_profile(profile: Any) -> VoiceProfileSchema:
    """Render a stored profile without its embedding.

    Args:
        profile (Any): The persisted profile.

    Returns:
        VoiceProfileSchema: The response model.
    """
    return VoiceProfileSchema(
        id=profile.id,
        label=profile.label,
        model_name=profile.model_name,
        consent_at=profile.consent_at.isoformat(),
        consent_reference=profile.consent_reference,
        created_at=profile.created_at.isoformat(),
        last_matched_at=(
            profile.last_matched_at.isoformat() if profile.last_matched_at else None
        ),
    )


async def _read_upload(upload: UploadFile, *, max_bytes: int) -> bytes:
    """Read an uploaded recording, refusing one that is too large.

    Reads in chunks and stops at the ceiling rather than reading the
    whole body and measuring afterwards — measuring afterwards means the
    oversized upload already occupied the memory it was meant to be
    denied.

    Args:
        upload (UploadFile): The uploaded file.
        max_bytes (int): Ceiling.

    Returns:
        bytes: The recording.

    Raises:
        ValidationException: When the upload exceeds the ceiling.
    """
    from tempest_fastapi_sdk.exceptions import ValidationException

    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1 << 20):
        total += len(chunk)
        if total > max_bytes:
            raise ValidationException(
                message=f"audio is larger than {max_bytes} bytes",
                details={"max_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)


def make_voice_router(
    *,
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    transcriber: ConversationTranscriber | None = None,
    profiles: VoiceProfileService | None = None,
    current_user_id: Callable[..., Any] | None = None,
    prefix: str = "/voice",
    tags: list[str] | None = None,
    dependencies: Sequence[Any] | None = None,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> APIRouter:
    """Build the voice router, mounting only what was injected.

    Args:
        session_factory (Callable): FastAPI dependency yielding an async
            session.
        transcriber (ConversationTranscriber | None): Mounts
            ``POST /voice/transcribe`` when given.
        profiles (VoiceProfileService | None): Mounts the enrolment,
            listing and erasure routes when given.
        current_user_id (Callable[..., Any] | None): Dependency
            resolving the caller's user id. **Required** alongside
            ``profiles``: enrolling or erasing on behalf of a user id
            taken from the request body would let anyone write biometric
            data against somebody else's account.
        prefix (str): URL prefix.
        tags (list[str] | None): OpenAPI tags.
        dependencies (Sequence[Any] | None): Applied to every route —
            where authentication and rate limiting go.
        max_upload_bytes (int): Largest accepted upload.

    Returns:
        APIRouter: Ready to mount.

    Raises:
        ValueError: When ``profiles`` is given without
            ``current_user_id``, or when neither surface was injected —
            an empty router is a wiring mistake, not a configuration.
    """
    if profiles is not None and current_user_id is None:
        raise ValueError(
            "profiles= needs current_user_id=: taking the user id from the "
            "request would let a caller enrol or erase somebody else's voice",
        )
    if transcriber is None and profiles is None:
        raise ValueError("make_voice_router needs a transcriber, profiles, or both")

    from fastapi import Depends

    router = APIRouter(
        prefix=prefix,
        tags=list(tags or ["voice"]),
        dependencies=list(dependencies or []),
    )

    async def _session() -> AsyncIterator[AsyncSession]:
        async for item in session_factory():
            yield item

    session_dep = Depends(_session)

    if transcriber is not None:
        service_transcriber = transcriber

        @router.post(
            "/transcribe",
            response_model=DiarizedTranscription,
            summary="Transcribe a conversation, split by speaker",
            description=(
                "Uploads a recording and returns the transcript with one "
                "entry per speaker turn.\n\n"
                "**Pass `num_speakers` whenever you know it** — on a "
                "two-party call, an interview, a support conversation you "
                "do. Clustering by threshold alone is the least reliable "
                "part of the pipeline.\n\n"
                "Turns carry `speaker`, a cluster index that is stable "
                "inside this recording and meaningless across recordings. "
                "A turn with `speaker = -1` is speech that fell outside "
                "every detected turn: it is transcribed rather than "
                "dropped, because losing words silently is worse."
            ),
        )
        async def transcribe(
            audio: UploadFile = File(description="The recording."),
            language: str | None = Form(default=None),
            num_speakers: int | None = Form(default=None),
            session: AsyncSession = session_dep,
        ) -> DiarizedTranscription:
            """Transcribe an uploaded conversation.

            Args:
                audio (UploadFile): The recording.
                language (str | None): Force a language.
                num_speakers (int | None): Exact speaker count when known.
                session (AsyncSession): Request-scoped DB session.

            Returns:
                DiarizedTranscription: The conversation, split by speaker.
            """
            payload = await _read_upload(audio, max_bytes=max_upload_bytes)
            return await service_transcriber.transcribe(
                payload,
                language=language,
                num_speakers=num_speakers,
            )

    if profiles is not None and current_user_id is not None:
        service_profiles = profiles
        user_dep = Depends(current_user_id)

        @router.post(
            "/profiles",
            response_model=EnrollmentResponseSchema,
            status_code=status.HTTP_201_CREATED,
            summary="Enrol the caller's voice",
            description=(
                "Records a voiceprint for the **authenticated caller** — "
                "the user id comes from the session, never from the "
                "body.\n\n"
                "`consent_reference` is required and identifies what the "
                "person agreed to: a policy version, a signed document, a "
                "ticket. A voiceprint is sensitive personal data and needs "
                "specific, highlighted consent; a blank reference returns "
                "**422**.\n\n"
                "The audio is used to compute the vector and discarded. "
                "Nothing stores the recording."
            ),
        )
        async def enroll(
            audio: UploadFile = File(description="At least 3 seconds of speech."),
            consent_reference: str = Form(
                description="Evidence of the person's consent.",
            ),
            label: str | None = Form(default=None),
            session: AsyncSession = session_dep,
            user_id: Any = user_dep,
        ) -> EnrollmentResponseSchema:
            """Enrol the caller's voice.

            Args:
                audio (UploadFile): The enrolment recording.
                consent_reference (str): Evidence of consent.
                label (str | None): What this recording was.
                session (AsyncSession): Request-scoped DB session.
                user_id (Any): The authenticated caller.

            Returns:
                EnrollmentResponseSchema: The stored profile.
            """
            payload = await _read_upload(audio, max_bytes=max_upload_bytes)
            profile = await service_profiles.enroll(
                session,
                user_id=user_id,
                audio=payload,
                consent_reference=consent_reference,
                label=label,
            )
            await session.commit()
            return EnrollmentResponseSchema(profile=_render_profile(profile))

        @router.get(
            "/profiles",
            response_model=list[VoiceProfileSchema],
            summary="List the caller's enrolled voice profiles",
            description=(
                "What is enrolled, when, and under which consent. The "
                "embedding itself is **not** returned: the person needs to "
                "know a profile exists, not to receive a copy of their own "
                "biometric template over HTTP.\n\n"
                "Returns `200` with an empty list when there are none."
            ),
        )
        async def list_profiles(
            session: AsyncSession = session_dep,
            user_id: Any = user_dep,
        ) -> list[VoiceProfileSchema]:
            """List the caller's profiles.

            Args:
                session (AsyncSession): Request-scoped DB session.
                user_id (Any): The authenticated caller.

            Returns:
                list[VoiceProfileSchema]: The profiles, possibly empty.
            """
            stored = await service_profiles.list_profiles(session, user_id=user_id)
            return [_render_profile(profile) for profile in stored]

        @router.delete(
            "/profiles",
            response_model=ErasureResponseSchema,
            summary="Erase every voice profile the caller enrolled",
            description=(
                "Deletes the caller's biometric data. This is the request "
                "the LGPD makes unconditional (Art. 18, VI), which is why "
                "it is an endpoint rather than a support ticket.\n\n"
                "Deleting when there is nothing to delete returns `200` "
                "with `deleted: 0` — a successful outcome, not an error."
            ),
        )
        async def erase(
            session: AsyncSession = session_dep,
            user_id: Any = user_dep,
        ) -> ErasureResponseSchema:
            """Erase the caller's voice profiles.

            Args:
                session (AsyncSession): Request-scoped DB session.
                user_id (Any): The authenticated caller.

            Returns:
                ErasureResponseSchema: How many were deleted.
            """
            deleted = await service_profiles.forget_user(session, user_id=user_id)
            await session.commit()
            return ErasureResponseSchema(deleted=deleted)

    return router


__all__: list[str] = [
    "DEFAULT_MAX_UPLOAD_BYTES",
    "EnrollmentResponseSchema",
    "ErasureResponseSchema",
    "VoiceProfileSchema",
    "make_voice_router",
]
