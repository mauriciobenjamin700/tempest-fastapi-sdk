"""Quantization and optimization — raw ONNX graphs and HuggingFace models.

Quantization trades numeric precision for size and speed. Which trade you
get depends entirely on which path you take, so pick deliberately:

* :func:`quantize_onnx_dynamic` — needs nothing but the model. Roughly 4x
  smaller and faster on CPU, with no calibration step.
* :func:`quantize_onnx_static` — needs representative inputs. Same size as
  dynamic, faster still, and usually more accurate on models that dynamic
  quantization degrades.
* :func:`quantize_hf_onnx` — needs a transformers ONNX export. Dynamic int8
  with the operator set and per-ISA settings that suit transformer graphs.
* :func:`optimize_hf_onnx` — needs a transformers ONNX export. Fusion only,
  so no precision is lost at ``O1``/``O2``.
* :func:`quantize_hf_bnb` — needs a GPU. Int8/int4 weights that stay in the
  PyTorch format, for generation.

**Always re-measure accuracy after quantizing.** Int8 is lossy, the loss is
model-specific, and nothing in this module can tell you whether your task
tolerates it. Benchmark the quantized artifact with
:func:`~tempest_fastapi_sdk.modelops.benchmark_onnx` and re-run your
evaluation set before shipping.

Every ONNX function here — raw graphs and transformers exports alike — needs
only the ``[modelops-onnx]`` extra. :func:`quantize_hf_bnb` needs ``[genai]``
plus ``[genai-quant]``.

**Producing the transformers export is deliberately out of scope.** Turning
an arbitrary architecture into ONNX needs a per-architecture graph
description, and the only maintained registry of those lives in HuggingFace
`optimum`, which pins ``transformers`` to an upper bound. Depending on it
would propagate that cap to every consumer of this SDK, so the export stays a
one-off build step you run in a throwaway environment::

    uvx --from "optimum[onnxruntime]" optimum-cli export onnx \\
        --model distilbert-base-uncased --task text-classification \\
        exports/distilbert

Point :func:`optimize_hf_onnx` and :func:`quantize_hf_onnx` at the directory
that command writes. Everything after the export runs on the ``onnxruntime``
this SDK already depends on, with no bound on ``transformers`` at all.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.modelops._fs import size_mb, size_ratio
from tempest_fastapi_sdk.modelops.schemas import (
    CalibrationMethod,
    ExportResult,
    HFOptimizationLevel,
    HFQuantizationTarget,
    ModelFormat,
    QuantizationBackend,
    QuantizationFormat,
    QuantizationResult,
    QuantWeightType,
)

_QUANT_TYPE_ATTRS: dict[QuantWeightType, str] = {
    QuantWeightType.INT8: "QInt8",
    QuantWeightType.UINT8: "QUInt8",
    QuantWeightType.INT16: "QInt16",
    QuantWeightType.UINT16: "QUInt16",
    QuantWeightType.INT4: "QInt4",
    QuantWeightType.UINT4: "QUInt4",
    QuantWeightType.FLOAT8E4M3FN: "QFLOAT8E4M3FN",
}
"""Our weight-type names mapped to ``onnxruntime.quantization.QuantType``."""

_QUANT_FORMAT_ATTRS: dict[QuantizationFormat, str] = {
    QuantizationFormat.QDQ: "QDQ",
    QuantizationFormat.QOPERATOR: "QOperator",
}
"""Our format names mapped to ``onnxruntime.quantization.QuantFormat``."""

_CALIBRATION_ATTRS: dict[CalibrationMethod, str] = {
    CalibrationMethod.MINMAX: "MinMax",
    CalibrationMethod.ENTROPY: "Entropy",
    CalibrationMethod.PERCENTILE: "Percentile",
    CalibrationMethod.DISTRIBUTION: "Distribution",
}
"""Our method names mapped to ``onnxruntime.quantization.CalibrationMethod``."""


def _require_onnx_quantization() -> Any:
    """Import ``onnxruntime.quantization`` or raise a helpful error.

    Returns:
        Any: The ``onnxruntime.quantization`` module.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is not installed.
    """
    try:
        from onnxruntime import quantization
    except ImportError as exc:
        raise ImportError(
            "ONNX quantization requires the optional [modelops-onnx] extra "
            "(onnx + onnxruntime). Install with: "
            "pip install tempest-fastapi-sdk[modelops-onnx]",
        ) from exc
    return quantization


_ORT_FUSION_MODEL_TYPES: dict[str, str] = {
    "albert": "bert",
    "bart": "bart",
    "bert": "bert",
    "big_bird": "bert",
    "bigbird_pegasus": "bart",
    "blenderbot": "bert",
    "bloom": "gpt2",
    "camembert": "bert",
    "clip": "clip",
    "codegen": "gpt2",
    "deberta": "bert",
    "deberta-v2": "bert",
    "dinov2": "vit",
    "distilbert": "bert",
    "electra": "bert",
    "gpt2": "gpt2",
    "gpt_bigcode": "gpt2",
    "gpt_neo": "gpt2",
    "gpt_neox": "gpt2",
    "gptj": "gpt2",
    "granite": "gpt2",
    "llama": "gpt2",
    "longt5": "bert",
    "m2m_100": "bart",
    "marian": "bart",
    "mbart": "bart",
    "mistral": "gpt2",
    "modernbert": "bert",
    "mpnet": "bert",
    "mt5": "bart",
    "nystromformer": "bert",
    "pegasus": "bert",
    "pix2struct": "vit",
    "roberta": "bert",
    "segformer": "vit",
    "t5": "bert",
    "vit": "vit",
    "whisper": "bart",
    "xlm-roberta": "bert",
}
"""HuggingFace ``model_type`` mapped to an ONNX Runtime fusion model type.

