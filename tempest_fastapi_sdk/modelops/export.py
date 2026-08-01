"""Model export: PyTorch to ONNX, ONNX to ORT, and graph optimization.

The path this module exists for is the edge one:

``.pt`` → :func:`export_torch_to_onnx` → ``.onnx`` →
:func:`export_onnx_to_ort` → ``.ort``

``.ort`` is ONNX Runtime's own serialized format. It matters for mobile and
embedded targets for two reasons: the graph optimizations are already baked
in, so start-up does not pay for them, and the conversion emits a
``.required_operators.config`` listing exactly which kernels the model uses
— feed that to a minimal ONNX Runtime build and the binary drops from tens
of megabytes to a few.

Everything here needs the ``[modelops-onnx]`` extra; the torch export
additionally needs torch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.modelops._fs import size_mb, size_ratio
from tempest_fastapi_sdk.modelops.schemas import (
    ExportResult,
    GraphOptimizationLevel,
    ModelFormat,
    OrtOptimizationStyle,
)
from tempest_fastapi_sdk.modelops.static import (
    _require_onnxruntime,
    _require_torch,
    analyze_onnx,
    default_providers,
)

ORT_CONFIG_SUFFIXES: tuple[str, ...] = (
    ".required_operators.config",
    ".required_operators.with_runtime_opt.config",
)
"""Side files the ORT conversion writes for minimal-build kernel selection."""


def _require_torch_onnx_export() -> Any:
    """Import ``torch`` and check its ONNX exporter can actually run.

    Since torch 2.9 ``torch.onnx.export`` defaults to the dynamo exporter,
    which imports ``onnxscript`` lazily — so a missing ``onnxscript`` surfaces
    as a ``ModuleNotFoundError`` from deep inside torch, halfway through the
    export, rather than as something actionable. Checking up front turns that
    into the same "install this" message every other optional dependency in
    this module produces.

    Returns:
        Any: The ``torch`` module.

    Raises:
        ImportError: When torch or ``onnxscript`` is missing.
    """
    torch = _require_torch()
    try:
        import onnxscript  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "torch.onnx.export needs onnxscript from torch 2.9 onwards (its "
            "dynamo exporter is built on it). Install with: "
            "pip install onnxscript",
        ) from exc
    return torch


_GRAPH_LEVEL_ATTRS: dict[GraphOptimizationLevel, str] = {
    GraphOptimizationLevel.DISABLE_ALL: "ORT_DISABLE_ALL",
    GraphOptimizationLevel.BASIC: "ORT_ENABLE_BASIC",
    GraphOptimizationLevel.EXTENDED: "ORT_ENABLE_EXTENDED",
    GraphOptimizationLevel.LAYOUT: "ORT_ENABLE_LAYOUT",
    GraphOptimizationLevel.ALL: "ORT_ENABLE_ALL",
}
"""Our level names mapped to ``onnxruntime.GraphOptimizationLevel`` members."""


def _require_ort_converter() -> Any:
    """Import the ``.onnx`` to ``.ort`` converter or raise a helpful error.

    The converter lives in ``onnxruntime.tools`` but imports ``onnx``
    internally, so both packages must be present.

    Returns:
        Any: The ``convert_onnx_models_to_ort`` module.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is not installed.
    """
    try:
        import onnxruntime.tools.convert_onnx_models_to_ort as converter
    except ImportError as exc:
        raise ImportError(
            "ORT conversion requires the optional [modelops-onnx] extra "
            "(onnx + onnxruntime). Install with: "
            "pip install tempest-fastapi-sdk[modelops-onnx]",
        ) from exc
    return converter


def _ort_files(directory: Path) -> dict[Path, float]:
    """Map every ``.ort`` file under ``directory`` to its mtime.

    Args:
        directory (Path): Directory to scan, recursively.

    Returns:
        dict[Path, float]: Path to modification time.
    """
    if not directory.is_dir():
        return {}
    return {path: path.stat().st_mtime for path in sorted(directory.rglob("*.ort"))}


def _matching_source(produced: Path, sources: list[Path]) -> Path:
    """Find the ``.onnx`` a produced ``.ort`` came from.

    The converter derives the output name from the input stem, appending
    ``.with_runtime_opt`` for the runtime style, so the longest source stem
    that prefixes the output stem is the right one.

    Args:
        produced (Path): The written ``.ort`` file.
        sources (list[Path]): Candidate ``.onnx`` inputs.

    Returns:
        Path: The best-matching source, or ``produced`` itself when no
        candidate matches.
    """
    stem = produced.stem
    matches = [source for source in sources if stem.startswith(source.stem)]
    if not matches:
        return produced
    return max(matches, key=lambda source: len(source.stem))


def export_onnx_to_ort(
    model_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    optimization_style: OrtOptimizationStyle = OrtOptimizationStyle.FIXED,
    enable_type_reduction: bool = False,
    target_platform: str | None = None,
    save_optimized_onnx_model: bool = False,
    allow_conversion_failures: bool = False,
    custom_op_library: str | Path | None = None,
) -> list[ExportResult]:
    """Convert one ``.onnx`` file — or a directory of them — to ``.ort``.

    Args:
        model_path (str | Path): A ``.onnx`` file, or a directory that will
            be converted recursively.
        output_dir (str | Path | None): Where to write. Defaults to writing
            next to each input.
        optimization_style (OrtOptimizationStyle): ``FIXED`` bakes the
            optimizations in (smallest, fastest to load, the mobile
            default); ``RUNTIME`` keeps the graph re-optimizable on the
            target device.
        enable_type_reduction (bool): Also record which *data types* each
            operator needs, so a minimal build can drop unused type
            implementations. Shrinks the runtime binary further; only
            useful together with a custom build.
        target_platform (str | None): ``"amd64"`` or ``"arm"`` — restricts
            optimizations to those valid on that platform. Set it when the
            conversion machine and the deployment target differ, which for
            a mobile build is always.
        save_optimized_onnx_model (bool): Also write the optimized ``.onnx``
            alongside, for inspection.
        allow_conversion_failures (bool): Keep going when one model in a
            directory fails, instead of aborting the batch.
        custom_op_library (str | Path | None): Shared library providing
            custom operators the model needs.

    Returns:
        list[ExportResult]: One entry per ``.ort`` written, each carrying
        the size comparison and the config side files.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        FileNotFoundError: When ``model_path`` does not exist.

    Example:

        >>> from tempest_fastapi_sdk.modelops import export_onnx_to_ort
        >>> results = export_onnx_to_ort(
        ...     "models/classify.onnx",
        ...     "models/mobile",
        ...     target_platform="arm",
        ...     enable_type_reduction=True,
        ... )
        >>> results[0].output_path
    """
    converter = _require_ort_converter()
    optimization_style = OrtOptimizationStyle(optimization_style)
    source = Path(model_path)
    if not source.exists():
        raise FileNotFoundError(f"model not found: {source}")

    destination = Path(output_dir) if output_dir is not None else None
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=True)
    scan_root = destination or (source if source.is_dir() else source.parent)
    before = _ort_files(scan_root)

    style = (
        converter.OptimizationStyle.Fixed
        if optimization_style is OrtOptimizationStyle.FIXED
        else converter.OptimizationStyle.Runtime
    )
    converter.convert_onnx_models_to_ort(
        source,
        output_dir=destination,
        optimization_styles=[style],
        custom_op_library_path=(
            Path(custom_op_library) if custom_op_library is not None else None
        ),
        target_platform=target_platform,
        save_optimized_onnx_model=save_optimized_onnx_model,
        allow_conversion_failures=allow_conversion_failures,
        enable_type_reduction=enable_type_reduction,
    )

    after = _ort_files(scan_root)
    produced = [
        path
        for path, mtime in after.items()
        if path not in before or mtime > before[path]
    ]
    sources = sorted(source.rglob("*.onnx")) if source.is_dir() else [source]
    source_size = 0.0 if source.is_dir() else size_mb(source)

    results: list[ExportResult] = []
    for path in sorted(produced):
        origin = _matching_source(path, sources)
        origin_size = size_mb(origin) if origin != path else source_size
        output_size = size_mb(path)
        results.append(
            ExportResult(
                source_path=str(origin),
                output_path=str(path),
                format=ModelFormat.ORT,
                source_size_mb=origin_size,
                output_size_mb=output_size,
                size_ratio=size_ratio(origin_size, output_size),
                optimization_style=optimization_style,
                extra_files=_config_files(path),
            )
        )
    return results


def _config_files(ort_path: Path) -> list[str]:
    """Collect the operator-config files written next to an ``.ort``.

    Args:
        ort_path (Path): The produced ``.ort`` file.

    Returns:
        list[str]: Existing config paths, possibly empty.
    """
    found: list[str] = []
    for suffix in ORT_CONFIG_SUFFIXES:
        for candidate in sorted(ort_path.parent.glob(f"*{suffix}")):
            found.append(str(candidate))
    return found


def export_torch_to_onnx(
    module: Any,
    output_path: str | Path,
    *,
    example_input: Any,
    opset: int = 17,
    input_names: list[str] | None = None,
    output_names: list[str] | None = None,
    dynamic_axes: dict[str, dict[int, str]] | None = None,
    half: bool = False,
    do_constant_folding: bool = True,
    source_path: str | Path | None = None,
) -> ExportResult:
    """Export a ``torch.nn.Module`` to ONNX.

    Args:
        module (Any): Module to export. It is switched to ``eval()`` first;
            exporting a module in training mode bakes dropout and the
            batch-norm training path into the graph.
        output_path (str | Path): Where to write the ``.onnx`` file.
        example_input (Any): A tensor, or tuple of tensors, tracing runs
            through the model. Its shapes become the graph's fixed shapes
            unless ``dynamic_axes`` says otherwise.
        opset (int): ONNX opset. Newer is more expressive; older is more
            portable — mobile runtimes and third-party converters often lag,
            and ``12`` remains the safest floor for those.
        input_names (list[str] | None): Names for the graph inputs.
        output_names (list[str] | None): Names for the graph outputs.
        dynamic_axes (dict[str, dict[int, str]] | None): Axes that must stay
            variable, e.g. ``{"images": {0: "batch"}}``. Leave a dimension
            fixed when you can: fixed shapes let the runtime pick faster
            kernels.
        half (bool): Convert the module and inputs to float16 before
            tracing. **Mutates the module in place.** Halves the artifact,
            but a float16 graph is slower than float32 on most CPUs — this
            is for GPU and for ARM cores with native half support.
        do_constant_folding (bool): Fold constant subgraphs at export.
        source_path (str | Path | None): Checkpoint the module came from,
            recorded for the size comparison.

    Returns:
        ExportResult: The written artifact with its size and opset.

    Raises:
        ImportError: When torch is not installed.

    Example:

        >>> import torch
        >>> from tempest_fastapi_sdk.modelops import export_torch_to_onnx
        >>> result = export_torch_to_onnx(
        ...     torch.nn.Linear(128, 10),
        ...     "linear.onnx",
        ...     example_input=torch.randn(1, 128),
        ...     input_names=["features"],
        ...     output_names=["logits"],
        ...     dynamic_axes={"features": {0: "batch"}},
        ... )
        >>> result.opset
        17
    """
    torch = _require_torch_onnx_export()
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    module.eval()
    args = example_input if isinstance(example_input, tuple) else (example_input,)
    if half:
        module.half()
        args = tuple(item.half() if hasattr(item, "half") else item for item in args)

    with torch.no_grad():
        torch.onnx.export(
            module,
            args,
            str(destination),
            opset_version=opset,
            input_names=input_names,
            output_names=output_names,
            dynamic_axes=dynamic_axes,
            do_constant_folding=do_constant_folding,
        )

    origin = Path(source_path) if source_path is not None else None
    origin_size = size_mb(origin) if origin is not None else 0.0
    output_size = size_mb(destination)
    return ExportResult(
        source_path=str(origin) if origin is not None else "",
        output_path=str(destination),
        format=ModelFormat.ONNX,
        source_size_mb=origin_size,
        output_size_mb=output_size,
        size_ratio=size_ratio(origin_size, output_size),
        opset=opset,
    )


def optimize_onnx_graph(
    model_path: str | Path,
    output_path: str | Path,
    *,
    level: GraphOptimizationLevel = GraphOptimizationLevel.ALL,
    providers: list[str] | None = None,
) -> ExportResult:
    """Run ONNX Runtime's graph optimizer and write the result back out.

    This is constant folding plus operator fusion — the same work the
    runtime does at every session start, done once and persisted. It changes
    the graph, not the numerics, so accuracy is unaffected. Reach for it
    when start-up latency matters but you cannot move to ``.ort``.

    An optimized graph is specialized for the providers it was optimized
    with: a model fused for CUDA can be slower, or fail to load, on a
    CPU-only host. Optimize per target.

    Args:
        model_path (str | Path): Model to optimize.
        output_path (str | Path): Where to write the optimized ``.onnx``.
        level (GraphOptimizationLevel): How aggressive to be.
        providers (list[str] | None): Providers to optimize for. Defaults to
            whatever the runtime offers.

    Returns:
        ExportResult: The written artifact with a before/after size ratio.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        FileNotFoundError: When the input does not exist.
        ValueError: When the installed runtime has no such level.

    Example:

        >>> from tempest_fastapi_sdk.modelops import optimize_onnx_graph
        >>> result = optimize_onnx_graph(
        ...     "models/classify.onnx", "models/classify.opt.onnx"
        ... )
        >>> result.size_ratio
    """
    onnxruntime = _require_onnxruntime()
    source = Path(model_path)
    if not source.is_file():
        raise FileNotFoundError(f"model not found: {source}")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    attribute = _GRAPH_LEVEL_ATTRS[GraphOptimizationLevel(level)]
    if not hasattr(onnxruntime.GraphOptimizationLevel, attribute):
        raise ValueError(
            f"optimization level {level.value!r} needs a newer onnxruntime: "
            f"GraphOptimizationLevel has no {attribute}"
        )

    options = onnxruntime.SessionOptions()
    options.graph_optimization_level = getattr(
        onnxruntime.GraphOptimizationLevel, attribute
    )
    options.optimized_model_filepath = str(destination)
    onnxruntime.InferenceSession(
        str(source),
        options,
        providers=providers or default_providers(onnxruntime),
    )

    source_size = size_mb(source)
    output_size = size_mb(destination)
    opset: int | None = None
    try:
        opset = analyze_onnx(destination).opset
    except (ImportError, FileNotFoundError):
        opset = None
    return ExportResult(
        source_path=str(source),
        output_path=str(destination),
        format=ModelFormat.ONNX,
        source_size_mb=source_size,
        output_size_mb=output_size,
        size_ratio=size_ratio(source_size, output_size),
        opset=opset,
    )


__all__: list[str] = [
    "ORT_CONFIG_SUFFIXES",
    "export_onnx_to_ort",
    "export_torch_to_onnx",
    "optimize_onnx_graph",
]
