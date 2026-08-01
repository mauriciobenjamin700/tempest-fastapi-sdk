"""``tempest model`` — inspect, benchmark, convert and quantize models.

The command-line face of :mod:`tempest_fastapi_sdk.modelops`. It exists so
the measure/convert/quantize loop can run from a Makefile or a CI job
without anyone writing a throwaway script:

    tempest model analyze models/classify.onnx
    tempest model bench models/classify.onnx --dim height=224 --dim width=224
    tempest model quantize models/classify.onnx models/classify.int8.onnx
    tempest model export-ort models/classify.int8.onnx -o dist/ -t arm

Every command needs the ``[modelops-onnx]`` extra except ``hardware``, which
reports what this host can measure and therefore has to work everywhere. A
missing extra exits 2 with the install line, never a traceback.
"""

from __future__ import annotations

import json
from typing import Any

import typer

model_app: typer.Typer = typer.Typer(
    name="model",
    help="Analyze, benchmark, convert and quantize ONNX models.",
    no_args_is_help=True,
)


def _fail(message: str) -> None:
    """Print an error and exit with the CLI's validation code.

    Args:
        message (str): Message shown to the user, lowercase, saying how to
            fix the problem.

    Raises:
        typer.Exit: Always, with code 2.
    """
    typer.secho(f"error: {message}", fg="red", err=True)
    raise typer.Exit(2)


def _parse_dims(values: list[str]) -> dict[str, int]:
    """Parse repeated ``--dim name=value`` options.

    Args:
        values (list[str]): Raw ``name=value`` strings.

    Returns:
        dict[str, int]: Dimension name to concrete size.

    Raises:
        typer.BadParameter: When an entry is malformed.
    """
    parsed: dict[str, int] = {}
    for entry in values:
        name, separator, raw = entry.partition("=")
        if not separator or not name.strip():
            raise typer.BadParameter(f"--dim must be 'name=value', got: {entry}")
        try:
            parsed[name.strip()] = int(raw)
        except ValueError as exc:
            raise typer.BadParameter(
                f"--dim value must be an integer, got: {raw!r}"
            ) from exc
    return parsed


def _echo_json(payload: Any) -> None:
    """Print a Pydantic model or plain object as indented JSON.

    Args:
        payload (Any): A Pydantic model, or anything ``json.dumps`` accepts.
    """
    if hasattr(payload, "model_dump_json"):
        typer.echo(payload.model_dump_json(indent=2))
        return
    typer.echo(json.dumps(payload, indent=2, default=str))


def _format_optional(value: float | None, suffix: str = "", digits: int = 2) -> str:
    """Render a measurement that may not have been taken.

    Args:
        value (float | None): The measured value.
        suffix (str): Unit appended after the number.
        digits (int): Decimal places.

    Returns:
        str: The formatted value, or ``"-"`` when it is ``None``.
    """
    if value is None:
        return "-"
    return f"{value:.{digits}f}{suffix}"


