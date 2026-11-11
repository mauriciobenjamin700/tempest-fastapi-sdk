"""Speech-to-text over the OpenAI ``/audio/transcriptions`` wire format.

:class:`OpenAICompatSpeech` sends audio to a remote server that speaks the
OpenAI transcription endpoint, so a service transcribes without running
Whisper in its own process. It satisfies
:class:`~tempest_fastapi_sdk.genai.audio.SpeechTranscriber` and takes the
same ``language`` / ``with_segments`` keywords as the local
:class:`~tempest_fastapi_sdk.genai.audio.SpeechToText`, so swapping one for
the other changes the line that builds the backend and nothing else.

It mirrors :class:`~tempest_fastapi_sdk.genai.OpenAICompatGenerator`:
``base_url`` is the API root **including** the version segment
(``https://api.openai.com/v1``, ``https://ai.buscar.app.br/api/openai/v1``)
and ``/audio/transcriptions`` is appended; the key goes out as
``Authorization: Bearer``; the HTTP client is created lazily, owned, and
closed by :meth:`OpenAICompatSpeech.aclose`.

The request is the multipart form of the ``CreateTranscriptionRequest``
schema of the official OpenAPI document
(https://github.com/openai/openai-openapi): ``file``, ``model``, an optional
``language`` (ISO-639-1) and ``response_format=verbose_json``, the format
that carries ``duration`` and ``segments``. The tests drive an
``httpx.MockTransport``, so what they pin is the request built here and the
response read here, not any live server.

Synthesis (``/audio/speech``) is not wrapped: it needs a second model id
(the text-to-speech one) on the same instance, which this class does not
model.

Example:

    >>> speech = OpenAICompatSpeech(
    ...     "base",
    ...     api_key="tk-...",
    ...     base_url="https://ai.buscar.app.br/api/openai/v1",
    ... )
    >>> result = await speech.transcribe(audio_bytes, language="pt")
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.exceptions.upload import FileTooLargeException
from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.genai.audio.language import LanguageEnum, whisper_language
from tempest_fastapi_sdk.genai.audio.schemas import (
    Transcription,
    TranscriptionSegment,
)
from tempest_fastapi_sdk.genai.hosted import hosted_error
from tempest_fastapi_sdk.genai.openai_compat import DEFAULT_OPENAI_URL
from tempest_fastapi_sdk.genai.tracing import genai_span

if TYPE_CHECKING:
    import httpx

    from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

TRANSCRIPTION_RESPONSE_FORMAT: str = "verbose_json"
"""The ``response_format`` every transcription asks for.

