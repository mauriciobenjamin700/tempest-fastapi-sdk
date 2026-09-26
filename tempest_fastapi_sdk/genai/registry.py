"""Share loaded models across call sites, with LRU eviction.

Loading a model is expensive and its weights eat VRAM. `ModelRegistry`
keeps loaded models keyed by an id, so two call sites asking for the same
model reuse one instance instead of loading it twice. When more than
``max_models`` are live, the least-recently-used one is evicted and its
``unload()`` called to free memory.

An evicted SDK loader stays evicted even through a handle a caller kept:
its next call re-registers it here, evicting another model, instead of
loading behind the registry's back.

Dependency-free (pure Python) — imports and tests without ``[genai]``.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle


@runtime_checkable
class Unloadable(Protocol):
    """Something the registry can evict by freeing its memory."""

    def unload(self) -> None:
        """Release the object's resources (VRAM/RAM)."""
        ...


T = TypeVar("T", bound=Unloadable)


class ModelRegistry:
    """An LRU cache of loaded models keyed by a string id.

    Each held object must expose ``unload()`` (``TextGenerator`` and
    ``Embedder`` do), called on eviction.

    Eviction never frees weights under a running call: the SDK loaders
    defer an ``unload()`` that arrives while calls are in flight until the
    last one finishes.

    A handle kept after its eviction (``model = registry.get(...)`` at
    startup, used for every request) does not reload behind the registry.
    Its next call re-registers it under its key — replacing whatever
    object a later :meth:`get` built there — and evicts the
    least-recently-used entry to make room, then waits for that entry's
    in-flight calls to finish before building. So ``max_models`` keeps
    holding for SDK loaders however the handle was obtained.

    The same wait applies to a model :meth:`get` just built: when the
    entry it pushed out still has calls running, the new model's first
    call blocks until they finish, so the two are never resident together.
    One allowance remains — a call made from *inside* another model's call
    does not wait, since the outer call could be what the evicted model is
    waiting on; there the ceiling is exceeded until that outer call ends.
    A third-party object exposing only ``unload()`` has no hook for any of
    this; hold those through :meth:`get` per request.

    Attributes:
        max_models (int): How many models may be live at once before the
            least-recently-used is evicted.
    """

    def __init__(self, max_models: int = 2) -> None:
        """Initialize the registry.

        Args:
            max_models (int): Live-model ceiling before LRU eviction.

        Raises:
            ValueError: When ``max_models`` is not positive.
        """
        if max_models <= 0:
            raise ValueError("max_models must be positive")
        self.max_models = max_models
        self._models: OrderedDict[str, Unloadable] = OrderedDict()
        self._lock: threading.RLock = threading.RLock()

    def get(self, key: str, factory: Callable[[], T]) -> T:
        """Return the model for ``key``, creating it via ``factory`` on miss.

        A hit marks the entry most-recently-used. A miss builds the model,
        stores it, and evicts the LRU entry when over capacity.

        Args:
            key (str): The model identity (e.g. the model id + config).
            factory (Callable[[], T]): Zero-arg builder called on a miss.

        Returns:
            T: The cached or freshly built model.
        """
        with self._lock:
            existing = self._models.get(key)
            if existing is not None:
                self._models.move_to_end(key)
                return existing  # type: ignore[return-value]
            model = factory()
            self._models[key] = model
            self._models.move_to_end(key)
            self._hold_until_drained(model, self._evict_over_capacity())
            return model

    @staticmethod
    def _hold_until_drained(model: Unloadable, blockers: list[ModelLifecycle]) -> None:
        """Make ``model`` wait for ``blockers`` to release before it builds.

        Args:
            model (Unloadable): The model just admitted.
            blockers (list[ModelLifecycle]): Models evicted to make room
                whose release is deferred behind calls in flight.
        """
        lifecycle = _lifecycle_of(model)
        if lifecycle is not None and blockers:
            lifecycle.wait_for(blockers)

    def _evict_over_capacity(self) -> list[ModelLifecycle]:
        """Evict LRU entries until at most ``max_models`` remain.

        Returns:
            list[ModelLifecycle]: The evicted models whose release was
            deferred behind calls still in flight.
        """
        pending: list[ModelLifecycle] = []
        while len(self._models) > self.max_models:
            key, model = self._models.popitem(last=False)
            lifecycle = self._unload_evicted(key, model)
            if lifecycle is not None:
                pending.append(lifecycle)
        return pending

    def _unload_evicted(self, key: str, model: Unloadable) -> ModelLifecycle | None:
        """Unload a model just removed from the registry, marking it evicted.

        Runs under the registry lock, so a call on a kept handle that finds
        the mark cannot readmit the model between the mark and the unload.

        Args:
            key (str): The key the model was registered under.
            model (Unloadable): The model removed from the registry.

        Returns:
            ModelLifecycle | None: The model's lifecycle when its release
            was deferred behind calls in flight, ``None`` otherwise.
        """
        lifecycle = _lifecycle_of(model)
        if lifecycle is not None:
            lifecycle.mark_evicted(lambda: self._readmit(key, model))
        model.unload()
        if lifecycle is not None and lifecycle.unload_pending:
            return lifecycle
        return None

    def _readmit(self, key: str, model: Unloadable) -> None:
        """Take an evicted model back, evicting what no longer fits.

        Called from the model's own lifecycle when a kept handle is used
        after its eviction. The object under ``key`` now, if a later
        :meth:`get` built one, is evicted in its favour: the caller is
        about to load this one, and two live copies of one key would be
        exactly the overcommit the ceiling exists to prevent.

        Models evicted to make room whose release is still waiting for
        their calls in flight become the readmitted model's blockers: it
        does not build until they have left memory.

        Args:
            key (str): The key the model was registered under.
            model (Unloadable): The evicted model being used again.
        """
        with self._lock:
            lifecycle = _lifecycle_of(model)
            if lifecycle is not None:
                lifecycle.clear_eviction()
            pending: list[ModelLifecycle] = []
            current = self._models.get(key)
            if current is not model:
                if current is not None:
                    del self._models[key]
                    replaced = self._unload_evicted(key, current)
                    if replaced is not None:
                        pending.append(replaced)
                self._models[key] = model
            self._models.move_to_end(key)
            pending.extend(self._evict_over_capacity())
            self._hold_until_drained(model, pending)

    def evict(self, key: str) -> bool:
        """Evict one model by key, calling its ``unload()``.

        Args:
            key (str): The entry to remove.

        An SDK loader evicted this way still counts if a kept handle uses
        it again: the call re-registers it (see the class docstring).

        Returns:
            bool: ``True`` when an entry was evicted, ``False`` otherwise.
        """
        with self._lock:
            model = self._models.pop(key, None)
            if model is None:
                return False
            self._unload_evicted(key, model)
            return True

    def evict_all(self) -> None:
        """Evict every model, calling each ``unload()``."""
        with self._lock:
            evicted = list(self._models.items())
            self._models.clear()
            for key, model in evicted:
                self._unload_evicted(key, model)

    def items(self) -> dict[str, Unloadable]:
        """Return the live entries, most-recently-used last.

        A copy, so iterating it while evicting is safe.

        Returns:
            dict[str, Unloadable]: Key to model, in LRU order — the first
            entry is the next one eviction would take.
        """
        with self._lock:
            return dict(self._models)

    def inventory(
        self,
        *,
        hardware: Any | None = None,
        probe: bool = True,
    ) -> Any:
        """Report what this registry is holding in memory right now.

        The capacity ceiling says how many models *may* be live; this says
        which ones are, on what device, and how long each has been idle —
        the question an operator actually asks when a card fills up.

        Args:
            hardware (Any | None): A ``HardwareInfo`` snapshot to reuse
                instead of probing the host again.
            probe (bool): When no snapshot is given, probe the host.
                ``False`` skips it, which makes this call pure bookkeeping.

        Returns:
            ModelRuntimeReport: The held handles plus, optionally, the
            host's memory picture.
        """
        from tempest_fastapi_sdk.genai.inventory import runtime_report

        return runtime_report(self.items(), hardware=hardware, probe=probe)

    def unload_idle(self) -> list[str]:
        """Free every held model that has sat idle past its own threshold.

        Entries stay in the registry — a `TextGenerator` that unloaded its
        weights is still the right object to hand out, and it reloads on
        next use. This frees memory without losing the configuration, so
        it is what a periodic task should call; :meth:`evict` and
        :meth:`evict_all` are for forgetting the entry entirely.

        A handle that does not implement ``unload_if_idle`` is skipped:
        without an idle clock there is no basis to decide, and unloading
        someone's model on a guess is worse than leaving it.

        Returns:
            list[str]: The keys whose models this call unloaded.
        """
        freed: list[str] = []
        for key, model in self.items().items():
            hook = getattr(model, "unload_if_idle", None)
            if callable(hook) and hook():
                freed.append(key)
        return freed

    def __len__(self) -> int:
        """Return how many models are currently live."""
        return len(self._models)

    def __contains__(self, key: str) -> bool:
        """Return whether ``key`` has a live model."""
        return key in self._models


def _lifecycle_of(model: object) -> ModelLifecycle | None:
    """Return the SDK lifecycle behind a held model, when it has one.

    Args:
        model (object): A model held by the registry.

    Returns:
        ModelLifecycle | None: The loader's lifecycle, or ``None`` for an
        object that only implements ``unload()``.
    """
    lifecycle = getattr(model, "_lifecycle", None)
    if isinstance(lifecycle, ModelLifecycle):
        return lifecycle
    return None


__all__: list[str] = [
    "ModelRegistry",
    "Unloadable",
]
