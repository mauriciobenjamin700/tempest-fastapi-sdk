"""Static model analysis — parameters, size, shapes and FLOPs.

"Static" means *without running inference*: everything here is read from the
artifact itself, so it is cheap, deterministic and comparable across
machines. That is what makes it the right thing to quote next to a latency
number, which is not comparable across machines at all.

Three entry points, one per artifact kind, plus a dispatcher:

* :func:`analyze_onnx` — parses the ONNX graph (needs the ``[modelops-onnx]``
  extra).
* :func:`analyze_ort` — opens an ``.ort`` file through ONNX Runtime. The
  serialized format drops the initializer table the parameter count comes
  from, so it reports shapes and size only.
* :func:`analyze_torch` — inspects a live ``torch.nn.Module``.
* :func:`analyze_model` — picks one of the above from the file suffix.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.modelops._fs import size_mb
from tempest_fastapi_sdk.modelops.schemas import (
    ModelFormat,
    StaticModelMetrics,
    TensorSpec,
)


def _require_onnx() -> Any:
    """Import ``onnx`` or raise a helpful error.

    Returns:
        Any: The ``onnx`` module.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is not installed.
    """
    try:
        import onnx
    except ImportError as exc:
        raise ImportError(
            "ONNX analysis requires the optional [modelops-onnx] extra "
            "(onnx + onnxruntime). Install with: "
            "pip install tempest-fastapi-sdk[modelops-onnx]",
        ) from exc
    return onnx


def _require_onnxruntime() -> Any:
    """Import ``onnxruntime`` or raise a helpful error.

    Returns:
        Any: The ``onnxruntime`` module.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is not installed.
    """
    try:
        import onnxruntime
    except ImportError as exc:
        raise ImportError(
            "ONNX Runtime support requires the optional [modelops-onnx] "
            "extra. Install with: "
            "pip install tempest-fastapi-sdk[modelops-onnx]",
        ) from exc
    return onnxruntime


REMOTE_PROVIDERS: frozenset[str] = frozenset({"AzureExecutionProvider"})
"""Execution providers that call out to a remote endpoint.

