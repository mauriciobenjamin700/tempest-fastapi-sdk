"""``OllamaGenerator(truncate=...)`` reaches the daemon as a top-level field.

The bodies below were recorded against Ollama 0.30.11 on 2026-11-27, on CPU
(``qwen2.5:0.5b``, ``options.num_gpu=0``, ``num_ctx=2048``, a 4,000-word
prompt). With ``"truncate": false`` both ``/api/chat`` and
``/api/generate``, streamed or not, answered ``400`` with the body in
``_EXCEEDED_BODY`` (the ``error`` value is itself a JSON document) before
producing a single line. Without the field, and with ``"truncate": true``,
every combination answered ``200`` with ``prompt_eval_count`` 1026 — the
prompt cut to half the window plus 2.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    StopReasonEnum,
    text_tool,
)
from tempest_fastapi_sdk.genai import (
    InMemoryGenerationCache,
    OllamaContextExceededError,
    OllamaError,
    OllamaGenerator,
)

_EXCEEDED_BODY: str = (
    '{"error":"{\\"error\\":{\\"code\\":400,\\"message\\":\\"request (18923 '
    "tokens) exceeds the available context size (2048 tokens), try increasing "
    'it\\",\\"type\\":\\"exceed_context_size_error\\",\\"n_prompt_tokens\\":'
    '18923,\\"n_ctx\\":2048}}"}'
)


class _ContactSchema(BaseModel):
    """A small schema for the structured calls."""

    nome: str


def _recorder(bodies: list[dict[str, Any]]) -> httpx.MockTransport:
    """Return a transport that records bodies and answers both endpoints.

    Args:
        bodies (list[dict[str, Any]]): Receives every request body.

    Returns:
        httpx.MockTransport: The transport.
    """
    content = '{"nome": "Ana"}'

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if request.url.path == "/api/generate":
            if bodies[-1].get("stream"):
                lines = [
                    json.dumps({"response": content, "done": False}),
                    json.dumps({"response": "", "done": True}),
                ]
                return httpx.Response(200, text="\n".join(lines))
            return httpx.Response(200, json={"response": content})
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": content}}
        )

    return httpx.MockTransport(handler)


def _exceeded(body: str = _EXCEEDED_BODY) -> httpx.MockTransport:
    """Return a transport that refuses the prompt as the daemon does.

    Args:
        body (str): The ``400`` body to answer with.

    Returns:
        httpx.MockTransport: The transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, text=body, headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