ONNX Runtime's fusion registry keys off its own coarser set of graph shapes,
not off HuggingFace architecture names: every BERT-shaped encoder fuses as
``"bert"``, every decoder-only stack as ``"gpt2"``, every encoder-decoder as
``"bart"``. The mapping is not derivable — ``t5`` fusing as ``"bert"`` rather
than ONNX Runtime's own ``"t5"`` is an empirical choice, not a naming rule.

Ported from `optimum`'s ``ORTConfigManager._conf``
(``optimum/onnxruntime/utils.py``), which is where that empirical knowledge
was accumulated. ``tests/modelops/test_quantize.py`` asserts every value here
still exists in the installed runtime's registry, so a rename upstream fails
the suite instead of silently falling back.

An architecture absent from this table is reported, never guessed: fusing a
graph as the wrong shape produces a model that loads and returns wrong
numbers. Pass ``model_type=`` explicitly to override.
"""


@dataclass(frozen=True)
class _OptimizationSpec:
    """One graph-optimization preset, resolved to ONNX Runtime arguments.

    Attributes:
        opt_level (int): ONNX Runtime ``opt_level`` — 1 is basic, 2 adds the
            extended graph transformations.
        transformers_specific (bool): Whether to run the transformer fusions
            on top of the runtime's own passes. Maps to the inverse of
            ``only_onnxruntime``.
        gelu_approximation (bool): Swap GELU for its tanh approximation.
            Faster, and it moves the numbers.
        fp16 (bool): Convert the graph to float16 after fusing.
    """

    opt_level: int
    transformers_specific: bool
    gelu_approximation: bool
    fp16: bool


_OPTIMIZATION_SPECS: dict[HFOptimizationLevel, _OptimizationSpec] = {
    HFOptimizationLevel.O1: _OptimizationSpec(
        opt_level=1,
        transformers_specific=False,
        gelu_approximation=False,
        fp16=False,
    ),
    HFOptimizationLevel.O2: _OptimizationSpec(
        opt_level=2,
        transformers_specific=True,
        gelu_approximation=False,
        fp16=False,
    ),
    HFOptimizationLevel.O3: _OptimizationSpec(
        opt_level=2,
        transformers_specific=True,
        gelu_approximation=True,
        fp16=False,
    ),
    HFOptimizationLevel.O4: _OptimizationSpec(
        opt_level=2,
        transformers_specific=True,
        gelu_approximation=True,
        fp16=True,
    ),
}
"""The ``O1`` to ``O4`` presets.

Ported from `optimum`'s ``AutoOptimizationConfig._LEVELS``
(``optimum/onnxruntime/configuration.py``).

Note that ``O3`` and ``O4`` share ``opt_level=2`` with ``O2``: the escalation
past ``O2`` is not a deeper runtime level, it is opting into transformations
that change the computed numbers.
"""


@dataclass(frozen=True)
class _IsaQuantizationSpec:
    """Dynamic-quantization settings for one target instruction set.

    Attributes:
        weight_type (QuantWeightType): Integer type the weights become.
        tunable_reduce_range (bool): Whether ``reduce_range`` is meaningful
            on this ISA. Only the pre-VNNI x86 targets saturate, so it is
            rejected elsewhere rather than silently costing accuracy.
    """

    weight_type: QuantWeightType
    tunable_reduce_range: bool


_ISA_QUANTIZATION_SPECS: dict[HFQuantizationTarget, _IsaQuantizationSpec] = {
    HFQuantizationTarget.ARM64: _IsaQuantizationSpec(
        weight_type=QuantWeightType.INT8,
        tunable_reduce_range=False,
    ),
    HFQuantizationTarget.AVX2: _IsaQuantizationSpec(
        weight_type=QuantWeightType.UINT8,
        tunable_reduce_range=True,
    ),
    HFQuantizationTarget.AVX512: _IsaQuantizationSpec(
        weight_type=QuantWeightType.INT8,
        tunable_reduce_range=True,
    ),
    HFQuantizationTarget.AVX512_VNNI: _IsaQuantizationSpec(
        weight_type=QuantWeightType.INT8,
        tunable_reduce_range=False,
    ),
}
"""Per-ISA weight types, ported from `optimum`'s ``AutoQuantizationConfig``.

Two things are worth knowing about this table, because both look like
mistakes and are not:

