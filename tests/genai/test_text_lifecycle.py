"""TextGenerator lifecycle: one build under a stampede, no unload mid-call,
and a stream that never blocks the event loop.

``transformers`` is replaced by a scripted stand-in through
:func:`tempest_fastapi_sdk.genai.text._require_transformers`, so the real
``_build`` / ``_generate_sync`` / ``stream`` code runs without weights. The
stand-in is slow on purpose — a 0.2 s build and 20 ms per token — because
every defect here is a race that a fast fake would let pass by luck.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tempest_fastapi_sdk.genai import HardwareInfo, ModelRegistry, TextGenerator
from tempest_fastapi_sdk.genai import text as text_module

BUILD_SECONDS: float = 0.2
TOKEN_SECONDS: float = 0.02


class FakeIds:
    """Stands in for the ``input_ids`` tensor; only ``shape`` is read."""

    shape: tuple[int, int] = (1, 1)


class FakeBatch(dict[str, Any]):
    """The tokenizer's output: a mapping with a ``.to(device)``."""

    def to(self, device: Any) -> FakeBatch:
        """Return itself, as a tensor batch moved to ``device`` would.

        Args:
            device (Any): Ignored.

        Returns:
            FakeBatch: This batch.
        """
        return self


class FakeTokenizer:
    """Tokenizes to one id and decodes a token list to text."""

    def __call__(self, prompt: str, **kwargs: Any) -> FakeBatch:
        """Encode ``prompt``.

        Args:
            prompt (str): Ignored.
            **kwargs (Any): Ignored.

        Returns:
            FakeBatch: A batch with ``input_ids``.
        """
        return FakeBatch(input_ids=FakeIds())

    def decode(self, tokens: Any, **kwargs: Any) -> str:
        """Join the generated tokens.

        Args:
            tokens (Any): The generated token strings.
            **kwargs (Any): Ignored.

        Returns:
            str: The completion.
        """
        return "".join(tokens)


class FakeModel:
    """Emits tokens slowly, honouring stopping criteria and streamers.

    Attributes:
        started (threading.Event): Set when a generation starts.
        emitted (int): Tokens produced across all generations.
    """

    device: str = "cpu"

    def __init__(self, tokens: int) -> None:
        """Initialize the model.

        Args:
            tokens (int): Tokens each generation produces unless stopped.
        """
        self.tokens = tokens
        self.started = threading.Event()
        self.emitted = 0

    def to(self, device: str) -> FakeModel:
        """Return itself.

        Args:
            device (str): Ignored.

        Returns:
            FakeModel: This model.
        """
        return self

    def generate(self, **kwargs: Any) -> list[list[str]]:
        """Produce tokens one by one, checking the stop criteria each time.

        Args:
            **kwargs (Any): ``streamer`` and ``stopping_criteria`` are read.

        Returns:
            list[list[str]]: One sequence: the prompt slot plus the tokens.
        """
        self.started.set()
        streamer = kwargs.get("streamer")
        criteria = kwargs.get("stopping_criteria") or []
        produced: list[str] = ["<prompt>"]
        for index in range(self.tokens):
            if any(criterion(None, None) for criterion in criteria):
                break
            time.sleep(TOKEN_SECONDS)
            piece = f"t{index} "
            produced.append(piece)
            self.emitted += 1
            if streamer is not None:
                streamer.put(piece)
        if streamer is not None:
            streamer.end()
        return [produced]


class FakeFromPretrained:
    """A slow ``from_pretrained`` that counts its calls."""

    def __init__(self, make: Any) -> None:
        """Initialize the loader.

        Args:
            make (Any): Zero-arg factory for the loaded object.
        """
        self.make = make
        self.calls = 0
        self._lock = threading.Lock()

    def from_pretrained(self, model_id: str, **kwargs: Any) -> Any:
        """Build the object after a delay, recording the call.

        Args:
            model_id (str): Ignored.
            **kwargs (Any): Ignored.

        Returns:
            Any: A fresh object from the factory.
        """
        with self._lock:
            self.calls += 1
        time.sleep(BUILD_SECONDS)
        return self.make()


