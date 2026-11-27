"""``OllamaGenerator(think=...)`` reaches the daemon as a top-level field.

The daemon reads ``think`` at the top of the ``/api/chat`` and
``/api/generate`` bodies; inside ``options`` it is ignored. The failure
modes asserted here were reproduced against Ollama 0.30.11: a model without
the thinking capability answers ``400`` with
``{"error": "\\"ministral-3:14b\\" does not support thinking"}`` on both
endpoints (streamed or not), and ``ornith:35b`` with ``think=false`` ignored
``format`` and answered markdown with ``200`` (3/3,
``llm-bench/results/format_think_repro.jsonl``).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import (
    InMemoryGenerationCache,
    OllamaError,
    OllamaFormatIgnoredError,
    OllamaGenerator,
    OllamaThinkingUnsupportedError,
    ThinkLevel,
)

_UNSUPPORTED: str = '"ministral-3:14b" does not support thinking'


class _ContactSchema(BaseModel):
    """The schema the format-vs-think reproduction asked for."""

    nome: str
    cidade: str


def _recorder(
    bodies: list[dict[str, Any]], content: str = '{"nome": "Ana", "cidade": "Recife"}'
) -> httpx.MockTransport:
    """Return a transport that records bodies and answers both endpoints.

    Args:
        bodies (list[dict[str, Any]]): Receives every request body.
        content (str): The reply text.

    Returns:
        httpx.MockTransport: The transport.
    """

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


def _unsupported() -> httpx.MockTransport:
    """Return a transport that refuses thinking as the daemon does.

    Returns:
        httpx.MockTransport: The transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": _UNSUPPORTED})

    return httpx.MockTransport(handler)


class TestThinkField:
    async def test_constructor_think_goes_to_the_top_level(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("gpt-oss:20b", think="low", transport=_recorder(bodies))
        await gen.chat([{"role": "user", "content": "oi"}], temperature=0)
        assert bodies[0]["think"] == "low"
        assert "think" not in bodies[0]["options"]

    async def test_think_is_absent_when_none(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("gpt-oss:20b", transport=_recorder(bodies))
        await gen.chat([{"role": "user", "content": "oi"}])
        await gen.generate("oi")
        assert all("think" not in body for body in bodies)

    async def test_per_call_think_wins_over_the_constructor(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("ornith-1.5:9b", think=True, transport=_recorder(bodies))
        await gen.chat([{"role": "user", "content": "oi"}], think=False)
        await gen.chat([{"role": "user", "content": "oi"}])
        assert bodies[0]["think"] is False
        assert bodies[1]["think"] is True

    @pytest.mark.parametrize("level", [True, False, "low", "medium", "high"])
    async def test_every_method_forwards_think(self, level: ThinkLevel) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator("gpt-oss:20b", transport=_recorder(bodies))
        messages: list[dict[str, Any]] = [{"role": "user", "content": "oi"}]
        await gen.generate("oi", think=level)
        await gen.chat(messages, think=level)
        await gen.chat_with_tools(messages, [], think=level)
        await gen.generate_structured("oi", _ContactSchema, think=level)
        await gen.chat_structured(messages, _ContactSchema, think=level)
        pieces = [piece async for piece in gen.stream("oi", think=level)]
        assert pieces
        assert [body["think"] for body in bodies] == [level] * 6

    async def test_think_is_part_of_the_cache_key(self) -> None:
        bodies: list[dict[str, Any]] = []
        gen = OllamaGenerator(
            "gpt-oss:20b",
            transport=_recorder(bodies),
            generation_cache=InMemoryGenerationCache(),
        )
        await gen.generate("oi", do_sample=False, think="low")
        await gen.generate("oi", do_sample=False, think="high")
        await gen.generate("oi", do_sample=False, think="low")
        assert [body["think"] for body in bodies] == ["low", "high"]

    def test_think_rejects_an_unknown_level(self) -> None:
        with pytest.raises(ValueError, match="think"):
            OllamaGenerator("gpt-oss:20b", think="max")  # type: ignore[arg-type]


class TestThinkingUnsupported:
    async def test_chat_raises_the_sdk_error(self) -> None:
        gen = OllamaGenerator("ministral-3:14b", think=True, transport=_unsupported())
        with pytest.raises(OllamaThinkingUnsupportedError) as caught:
            await gen.chat([{"role": "user", "content": "oi"}])
        assert isinstance(caught.value, OllamaError)
        assert caught.value.detail == _UNSUPPORTED
        assert "think" in str(caught.value)

    async def test_generate_raises_the_sdk_error(self) -> None:
        gen = OllamaGenerator("ministral-3:14b", think=True, transport=_unsupported())
        with pytest.raises(OllamaThinkingUnsupportedError):
            await gen.generate("oi")

    async def test_stream_raises_the_sdk_error(self) -> None:
        gen = OllamaGenerator("ministral-3:14b", think=True, transport=_unsupported())
        with pytest.raises(OllamaThinkingUnsupportedError):
            async for _piece in gen.stream("oi"):
                pass

    async def test_structured_and_tools_raise_the_sdk_error(self) -> None:
        gen = OllamaGenerator("ministral-3:14b", think=True, transport=_unsupported())
        messages: list[dict[str, Any]] = [{"role": "user", "content": "oi"}]
        with pytest.raises(OllamaThinkingUnsupportedError):
            await gen.chat_structured(messages, _ContactSchema)
        with pytest.raises(OllamaThinkingUnsupportedError):
            await gen.generate_structured("oi", _ContactSchema)
        with pytest.raises(OllamaThinkingUnsupportedError):
            await gen.chat_with_tools(messages, [])

    async def test_other_status_errors_stay_http_errors(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model 'x' not found"})

        gen = OllamaGenerator("x", transport=httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            await gen.chat([{"role": "user", "content": "oi"}])


class TestFormatIgnored:
    async def test_free_text_under_think_false_names_the_cause(self) -> None:
        bodies: list[dict[str, Any]] = []
        transport = _recorder(
            bodies, content="**Nome:** Ana Costa  \n**Cidade:** Recife"
        )
        gen = OllamaGenerator("ornith:35b", think=False, transport=transport)
        with pytest.raises(OllamaFormatIgnoredError) as caught:
            await gen.chat_structured(
                [{"role": "user", "content": "Ana mora em Recife"}], _ContactSchema
            )
        assert isinstance(caught.value, ValueError)
        assert isinstance(caught.value, OllamaError)
        assert "think=False" in str(caught.value)
        assert caught.value.content.startswith("**Nome:**")

    async def test_generate_structured_raises_it_too(self) -> None:
        bodies: list[dict[str, Any]] = []
        transport = _recorder(bodies, content="**Nome:** Ana Costa")
        gen = OllamaGenerator("ornith:35b", transport=transport)
        with pytest.raises(OllamaFormatIgnoredError) as caught:
            await gen.generate_structured("Ana mora em Recife", _ContactSchema)
        assert "think=False" not in str(caught.value)
