"""Speech synthesis and transcription through an Ollama daemon.

Since v0.40.11 Ollama serves text-to-speech on the OpenAI-compatible
``POST /v1/audio/speech`` endpoint (``phonon``, ``lfm3-audio``), and models
with the ``audio`` capability (``lfm3-audio``) transcribe on
``POST /v1/audio/transcriptions``. :class:`OllamaSpeech` wraps both, plus the
voice list that ``/api/show`` exposes, with the same HTTP client, retry and
error handling as :class:`~tempest_fastapi_sdk.genai.OllamaGenerator`. The
only dependency is ``httpx`` (the ``[genai-ollama]`` extra); no ``torch``, no
weights in this process.

Behaviour measured against Ollama 0.40.13 (2026-10-31) that shapes this
module:

* ``/v1/audio/speech`` streams a WAV whose RIFF and ``data`` sizes are
  ``0xFFFFFFFF``, because the length is unknown when the header is written.
  Python's :mod:`wave` then reports ``2147483647`` frames. Since
  :meth:`OllamaSpeech.synthesize` buffers the whole body, it rewrites both
  sizes to the real ones.
* The transcription endpoint accepts ``language`` but only echoes it back as
  a label: English audio sent with ``language=pt`` came back as English
  text tagged ``"portuguese"``. ``prompt`` had no visible effect. Neither is
  exposed here, so the returned ``language`` is always empty rather than a
  label the daemon did not derive from the audio.
* The OpenAI-compatible endpoints ignore ``keep_alive``: the model stayed
  resident after a request that sent ``keep_alive: 0``. The constructor
  therefore does not take it.

Example:

    >>> speech = OllamaSpeech("lfm3-audio")
    >>> wav = await speech.synthesize("Olá!", voice="female-portuguese")
    >>> result = await speech.transcribe(wav)
    >>> result.text
"""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tempest_fastapi_sdk.genai.audio.schemas import (
    Transcription,
    TranscriptionSegment,
)
from tempest_fastapi_sdk.genai.ollama import (
    DEFAULT_OLLAMA_URL,
    OllamaError,
    _OllamaClientMixin,
)
from tempest_fastapi_sdk.genai.tracing import genai_span

if TYPE_CHECKING:
    import httpx

    from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

SpeechFormat = Literal["wav", "pcm"]

STREAMED_WAV_SIZE: int = 0xFFFFFFFF
"""The placeholder Ollama writes in the RIFF and ``data`` size fields.

Measured on Ollama 0.40.13: the header is written before synthesis ends, so
both 32-bit sizes carry the maximum value.
"""


def _json_body(model: str, response: httpx.Response) -> dict[str, Any]:
    """Decode a success body, or raise when it is not a JSON object.

    Args:
        model (str): The model tag, used in the error.
        response (httpx.Response): The success response.

    Returns:
        dict[str, Any]: The decoded object.

    Raises:
        OllamaError: When the body is not JSON, or is JSON but not an object.
    """
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise OllamaError(
            model,
            f"expected a JSON object, got: {response.text[:200]!r}",
        )
    return body


def _error_detail(response: httpx.Response) -> str:
    """Extract the daemon's error message from a non-success response.

    Ollama answers in two shapes: the native API uses
    ``{"error": "<message>"}`` and the OpenAI-compatible endpoints use
    ``{"error": {"message": "<message>", ...}}``. Anything else falls back to
    the status line and the raw body.

    Args:
        response (httpx.Response): The non-success response.

    Returns:
        str: The most specific message available.
    """
    try:
        body: Any = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text}".strip()
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(error, str) and error:
        return error
    return f"HTTP {response.status_code}: {response.text}".strip()


def fix_streamed_wav_sizes(audio: bytes) -> bytes:
    """Rewrite the RIFF and ``data`` sizes of a fully-buffered streamed WAV.

    Only sizes equal to :data:`STREAMED_WAV_SIZE` are replaced, so a WAV that
    already carries real sizes, PCM, or any non-WAV payload comes back
    unchanged. A placeholder ``data`` size becomes everything after the chunk
    header, which assumes ``data`` is the last chunk -- the layout Ollama
    writes, since the samples are what streams.

    Args:
        audio (bytes): The complete response body.

    Returns:
        bytes: The same audio with real chunk sizes in the header.
    """
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    fixed = bytearray(audio)
    if struct.unpack_from("<I", fixed, 4)[0] == STREAMED_WAV_SIZE:
        struct.pack_into("<I", fixed, 4, len(fixed) - 8)
    offset = 12
    while offset + 8 <= len(fixed):
        chunk_id = bytes(fixed[offset : offset + 4])
        size = struct.unpack_from("<I", fixed, offset + 4)[0]
        if chunk_id == b"data":
            if size == STREAMED_WAV_SIZE:
                struct.pack_into("<I", fixed, offset + 4, len(fixed) - offset - 8)
            break
        offset += 8 + size + (size % 2)
    return bytes(fixed)


