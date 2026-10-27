"""``complete_structured`` reports the outcome, on both hosted generators.

Every test drives an ``httpx.MockTransport`` answering with the response
shapes the providers document (the Messages API at
https://platform.claude.com/docs/en/api/messages, the OpenAI
``CreateChatCompletionResponse`` of https://github.com/openai/openai-openapi).
What is pinned is the request each generator builds and how the response is
read — not the behavior of a live provider.
"""

from __future__ import annotations

import base64
import inspect
import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    OpenAICompatGenerator,
    StructuredCompletion,
    StructuredCompletionBackend,
    TokenUsage,
    build_structured_completion,
    image_part,
)
from tempest_fastapi_sdk.utils.http_client import RetryPolicy

NO_RETRY: RetryPolicy = RetryPolicy(max_attempts=1)

PNG: bytes = b"\x89PNG\r\n\x1a\nfake"


class Verdict(BaseModel):
    """The schema every call here asks for."""

    label: str
    score: int


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
        self.payload = payload

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


def claude_message(
    text: str,
    *,
    stop_reason: str = "end_turn",
) -> dict[str, Any]:
    """Build a Messages API response.

    Args:
        text (str): The reply text; ``""`` sends no text block.
        stop_reason (str): The stop reason.

    Returns:
        dict[str, Any]: The decoded Message.
    """
    content = [{"type": "text", "text": text}] if text else []
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": 4,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 900,
            "output_tokens": 7,
        },
    }


def chat_completion(
    content: str | None,
    *,
    finish_reason: str = "stop",
    refusal: str | None = None,
) -> dict[str, Any]:
    """Build an OpenAI ``/chat/completions`` response.

    Args:
        content (str | None): The message content.
        finish_reason (str): The finish reason.
        refusal (str | None): The ``message.refusal`` field.

    Returns:
        dict[str, Any]: The decoded response.
    """
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": refusal,
                },
            },
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 9,
            "total_tokens": 59,
            "prompt_tokens_details": {"cached_tokens": 32},
        },
    }


def claude(recorder: Recorder, **kwargs: Any) -> AnthropicGenerator:
    """Build an Anthropic generator over the recorder.

    Args:
        recorder (Recorder): The transport handler.
        **kwargs (Any): Extra constructor arguments.

    Returns:
        AnthropicGenerator: The generator.
    """
    return AnthropicGenerator(
        "claude-haiku-5-5",
        api_key="sk-ant-test",
        transport=httpx.MockTransport(recorder),
        retry_policy=NO_RETRY,
        **kwargs,
    )


def compat(recorder: Recorder, **kwargs: Any) -> OpenAICompatGenerator:
    """Build an OpenAI-compatible generator over the recorder.

    Args:
        recorder (Recorder): The transport handler.
        **kwargs (Any): Extra constructor arguments.

    Returns:
        OpenAICompatGenerator: The generator.
    """
    return OpenAICompatGenerator(
        "gpt-test",
        api_key="sk-test",
        transport=httpx.MockTransport(recorder),
        retry_policy=NO_RETRY,
        **kwargs,
    )


MESSAGES: list[dict[str, Any]] = [
    {"role": "system", "content": "Classify the text."},
    {"role": "user", "content": "I loved it."},
]

GOOD: str = '{"label": "positive", "score": 9}'


class TestImagePart:
    def test_builds_a_base64_data_url(self) -> None:
        part = image_part(PNG, "image/png")
        encoded = base64.standard_b64encode(PNG).decode()
        assert part == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encoded}"},
        }


class TestBuildStructuredCompletion:
    def test_natural_stop_parses(self) -> None:
        result = build_structured_completion(
            GOOD,
            Verdict,
            usage=None,
            stop_reason="end",
            provider_stop_reason="end_turn",
        )
        assert result.output == Verdict(label="positive", score=9)
        assert result.ok is True
        assert result.error is None

    def test_non_natural_stop_is_never_parsed(self) -> None:
        result = build_structured_completion(
            GOOD,
            Verdict,
            usage=None,
            stop_reason="max_tokens",
            provider_stop_reason="max_tokens",
        )
        assert result.output is None
        assert result.ok is False
        assert result.error is None

    def test_schema_failure_is_kept_as_the_error(self) -> None:
        result = build_structured_completion(
            '{"label": "positive"}',
            Verdict,
            usage=None,
            stop_reason="end",
            provider_stop_reason="stop",
        )
        assert result.output is None
        assert isinstance(result.error, ValidationError)

    def test_reply_without_json_is_a_value_error(self) -> None:
        result = build_structured_completion(
            "no json here",
            Verdict,
            usage=None,
            stop_reason="end",
            provider_stop_reason="stop",
        )
        assert result.output is None
        assert isinstance(result.error, ValueError)


