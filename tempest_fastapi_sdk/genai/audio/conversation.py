"""Transcribe a conversation and attribute each line to a speaker.

Joins the two halves the SDK already has — diarization (*who spoke
when*) and speech-to-text (*what was said*) — by overlapping their
timelines.

**The recording is transcribed once, not once per turn.** Feeding
Whisper isolated two-second clips throws away the context it uses to
decide wording and punctuation, and costs one model invocation per turn.
Transcribing whole and then attributing is both cheaper and better, and
it is what the established tools do.

The cost of that choice is the alignment itself: a Whisper segment can
straddle a speaker change, and the whole segment then lands on whichever
speaker holds more of it. :func:`align_turns` reports the overlap it
used, so a caller can see when attribution was close.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tempest_fastapi_sdk.genai.audio.diarization import SpeakerDiarizer
from tempest_fastapi_sdk.genai.audio.schemas import (
    DiarizedTranscription,
    SpeakerTurn,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.genai.audio.language import Language
    from tempest_fastapi_sdk.genai.audio.profiles import VoiceProfileService
    from tempest_fastapi_sdk.genai.audio.schemas import (
        Transcription,
        TranscriptionSegment,
    )
    from tempest_fastapi_sdk.genai.audio.stt import SpeechToText


def _overlap(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
) -> float:
    """Seconds two intervals share.

    Args:
        first_start (float): Start of the first interval.
        first_end (float): End of the first interval.
        second_start (float): Start of the second interval.
        second_end (float): End of the second interval.

    Returns:
        float: Overlap in seconds, ``0.0`` when they are disjoint.
    """
    return max(0.0, min(first_end, second_end) - max(first_start, second_start))


def align_turns(
    turns: list[SpeakerTurn],
    segments: list[TranscriptionSegment],
) -> list[SpeakerTurn]:
    """Attach transcribed text to the speaker turns it overlaps.

    Each transcription segment goes to the turn sharing the most time
    with it. A segment overlapping nothing — speech the diarizer dropped
    as too short, or audio outside every turn — is kept as its own turn
    with speaker ``-1`` rather than discarded: losing words silently is
    worse than admitting the speaker is unknown.

    Args:
        turns (list[SpeakerTurn]): Diarized turns, chronological.
        segments (list[TranscriptionSegment]): Transcribed spans.

    Returns:
        list[SpeakerTurn]: Turns carrying text, chronological, with
        empty turns dropped.
    """
    if not turns:
        return [
            SpeakerTurn(
                start=segment.start,
                end=segment.end,
                speaker=-1,
                text=segment.text.strip(),
            )
            for segment in segments
            if segment.text.strip()
        ]

    texts: dict[int, list[str]] = {index: [] for index in range(len(turns))}
    orphans: list[SpeakerTurn] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        best_index = -1
        best_overlap = 0.0
        for index, turn in enumerate(turns):
            shared = _overlap(segment.start, segment.end, turn.start, turn.end)
            if shared > best_overlap:
                best_overlap = shared
                best_index = index
        if best_index < 0:
            orphans.append(
                SpeakerTurn(
                    start=segment.start,
                    end=segment.end,
                    speaker=-1,
                    text=text,
                ),
            )
            continue
        texts[best_index].append(text)

    aligned = [
        turn.model_copy(update={"text": " ".join(texts[index])})
        for index, turn in enumerate(turns)
        if texts[index]
    ]
    return sorted([*aligned, *orphans], key=lambda turn: turn.start)


class ConversationTranscriber:
    """Produces a transcript split by speaker.

    Holds a diarizer and a speech-to-text engine and runs them over the
    same recording. Both are injected so a service that already has a
    configured :class:`~tempest_fastapi_sdk.genai.audio.stt.SpeechToText`
    reuses it instead of loading Whisper twice.

    Attributes:
        diarizer (SpeakerDiarizer): Decides who spoke when.
        stt (SpeechToText): Decides what was said.
    """

    def __init__(
        self,
        *,
        stt: SpeechToText,
        diarizer: SpeakerDiarizer | None = None,
    ) -> None:
        """Initialize the transcriber.

        Args:
            stt (SpeechToText): Configured speech-to-text engine.
            diarizer (SpeakerDiarizer | None): Configured diarizer.
                ``None`` builds a default one, which downloads its models
                on first use.
        """
        self.stt: SpeechToText = stt
        self.diarizer: SpeakerDiarizer = diarizer or SpeakerDiarizer()

    async def transcribe(
        self,
        audio: str | Path | bytes,
        *,
        language: Language | str | None = None,
        num_speakers: int | None = None,
        identify_with: VoiceProfileService | None = None,
        session: AsyncSession | None = None,
        user_ids: list[UUID] | None = None,
    ) -> DiarizedTranscription:
        """Transcribe ``audio`` and attribute each line to a speaker.

        Diarization and transcription are independent, so they are
        dispatched together rather than in sequence. Both are CPU-bound
        native work in worker threads, so how much wall clock that saves
        depends on the cores available — on a single core it saves
        nothing, and the point is that neither has to wait on the other.

        Args:
            audio (str | Path | bytes): The recording.
            language (Language | str | None): Force a language instead of
                detecting it.
            num_speakers (int | None): Exact speaker count when known.
                **Pass it whenever you can** — clustering by threshold
                alone is the least reliable part of the pipeline; see
                :data:`~tempest_fastapi_sdk.genai.audio.diarization.DEFAULT_CLUSTERING_THRESHOLD`.
            identify_with (VoiceProfileService | None): Match each
                speaker against enrolled voice profiles. Requires
                ``session``. ``None`` leaves the turns anonymous.
            session (AsyncSession | None): Database session for the
                identification lookup.
            user_ids (list[UUID] | None): Restrict identification to
                these people — the meeting's participants, say. Far
                cheaper than searching everyone, and far less likely to
                put a stranger's name on a line.

        Returns:
            DiarizedTranscription: The conversation in order, with the
            full transcript alongside.

        Raises:
            ImportError: When the ``[genai-diarization]`` or
                ``[genai-audio]`` extra is missing.
        """
        if num_speakers is not None:
            self.diarizer.num_speakers = num_speakers
            self.diarizer.unload()

        turns, transcription = await asyncio.gather(
            self.diarizer.diarize(audio),
            self.stt.transcribe(audio, language=language, with_segments=True),
        )
        result = self._assemble(turns, transcription)
        if identify_with is None:
            return result
        if session is None:
            raise ValueError("identify_with needs a session to read profiles from")
        return await self._identify(
            result,
            audio,
            service=identify_with,
            session=session,
            user_ids=user_ids,
        )

    async def _identify(
        self,
        result: DiarizedTranscription,
        audio: str | Path | bytes,
        *,
        service: VoiceProfileService,
        session: AsyncSession,
        user_ids: list[UUID] | None,
    ) -> DiarizedTranscription:
        """Attach enrolled identities to the diarized turns.

        Identification runs **once per speaker cluster**, not once per
        turn: every turn of one cluster is the same voice by
        construction, so embedding each of them separately would pay the
        model N times for one answer — and worse, could label two turns
        of the same person differently.

        The longest turn of each cluster is used, because a longer
        sample makes a better voiceprint and the shortest turn in a
        conversation is often a single word.

        Args:
            result (DiarizedTranscription): The anonymous transcription.
            audio (str | Path | bytes): The recording.
            service (VoiceProfileService): Where the profiles live.
            session (AsyncSession): Database session.
            user_ids (list[UUID] | None): Restrict the search.

        Returns:
            DiarizedTranscription: The same conversation with the turns
            of recognised speakers carrying an id, a name and a score.
        """
        longest: dict[int, SpeakerTurn] = {}
        for turn in result.turns:
            if turn.speaker < 0:
                continue
            current = longest.get(turn.speaker)
            if current is None or turn.duration > current.duration:
                longest[turn.speaker] = turn

        matches: dict[int, Any] = {}
        for speaker, turn in longest.items():
            match = await service.identify_audio(
                session,
                audio=audio,
                start=turn.start,
                end=turn.end,
                user_ids=user_ids,
            )
            if match is not None:
                matches[speaker] = match

        if not matches:
            return result

        turns = [
            turn.model_copy(
                update={
                    "speaker_id": str(matches[turn.speaker].user_id),
                    "confidence": matches[turn.speaker].similarity,
                },
            )
            if turn.speaker in matches
            else turn
            for turn in result.turns
        ]
        return result.model_copy(update={"turns": turns})

    def _assemble(
        self,
        turns: list[SpeakerTurn],
        transcription: Transcription,
    ) -> DiarizedTranscription:
        """Build the result from the two halves.

        Args:
            turns (list[SpeakerTurn]): Diarized turns.
            transcription (Transcription): The full transcription.

        Returns:
            DiarizedTranscription: The assembled conversation.
        """
        aligned = align_turns(turns, transcription.segments)
        speakers = {turn.speaker for turn in aligned if turn.speaker >= 0}
        return DiarizedTranscription(
            text=transcription.text,
            language=transcription.language,
            duration=transcription.duration,
            num_speakers=len(speakers),
            turns=aligned,
        )


__all__: list[str] = [
    "ConversationTranscriber",
    "align_turns",
]
