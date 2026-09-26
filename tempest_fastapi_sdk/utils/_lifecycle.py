"""Load-once, in-flight-aware lifecycle shared by every self-hosted loader.

Every loader in :mod:`tempest_fastapi_sdk.genai`, and
:class:`~tempest_fastapi_sdk.faces.FaceRecognizer`, holds one lazily-built
model and offers ``load`` / ``unload`` / ``unload_if_idle``. The helper
lives under ``utils`` rather than ``genai`` so ``faces`` can use it without
importing the whole ``genai`` package. Each used to
implement those three with a bare ``if self.is_loaded: return`` and an idle
clock touched only after a call finished, which shipped two defects in
every class at once:

* **Cold-start stampede.** ``load`` runs inside the worker thread, so
  three requests arriving on a cold instance all read ``is_loaded`` as
  ``False`` and all call ``from_pretrained``. Measured on
  ``TextGenerator``: three concurrent ``generate`` calls built the model
  three times, and some of them failed with ``NotImplementedError: Cannot
  copy out of meta tensor`` depending on how the loads interleaved.
* **Unload under a running call.** The idle clock only moved when a call
  *returned*, so a generation longer than ``idle_unload_seconds`` looked
  idle while it ran, and ``unload_if_idle`` freed the weights out from
  under it. The call then died on ``'NoneType' object has no attribute
  'decode'``.

:class:`ModelLifecycle` owns the answer in one place. A load lock makes
the build happen once however many threads race for it; a counter of calls
in flight makes an idle unload refuse and an explicit unload wait until
the last call leaves. The counter is guarded by a short lock that is never
held while a model builds, so asking "can I unload?" from the event loop
never waits for a download.

A :class:`~tempest_fastapi_sdk.genai.ModelRegistry` that evicts a model also
marks it evicted here (:meth:`ModelLifecycle.mark_evicted`). The next call
on a handle kept past the eviction then goes back through the registry
before building — re-registering the model and evicting another — instead
of reloading behind it, so ``max_models`` keeps counting it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

_THREAD_CALLS: threading.local = threading.local()


def _thread_calls() -> int:
    """Return how many lifecycle calls the current thread has in flight.

    Returns:
        int: Calls entered and not yet left on this thread, across every
        :class:`ModelLifecycle`.
    """
    count: int = getattr(_THREAD_CALLS, "count", 0)
    return count


def _add_thread_calls(delta: int) -> None:
    """Shift the current thread's in-flight call count by ``delta``.

    Args:
        delta (int): ``1`` on entering a call, ``-1`` on leaving it.
    """
    _THREAD_CALLS.count = _thread_calls() + delta


class ModelLifecycle:
    """Serialize a model's build and track the calls using it.

    The owner keeps its public ``load`` / ``unload`` / ``unload_if_idle``
    surface and delegates here, so the rendered API reference still
    documents each loader's own methods.

    Attributes:
        last_used (float): ``time.monotonic()`` of the last time a call
            started or finished.
    """

    def __init__(
        self,
        *,
        build: Callable[[], None],
        release: Callable[[], None],
        is_loaded: Callable[[], bool],
    ) -> None:
        """Wire the lifecycle to its owner.

        Args:
            build (Callable[[], None]): Loads the model into the owner.
                Called at most once per unload cycle, under the load lock.
            release (Callable[[], None]): Drops the owner's references to
                the model. Called with no call in flight.
            is_loaded (Callable[[], bool]): Reports whether the owner
                currently holds a model.
        """
        self._build = build
        self._release = release
        self._is_loaded = is_loaded
        self._load_lock: threading.RLock = threading.RLock()
        self._state_lock: threading.Lock = threading.Lock()
        self._in_flight: int = 0
        self._unload_pending: bool = False
        self._released: threading.Condition = threading.Condition(self._state_lock)
        self._readmit: Callable[[], None] | None = None
        self._blockers: list[ModelLifecycle] = []
        self.last_used: float = time.monotonic()

    @property
    def in_flight(self) -> int:
        """Return how many calls (loads included) are using the model now.

        Returns:
            int: The number of calls between entering and leaving
            :meth:`use`.
        """
        with self._state_lock:
            return self._in_flight

    @property
    def load_lock(self) -> threading.RLock:
        """Return the re-entrant lock the build runs under.

        For owners that build a second, derived object lazily (the
        image-to-image pipeline built from the loaded text-to-image one)
        and need the same once-only guarantee for it.

        Returns:
            threading.RLock: The load lock.
        """
        return self._load_lock

    @property
    def unload_pending(self) -> bool:
        """Return whether an explicit unload is waiting for calls to drain.

        Returns:
            bool: ``True`` between an :meth:`unload` that found calls in
            flight and the moment the last of them leaves.
        """
        with self._state_lock:
            return self._unload_pending

    @property
    def evicted(self) -> bool:
        """Return whether a registry evicted the model and has not taken it back.

        Returns:
            bool: ``True`` between :meth:`mark_evicted` and
            :meth:`clear_eviction`.
        """
        with self._state_lock:
            return self._readmit is not None

    def mark_evicted(self, readmit: Callable[[], None]) -> None:
        """Route the next build of this model through its registry.

        Called by the registry, under its own lock, right before it calls
        the owner's ``unload()``. A call arriving afterwards on a handle
        kept past the eviction finds nothing in flight to join, so instead
        of building it runs ``readmit`` first: the registry takes the model
        back (calling :meth:`clear_eviction`), evicts whatever now falls
        over capacity and hands the evicted models still draining to
        :meth:`wait_for`. A call arriving while earlier calls are still in
        flight joins them — the weights are still resident, so joining
        costs no memory the ceiling has not already counted.

        Args:
            readmit (Callable[[], None]): Re-registers the model with the
                registry that evicted it.
        """
        with self._state_lock:
            self._readmit = readmit

    def wait_for(self, blockers: Iterable[ModelLifecycle]) -> None:
        """Hold this model's next build until ``blockers`` have released.

        The registry calls this for a model it just admitted, with the
        models it evicted to make room whose unload is deferred behind
        calls in flight. Every call that would start this model from idle
        waits for them first, so the two are never resident together.

        Args:
            blockers (Iterable[ModelLifecycle]): Evicted models still
                draining.
        """
        pending = [blocker for blocker in blockers if blocker is not self]
        with self._state_lock:
            self._blockers.extend(pending)

    def clear_eviction(self) -> None:
        """Forget an eviction mark, once the registry holds the model again."""
        with self._state_lock:
            self._readmit = None

    def wait_released(self) -> None:
        """Block until a deferred unload has run.

        Returns at once when no unload is pending. Used by a model that
        was admitted in place of this one, to wait for it to leave memory
        before building.
        """
        with self._released:
            while self._unload_pending:
                self._released.wait()

    def seconds_idle(self) -> float:
        """Return the seconds since the model was last in use.

        A model with a call in flight is not idle at all, so this answers
        ``0.0`` then, however long ago the call started.

        Returns:
            float: Idle time in seconds.
        """
        with self._state_lock:
            if self._in_flight > 0:
                return 0.0
            return time.monotonic() - self.last_used

    def touch(self) -> None:
        """Reset the idle clock without registering a call."""
        with self._state_lock:
            self.last_used = time.monotonic()

    def _admit(self) -> None:
        """Register one call, going through the registry when it must.

        Three cases, checked again after every wait because an eviction
        may land meanwhile:

        * **Calls already in flight** — join them. The model is resident
          (or being built by the first of them), even if it was evicted
          and its release is pending.
        * **Evicted, nothing in flight** — the weights were released (or
          never loaded), so building now would load behind the registry.
          Run the readmission instead, which re-registers the model and
          evicts another.
        * **Admitted in place of models still draining** — wait for each
          of them to release before building.

        The wait is skipped when this thread already has a call in flight
        on some model: that call could be what a blocker's drain is waiting
        for, and two such threads would each wait on the other. In that
        nested case the ceiling can be exceeded until the blockers' calls
        finish.
        """
        while True:
            with self._state_lock:
                readmit = self._readmit
                blockers = list(self._blockers)
                if self._in_flight > 0 or (
                    readmit is None and (not blockers or _thread_calls() > 0)
                ):
                    self._in_flight += 1
                    self.last_used = time.monotonic()
                    break
            if readmit is not None:
                readmit()
                continue
            for blocker in blockers:
                blocker.wait_released()
            with self._state_lock:
                self._blockers = [
                    blocker for blocker in self._blockers if blocker not in blockers
                ]
        _add_thread_calls(1)

    def _leave(self) -> None:
        """Unregister one call and run an unload that was waiting for it.

        The deferred release runs on the thread of the last call to leave,
        under the state lock, so no new call can slip in between the count
        reaching zero and the model being dropped.
        """
        _add_thread_calls(-1)
        with self._state_lock:
            self._in_flight -= 1
            self.last_used = time.monotonic()
            if self._in_flight == 0 and self._unload_pending:
                self._unload_pending = False
                try:
                    if self._is_loaded():
                        self._release()
                finally:
                    self._released.notify_all()

    def _ensure_loaded(self) -> None:
        """Build the model unless it is already resident.

        The ``is_loaded`` test sits inside the lock, not before it as a
        fast path: an uncontended acquire costs nanoseconds against a call
        measured in milliseconds, and double-checked locking is the shape
        the original race hid in.
        """
        with self._load_lock:
            if not self._is_loaded():
                self._build()

    @contextmanager
    def use(self) -> Iterator[None]:
        """Hold the model resident for the duration of one call.

        Registers the call, loads the model if needed, and on exit
        unregisters it — running a deferred unload if one was requested
        meanwhile. Nesting is fine: a call that delegates to another call
        of the same owner counts twice and leaves twice. A model its
        registry evicted is readmitted through the registry before it
        builds (see :meth:`mark_evicted`).

        Yields:
            None: Control, with the model loaded.
        """
        self._admit()
        try:
            self._ensure_loaded()
            yield
        finally:
            self._leave()

    def load(self) -> None:
        """Load the model once, counting the load as a call in flight.

        Counting the load is what stops a concurrent unload from freeing a
        model while its build is still running.
        """
        with self.use():
            pass

    def unload(self) -> bool:
        """Release the model now, or as soon as the calls using it finish.

        Returns:
            bool: ``True`` when the model was released by this call,
            ``False`` when it was not loaded or the release was deferred
            until the calls in flight drain.
        """
        with self._state_lock:
            if self._in_flight > 0:
                self._unload_pending = True
                return False
            if not self._is_loaded():
                return False
            self._release()
            return True

    def unload_if_idle(self, threshold: float | None) -> bool:
        """Release the model when nothing uses it and it idled past ``threshold``.

        Never defers: a model with a call in flight is not idle, so the
        answer is simply ``False``.

        Args:
            threshold (float | None): Idle seconds required; ``None``
                disables idle unloading.

        Returns:
            bool: ``True`` when this call released the model.
        """
        if threshold is None:
            return False
        with self._state_lock:
            if self._in_flight > 0 or not self._is_loaded():
                return False
            if time.monotonic() - self.last_used < threshold:
                return False
            self._release()
            return True


__all__: list[str] = ["ModelLifecycle"]
