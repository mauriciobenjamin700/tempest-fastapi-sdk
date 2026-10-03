"""Generator-level defaults reach callers that pass no options.

The :class:`~tempest_fastapi_sdk.agents.Agent` loop calls
``chat_with_tools(messages, tools)`` and nothing else, so before
``config=`` / ``num_ctx=`` / ``options=`` existed on the generators an agent
always ran on the backend's defaults. On Ollama that includes the context
window, which truncates silently: measured on Ollama 0.30.11 with
``ministral-3:14b``, an agent whose tool returned ~11.5k tokens had its
prompt cut to 2,051 tokens and answered the wrong question (3/3 runs, run
reported ``completed``); with ``num_ctx=32768`` the daemon read all 11,551
tokens and the agent answered correctly (3/3).

These tests pin the plumbing without a daemon: the request body an agent's
call produces, the precedence of the layers, and the cache key.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk.genai import GenerationConfig, OllamaGenerator, TextGenerator
from tempest_fastapi_sdk.genai.schemas import HardwareInfo, _layer_config
from tempest_fastapi_sdk.utils.http_client import HTTPClient

MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "noop",
            "description": "Do nothing.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _cpu() -> HardwareInfo:
    """Return a CPU-only snapshot so no hardware probe runs.

    Returns:
        HardwareInfo: The snapshot.
    """
    return HardwareInfo(
        cpu_cores=2,
        ram_total_bytes=10**9,
        ram_available_bytes=10**9,
    )


class _Capture:
    """A mock daemon that records every request body."""

    def __init__(self) -> None:
        """Start with no request seen."""
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the body and answer like ``/api/chat``.

        Args:
            request (httpx.Request): The request the generator sent.

        Returns:
            httpx.Response: A minimal successful chat reply.
        """
        self.bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}, "done": True},
        )


async def _agent_call(**generator_kwargs: Any) -> dict[str, Any]:
    """Make the call an agent makes and return the body the daemon got.

    Args:
        **generator_kwargs (Any): Forwarded to :class:`OllamaGenerator`.

    Returns:
        dict[str, Any]: The request body.
    """
    capture = _Capture()
    client = HTTPClient(transport=httpx.MockTransport(capture))
    gen = OllamaGenerator("some-model", http_client=client, **generator_kwargs)
    await gen.chat_with_tools(MESSAGES, TOOLS)
    await client.aclose()
    return capture.bodies[0]


class TestLayerConfig:
    """Field-by-field layering of a call's config over a default."""

    def test_neither_side_is_none(self) -> None:
        assert _layer_config(None, None) is None

    def test_one_side_passes_through(self) -> None:
        config = GenerationConfig(max_new_tokens=8)
        assert _layer_config(config, None) is config
        assert _layer_config(None, config) is config

    def test_a_set_field_wins_and_an_unset_one_falls_through(self) -> None:
        base = GenerationConfig(max_new_tokens=8, do_sample=False)
        call = GenerationConfig(max_new_tokens=64)

        layered = _layer_config(base, call)

        assert layered is not None
        assert layered.max_new_tokens == 64
        assert layered.do_sample is False

    def test_an_explicit_none_clears_the_default(self) -> None:
        base = GenerationConfig(seed=7)
        call = GenerationConfig(seed=None)

        layered = _layer_config(base, call)

        assert layered is not None
        assert layered.seed is None

    def test_only_set_fields_travel(self) -> None:
        layered = _layer_config(
            GenerationConfig(max_new_tokens=8),
            GenerationConfig(temperature=0.2),
        )

        assert layered is not None
        assert layered.to_generate_kwargs() == {
            "max_new_tokens": 8,
            "temperature": 0.2,
        }


