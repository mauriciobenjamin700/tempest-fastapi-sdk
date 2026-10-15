"""Typed provider errors, the SSE reader and the schema adapter.

The OpenAI-compatible half of the hosted backends lives here too: tool
calling, structured output, the embedder and the error translation that
``OpenAICompatGenerator`` gained alongside ``AnthropicGenerator``. Bodies
follow the official OpenAPI document (https://github.com/openai/openai-openapi)
and the DeepSeek reference (https://api-docs.deepseek.com/api/create-chat-completion);
everything runs on ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.agents import ToolCallingBackend
from tempest_fastapi_sdk.genai import (
    HostedAPIError,
    HostedAuthenticationError,
    HostedRateLimitError,
    HostedServerError,
    OpenAICompatEmbedder,
    OpenAICompatGenerator,
    StructuredTextBackend,
)
from tempest_fastapi_sdk.genai.hosted import hosted_error, sse_data, strict_json_schema
from tempest_fastapi_sdk.genai.rag import SupportsEmbed
from tempest_fastapi_sdk.utils.http_client import RetryPolicy

NO_RETRY: RetryPolicy = RetryPolicy(max_attempts=1)


class Recorder:
    """Records requests and answers with one fixed response.

    Attributes:
        requests (list[httpx.Request]): Every request, in order.
    """

    def __init__(self, response: httpx.Response) -> None:
        """Build the recorder.

        Args:
            response (httpx.Response): The response to return.
        """
        self.requests: list[httpx.Request] = []
        self.response = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer it.

        Args:
            request (httpx.Request): The outgoing request.

        Returns:
            httpx.Response: The canned response.
        """
        self.requests.append(request)
        return self.response

    def body(self, index: int = -1) -> dict[str, Any]:
        """Decode one request body.

        Args:
            index (int): Which request.

        Returns:
            dict[str, Any]: The JSON body.
        """
        decoded: dict[str, Any] = json.loads(self.requests[index].content)
        return decoded


def completion(message: dict[str, Any]) -> httpx.Response:
    """Build a ``/chat/completions`` response.

    Args:
        message (dict[str, Any]): The assistant message.

    Returns:
        httpx.Response: The response.
    """
    return httpx.Response(
        200,
        json={
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        },
    )


def oai(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    model: str = "gpt-test",
    **kwargs: Any,
) -> OpenAICompatGenerator:
    """Build an OpenAI-compatible generator over a mock transport.

    Args:
        handler (Callable[[httpx.Request], httpx.Response]): The handler.
        model (str): The model name.
        **kwargs (Any): Extra constructor arguments.

    Returns:
        OpenAICompatGenerator: The generator.
    """
    return OpenAICompatGenerator(
        model,
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
        retry_policy=NO_RETRY,
        **kwargs,
    )


class TestHostedError:
    def _response(self, status: int, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            status,
            request=httpx.Request("POST", "https://x.test"),
            **kwargs,
        )

    @pytest.mark.parametrize(
        ("status", "cls"),
        [
            (401, HostedAuthenticationError),
            (403, HostedAuthenticationError),
            (429, HostedRateLimitError),
            (500, HostedServerError),
            (503, HostedServerError),
            (529, HostedServerError),
            (400, HostedAPIError),
            (402, HostedAPIError),
            (422, HostedAPIError),
        ],
    )
    def test_status_picks_the_class(
        self,
        status: int,
        cls: type[HostedAPIError],
    ) -> None:
        error = hosted_error("p", self._response(status, json={}))
        assert type(error) is cls
        assert isinstance(error, httpx.HTTPStatusError)

    def test_openai_error_body_is_read(self) -> None:
        body = {
            "error": {
                "message": "Incorrect API key provided",
                "type": "invalid_request_error",
                "param": None,
                "code": "invalid_api_key",
            },
        }
        error = hosted_error("openai", self._response(401, json=body))
        assert error.detail == "Incorrect API key provided"
        assert error.error_type == "invalid_request_error"
        assert "openai answered 401" in str(error)

    def test_non_json_body_falls_back_to_text(self) -> None:
        error = hosted_error("p", self._response(502, text="Bad Gateway"))
        assert error.detail == "Bad Gateway"
        assert error.error_type is None

    def test_http_date_retry_after_is_none(self) -> None:
        error = hosted_error(
            "p",
            self._response(
                429,
                json={},
                headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"},
            ),
        )
        assert error.retry_after is None


