"""The shared model lifecycle, and every loader that runs on it.

Two defects shipped in every self-hosted loader at once, and each class
below is exercised for both:

* **Cold-start stampede** — ``load`` ran on the worker thread with a bare
  ``if self.is_loaded: return``, so concurrent first calls each built the
  model.
* **Unload under a running call** — the idle clock only moved when a call
  returned, so ``unload_if_idle`` freed a model that was mid-call.

``_build`` is replaced at class level by a slow, counting stand-in (the
lifecycle captures the bound method at construction), and the per-call
work blocks on a :class:`Gate` so the test decides when it finishes.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from tempest_fastapi_sdk.genai import (
    ClassifierModerator,
    Embedder,
    HardwareInfo,
    ImageGenerator,
    ModelRegistry,
    OnnxEmbedder,
    VisionTextGenerator,
)
from tempest_fastapi_sdk.genai import image as image_module
from tempest_fastapi_sdk.genai._lifecycle import ModelLifecycle
from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer, SpeechToText, TextToSpeech
from tempest_fastapi_sdk.genai.audio import diarization as diarization_module
from tempest_fastapi_sdk.genai.moderation import ModerationResult
from tempest_fastapi_sdk.genai.rag import Chunk, Reranker

BUILD_SECONDS: float = 0.1


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


class Gate:
    """Lets a test hold a call in flight and release it on demand.

    Attributes:
        entered (threading.Event): Set once the call reaches the model.
        release (threading.Event): The call returns once this is set.
    """

    def __init__(self, *, open_: bool = False) -> None:
        """Initialize the gate.

        Args:
            open_ (bool): Start released, for tests that only count builds.
        """
        self.entered = threading.Event()
        self.release = threading.Event()
        if open_:
            self.release.set()

    def pass_through(self) -> None:
        """Mark the call as inside the model and wait to be released."""
        self.entered.set()
        self.release.wait(5)


class BuildCounter:
    """Counts builds across threads."""

    def __init__(self) -> None:
        """Initialize the counter."""
        self.calls = 0
        self._lock = threading.Lock()

    def hit(self) -> None:
        """Record one build, slowly, so racing callers overlap."""
        with self._lock:
            self.calls += 1
        time.sleep(BUILD_SECONDS)


class FakeBatch(dict[str, Any]):
    """A processor output with ``.to(device)`` and an ``input_ids`` shape."""

    def to(self, device: Any) -> FakeBatch:
        """Return itself.

        Args:
            device (Any): Ignored.

        Returns:
            FakeBatch: This batch.
        """
        return self


class FakeIds:
    """Only ``shape`` is read."""

    shape: tuple[int, int] = (1, 1)


class FakeVisionProcessor:
    """Processor stand-in for :class:`VisionTextGenerator`."""

    def __init__(self, gate: Gate) -> None:
        """Initialize the processor.

        Args:
            gate (Gate): Held while decoding.
        """
        self.gate = gate

    def __call__(self, **kwargs: Any) -> FakeBatch:
        """Encode inputs.

        Args:
            **kwargs (Any): Ignored.

        Returns:
            FakeBatch: A batch.
        """
        return FakeBatch(input_ids=FakeIds())

    def decode(self, tokens: Any, **kwargs: Any) -> str:
        """Decode, after the gate opens.

        Args:
            tokens (Any): Ignored.
            **kwargs (Any): Ignored.

        Returns:
            str: A fixed completion.
        """
        self.gate.pass_through()
        return "ok"


class FakeVisionModel:
    """Model stand-in for :class:`VisionTextGenerator`."""

    device: str = "cpu"

    def generate(self, **kwargs: Any) -> list[list[str]]:
        """Return one sequence.

        Args:
            **kwargs (Any): Ignored.

        Returns:
            list[list[str]]: A prompt slot and one token.
        """
        return [["<prompt>", "ok"]]


class FakeWhisper:
    """``WhisperModel`` stand-in whose segments are lazy."""

    def __init__(self, gate: Gate) -> None:
        """Initialize the model.

        Args:
            gate (Gate): Held while the segments are consumed.
        """
        self.gate = gate

    def transcribe(self, source: Any, **options: Any) -> tuple[Iterator[Any], Any]:
        """Return a lazy segment generator and info.

        Args:
            source (Any): Ignored.
            **options (Any): Ignored.

        Returns:
            tuple[Iterator[Any], Any]: Segments and info.
        """

        def _segments() -> Iterator[Any]:
            self.gate.pass_through()
            yield type("Seg", (), {"start": 0.0, "end": 1.0, "text": "ok"})()

        info = type("Info", (), {"duration": 1.0, "language": "en"})()
        return _segments(), info


class FakeVoice:
    """Coqui ``TTS`` stand-in."""

    def __init__(self, gate: Gate) -> None:
        """Initialize the voice.

        Args:
            gate (Gate): Held while synthesizing.
        """
        self.gate = gate

    def tts_to_file(self, *, file_path: str, **kwargs: Any) -> None:
        """Write a fake WAV after the gate opens.

        Args:
            file_path (str): Where to write.
            **kwargs (Any): Ignored.
        """
        self.gate.pass_through()
        with open(file_path, "wb") as handle:
            handle.write(b"RIFF")


class FakeDiarizationResult(list[Any]):
    """``process()`` output with ``sort_by_start_time``."""

    def sort_by_start_time(self) -> FakeDiarizationResult:
        """Return itself.

        Returns:
            FakeDiarizationResult: This result.
        """
        return self


class FakeEngine:
    """sherpa-onnx ``OfflineSpeakerDiarization`` stand-in."""

    def __init__(self, gate: Gate) -> None:
        """Initialize the engine.

        Args:
            gate (Gate): Held while processing.
        """
        self.gate = gate

    def set_config(self, config: Any) -> None:
        """Accept a config.

        Args:
            config (Any): Ignored.
        """

    def process(self, samples: Any) -> FakeDiarizationResult:
        """Return one turn after the gate opens.

        Args:
            samples (Any): Ignored.

        Returns:
            FakeDiarizationResult: One segment.
        """
        self.gate.pass_through()
        segment = type("Segment", (), {"start": 0.0, "end": 1.0, "speaker": 0})()
        return FakeDiarizationResult([segment])


class FakeOnnxInput:
    """An ONNX Runtime input descriptor."""

    def __init__(self, name: str) -> None:
        """Initialize the descriptor.

        Args:
            name (str): Input name.
        """
        self.name = name


class FakeOnnxSession:
    """``InferenceSession`` stand-in."""

    def __init__(self, gate: Gate) -> None:
        """Initialize the session.

        Args:
            gate (Gate): Held while running.
        """
        self.gate = gate

    def get_inputs(self) -> list[FakeOnnxInput]:
        """Return the inputs.

        Returns:
            list[FakeOnnxInput]: ``input_ids`` + ``attention_mask``.
        """
        return [FakeOnnxInput("input_ids"), FakeOnnxInput("attention_mask")]

    def run(self, names: Any, feeds: dict[str, Any]) -> list[Any]:
        """Return token embeddings after the gate opens.

        Args:
            names (Any): Ignored.
            feeds (dict[str, Any]): The inputs.

        Returns:
            list[Any]: One ``(batch, tokens, 2)`` array.
        """
        import numpy as np

        self.gate.pass_through()
        batch, tokens = feeds["input_ids"].shape
        return [np.ones((batch, tokens, 2), dtype=np.float32)]


class FakeEncoding:
    """A ``tokenizers`` encoding."""

    def __init__(self) -> None:
        """Encode two real tokens."""
        self.ids: list[int] = [5, 6]
        self.attention_mask: list[int] = [1, 1]


class FakeOnnxTokenizer:
    """``tokenizers.Tokenizer`` stand-in."""

    def encode_batch(self, batch: list[str]) -> list[FakeEncoding]:
        """Encode a batch.

        Args:
            batch (list[str]): Texts.

        Returns:
            list[FakeEncoding]: One encoding per text.
        """
        return [FakeEncoding() for _ in batch]


class FakeImageResult:
    """A diffusers pipeline result."""

    def __init__(self) -> None:
        """Hold no images; encoding is not what is under test."""
        self.images: list[Any] = []


class FakeImagePipeline:
    """A diffusers pipeline stand-in."""

    def __init__(self, gate: Gate) -> None:
        """Initialize the pipeline.

        Args:
            gate (Gate): Held while rendering.
        """
        self.gate = gate

    def __call__(self, **kwargs: Any) -> FakeImageResult:
        """Render after the gate opens.

        Args:
            **kwargs (Any): Ignored.

        Returns:
            FakeImageResult: No images.
        """
        self.gate.pass_through()
        return FakeImageResult()


class FakeGenerator:
    """A ``torch.Generator`` stand-in."""

    def manual_seed(self, seed: int) -> FakeGenerator:
        """Accept a seed.

        Args:
            seed (int): Ignored.

        Returns:
            FakeGenerator: Itself.
        """
        return self


class FakeTorch:
    """Only ``Generator`` is used by the image path."""

    @staticmethod
    def Generator(device: str) -> FakeGenerator:  # noqa: N802
        """Build a generator.

        Args:
            device (str): Ignored.

        Returns:
            FakeGenerator: The generator.
        """
        return FakeGenerator()


@dataclass
class Case:
    """One loader wired to fakes.

    Attributes:
        name (str): Test id.
        make (Callable[[pytest.MonkeyPatch, Gate, BuildCounter], Any]):
            Patches the class and returns a configured instance.
        call (Callable[[Any], Awaitable[Any]]): One public async call.
    """

    name: str
    make: Callable[[pytest.MonkeyPatch, Gate, BuildCounter], Any]
    call: Callable[[Any], Awaitable[Any]]


def _patch_build(
    monkeypatch: pytest.MonkeyPatch,
    cls: type[Any],
    counter: BuildCounter,
    assign: Callable[[Any], None],
) -> None:
    """Replace ``cls._build`` with a slow, counting stand-in.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher.
        cls (type[Any]): The loader class.
        counter (BuildCounter): Records each build.
        assign (Callable[[Any], None]): Sets the loaded state on ``self``.
    """

    def _build(self: Any) -> None:
        counter.hit()
        assign(self)

    monkeypatch.setattr(cls, "_build", _build)


def _embedder(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build an :class:`Embedder` over fakes."""

    def _assign(self: Any) -> None:
        self._model = object()
        self._tokenizer = object()

    def _batches(self: Any, texts: list[str], batch_size: int) -> list[list[float]]:
        gate.pass_through()
        return [[1.0] for _ in texts]

    _patch_build(mp, Embedder, counter, _assign)
    mp.setattr(Embedder, "_embed_batches", _batches)
    return Embedder("m", hardware=_cpu(), idle_unload_seconds=0.0)