class TestOllamaDefaults:
    """What an agent's bare ``chat_with_tools`` call sends to the daemon."""

    async def test_without_defaults_no_options_are_sent(self) -> None:
        body = await _agent_call()

        assert "options" not in body

    async def test_num_ctx_reaches_the_daemon(self) -> None:
        body = await _agent_call(num_ctx=32768)

        assert body["options"] == {"num_ctx": 32768}

    async def test_default_config_reaches_the_daemon(self) -> None:
        body = await _agent_call(
            config=GenerationConfig(max_new_tokens=64, do_sample=False),
        )

        assert body["options"] == {"num_predict": 64, "temperature": 0.0}

    async def test_raw_options_reach_the_daemon(self) -> None:
        body = await _agent_call(options={"num_thread": 6}, num_ctx=8192)

        assert body["options"] == {"num_thread": 6, "num_ctx": 8192}

    async def test_a_call_wins_over_every_default(self) -> None:
        capture = _Capture()
        client = HTTPClient(transport=httpx.MockTransport(capture))
        gen = OllamaGenerator(
            "some-model",
            http_client=client,
            options={"temperature": 0.9, "num_thread": 6},
            config=GenerationConfig(max_new_tokens=64, top_k=20),
        )

        await gen.chat(
            MESSAGES,
            config=GenerationConfig(max_new_tokens=128),
            top_k=5,
        )
        await client.aclose()

        assert capture.bodies[0]["options"] == {
            "temperature": 0.9,
            "num_thread": 6,
            "num_predict": 128,
            "top_k": 5,
        }

    def test_num_ctx_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            OllamaGenerator("some-model", num_ctx=0)

    def test_num_ctx_given_twice_is_refused(self) -> None:
        with pytest.raises(ValueError, match="both"):
            OllamaGenerator("some-model", num_ctx=4096, options={"num_ctx": 8192})


class TestCacheKeyCarriesDefaults:
    """A generator never answers from another's cached completions."""

    def test_ollama_context_window_is_part_of_the_key(self) -> None:
        small = OllamaGenerator("some-model", num_ctx=2048)
        large = OllamaGenerator("some-model", num_ctx=32768)

        assert small._key_params(None, {}) != large._key_params(None, {})

    def test_ollama_default_config_is_part_of_the_key(self) -> None:
        short = OllamaGenerator("some-model", config=GenerationConfig(max_new_tokens=8))
        plain = OllamaGenerator("some-model")

        assert short._key_params(None, {}) == {"max_new_tokens": 8}
        assert plain._key_params(None, {}) == {}

    def test_text_generator_default_config_is_part_of_the_key(self) -> None:
        short = TextGenerator(
            "some/model",
            hardware=_cpu(),
            config=GenerationConfig(max_new_tokens=8, do_sample=False),
        )

        assert short._key_params(GenerationConfig(max_new_tokens=16), {}) == {
            "max_new_tokens": 16,
            "do_sample": False,
        }


class TestTextGeneratorDefaults:
    """The default config reaches ``model.generate`` on a real (tiny) model."""

    async def test_an_agent_style_call_uses_the_default(self) -> None:
        pytest.importorskip("torch")
        pytest.importorskip("transformers")
        from tests.genai.test_text_seed import _tiny_model, _tiny_tokenizer

        gen = TextGenerator(
            "tiny-random-llama",
            device="cpu",
            hardware=_cpu(),
            config=GenerationConfig(max_new_tokens=3, do_sample=False),
        )
        gen._model = _tiny_model()
        tokenizer = _tiny_tokenizer()
        tokenizer.chat_template = (
            "{% for m in messages %}{{ m['content'] }} {% endfor %}"
        )
        gen._tokenizer = tokenizer
        seen: list[dict[str, Any]] = []
        original = gen._model.generate

        def tracked(*args: Any, **kwargs: Any) -> Any:
            """Record the kwargs ``model.generate`` received.

            Args:
                *args (Any): Positional arguments, forwarded.
                **kwargs (Any): Keyword arguments, recorded and forwarded.

            Returns:
                Any: The real output.
            """
            seen.append(kwargs)
            return original(*args, **kwargs)

        gen._model.generate = tracked

        await gen.chat([{"role": "user", "content": "w1 w2"}])

        assert seen[0]["max_new_tokens"] == 3
        assert seen[0]["do_sample"] is False