class TestTruncateField:
    async def test_constructor_truncate_goes_to_the_top_level(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator(
            "qwen2.5:0.5b", truncate=False, transport=_recorder(bodies)
        )
        await gen.chat([{"role": "user", "content": "oi"}], temperature=0)
        assert bodies[0]["truncate"] is False
        assert "truncate" not in bodies[0]["options"]

    async def test_truncate_is_absent_when_none(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("qwen2.5:0.5b", transport=_recorder(bodies))
        await gen.chat([{"role": "user", "content": "oi"}])
        await gen.generate("oi")
        assert all("truncate" not in body for body in bodies)

    async def test_per_call_truncate_wins_over_the_constructor(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator(
            "qwen2.5:0.5b", truncate=False, transport=_recorder(bodies)
        )
        await gen.chat([{"role": "user", "content": "oi"}], truncate=True)
        await gen.chat([{"role": "user", "content": "oi"}])
        assert bodies[0]["truncate"] is True
        assert bodies[1]["truncate"] is False

    @pytest.mark.parametrize("value", [True, False])
    async def test_every_method_forwards_truncate(self, value: bool) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("qwen2.5:0.5b", transport=_recorder(bodies))
        messages: list[dict[str, Any]] = [{"role": "user", "content": "oi"}]
        await gen.generate("oi", truncate=value)
        await gen.chat(messages, truncate=value)
        await gen.chat_with_tools(messages, [], truncate=value)
        await gen.generate_structured("oi", _ContactSchema, truncate=value)
        await gen.chat_structured(messages, _ContactSchema, truncate=value)
        pieces = [piece async for piece in gen.stream("oi", truncate=value)]
        assert pieces
        assert [body["truncate"] for body in bodies] == [value] * 6

    async def test_truncate_is_part_of_the_cache_key(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator(
            "qwen2.5:0.5b",
            transport=_recorder(bodies),
            generation_cache=InMemoryGenerationCache(),
        )
        await gen.generate("oi", do_sample=False)
        await gen.generate("oi", do_sample=False, truncate=False)
        await gen.generate("oi", do_sample=False, truncate=False)
        assert ["truncate" in body for body in bodies] == [False, True]

    def test_truncate_rejects_a_non_bool(self) -> None:
        with pytest.raises(ValueError, match="truncate"):
            OllamaGenerator("qwen2.5:0.5b", truncate="no")  # type: ignore[arg-type]


class TestContextExceeded:
    async def test_chat_raises_the_sdk_error_with_the_sizes(self) -> None:
        gen = OllamaGenerator("qwen2.5:0.5b", truncate=False, transport=_exceeded())
        with pytest.raises(OllamaContextExceededError) as caught:
            await gen.chat([{"role": "user", "content": "oi"}])
        error = caught.value
        assert isinstance(error, OllamaError)
        assert error.prompt_tokens == 18923
        assert error.context_window == 2048
        assert "18923" in str(error)
        assert "2048" in str(error)
        assert "num_ctx" in str(error)

    async def test_generate_raises_the_sdk_error(self) -> None:
        gen = OllamaGenerator("qwen2.5:0.5b", truncate=False, transport=_exceeded())
        with pytest.raises(OllamaContextExceededError):
            await gen.generate("oi")

    async def test_stream_raises_the_sdk_error(self) -> None:
        gen = OllamaGenerator("qwen2.5:0.5b", truncate=False, transport=_exceeded())
        with pytest.raises(OllamaContextExceededError) as caught:
            async for _piece in gen.stream("oi"):
                pass
        assert caught.value.prompt_tokens == 18923

    async def test_structured_and_tools_raise_the_sdk_error(self) -> None:
        gen = OllamaGenerator("qwen2.5:0.5b", truncate=False, transport=_exceeded())
        messages: list[dict[str, Any]] = [{"role": "user", "content": "oi"}]
        with pytest.raises(OllamaContextExceededError):
            await gen.chat_structured(messages, _ContactSchema)
        with pytest.raises(OllamaContextExceededError):
            await gen.generate_structured("oi", _ContactSchema)
        with pytest.raises(OllamaContextExceededError):
            await gen.chat_with_tools(messages, [])

    async def test_sizes_are_none_when_the_daemon_omits_them(self) -> None:
        body = json.dumps({"error": "exceed_context_size_error"})
        gen = OllamaGenerator("qwen2.5:0.5b", truncate=False, transport=_exceeded(body))
        with pytest.raises(OllamaContextExceededError) as caught:
            await gen.chat([{"role": "user", "content": "oi"}])
        assert caught.value.prompt_tokens is None
        assert caught.value.context_window is None
        assert "num_ctx" in str(caught.value)

    async def test_other_400_stays_an_http_error(self) -> None:
        body = json.dumps({"error": "invalid options"})
        gen = OllamaGenerator("qwen2.5:0.5b", transport=_exceeded(body))
        with pytest.raises(httpx.HTTPStatusError):
            await gen.chat([{"role": "user", "content": "oi"}])


class TestAgentRun:
    async def test_agent_run_stops_with_error_naming_the_overflow(self) -> None:
        async def echo(arguments: dict[str, Any], _context: AgentContext) -> str:
            """Return the text unchanged.

            Args:
                arguments (dict[str, Any]): The tool arguments.
                _context (AgentContext): The run context.

            Returns:
                str: The ``text`` argument.
            """
            return str(arguments["text"])

        gen = OllamaGenerator("qwen2.5:0.5b", truncate=False, transport=_exceeded())
        agent = Agent(gen, tools=[text_tool("echo", "Echo the text.", echo)])
        run = await agent.run("oi")
        assert run.stop_reason == StopReasonEnum.ERROR
        assert run.steps[-1].error is not None
        assert run.steps[-1].error.startswith("OllamaContextExceededError: ")
        assert "18923" in run.output
        assert "2048" in run.output
