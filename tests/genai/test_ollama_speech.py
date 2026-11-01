"""Tests for the Ollama speech backend (TTS, STT and voices).

The daemon is replaced by ``httpx.MockTransport``. Every response body below
is a shape captured from Ollama 0.40.13 on 2026-10-31, including the streamed
WAV header whose RIFF and ``data`` sizes are ``0xFFFFFFFF``.
"""

from __future__ import annotations

import io
import json
import struct
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.genai import OllamaError, OllamaSpeech
from tempest_fastapi_sdk.genai.audio import SpeechSynthesizer, SpeechTranscriber
from tempest_fastapi_sdk.genai.ollama_speech import (
    PCM_MEDIA_TYPE,
    STREAMED_WAV_SIZE,
    fix_streamed_wav_sizes,
)
from tempest_fastapi_sdk.utils.http_client import HTTPClient

CAPTURED_HEADER: bytes = bytes.fromhex(
    "52494646ffffffff57415645666d74201000000001000100"
    "c05d000080bb00000200100064617461ffffffff",
)
"""The first 44 bytes of a ``/v1/audio/speech`` WAV from Ollama 0.40.13."""

SAMPLES: bytes = b"\x01\x00" * 2400
"""0.1 s of 16-bit mono 24 kHz audio."""

STREAMED_WAV: bytes = CAPTURED_HEADER + SAMPLES


VERBOSE_TRANSCRIPTION: dict[str, Any] = {
    "task": "transcribe",
    "duration": 7.04,
    "text": "Olá, este áudio foi gerado localmente.",
    "segments": [
        {
            "id": 0,
            "start": 0,
            "end": 7.04,
            "text": "Olá, este áudio foi gerado localmente.",
        },
    ],
}
"""A ``verbose_json`` transcription body as Ollama 0.40.13 returns it."""


def _frames(audio: bytes) -> tuple[int, int]:
    """Read a WAV the way a consumer would.

    Args:
        audio (bytes): The WAV bytes.

    Returns:
        tuple[int, int]: The frame count and the frame rate the header reports.
    """
    with wave.open(io.BytesIO(audio)) as reader:
        return reader.getnframes(), reader.getframerate()


def _speech(handler: Any) -> OllamaSpeech:
    """Build an ``OllamaSpeech`` whose daemon is ``handler``.

    Args:
        handler (Any): The ``httpx.MockTransport`` handler.

    Returns:
        OllamaSpeech: An instance bound to the mock transport.
    """
    return OllamaSpeech("lfm3-audio", transport=httpx.MockTransport(handler))


class TestFixStreamedWavSizes:
    def test_captured_header_breaks_wave_before_the_fix(self) -> None:
        assert _frames(STREAMED_WAV)[0] == 2147483647

    def test_rewrites_both_sizes_to_the_real_ones(self) -> None:
        fixed = fix_streamed_wav_sizes(STREAMED_WAV)
        assert struct.unpack_from("<I", fixed, 4)[0] == len(STREAMED_WAV) - 8
        assert struct.unpack_from("<I", fixed, 40)[0] == len(SAMPLES)
        assert _frames(fixed) == (2400, 24000)
        assert fixed[44:] == SAMPLES

    def test_real_sizes_are_left_alone(self) -> None:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(24000)
            writer.writeframes(SAMPLES)
        assert fix_streamed_wav_sizes(buffer.getvalue()) == buffer.getvalue()

    def test_skips_chunks_before_data(self) -> None:
        extra = b"LIST" + struct.pack("<I", 4) + b"INFO"
        audio = CAPTURED_HEADER[:36] + extra + CAPTURED_HEADER[36:] + SAMPLES
        fixed = fix_streamed_wav_sizes(audio)
        assert struct.unpack_from("<I", fixed, 52)[0] == len(SAMPLES)
        assert _frames(fixed)[0] == 2400

    @pytest.mark.parametrize("payload", [b"", b"RIFF", SAMPLES, b"ID3" + SAMPLES])
    def test_non_wav_payload_is_unchanged(self, payload: bytes) -> None:
        assert fix_streamed_wav_sizes(payload) == payload

    def test_odd_chunk_before_data_is_padded(self) -> None:
        extra = b"junk" + struct.pack("<I", 3) + b"abc\x00"
        audio = CAPTURED_HEADER[:36] + extra + CAPTURED_HEADER[36:] + SAMPLES
        fixed = fix_streamed_wav_sizes(audio)
        assert struct.unpack_from("<I", fixed, 52)[0] == len(SAMPLES)
        assert _frames(fixed)[0] == 2400

    def test_only_the_placeholder_size_is_rewritten(self) -> None:
        real_data = CAPTURED_HEADER[:40] + struct.pack("<I", len(SAMPLES)) + SAMPLES
        fixed = fix_streamed_wav_sizes(real_data)
        assert struct.unpack_from("<I", fixed, 4)[0] == len(real_data) - 8
        assert fixed[40:] == real_data[40:]

    def test_header_without_chunks_is_unchanged_past_riff(self) -> None:
        fixed = fix_streamed_wav_sizes(CAPTURED_HEADER[:12])
        assert fixed[8:] == b"WAVE"

    def test_placeholder_constant(self) -> None:
        assert STREAMED_WAV_SIZE == 4294967295


