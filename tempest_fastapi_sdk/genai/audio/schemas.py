"""Schemas for self-hosted audio (speech-to-text)."""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class TranscriptionSegment(BaseSchema):
    """One time-stamped span of transcribed speech.

    Attributes:
        start (float): Segment start in seconds.
        end (float): Segment end in seconds.
        text (str): The transcribed text for the span.
    """

    start: float
    end: float
    text: str


class Transcription(BaseSchema):
    """The result of transcribing an audio file.

    Attributes:
        text (str): The full transcript (all segments joined).
        language (str): Detected (or forced) language code.
        language_probability (float): Confidence of the detected language
            (``0..1``); ``0.0`` when the language was forced or unknown.
        duration (float): Audio duration in seconds.
        segments (list[TranscriptionSegment]): Per-span breakdown with
            timestamps (empty when segment output is disabled).
    """

    text: str
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    segments: list[TranscriptionSegment] = Field(default_factory=list)


class SpeakerTurn(BaseSchema):
    """One stretch of speech attributed to a single speaker.

    Attributes:
        start (float): Turn start in seconds.
        end (float): Turn end in seconds.
        speaker (int): Cluster index the diarizer assigned. Stable
            within one recording and meaningless across recordings —
            speaker ``0`` here is not speaker ``0`` in the next file.
        speaker_id (str | None): Enrolled profile this turn matched, when
            identification ran and cleared the threshold. ``None`` means
            *not identified*, which is not the same as *not a person*.
        speaker_name (str | None): Display name of the matched profile.
        confidence (float): Cosine similarity against the matched
            profile, ``0.0`` when no identification ran.
        text (str): What was said, empty until transcription is joined in.
    """

    start: float = Field(
        title="Início (s)",
        description="Offset from the start of the recording.",
        examples=[0.32],
    )
    end: float = Field(
        title="Fim (s)",
        description="Offset from the start of the recording.",
        examples=[6.87],
    )
    speaker: int = Field(
        title="Índice do falante",
        description=(
            "Cluster the diarizer assigned. Stable inside one recording "
            "only — it carries no identity across files."
        ),
        examples=[0, 1],
    )
    speaker_id: str | None = Field(
        default=None,
        title="Perfil identificado",
        description=(
            "Enrolled profile this turn matched. ``null`` means nobody "
            "cleared the threshold, which is different from 'nobody spoke'."
        ),
        examples=["c0ffee00-0000-4000-8000-000000000000", None],
    )
    speaker_name: str | None = Field(
        default=None,
        title="Nome do perfil",
        description="Display name of the matched profile.",
        examples=["Ana Souza", None],
    )
    confidence: float = Field(
        default=0.0,
        title="Similaridade",
        description=(
            "Cosine similarity against the matched profile. ``0.0`` when "
            "no identification ran — not a low-confidence match."
        ),
        examples=[0.687, 0.0],
    )
    text: str = Field(
        default="",
        title="Texto",
        description="What was said in this turn.",
        examples=["Bom dia, tudo bem?"],
    )

    @property
    def duration(self) -> float:
        """Length of the turn in seconds.

        Returns:
            float: ``end - start``.
        """
        return self.end - self.start


class DiarizedTranscription(BaseSchema):
    """A conversation transcribed and split by who was speaking.

    Attributes:
        text (str): The full transcript, speakers not marked.
        language (str): Detected or forced language code.
        duration (float): Audio duration in seconds.
        num_speakers (int): Distinct speakers the diarizer found.
        turns (list[SpeakerTurn]): The conversation in order.
    """

    text: str = Field(
        title="Transcrição",
        description="Full transcript with no speaker markers.",
        examples=["Bom dia. Bom dia, tudo bem?"],
    )
    language: str = Field(
        default="",
        title="Idioma",
        description="Detected or forced language code.",
        examples=["pt", "en"],
    )
    duration: float = Field(
        default=0.0,
        title="Duração (s)",
        description="Length of the audio.",
        examples=[56.9],
    )
    num_speakers: int = Field(
        default=0,
        title="Falantes",
        description="Distinct speakers found.",
        examples=[2, 4],
    )
    turns: list[SpeakerTurn] = Field(
        default_factory=list,
        title="Turnos",
        description="The conversation in chronological order.",
    )

    def by_speaker(self) -> dict[int, str]:
        """Group the transcript by speaker cluster.

        Useful for "what did each person say", which is a different
        question from "how did the conversation go".

        Returns:
            dict[int, str]: Cluster index to everything it said, joined
            in chronological order.
        """
        grouped: dict[int, list[str]] = {}
        for turn in self.turns:
            if turn.text:
                grouped.setdefault(turn.speaker, []).append(turn.text)
        return {speaker: " ".join(parts) for speaker, parts in grouped.items()}

    def transcript(self, *, unknown: str = "Falante {speaker}") -> str:
        """Render the conversation as labelled lines.

        Args:
            unknown (str): Template for an unidentified speaker, with a
                ``{speaker}`` placeholder for the cluster index.

        Returns:
            str: One ``Nome: texto`` line per turn.
        """
        lines: list[str] = []
        for turn in self.turns:
            if not turn.text:
                continue
            label = turn.speaker_name or unknown.format(speaker=turn.speaker)
            lines.append(f"{label}: {turn.text}")
        return "\n".join(lines)


__all__: list[str] = [
    "DiarizedTranscription",
    "SpeakerTurn",
    "Transcription",
    "TranscriptionSegment",
]