ONNX Runtime registers ``AzureExecutionProvider`` by default and returns it
first from ``get_available_providers()``. Leaving it in the default priority
list would make a *local* benchmark report a remote provider, so it is
filtered out unless the caller asks for it explicitly.
"""


def default_providers(onnxruntime: Any) -> list[str]:
    """Return the local execution providers, in priority order.

    Args:
        onnxruntime (Any): The imported ``onnxruntime`` module.

    Returns:
        list[str]: Available providers minus the remote ones.
    """
    return [
        provider
        for provider in onnxruntime.get_available_providers()
        if provider not in REMOTE_PROVIDERS
    ]


def _require_torch() -> Any:
    """Import ``torch`` or raise a helpful error.

    Returns:
        Any: The ``torch`` module.

    Raises:
        ImportError: When torch is not installed.
    """
    try:
        import torch
    except ImportError as exc:
        raise ImportError(
            "Torch model analysis requires torch. Install it directly or "
            "through the optional [genai] extra: "
            "pip install tempest-fastapi-sdk[genai]",
        ) from exc
    return torch


def _onnx_tensor_specs(value_infos: Any) -> list[TensorSpec]:
    """Convert ONNX ``ValueInfoProto`` entries into :class:`TensorSpec`.

    A dimension is emitted as an ``int`` when the graph fixes it and as the
    declared parameter name (``"batch"``) when it is symbolic, so a caller
    can tell which dimensions still need a value before benchmarking.

    Args:
        value_infos (Any): Iterable of ``onnx.ValueInfoProto``.

    Returns:
        list[TensorSpec]: One entry per tensor.
    """
    specs: list[TensorSpec] = []
    for info in value_infos:
        tensor_type = info.type.tensor_type
        shape: list[int | str] = []
        for dim in tensor_type.shape.dim:
            if dim.HasField("dim_value"):
                shape.append(int(dim.dim_value))
            else:
                shape.append(dim.dim_param or "?")
        specs.append(
            TensorSpec(
                name=info.name,
                dtype=str(tensor_type.elem_type),
                shape=shape,
            )
        )
    return specs


def analyze_onnx(
    model_path: str | Path,
    *,
    name: str | None = None,
) -> StaticModelMetrics:
    """Read parameter count, opset, size and shapes out of an ONNX graph.

    Parameters are summed from the initializer dimensions rather than from
    the tensor data, so a multi-gigabyte model is inspected without loading
    a single weight.

    Args:
        model_path (str | Path): Path to the ``.onnx`` file.
        name (str | None): Display name. Defaults to the file stem.

    Returns:
        StaticModelMetrics: Populated metrics with ``gflops`` left ``None``
        — an ONNX graph carries no cheap FLOP count.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        FileNotFoundError: When ``model_path`` does not exist.

    Example:

        >>> from tempest_fastapi_sdk.modelops import analyze_onnx
        >>> metrics = analyze_onnx("models/classify.onnx")
        >>> metrics.n_parameters, metrics.disk_size_mb
    """
    onnx = _require_onnx()
    path = Path(model_path)
    if not path.is_file():
        raise FileNotFoundError(f"model not found: {path}")

    model = onnx.load(str(path), load_external_data=False)
    n_parameters = sum(
        int(math.prod(initializer.dims)) for initializer in model.graph.initializer
    )
    opset: int | None = None
    for entry in model.opset_import:
        if entry.domain in {"", "ai.onnx"}:
            opset = int(entry.version)
            break

    return StaticModelMetrics(
        name=name or path.stem,
        path=str(path),
        format=ModelFormat.ONNX,
        n_parameters=n_parameters,
        disk_size_mb=size_mb(path),
        opset=opset,
        producer=model.producer_name or None,
        inputs=_onnx_tensor_specs(model.graph.input),
        outputs=_onnx_tensor_specs(model.graph.output),
    )


def analyze_ort(
    model_path: str | Path,
    *,
    name: str | None = None,
    providers: list[str] | None = None,
) -> StaticModelMetrics:
    """Read size and tensor shapes out of an ``.ort`` file.

    The ORT format is the graph already optimized and serialized for the
    minimal runtime; it does not keep the initializer table in a form the
    Python API exposes, so ``n_parameters`` stays ``0``. Analyze the source
    ``.onnx`` with :func:`analyze_onnx` when the parameter count matters.

    Args:
        model_path (str | Path): Path to the ``.ort`` file.
        name (str | None): Display name. Defaults to the file stem.
        providers (list[str] | None): Execution providers to open the
            session with. Defaults to :func:`default_providers`.

    Returns:
        StaticModelMetrics: Size plus declared inputs and outputs.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        FileNotFoundError: When ``model_path`` does not exist.
    """
    onnxruntime = _require_onnxruntime()
    path = Path(model_path)
    if not path.is_file():
        raise FileNotFoundError(f"model not found: {path}")

    session = onnxruntime.InferenceSession(
        str(path),
        providers=providers or default_providers(onnxruntime),
    )
    return StaticModelMetrics(
        name=name or path.stem,
        path=str(path),
        format=ModelFormat.ORT,
        disk_size_mb=size_mb(path),
        inputs=[
            TensorSpec(name=i.name, dtype=str(i.type), shape=list(i.shape))
            for i in session.get_inputs()
        ],
        outputs=[
            TensorSpec(name=o.name, dtype=str(o.type), shape=list(o.shape))
            for o in session.get_outputs()
        ],
    )


def analyze_torch(
    module: Any,
    *,
    name: str | None = None,
    path: str | Path | None = None,
    example_input: Any = None,
) -> StaticModelMetrics:
    """Count parameters and (optionally) FLOPs of a live ``nn.Module``.

    FLOPs are counted with ``torch.utils.flop_counter.FlopCounterMode``,
    which runs one forward pass under a dispatch mode. Two consequences
    worth knowing before you quote the number:

    * It counts a multiply-accumulate as **two** FLOPs. Papers that report
      "GFLOPs" from ``thop`` or Ultralytics usually report MACs, i.e. half
      this figure. Say which convention you used.
    * It executes the model, so pass an ``example_input`` shaped like real
      input and expect it to cost one forward pass.

    Args:
        module (Any): The ``torch.nn.Module`` to inspect.
        name (str | None): Display name. Defaults to the class name.
        path (str | Path | None): Checkpoint path, when the module came
            from one, so the report can carry its on-disk size.
        example_input (Any): A tensor (or tuple of tensors) to run through
            the module for the FLOP count. Omit to skip counting.

    Returns:
        StaticModelMetrics: Parameter counts, size and — when
        ``example_input`` was supplied and the counter is available —
        ``gflops``.

    Raises:
        ImportError: When torch is not installed.

    Example:

        >>> import torch
        >>> from tempest_fastapi_sdk.modelops import analyze_torch
        >>> model = torch.nn.Linear(128, 10)
        >>> metrics = analyze_torch(
        ...     model, example_input=torch.randn(1, 128)
        ... )
        >>> metrics.n_parameters
        1290
    """
    _require_torch()
    parameters = list(module.parameters())
    return StaticModelMetrics(
        name=name or type(module).__name__,
        path=str(path) if path is not None else "",
        format=ModelFormat.TORCH,
        n_parameters=sum(int(p.numel()) for p in parameters),
        n_trainable_parameters=sum(
            int(p.numel()) for p in parameters if p.requires_grad
        ),
        disk_size_mb=size_mb(path) if path is not None else 0.0,
        gflops=_count_gflops(module, example_input),
    )


def _count_gflops(module: Any, example_input: Any) -> float | None:
    """Run one forward pass under the torch FLOP counter.

    Args:
        module (Any): Module to profile.
        example_input (Any): Input tensor, tuple of tensors, or ``None`` to
            skip counting.

    Returns:
        float | None: Forward GFLOPs, or ``None`` when no input was given
        or the installed torch has no usable flop counter.
    """
    if example_input is None:
        return None
    torch = _require_torch()
    try:
        from torch.utils.flop_counter import FlopCounterMode
    except ImportError:  # pragma: no cover - torch < 2.1
        return None

    args = example_input if isinstance(example_input, tuple) else (example_input,)
    counter = FlopCounterMode(display=False)
    try:
        with torch.no_grad(), counter:
            module(*args)
    except Exception:  # pragma: no cover - model-specific forward failures
        return None
    return float(counter.get_total_flops()) / 1e9


def analyze_model(
    model_path: str | Path,
    *,
    name: str | None = None,
) -> StaticModelMetrics:
    """Analyze a model file, picking the reader from its suffix.

    Args:
        model_path (str | Path): Path to a ``.onnx`` or ``.ort`` file.
        name (str | None): Display name. Defaults to the file stem.

    Returns:
        StaticModelMetrics: Whatever the matching reader could collect.

    Raises:
        ValueError: When the suffix is neither ``.onnx`` nor ``.ort``.
            PyTorch checkpoints need :func:`analyze_torch` with a loaded
            module, because unpickling arbitrary weights is the caller's
            decision, not the SDK's.

    Example:

        >>> from tempest_fastapi_sdk.modelops import analyze_model
        >>> analyze_model("models/classify.ort").format
    """
    path = Path(model_path)
    suffix = path.suffix.lower()
    if suffix == ".onnx":
        return analyze_onnx(path, name=name)
    if suffix == ".ort":
        return analyze_ort(path, name=name)
    raise ValueError(
        f"cannot analyze {path.name}: expected a .onnx or .ort file. "
        "Load a torch checkpoint yourself and call analyze_torch()."
    )


__all__: list[str] = [
    "REMOTE_PROVIDERS",
    "analyze_model",
    "analyze_onnx",
    "analyze_ort",
    "analyze_torch",
    "default_providers",
]