@model_app.command("analyze")
def analyze_cmd(
    model: str = typer.Argument(..., help="Path to a .onnx or .ort file."),
    as_json: bool = typer.Option(
        False, "--json", help="Print the full report as JSON."
    ),
) -> None:
    """Report parameters, size, opset and tensor shapes without running it.

    Reads the artifact only — no inference, no warm-up — so it is instant
    and gives identical numbers on any machine.
    """
    from tempest_fastapi_sdk.modelops import analyze_model

    try:
        metrics = analyze_model(model)
    except (ImportError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if as_json:
        _echo_json(metrics)
        return

    typer.secho(f"{metrics.name}  [{metrics.format}]", fg="cyan", bold=True)
    typer.echo(f"  parameters : {metrics.n_parameters:,}")
    typer.echo(f"  disk size  : {metrics.disk_size_mb:.2f} MB")
    typer.echo(f"  opset      : {metrics.opset if metrics.opset else '-'}")
    typer.echo(f"  producer   : {metrics.producer or '-'}")
    for spec in metrics.inputs:
        typer.echo(f"  input      : {spec.name} {spec.shape}")
    for spec in metrics.outputs:
        typer.echo(f"  output     : {spec.name} {spec.shape}")


@model_app.command("bench")
def bench_cmd(
    model: str = typer.Argument(..., help="Path to a .onnx or .ort file."),
    repetitions: int = typer.Option(
        50, "--repetitions", "-n", min=1, help="Number of timed calls."
    ),
    warmup: int = typer.Option(
        10, "--warmup", "-w", min=0, help="Warm-up calls discarded before timing."
    ),
    batch_size: int = typer.Option(
        1, "--batch-size", "-b", min=1, help="Value for an unnamed leading dimension."
    ),
    dim: list[str] = typer.Option(
        [],
        "--dim",
        "-d",
        help="Resolve a symbolic dimension; repeatable, e.g. --dim height=224.",
    ),
    provider: list[str] = typer.Option(
        [],
        "--provider",
        "-p",
        help="Execution provider; repeatable, in priority order.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print the full report as JSON."
    ),
) -> None:
    """Measure latency, memory and energy over repeated inferences.

    Inputs are synthesized from the declared shapes, so the latency measured
    is the shape-dependent cost, not the content-dependent one. For a model
    whose work varies with its input — a detector, an autoregressive
    decoder — drive it from Python with real ``feeds`` instead.
    """
    from tempest_fastapi_sdk.modelops import benchmark_onnx

    try:
        profile = benchmark_onnx(
            model,
            n_warmup=warmup,
            n_repetitions=repetitions,
            batch_size=batch_size,
            dynamic_dims=_parse_dims(dim),
            providers=list(provider) or None,
        )
    except (ImportError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if as_json:
        _echo_json(profile)
        return

    runtime = profile.runtime
    typer.secho(
        f"{profile.name}  [{runtime.device} / {runtime.provider or 'default'}]",
        fg="cyan",
        bold=True,
    )
    typer.echo(
        f"  latency ms : median {runtime.latency_ms_median:.3f}  "
        f"iqr {runtime.latency_ms_iqr:.3f}  "
        f"p95 {runtime.latency_ms_p95:.3f}  p99 {runtime.latency_ms_p99:.3f}"
    )
    typer.echo(
        f"  throughput : {runtime.throughput_per_s:.1f}/s  "
        f"({runtime.n_repetitions} reps, {runtime.n_warmup} warm-up, "
        f"batch {runtime.batch_size})"
    )
    typer.echo(
        f"  memory     : rss peak {_format_optional(runtime.rss_peak_mb, ' MB')}  "
        f"gpu peak {_format_optional(runtime.gpu_memory_peak_mb, ' MB')}"
    )
    typer.echo(
        f"  energy     : "
        f"{_format_optional(runtime.energy_per_inference_j, ' J/inference', 4)}  "
        f"({runtime.energy_source})"
    )
    if profile.static is not None:
        typer.echo(
            f"  static     : {profile.static.n_parameters:,} params  "
            f"{profile.static.disk_size_mb:.2f} MB"
        )


@model_app.command("export-ort")
def export_ort_cmd(
    model: str = typer.Argument(..., help="A .onnx file, or a directory of them."),
    output_dir: str | None = typer.Option(
        None, "--output-dir", "-o", help="Where to write. Defaults to alongside."
    ),
    style: str = typer.Option(
        "fixed",
        "--style",
        "-s",
        help="'fixed' bakes optimizations in; 'runtime' keeps them deferrable.",
    ),
    target_platform: str | None = typer.Option(
        None,
        "--target-platform",
        "-t",
        help="'amd64' or 'arm' — restrict to optimizations valid there.",
    ),
    type_reduction: bool = typer.Option(
        False,
        "--type-reduction/--no-type-reduction",
        help="Also record required data types, for a minimal runtime build.",
    ),
) -> None:
    """Convert ONNX models to the ``.ort`` format used on mobile and edge.

    Writes the ``.required_operators.config`` next to each output; a minimal
    ONNX Runtime build reads it to compile only the kernels these models
    need.
    """
    from tempest_fastapi_sdk.modelops import OrtOptimizationStyle, export_onnx_to_ort

    try:
        results = export_onnx_to_ort(
            model,
            output_dir,
            optimization_style=OrtOptimizationStyle(style),
            target_platform=target_platform,
            enable_type_reduction=type_reduction,
        )
    except (ImportError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if not results:
        typer.secho("no .ort files were produced", fg="yellow", err=True)
        raise typer.Exit(1)
    for result in results:
        typer.secho(f"wrote {result.output_path}", fg="green")
        typer.echo(
            f"  {result.source_size_mb:.2f} MB -> "
            f"{result.output_size_mb:.2f} MB  "
            f"(x{result.size_ratio:.2f})"
        )
        for extra in result.extra_files:
            typer.echo(f"  config: {extra}")


@model_app.command("optimize")
def optimize_cmd(
    model: str = typer.Argument(..., help="Path to the .onnx file to optimize."),
    output: str = typer.Argument(..., help="Where to write the optimized .onnx."),
    level: str = typer.Option(
        "all",
        "--level",
        "-l",
        help="disable_all, basic, extended, layout or all.",
    ),
) -> None:
    """Persist ONNX Runtime's graph optimizations to a new file.

    Fusion and constant folding only — the numerics are untouched. The
    result is specialized for the providers available on this machine, so
    optimize on a host that matches the deployment target.
    """
    from tempest_fastapi_sdk.modelops import GraphOptimizationLevel, optimize_onnx_graph

    try:
        result = optimize_onnx_graph(model, output, level=GraphOptimizationLevel(level))
    except (ImportError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    typer.secho(f"wrote {result.output_path}", fg="green")
    typer.echo(
        f"  {result.source_size_mb:.2f} MB -> {result.output_size_mb:.2f} MB  "
        f"(x{result.size_ratio:.2f})"
    )


@model_app.command("quantize")
def quantize_cmd(
    model: str = typer.Argument(..., help="Path to the .onnx file to quantize."),
    output: str = typer.Argument(..., help="Where to write the quantized .onnx."),
    weight_type: str = typer.Option(
        "int8", "--weight-type", "-t", help="int8, uint8, int16, uint16, int4, uint4."
    ),
    per_channel: bool = typer.Option(
        False,
        "--per-channel/--per-tensor",
        help="Quantize convolution weights per output channel.",
    ),
    reduce_range: bool = typer.Option(
        False,
        "--reduce-range/--full-range",
        help="Use 7 bits, avoiding int8 overflow on pre-VNNI x86 CPUs.",
    ),
) -> None:
    """Quantize weights to integers, with no calibration data.

    This is lossy. Re-run your evaluation set on the output before shipping
    it — how much accuracy int8 costs is a property of your model, and
    nothing here can predict it.
    """
    from tempest_fastapi_sdk.modelops import QuantWeightType, quantize_onnx_dynamic

    try:
        result = quantize_onnx_dynamic(
            model,
            output,
            weight_type=QuantWeightType(weight_type),
            per_channel=per_channel,
            reduce_range=reduce_range,
        )
    except (ImportError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    typer.secho(f"wrote {result.output_path}", fg="green")
    typer.echo(
        f"  {result.source_size_mb:.2f} MB -> {result.output_size_mb:.2f} MB  "
        f"(x{result.compression_ratio:.2f} smaller, {result.backend})"
    )
    typer.secho(
        "  re-measure accuracy before shipping a quantized model",
        fg="yellow",
    )


@model_app.command("hardware")
def hardware_cmd(
    as_json: bool = typer.Option(
        False, "--json", help="Print the full report as JSON."
    ),
) -> None:
    """Report what this host can run and what it can measure.

    Run it before trusting an energy number: it says whether NVML, the
    ``nvidia-smi`` fallback or the Intel RAPL counters are actually
    readable here, and every one of those can be missing for a reason that
    has nothing to do with the hardware — no driver in WSL2, no powercap in
    a container, root-only permissions on ``energy_uj``.
    """
    from tempest_fastapi_sdk.genai import probe_hardware
    from tempest_fastapi_sdk.modelops import (
        resolve_cpu_energy_sampler,
        resolve_power_sampler,
    )

    info = probe_hardware()
    gpu_sampler = resolve_power_sampler()
    cpu_sampler = resolve_cpu_energy_sampler()
    payload = {
        "hardware": info.model_dump(mode="json"),
        "gpu_energy_sampler": type(gpu_sampler).__name__,
        "gpu_energy_available": gpu_sampler.available,
        "cpu_energy_sampler": type(cpu_sampler).__name__,
        "cpu_energy_available": cpu_sampler.available,
    }
    if as_json:
        _echo_json(payload)
        return

    typer.secho("hardware", fg="cyan", bold=True)
    typer.echo(f"  cpu cores  : {info.cpu_cores}")
    typer.echo(f"  ram total  : {info.ram_total_bytes / 10**9:.1f} GB")
    typer.echo(f"  cuda       : {info.has_cuda}")
    for gpu in info.gpus:
        typer.echo(
            f"  gpu {gpu.index}      : {gpu.name}  "
            f"{gpu.vram_total_bytes / 10**9:.1f} GB"
        )
    typer.secho("energy measurement", fg="cyan", bold=True)
    typer.echo(
        f"  gpu        : {type(gpu_sampler).__name__} "
        f"({'available' if gpu_sampler.available else 'unavailable'})"
    )
    typer.echo(
        f"  cpu        : {type(cpu_sampler).__name__} "
        f"({'available' if cpu_sampler.available else 'unavailable'})"
    )


__all__: list[str] = ["model_app"]
