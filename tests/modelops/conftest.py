"""Shared fixtures for the model-ops tests.

The ONNX fixtures build a real graph on disk rather than mocking, because
what these tests are actually checking is that our reader agrees with the
installed ``onnx`` / ``onnxruntime`` — a mock would agree with itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def _build_gemm_model(path: Path, *, features: int, units: int) -> None:
    """Write a one-node Gemm model with a symbolic batch dimension.

    Args:
        path (Path): Where to write the ``.onnx`` file.
        features (int): Input width.
        units (int): Output width, which also sets the parameter count.
    """
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    weight = numpy_helper.from_array(
        np.random.default_rng(0).standard_normal((features, units)).astype(np.float32),
        "W",
    )
    bias = numpy_helper.from_array(np.zeros(units, dtype=np.float32), "B")
    inputs = helper.make_tensor_value_info("x", TensorProto.FLOAT, ["batch", features])
    outputs = helper.make_tensor_value_info("y", TensorProto.FLOAT, ["batch", units])
    graph = helper.make_graph(
        [helper.make_node("Gemm", ["x", "W", "B"], ["y"])],
        "tiny",
        [inputs],
        [outputs],
        [weight, bias],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 10
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(path))


@pytest.fixture
def tiny_onnx(tmp_path: Path) -> Path:
    """Return a small real ONNX model with 576 parameters.

    Returns:
        Path: Path to ``tiny.onnx`` inside the test's temp directory.
    """
    pytest.importorskip("onnx")
    path = tmp_path / "tiny.onnx"
    _build_gemm_model(path, features=8, units=64)
    return path


@pytest.fixture
def bigger_onnx(tmp_path: Path) -> Path:
    """Return a second, larger model for comparison tests.

    Returns:
        Path: Path to ``bigger.onnx`` inside the test's temp directory.
    """
    pytest.importorskip("onnx")
    path = tmp_path / "bigger.onnx"
    _build_gemm_model(path, features=8, units=512)
    return path


class FakeInputSpec:
    """Minimal stand-in for an ONNX Runtime input description."""

    def __init__(self, name: str, type_: str, shape: list[Any]) -> None:
        """Store the three attributes the SDK reads off a real spec.

        Args:
            name (str): Tensor name.
            type_ (str): ONNX Runtime type string.
            shape (list[Any]): Declared dimensions.
        """
        self.name = name
        self.type = type_
        self.shape = shape


class FakeSession:
    """Stand-in for ``onnxruntime.InferenceSession`` with fixed inputs."""

    def __init__(self, inputs: list[FakeInputSpec]) -> None:
        """Record the declared inputs.

        Args:
            inputs (list[FakeInputSpec]): Inputs the session reports.
        """
        self._inputs = inputs

    def get_inputs(self) -> list[FakeInputSpec]:
        """Return the declared inputs.

        Returns:
            list[FakeInputSpec]: The inputs passed to the constructor.
        """
        return self._inputs
