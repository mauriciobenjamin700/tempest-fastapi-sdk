"""Cancelling a local generation stops the model, not only the wait.

Local generation runs inside ``asyncio.to_thread``, and a thread cannot be
interrupted: cancelling the coroutine that awaits it — an ``asyncio.timeout``,
an agent budget, a client that went away — only abandons the wrapper. Before
this guard, the agent loop (which never passes a ``stop_event``) left the
worker decoding to ``max_new_tokens`` after every budget timeout: measured
with ``Qwen/Qwen2.5-0.5B-Instruct`` on CPU, 300 forced tokens, a 1 s budget,
the thread kept decoding 10.8 s after the run had returned ``timeout``.

No torch in CI, so each public method is exercised with its blocking
implementation replaced by one that only returns once the event it was
handed is set — exactly what the transformers stopping criterion does.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import TextGenerator, VisionTextGenerator
from tempest_fastapi_sdk.genai.text import _decode_in_thread

STOP_DEADLINE_SECONDS: float = 5.0
"""How long the worker may take to notice the event before the test fails."""


class _Answer(BaseModel):
    """A schema for the structured calls."""

    text: str


class _BlockingSync:
    """Stand-in for a ``_*_sync`` method that decodes until told to stop."""

    def __init__(self) -> None:
        """Start with no event seen and the worker not yet finished."""
        self.event: threading.Event | None = None
        self.finished: threading.Event = threading.Event()

    def __call__(self, *args: Any) -> Any:
        """Block until the stop event handed in as the last argument is set.

        Args:
            *args (Any): The positional arguments of the real method; the
                last one is its ``stop_event``.

        Returns:
            Any: Never meaningful — the caller was cancelled.
        """
        event = args[-1]
        self.event = event
        if event is not None:
            event.wait(STOP_DEADLINE_SECONDS)
        self.finished.set()
        return None


async def _cancel_soon(call: Awaitable[Any]) -> None:
    """Await ``call`` under a timeout that always fires.

    Args:
        call (Awaitable[Any]): The generation to cancel.
    """
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await call


def _text_calls(
    gen: TextGenerator,
) -> dict[str, tuple[str, Callable[[threading.Event | None], Awaitable[Any]]]]:
    """Map each public text method to its blocking impl and a call.

    Args:
        gen (TextGenerator): The generator under test.

    Returns:
        dict[str, tuple[str, Callable[[threading.Event | None], Awaitable[Any]]]]:
        Method name → (sync attribute patched, call taking a ``stop_event``).
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    return {
        "generate": (
            "_generate_sync",
            lambda e: gen.generate("hi", stop_event=e),
        ),
        "chat": ("_chat_sync", lambda e: gen.chat(messages, stop_event=e)),
        "chat_with_tools": (
            "_chat_with_tools_sync",
            lambda e: gen.chat_with_tools(messages, [], stop_event=e),
        ),
        "generate_structured": (
            "_generate_structured_sync",
            lambda e: gen.generate_structured("hi", _Answer, stop_event=e),
        ),
        "chat_structured": (
            "_chat_structured_sync",
            lambda e: gen.chat_structured(messages, _Answer, stop_event=e),
        ),
    }


TEXT_METHODS: list[str] = [
    "generate",
    "chat",
    "chat_with_tools",
    "generate_structured",
    "chat_structured",
]


class TestDecodeInThread:
    """The helper every local generation runs through."""

    async def test_returns_the_work_result_and_leaves_the_event_alone(self) -> None:
        event = threading.Event()

        result = await _decode_in_thread(lambda e: e is event, event)

        assert result is True
        assert not event.is_set()

    async def test_cancellation_sets_a_private_event(self) -> None:
        work = _BlockingSync()

        await _cancel_soon(_decode_in_thread(work, None))

        assert await asyncio.to_thread(work.finished.wait, STOP_DEADLINE_SECONDS)
        assert work.event is not None and work.event.is_set()

    async def test_cancellation_sets_the_callers_event(self) -> None:
        event = threading.Event()
        work = _BlockingSync()

        await _cancel_soon(_decode_in_thread(work, event))

        assert work.event is event
        assert event.is_set()


class TestTextGeneratorStopsOnCancel:
    """Every public ``TextGenerator`` method stops its thread when cancelled."""

    @pytest.mark.parametrize("method", TEXT_METHODS)
    async def test_without_a_stop_event(
        self,
        method: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model")
        attribute, call = _text_calls(gen)[method]
        work = _BlockingSync()
        monkeypatch.setattr(gen, attribute, work)

        await _cancel_soon(call(None))

        assert await asyncio.to_thread(work.finished.wait, STOP_DEADLINE_SECONDS)
        assert work.event is not None and work.event.is_set()

    @pytest.mark.parametrize("method", TEXT_METHODS)
    async def test_with_the_callers_stop_event(
        self,
        method: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model")
        attribute, call = _text_calls(gen)[method]
        work = _BlockingSync()
        monkeypatch.setattr(gen, attribute, work)
        event = threading.Event()

        await _cancel_soon(call(event))

        assert work.event is event
        assert event.is_set()


class TestVisionTextGeneratorStopsOnCancel:
    """The vision-language model, driven by the agent's image tool, too."""

    @pytest.mark.parametrize(
        ("attribute", "method"),
        [("_generate_sync", "generate"), ("_chat_sync", "chat")],
    )
    async def test_without_a_stop_event(
        self,
        attribute: str,
        method: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vlm = VisionTextGenerator("some/model")
        work = _BlockingSync()
        monkeypatch.setattr(vlm, attribute, work)
        argument: Any = (
            "hi" if method == "generate" else [{"role": "user", "content": "hi"}]
        )

        await _cancel_soon(getattr(vlm, method)(argument))

        assert await asyncio.to_thread(work.finished.wait, STOP_DEADLINE_SECONDS)
        assert work.event is not None and work.event.is_set()
