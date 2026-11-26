"""Protocols for the speech backends ``make_genai_router`` accepts.

The ``/transcribe`` and ``/tts`` endpoints call one method each, with the
same keywords whatever runs underneath. These protocols name that shape, so
the local engines (:class:`~tempest_fastapi_sdk.genai.audio.SpeechToText`,
:class:`~tempest_fastapi_sdk.genai.audio.TextToSpeech`) and the daemon-backed
:class:`~tempest_fastapi_sdk.genai.OllamaSpeech` plug into the same router.
The required argument is positional-only so an implementation may name it
freely; the keywords are what the router passes. ``prompt`` and
``temperature`` are part of the transcription shape so code typed against
the protocol can hand a vocabulary hint to whichever backend it holds; the
router itself forwards ``prompt`` only when the request carries one, so a
backend written before those keywords existed still serves requests that do
not use them.
"""

from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.audio.schemas import Transcription


@runtime_checkable
class SpeechTranscriber(Protocol):
    """Anything that turns audio into a :class:`Transcription`.

    ``audio`` is raw bytes (what the ``/transcribe`` endpoint uploads) or a
    file path (what
    :func:`~tempest_fastapi_sdk.agents.transcribe_audio_tool` hands over
    when its ``path_root`` is set). Every SDK backend accepts both.
    """

    def transcribe(
        self,
        audio: bytes | str | Path,
        /,
        *,
        language: str | None = None,
        prompt: str | None = None,
        temperature: float | None = None,
    ) -> Awaitable[Transcription]:
        """Transcribe ``audio``.

        Args:
            audio (bytes | str | Path): The audio bytes, or a path to an
                audio file.
            language (str | None): A language the caller asked for, or
                ``None`` to let the backend decide.
            prompt (str | None): Text the decoder reads as preceding the
                audio, used to bias vocabulary and spelling (domain names,
                proper nouns), or ``None`` for no prompt.
            temperature (float | None): One sampling temperature for the
                decode, or ``None`` for the backend default.

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
