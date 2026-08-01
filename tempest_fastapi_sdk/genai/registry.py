"""Share loaded models across call sites, with LRU eviction.

Loading a model is expensive and its weights eat VRAM. `ModelRegistry`
keeps loaded models keyed by an id, so two call sites asking for the same
model reuse one instance instead of loading it twice. When more than
``max_models`` are live, the least-recently-used one is evicted and its
``unload()`` called to free memory.

Dependency-free (pure Python) — imports and tests without ``[genai]``.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable


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
        existing = self._models.get(key)
        if existing is not None:
            self._models.move_to_end(key)
            return existing  # type: ignore[return-value]
        model = factory()
        self._models[key] = model
        self._models.move_to_end(key)
        self._evict_over_capacity()
        return model

    def _evict_over_capacity(self) -> None:
        """Evict LRU entries until at most ``max_models`` remain."""
        while len(self._models) > self.max_models:
            _key, model = self._models.popitem(last=False)
            model.unload()

    def evict(self, key: str) -> bool:
        """Evict one model by key, calling its ``unload()``.

        Args:
            key (str): The entry to remove.

        Returns:
            bool: ``True`` when an entry was evicted, ``False`` otherwise.
        """
        model = self._models.pop(key, None)
        if model is None:
            return False
        model.unload()
        return True

    def evict_all(self) -> None:
        """Evict every model, calling each ``unload()``."""
        for model in self._models.values():
            model.unload()
        self._models.clear()

    def items(self) -> dict[str, Unloadable]:
        """Return the live entries, most-recently-used last.

        A copy, so iterating it while evicting is safe.

        Returns:
            dict[str, Unloadable]: Key to model, in LRU order — the first
            entry is the next one eviction would take.
        """
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

        return runtime_report(dict(self._models), hardware=hardware, probe=probe)

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
        for key, model in self._models.items():
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


__all__: list[str] = [
    "ModelRegistry",
    "Unloadable",
]
