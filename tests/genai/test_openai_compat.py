"""The hosted backend speaks the wire format, and reports what it cost.

Every test drives a mock transport, so nothing here needs a key, a network
or a provider. What is under test is the body that goes out and the reading
of what comes back — the two halves a vendor SDK would have hidden.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    OpenAICompatGenerator,
    TextBackend,
    TokenUsage,
)


def _completion(content: str = "ok", usage: dict[str, Any] | None = None) -> dict:
    """Build one non-streaming response body.

    Args:
        content (str): The assistant message content.
        usage (dict[str, Any] | None): The usage object, when the provider
            reports one.

    Returns:
        dict: A decoded ``/chat/completions`` response.
    """
    body: dict[str, Any] = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


class _Recorder:
    """Captures the last request a generator sent.

    Attributes:
        requests (list[httpx.Request]): Every request, in order.
        response (dict): The body to answer with.
        status (int): The status to answer with.
    """

    def __init__(self, response: dict | None = None, status: int = 200) -> None:
        """Build the recorder.

        Args:
            response (dict | None): Body to return; defaults to a plain
                completion.
            status (int): Status code to return.
        """
        self.requests: list[httpx.Request] = []
        self.response = response if response is not None else _completion()
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer it.

        Args:
            request (httpx.Request): The outgoing request.

        Returns:
            httpx.Response: The canned response.
        """
        self.requests.append(request)
        return httpx.Response(self.status, json=self.response)

    @property
    def body(self) -> dict:
        """Return the last request body, decoded.

        Returns:
            dict: The JSON that was sent.
        """
        return json.loads(self.requests[-1].content)


def _generator(recorder: _Recorder, **kwargs: Any) -> OpenAICompatGenerator:
    """Build a generator wired to ``recorder``.

    Args:
        recorder (_Recorder): The stub transport handler.
        **kwargs (Any): Extra constructor arguments.

    Returns:
        OpenAICompatGenerator: The generator under test.
    """
    return OpenAICompatGenerator(
        "test-model",
        api_key="sk-test",
        base_url="https://api.example.com/v1",
        transport=httpx.MockTransport(recorder),
        **kwargs,
    )


class TestConstruction:
    """What the constructor accepts and refuses."""

    def test_empty_api_key_raises_at_construction(self) -> None:
        """An empty key fails here, not inside a background job later."""
        with pytest.raises(ValueError, match="api_key"):
            OpenAICompatGenerator("m", api_key="")

    def test_base_url_trailing_slash_is_dropped(self) -> None:
        """A trailing slash must not produce a double slash in the path."""
        gen = OpenAICompatGenerator("m", api_key="k", base_url="https://x.test/v1/")
        assert gen.base_url == "https://x.test/v1"

    def test_satisfies_the_text_backend_protocol(self) -> None:
        """It plugs in wherever a ``TextBackend`` is expected.

        The protocol's own docstring names "a hosted API" as the case it is
        meant to be filled with; this asserts the claim instead of trusting
        that the signatures happened to line up.
        """
        backend: TextBackend = OpenAICompatGenerator("m", api_key="k")
        assert backend is not None


class TestRequestBody:
    """What goes out on the wire."""

    async def test_prompt_becomes_a_user_message(self) -> None:
        """A prompt is sent as one user turn."""
        rec = _Recorder()
        await _generator(rec).generate("oi")
        assert rec.body["messages"] == [{"role": "user", "content": "oi"}]
        assert rec.body["model"] == "test-model"
        assert rec.body["stream"] is False

    async def test_system_goes_in_its_own_turn(self) -> None:
        """``system=`` becomes a system turn before the prompt."""
        rec = _Recorder()
        await _generator(rec).generate_with_usage("doc", system="extraia campos")
        assert rec.body["messages"] == [
            {"role": "system", "content": "extraia campos"},
            {"role": "user", "content": "doc"},
        ]

    async def test_config_maps_to_wire_names(self) -> None:
        """``max_new_tokens`` is HuggingFace's name; the wire wants ``max_tokens``."""
        rec = _Recorder()
        config = GenerationConfig(max_new_tokens=500, temperature=0.7, top_p=0.9)
        await _generator(rec).generate("x", config=config)
        assert rec.body["max_tokens"] == 500
        assert rec.body["temperature"] == 0.7
        assert rec.body["top_p"] == 0.9
        assert "max_new_tokens" not in rec.body

    async def test_per_call_kwargs_beat_config(self) -> None:
        """A per-call value wins over the shared config."""
        rec = _Recorder()
        config = GenerationConfig(temperature=0.1)
        await _generator(rec).generate("x", config=config, temperature=0.9)
        assert rec.body["temperature"] == 0.9

    async def test_extra_body_is_merged(self) -> None:
        """Provider extensions ride along without a branch per vendor.

        This is the DeepSeek case: a hybrid reasoning model spends
        ``max_tokens`` on hidden reasoning unless thinking is turned off,
        and returns empty content.
        """
        rec = _Recorder()
        gen = _generator(rec, extra_body={"thinking": {"type": "disabled"}})
        await gen.generate("x")
        assert rec.body["thinking"] == {"type": "disabled"}

    async def test_extra_body_cannot_hijack_the_model(self) -> None:
        """Computed fields win, so ``extra_body`` cannot redirect the call."""
        rec = _Recorder()
        gen = _generator(rec, extra_body={"model": "someone-elses-model"})
        await gen.generate("x")
        assert rec.body["model"] == "test-model"

    async def test_authorization_header_is_sent(self) -> None:
        """The key travels as a bearer token."""
        rec = _Recorder()
        await _generator(rec).generate("x")
        assert rec.requests[-1].headers["Authorization"] == "Bearer sk-test"

    async def test_extra_headers_cannot_override_authorization(self) -> None:
        """A stray header must not silently replace the credential."""
        rec = _Recorder()
        gen = _generator(rec, extra_headers={"Authorization": "Bearer wrong"})
        await gen.generate("x")
        assert rec.requests[-1].headers["Authorization"] == "Bearer sk-test"

    async def test_url_is_the_chat_completions_path(self) -> None:
        """The path is appended to the configured base URL."""
        rec = _Recorder()
        await _generator(rec).generate("x")
        assert str(rec.requests[-1].url) == (
            "https://api.example.com/v1/chat/completions"
        )