class TestSseData:
    @pytest.mark.parametrize(
        "line",
        ["", "event: ping", ": keep-alive", "data: [DONE]", "data: {not json", "data:"],
    )
    def test_lines_without_a_payload(self, line: str) -> None:
        assert sse_data(line) is None

    def test_data_line_decodes(self) -> None:
        assert sse_data('data: {"type": "ping"}') == {"type": "ping"}


class Nested(BaseModel):
    """A nested model, to check that ``$defs`` are walked."""

    minimum: int


class Outer(BaseModel):
    """A model whose property is literally named ``minimum``."""

    inner: Nested
    tags: list[str]


class TestStrictJsonSchema:
    def test_every_object_is_closed(self) -> None:
        schema = strict_json_schema(Outer.model_json_schema())
        assert schema["additionalProperties"] is False
        assert schema["$defs"]["Nested"]["additionalProperties"] is False

    def test_property_named_like_a_keyword_survives(self) -> None:
        schema = strict_json_schema(Outer.model_json_schema())
        assert "minimum" in schema["$defs"]["Nested"]["properties"]

    def test_constraints_are_removed_and_the_input_is_untouched(self) -> None:
        original = {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "minimum": 0, "maximum": 9},
                "s": {"type": "string", "minLength": 1, "maxLength": 3},
                "a": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                "b": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
        }
        adapted = strict_json_schema(original)
        properties = adapted["properties"]
        assert properties["n"] == {"type": "integer"}
        assert properties["s"] == {"type": "string"}
        assert "minItems" not in properties["a"]
        assert properties["b"]["minItems"] == 1
        assert original["properties"]["n"]["minimum"] == 0
        assert "additionalProperties" not in original


class TestOpenAICompatErrors:
    async def test_401_is_typed_and_still_http_status_error(self) -> None:
        rec = Recorder(
            httpx.Response(
                401,
                json={"error": {"message": "bad key", "type": "x", "code": None}},
            ),
        )
        with pytest.raises(HostedAuthenticationError) as excinfo:
            await oai(rec, provider="deepseek").generate("hi")
        assert excinfo.value.provider == "deepseek"
        assert isinstance(excinfo.value, httpx.HTTPStatusError)

    async def test_stream_open_failure_is_typed(self) -> None:
        rec = Recorder(httpx.Response(429, json={}, headers={"retry-after": "3"}))
        with pytest.raises(HostedRateLimitError) as excinfo:
            async for _piece in oai(rec).stream("hi"):
                pass
        assert excinfo.value.retry_after == 3.0

    async def test_503_is_server(self) -> None:
        rec = Recorder(httpx.Response(503, json={}))
        with pytest.raises(HostedServerError):
            await oai(rec).generate("hi")


class TestOpenAICompatMaxTokens:
    async def test_default_field_is_max_tokens(self) -> None:
        rec = Recorder(completion({"role": "assistant", "content": "ok"}))
        await oai(rec).generate("hi", max_new_tokens=5)
        assert rec.body()["max_tokens"] == 5

    async def test_field_is_configurable(self) -> None:
        rec = Recorder(completion({"role": "assistant", "content": "ok"}))
        await oai(rec, max_tokens_field="max_completion_tokens").generate(
            "hi",
            max_new_tokens=5,
        )
        body = rec.body()
        assert body["max_completion_tokens"] == 5
        assert "max_tokens" not in body


class TestOpenAICompatTools:
    def test_satisfies_the_shared_protocols(self) -> None:
        gen = OpenAICompatGenerator("m", api_key="k")
        assert isinstance(gen, ToolCallingBackend)
        assert isinstance(gen, StructuredTextBackend)

    async def test_tool_calls_come_back_as_sent(self) -> None:
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
        }
        rec = Recorder(
            completion({"role": "assistant", "content": None, "tool_calls": [call]}),
        )
        spec = {
            "type": "function",
            "function": {"name": "get_weather", "parameters": {"type": "object"}},
        }
        reply = await oai(rec).chat_with_tools(
            [{"role": "user", "content": "Paris?"}],
            [spec],
        )
        assert reply == {"role": "assistant", "content": "", "tool_calls": [call]}
        assert rec.body()["tools"] == [spec]

    async def test_no_calls_is_an_empty_list(self) -> None:
        rec = Recorder(completion({"role": "assistant", "content": "done"}))
        reply = await oai(rec).chat_with_tools([{"role": "user", "content": "x"}], [])
        assert reply["tool_calls"] == []
        assert "tools" not in rec.body()

    async def test_dict_arguments_are_serialized_on_the_wire(self) -> None:
        rec = Recorder(completion({"role": "assistant", "content": "ok"}))
        history: list[dict[str, Any]] = [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "f", "arguments": {}}}
                ],
            },
            {"role": "tool", "content": "r", "tool_call_id": "c1"},
        ]
        await oai(rec).chat(history)
        sent = rec.body()["messages"][1]["tool_calls"][0]
        assert sent == {
            "id": "c1",
            "type": "function",
            "function": {"name": "f", "arguments": "{}"},
        }
        assert history[1]["tool_calls"][0]["function"]["arguments"] == {}


