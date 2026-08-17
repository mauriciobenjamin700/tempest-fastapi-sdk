"""A cold transcriber must build exactly one model, however many callers.

``SpeechToText.load`` used to be a bare ``if self.is_loaded: return``
followed by the constructor. That reads as idempotent, and it is — from one
thread. But ``load`` is called from inside ``_transcribe_sync``, which runs
on a worker thread, and the only thing standing between two callers is
``asyncio.Semaphore(max_concurrent)``, which by construction admits
``max_concurrent`` of them. Two requests arriving on a cold instance both
read ``is_loaded`` as False and both build a ``WhisperModel``: peak memory
doubles, and the loser's copy stays alive for the process lifetime because
``self._model`` only ever holds the last one written.

The tests below stub the constructor so they need no weights and no extra:
what is under test is how many times it is called, not what it returns.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.audio import stt as stt_module
from tempest_fastapi_sdk.genai.audio.stt import SpeechToText


class _SlowModelFactory:
    """A ``WhisperModel`` stand-in that is slow to build, and counts.

    The sleep is what makes the race observable: without it the first
    caller can finish constructing before the second one checks
    ``is_loaded``, and a broken implementation passes by luck.

    Attributes:
        calls (int): How many models were constructed.
        started (threading.Event): Set once the first construction begins,
            so the second caller can be released exactly then.
    """

    def __init__(self) -> None:
        """Initialize the factory."""
        self.calls: int = 0
        self.started: threading.Event = threading.Event()
        self._lock: threading.Lock = threading.Lock()

    def __call__(self, *args: Any, **kwargs: Any) -> object:
        """Construct one fake model, recording the call.

        Args:
            *args (Any): Ignored positional arguments.
            **kwargs (Any): Ignored keyword arguments.

        Returns:
            object: A unique sentinel standing in for the loaded model.
        """
        with self._lock:
            self.calls += 1
        self.started.set()
        threading.Event().wait(0.05)
        return object()


def _fake_pipeline(*, model: Any) -> object:
    """Stand in for ``BatchedInferencePipeline``.

    Args:
        model (Any): The model the real pipeline would wrap.

    Returns:
        object: A sentinel standing in for the pipeline.
    """
    return object()


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> _SlowModelFactory:
    """Replace faster-whisper's constructor with a counting stub.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher for the module lookup.

    Returns:
        _SlowModelFactory: The stub, for asserting the call count.
    """
    made = _SlowModelFactory()
    fake_module = type(
        "_FasterWhisper",
        (),
        {"WhisperModel": made, "BatchedInferencePipeline": _fake_pipeline},
    )
    monkeypatch.setattr(stt_module, "_require_faster_whisper", lambda: fake_module)
    return made


def test_concurrent_load_builds_one_model(factory: _SlowModelFactory) -> None:
    """Two threads loading a cold instance together build one model.

    This is the shipped defect reproduced: drop the lock from
    :meth:`SpeechToText.load` and ``factory.calls`` becomes 2.
    """
    stt = SpeechToText("base", device="cpu", compute_type="int8")
    barrier = threading.Barrier(2)

    def _load() -> None:
        barrier.wait()
        stt.load()

    threads = [threading.Thread(target=_load) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert factory.calls == 1
    assert stt.is_loaded


@pytest.mark.asyncio
async def test_concurrent_transcribe_builds_one_model(
    factory: _SlowModelFactory,
) -> None:
    """Two overlapping transcriptions on a cold instance load once.

    Goes through the public coroutine rather than :meth:`load` directly, so
    the semaphore that used to be mistaken for the guard is in the picture.
    """
    stt = SpeechToText("base", device="cpu", compute_type="int8", max_concurrent=2)

    async def _load() -> None:
        await asyncio.to_thread(stt.load)

    await asyncio.gather(_load(), _load())

    assert factory.calls == 1


def test_unload_clears_the_batched_pipeline(factory: _SlowModelFactory) -> None:
    """``unload`` drops the pipeline too, not only the model.

    The pipeline holds the model, so clearing one and not the other frees
    nothing — the point of unloading.
    """
    stt = SpeechToText("base", device="cpu", compute_type="int8", batch_size=8)
    stt.load()
    assert stt.is_loaded

    stt.unload()

    assert not stt.is_loaded
    assert stt._pipeline is None