``verbose_json`` is the one format of the OpenAI spec whose body carries
``duration``, ``language`` and ``segments``; ``json`` carries only ``text``.
"""

_INPUT_ERROR_STATUSES: frozenset[int] = frozenset({400, 422})
"""Statuses where the server refused the input itself."""


def _server_message(response: httpx.Response) -> str:
    """Extract the server's error message from a non-success response.

    Reads the OpenAI ``{"error": {"message"}}`` shape and the SDK's own
    ``{"detail": "<message>"}`` shape, falling back to the status line and
    the start of the body.

    Args:
        response (httpx.Response): The non-success response.

    Returns:
        str: The most specific message available.
    """
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
        detail = body.get("detail")
        if isinstance(detail, str) and detail:
            return detail
    return f"HTTP {response.status_code}: {response.text[:500]}".strip()


def _server_code(response: httpx.Response) -> str | None:
    """Read the machine-readable error ``code`` the server sent, if any.

    Args:
        response (httpx.Response): The non-success response.

    Returns:
        str | None: ``error.code`` (OpenAI shape) or ``code`` (SDK shape),
        or ``None`` when the body carries neither.
    """
    try:
        body: Any = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict) and error.get("code"):
        return str(error["code"])
    code = body.get("code")
    return str(code) if code else None


class OpenAICompatSpeech:
    """Transcription against any OpenAI-compatible ``/audio/transcriptions``.

    Satisfies :class:`~tempest_fastapi_sdk.genai.audio.SpeechTranscriber`,
    so it backs ``/transcribe`` in
    :func:`~tempest_fastapi_sdk.genai.make_genai_router` and replaces a
    local :class:`~tempest_fastapi_sdk.genai.audio.SpeechToText`.

    Attributes:
        model (str): The transcription model id the server serves.
        base_url (str): API root, without a trailing slash.
        timeout (float): Per-request HTTP timeout in seconds.
        provider (str): Label carried by the errors this instance raises.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        base_url: str = DEFAULT_OPENAI_URL,
        timeout: float = 300.0,
        extra_headers: dict[str, str] | None = None,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        provider: str = "openai-compatible",
    ) -> None:
        """Configure the client. No network call happens here.

        Args:
            model (str): Transcription model id. A server that serves one
                model (``tempest-ai`` lists it on ``GET {base_url}/models``)
                refuses any other.
            api_key (str): Bearer token. Empty raises immediately, where the
                configuration is read, instead of as a ``401`` from the
                first request.
            base_url (str): API root including the version segment;
                ``/audio/transcriptions`` is appended. The default points at
                OpenAI.
            timeout (float): Per-request HTTP timeout in seconds. Higher
                than the generator's default because the server decodes the
                whole clip before it answers.
            extra_headers (dict[str, str] | None): Headers added to every
                request. ``Authorization`` is always set from ``api_key``
                and cannot be overridden here.
            http_client (HTTPClient | None): An injected client for
                connection reuse. When ``None``, one is created lazily and
                owned by this instance.
            transport (httpx.AsyncBaseTransport | None): Explicit transport
                for the lazily-created client — pass an
                ``httpx.MockTransport`` in tests. Ignored when
                ``http_client`` is given.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client. ``None`` keeps the ``HTTPClient``
                defaults but does not retry a read timeout, as in
                :class:`~tempest_fastapi_sdk.genai.OpenAICompatGenerator`: a
                retry uploads the audio again and the server transcribes it
                again from zero. A connection error and a retryable status
                (``429``, ``5xx``) are still retried. Ignored when
                ``http_client`` is given.
            provider (str): Label carried by the errors this instance raises,
                so a log line says which account failed.

        Raises:
            ValueError: When ``api_key`` is empty.
        """
        if not api_key:
            raise ValueError(
                "OpenAICompatSpeech requires an api_key; it was empty. "
                "Read it from settings/environment at construction time.",
            )
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.provider = provider
        self._api_key = api_key
        self._extra_headers = dict(extra_headers or {})
        self._client: HTTPClient | None = http_client
        self._owns_client: bool = http_client is None
        self._transport = transport
        self._retry_policy = retry_policy

    def _http(self) -> HTTPClient:
        """Return the HTTP client, creating an owned one on first use.

        Built lazily so constructing the backend never needs a running event
        loop.

        Returns:
            HTTPClient: The resilient client used for API requests.
        """
        if self._client is None:
            from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

            self._client = HTTPClient(
                timeout=self.timeout,
                transport=self._transport,
                retry_policy=self._retry_policy
                or RetryPolicy(retry_on_read_timeout=False),
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        """Build the request headers, with ``Authorization`` last.

        Returns:
            dict[str, str]: Headers for one request.
        """
        headers = dict(self._extra_headers)
        headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise the typed error for a non-success response.

        Args:
            response (httpx.Response): The response to check.

        Raises:
            FileTooLargeException: On ``413`` — the audio is over the
                server's upload cap (``413`` behind the SDK's handlers).
            ValidationException: On ``400`` or ``422`` — the server refused
                the input (empty or undecodable audio, a bad field), with
                its message (``422`` behind the SDK's handlers).
            HostedAPIError: On any other error status — a
                :class:`~tempest_fastapi_sdk.genai.HostedAuthenticationError`
                for ``401``/``403``,
                :class:`~tempest_fastapi_sdk.genai.HostedRateLimitError` for
                ``429``, :class:`~tempest_fastapi_sdk.genai.HostedServerError`
                for ``5xx``.
        """
        if response.is_success:
            return
        status = response.status_code
        details: dict[str, Any] = {
            "backend": self.provider,
            "model": self.model,
            "status": status,
        }
        code = _server_code(response)
        if code is not None:
            details["server_code"] = code
        if status == 413:
            raise FileTooLargeException(
                message=_server_message(response),
                details=details,
            ) from hosted_error(self.provider, response)
        if status in _INPUT_ERROR_STATUSES:
            raise ValidationException(
                message=_server_message(response),
                details=details,
            ) from hosted_error(self.provider, response)
        raise hosted_error(self.provider, response)

    async def transcribe(
        self,
        audio: bytes | str | Path,
        /,
        *,
        language: LanguageEnum | str | None = None,
        with_segments: bool = True,
    ) -> Transcription:
        """Transcribe ``audio`` on the remote server.

        Args:
            audio (bytes | str | Path): The audio bytes, or a local file
                path. A string is always a path; it is never fetched.
            language (LanguageEnum | str | None): Force the language — a
                :class:`~tempest_fastapi_sdk.genai.audio.LanguageEnum`
                member (resolved to its Whisper code, ``"pt"``), a raw
                ISO-639-1 code, or ``None`` to let the server detect it
                (the field is then not sent).
            with_segments (bool): Keep the per-span timestamps. ``False``
                returns ``segments=[]``; the request is the same either way,
                so ``duration`` and ``language`` are still filled.

        Returns:
            Transcription: ``text`` and ``duration`` from the response,
            ``language`` from the response's ``language`` field (unchanged)
            or, when the response has none, the requested code (``""`` when
            nothing was requested), and the segments. ``language_probability``
            stays ``0.0``: the wire format does not carry it.

        Raises:
            FileNotFoundError: When ``audio`` is a path that is not an
                existing regular file. The message carries the file name
                only, never the full path, since the string may come from a
                form field.
            FileTooLargeException: When the server answers ``413``.
            ValidationException: When the server answers ``400`` or ``422``.
            HostedAPIError: A typed subclass for any other error status
                (``HostedAuthenticationError`` for ``401``/``403``).
            ValueError: When a success response is not a JSON object.
            httpx.HTTPError: When the server cannot be reached or does not
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
        code = whisper_language(language)
        form: dict[str, str] = {
            "model": self.model,
            "response_format": TRANSCRIPTION_RESPONSE_FORMAT,
        }
        if code:
            form["language"] = code
        async with genai_span("transcribe", self.model):
            response = await self._http().post(
                f"{self.base_url}/audio/transcriptions",
                data=form,
                files={"file": (filename, content)},
                headers=self._headers(),
            )
            self._raise_for_status(response)
            try:
                data: Any = response.json()
            except ValueError:
                data = None
        if not isinstance(data, dict):
            raise ValueError(
                f"{self.provider} answered a transcription that is not a JSON "
                f"object: {response.text[:200]!r}",
            )
        segments = [
            TranscriptionSegment(
                start=float(segment.get("start") or 0.0),
                end=float(segment.get("end") or 0.0),
                text=str(segment.get("text") or "").strip(),
            )
            for segment in data.get("segments") or []
            if isinstance(segment, dict)
        ]
        return Transcription(
            text=str(data.get("text") or "").strip(),
            language=str(data.get("language") or code or ""),
            duration=float(data.get("duration") or 0.0),
            segments=segments if with_segments else [],
        )

    async def aclose(self) -> None:
        """Close the HTTP client when this instance owns it.

        An injected client is left open for whoever injected it.
        """
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


__all__: list[str] = ["TRANSCRIPTION_RESPONSE_FORMAT", "OpenAICompatSpeech"]