* ``AVX2`` takes **unsigned** int8 weights while the others take signed. That
  is what `optimum` selects, and it follows the kernels AVX2 actually has.
* ``reduce_range`` is exposed only on ``AVX2`` and ``AVX512``. Those lack VNNI,
  so their int8 accumulation can saturate and dropping to 7 bits avoids it.
  ARM64 and VNNI do not have the problem, and there ``reduce_range`` would be
  pure accuracy loss — so it is refused instead of accepted and ignored.

Activation types are absent on purpose: dynamic quantization derives
activation ranges at inference, so ONNX Runtime's ``quantize_dynamic`` takes
no activation dtype at all.
"""


def _require_ort_transformers() -> Any:
    """Import ``onnxruntime.transformers.optimizer`` or raise a helpful error.

    This ships inside the ``onnxruntime`` wheel — it is the same fusion engine
    `optimum` delegates to — so the ``[modelops-onnx]`` extra is all it needs.

    Returns:
        Any: The ``onnxruntime.transformers.optimizer`` module.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is not installed.
    """
    try:
        from onnxruntime.transformers import optimizer
    except ImportError as exc:
        raise ImportError(
            "transformers graph optimization requires the optional "
            "[modelops-onnx] extra (onnx + onnxruntime). Install with: "
            "pip install tempest-fastapi-sdk[modelops-onnx]",
        ) from exc
    return optimizer


def _resolve_enum(module: Any, container: str, attribute: str, label: str) -> Any:
    """Look up an enum member on the installed onnxruntime.

    Args:
        module (Any): The ``onnxruntime.quantization`` module.
        container (str): Enum class name, e.g. ``"QuantType"``.
        attribute (str): Member name, e.g. ``"QInt4"``.
        label (str): Our own name, for the error message.

    Returns:
        Any: The enum member.

    Raises:
        ValueError: When the installed runtime is too old to have it.
    """
    enum_class = getattr(module, container)
    if not hasattr(enum_class, attribute):
        raise ValueError(
            f"{label!r} needs a newer onnxruntime: "
            f"{container} has no member {attribute}"
        )
    return getattr(enum_class, attribute)


def quantize_onnx_dynamic(
    model_path: str | Path,
    output_path: str | Path,
    *,
    weight_type: QuantWeightType = QuantWeightType.INT8,
    per_channel: bool = False,
    reduce_range: bool = False,
    op_types: Sequence[str] | None = None,
    nodes_to_exclude: Sequence[str] | None = None,
    extra_options: Mapping[str, Any] | None = None,
) -> QuantizationResult:
    """Quantize weights to int8 without any calibration data.

    Weights are quantized ahead of time; activation ranges are computed at
    inference. That makes this the zero-friction option — no dataset, no
    calibration pass — and it is usually the right first attempt for
    transformer and MLP-heavy models, where the win comes from the weights.

    Convolutional vision models often prefer
    :func:`quantize_onnx_static`: their cost is in activations, which this
    path leaves in float.

    Args:
        model_path (str | Path): Model to quantize.
        output_path (str | Path): Where to write the quantized model.
        weight_type (QuantWeightType): Target weight type. ``INT8`` is the
            default; ``UINT8`` sometimes suits older ARM kernels better.
        per_channel (bool): Quantize convolution weights per output channel
            instead of per tensor. Better accuracy, slightly larger, and
            not supported by every provider.
        reduce_range (bool): Use 7 bits instead of 8. Costs accuracy, but
            avoids the int8 overflow that pre-VNNI x86 CPUs are prone to.
        op_types (Sequence[str] | None): Restrict quantization to these
            operator types. Defaults to the runtime's own selection.
        nodes_to_exclude (Sequence[str] | None): Node names to leave in
            float — the standard escape hatch for the one layer that
            collapses under int8.
        extra_options (Mapping[str, Any] | None): Passed straight through
            to ONNX Runtime.

    Returns:
        QuantizationResult: Output path plus the compression ratio.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        FileNotFoundError: When the input does not exist.
        ValueError: When the runtime lacks the requested weight type.

    Example:

        >>> from tempest_fastapi_sdk.modelops import quantize_onnx_dynamic
        >>> result = quantize_onnx_dynamic(
        ...     "models/classify.onnx", "models/classify.int8.onnx"
        ... )
        >>> result.compression_ratio
    """
    quantization = _require_onnx_quantization()
    weight_type = QuantWeightType(weight_type)
    source = Path(model_path)
    if not source.is_file():
        raise FileNotFoundError(f"model not found: {source}")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    quantization.quantize_dynamic(
        str(source),
        str(destination),
        weight_type=_resolve_enum(
            quantization,
            "QuantType",
            _QUANT_TYPE_ATTRS[weight_type],
            weight_type.value,
        ),
        per_channel=per_channel,
        reduce_range=reduce_range,
        op_types_to_quantize=list(op_types) if op_types else None,
        nodes_to_exclude=list(nodes_to_exclude) if nodes_to_exclude else None,
        extra_options=dict(extra_options) if extra_options else None,
    )
    return _quantization_result(
        source,
        destination,
        backend=QuantizationBackend.ONNXRUNTIME_DYNAMIC,
        weight_type=weight_type,
        per_channel=per_channel,
    )


def quantize_onnx_static(
    model_path: str | Path,
    output_path: str | Path,
    *,
    calibration_inputs: Iterable[Mapping[str, Any]],
    weight_type: QuantWeightType = QuantWeightType.INT8,
    activation_type: QuantWeightType = QuantWeightType.INT8,
    quant_format: QuantizationFormat = QuantizationFormat.QDQ,
    calibrate_method: CalibrationMethod = CalibrationMethod.MINMAX,
    per_channel: bool = False,
    op_types: Sequence[str] | None = None,
    nodes_to_exclude: Sequence[str] | None = None,
    extra_options: Mapping[str, Any] | None = None,
) -> QuantizationResult:
    """Quantize weights *and* activations using calibration samples.

    A calibration pass runs the model over real inputs to learn the range
    each activation actually occupies, then bakes those ranges in. Both the
    weight and the activation path become integer, which is what unlocks
    the fused int8 kernels — the speedup is larger than dynamic
    quantization, and so is the accuracy risk.

    The calibration data must look like production data. A few hundred
    samples drawn from the real distribution beat tens of thousands of
    synthetic ones; a range learned from noise will clip real activations.

    Args:
        model_path (str | Path): Model to quantize.
        output_path (str | Path): Where to write the quantized model.
        calibration_inputs (Iterable[Mapping[str, Any]]): Feed dicts, one
            per calibration batch, shaped exactly like the dicts you would
            pass to ``session.run``. Consumed once.
        weight_type (QuantWeightType): Target weight type.
        activation_type (QuantWeightType): Target activation type.
            ``UINT8`` activations are common for vision models whose
            activations are post-ReLU and therefore non-negative.
        quant_format (QuantizationFormat): ``QDQ`` (portable) or
            ``QOPERATOR`` (smaller, narrower provider support).
        calibrate_method (CalibrationMethod): Range-selection algorithm.
            Try ``ENTROPY`` or ``PERCENTILE`` when ``MINMAX`` loses accuracy
            — a single outlier activation stretches a min/max range until
            everything else quantizes into a handful of levels.
        per_channel (bool): Quantize convolution weights per output channel.
        op_types (Sequence[str] | None): Restrict quantization to these
            operator types.
        nodes_to_exclude (Sequence[str] | None): Node names to leave float.
        extra_options (Mapping[str, Any] | None): Passed straight through.

    Returns:
        QuantizationResult: Output path, compression ratio, and a note with
        the number of calibration batches consumed.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        FileNotFoundError: When the input does not exist.
        ValueError: When no calibration samples were supplied.

    Example:

        >>> import numpy as np
        >>> from tempest_fastapi_sdk.modelops import quantize_onnx_static
        >>> batches = [
        ...     {"images": np.zeros((1, 3, 224, 224), dtype=np.float32)}
        ... ]
        >>> result = quantize_onnx_static(
        ...     "models/classify.onnx",
        ...     "models/classify.qdq.onnx",
        ...     calibration_inputs=batches,
        ... )
        >>> result.notes
    """
    quantization = _require_onnx_quantization()
    weight_type = QuantWeightType(weight_type)
    activation_type = QuantWeightType(activation_type)
    quant_format = QuantizationFormat(quant_format)
    calibrate_method = CalibrationMethod(calibrate_method)
    source = Path(model_path)
    if not source.is_file():
        raise FileNotFoundError(f"model not found: {source}")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    batches = [dict(batch) for batch in calibration_inputs]
    if not batches:
        raise ValueError(
            "static quantization needs at least one calibration batch; "
            "use quantize_onnx_dynamic() when you have no representative data"
        )

    reader = _make_calibration_reader(quantization, batches)
    quantization.quantize_static(
        str(source),
        str(destination),
        calibration_data_reader=reader,
        quant_format=_resolve_enum(
            quantization,
            "QuantFormat",
            _QUANT_FORMAT_ATTRS[quant_format],
            quant_format.value,
        ),
        activation_type=_resolve_enum(
            quantization,
            "QuantType",
            _QUANT_TYPE_ATTRS[activation_type],
            activation_type.value,
        ),
        weight_type=_resolve_enum(
            quantization,
            "QuantType",
            _QUANT_TYPE_ATTRS[weight_type],
            weight_type.value,
        ),
        calibrate_method=_resolve_enum(
            quantization,
            "CalibrationMethod",
            _CALIBRATION_ATTRS[calibrate_method],
            calibrate_method.value,
        ),
        per_channel=per_channel,
        op_types_to_quantize=list(op_types) if op_types else None,
        nodes_to_exclude=list(nodes_to_exclude) if nodes_to_exclude else None,
        extra_options=dict(extra_options) if extra_options else None,
    )
    return _quantization_result(
        source,
        destination,
        backend=QuantizationBackend.ONNXRUNTIME_STATIC,
        weight_type=weight_type,
        per_channel=per_channel,
        notes=[f"calibrated on {len(batches)} batch(es)"],
    )


def _make_calibration_reader(quantization: Any, batches: list[dict[str, Any]]) -> Any:
    """Wrap a list of feed dicts in ONNX Runtime's reader interface.

    Args:
        quantization (Any): The ``onnxruntime.quantization`` module.
        batches (list[dict[str, Any]]): Already-materialized feed dicts.

    Returns:
        Any: A ``CalibrationDataReader`` yielding each batch once.
    """

    class _ListCalibrationReader(quantization.CalibrationDataReader):  # type: ignore[misc]
        """Yield pre-built feed dicts to the calibrator, once each."""

        def __init__(self) -> None:
            """Start the iterator over the supplied batches."""
            self._iterator = iter(batches)

        def get_next(self) -> dict[str, Any] | None:
            """Return the next feed dict, or ``None`` when exhausted.

            Returns:
                dict[str, Any] | None: The next calibration batch.
            """
            return next(self._iterator, None)

    return _ListCalibrationReader()


def _quantization_result(
    source: Path,
    destination: Path,
    *,
    backend: QuantizationBackend,
    weight_type: QuantWeightType | None,
    per_channel: bool = False,
    notes: list[str] | None = None,
) -> QuantizationResult:
    """Build a :class:`QuantizationResult` from two paths.

    Args:
        source (Path): Input model or directory.
        destination (Path): Output model or directory.
        backend (QuantizationBackend): Engine that produced it.
        weight_type (QuantWeightType | None): Target weight type.
        per_channel (bool): Whether per-channel quantization was used.
        notes (list[str] | None): Extra caveats to surface.

    Returns:
        QuantizationResult: Populated result with the size comparison.
    """
    source_size = size_mb(source)
    output_size = size_mb(destination)
    return QuantizationResult(
        source_path=str(source),
        output_path=str(destination),
        backend=backend,
        weight_type=weight_type,
        source_size_mb=source_size,
        output_size_mb=output_size,
        compression_ratio=size_ratio(source_size, output_size),
        per_channel=per_channel,
        notes=notes or [],
    )


_CONFIG_HEAD_KEYS: tuple[str, ...] = ("num_attention_heads", "n_head", "num_heads")
"""Keys a HuggingFace config may use for the attention-head count."""

_CONFIG_HIDDEN_KEYS: tuple[str, ...] = ("hidden_size", "n_embd", "d_model")
"""Keys a HuggingFace config may use for the hidden size."""

_EXTERNAL_DATA_THRESHOLD_MB: float = 1900.0
"""Graph size above which tensors must be written outside the protobuf.