class TestSynthesize:
    async def test_posts_openai_payload_and_fixes_the_header(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                content=STREAMED_WAV,
                headers={"content-type": "audio/wav"},
            )

        speech = _speech(handler)
        audio = await speech.synthesize("Olá", voice="female-portuguese")
        assert captured["path"] == "/v1/audio/speech"
        assert captured["body"] == {
            "model": "lfm3-audio",
            "input": "Olá",
            "response_format": "wav",
            "voice": "female-portuguese",
        }
        assert _frames(audio)[0] == 2400

    async def test_no_voice_and_no_keep_alive_are_sent(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, content=STREAMED_WAV)

        await _speech(handler).synthesize("hi")
        assert "voice" not in captured["body"]
        assert "keep_alive" not in captured["body"]

    async def test_pcm_is_returned_untouched(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content)["response_format"] == "pcm"
            return httpx.Response(200, content=SAMPLES)

        assert await _speech(handler).synthesize("hi", response_format="pcm") == SAMPLES

    async def test_out_path_gets_the_same_bytes(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=STREAMED_WAV)

        target = tmp_path / "speech.wav"
        audio = await _speech(handler).synthesize("hi", out_path=target)
        assert target.read_bytes() == audio

    async def test_daemon_400_becomes_validation_exception(self) -> None:
        detail = 'voice "nope" not found; available voices: Alfie, default'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": detail,
                        "type": "invalid_request_error",
                        "param": None,
                        "code": None,
                    },
                },
            )

        with pytest.raises(ValidationException) as caught:
            await _speech(handler).synthesize("x", voice="nope")
        assert caught.value.status_code == 422
        assert caught.value.message == detail
        assert caught.value.details == {"backend": "ollama", "model": "lfm3-audio"}

    async def test_model_not_pulled_stays_ollama_error(self) -> None:
        detail = 'model "nao-existe" not found, try pulling it first'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"error": {"message": detail, "type": "not_found_error"}},
            )

        with pytest.raises(OllamaError) as caught:
            await _speech(handler).synthesize("x")
        assert caught.value.detail == detail
        assert caught.value.model == "lfm3-audio"

    async def test_speaker_is_sent_as_voice(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, content=STREAMED_WAV)

        await _speech(handler).synthesize("hi", speaker="male-portuguese")
        assert captured["body"]["voice"] == "male-portuguese"
        assert "speaker" not in captured["body"]

    async def test_voice_and_speaker_must_agree(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        speech = _speech(handler)
        with pytest.raises(ValidationException) as caught:
            await speech.synthesize("hi", voice="a", speaker="b")
        assert caught.value.details == {"field": "speaker"}

    async def test_language_is_refused_before_the_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(ValidationException) as caught:
            await _speech(handler).synthesize("hi", language="pt")
        assert caught.value.details == {"field": "language"}


class TestTranscribe:
    async def test_bytes_are_uploaded_as_multipart(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = request.content
            return httpx.Response(200, json=VERBOSE_TRANSCRIPTION)

        result = await _speech(handler).transcribe(STREAMED_WAV)
        assert captured["path"] == "/v1/audio/transcriptions"
        body: bytes = captured["body"]
        assert b'name="model"\r\n\r\nlfm3-audio' in body
        assert b'name="response_format"\r\n\r\nverbose_json' in body
        assert b'name="file"; filename="audio"' in body
        assert STREAMED_WAV in body
        assert b'name="language"' not in body
        assert result.text == "Olá, este áudio foi gerado localmente."
        assert result.duration == 7.04
        assert result.language == ""
        assert [(s.start, s.end) for s in result.segments] == [(0.0, 7.04)]

    async def test_path_upload_keeps_the_file_name(self, tmp_path: Path) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(200, json=VERBOSE_TRANSCRIPTION)

        source = tmp_path / "reuniao.mp3"
        source.write_bytes(b"ID3" + SAMPLES)
        await _speech(handler).transcribe(str(source))
        assert b'filename="reuniao.mp3"' in captured["body"]

    async def test_echoed_language_label_is_not_trusted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={**VERBOSE_TRANSCRIPTION, "language": "portuguese"}
            )

        assert (await _speech(handler).transcribe(SAMPLES)).language == ""

    async def test_missing_file_raises_without_a_request(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(FileNotFoundError):
            await _speech(handler).transcribe(tmp_path / "missing.wav")

    async def test_url_string_is_a_path_not_a_fetch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(FileNotFoundError):
            await _speech(handler).transcribe("https://example.com/a.wav")

    async def test_missing_file_message_hides_the_directory(
        self,
        tmp_path: Path,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(FileNotFoundError) as caught:
            await _speech(handler).transcribe(tmp_path / "secret" / "a.wav")
        assert str(tmp_path) not in str(caught.value)
        assert "a.wav" in str(caught.value)

    async def test_directory_is_not_a_file(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(FileNotFoundError):
            await _speech(handler).transcribe(tmp_path)

    async def test_non_json_success_becomes_ollama_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="Olá")

        with pytest.raises(OllamaError, match="expected a JSON object"):
            await _speech(handler).transcribe(SAMPLES)

    async def test_undecodable_audio_becomes_ollama_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Failed to load image or audio file",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": None,
                    },
                },
            )

        with pytest.raises(ValidationException) as caught:
            await _speech(handler).transcribe(b"\x00" * 16)
        assert caught.value.message == "Failed to load image or audio file"

    async def test_language_is_refused_before_the_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(ValidationException) as caught:
            await _speech(handler).transcribe(SAMPLES, language="pt")
        assert caught.value.details == {"field": "language"}


