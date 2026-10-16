"""The Anthropic backend speaks the Messages API and answers in the SDK's shapes.

Every test drives an ``httpx.MockTransport`` that answers with the bodies the
Anthropic documentation shows (https://platform.claude.com/docs/en/api/messages,
https://platform.claude.com/docs/en/build-with-claude/streaming,
https://platform.claude.com/docs/en/api/errors). No key, no network: what is
pinned is the request this class builds and the reading of what comes back.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.agents import ToolCallingBackend
from tempest_fastapi_sdk.genai import (
    ANTHROPIC_VERSION,
    DEFAULT_ANTHROPIC_MAX_TOKENS,
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicGenerator,
    GenerationConfig,
    HostedAPIError,
    HostedAuthenticationError,
    HostedRateLimitError,
    HostedServerError,
    StructuredTextBackend,
    TextBackend,
)
from tempest_fastapi_sdk.genai.anthropic import (
    anthropic_usage,
    to_anthropic_messages,
    to_anthropic_tools,
)
from tempest_fastapi_sdk.utils.http_client import RetryPolicy

NO_RETRY: RetryPolicy = RetryPolicy(max_attempts=1)


def message(
    *blocks: dict[str, Any],
    stop_reason: str = "end_turn",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Messages API response body.

    Args:
        *blocks (dict[str, Any]): Content blocks.
        stop_reason (str): The stop reason.
        usage (dict[str, Any] | None): The usage object.

    Returns:
        dict[str, Any]: The decoded Message.
    """
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_ANTHROPIC_MODEL,
        "content": list(blocks),
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }


def text_block(text: str) -> dict[str, Any]:
    """Build a ``text`` content block.

    Args:
        text (str): The text.

    Returns:
        dict[str, Any]: The block.
    """
    return {"type": "text", "text": text}