class TestResponseReading:
    """What comes back."""

    async def test_generate_returns_the_content(self) -> None:
        """The bare ``generate`` returns a string, as the protocol declares."""
        rec = _Recorder(_completion("resposta"))
        assert await _generator(rec).generate("x") == "resposta"

    async def test_usage_is_reported(self) -> None:
        """``generate_with_usage`` returns the provider's own counts."""
        rec = _Recorder(
            _completion(
                "ok",
                usage={
                    "prompt_tokens": 120,
                    "completion_tokens": 40,
                    "total_tokens": 160,
                },
            ),
        )
        _text, usage = await _generator(rec).generate_with_usage("x")
        assert usage == TokenUsage(input_tokens=120, output_tokens=40, total_tokens=160)

    async def test_usage_is_none_when_absent(self) -> None:
        """No ``usage`` in the response means ``None``, never a zeroed usage.

        A zeroed usage would claim the call was free, which is a different
        statement from "the provider did not say".
        """
        rec = _Recorder(_completion("ok"))
        _text, usage = await _generator(rec).generate_with_usage("x")
        assert usage is None

    async def test_empty_choices_yields_empty_string(self) -> None:
        """A response with no choices must not raise IndexError."""
        rec = _Recorder({"choices": []})
        assert await _generator(rec).generate("x") == ""

    async def test_null_content_yields_empty_string(self) -> None:
        """``content: null`` is what a filtered completion looks like."""
        rec = _Recorder({"choices": [{"message": {"content": None}}]})
        assert await _generator(rec).generate("x") == ""

    async def test_error_status_propagates(self) -> None:
        """A 401 reaches the caller with its status intact.

        Translating it here would hide which of "bad key" (401), "slow
        down" (429) and "provider is down" (5xx) happened — the three that
        call for different handling.
        """
        rec = _Recorder({"error": "nope"}, status=401)
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await _generator(rec).generate("x")
        assert excinfo.value.response.status_code == 401


class TestStreaming:
    """Server-sent events become text deltas."""

    async def test_stream_yields_deltas_and_stops_at_done(self) -> None:
        """Deltas arrive in order; the sentinel and blank lines are skipped."""
        events = (
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n'
            "\n"
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n'
            ": keep-alive\n"
            "data: [DONE]\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            """Answer with a canned SSE body.

            Args:
                request (httpx.Request): Ignored.

            Returns:
                httpx.Response: The streamed body.
            """
            return httpx.Response(200, content=events.encode())

        gen = OpenAICompatGenerator(
            "m",
            api_key="k",
            base_url="https://x.test/v1",
            transport=httpx.MockTransport(handler),
        )
        pieces = [piece async for piece in gen.stream("hi")]
        assert "".join(pieces) == "Hello"

    async def test_stream_sets_the_stream_flag(self) -> None:
        """The request must ask for a stream, or the body never chunks."""
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """Record the body and answer with the done sentinel.

            Args:
                request (httpx.Request): The outgoing request.

            Returns:
                httpx.Response: A body carrying only the sentinel.
            """
            seen.append(json.loads(request.content))
            return httpx.Response(200, content=b"data: [DONE]\n")

        gen = OpenAICompatGenerator(
            "m",
            api_key="k",
            base_url="https://x.test/v1",
            transport=httpx.MockTransport(handler),
        )
        _ = [piece async for piece in gen.stream("hi")]
        assert seen[-1]["stream"] is True


class TestTokenUsage:
    """The usage value type itself."""

    def test_adds_for_a_multi_call_job(self) -> None:
        """Map-reduce records the job, not each leg."""
        total = TokenUsage(10, 5, 15) + TokenUsage(20, 7, 27)
        assert total == TokenUsage(30, 12, 42)

    def test_reported_total_wins_over_the_sum(self) -> None:
        """A provider may bill something other than input + output.

        Cached-prefix discounts show up exactly this way, so recomputing the
        total would overstate the cost.
        """
        usage = TokenUsage.from_payload(
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 120},
        )
        assert usage == TokenUsage(100, 50, 120)

    def test_missing_total_falls_back_to_the_sum(self) -> None:
        """Without a reported total, the sum is the best available answer."""
        usage = TokenUsage.from_payload(
            {"prompt_tokens": 100, "completion_tokens": 50},
        )
        assert usage == TokenUsage(100, 50, 150)

    def test_none_payload_is_none(self) -> None:
        """No usage object means no usage, not a zeroed one."""
        assert TokenUsage.from_payload(None) is None