class TestProtocol:
    def test_both_generators_share_the_protocol_signature(self) -> None:
        expected = list(
            inspect.signature(
                StructuredCompletionBackend.complete_structured
            ).parameters,
        )
        for backend in (
            claude(Recorder(claude_message(GOOD))),
            compat(Recorder(chat_completion(GOOD))),
        ):
            method = backend.complete_structured
            assert ["self", *inspect.signature(method).parameters] == expected


class TestAnthropic:
    async def test_body_carries_format_effort_cache_and_budget(self) -> None:
        rec = Recorder(claude_message(GOOD))
        result = await claude(rec).complete_structured(
            MESSAGES,
            Verdict,
            effort="low",
            cache_system=True,
            max_tokens=512,
        )
        body = rec.body()
        assert body["output_config"]["effort"] == "low"
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["max_tokens"] == 512
        assert body["system"] == [
            {
                "type": "text",
                "text": "Classify the text.",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        assert result.output == Verdict(label="positive", score=9)
        assert result.stop_reason == "end"
        assert result.provider_stop_reason == "end_turn"

    async def test_no_effort_and_no_cache_leave_the_body_plain(self) -> None:
        rec = Recorder(claude_message(GOOD))
        await claude(rec).complete_structured(MESSAGES, Verdict)
        body = rec.body()
        assert "effort" not in body["output_config"]
        assert body["system"] == "Classify the text."

    async def test_extra_body_output_config_is_kept(self) -> None:
        rec = Recorder(claude_message(GOOD))
        await claude(
            rec,
            extra_body={"output_config": {"effort": "high"}},
        ).complete_structured(MESSAGES, Verdict)
        assert rec.body()["output_config"]["effort"] == "high"

    async def test_usage_splits_the_cache_write(self) -> None:
        rec = Recorder(claude_message(GOOD))
        result = await claude(rec).complete_structured(MESSAGES, Verdict)
        assert result.usage == TokenUsage(
            input_tokens=1004,
            output_tokens=7,
            total_tokens=1011,
            cache_hit_tokens=900,
            cache_write_tokens=100,
        )

    async def test_refusal_returns_the_usage_without_output(self) -> None:
        rec = Recorder(claude_message("", stop_reason="refusal"))
        result = await claude(rec).complete_structured(MESSAGES, Verdict)
        assert result.stop_reason == "refusal"
        assert result.output is None
        assert result.usage is not None
        assert result.usage.output_tokens == 7

    async def test_truncated_reply_is_not_parsed(self) -> None:
        rec = Recorder(claude_message('{"label": "pos', stop_reason="max_tokens"))
        result = await claude(rec).complete_structured(MESSAGES, Verdict)
        assert result.stop_reason == "max_tokens"
        assert result.output is None
        assert result.text == '{"label": "pos'

    async def test_unknown_stop_reason_is_other(self) -> None:
        rec = Recorder(claude_message(GOOD, stop_reason="pause_turn"))
        result = await claude(rec).complete_structured(MESSAGES, Verdict)
        assert result.stop_reason == "other"
        assert result.provider_stop_reason == "pause_turn"
        assert result.output is None

    async def test_invalid_json_keeps_usage_and_error(self) -> None:
        rec = Recorder(claude_message('{"label": 3}'))
        result = await claude(rec).complete_structured(MESSAGES, Verdict)
        assert isinstance(result.error, ValidationError)
        assert result.usage is not None
        assert result.usage.input_tokens == 1004

    async def test_image_part_goes_out_as_an_image_block(self) -> None:
        rec = Recorder(claude_message(GOOD))
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    image_part(PNG, "image/png"),
                    {"type": "text", "text": "Read it."},
                ],
            },
        ]
        await claude(rec).complete_structured(messages, Verdict)
        content = rec.body()["messages"][0]["content"]
        assert content[0] == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(PNG).decode(),
            },
        }
        assert content[1] == {"type": "text", "text": "Read it."}

    async def test_output_config_keyword_is_refused(self) -> None:
        rec = Recorder(claude_message(GOOD))
        with pytest.raises(TypeError, match="output_config"):
            await claude(rec).complete_structured(
                MESSAGES,
                Verdict,
                output_config={"effort": "low"},
            )
        assert rec.requests == []


