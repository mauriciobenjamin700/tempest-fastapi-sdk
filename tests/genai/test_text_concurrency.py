"""``max_concurrent`` bounds how many local decodings run at once.

Every local generation runs on a worker thread, and on CPU each thread's
``model.generate`` already uses the whole intra-op thread pool, so N
concurrent agent runs used to mean N threads fighting over the same cores.
``max_concurrent`` queues the extra calls on a pool of that many dedicated
threads instead.

No torch in CI, so the blocking ``_*_sync`` methods are replaced by fakes
that count how many of them are inside at once and wait on a gate the test
opens.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import TextGenerator, VisionTextGenerator
from tempest_fastapi_sdk.genai.text import _decode_in_thread, _decoding_executor

DEADLINE_SECONDS: float = 5.0
"""How long any wait in these tests may take before the test fails."""

SETTLE_SECONDS: float = 0.1
"""How long to wait for a call that should *not* start to show up anyway."""


class _Answer(BaseModel):
    """A schema for the structured calls."""

    text: str


class _GatedSync:
    """Stand-in for a ``_*_sync`` method that counts concurrent callers.

    Attributes:
        active (int): Calls currently inside.
        peak (int): The most calls ever inside at once.
        calls (int): How many calls entered in total.
        gate (threading.Event): Opened by the test to let calls finish.
    """

    def __init__(self) -> None:
        """Start empty, with the gate closed."""
        self.active: int = 0
        self.peak: int = 0
        self.calls: int = 0
        self.gate: threading.Event = threading.Event()
        self._lock: threading.Lock = threading.Lock()

    def __call__(self, *args: Any) -> Any:
        """Enter, wait for the gate (or the stop event), then leave.

        Args:
            *args (Any): The real method's positional arguments; for the
                one-shot methods the last one is the ``stop_event``.

        Returns:
            Any: A value every caller can parse — a JSON object string.
        """
        with self._lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
        try:
            self.gate.wait(DEADLINE_SECONDS)
        finally:
            with self._lock:
                self.active -= 1
        return '{"text": "ok"}'


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Poll ``predicate`` until it holds, failing after the deadline.

    Args:
        predicate (Callable[[], bool]): The condition to wait for.
    """
    deadline = time.monotonic() + DEADLINE_SECONDS
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail("condition not reached before the deadline")
        await asyncio.sleep(0.005)