class Recorder:
    """Records requests and answers each with the next scripted response.

    Attributes:
        requests (list[httpx.Request]): Every request, in order.
    """

    def __init__(self, *responses: httpx.Response) -> None:
        """Build the recorder.

        Args:
            *responses (httpx.Response): Responses to return, in order; the
                last one repeats.
        """
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer it.

        Args:
            request (httpx.Request): The outgoing request.

        Returns:
            httpx.Response: The scripted response.
        """
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]

    def body(self, index: int = -1) -> dict[str, Any]:
        """Decode one request body.

        Args:
            index (int): Which request.

        Returns:
            dict[str, Any]: The JSON body.
        """
        decoded: dict[str, Any] = json.loads(self.requests[index].content)
        return decoded


def generator(
    recorder: Callable[[httpx.Request], httpx.Response],
    **kwargs: Any,
) -> AnthropicGenerator:
    """Build a generator over a mock transport.

    Args:
        recorder (Callable[[httpx.Request], httpx.Response]): The handler.
        **kwargs (Any): Extra constructor arguments.

    Returns:
        AnthropicGenerator: The generator.
    """
    return AnthropicGenerator(
        api_key="sk-ant-test",
        transport=httpx.MockTransport(recorder),
        retry_policy=NO_RETRY,
        **kwargs,
    )


def sse(*events: dict[str, Any]) -> bytes:
    """Encode events the way the streaming guide shows them.

    Args:
        *events (dict[str, Any]): Event payloads; each ``type`` is also the
            ``event:`` name.

    Returns:
        bytes: The stream body.
    """
    lines: list[str] = []
    for event in events:
        lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


class TestConstruction:
    def test_empty_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key"):
            AnthropicGenerator(api_key="")

    def test_non_positive_max_tokens_raises(self) -> None:
        with pytest.raises(ValueError, match="max_tokens"):
            AnthropicGenerator(api_key="k", max_tokens=0)

    def test_defaults(self) -> None:
        gen = AnthropicGenerator(api_key="k", base_url="https://x.test/")
        assert gen.model == DEFAULT_ANTHROPIC_MODEL
        assert gen.url == "https://x.test/v1/messages"
        assert gen.is_loaded is True

    def test_satisfies_the_shared_protocols(self) -> None:
        gen = AnthropicGenerator(api_key="k")
        assert isinstance(gen, TextBackend)
        assert isinstance(gen, StructuredTextBackend)
        assert isinstance(gen, ToolCallingBackend)


class TestRequest:
    async def test_headers_and_endpoint(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("hi"))))
        gen = generator(rec, extra_headers={"x-api-key": "stolen"})
        await gen.generate("hello")
        request = rec.requests[0]
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "sk-ant-test"
        assert request.headers["anthropic-version"] == ANTHROPIC_VERSION

    async def test_max_tokens_is_always_sent(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("hi"))))
        await generator(rec).generate("hello")
        body = rec.body()
        assert body["max_tokens"] == DEFAULT_ANTHROPIC_MAX_TOKENS
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert "stream" not in body

    async def test_config_maps_and_drops(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("hi"))))
        config = GenerationConfig(
            max_new_tokens=64,
            top_k=5,
            stop=["END"],
            do_sample=False,
            repetition_penalty=1.1,
            seed=3,
        )
        await generator(rec).generate("hello", config=config)
        body = rec.body()
        assert body["max_tokens"] == 64
        assert body["top_k"] == 5
        assert body["stop_sequences"] == ["END"]
        for dropped in ("do_sample", "repetition_penalty", "seed", "temperature"):
            assert dropped not in body

    async def test_instance_config_layers_under_the_call(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("hi"))))
        gen = generator(rec, config=GenerationConfig(max_new_tokens=10, top_p=0.5))
        await gen.generate("hello", config=GenerationConfig(max_new_tokens=20))
        body = rec.body()
        assert body["max_tokens"] == 20
        assert body["top_p"] == 0.5

    async def test_system_turns_become_the_top_level_field(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("hi"))))
        await generator(rec).chat(
            [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "hello"},
            ],
        )
        body = rec.body()
        assert body["system"] == "Be terse."
        assert body["messages"] == [{"role": "user", "content": "hello"}]

    async def test_extra_body_cannot_replace_computed_fields(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("hi"))))
        gen = generator(
            rec,
            extra_body={"model": "other", "output_config": {"effort": "low"}},
        )
        await gen.generate("hello")
        body = rec.body()
        assert body["model"] == DEFAULT_ANTHROPIC_MODEL
        assert body["output_config"] == {"effort": "low"}

    @pytest.mark.parametrize("key", ["model", "messages", "system", "tools", "stream"])
    async def test_reserved_keyword_raises(self, key: str) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("hi"))))
        with pytest.raises(TypeError):
            await generator(rec).chat([{"role": "user", "content": "x"}], **{key: "x"})


class TestResponse:
    async def test_text_blocks_are_joined_and_others_skipped(self) -> None:
        rec = Recorder(
            httpx.Response(
                200,
                json=message(
                    {"type": "thinking", "thinking": "", "signature": "s"},
                    text_block("Hello, "),
                    text_block("world"),
                ),
            ),
        )
        assert await generator(rec).generate("hi") == "Hello, world"

    async def test_usage_counts_the_cached_prefix(self) -> None:
        usage = {
            "input_tokens": 4,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 900,
            "output_tokens": 7,
        }
        rec = Recorder(
            httpx.Response(200, json=message(text_block("ok"), usage=usage)),
        )
        text, reported = await generator(rec).generate_with_usage("hi")
        assert text == "ok"
        assert reported is not None
        assert reported.input_tokens == 1004
        assert reported.cache_hit_tokens == 900
        assert reported.output_tokens == 7
        assert reported.total_tokens == 1011

    def test_usage_none_without_payload(self) -> None:
        assert anthropic_usage(None) is None

    async def test_refusal_without_text_is_empty(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(stop_reason="refusal")))
        assert await generator(rec).generate("hi") == ""


class TestToolCalling:
    async def test_tools_go_out_in_the_messages_shape(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("done"))))
        spec = {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            },
        }
        await generator(rec).chat_with_tools(
            [{"role": "user", "content": "Paris?"}],
            [spec],
        )
        assert rec.body()["tools"] == [
            {
                "name": "get_weather",
                "description": "Weather for a city.",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            },
        ]

    async def test_tool_use_comes_back_as_tool_calls(self) -> None:
        rec = Recorder(
            httpx.Response(
                200,
                json=message(
                    text_block("Let me check."),
                    {
                        "type": "tool_use",
                        "id": "toolu_abc123",
                        "name": "get_weather",
                        "input": {"location": "Paris"},
                    },
                    stop_reason="tool_use",
                ),
            ),
        )
        reply = await generator(rec).chat_with_tools(
            [{"role": "user", "content": "Paris?"}],
            [],
        )
        assert reply == {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {
                    "id": "toolu_abc123",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": {"location": "Paris"},
                    },
                },
            ],
        }

    def test_history_translates_to_tool_use_and_one_result_turn(self) -> None:
        system, translated = to_anthropic_messages(
            [
                {"role": "system", "content": "Use tools."},
                {"role": "user", "content": "Two cities."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "toolu_1",
                            "function": {
                                "name": "w",
                                "arguments": '{"city": "Paris"}',
                            },
                        },
                        {"id": "toolu_2", "function": {"name": "w", "arguments": {}}},
                    ],
                },
                {"role": "tool", "content": "sunny", "tool_call_id": "toolu_1"},
                {"role": "tool", "content": "rain", "tool_call_id": "toolu_2"},
            ],
        )
        assert system == "Use tools."
        assert translated[1] == {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "w",
                    "input": {"city": "Paris"},
                },
                {"type": "tool_use", "id": "toolu_2", "name": "w", "input": {}},
            ],
        }
        assert translated[2] == {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "sunny"},
                {"type": "tool_result", "tool_use_id": "toolu_2", "content": "rain"},
            ],
        }

    def test_calls_without_ids_are_paired_in_order(self) -> None:
        _system, translated = to_anthropic_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "a", "arguments": {}}},
                        {"function": {"name": "b", "arguments": {}}},
                    ],
                },
                {"role": "tool", "content": "A"},
                {"role": "tool", "content": "B"},
            ],
        )
        ids = [block["id"] for block in translated[0]["content"]]
        results = [block["tool_use_id"] for block in translated[1]["content"]]
        assert ids == results
        assert len(set(ids)) == 2

    def test_anthropic_tools_pass_through(self) -> None:
        native = {"name": "x", "input_schema": {"type": "object"}}
        assert to_anthropic_tools([native]) == [native]


class Invoice(BaseModel):
    """A structured-output target with constraints the API refuses."""

    number: str = Field(min_length=3)
    total_cents: int = Field(ge=0)


class TestStructured:
    async def test_schema_goes_to_output_config(self) -> None:
        rec = Recorder(
            httpx.Response(
                200,
                json=message(text_block('{"number": "A-1", "total_cents": 500}')),
            ),
        )
        gen = generator(rec, extra_body={"output_config": {"effort": "low"}})
        invoice = await gen.chat_structured(
            [{"role": "user", "content": "Read it."}],
            Invoice,
        )
        assert invoice == Invoice(number="A-1", total_cents=500)
        output_config = rec.body()["output_config"]
        assert output_config["effort"] == "low"
        schema = output_config["format"]["schema"]
        assert output_config["format"]["type"] == "json_schema"
        assert schema["additionalProperties"] is False
        assert "minLength" not in schema["properties"]["number"]
        assert "minimum" not in schema["properties"]["total_cents"]

    async def test_dropped_constraint_is_still_enforced_here(self) -> None:
        rec = Recorder(
            httpx.Response(
                200,
                json=message(text_block('{"number": "A", "total_cents": 500}')),
            ),
        )
        with pytest.raises(ValueError):
            await generator(rec).chat_structured(
                [{"role": "user", "content": "Read it."}],
                Invoice,
            )

    async def test_output_config_keyword_raises(self) -> None:
        rec = Recorder(httpx.Response(200, json=message(text_block("{}"))))
        with pytest.raises(TypeError):
            await generator(rec).chat_structured(
                [{"role": "user", "content": "x"}],
                Invoice,
                output_config={},
            )


class TestStreaming:
    async def test_text_deltas_in_order(self) -> None:
        body = sse(
            {"type": "message_start", "message": message()},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {"type": "ping"},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": " world"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 15},
            },
            {"type": "message_stop"},
        )
        rec = Recorder(
            httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/event-stream"},
            ),
        )
        pieces = [piece async for piece in generator(rec).stream("hi")]
        assert pieces == ["Hello", " world"]
        assert rec.body()["stream"] is True

    async def test_mid_stream_error_raises_typed(self) -> None:
        body = sse(
            {"type": "message_start", "message": message()},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "partial"},
            },
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"},
            },
        )
        rec = Recorder(httpx.Response(200, content=body))
        received: list[str] = []
        with pytest.raises(HostedServerError) as excinfo:
            async for piece in generator(rec).stream("hi"):
                received.append(piece)
        assert received == ["partial"]
        assert excinfo.value.status_code == 529
        assert excinfo.value.error_type == "overloaded_error"

    async def test_stream_open_failure_is_typed(self) -> None:
        rec = Recorder(
            httpx.Response(
                401,
                json={
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "bad"},
                },
            ),
        )
        with pytest.raises(HostedAuthenticationError):
            async for _piece in generator(rec).stream("hi"):
                pass


def error_body(kind: str, text: str) -> dict[str, Any]:
    """Build the documented error body.

    Args:
        kind (str): The error type.
        text (str): The message.

    Returns:
        dict[str, Any]: The body.
    """
    return {
        "type": "error",
        "error": {"type": kind, "message": text},
        "request_id": "req_body",
    }


class TestErrors:
    async def test_401_is_authentication(self) -> None:
        rec = Recorder(
            httpx.Response(
                401,
                json=error_body("authentication_error", "invalid x-api-key"),
                headers={"request-id": "req_018E"},
            ),
        )
        with pytest.raises(HostedAuthenticationError) as excinfo:
            await generator(rec).generate("hi")
        error = excinfo.value
        assert isinstance(error, httpx.HTTPStatusError)
        assert error.provider == "anthropic"
        assert error.error_type == "authentication_error"
        assert error.detail == "invalid x-api-key"
        assert error.request_id == "req_018E"

    async def test_429_carries_retry_after(self) -> None:
        rec = Recorder(
            httpx.Response(
                429,
                json=error_body("rate_limit_error", "slow down"),
                headers={"retry-after": "12"},
            ),
        )
        with pytest.raises(HostedRateLimitError) as excinfo:
            await generator(rec).generate("hi")
        assert excinfo.value.retry_after == 12.0
        assert excinfo.value.request_id == "req_body"

    @pytest.mark.parametrize("status", [500, 529])
    async def test_5xx_is_server(self, status: int) -> None:
        rec = Recorder(httpx.Response(status, json=error_body("api_error", "x")))
        with pytest.raises(HostedServerError):
            await generator(rec).generate("hi")

    async def test_400_is_the_base_class(self) -> None:
        rec = Recorder(
            httpx.Response(400, json=error_body("invalid_request_error", "x")),
        )
        with pytest.raises(HostedAPIError) as excinfo:
            await generator(rec).generate("hi")
        assert type(excinfo.value) is HostedAPIError

    async def test_529_is_retried_by_default(self) -> None:
        rec = Recorder(
            httpx.Response(529, json=error_body("overloaded_error", "x")),
            httpx.Response(200, json=message(text_block("recovered"))),
        )
        gen = AnthropicGenerator(
            api_key="k",
            transport=httpx.MockTransport(rec),
        )
        gen._http().retry_policy.backoff_initial_seconds = 0.0
        assert await gen.generate("hi") == "recovered"
        assert len(rec.requests) == 2
