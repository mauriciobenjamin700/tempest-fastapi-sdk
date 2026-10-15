"""One consumer, four backends: Ollama, Anthropic, OpenAI and DeepSeek.

The promise of the hosted backends is that a service swaps the line that
builds the backend and nothing else. These tests hold that promise: the
consumer functions below are typed against the SDK's protocols and never name
a provider, and each runs unchanged against every backend.

Each backend talks to a fake provider that speaks **its own** wire format —
Ollama's ``/api/chat`` and NDJSON, Anthropic's ``/v1/messages`` and typed SSE,
the OpenAI ``/chat/completions`` and ``data:`` SSE. The tool scenario is the
strict one: the fake answers the final turn only when it finds the tool result
in its own format, linked to the call it issued, so a translation that drops
or mislinks the result fails here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    AgentTool,
    StopReason,
    ToolCallingBackend,
)
from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    OllamaGenerator,
    OpenAICompatGenerator,
    StructuredTextBackend,
    TextBackend,
)
from tempest_fastapi_sdk.utils.http_client import RetryPolicy

NO_RETRY: RetryPolicy = RetryPolicy(max_attempts=1)
ANSWER: str = '{"city": "Paris", "celsius": 21}'
FINAL: str = "The sum is 5."


class Weather(BaseModel):
    """The structured-output target."""

    city: str
    celsius: int


def _sse_body(events: list[dict[str, Any]], *, named: bool) -> bytes:
    """Encode server-sent events.

    Args:
        events (list[dict[str, Any]]): Payloads.
        named (bool): Whether to add Anthropic's ``event:`` line.

    Returns:
        bytes: The body.
    """
    lines: list[str] = []
    for event in events:
        if named:
            lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    if not named:
        lines.extend(["data: [DONE]", ""])
    return "\n".join(lines).encode()


def ollama_provider(request: httpx.Request) -> httpx.Response:
    """Answer like an Ollama daemon.

    Args:
        request (httpx.Request): The request.

    Returns:
        httpx.Response: The reply.
    """
    body = json.loads(request.content)
    if request.url.path == "/api/generate":
        lines = [
            json.dumps({"response": "Hello", "done": False}),
            json.dumps({"response": " world", "done": True}),
        ]
        return httpx.Response(200, content="\n".join(lines).encode())
    if "format" in body:
        return httpx.Response(200, json={"message": {"content": ANSWER}})
    if body.get("tools"):
        results = [m for m in body["messages"] if m["role"] == "tool"]
        if results and results[-1]["content"] == "5":
            return httpx.Response(200, json={"message": {"content": FINAL}})
        call = {"function": {"name": "add", "arguments": {"a": 2, "b": 3}}}
        return httpx.Response(
            200,
            json={"message": {"content": "", "tool_calls": [call]}},
        )
    return httpx.Response(200, json={"message": {"content": "42"}})


def anthropic_provider(request: httpx.Request) -> httpx.Response:
    """Answer like the Messages API.

    Args:
        request (httpx.Request): The request.

    Returns:
        httpx.Response: The reply.
    """
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "k"
    body = json.loads(request.content)

    def reply(*blocks: dict[str, Any], stop: str = "end_turn") -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "message",
                "role": "assistant",
                "content": list(blocks),
                "stop_reason": stop,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    if body.get("stream"):
        deltas = [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            }
            for text in ("Hello", " world")
        ]
        events = [
            {"type": "message_start", "message": {"content": []}},
            {"type": "ping"},
            *deltas,
            {"type": "message_stop"},
        ]
        return httpx.Response(200, content=_sse_body(events, named=True))
    if "output_config" in body:
        return reply({"type": "text", "text": ANSWER})
    if body.get("tools"):
        last = body["messages"][-1]
        content = last["content"]
        if last["role"] == "user" and isinstance(content, list):
            result = content[0]
            previous = body["messages"][-2]["content"]
            issued = [b["id"] for b in previous if b["type"] == "tool_use"]
            if (
                result["type"] == "tool_result"
                and result["tool_use_id"] in issued
                and result["content"] == "5"
            ):
                return reply({"type": "text", "text": FINAL})
        return reply(
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "add",
                "input": {"a": 2, "b": 3},
            },
            stop="tool_use",
        )
    return reply({"type": "text", "text": "42"})


def openai_provider(request: httpx.Request) -> httpx.Response:
    """Answer like an OpenAI-compatible ``/chat/completions``.

    Args:
        request (httpx.Request): The request.

    Returns:
        httpx.Response: The reply.
    """
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["authorization"] == "Bearer k"
    body = json.loads(request.content)

    def reply(message: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": message}]})

    if body.get("stream"):
        events = [
            {"choices": [{"delta": {"content": text}}]} for text in ("Hello", " world")
        ]
        return httpx.Response(200, content=_sse_body(events, named=False))
    if "response_format" in body:
        return reply({"role": "assistant", "content": ANSWER})
    if body.get("tools"):
        last = body["messages"][-1]
        if (
            last["role"] == "tool"
            and last.get("tool_call_id") == "call_01"
            and last["content"] == "5"
        ):
            return reply({"role": "assistant", "content": FINAL})
        call = {
            "id": "call_01",
            "type": "function",
            "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
        }
        return reply({"role": "assistant", "content": None, "tool_calls": [call]})
    return reply({"role": "assistant", "content": "42"})


Backend = OllamaGenerator | AnthropicGenerator | OpenAICompatGenerator


def build_ollama() -> Backend:
    """Build the Ollama backend over its fake daemon.

    Returns:
        Backend: The backend.
    """
    return OllamaGenerator(
        "llama3.2",
        transport=httpx.MockTransport(ollama_provider),
        retry_policy=NO_RETRY,
    )


def build_anthropic() -> Backend:
    """Build the Anthropic backend over its fake API.

    Returns:
        Backend: The backend.
    """
    return AnthropicGenerator(
        api_key="k",
        transport=httpx.MockTransport(anthropic_provider),
        retry_policy=NO_RETRY,
    )


def build_openai() -> Backend:
    """Build the OpenAI backend over its fake API.

    Returns:
        Backend: The backend.
    """
    return OpenAICompatGenerator(
        "gpt-test",
        api_key="k",
        transport=httpx.MockTransport(openai_provider),
        retry_policy=NO_RETRY,
        provider="openai",
        max_tokens_field="max_completion_tokens",
    )


def build_deepseek() -> Backend:
    """Build the DeepSeek backend over its fake API.

    Returns:
        Backend: The backend.
    """
    return OpenAICompatGenerator(
        "deepseek-flash",
        api_key="k",
        base_url="https://api.deepseek.com",
        transport=httpx.MockTransport(openai_provider),
        retry_policy=NO_RETRY,
        provider="deepseek",
        structured_output="json_object",
    )


BACKENDS: dict[str, Callable[[], Backend]] = {
    "ollama": build_ollama,
    "anthropic": build_anthropic,
    "openai": build_openai,
    "deepseek": build_deepseek,
}


async def ask(backend: TextBackend, question: str) -> str:
    """Consumer code: one chat turn.

    Args:
        backend (TextBackend): Any text backend.
        question (str): The question.

    Returns:
        str: The answer.
    """
    return await backend.chat([{"role": "user", "content": question}])


async def stream_all(backend: TextBackend, prompt: str) -> list[str]:
    """Consumer code: collect a stream.

    Args:
        backend (TextBackend): Any text backend.
        prompt (str): The prompt.

    Returns:
        list[str]: The pieces.
    """
    return [piece async for piece in backend.stream(prompt)]


async def extract(backend: StructuredTextBackend, text: str) -> Weather:
    """Consumer code: structured extraction.

    Args:
        backend (StructuredTextBackend): Any structured backend.
        text (str): The document.

    Returns:
        Weather: The extracted record.
    """
    return await backend.chat_structured(
        [
            {"role": "system", "content": "Extract the weather."},
            {"role": "user", "content": text},
        ],
        Weather,
    )


async def add(arguments: dict[str, Any], _context: AgentContext) -> str:
    """The agent's one tool.

    Args:
        arguments (dict[str, Any]): ``a`` and ``b``.
        _context (AgentContext): The run context.

    Returns:
        str: The sum.
    """
    return str(int(arguments["a"]) + int(arguments["b"]))


ADD_TOOL: AgentTool = AgentTool(
    name="add",
    description="Add two integers.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
    handler=add,
)


async def run_agent(backend: ToolCallingBackend) -> tuple[str, StopReason, int]:
    """Consumer code: an agent with one tool.

    Args:
        backend (ToolCallingBackend): Any tool-calling backend.

    Returns:
        tuple[str, StopReason, int]: Output, stop reason, tool calls.
    """
    agent = Agent(backend, tools=[ADD_TOOL])
    run = await agent.run("What is 2 + 3?")
    return run.output, run.stop_reason, len(run.tool_calls)


@pytest.fixture(params=sorted(BACKENDS))
def backend(request: pytest.FixtureRequest) -> Backend:
    """Yield each backend in turn.

    Args:
        request (pytest.FixtureRequest): The fixture request.

    Returns:
        Backend: The backend under test.
    """
    return BACKENDS[str(request.param)]()


class TestSameConsumerEveryBackend:
    def test_every_backend_satisfies_every_protocol(self, backend: Backend) -> None:
        assert isinstance(backend, TextBackend)
        assert isinstance(backend, StructuredTextBackend)
        assert isinstance(backend, ToolCallingBackend)

    async def test_chat(self, backend: Backend) -> None:
        assert await ask(backend, "Meaning of life?") == "42"

    async def test_stream(self, backend: Backend) -> None:
        assert await stream_all(backend, "Greet.") == ["Hello", " world"]

    async def test_structured(self, backend: Backend) -> None:
        assert await extract(backend, "21C in Paris.") == Weather(
            city="Paris",
            celsius=21,
        )

    async def test_agent_round_trips_the_tool_result(self, backend: Backend) -> None:
        output, reason, calls = await run_agent(backend)
        assert reason == StopReason.COMPLETED
        assert output == FINAL
        assert calls == 1