def _text_calls(
    gen: TextGenerator,
) -> dict[str, tuple[str, Callable[[], Awaitable[Any]]]]:
    """Map each one-shot text method to its blocking impl and a call.

    Args:
        gen (TextGenerator): The generator under test.

    Returns:
        dict[str, tuple[str, Callable[[], Awaitable[Any]]]]: Method name →
        (sync attribute patched, a call of the public method).
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    return {
        "generate": ("_generate_sync", lambda: gen.generate("hi")),
        "chat": ("_chat_sync", lambda: gen.chat(messages)),
        "chat_with_tools": (
            "_chat_with_tools_sync",
            lambda: gen.chat_with_tools(messages, []),
        ),
        "generate_structured": (
            "_generate_structured_sync",
            lambda: gen.generate_structured("hi", _Answer),
        ),
        "chat_structured": (
            "_chat_structured_sync",
            lambda: gen.chat_structured(messages, _Answer),
        ),
    }


TEXT_METHODS: list[str] = [
    "generate",
    "chat",
    "chat_with_tools",
    "generate_structured",
    "chat_structured",
]


async def _run_four(
    work: _GatedSync,
    call: Callable[[], Awaitable[Any]],
    expected: int,
) -> None:
    """Start four calls, check ``expected`` run at once, then drain them.

    Args:
        work (_GatedSync): The patched blocking method.
        call (Callable[[], Awaitable[Any]]): Starts one public call.
        expected (int): How many must be inside at once.
    """
    tasks = [asyncio.ensure_future(call()) for _ in range(4)]
    await _wait_until(lambda: work.active == expected)
    await asyncio.sleep(SETTLE_SECONDS)
    assert work.active == expected
    work.gate.set()
    await asyncio.wait_for(asyncio.gather(*tasks), DEADLINE_SECONDS)
    assert work.peak == expected
    assert work.calls == 4


class TestValidation:
    """``max_concurrent`` must be a positive count or ``None``."""

    @pytest.mark.parametrize("value", [0, -1])
    def test_text_generator_rejects_non_positive(self, value: int) -> None:
        with pytest.raises(ValueError, match="max_concurrent must be positive"):
            TextGenerator("some/model", max_concurrent=value)

    @pytest.mark.parametrize("value", [0, -1])
    def test_vision_generator_rejects_non_positive(self, value: int) -> None:
        with pytest.raises(ValueError, match="max_concurrent must be positive"):
            VisionTextGenerator("some/model", max_concurrent=value)

    def test_defaults_to_no_limit(self) -> None:
        assert TextGenerator("some/model").max_concurrent is None
        assert VisionTextGenerator("some/model").max_concurrent is None


class TestTextGeneratorLimit:
    """Every public ``TextGenerator`` method honours the limit."""

    @pytest.mark.parametrize("method", TEXT_METHODS)
    @pytest.mark.parametrize("limit", [1, 2])
    async def test_runs_at_most_the_limit_at_once(
        self,
        method: str,
        limit: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model", max_concurrent=limit)
        attribute, call = _text_calls(gen)[method]
        work = _GatedSync()
        monkeypatch.setattr(gen, attribute, work)

        await _run_four(work, call, limit)

    @pytest.mark.parametrize("method", TEXT_METHODS)
    async def test_without_a_limit_all_start(
        self,
        method: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model")
        attribute, call = _text_calls(gen)[method]
        work = _GatedSync()
        monkeypatch.setattr(gen, attribute, work)

        await _run_four(work, call, 4)


class TestDedicatedThread:
    """With a bound, decoding always lands on the generator's own threads.

    A semaphore in front of the shared default executor bounds the count
    but not *which* thread decodes, and on CPU every distinct calling
    thread keeps its own OpenMP team: measured, serial decoding went from
    4.5 s to 6.4 s per call once it had alternated across four threads.
    """

    @pytest.mark.parametrize("method", TEXT_METHODS)
    async def test_every_call_runs_on_the_same_pool_thread(
        self,
        method: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model", max_concurrent=1)
        attribute, call = _text_calls(gen)[method]
        names: list[str] = []

        def _sync(*args: Any) -> str:
            """Record which thread decodes.

            Args:
                *args (Any): Unused.

            Returns:
                str: A value every caller can parse.
            """
            names.append(threading.current_thread().name)
            return '{"text": "ok"}'

        monkeypatch.setattr(gen, attribute, _sync)

        for _ in range(4):
            await asyncio.gather(
                call(),
                *(asyncio.to_thread(time.sleep, 0.01) for _ in range(4)),
            )

        assert len(names) == 4
        assert len(set(names)) == 1
        assert names[0].startswith("genai-decode-some/model")


class TestStreamLimit:
    """``stream`` takes a slot for as long as its worker thread runs."""

    @staticmethod
    def _patch_stream(
        gen: TextGenerator,
        work: _GatedSync,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Replace ``_stream_sync`` with one that emits, then waits on the gate.

        Args:
            gen (TextGenerator): The generator under test.
            work (_GatedSync): Counts the concurrent streams.
            monkeypatch (pytest.MonkeyPatch): Undoes the patch.
        """

        def _stream_sync(
            prompt: str,
            config: Any,
            overrides: dict[str, Any],
            stop_event: threading.Event,
            emit: Callable[[str], None],
        ) -> None:
            """Emit one piece, then block until the gate or the stop event.

            Args:
                prompt (str): Unused.
                config (Any): Unused.
                overrides (dict[str, Any]): Unused.
                stop_event (threading.Event): Set when the consumer leaves.
                emit (Callable[[str], None]): Delivers the piece.
            """
            emit("piece")
            gate = threading.Thread(target=lambda: (stop_event.wait(), work.gate.set()))
            gate.daemon = True
            gate.start()
            work(prompt)

        monkeypatch.setattr(gen, "_stream_sync", _stream_sync)

    async def test_runs_at_most_the_limit_at_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model", max_concurrent=1)
        work = _GatedSync()
        self._patch_stream(gen, work, monkeypatch)

        async def consume() -> list[str]:
            return [piece async for piece in gen.stream("hi")]

        tasks = [asyncio.ensure_future(consume()) for _ in range(3)]
        await _wait_until(lambda: work.active == 1)
        await asyncio.sleep(SETTLE_SECONDS)
        assert work.calls == 1
        work.gate.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), DEADLINE_SECONDS)

        assert results == [["piece"]] * 3
        assert work.peak == 1

    async def test_closing_the_stream_frees_the_slot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model", max_concurrent=1)
        work = _GatedSync()
        self._patch_stream(gen, work, monkeypatch)
        first = gen.stream("hi")
        assert await first.__anext__() == "piece"
        second = asyncio.ensure_future(gen.stream("hi").__anext__())
        await asyncio.sleep(SETTLE_SECONDS)
        assert not second.done()

        await first.aclose()

        assert await asyncio.wait_for(second, DEADLINE_SECONDS) == "piece"
        work.gate.set()


