"""Protocols for the speech backends ``make_genai_router`` accepts.

The ``/transcribe`` and ``/tts`` endpoints call one method each, with the
same keywords whatever runs underneath. These protocols name that shape, so
the local engines (:class:`~tempest_fastapi_sdk.genai.audio.SpeechToText`,
:class:`~tempest_fastapi_sdk.genai.audio.TextToSpeech`) and the daemon-backed
:class:`~tempest_fastapi_sdk.genai.OllamaSpeech` plug into the same router.
The required argument is positional-only so an implementation may name it
freely; the keywords are what the router passes.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.audio.schemas import Transcription


@runtime_checkable
class SpeechTranscriber(Protocol):
    """Anything that turns audio bytes into a :class:`Transcription`."""

    def transcribe(
        self,
        audio: bytes,
        /,
        *,
        language: str | None = None,
    ) -> Awaitable[Transcription]:
        """Transcribe ``audio``.

        Args:
            audio (bytes): The uploaded audio.
            language (str | None): A language the caller asked for, or
                ``None`` to let the backend decide.

        Returns:
            Awaitable[Transcription]: The transcript.
        """
        ...


@runtime_checkable
class SpeechSynthesizer(Protocol):
    """Anything that turns text into WAV bytes."""

    def synthesize(
        self,
        text: str,
        /,
        *,
        language: str | None = None,
        speaker: str | None = None,
    ) -> Awaitable[bytes]:
        """Synthesize ``text`` into WAV audio.

        Args:
            text (str): The text to speak.
            language (str | None): A language the caller asked for, or
                ``None``.
            speaker (str | None): A speaker or voice name, or ``None`` for
                the backend default.

        Returns:
            Awaitable[bytes]: The WAV payload.
        """
        ...


__all__: list[str] = ["SpeechSynthesizer", "SpeechTranscriber"]