class OllamaSpeech(_OllamaClientMixin):
    """Text-to-speech and speech-to-text backed by an Ollama daemon.

    Pull the model first (``ollama pull phonon`` for English speech,
    ``ollama pull lfm3-audio`` for 18 languages and transcription). A model
    without the ``speech`` or ``audio`` capability makes the daemon answer
    ``400``, raised here as :class:`~tempest_fastapi_sdk.genai.OllamaError`
    with the daemon's message.

    ``keep_alive``, ``metrics`` and ``generation_cache`` from the shared
    Ollama client stay ``None``: the speech endpoints ignore ``keep_alive``,
    and there is no text generation to meter or cache.

    Attributes:
        model (str): The Ollama model tag.
        base_url (str): The daemon base URL.
        timeout (float): Per-request HTTP timeout in seconds.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = 120.0,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """Configure the connection to the Ollama daemon.

        Args:
            model (str): The Ollama model tag (e.g. ``"phonon"`` or
                ``"lfm3-audio"``).
            base_url (str): Base URL of the Ollama daemon.
            timeout (float): Per-request HTTP timeout in seconds. The first
                request after a pull loads the model and can be slow:
                measured at 116 s for ``phonon`` and 55 s for ``lfm3-audio``
                on an RTX 4070 Ti SUPER, against 1.4 s for a later reload.
            http_client (HTTPClient | None): An injected client (connection
                reuse). When ``None``, one is created lazily and owned here.
            transport (httpx.AsyncBaseTransport | None): Explicit transport
                for the lazily-created client, such as an
                ``httpx.MockTransport`` in tests.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client; ``None`` keeps the Ollama default of
                not retrying a read timeout. A retryable status (``5xx``)
                is still retried, and each retry redoes the whole synthesis
                or transcription.
        """
        super().__init__(
            model,
            base_url=base_url,
            timeout=timeout,
            http_client=http_client,
            transport=transport,
            retry_policy=retry_policy,
        )

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        response_format: SpeechFormat = "wav",
        out_path: str | Path | None = None,
    ) -> bytes:
        """Generate speech audio from ``text``.

        Args:
            text (str): The text to speak. The daemon rejects empty or
                blank input and input over 4096 characters with ``400``.
            voice (str | None): A voice name from :meth:`voices`
                (case-insensitive on the daemon). ``None`` uses the model's
                ``default`` voice.
            response_format (SpeechFormat): ``"wav"`` (16-bit mono 24 kHz
                with real header sizes) or ``"pcm"`` (the same samples, raw
                little-endian, no header). The daemon refuses other formats.
            out_path (str | Path | None): When given, also write the audio
                there (exactly the returned bytes, so raw samples for
                ``"pcm"``); the parent directory must exist. The bytes are
                returned either way.

        Returns:
            bytes: The synthesized audio.

        Raises:
            OllamaError: When the daemon rejects the request (unknown voice,
                model without speech, input too long, model not pulled).
            httpx.HTTPError: When the daemon cannot be reached or does not
                answer in time.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "input": text,
            "response_format": response_format,
        }
        if voice is not None:
            payload["voice"] = voice
        async with genai_span("speech", self.model):
            response = await self._http().post(
                f"{self.base_url}/v1/audio/speech",
                json=payload,
            )
            if response.is_error:
                raise OllamaError(self.model, _error_detail(response))
            audio = response.content
        if response_format == "wav":
            audio = fix_streamed_wav_sizes(audio)
        if out_path is not None:
            await asyncio.to_thread(Path(out_path).write_bytes, audio)
        return audio

    async def transcribe(self, audio: str | Path | bytes) -> Transcription:
        """Transcribe speech with a model that has the ``audio`` capability.

        The daemon sniffs the container itself: MP3 bytes uploaded under a
        ``.wav`` name, and a WAV uploaded with no extension, were both
        transcribed. Ollama 0.40.13 returns a single segment spanning the
        whole clip, and no language is reported (see the module docstring).

        Args:
            audio (str | Path | bytes): A local file path or the audio
                bytes. A string is always a path; it is never fetched.

        Returns:
            Transcription: The text, the duration the daemon measured, and
                its segments.

        Raises:
            FileNotFoundError: When ``audio`` is not an existing regular
                file. The message carries the file name only, never the full
                path, since the string may come from a form field.
            OllamaError: When the daemon rejects the request (undecodable
                audio, model without audio input, model not pulled) or
                answers with something other than a JSON object.
            httpx.HTTPError: When the daemon cannot be reached or does not
                answer in time.
        """
        if isinstance(audio, bytes):
            content, filename = audio, "audio"
        else:
            path = Path(audio)
            if not await asyncio.to_thread(path.is_file):
                raise FileNotFoundError(
                    f"audio must be a local file, got {path.name!r}",
                )
            content = await asyncio.to_thread(path.read_bytes)
            filename = path.name
        async with genai_span("transcribe", self.model):
            response = await self._http().post(
                f"{self.base_url}/v1/audio/transcriptions",
                data={"model": self.model, "response_format": "verbose_json"},
                files={"file": (filename, content)},
            )
            if response.is_error:
                raise OllamaError(self.model, _error_detail(response))
            data = _json_body(self.model, response)
        return Transcription(
            text=str(data.get("text") or "").strip(),
            duration=float(data.get("duration") or 0.0),
            segments=[
                TranscriptionSegment(
                    start=float(segment.get("start") or 0.0),
                    end=float(segment.get("end") or 0.0),
                    text=str(segment.get("text") or "").strip(),
                )
                for segment in data.get("segments") or []
            ],
        )

    async def voices(self) -> list[str]:
        """List the voices the model offers, as ``/api/show`` reports them.

        Returns:
            list[str]: The voice names, in the daemon's order; empty for a
                model without voices.

        Raises:
            OllamaError: When the daemon rejects the request (model not
                pulled) or answers with something other than a JSON object.
            httpx.HTTPError: When the daemon cannot be reached or does not
                answer in time.
        """
        async with genai_span("voices", self.model):
            response = await self._http().post(
                f"{self.base_url}/api/show",
                json={"model": self.model},
            )
            if response.is_error:
                raise OllamaError(self.model, _error_detail(response))
            data = _json_body(self.model, response)
        return [str(voice) for voice in data.get("voices") or []]


__all__: list[str] = [
    "STREAMED_WAV_SIZE",
    "OllamaSpeech",
    "SpeechFormat",
    "fix_streamed_wav_sizes",
]