class FakeTextStreamer:
    """The subset of ``transformers.TextStreamer`` the SDK relies on."""

    def __init__(self, tokenizer: Any, **kwargs: Any) -> None:
        """Initialize the streamer.

        Args:
            tokenizer (Any): Ignored.
            **kwargs (Any): Ignored.
        """
        self.tokenizer = tokenizer

    def put(self, value: str) -> None:
        """Deliver one piece.

        Args:
            value (str): The token text.
        """
        self.on_finalized_text(value)

    def end(self) -> None:
        """Signal the end of the stream."""
        self.on_finalized_text("", stream_end=True)

    def on_finalized_text(self, text: str, stream_end: bool = False) -> None:
        """Print nothing; subclasses override.

        Args:
            text (str): The piece.
            stream_end (bool): Whether this is the last call.
        """


class FakeTextIteratorStreamer(FakeTextStreamer):
    """A blocking-queue streamer, as ``TextIteratorStreamer`` is."""

    def __init__(self, tokenizer: Any, **kwargs: Any) -> None:
        """Initialize the queue.

        Args:
            tokenizer (Any): Ignored.
            **kwargs (Any): Ignored.
        """
        super().__init__(tokenizer)
        self.queue: queue.Queue[str | None] = queue.Queue()

    def on_finalized_text(self, text: str, stream_end: bool = False) -> None:
        """Queue the piece, or the end marker.

        Args:
            text (str): The piece.
            stream_end (bool): Whether this is the last call.
        """
        if text:
            self.queue.put(text)
        if stream_end:
            self.queue.put(None)

    def __iter__(self) -> Iterator[str]:
        """Yield pieces, blocking on the queue between them.

        Yields:
            str: Each piece.
        """
        while (item := self.queue.get()) is not None:
            yield item


class FakeStoppingCriteria:
    """Base class the SDK subclasses."""


class FakeTransformers:
    """The attributes of ``transformers`` the text path touches."""

    StoppingCriteria = FakeStoppingCriteria
    TextStreamer = FakeTextStreamer
    TextIteratorStreamer = FakeTextIteratorStreamer

    def __init__(self, tokens: int) -> None:
        """Wire the slow loaders.

        Args:
            tokens (int): Tokens per generation.
        """
        self.models: list[FakeModel] = []
        self.AutoTokenizer = FakeFromPretrained(FakeTokenizer)
        self.AutoModelForCausalLM = FakeFromPretrained(self._make_model)
        self._tokens = tokens

    def _make_model(self) -> FakeModel:
        """Build and remember one model.

        Returns:
            FakeModel: The new model.
        """
        model = FakeModel(self._tokens)
        self.models.append(model)
        return model

    @staticmethod
    def StoppingCriteriaList(items: list[Any]) -> list[Any]:  # noqa: N802
        """Return the criteria as a plain list.

        Args:
            items (list[Any]): The criteria.

        Returns:
            list[Any]: The same list.
        """
        return list(items)

    @staticmethod
    def set_seed(seed: int) -> None:
        """Accept a seed.

        Args:
            seed (int): Ignored.
        """


class FakeTorch:
    """Only the dtype attributes are read."""

    float32: str = "float32"
    bfloat16: str = "bfloat16"


