"""ModelRegistry keeps ``max_models`` for handles kept past an eviction.

The shipped registry only unloaded an evicted model; a caller that kept the
handle (``model = registry.get(...)`` at startup) reloaded the weights on
its next call through the loader's own lifecycle, outside the registry, so
two models ended up resident under ``max_models=1``. The fake loader here
runs the real :class:`ModelLifecycle`, and a shared tracker records how
many models are resident at once.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from tempest_fastapi_sdk.genai import ModelRegistry
from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle

WAIT_SECONDS: float = 5.0


class Tracker:
    """Counts loads and unloads across fake models and the peak resident.

    Attributes:
        loads (list[str]): Model names, in build order.
        unloads (list[str]): Model names, in release order.
        live (int): Models resident right now.
        peak (int): The highest ``live`` ever observed.
    """

    def __init__(self) -> None:
        """Initialize an empty tracker."""
        self._lock: threading.Lock = threading.Lock()
        self.loads: list[str] = []
        self.unloads: list[str] = []
        self.live: int = 0
        self.peak: int = 0

    def loaded(self, name: str) -> None:
        """Record a build.

        Args:
            name (str): The model that loaded.
        """
        with self._lock:
            self.loads.append(name)
            self.live += 1
            self.peak = max(self.peak, self.live)

    def released(self, name: str) -> None:
        """Record a release.

        Args:
            name (str): The model that unloaded.
        """
        with self._lock:
            self.unloads.append(name)
            self.live -= 1


class FakeLoader:
    """A loader shaped like the SDK's: ``_lifecycle`` + ``unload()``.

    Attributes:
        name (str): The model name reported to the tracker.
        is_loaded (bool): Whether the fake weights are resident.
    """

    def __init__(self, name: str, tracker: Tracker, build_seconds: float = 0.0) -> None:
        """Initialize the loader.

        Args:
            name (str): The model name.
            tracker (Tracker): Where loads and releases are recorded.
            build_seconds (float): How long a build takes.
        """
        self.name = name
        self.is_loaded = False
        self._tracker = tracker
        self._build_seconds = build_seconds
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self.is_loaded,
        )

    def _build(self) -> None:
        """Load the fake weights."""
        time.sleep(self._build_seconds)
        self.is_loaded = True
        self._tracker.loaded(self.name)

    def _release(self) -> None:
        """Drop the fake weights."""
        self.is_loaded = False
        self._tracker.released(self.name)

    def run(self, body: Callable[[], None] | None = None) -> str:
        """Run one call with the model resident.

        Args:
            body (Callable[[], None] | None): Work done inside the call.

        Returns:
            str: The model name.
        """
        with self._lifecycle.use():
            if body is not None:
                body()
            return self.name

    def unload(self) -> None:
        """Release the fake weights, deferred behind calls in flight."""
        self._lifecycle.unload()


def _factory(
    name: str, tracker: Tracker, build_seconds: float = 0.0
) -> Callable[[], FakeLoader]:
    """Return a zero-arg factory for :class:`FakeLoader`.

    Args:
        name (str): The model name.
        tracker (Tracker): The shared tracker.
        build_seconds (float): How long a build takes.

    Returns:
        Callable[[], FakeLoader]: The factory.
    """
    return lambda: FakeLoader(name, tracker, build_seconds)


def _run_threads(targets: list[Callable[[], object]]) -> None:
    """Run each target on its own thread and wait for all of them.

    Args:
        targets (list[Callable[[], object]]): The work, one per thread.
    """
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_SECONDS)
    assert not any(thread.is_alive() for thread in threads)


class TestKeptHandleAfterEviction:
    def test_kept_handle_never_leaves_two_models_loaded(self) -> None:
        """The acceptance case of #320: max_models=1, kept A, get(B), use A."""
        tracker = Tracker()
        registry = ModelRegistry(max_models=1)
        first = registry.get("a", _factory("a", tracker))
        first.run()

        registry.get("b", _factory("b", tracker)).run()
        first.run()

        assert tracker.peak == 1
        assert tracker.loads == ["a", "b", "a"]
        assert tracker.unloads == ["a", "b"]
        assert list(registry.items()) == ["a"]

    def test_kept_handle_re_registers_and_evicts_the_lru(self) -> None:
        """A readmitted model evicts the least-recently-used entry."""
        tracker = Tracker()
        registry = ModelRegistry(max_models=2)
        first = registry.get("a", _factory("a", tracker))
        first.run()
        registry.get("b", _factory("b", tracker)).run()
        registry.get("c", _factory("c", tracker)).run()

        first.run()

        assert tracker.peak == 2
        assert list(registry.items()) == ["c", "a"]
        assert tracker.unloads == ["a", "b"]

    def test_concurrent_calls_on_a_kept_handle_readmit_once(self) -> None:
        """Eight racing calls on a kept handle build it once and evict once."""
        tracker = Tracker()
        registry = ModelRegistry(max_models=1)
        first = registry.get("a", _factory("a", tracker, build_seconds=0.05))
        first.run()
        registry.get("b", _factory("b", tracker)).run()

        _run_threads([first.run for _ in range(8)])

        assert tracker.peak == 1
        assert tracker.loads == ["a", "b", "a"]
        assert tracker.unloads == ["a", "b"]

    def test_readmission_waits_for_the_evicted_model_to_drain(self) -> None:
        """A kept handle does not build while the model it evicts still runs."""
        tracker = Tracker()
        registry = ModelRegistry(max_models=1)
        first = registry.get("a", _factory("a", tracker))
        first.run()
        second = registry.get("b", _factory("b", tracker))
        running = threading.Event()
        release = threading.Event()

        def _hold() -> None:
            running.set()
            release.wait(WAIT_SECONDS)

        holder = threading.Thread(target=second.run, args=(_hold,))
        holder.start()
        assert running.wait(WAIT_SECONDS)
        user = threading.Thread(target=first.run)
        user.start()
        time.sleep(0.1)

        assert tracker.loads == ["a", "b"]
        assert second.is_loaded
        release.set()
        holder.join(WAIT_SECONDS)
        user.join(WAIT_SECONDS)

        assert tracker.peak == 1
        assert tracker.loads == ["a", "b", "a"]
        assert not second.is_loaded

    def test_new_model_waits_for_the_entry_it_pushed_out(self) -> None:
        """``get()`` evicting a busy model holds the new one's first build."""
        tracker = Tracker()
        registry = ModelRegistry(max_models=1)
        first = registry.get("a", _factory("a", tracker))
        running = threading.Event()
        release = threading.Event()

        def _hold() -> None:
            running.set()
            release.wait(WAIT_SECONDS)

        holder = threading.Thread(target=first.run, args=(_hold,))
        holder.start()
        assert running.wait(WAIT_SECONDS)
        second = registry.get("b", _factory("b", tracker))
        user = threading.Thread(target=second.run)
        user.start()
        time.sleep(0.1)

        assert tracker.loads == ["a"]
        release.set()
        holder.join(WAIT_SECONDS)
        user.join(WAIT_SECONDS)

        assert tracker.peak == 1
        assert tracker.loads == ["a", "b"]

    def test_alternating_kept_handles_hold_the_ceiling(self) -> None:
        """Threads hammering two kept handles never have both resident."""
        tracker = Tracker()
        registry = ModelRegistry(max_models=1)
        first = registry.get("a", _factory("a", tracker))
        second = registry.get("b", _factory("b", tracker))

        def _loop(model: FakeLoader) -> Callable[[], None]:
            def _work() -> None:
                for _ in range(30):
                    model.run(lambda: time.sleep(0.001))

            return _work

        _run_threads([_loop(first), _loop(second), _loop(first), _loop(second)])

        assert tracker.peak == 1
        assert len(registry) == 1

    def test_explicit_evict_is_readmitted_on_use(self) -> None:
        """``evict(key)`` then a call on the kept handle registers it again."""
        tracker = Tracker()
        registry = ModelRegistry(max_models=1)
        first = registry.get("a", _factory("a", tracker))
        first.run()

        assert registry.evict("a") is True
        assert "a" not in registry
        first.run()

        assert "a" in registry
        assert tracker.loads == ["a", "a"]

    def test_kept_handle_replaces_a_rebuilt_entry_under_its_key(self) -> None:
        """A newer object under the same key is evicted, not doubled up."""
        tracker = Tracker()
        registry = ModelRegistry(max_models=2)
        first = registry.get("a", _factory("a", tracker))
        first.run()
        registry.evict("a")
        rebuilt = registry.get("a", _factory("a2", tracker))
        rebuilt.run()

        first.run()

        assert registry.items() == {"a": first}
        assert tracker.peak == 1
        assert not rebuilt.is_loaded

    def test_nested_readmission_does_not_deadlock(self) -> None:
        """A kept handle used inside another model's call skips the wait.

        The nested call cannot wait for the outer model to drain — it is
        part of what the outer call is waiting for — so the evicted outer
        model stays resident until its call returns, then is released.
        """
        tracker = Tracker()
        registry = ModelRegistry(max_models=1)
        first = registry.get("a", _factory("a", tracker))
        first.run()
        second = registry.get("b", _factory("b", tracker))
        results: list[str] = []

        def _outer() -> None:
            results.append(second.run(lambda: results.append(first.run())))

        _run_threads([_outer])

        assert results == ["a", "b"]
        assert not second.is_loaded
        assert list(registry.items()) == ["a"]
        assert tracker.live == 1
