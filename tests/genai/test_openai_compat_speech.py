"""Tests for ``OpenAICompatSpeech`` (remote transcription, OpenAI wire).

The server is an ``httpx.MockTransport``. The ``verbose_json`` body follows
the ``CreateTranscriptionResponseVerboseJson`` schema of the OpenAI spec; the
error bodies are the shapes ``tempest-ai`` documents in its OpenAPI
(``{"error": {"message", "type", "param", "code"}}``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import FileTooLargeException, ValidationException
from tempest_fastapi_sdk.genai import (
    DEFAULT_OPENAI_URL,
    HostedAuthenticationError,
    HostedServerError,
    OpenAICompatSpeech,
    make_genai_router,
)
from tempest_fastapi_sdk.genai.audio import LanguageEnum, SpeechTranscriber
from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

BASE_URL: str = "https://ai.buscar.app.br/api/openai/v1"
"""The ``tempest-ai`` OpenAI-compatible root the docs use."""

VERBOSE: dict[str, Any] = {
    "task": "transcribe",
    "language": "pt",
    "duration": 3.5,
    "text": " Olá, tudo bem? ",
    "segments": [
        {"id": 0, "start": 0.0, "end": 1.5, "text": " Olá, "},
        {"id": 1, "start": 1.5, "end": 3.5, "text": " tudo bem? "},
    ],
}
"""A ``verbose_json`` transcription body."""

AUDIO: bytes = b"RIFF" + b"\x00" * 64
"""Opaque audio bytes; the mock server never decodes them."""


def _speech(handler: Any, **kwargs: Any) -> OpenAICompatSpeech:
    """Build an ``OpenAICompatSpeech`` whose server is ``handler``.

    Args:
        handler (Any): The ``httpx.MockTransport`` handler.
        **kwargs (Any): Extra constructor arguments.

    Returns:
        OpenAICompatSpeech: The client under test.
    """
    return OpenAICompatSpeech(
        "base",
        api_key="tk-test",
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _field(body: bytes, name: str) -> str | None:
    """Read one plain form field out of a multipart body.

    Args:
        body (bytes): The raw multipart request body.
        name (str): The field name.

    Returns:
        str | None: The field value, or ``None`` when the field is absent.
    """
    marker = f'name="{name}"\r\n\r\n'.encode()
    start = body.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = body.find(b"\r\n", start)
    return body[start:end].decode()


def _error(status: int, message: str, code: str) -> httpx.Response:
    """Build an OpenAI-shaped error response.

    Args:
        status (int): The HTTP status.
        message (str): ``error.message``.
        code (str): ``error.code``.

    Returns:
        httpx.Response: The response.
    """
    return httpx.Response(
        status,
        json={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": None,
                "code": code,
            },
        },
    )


class TestConstruction:
    def test_empty_key_fails_at_construction(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            OpenAICompatSpeech("base", api_key="")

    def test_defaults_point_at_openai(self) -> None:
        speech = OpenAICompatSpeech("whisper-1", api_key="sk")
        assert speech.base_url == DEFAULT_OPENAI_URL
        assert speech.provider == "openai-compatible"

    def test_trailing_slash_is_dropped(self) -> None:
        speech = OpenAICompatSpeech("base", api_key="sk", base_url=BASE_URL + "/")
        assert speech.base_url == BASE_URL


class TestRequestShape:
    async def test_url_header_and_form_fields(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=VERBOSE)

        await _speech(handler).transcribe(AUDIO, language="pt")
        request = seen[0]
        assert request.method == "POST"
        assert str(request.url) == f"{BASE_URL}/audio/transcriptions"
        assert request.headers["Authorization"] == "Bearer tk-test"
        assert request.headers["Content-Type"].startswith("multipart/form-data")
        body = request.content
        assert _field(body, "model") == "base"
        assert _field(body, "language") == "pt"
        assert _field(body, "response_format") == "verbose_json"
        assert b'name="file"; filename="audio"' in body
        assert AUDIO in body

    async def test_no_language_means_the_field_is_absent(self) -> None:
        seen: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json=VERBOSE)

        await _speech(handler).transcribe(AUDIO)
        assert _field(seen[0], "language") is None

    async def test_prompt_and_temperature_are_form_fields(self) -> None:
        seen: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json=VERBOSE)

        await _speech(handler).transcribe(
            AUDIO,
            prompt="Picos, Teresina, Parnaíba",
            temperature=0.0,
        )
        assert _field(seen[0], "prompt") == "Picos, Teresina, Parnaíba"
        assert _field(seen[0], "temperature") == "0.0"

    async def test_no_prompt_or_temperature_means_the_fields_are_absent(
        self,
    ) -> None:
        seen: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json=VERBOSE)

        await _speech(handler).transcribe(AUDIO)
        assert _field(seen[0], "prompt") is None
        assert _field(seen[0], "temperature") is None

    async def test_language_enum_resolves_to_the_iso_code(self) -> None:
        seen: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json=VERBOSE)

        await _speech(handler).transcribe(AUDIO, language=LanguageEnum.PT_BR)
        assert _field(seen[0], "language") == "pt"

    async def test_extra_headers_cannot_replace_authorization(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=VERBOSE)

        speech = _speech(
            handler,
            extra_headers={"Authorization": "Bearer other", "X-Tenant": "t1"},
        )
        await speech.transcribe(AUDIO)
        assert seen[0].headers["Authorization"] == "Bearer tk-test"
        assert seen[0].headers["X-Tenant"] == "t1"


class TestParsing:
    async def test_verbose_json_becomes_a_transcription(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=VERBOSE)

        result = await _speech(handler).transcribe(AUDIO)
        assert result.text == "Olá, tudo bem?"
        assert result.duration == 3.5
        assert result.language == "pt"
        assert result.language_probability == 0.0
        assert [(s.start, s.end, s.text) for s in result.segments] == [
            (0.0, 1.5, "Olá,"),
            (1.5, 3.5, "tudo bem?"),
        ]

    async def test_without_segments_keeps_duration_and_language(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=VERBOSE)

        result = await _speech(handler).transcribe(AUDIO, with_segments=False)
        assert result.segments == []
        assert result.duration == 3.5
        assert result.language == "pt"
        assert result.text == "Olá, tudo bem?"

    async def test_missing_language_falls_back_to_the_requested_code(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"text": "oi", "duration": 1.0})

        speech = _speech(handler)
        assert (await speech.transcribe(AUDIO, language="pt")).language == "pt"
        assert (await speech.transcribe(AUDIO)).language == ""

    async def test_language_is_passed_through_unchanged(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={**VERBOSE, "language": "portuguese"})

        result = await _speech(handler).transcribe(AUDIO, language="pt")
        assert result.language == "portuguese"

    async def test_non_json_success_raises_value_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="Olá")

        with pytest.raises(ValueError, match="not a JSON object"):
            await _speech(handler).transcribe(AUDIO)


class TestInput:
    async def test_path_upload_keeps_the_file_name(self, tmp_path: Path) -> None:
        seen: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json=VERBOSE)

        source = tmp_path / "reuniao.mp3"
        source.write_bytes(AUDIO)
        await _speech(handler).transcribe(source)
        await _speech(handler).transcribe(str(source))
        for body in seen:
            assert b'filename="reuniao.mp3"' in body
            assert AUDIO in body

    async def test_missing_file_raises_without_a_request(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(FileNotFoundError) as caught:
            await _speech(handler).transcribe(tmp_path / "secret" / "a.wav")
        assert str(tmp_path) not in str(caught.value)
        assert "a.wav" in str(caught.value)

    async def test_url_string_is_a_path_not_a_fetch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(FileNotFoundError):
            await _speech(handler).transcribe("https://example.com/a.wav")

    async def test_directory_is_not_a_file(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(FileNotFoundError):
            await _speech(handler).transcribe(tmp_path)


class TestErrors:
    async def test_401_is_an_authentication_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error(401, "Chave de API ausente", "INVALID_API_KEY")

        with pytest.raises(HostedAuthenticationError) as caught:
            await _speech(handler).transcribe(AUDIO)
        assert caught.value.status_code == 401
        assert caught.value.detail == "Chave de API ausente"

    async def test_403_is_an_authentication_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error(403, "sem permissão", "FORBIDDEN")

        with pytest.raises(HostedAuthenticationError):
            await _speech(handler).transcribe(AUDIO)

    async def test_413_is_file_too_large(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error(
                413,
                "O arquivo de áudio passa do teto configurado",
                "AUDIO_TOO_LARGE",
            )

        with pytest.raises(FileTooLargeException) as caught:
            await _speech(handler).transcribe(AUDIO)
        assert caught.value.status_code == 413
        assert caught.value.message == "O arquivo de áudio passa do teto configurado"
        assert caught.value.details["server_code"] == "AUDIO_TOO_LARGE"
        assert caught.value.details["model"] == "base"

    @pytest.mark.parametrize("status", [400, 422])
    async def test_input_refusal_is_a_validation_exception(self, status: int) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _error(status, "áudio inválido", "INVALID_AUDIO")

        with pytest.raises(ValidationException) as caught:
            await _speech(handler).transcribe(AUDIO)
        assert caught.value.status_code == 422
        assert caught.value.message == "áudio inválido"
        assert caught.value.details["status"] == status

    async def test_sdk_detail_shape_is_read_too(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"detail": "EMPTY", "code": "EMPTY_AUDIO"})

        with pytest.raises(ValidationException) as caught:
            await _speech(handler).transcribe(AUDIO)
        assert caught.value.message == "EMPTY"
        assert caught.value.details["server_code"] == "EMPTY_AUDIO"

    async def test_5xx_is_a_server_error(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(503, json={"error": {"message": "down"}})

        speech = _speech(handler, retry_policy=RetryPolicy(max_attempts=1))
        with pytest.raises(HostedServerError):
            await speech.transcribe(AUDIO)
        assert len(calls) == 1

    def test_errors_reach_the_router_as_their_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if b"big" in request.content:
                return _error(413, "grande demais", "AUDIO_TOO_LARGE")
            return _error(400, "áudio inválido", "INVALID_AUDIO")

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(make_genai_router(speech_to_text=_speech(handler)))
        client = TestClient(app)
        bad = client.post(
            "/api/genai/transcribe",
            files={"file": ("clip.wav", b"1234", "audio/wav")},
        )
        big = client.post(
            "/api/genai/transcribe",
            files={"file": ("clip.wav", b"big", "audio/wav")},
        )
        assert bad.status_code == 422
        assert big.status_code == 413


class TestRouter:
    def test_transcribe_route(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=VERBOSE)

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(make_genai_router(speech_to_text=_speech(handler)))
        resp = TestClient(app).post(
            "/api/genai/transcribe?language=pt",
            files={"file": ("clip.wav", AUDIO, "audio/wav")},
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "Olá, tudo bem?"


class TestRouterPrompt:
    def _client(self, handler: Any) -> TestClient:
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(make_genai_router(speech_to_text=_speech(handler)))
        return TestClient(app)

    def test_prompt_form_field_reaches_the_server(self) -> None:
        seen: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json=VERBOSE)

        resp = self._client(handler).post(
            "/api/genai/transcribe",
            files={"file": ("clip.wav", AUDIO, "audio/wav")},
            data={"prompt": "Picos, Teresina"},
        )
        assert resp.status_code == 200
        assert _field(seen[0], "prompt") == "Picos, Teresina"

    def test_prompt_over_the_limit_is_a_422_without_a_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        resp = self._client(handler).post(
            "/api/genai/transcribe",
            files={"file": ("clip.wav", AUDIO, "audio/wav")},
            data={"prompt": "x" * 100_001},
        )
        assert resp.status_code == 422
        assert resp.json()["details"]["field"] == "prompt"


class TestClientLifecycle:
    async def test_owned_client_is_closed_and_recreated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=VERBOSE)

        speech = _speech(handler)
        await speech.transcribe(AUDIO)
        assert speech._client is not None
        await speech.aclose()
        assert speech._client is None
        assert (await speech.transcribe(AUDIO)).text == "Olá, tudo bem?"
        await speech.aclose()

    async def test_injected_client_is_not_closed(self) -> None:
        client = HTTPClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=VERBOSE),
            ),
        )
        speech = OpenAICompatSpeech(
            "base", api_key="tk", base_url=BASE_URL, http_client=client
        )
        await speech.transcribe(AUDIO)
        await speech.aclose()
        response = await client.get(f"{BASE_URL}/models")
        assert response.status_code == 200
        await client.aclose()


class TestProtocols:
    def test_satisfies_speech_transcriber(self) -> None:
        speech = OpenAICompatSpeech("base", api_key="tk")
        assert isinstance(speech, SpeechTranscriber)