class Invoice(BaseModel):
    """A structured-output target."""

    number: str
    total_cents: int


class TestOpenAICompatStructured:
    async def test_json_schema_mode(self) -> None:
        rec = Recorder(
            completion(
                {"role": "assistant", "content": '{"number": "A", "total_cents": 1}'},
            ),
        )
        result = await oai(rec).chat_structured(
            [{"role": "user", "content": "read"}],
            Invoice,
        )
        assert result == Invoice(number="A", total_cents=1)
        response_format = rec.body()["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == "Invoice"
        assert response_format["json_schema"]["schema"] == Invoice.model_json_schema()

    async def test_json_object_mode_states_the_schema(self) -> None:
        rec = Recorder(
            completion(
                {"role": "assistant", "content": '{"number": "A", "total_cents": 1}'},
            ),
        )
        gen = oai(rec, structured_output="json_object")
        await gen.chat_structured([{"role": "user", "content": "read"}], Invoice)
        body = rec.body()
        assert body["response_format"] == {"type": "json_object"}
        last = body["messages"][-1]
        assert last["role"] == "system"
        assert "JSON" in last["content"]
        assert "total_cents" in last["content"]

    async def test_empty_content_raises_value_error(self) -> None:
        rec = Recorder(completion({"role": "assistant", "content": ""}))
        with pytest.raises(ValueError):
            await oai(rec).chat_structured([{"role": "user", "content": "x"}], Invoice)

    async def test_response_format_keyword_raises(self) -> None:
        rec = Recorder(completion({"role": "assistant", "content": "{}"}))
        with pytest.raises(TypeError):
            await oai(rec).chat_structured(
                [{"role": "user", "content": "x"}],
                Invoice,
                response_format={},
            )


class TestOpenAICompatEmbedder:
    def _embedder(self, rec: Recorder, **kwargs: Any) -> OpenAICompatEmbedder:
        return OpenAICompatEmbedder(
            "text-embedding-3-small",
            api_key="sk-test",
            transport=httpx.MockTransport(rec),
            retry_policy=NO_RETRY,
            **kwargs,
        )

    def test_satisfies_supports_embed(self) -> None:
        assert isinstance(OpenAICompatEmbedder("m", api_key="k"), SupportsEmbed)

    def test_empty_key_raises(self) -> None:
        with pytest.raises(ValueError):
            OpenAICompatEmbedder("m", api_key="")

    async def test_vectors_follow_the_index_not_the_arrival(self) -> None:
        rec = Recorder(
            httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                        {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
                    ],
                    "model": "text-embedding-3-small",
                    "usage": {"prompt_tokens": 2, "total_tokens": 2},
                },
            ),
        )
        vectors = await self._embedder(rec, dimensions=2).embed(["a", "b"])
        assert vectors == [[1.0, 0.0], [0.0, 1.0]]
        body = rec.body()
        assert body == {
            "model": "text-embedding-3-small",
            "input": ["a", "b"],
            "dimensions": 2,
        }
        assert str(rec.requests[0].url) == "https://api.openai.com/v1/embeddings"
        assert rec.requests[0].headers["authorization"] == "Bearer sk-test"

    async def test_batches_and_empty_input(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            batch = json.loads(request.content)["input"]
            data = [
                {"index": i, "embedding": [float(len(text))]}
                for i, text in enumerate(batch)
            ]
            return httpx.Response(200, json={"data": data})

        embedder = OpenAICompatEmbedder(
            "m",
            api_key="k",
            transport=httpx.MockTransport(handler),
        )
        assert await embedder.embed([]) == []
        vectors = await embedder.embed(["a", "bb", "ccc"], batch_size=2)
        assert vectors == [[1.0], [2.0], [3.0]]

    @pytest.mark.parametrize("size", [0, 2049])
    async def test_batch_size_bounds(self, size: int) -> None:
        with pytest.raises(ValueError):
            await OpenAICompatEmbedder("m", api_key="k").embed(["a"], batch_size=size)

    async def test_error_is_typed(self) -> None:
        rec = Recorder(httpx.Response(401, json={}))
        with pytest.raises(HostedAuthenticationError):
            await self._embedder(rec).embed("a")
