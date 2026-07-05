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
from typing import Protocol, TypeVar, runtime_checkable


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
