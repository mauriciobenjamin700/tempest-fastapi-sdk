"""``OllamaGenerator`` warns when the daemon truncated the prompt.

Ollama cuts a prompt longer than the context window and answers ``200``
with no error. Measured on Ollama 0.30.11 on CPU (``qwen2.5:0.5b``,
``num_gpu=0``): every overflowing prompt came back with ``prompt_eval_count``
equal to half the window plus 2 — 1026 at ``num_ctx`` 2048, 2050 at the
default 4096, 4098 at 8192, 8194 at 16384, whatever the prompt length — and
the 2026-11-26/27 study saw 2050 (``qwen2.5:3b``, ``qwen2.5-coder:14b``) and
2051 (``lfm2.5-thinking:1.2b``, ``ministral-3:14b``) at 4096. A prompt that
fits reports its own size (1243 at 2048), so the warning keys on that band.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import OllamaGenerator

_LOGGER_NAME: str = "tempest_fastapi_sdk.genai.ollama"


class _CitySchema(BaseModel):
    """Minimal structured target."""

    city: str


def _daemon(
    prompt_eval_count: int,
    requests: list[httpx.Request],
    *,
    loaded: list[dict[str, Any]] | None = None,
    ps_status: int = 200,
) -> httpx.MockTransport:
    """Return a transport that answers like the daemon with a fixed count.

    Args:
        prompt_eval_count (int): The ``prompt_eval_count`` of every reply.
        requests (list[httpx.Request]): Receives every request.
        loaded (list[dict[str, Any]] | None): The ``/api/ps`` model list.
        ps_status (int): Status of the ``/api/ps`` reply.

    Returns:
        httpx.MockTransport: The transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/ps":
            return httpx.Response(ps_status, json={"models": loaded or []})
        stats: dict[str, Any] = {
            "prompt_eval_count": prompt_eval_count,
            "eval_count": 1,
        }
        if request.url.path == "/api/generate":
            body = json.loads(request.content)
            if body.get("stream"):
                lines = [
                    json.dumps({"response": "ok", "done": False}),
                    json.dumps({"response": "", "done": True, **stats}),
                ]
                return httpx.Response(200, text="\n".join(lines))
            return httpx.Response(200, json={"response": "ok", **stats})
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": '{"city": "Recife"}'},
                **stats,
            },
        )

    return httpx.MockTransport(handler)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the truncation warnings captured so far.

    Args:
        caplog (pytest.LogCaptureFixture): The capture fixture.

    Returns:
        list[logging.LogRecord]: The warning records of the Ollama logger.
    """
    return [
        record
        for record in caplog.records
        if record.name == _LOGGER_NAME and record.levelno == logging.WARNING
    ]


class TestTruncationWarning:
    async def test_measured_truncation_at_the_default_window_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        requests: list[httpx.Request] = []
        loaded = [{"name": "qwen2.5:3b", "model": "qwen2.5:3b", "context_length": 4096}]
        gen = OllamaGenerator(
            "qwen2.5:3b", transport=_daemon(2050, requests, loaded=loaded)
        )
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            await gen.chat([{"role": "user", "content": "long"}])
        records = _warnings(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert "2050" in message
        assert "4096" in message
        assert "num_ctx" in message

    async def test_explicit_num_ctx_needs_no_lookup(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        requests: list[httpx.Request] = []
        gen = OllamaGenerator(
            "qwen2.5:0.5b", num_ctx=2048, transport=_daemon(1026, requests)
        )
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            await gen.chat([{"role": "user", "content": "long"}])
        assert len(_warnings(caplog)) == 1
        assert [request.url.path for request in requests] == ["/api/chat"]

    async def test_a_prompt_that_fits_does_not_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        requests: list[httpx.Request] = []
        gen = OllamaGenerator(
            "qwen2.5:0.5b", num_ctx=2048, transport=_daemon(1243, requests)
        )
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            await gen.chat([{"role": "user", "content": "fits"}])
        assert _warnings(caplog) == []

    @pytest.mark.parametrize(
        ("count", "warns"),
        [
            (2047, False),
            (2048, True),
            (2050, True),
            (2064, True),
            (2065, False),
            (4096, True),
        ],
    )
    async def test_band_edges(
        self, caplog: pytest.LogCaptureFixture, count: int, warns: bool
    ) -> None:
        requests: list[httpx.Request] = []
        gen = OllamaGenerator("m", num_ctx=4096, transport=_daemon(count, requests))
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            await gen.chat([{"role": "user", "content": "x"}])
        assert bool(_warnings(caplog)) is warns

    async def test_per_call_num_ctx_is_the_window(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        requests: list[httpx.Request] = []
        gen = OllamaGenerator("m", num_ctx=4096, transport=_daemon(4098, requests))
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            await gen.chat([{"role": "user", "content": "x"}], num_ctx=8192)
        assert len(_warnings(caplog)) == 1
        assert "8192" in _warnings(caplog)[0].getMessage()

    async def test_every_call_path_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        requests: list[httpx.Request] = []
        gen = OllamaGenerator("m", num_ctx=4096, transport=_daemon(2050, requests))
        messages: list[dict[str, Any]] = [{"role": "user", "content": "x"}]
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            await gen.generate("x")
            await gen.chat(messages)
            await gen.chat_with_tools(messages, [])
            await gen.generate_structured("x", _CitySchema)
            await gen.chat_structured(messages, _CitySchema)
            pieces = [piece async for piece in gen.stream("x")]
        assert pieces == ["ok"]
        assert len(_warnings(caplog)) == 6

    async def test_loaded_window_is_looked_up_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        requests: list[httpx.Request] = []
        loaded = [
            {"name": "other:1b", "model": "other:1b", "context_length": 2048},
            {
                "name": "llama3.2:latest",
                "model": "llama3.2:latest",
                "context_length": 16384,
            },
        ]
        gen = OllamaGenerator(
            "llama3.2", transport=_daemon(8194, requests, loaded=loaded)
        )
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            await gen.chat([{"role": "user", "content": "x"}])
            await gen.chat([{"role": "user", "content": "x"}])
        assert len(_warnings(caplog)) == 2
        assert "16384" in _warnings(caplog)[0].getMessage()
        assert [request.url.path for request in requests].count("/api/ps") == 1

    async def test_failed_lookup_is_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        requests: list[httpx.Request] = []
        gen = OllamaGenerator("m", transport=_daemon(2050, requests, ps_status=404))
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            reply = await gen.chat([{"role": "user", "content": "x"}])
        assert reply == '{"city": "Recife"}'
        assert _warnings(caplog) == []

    async def test_no_count_means_no_lookup(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"message": {"content": "oi"}})

        gen = OllamaGenerator("m", transport=httpx.MockTransport(handler))
        await gen.chat([{"role": "user", "content": "x"}])
        assert [request.url.path for request in requests] == ["/api/chat"]