class TestVoices:
    async def test_reads_voices_from_api_show(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/show"
            assert json.loads(request.content) == {"model": "lfm3-audio"}
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "audio", "speech", "tools"],
                    "voices": ["default", "female-portuguese"],
                },
            )

        assert await _speech(handler).voices() == ["default", "female-portuguese"]

    async def test_model_without_voices_is_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"capabilities": ["completion"]})

        assert await _speech(handler).voices() == []

    async def test_json_list_becomes_ollama_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["default"])

        with pytest.raises(OllamaError, match="expected a JSON object"):
            await _speech(handler).voices()

    async def test_native_error_body_becomes_ollama_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404, json={"error": "model 'nao-existe:latest' not found"}
            )

        with pytest.raises(OllamaError) as caught:
            await _speech(handler).voices()
        assert caught.value.detail == "model 'nao-existe:latest' not found"

    async def test_non_json_error_keeps_status_and_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="404 page not found")

        with pytest.raises(OllamaError) as caught:
            await _speech(handler).voices()
        assert caught.value.detail == "HTTP 404: 404 page not found"


class TestClientLifecycle:
    async def test_injected_client_is_not_closed(self) -> None:
        client = HTTPClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
        speech = OllamaSpeech("phonon", http_client=client)
        await speech.voices()
        await speech.aclose()
        response = await client.get("http://127.0.0.1:11434/api/version")
        assert response.status_code == 200
        await client.aclose()


class TestProtocols:
    def test_satisfies_both_router_protocols(self) -> None:
        speech = OllamaSpeech("lfm3-audio")
        assert isinstance(speech, SpeechTranscriber)
        assert isinstance(speech, SpeechSynthesizer)


class TestStreamSpeech:
    async def test_chunks_arrive_and_pcm_is_the_default(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, content=SAMPLES)

        chunks = [c async for c in _speech(handler).stream_speech("oi", speaker="v")]
        assert b"".join(chunks) == SAMPLES
        assert captured["body"]["response_format"] == "pcm"
        assert captured["body"]["voice"] == "v"

    async def test_daemon_400_is_a_validation_exception(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "bad voice"}})

        with pytest.raises(ValidationException) as caught:
            async for _ in _speech(handler).stream_speech("oi", voice="nope"):
                pass
        assert caught.value.message == "bad voice"

    async def test_model_not_pulled_is_an_ollama_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"message": "not found"}})

        with pytest.raises(OllamaError, match="not found"):
            async for _ in _speech(handler).stream_speech("oi"):
                pass

    async def test_language_is_refused_before_the_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        with pytest.raises(ValidationException) as caught:
            async for _ in _speech(handler).stream_speech("oi", language="pt"):
                pass
        assert caught.value.details == {"field": "language"}

    async def test_wav_stream_is_passed_through_raw(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content)["response_format"] == "wav"
            return httpx.Response(200, content=STREAMED_WAV)

        speech = _speech(handler)
        chunks = [c async for c in speech.stream_speech("oi", response_format="wav")]
        assert b"".join(chunks) == STREAMED_WAV


class TestSpeechResponse:
    """``speech_response`` primes the first chunk so a refusal is a 422."""

    def _app(self, handler: Any, **kwargs: Any) -> TestClient:
        speech = _speech(handler)
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/speak")
        async def speak(text: str) -> Response:
            """Stream the speech.

            Args:
                text (str): The text.

            Returns:
                Response: The streamed audio.
            """
            return await speech.speech_response(text, **kwargs)

        return TestClient(app, raise_server_exceptions=False)

    def test_unknown_voice_is_a_422_not_an_empty_200(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "voice not found"}})

        response = self._app(handler, voice="nope").get("/speak", params={"text": "oi"})
        assert response.status_code == 422
        assert response.json()["detail"] == "voice not found"

    def test_audio_streams_whole_with_the_pcm_media_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=SAMPLES)

        response = self._app(handler).get("/speak", params={"text": "oi"})
        assert response.status_code == 200
        assert response.content == SAMPLES
        assert response.headers["content-type"] == PCM_MEDIA_TYPE

    def test_wav_gets_the_wav_media_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=STREAMED_WAV)

        response = self._app(handler, response_format="wav").get(
            "/speak", params={"text": "oi"}
        )
        assert response.headers["content-type"] == "audio/wav"
        assert response.content == STREAMED_WAV

    def test_empty_body_is_an_empty_200(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"")

        response = self._app(handler).get("/speak", params={"text": "oi"})
        assert response.status_code == 200
        assert response.content == b""
