"""Quantization and optimization — raw ONNX graphs and HuggingFace models.

Quantization trades numeric precision for size and speed. Which trade you
get depends entirely on which path you take, so pick deliberately:

* :func:`quantize_onnx_dynamic` — needs nothing but the model. Roughly 4x
  smaller and faster on CPU, with no calibration step.
* :func:`quantize_onnx_static` — needs representative inputs. Same size as
  dynamic, faster still, and usually more accurate on models that dynamic
  quantization degrades.
* :func:`quantize_hf_onnx` — needs a transformers ONNX export. Dynamic int8
  with transformers-aware operator selection.
* :func:`optimize_hf_onnx` — needs a transformers ONNX export. Fusion only,
  so no precision is lost at all.
* :func:`quantize_hf_bnb` — needs a GPU. Int8/int4 weights that stay in the
  PyTorch format, for generation.

**Always re-measure accuracy after quantizing.** Int8 is lossy, the loss is
model-specific, and nothing in this module can tell you whether your task
tolerates it. Benchmark the quantized artifact with
:func:`~tempest_fastapi_sdk.modelops.benchmark_onnx` and re-run your
evaluation set before shipping.

The ONNX functions need the ``[modelops-onnx]`` extra; the HuggingFace ONNX
ones need ``[modelops-quant]``; :func:`quantize_hf_bnb` needs ``[genai]``
plus ``[genai-quant]``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
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


def _require_optimum() -> Any:
    """Import ``optimum.onnxruntime`` or raise a helpful error.

    Returns:
        Any: The ``optimum.onnxruntime`` module.

    Raises:
        ImportError: When the ``[modelops-quant]`` extra is not installed.
    """
    try:
        from optimum import onnxruntime as optimum_ort
    except ImportError as exc:
        raise ImportError(
            "HuggingFace optimization requires the optional "
            "[modelops-quant] extra (optimum + onnxruntime). Install with: "
            "pip install tempest-fastapi-sdk[modelops-quant]",
        ) from exc
    return optimum_ort


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


def export_hf_to_onnx(
    model_id: str,
    output_dir: str | Path,
    *,
    task: str = "auto",
    opset: int | None = None,
    device: str = "cpu",
    trust_remote_code: bool = False,
    cache_dir: str | Path | None = None,
) -> ExportResult:
    """Export a HuggingFace model to ONNX with `optimum`.

    The output is a *directory*: the graph plus the tokenizer and config
    files the runtime needs. Encoder-decoder models emit several graphs.

    Args:
        model_id (str): Hub id or a local directory.
        output_dir (str | Path): Directory to write the export into.
        task (str): Task to export for (``"text-classification"``,
            ``"feature-extraction"``, ``"token-classification"``…).
            ``"auto"`` infers it from the model config, which is right for
            most models and wrong for the ones with several heads.
        opset (int | None): ONNX opset. Defaults to `optimum`'s minimum for
            the architecture.
        device (str): Device to trace on. ``"cuda"`` is faster for large
            models but requires the weights to fit in VRAM.
        trust_remote_code (bool): Allow executing custom modelling code from
            the Hub repository. **This runs arbitrary Python from a remote
            source** — only enable it for a repository you audited.
        cache_dir (str | Path | None): HuggingFace cache directory.

    Returns:
        ExportResult: The written directory and its total size.

    Raises:
        ImportError: When the ``[modelops-quant]`` extra is missing.

    Example:

        >>> from tempest_fastapi_sdk.modelops import export_hf_to_onnx
        >>> result = export_hf_to_onnx(
        ...     "distilbert-base-uncased",
        ...     "exports/distilbert",
        ...     task="text-classification",
        ... )
        >>> result.output_path
    """
    _require_optimum()
    from optimum.exporters.onnx import main_export

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    main_export(
        model_name_or_path=model_id,
        output=str(destination),
        task=task,
        opset=opset,
        device=device,
        trust_remote_code=trust_remote_code,
        cache_dir=str(cache_dir) if cache_dir is not None else None,
    )
    output_size = size_mb(destination)
    return ExportResult(
        source_path=model_id,
        output_path=str(destination),
        format=ModelFormat.ONNX,
        output_size_mb=output_size,
        size_ratio=1.0,
        opset=opset,
    )


def optimize_hf_onnx(
    model_dir: str | Path,
    output_dir: str | Path,
    *,
    level: HFOptimizationLevel = HFOptimizationLevel.O2,
    for_gpu: bool = False,
    file_name: str | None = None,
) -> ExportResult:
    """Apply `optimum`'s graph fusions to an exported transformers model.

    Unlike quantization this is **lossless in precision** at ``O1``/``O2``:
    it fuses attention, layer norm and friends into single kernels without
    changing what the graph computes. ``O3`` swaps in an approximate GELU
    and ``O4`` converts to float16, so those two do move the numbers.

    Args:
        model_dir (str | Path): Directory produced by
            :func:`export_hf_to_onnx`.
        output_dir (str | Path): Where to write the optimized model.
        level (HFOptimizationLevel): Optimization preset. ``O4`` requires
            ``for_gpu=True``.
        for_gpu (bool): Target GPU kernels. A model optimized for GPU is not
            portable back to CPU.
        file_name (str | None): Specific graph inside ``model_dir``, for
            exports that contain more than one.

    Returns:
        ExportResult: The optimized directory and its size.

    Raises:
        ImportError: When the ``[modelops-quant]`` extra is missing.
        ValueError: When ``O4`` is requested without ``for_gpu``.

    Example:

        >>> from tempest_fastapi_sdk.modelops import optimize_hf_onnx
        >>> result = optimize_hf_onnx(
        ...     "exports/distilbert", "exports/distilbert-o2"
        ... )
        >>> result.output_path
    """
    optimum_ort = _require_optimum()
    from optimum.onnxruntime.configuration import AutoOptimizationConfig

    level = HFOptimizationLevel(level)
    if level is HFOptimizationLevel.O4 and not for_gpu:
        raise ValueError(
            "optimization level O4 converts the graph to float16 and is "
            "GPU-only; pass for_gpu=True or drop to O3"
        )

    source = Path(model_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    optimizer = optimum_ort.ORTOptimizer.from_pretrained(
        str(source),
        file_names=[file_name] if file_name else None,
    )
    optimizer.optimize(
        save_dir=str(destination),
        optimization_config=getattr(AutoOptimizationConfig, level.value)(
            for_gpu=for_gpu
        ),
    )
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
    file_name: str | None = None,
) -> QuantizationResult:
    """Dynamically quantize an exported transformers model with `optimum`.

    Same idea as :func:`quantize_onnx_dynamic`, but `optimum` picks the
    operator set and symmetry settings that suit transformer graphs, and
    ``target`` selects kernels for the CPU you will actually deploy on.

    Static quantization is deliberately not offered here: it needs a
    calibration dataset, and building one is a modelling decision. Export
    first, then call :func:`quantize_onnx_static` on the graph with your own
    samples.

    Args:
        model_dir (str | Path): Directory produced by
            :func:`export_hf_to_onnx`.
        output_dir (str | Path): Where to write the quantized model.
        target (HFQuantizationTarget): Instruction set to target. Choosing
            one your CPU lacks still yields a valid model, just a slow one.
        per_channel (bool): Quantize weights per channel.
        file_name (str | None): Specific graph inside ``model_dir``.

    Returns:
        QuantizationResult: The quantized directory and its size ratio.

    Raises:
        ImportError: When the ``[modelops-quant]`` extra is missing.

    Example:

        >>> from tempest_fastapi_sdk.modelops import quantize_hf_onnx
        >>> result = quantize_hf_onnx(
        ...     "exports/distilbert",
        ...     "exports/distilbert-int8",
        ...     target="arm64",
        ... )
        >>> result.compression_ratio
    """
    optimum_ort = _require_optimum()
    from optimum.onnxruntime.configuration import AutoQuantizationConfig

    target = HFQuantizationTarget(target)
    source = Path(model_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    factory = getattr(AutoQuantizationConfig, target.value)
    config = (
        factory(per_channel=per_channel)
        if target is HFQuantizationTarget.TENSORRT
        else factory(is_static=False, per_channel=per_channel)
    )
    quantizer = optimum_ort.ORTQuantizer.from_pretrained(
        str(source), file_name=file_name
    )
    quantizer.quantize(save_dir=str(destination), quantization_config=config)
    return _quantization_result(
        source,
        destination,
        backend=QuantizationBackend.OPTIMUM_ONNX,
        weight_type=QuantWeightType.INT8,
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
    "export_hf_to_onnx",
    "optimize_hf_onnx",
    "quantize_hf_bnb",
    "quantize_hf_onnx",
    "quantize_onnx_dynamic",
    "quantize_onnx_static",
]
