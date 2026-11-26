"""Structural typing contracts for optional third-party ML objects.

Modelops accepts objects from ``scikit-learn``, ``numpy``, ``pandas``,
``torch`` and ``onnxruntime`` without importing any of them at module load
time. The protocols and type aliases here name the exact slices those helpers
rely on so callers keep static type checking without pulling optional extras.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from numpy.typing import ArrayLike
    from torch import Tensor


class HasValues(Protocol):
    """DataFrame- or Series-like container exposing its raw array via ``values``."""

    @property
    def values(self) -> ArrayLike:
        """Underlying array representation."""
        ...


type TabularInput = ArrayLike | HasValues
"""2-D feature matrix or 1-D label sequence (array, nested sequence, DataFrame)."""


class SklearnEstimator(Protocol):
    """Minimal fitted-estimator contract accepted by scikit-learn export helpers.

    Satisfied by every fitted scikit-learn estimator and ``Pipeline``, as well
    as compatible third-party estimators exposing ``predict``.
    """

    def predict(self, x: TabularInput, /) -> Iterable[object]:
        """Predict labels or target values for a batch of rows."""
        ...


class SupportsAvailableProviders(Protocol):
    """Minimal ``onnxruntime`` module surface read by :func:`default_providers`."""

    def get_available_providers(self) -> Iterable[str]:
        """Return the execution providers registered by the runtime."""
        ...


type TorchExampleInput = Tensor | tuple[Tensor, ...]
"""Single tensor or tuple of tensors forwarded to a ``torch.nn.Module``."""

__all__: list[str] = [
    "HasValues",
    "SklearnEstimator",
    "SupportsAvailableProviders",
    "TabularInput",
    "TorchExampleInput",
]