Protobuf caps a single message at 2 GB. A graph near that ceiling cannot be
serialized inline, so the writer switches to external data. This is a format
limit, not a tuning knob.
"""


@dataclass(frozen=True)
class _FusionArchitecture:
    """What ONNX Runtime's fusion pass needs to know about a model.

    Attributes:
        model_type (str): Fusion model type, e.g. ``"bert"``.
        num_heads (int): Attention-head count, or ``0`` to let the runtime
            infer it from the graph.
        hidden_size (int): Hidden size, or ``0`` to let the runtime infer it.
    """

    model_type: str
    num_heads: int
    hidden_size: int


def _resolve_export_graph(export_dir: Path, file_name: str | None) -> Path:
    """Pick which ONNX graph inside a transformers export to work on.

    Args:
        export_dir (Path): Directory an ONNX export wrote.
        file_name (str | None): Explicit graph to use. Required when the
            export holds more than one.

    Returns:
        Path: The chosen graph file.

    Raises:
        FileNotFoundError: When the directory, or the named graph, is missing.
        ValueError: When the export holds several graphs and none was named.
    """
    if not export_dir.is_dir():
        raise FileNotFoundError(f"export directory not found: {export_dir}")
    if file_name is not None:
        graph = export_dir / file_name
        if not graph.is_file():
            raise FileNotFoundError(f"graph not found in export: {graph}")
        return graph
    graphs = sorted(export_dir.glob("*.onnx"))
    if not graphs:
        raise FileNotFoundError(f"no .onnx graph in export directory: {export_dir}")
    if len(graphs) > 1:
        names = ", ".join(graph.name for graph in graphs)
        raise ValueError(
            f"export holds {len(graphs)} graphs ({names}); pass file_name= to "
            "pick one. Encoder-decoder exports split into several graphs, and "
            "each has to be processed on its own"
        )
    return graphs[0]


def _resolve_fusion_architecture(
    export_dir: Path,
    model_type: str | None,
    supported_types: Mapping[str, Any],
) -> _FusionArchitecture:
    """Read a transformers export's config and map it onto ONNX Runtime's registry.

    Head count and hidden size are optional: ONNX Runtime treats ``0`` as
    "infer it from the graph", which is what a config missing those keys gets.
    The model type is not optional — fusing a graph as the wrong shape yields a
    model that loads and computes the wrong thing — so an unknown architecture
    raises instead of falling back to a default.

    Args:
        export_dir (Path): Directory holding ``config.json``.
        model_type (str | None): Override, skipping the config lookup. Must
            name a type the installed runtime knows.
        supported_types (Mapping[str, Any]): The runtime's fusion registry.

    Returns:
        _FusionArchitecture: Resolved fusion arguments.

    Raises:
        FileNotFoundError: When ``config.json`` is missing and no override was
            given.
        ValueError: When the config is unreadable, or the architecture is not
            in the mapping, or the override is unknown to the runtime.
    """
    if model_type is not None and model_type not in supported_types:
        known = ", ".join(sorted(supported_types))
        raise ValueError(
            f"model_type={model_type!r} is not a fusion type this onnxruntime "
            f"knows; supported: {known}"
        )

    config_path = export_dir / "config.json"
    config: dict[str, Any] = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"could not read {config_path}: {exc}") from exc
    elif model_type is None:
        raise FileNotFoundError(
            f"{config_path} not found: optimize/quantize expect the directory "
            "an ONNX export wrote, config.json included. Pass model_type= to "
            "work on a bare graph instead"
        )

    if model_type is None:
        declared = config.get("model_type")
        if not isinstance(declared, str):
            raise ValueError(
                f"{config_path} has no string 'model_type'; pass model_type= explicitly"
            )
        if declared not in _ORT_FUSION_MODEL_TYPES:
            known = ", ".join(sorted(_ORT_FUSION_MODEL_TYPES))
            raise ValueError(
                f"no fusion mapping for architecture {declared!r}. Fusing it as "
                "the wrong graph shape would produce a model that loads and "
                f"returns wrong numbers, so it is not guessed. Mapped "
                f"architectures: {known}. Pass model_type= to choose a fusion "
                "type yourself, or skip optimization and quantize directly"
            )
        model_type = _ORT_FUSION_MODEL_TYPES[declared]

    return _FusionArchitecture(
        model_type=model_type,
        num_heads=_first_int(config, _CONFIG_HEAD_KEYS),
        hidden_size=_first_int(config, _CONFIG_HIDDEN_KEYS),
    )


def _first_int(config: Mapping[str, Any], keys: Sequence[str]) -> int:
    """Read the first key holding an int, or ``0`` when none does.

    Args:
        config (Mapping[str, Any]): Parsed HuggingFace config.
        keys (Sequence[str]): Candidate keys, most canonical first.

    Returns:
        int: The value found, or ``0`` meaning "let the runtime infer it".
    """
    for key in keys:
        value = config.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _copy_export_sidecars(source: Path, destination: Path) -> None:
    """Copy an export's non-graph files next to the processed graph.

    An ONNX export directory is a graph plus the files a runtime needs to use
    it: ``config.json``, the tokenizer, preprocessor settings. Processing the
    graph without carrying those forward leaves an output directory that
    ``AutoTokenizer`` cannot load, so they come along.

    Args:
        source (Path): Original export directory.
        destination (Path): Directory the processed graph was written to.
    """
    for entry in sorted(source.iterdir()):
        if not entry.is_file() or entry.suffix in {".onnx", ".onnx_data"}:
            continue
        target = destination / entry.name
        if not target.exists():
            shutil.copy2(entry, target)


def _use_external_data(graph: Path, override: bool | None) -> bool:
    """Decide whether the output graph needs external tensor storage.

    Args:
        graph (Path): Input graph, used to size the decision.
        override (bool | None): Explicit choice, or ``None`` to size it.

    Returns:
        bool: Whether to write tensors outside the protobuf.
    """
    if override is not None:
        return override
    return size_mb(graph) > _EXTERNAL_DATA_THRESHOLD_MB


def optimize_hf_onnx(
    model_dir: str | Path,
    output_dir: str | Path,
    *,
    level: HFOptimizationLevel = HFOptimizationLevel.O2,
    for_gpu: bool = False,
    file_name: str | None = None,
    model_type: str | None = None,
    use_external_data_format: bool | None = None,
) -> ExportResult:
    """Apply ONNX Runtime's transformer fusions to an exported model.

    Unlike quantization this is **lossless in precision** at ``O1``/``O2``: it
    fuses attention, layer norm and friends into single kernels without
    changing what the graph computes. ``O3`` swaps in an approximate GELU and
    ``O4`` converts to float16, so those two do move the numbers.

    The fusion engine is ``onnxruntime.transformers``, which ships inside the
    ``onnxruntime`` wheel — no HuggingFace `optimum` involved, and therefore no
    bound on your ``transformers`` version. See this module's docstring for how
    to produce the export in the first place.

    Args:
        model_dir (str | Path): Directory an ONNX export wrote — the graph plus
            ``config.json``.
        output_dir (str | Path): Where to write the optimized model. The
            export's non-graph files are copied along.
        level (HFOptimizationLevel): Optimization preset. ``O4`` requires
            ``for_gpu=True``.
        for_gpu (bool): Target GPU kernels. A model optimized for GPU is not
            portable back to CPU.
        file_name (str | None): Specific graph inside ``model_dir``. Required
            when the export holds more than one.
        model_type (str | None): Override the fusion type instead of deriving
            it from ``config.json``. Use this for an architecture the mapping
            does not cover, or to optimize a bare graph with no config beside
            it.
        use_external_data_format (bool | None): Write tensors outside the
            protobuf. ``None`` decides by size, which is what a model near the
            2 GB protobuf ceiling needs.

    Returns:
        ExportResult: The optimized directory and its size.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        ValueError: When ``O4`` is requested without ``for_gpu``, when the
            export holds several graphs and none was named, or when the
            architecture has no fusion mapping.
        FileNotFoundError: When the export directory or its ``config.json`` is
            missing.

    Example:

        >>> from tempest_fastapi_sdk.modelops import optimize_hf_onnx
        >>> result = optimize_hf_onnx(
        ...     "exports/distilbert", "exports/distilbert-o2"
        ... )
        >>> result.output_path
    """
    optimizer = _require_ort_transformers()
    from onnxruntime.transformers.fusion_options import FusionOptions

    level = HFOptimizationLevel(level)
    if level is HFOptimizationLevel.O4 and not for_gpu:
        raise ValueError(
            "optimization level O4 converts the graph to float16 and is "
            "GPU-only; pass for_gpu=True or drop to O3"
        )

    source = Path(model_dir)
    graph = _resolve_export_graph(source, file_name)
    architecture = _resolve_fusion_architecture(
        source, model_type, optimizer.MODEL_TYPES
    )
    spec = _OPTIMIZATION_SPECS[level]

    options = FusionOptions(architecture.model_type)
    options.enable_gelu_approximation = spec.gelu_approximation

    optimized = optimizer.optimize_model(
        str(graph),
        architecture.model_type,
        architecture.num_heads,
        architecture.hidden_size,
        optimization_options=options,
        opt_level=spec.opt_level,
        use_gpu=for_gpu,
        only_onnxruntime=not spec.transformers_specific,
    )
    if spec.fp16:
        optimized.convert_float_to_float16(keep_io_types=True)

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    optimized.save_model_to_file(
        str(destination / graph.name),
        use_external_data_format=_use_external_data(graph, use_external_data_format),
    )
    _copy_export_sidecars(source, destination)

    source_size = size_mb(source)
    output_size = size_mb(destination)
    return ExportResult(
        source_path=str(source),
        output_path=str(destination),
        format=ModelFormat.ONNX,
        source_size_mb=source_size,
        output_size_mb=output_size,
        size_ratio=size_ratio(source_size, output_size),
    )


def quantize_hf_onnx(
    model_dir: str | Path,
    output_dir: str | Path,
    *,
    target: HFQuantizationTarget = HFQuantizationTarget.AVX512_VNNI,
    per_channel: bool = False,
    reduce_range: bool = False,
    symmetric_weights: bool = True,
    file_name: str | None = None,
) -> QuantizationResult:
    """Dynamically quantize an exported transformers model to int8.

    Same engine as :func:`quantize_onnx_dynamic`, with the weight type and
    saturation settings that suit transformer graphs on the CPU you will
    actually deploy on, and with the export's tokenizer and config carried
    forward so the output directory stays loadable.

    Static quantization is deliberately not offered here: it needs a
    calibration dataset, and building one is a modelling decision. Export
    first, then call :func:`quantize_onnx_static` on the graph with your own
    samples.

    Args:
        model_dir (str | Path): Directory an ONNX export wrote.
        output_dir (str | Path): Where to write the quantized model.
        target (HFQuantizationTarget): Instruction set to target. Choosing one
            your CPU lacks still yields a valid model, just a slow one.
        per_channel (bool): Quantize weights per output channel instead of per
            tensor. Better accuracy, slightly larger.
        reduce_range (bool): Use 7 bits instead of 8, avoiding the int8
            saturation that pre-VNNI x86 is prone to. Only ``AVX2`` and
            ``AVX512`` can saturate, so passing it for another target is an
            error rather than a silent accuracy loss.
        symmetric_weights (bool): Center the weight range on zero. On by
            default, matching ONNX Runtime.
        file_name (str | None): Specific graph inside ``model_dir``. Required
            when the export holds more than one.

    Returns:
        QuantizationResult: The quantized directory and its size ratio.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        ValueError: When ``reduce_range`` is set for an ISA that cannot
            saturate, or when the export holds several graphs and none was
            named.
        FileNotFoundError: When the export directory is missing.

    Example:

        >>> from tempest_fastapi_sdk.modelops import quantize_hf_onnx
        >>> result = quantize_hf_onnx(
        ...     "exports/distilbert",
        ...     "exports/distilbert-int8",
        ...     target="arm64",
        ... )
        >>> result.compression_ratio
    """
    quantization = _require_onnx_quantization()
    target = HFQuantizationTarget(target)
    spec = _ISA_QUANTIZATION_SPECS[target]
    if reduce_range and not spec.tunable_reduce_range:
        raise ValueError(
            f"reduce_range has no purpose on {target.value}: its int8 "
            "accumulation does not saturate, so 7-bit weights would only cost "
            "accuracy. It applies to avx2 and avx512"
        )

    source = Path(model_dir)
    graph = _resolve_export_graph(source, file_name)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    quantization.quantize_dynamic(
        str(graph),
        str(destination / graph.name),
        weight_type=_resolve_enum(
            quantization,
            "QuantType",
            _QUANT_TYPE_ATTRS[spec.weight_type],
            spec.weight_type.value,
        ),
        per_channel=per_channel,
        reduce_range=reduce_range,
        extra_options={"WeightSymmetric": symmetric_weights},
    )
    _copy_export_sidecars(source, destination)

    return _quantization_result(
        source,
        destination,
        backend=QuantizationBackend.ONNXRUNTIME_TRANSFORMERS,
        weight_type=spec.weight_type,
        per_channel=per_channel,
        notes=[f"targeted {target.value}"],
    )


def quantize_hf_bnb(
    model_id: str,
    output_dir: str | Path,
    *,
    bits: int = 4,
    quant_type: str = "nf4",
    compute_dtype: str = "float16",
    double_quant: bool = True,
    trust_remote_code: bool = False,
) -> QuantizationResult:
    """Load a HuggingFace model in int8/int4 and save the quantized weights.

    This is the PyTorch path, for generative models that stay in
    transformers. Unlike the ONNX routes it keeps the model runnable through
    ``AutoModelForCausalLM``, which is what
    :class:`~tempest_fastapi_sdk.genai.TextGenerator` consumes.

    Quantizing requires a CUDA GPU — bitsandbytes has no CPU kernel for the
    conversion — and the saved artifact can only be reloaded on a machine
    that also has bitsandbytes.

    Args:
        model_id (str): Hub id or local directory to load.
        output_dir (str | Path): Where to save the quantized weights.
        bits (int): ``4`` or ``8``.
        quant_type (str): 4-bit data type — ``"nf4"`` (normal-float, better
            for normally distributed weights) or ``"fp4"``. Ignored at
            8 bits.
        compute_dtype (str): Dtype the dequantized matmul runs in;
            ``"float16"`` or ``"bfloat16"``.
        double_quant (bool): Quantize the quantization constants too. Saves
            roughly another 0.4 bits per parameter for no measurable quality
            cost.
        trust_remote_code (bool): Allow executing custom modelling code from
            the Hub repository. **This runs arbitrary Python from a remote
            source** — only enable it for a repository you audited.

    Returns:
        QuantizationResult: Output directory and its size, with the source
        size left at zero because the original lives in the HuggingFace
        cache rather than at a path we were given.

    Raises:
        ImportError: When transformers, torch or bitsandbytes are missing.
        ValueError: When ``bits`` is neither 4 nor 8.

    Example:

        >>> from tempest_fastapi_sdk.modelops import quantize_hf_bnb
        >>> result = quantize_hf_bnb(
        ...     "Qwen/Qwen2.5-0.5B-Instruct", "models/qwen-int4", bits=4
        ... )
        >>> result.backend
    """
    if bits not in {4, 8}:
        raise ValueError(f"bits must be 4 or 8, got {bits}")
    torch, transformers = _require_transformers()

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    config = transformers.BitsAndBytesConfig(
        load_in_4bit=bits == 4,
        load_in_8bit=bits == 8,
        bnb_4bit_quant_type=quant_type,
        bnb_4bit_compute_dtype=getattr(torch, compute_dtype),
        bnb_4bit_use_double_quant=double_quant,
    )
    model = transformers.AutoModelForCausalLM.from_pretrained(
        model_id,
        quantization_config=config,
        device_map="auto",
        trust_remote_code=trust_remote_code,
    )
    model.save_pretrained(str(destination))
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        model_id, trust_remote_code=trust_remote_code
    )
    tokenizer.save_pretrained(str(destination))

    output_size = size_mb(destination)
    return QuantizationResult(
        source_path=model_id,
        output_path=str(destination),
        backend=QuantizationBackend.BITSANDBYTES,
        weight_type=(QuantWeightType.INT4 if bits == 4 else QuantWeightType.INT8),
        output_size_mb=output_size,
        compression_ratio=1.0,
        notes=[
            f"{bits}-bit bitsandbytes weights",
            "source size unknown: the original lives in the HuggingFace cache",
            "reloading requires bitsandbytes on the target host",
        ],
    )


def _require_transformers() -> tuple[Any, Any]:
    """Import torch + transformers + bitsandbytes or raise a helpful error.

    Returns:
        tuple[Any, Any]: ``(torch, transformers)``.

    Raises:
        ImportError: When any of the three is missing.
    """
    try:
        import bitsandbytes  # noqa: F401
        import torch
        import transformers
    except ImportError as exc:
        raise ImportError(
            "bitsandbytes quantization requires the optional [genai] and "
            "[genai-quant] extras. Install with: "
            "pip install tempest-fastapi-sdk[genai,genai-quant]",
        ) from exc
    return torch, transformers


__all__: list[str] = [
    "optimize_hf_onnx",
    "quantize_hf_bnb",
    "quantize_hf_onnx",
    "quantize_onnx_dynamic",
    "quantize_onnx_static",
]