class TestCancellationAndSlots:
    """Cancelling a call never leaks a slot, and never frees one early."""

    async def test_cancelled_while_queued_frees_nothing_and_never_runs(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model", max_concurrent=1)
        work = _GatedSync()
        monkeypatch.setattr(gen, "_generate_sync", work)
        running = asyncio.ensure_future(gen.generate("hi"))
        await _wait_until(lambda: work.active == 1)
        queued = asyncio.ensure_future(gen.generate("hi"))
        await asyncio.sleep(SETTLE_SECONDS)

        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        work.gate.set()
        await asyncio.wait_for(running, DEADLINE_SECONDS)
        await asyncio.wait_for(gen.generate("hi"), DEADLINE_SECONDS)

        assert work.calls == 2
        assert work.peak == 1

    async def test_cancelled_queued_stream_takes_no_slot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen = TextGenerator("some/model", max_concurrent=1)
        work = _GatedSync()
        monkeypatch.setattr(gen, "_generate_sync", work)
        streamed: list[str] = []
        monkeypatch.setattr(
            gen,
            "_stream_sync",
            lambda *args: streamed.append("ran"),
        )
        running = asyncio.ensure_future(gen.generate("hi"))
        await _wait_until(lambda: work.active == 1)
        queued = asyncio.ensure_future(gen.stream("hi").__anext__())
        await asyncio.sleep(SETTLE_SECONDS)

        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        work.gate.set()
        await asyncio.wait_for(running, DEADLINE_SECONDS)
        await asyncio.wait_for(gen.generate("hi"), DEADLINE_SECONDS)

        assert streamed == []
        assert work.peak == 1

    async def test_cancelled_while_decoding_sets_the_event_and_holds_the_slot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The slot is held until the stopped thread has really returned.

        The first call's fake keeps working for a while after it sees the
        stop event — a forward pass in flight — and the next queued call
        must not start before it ends.
        """
        gen = TextGenerator("some/model", max_concurrent=1)
        timeline: list[str] = []
        seen: list[threading.Event] = []

        def _sync(*args: Any) -> str:
            """Record start/end; the first call lingers after its stop event.

            Args:
                *args (Any): The last one is the ``stop_event``.

            Returns:
                str: A completion.
            """
            event = args[-1]
            index = len(seen)
            seen.append(event)
            timeline.append(f"start-{index}")
            if index == 0:
                event.wait(DEADLINE_SECONDS)
                time.sleep(SETTLE_SECONDS)
            timeline.append(f"end-{index}")
            return "ok"

        monkeypatch.setattr(gen, "_generate_sync", _sync)
        first = asyncio.ensure_future(gen.generate("hi"))
        await _wait_until(lambda: len(seen) == 1)

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = await asyncio.wait_for(gen.generate("hi"), DEADLINE_SECONDS)

        assert second == "ok"
        assert seen[0].is_set()
        assert timeline == ["start-0", "end-0", "start-1", "end-1"]


class TestDecodeInThreadExecutor:
    """The helper itself, on a bounded pool."""

    async def test_cancelled_while_queued_sets_the_callers_event(self) -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        gate = threading.Event()
        busy = asyncio.get_running_loop().run_in_executor(
            executor,
            gate.wait,
            DEADLINE_SECONDS,
        )
        event = threading.Event()
        ran: list[bool] = []
        call = asyncio.ensure_future(
            _decode_in_thread(lambda e: ran.append(True), event, executor=executor),
        )
        await asyncio.sleep(SETTLE_SECONDS)

        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        gate.set()
        await busy
        executor.shutdown(wait=True)

        assert event.is_set()
        assert ran == []

    async def test_runs_on_the_pools_threads_only(self) -> None:
        executor = _decoding_executor(1, "some/model")
        assert executor is not None
        names: list[str] = []

        for _ in range(3):
            await _decode_in_thread(
                lambda e: names.append(threading.current_thread().name),
                None,
                executor=executor,
            )
        executor.shutdown(wait=True)

        assert len(set(names)) == 1
        assert names[0].startswith("genai-decode-some/model")

    def test_no_bound_builds_no_pool(self) -> None:
        assert _decoding_executor(None, "some/model") is None


class TestVisionTextGeneratorLimit:
    """The vision-language model, driven by the agent's image tool, too."""

    @pytest.mark.parametrize(
        ("attribute", "method"),
        [("_generate_sync", "generate"), ("_chat_sync", "chat")],
    )
    async def test_runs_at_most_the_limit_at_once(
        self,
        attribute: str,
        method: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        vlm = VisionTextGenerator("some/model", max_concurrent=1)
        work = _GatedSync()
        monkeypatch.setattr(vlm, attribute, work)
        argument: Any = (
            "hi" if method == "generate" else [{"role": "user", "content": "hi"}]
        )

        await _run_four(work, lambda: getattr(vlm, method)(argument), 1)
