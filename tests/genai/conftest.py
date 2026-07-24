"""Shared genai test fixtures — the three-tier test infrastructure.

Camada 1 (``unit``) uses :class:`FakeTextBackend` and ``make_stub_httpx`` to
exercise wiring/parsing without any model weights or network. Camada 2
(``@pytest.mark.model``) uses ``tiny_causal_lm`` to run the real code path
against a minuscule downloaded model. See ``planning/genai/validation-strategy.md``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest


class FakeTextBackend:
    """Scriptable in-memory ``TextBackend`` for wiring tests.

    Pops queued replies in order and records every call, so a test can assert
    both what the caller sent and how many times the backend was hit (used to
    prove generation-cache hits, pipeline tool loops, router plumbing, …).

    Attributes:
        replies (list[str]): Remaining scripted replies, consumed FIFO.
        calls (list[tuple[str, Any]]): ``(method, payload)`` per invocation.
    """

    def __init__(self, replies: list[str] | None = None) -> None:
        """Initialize the backend with an optional reply script.

        Args:
            replies (list[str] | None): Replies handed out in order by
                ``generate`` / ``chat`` / ``stream``. Empty string when drained.
        """
        self.replies: list[str] = list(replies or [])
        self.calls: list[tuple[str, Any]] = []

    def _next(self) -> str:
        """Return the next scripted reply, or ``""`` when drained."""
        return self.replies.pop(0) if self.replies else ""

    async def generate(
        self,
        prompt: str,
        *,
        config: Any = None,
        **kwargs: Any,
    ) -> str:
        """Record the call and return the next scripted reply."""
        self.calls.append(("generate", prompt))
        return self._next()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        config: Any = None,
        **kwargs: Any,
    ) -> str:
        """Record the call and return the next scripted reply."""
        self.calls.append(("chat", messages))
        return self._next()

    async def stream(
        self,
        prompt: str,
        *,
        config: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Record the call and yield the next scripted reply char by char."""
        self.calls.append(("stream", prompt))
        for piece in self._next():
            yield piece


@pytest.fixture
def fake_text_backend() -> FakeTextBackend:
    """Return a fresh empty :class:`FakeTextBackend`."""
    return FakeTextBackend()


@pytest.fixture
def make_stub_httpx() -> Callable[
    [Callable[[httpx.Request], httpx.Response]], httpx.AsyncClient
]:
    """Return a factory building an ``httpx.AsyncClient`` over a mock handler.

    The handler is a plain function ``(request) -> Response`` and may hold state
    (e.g. fail N times then succeed) to drive retry/backoff tests for #8.
    """

    def _make(
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return _make


TINY_CAUSAL_LM: str = "hf-internal-testing/tiny-random-LlamaForCausalLM"


@pytest.fixture(scope="session")
def tiny_causal_lm() -> Any:  # pragma: no cover - opt-in, needs download
    """Load a minuscule causal LM once per session (camada 2, ``@model``).

    Returns:
        Any: A loaded :class:`~tempest_fastapi_sdk.genai.TextGenerator`. Output
        is gibberish — this fixture proves the real ``transformers`` code path
        runs (stop strings, seed, streaming), not generation quality.
    """
    from tempest_fastapi_sdk.genai import TextGenerator

    generator = TextGenerator(TINY_CAUSAL_LM, device="cpu")
    generator.load()
    return generator