class TestOpenAICompat:
    async def test_body_carries_format_effort_and_budget_field(self) -> None:
        rec = Recorder(chat_completion(GOOD))
        result = await compat(
            rec,
            max_tokens_field="max_completion_tokens",
        ).complete_structured(MESSAGES, Verdict, effort="medium", max_tokens=256)
        body = rec.body()
        assert body["reasoning_effort"] == "medium"
        assert body["max_completion_tokens"] == 256
        assert body["response_format"]["type"] == "json_schema"
        assert result.output == Verdict(label="positive", score=9)
        assert result.stop_reason == "end"

    async def test_effort_values_translate(self) -> None:
        rec = Recorder(chat_completion(GOOD))
        await compat(rec, effort_values={"max": "high"}).complete_structured(
            MESSAGES,
            Verdict,
            effort="max",
        )
        assert rec.body()["reasoning_effort"] == "high"

    async def test_no_effort_field_drops_effort(self) -> None:
        rec = Recorder(chat_completion(GOOD))
        await compat(rec, effort_field=None).complete_structured(
            MESSAGES,
            Verdict,
            effort="high",
        )
        assert "reasoning_effort" not in rec.body()

    async def test_cache_system_sends_nothing(self) -> None:
        rec = Recorder(chat_completion(GOOD))
        await compat(rec).complete_structured(MESSAGES, Verdict, cache_system=True)
        body = rec.body()
        assert body["messages"] == MESSAGES
        assert "cache_control" not in json.dumps(body)

    async def test_json_object_mode_states_the_schema(self) -> None:
        rec = Recorder(chat_completion(GOOD))
        await compat(rec, structured_output="json_object").complete_structured(
            MESSAGES,
            Verdict,
        )
        body = rec.body()
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][-1]["role"] == "system"

    async def test_image_part_is_forwarded_unchanged(self) -> None:
        rec = Recorder(chat_completion(GOOD))
        part = image_part(PNG, "image/jpeg")
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [part, {"type": "text", "text": "Read."}]},
        ]
        await compat(rec).complete_structured(messages, Verdict)
        assert rec.body()["messages"][0]["content"][0] == part

    async def test_usage_reads_the_cached_prefix(self) -> None:
        rec = Recorder(chat_completion(GOOD))
        result = await compat(rec).complete_structured(MESSAGES, Verdict)
        assert result.usage == TokenUsage(
            input_tokens=50,
            output_tokens=9,
            total_tokens=59,
            cache_hit_tokens=32,
        )

    @pytest.mark.parametrize(
        ("finish_reason", "expected"),
        [
            ("length", "max_tokens"),
            ("content_filter", "refusal"),
            ("tool_calls", "tool_use"),
            ("function_call", "other"),
        ],
    )
    async def test_finish_reason_is_normalized(
        self,
        finish_reason: str,
        expected: str,
    ) -> None:
        rec = Recorder(chat_completion(GOOD, finish_reason=finish_reason))
        result = await compat(rec).complete_structured(MESSAGES, Verdict)
        assert result.stop_reason == expected
        assert result.provider_stop_reason == finish_reason
        assert result.output is None

    async def test_message_refusal_wins_over_stop(self) -> None:
        rec = Recorder(chat_completion(None, refusal="I can't help with that."))
        result: StructuredCompletion[Verdict] = await compat(rec).complete_structured(
            MESSAGES, Verdict
        )
        assert result.stop_reason == "refusal"
        assert result.provider_stop_reason == "stop"
        assert result.output is None
        assert result.usage is not None

    async def test_invalid_json_keeps_usage_and_error(self) -> None:
        rec = Recorder(chat_completion("not json"))
        result = await compat(rec).complete_structured(MESSAGES, Verdict)
        assert isinstance(result.error, ValueError)
        assert result.usage is not None
        assert result.usage.total_tokens == 59

    async def test_response_format_keyword_is_refused(self) -> None:
        rec = Recorder(chat_completion(GOOD))
        with pytest.raises(TypeError, match="complete_structured"):
            await compat(rec).complete_structured(
                MESSAGES,
                Verdict,
                response_format={"type": "text"},
            )
        assert rec.requests == []