def _reranker(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build a :class:`Reranker` over fakes."""

    def _assign(self: Any) -> None:
        self._model = object()
        self._tokenizer = object()

    def _score(self: Any, query: str, chunks: list[Chunk]) -> list[float]:
        gate.pass_through()
        return [1.0 for _ in chunks]

    _patch_build(mp, Reranker, counter, _assign)
    mp.setattr(Reranker, "_score_pairs", _score)
    return Reranker("m", hardware=_cpu(), idle_unload_seconds=0.0)


def _moderator(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build a :class:`ClassifierModerator` over fakes."""

    def _assign(self: Any) -> None:
        self._model = object()
        self._tokenizer = object()

    def _classify(self: Any, text: str) -> ModerationResult:
        gate.pass_through()
        return ModerationResult(flagged=False)

    _patch_build(mp, ClassifierModerator, counter, _assign)
    mp.setattr(ClassifierModerator, "_classify", _classify)
    return ClassifierModerator("m", hardware=_cpu(), idle_unload_seconds=0.0)


def _vision(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build a :class:`VisionTextGenerator` over fakes."""

    def _assign(self: Any) -> None:
        self._processor = FakeVisionProcessor(gate)
        self._model = FakeVisionModel()

    _patch_build(mp, VisionTextGenerator, counter, _assign)
    return VisionTextGenerator("m", hardware=_cpu(), idle_unload_seconds=0.0)


def _stt(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build a :class:`SpeechToText` over fakes."""

    def _assign(self: Any) -> None:
        self._model = FakeWhisper(gate)

    _patch_build(mp, SpeechToText, counter, _assign)
    return SpeechToText(
        "base",
        device="cpu",
        compute_type="int8",
        max_concurrent=3,
        idle_unload_seconds=0.0,
    )


def _tts(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build a :class:`TextToSpeech` over fakes."""

    def _assign(self: Any) -> None:
        self._tts = FakeVoice(gate)

    _patch_build(mp, TextToSpeech, counter, _assign)
    return TextToSpeech(device="cpu", max_concurrent=3, idle_unload_seconds=0.0)


def _diarizer(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build a :class:`SpeakerDiarizer` over fakes."""

    def _assign(self: Any) -> None:
        self._engine = FakeEngine(gate)

    _patch_build(mp, SpeakerDiarizer, counter, _assign)
    mp.setattr(SpeakerDiarizer, "_make_config", lambda self, count: None)
    mp.setattr(diarization_module, "load_audio", lambda audio, target_rate: [])
    return SpeakerDiarizer(
        num_speakers=2,
        max_concurrent=3,
        idle_unload_seconds=0.0,
    )


def _onnx(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build an :class:`OnnxEmbedder` over fakes."""

    def _assign(self: Any) -> None:
        self._tokenizer = FakeOnnxTokenizer()
        self._session = FakeOnnxSession(gate)

    _patch_build(mp, OnnxEmbedder, counter, _assign)
    return OnnxEmbedder("m.onnx", tokenizer="t.json", idle_unload_seconds=0.0)


def _image(mp: pytest.MonkeyPatch, gate: Gate, counter: BuildCounter) -> Any:
    """Build an :class:`ImageGenerator` over fakes."""

    def _assign(self: Any) -> None:
        self._pipeline = FakeImagePipeline(gate)

    _patch_build(mp, ImageGenerator, counter, _assign)
    mp.setattr(image_module, "_require_diffusers", lambda: (FakeTorch(), None))
    return ImageGenerator(
        "m",
        device="cpu",
        max_concurrent=3,
        idle_unload_seconds=0.0,
    )


CASES: list[Case] = [
    Case("embedder", _embedder, lambda model: model.embed(["a"])),
    Case(
        "reranker",
        _reranker,
        lambda model: model.rerank("q", [Chunk(text="t", source="s", index=0)]),
    ),
    Case("moderator", _moderator, lambda model: model.check("text")),
    Case("vision", _vision, lambda model: model.generate("describe")),
    Case("stt", _stt, lambda model: model.transcribe(b"audio")),
    Case("tts", _tts, lambda model: model.synthesize("hello")),
    Case("diarizer", _diarizer, lambda model: model.diarize(b"audio")),
    Case("onnx", _onnx, lambda model: model.embed(["a"])),
    Case("image", _image, lambda model: model.generate("a cat")),
]


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
class TestEveryLoader:
    async def test_concurrent_first_calls_build_once(
        self,
        case: Case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Three cold calls together build the model exactly once."""
        counter = BuildCounter()
        model = case.make(monkeypatch, Gate(open_=True), counter)

        await asyncio.gather(*(case.call(model) for _ in range(3)))

        assert counter.calls == 1
        assert model.is_loaded

    async def test_idle_unload_refuses_during_a_call(
        self,
        case: Case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A call in flight keeps the model resident and the clock at zero."""
        gate = Gate()
        model = case.make(monkeypatch, gate, BuildCounter())
        await asyncio.to_thread(model.load)
        task = asyncio.ensure_future(case.call(model))
        assert await asyncio.to_thread(gate.entered.wait, 5)

        assert model.seconds_idle == 0.0
        assert model.unload_if_idle() is False
        assert model.is_loaded
        gate.release.set()
        await task

        assert model.unload_if_idle() is True
        assert not model.is_loaded

    async def test_explicit_unload_is_deferred_to_the_end_of_the_call(
        self,
        case: Case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``unload()`` mid-call frees the model once the call returns."""
        gate = Gate()
        model = case.make(monkeypatch, gate, BuildCounter())
        await asyncio.to_thread(model.load)
        task = asyncio.ensure_future(case.call(model))
        assert await asyncio.to_thread(gate.entered.wait, 5)

        model.unload()
        assert model.is_loaded
        gate.release.set()
        await task

        assert not model.is_loaded


class TestModelLifecycle:
    def _make(self) -> tuple[ModelLifecycle, dict[str, Any]]:
        """Build a lifecycle over a dict slot.

        Returns:
            tuple[ModelLifecycle, dict[str, Any]]: The lifecycle and its
            state, whose ``builds`` / ``releases`` count the callbacks.
        """
        state: dict[str, Any] = {"model": None, "builds": 0, "releases": 0}

        def _build() -> None:
            state["builds"] += 1
            state["model"] = object()

        def _release() -> None:
            state["releases"] += 1
            state["model"] = None

        lifecycle = ModelLifecycle(
            build=_build,
            release=_release,
            is_loaded=lambda: state["model"] is not None,
        )
        return lifecycle, state

    def test_unload_of_a_cold_model_releases_nothing(self) -> None:
        lifecycle, state = self._make()
        assert lifecycle.unload() is False
        assert state["releases"] == 0

    def test_no_threshold_never_unloads(self) -> None:
        lifecycle, _ = self._make()
        lifecycle.load()
        assert lifecycle.unload_if_idle(None) is False

    def test_nested_use_counts_twice_and_releases_once(self) -> None:
        lifecycle, state = self._make()
        with lifecycle.use():
            with lifecycle.use():
                assert lifecycle.in_flight == 2
                assert lifecycle.unload() is False
            assert state["model"] is not None
            assert lifecycle.unload_pending
        assert lifecycle.in_flight == 0
        assert state["model"] is None
        assert state["releases"] == 1
        assert not lifecycle.unload_pending

    def test_failed_build_leaves_nothing_in_flight(self) -> None:
        def _boom() -> None:
            raise ImportError("extra missing")

        lifecycle = ModelLifecycle(
            build=_boom,
            release=lambda: None,
            is_loaded=lambda: False,
        )
        with pytest.raises(ImportError, match="extra missing"):
            lifecycle.load()
        assert lifecycle.in_flight == 0

    def test_idle_clock_resumes_after_the_call(self) -> None:
        lifecycle, _ = self._make()
        lifecycle.load()
        lifecycle.last_used = time.monotonic() - 10
        assert lifecycle.seconds_idle() >= 10
        lifecycle.touch()
        assert lifecycle.seconds_idle() < 1


class TestRegistryEviction:
    async def test_lru_eviction_waits_for_the_running_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Evicting a model mid-call defers its unload to the call's end."""
        gate = Gate()
        counter = BuildCounter()
        first = _embedder(monkeypatch, gate, counter)
        registry = ModelRegistry(max_models=1)
        registry.get("first", lambda: first)
        await asyncio.to_thread(first.load)
        task = asyncio.ensure_future(first.embed(["a"]))
        assert await asyncio.to_thread(gate.entered.wait, 5)

        registry.get("second", lambda: Embedder("n", hardware=_cpu()))

        assert first.is_loaded
        gate.release.set()
        assert await task == [[1.0]]
        assert not first.is_loaded

    async def test_unload_idle_frees_an_onnx_embedder(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``OnnxEmbedder`` now has an idle clock the registry can act on."""
        embedder = _onnx(monkeypatch, Gate(open_=True), BuildCounter())
        registry = ModelRegistry()
        registry.get("onnx", lambda: embedder)
        await embedder.embed(["a"])

        assert registry.unload_idle() == ["onnx"]
        assert not embedder.is_loaded