def _cpu() -> HardwareInfo:
    """Return a CPU-only snapshot so no probe runs.

    Returns:
        HardwareInfo: The snapshot.
    """
    return HardwareInfo(
        cpu_cores=4,
        ram_total_bytes=8 * 10**9,
        ram_available_bytes=6 * 10**9,
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTransformers:
    """Install the scripted transformers (10 tokens per generation).

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher.

    Returns:
        FakeTransformers: The stand-in, for asserting call counts.
    """
    stand_in = FakeTransformers(tokens=10)
    monkeypatch.setattr(
        text_module,
        "_require_transformers",
        lambda: (FakeTorch(), stand_in),
    )
    return stand_in


@pytest.fixture
def long_fake(monkeypatch: pytest.MonkeyPatch) -> FakeTransformers:
    """Install the scripted transformers (50 tokens per generation).

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher.

    Returns:
        FakeTransformers: The stand-in.
    """
    stand_in = FakeTransformers(tokens=50)
    monkeypatch.setattr(
        text_module,
        "_require_transformers",
        lambda: (FakeTorch(), stand_in),
    )
    return stand_in


class TestColdStart:
    async def test_concurrent_generate_builds_the_model_once(
        self,
        fake: FakeTransformers,
    ) -> None:
        """Three first calls together run ``from_pretrained`` once.

        The shipped defect: each worker thread read ``is_loaded`` as False
        and built its own copy — three builds, and on real weights a
        nondeterministic ``Cannot copy out of meta tensor``.
        """
        gen = TextGenerator("m", hardware=_cpu())

        results = await asyncio.gather(*(gen.generate("hi") for _ in range(3)))

        assert fake.AutoModelForCausalLM.calls == 1
        assert fake.AutoTokenizer.calls == 1
        assert all(result.startswith("t0 ") for result in results)


class TestUnloadDuringGeneration:
    async def test_idle_unload_refuses_while_a_generation_runs(
        self,
        fake: FakeTransformers,
    ) -> None:
        """A generation longer than the idle window is not idle.

        With the clock touched only when a call returned, ``seconds_idle``
        kept growing during the call; ``unload_if_idle`` freed the model
        and the call died on ``'NoneType' object has no attribute
        'decode'``.
        """
        gen = TextGenerator("m", hardware=_cpu(), idle_unload_seconds=0.0)
        await asyncio.to_thread(gen.load)
        model = fake.models[0]
        task = asyncio.create_task(gen.generate("hi"))
        await asyncio.to_thread(model.started.wait, 5)

        assert gen.seconds_idle == 0.0
        assert gen.unload_if_idle() is False
        result = await task

        assert result.startswith("t0 ")
        assert gen.is_loaded
        assert gen.unload_if_idle() is True

    async def test_explicit_unload_waits_for_the_running_call(
        self,
        fake: FakeTransformers,
    ) -> None:
        """``unload()`` mid-call is deferred to the moment the call ends."""
        gen = TextGenerator("m", hardware=_cpu())
        await asyncio.to_thread(gen.load)
        task = asyncio.create_task(gen.generate("hi"))
        await asyncio.to_thread(fake.models[0].started.wait, 5)

        gen.unload()
        assert gen.is_loaded

        assert (await task).startswith("t0 ")
        assert not gen.is_loaded

    async def test_registry_eviction_does_not_free_a_running_model(
        self,
        fake: FakeTransformers,
    ) -> None:
        """LRU eviction of a model mid-generation waits for the call."""
        registry = ModelRegistry(max_models=1)
        first = registry.get("a", lambda: TextGenerator("a", hardware=_cpu()))
        await asyncio.to_thread(first.load)
        task = asyncio.create_task(first.generate("hi"))
        await asyncio.to_thread(fake.models[0].started.wait, 5)

        registry.get("b", lambda: TextGenerator("b", hardware=_cpu()))

        assert first.is_loaded
        assert (await task).startswith("t0 ")
        assert not first.is_loaded


class TestStreamDoesNotBlockTheLoop:
    async def test_first_stream_keeps_the_loop_responsive(
        self,
        fake: FakeTransformers,
    ) -> None:
        """Load and token waits happen off the loop.

        The shipped ``stream`` called ``self.load()`` inside ``async def``
        (the whole build on the loop thread) and then iterated a blocking
        queue between tokens. A ticker that should fire every millisecond
        measures the longest stall.
        """
        gen = TextGenerator("m", hardware=_cpu())
        gaps: list[float] = []
        stop = asyncio.Event()

        async def _tick() -> None:
            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.001)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        ticker = asyncio.create_task(_tick())
        await asyncio.sleep(0.01)
        pieces = [piece async for piece in gen.stream("hi")]
        stop.set()
        await ticker

        assert pieces == [f"t{index} " for index in range(10)]
        assert max(gaps) < BUILD_SECONDS / 2

    async def test_closing_the_stream_stops_generation_promptly(
        self,
        long_fake: FakeTransformers,
    ) -> None:
        """A consumer that leaves early stops decoding within a token.

        The shipped ``finally: thread.join()`` waited for all
        ``max_new_tokens`` — the client was gone, the GPU kept going, and
        ``aclose()`` blocked the loop for the whole remainder.
        """
        gen = TextGenerator("m", hardware=_cpu())
        await asyncio.to_thread(gen.load)
        model = long_fake.models[0]
        agen = gen.stream("hi")
        received = [await agen.__anext__(), await agen.__anext__()]

        started = time.perf_counter()
        await agen.aclose()
        close_seconds = time.perf_counter() - started
        await asyncio.sleep(0.2)

        assert received == ["t0 ", "t1 "]
        assert close_seconds < 0.1
        assert model.emitted < 10
        assert gen._lifecycle.in_flight == 0

    async def test_producer_errors_reach_the_consumer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failure on the worker thread is raised from the iterator."""

        def _missing() -> tuple[Any, Any]:
            raise ImportError("no transformers here")

        monkeypatch.setattr(text_module, "_require_transformers", _missing)
        gen = TextGenerator("m", hardware=_cpu())

        with pytest.raises(ImportError, match="no transformers here"):
            async for _ in gen.stream("hi"):
                pass
