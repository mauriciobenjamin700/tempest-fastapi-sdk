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


__all__: list[str] = [
    "Transcription",
    "TranscriptionSegment",
]
