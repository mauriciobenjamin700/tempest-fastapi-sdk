"""Both backends answer the same structured call, and both can be stopped.

A service that reads documents into schemas should not care whether the
model is a daemon on localhost or weights in this process. That is a claim
about two classes agreeing on one method, so it is checked here rather than
asserted in prose — including the half that is easy to get wrong: the local
backend generates inside a worker thread, where cancelling the awaiting
coroutine stops nothing at all.

No torch in CI, so the thread half is exercised with a thread that watches
the same event a real generation's stopping criteria would.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from tempest_fastapi_sdk.genai import (
    OllamaGenerator,
    StructuredTextBackend,
    TextGenerator,
)
from tempest_fastapi_sdk.genai.text import _stop_criteria
from tempest_fastapi_sdk.tasks import StageInterruptedError, run_cancellable


class _FakeCriteria:
    """Stands in for ``transformers.StoppingCriteria``."""


class _FakeCriteriaList(list[Any]):
    """Stands in for ``transformers.StoppingCriteriaList``."""


class _FakeTransformers:
    """The two attributes :func:`_stop_criteria` touches."""

    StoppingCriteria = _FakeCriteria
    StoppingCriteriaList = _FakeCriteriaList


class TestStructuredParity:
    """The two backends implement the same structured surface."""

    @pytest.mark.parametrize("backend", [OllamaGenerator, TextGenerator])
    def test_both_backends_satisfy_the_protocol(self, backend: type) -> None:
        assert issubclass(backend, StructuredTextBackend)

    def test_the_local_backend_takes_chat_turns_like_the_daemon(self) -> None:
        local = TextGenerator("some/model")
        daemon = OllamaGenerator("some-tag")
        assert callable(local.chat_structured)
        assert callable(daemon.chat_structured)


class TestStopCriteria:
    """The hook a thread can actually be stopped through."""

    def test_the_criterion_answers_the_event(self) -> None:
        event = threading.Event()
        criteria = _stop_criteria(_FakeTransformers(), event)
        criterion = criteria[0]
        assert criterion(None, None) is False
        event.set()
        assert criterion(None, None) is True


class TestCancellingWorkInAThread:
    """A cancelled job stops the thread, not only the coroutine waiting on it."""

    async def test_the_thread_sees_the_stop_event(self) -> None:
        event = threading.Event()
        stopped_early = threading.Event()

        def blocking_work() -> str:
            """Poll the event the way a stopping criterion would."""
            for _ in range(200):
                if event.is_set():
                    stopped_early.set()
                    return "stopped"
                threading.Event().wait(0.01)
            return "finished"

        async def cancelled() -> bool:
            """Report the work cancelled from the first check on."""
            return True

        with pytest.raises(StageInterruptedError):
            await run_cancellable(
                asyncio.to_thread(blocking_work),
                interrupted=cancelled,
                poll_seconds=0.01,
                stop_event=event,
            )

        assert event.is_set()
        await asyncio.sleep(0.1)
        assert stopped_early.is_set()

    async def test_without_the_event_the_thread_is_left_running(self) -> None:
        release = threading.Event()
        finished = threading.Event()

        def blocking_work() -> str:
            """Run until released, ignoring any coroutine cancellation."""
            release.wait(2.0)
            finished.set()
            return "finished"

        async def cancelled() -> bool:
            """Report the work cancelled from the first check on."""
            return True

        with pytest.raises(StageInterruptedError):
            await run_cancellable(
                asyncio.to_thread(blocking_work),
                interrupted=cancelled,
                poll_seconds=0.01,
            )

        assert not finished.is_set()
        release.set()
        await asyncio.sleep(0.1)
        assert finished.is_set()
