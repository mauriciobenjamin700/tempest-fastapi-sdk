"""``document_part`` goes out in each provider's own shape, or not at all.

Every test drives an ``httpx.MockTransport``. What is pinned is the request
body each generator builds from a :func:`document_part` — the Anthropic
``document`` block of
https://platform.claude.com/docs/en/build-with-claude/pdf-support and the
OpenAI Chat Completions ``file`` part of
https://developers.openai.com/api/docs/guides/pdf-files — and that the
providers without documented PDF input refuse it before any request. No live
provider is called: whether a provider answers the request well is not
measured here.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import (
    DEFAULT_DOCUMENT_FILENAME,
    DOCUMENT_PROVIDERS,
    HOSTED_PROVIDERS,
    AnthropicGenerator,
    OpenAICompatGenerator,
    document_part,
    hosted_generator,
    is_document_part,
    supports_documents,
    to_anthropic_document,
)
from tempest_fastapi_sdk.settings import HostedGenAISettings
from tempest_fastapi_sdk.utils.http_client import RetryPolicy

NO_RETRY: RetryPolicy = RetryPolicy(max_attempts=1)

PDF: bytes = b"%PDF-1.7\n%fake agenda\n%%EOF"

ENCODED: str = base64.standard_b64encode(PDF).decode("ascii")

CLAUDE_REPLY: dict[str, Any] = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 3, "output_tokens": 1},
}

CHAT_REPLY: dict[str, Any] = {
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"},
        },
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
}


class Events(BaseModel):
    """The schema the structured call asks for."""

    titles: list[str]


class Recorder:
    """Records each request and answers with one scripted JSON body.

    Attributes:
        requests (list[httpx.Request]): Every request, in order.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        """Build the recorder.

        Args:
            payload (dict[str, Any]): The JSON body of every response.
        """
        self.requests: list[httpx.Request] = []
        self.payload: dict[str, Any] = payload

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer it.

        Args:
            request (httpx.Request): The outgoing request.

        Returns:
            httpx.Response: The scripted response.
        """
        self.requests.append(request)
        return httpx.Response(200, json=self.payload)

    def body(self) -> dict[str, Any]:
        """Decode the last request body.

        Returns:
            dict[str, Any]: The JSON body.
        """
        decoded: dict[str, Any] = json.loads(self.requests[-1].content)
        return decoded


def pdf_messages(filename: str | None = "agenda.pdf") -> list[dict[str, Any]]:
    """Build one user turn carrying the PDF and a question.

    Args:
        filename (str | None): The filename passed to ``document_part``.

    Returns:
        list[dict[str, Any]]: The conversation.
    """
    return [
        {
            "role": "user",
            "content": [
                document_part(PDF, filename=filename),
                {"type": "text", "text": "List the events."},
            ],
        },
    ]


class TestDocumentPart:
    def test_builds_the_openai_file_part(self) -> None:
        assert document_part(PDF, filename="agenda.pdf") == {
            "type": "file",
            "file": {
                "filename": "agenda.pdf",
                "file_data": f"data:application/pdf;base64,{ENCODED}",
            },
        }

    def test_missing_filename_gets_the_default(self) -> None:
        part = document_part(PDF)
        assert part["file"]["filename"] == DEFAULT_DOCUMENT_FILENAME

    def test_non_pdf_media_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="application/pdf"):
            document_part(b"hello", "text/plain")

    def test_empty_data_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            document_part(b"")

    def test_is_document_part(self) -> None:
        assert is_document_part(document_part(PDF))
        assert not is_document_part({"type": "text", "text": "x"})
        assert not is_document_part("file")


class TestSupportsDocuments:
    def test_documented_providers(self) -> None:
        assert frozenset({"anthropic", "openai"}) == DOCUMENT_PROVIDERS
        assert {p for p in HOSTED_PROVIDERS if supports_documents(p)} == {
            "anthropic",
            "openai",
        }

    def test_unknown_and_none_are_false(self) -> None:
        assert supports_documents(None) is False
        assert supports_documents("mistral") is False


class TestAnthropic:
    async def test_goes_out_as_a_base64_document_block(self) -> None:
        rec = Recorder(CLAUDE_REPLY)
        gen = hosted_generator(
            "claude-haiku-5-5",
            api_key="sk-ant-test",
            transport=httpx.MockTransport(rec),
            retry_policy=NO_RETRY,
        )
        assert isinstance(gen, AnthropicGenerator)
        await gen.chat(pdf_messages())
        content = rec.body()["messages"][0]["content"]
        assert content == [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": ENCODED,
                },
                "title": "agenda.pdf",
            },
            {"type": "text", "text": "List the events."},
        ]

    def test_file_id_part_is_refused(self) -> None:
        with pytest.raises(ValueError, match="file_id"):
            to_anthropic_document({"type": "file", "file": {"file_id": "file-1"}})

    def test_non_data_url_is_refused(self) -> None:
        with pytest.raises(ValueError, match="data:"):
            to_anthropic_document(
                {"type": "file", "file": {"file_data": ENCODED}},
            )


class TestOpenAI:
    async def test_goes_out_as_a_file_part(self) -> None:
        rec = Recorder(CHAT_REPLY)
        gen = hosted_generator(
            "gpt-test",
            api_key="sk-test",
            transport=httpx.MockTransport(rec),
            retry_policy=NO_RETRY,
        )
        assert isinstance(gen, OpenAICompatGenerator)
        await gen.chat(pdf_messages())
        content = rec.body()["messages"][0]["content"]
        assert content[0] == {
            "type": "file",
            "file": {
                "filename": "agenda.pdf",
                "file_data": f"data:application/pdf;base64,{ENCODED}",
            },
        }

    async def test_generic_compatible_server_forwards_it(self) -> None:
        rec = Recorder(CHAT_REPLY)
        gen = OpenAICompatGenerator(
            "local-model",
            api_key="sk-test",
            base_url="http://vllm.internal/v1",
            transport=httpx.MockTransport(rec),
            retry_policy=NO_RETRY,
        )
        await gen.chat(pdf_messages())
        assert rec.body()["messages"][0]["content"][0]["type"] == "file"


class TestProvidersWithoutDocuments:
    @pytest.mark.parametrize(
        ("model", "provider"),
        [("gemini-test", "google"), ("deepseek-flash", "deepseek")],
    )
    async def test_chat_raises_before_any_request(
        self,
        model: str,
        provider: str,
    ) -> None:
        rec = Recorder(CHAT_REPLY)
        gen = hosted_generator(
            model,
            api_key="sk-test",
            transport=httpx.MockTransport(rec),
            retry_policy=NO_RETRY,
        )
        with pytest.raises(ValueError, match=f"provider '{provider}'"):
            await gen.chat(pdf_messages())
        assert rec.requests == []

    async def test_structured_and_tool_calls_raise_too(self) -> None:
        rec = Recorder(CHAT_REPLY)
        gen = hosted_generator(
            "deepseek-flash",
            api_key="sk-test",
            transport=httpx.MockTransport(rec),
            retry_policy=NO_RETRY,
        )
        assert isinstance(gen, OpenAICompatGenerator)
        with pytest.raises(ValueError, match="supports_documents"):
            await gen.complete_structured(pdf_messages(), Events)
        with pytest.raises(ValueError, match="supports_documents"):
            await gen.chat_with_tools(pdf_messages(), [])
        assert rec.requests == []

    async def test_text_only_calls_still_go_out(self) -> None:
        rec = Recorder(CHAT_REPLY)
        gen = hosted_generator(
            "deepseek-flash",
            api_key="sk-test",
            transport=httpx.MockTransport(rec),
            retry_policy=NO_RETRY,
        )
        await gen.chat([{"role": "user", "content": "hi"}])
        assert len(rec.requests) == 1

    @pytest.mark.parametrize("provider", ["google", "deepseek"])
    async def test_settings_kwargs_refuse_too(self, provider: str) -> None:
        settings = HostedGenAISettings(
            GOOGLE_API_KEY="AIza",
            GOOGLE_MODEL="gemini-test",
            DEEPSEEK_API_KEY="sk-test",
        )
        kwargs = (
            settings.gemini_kwargs()
            if provider == "google"
            else settings.deepseek_kwargs()
        )
        assert kwargs["accepts_documents"] is False
        rec = Recorder(CHAT_REPLY)
        gen = OpenAICompatGenerator(
            **kwargs,
            transport=httpx.MockTransport(rec),
            retry_policy=NO_RETRY,
        )
        with pytest.raises(ValueError, match="no PDF input"):
            await gen.chat(pdf_messages())
        assert rec.requests == []
