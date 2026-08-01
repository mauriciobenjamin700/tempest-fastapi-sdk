"""Latency, memory and energy benchmarking for models — and for anything.

The core is :func:`benchmark`, which times a zero-argument callable. Every
other entry point here builds that callable and hands it over, which is why
an ONNX session, a torch module and a hand-written closure all produce the
same :class:`~tempest_fastapi_sdk.modelops.BenchmarkProfile`.

Three things the loop does that a naive ``time.perf_counter`` around a call
does not, and that make the difference between a number you can publish and
a number you cannot:

* **Warm-up.** The first calls pay for lazy kernel selection, allocator
  growth and cuDNN autotuning. They are run and discarded.
* **Repetitions with dispersion.** Latency is heavy-tailed, so the report
  leads with median plus IQR and keeps p95/p99 — a mean alone hides the
  tail that your p99 SLO actually cares about.
* **Energy alongside time.** A GPU and a CPU sampler run for the duration of
  the timed window, so "how fast" and "how much power" come out of the same
  measurement instead of two unrelated runs.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.modelops.energy import (
    NullPowerSampler,
    PowerSampler,
    resolve_cpu_energy_sampler,
    resolve_power_sampler,
)
from tempest_fastapi_sdk.modelops.schemas import (
    BenchmarkProfile,
    BenchmarkReport,
    EnergySource,
    RuntimeAggregate,
    RuntimeSample,
    StaticModelMetrics,
)
from tempest_fastapi_sdk.modelops.static import (
    _require_onnxruntime,
    _require_torch,
    analyze_model,
    analyze_torch,
    default_providers,
)

DEFAULT_WARMUP: int = 10
"""Warm-up calls discarded before timing starts."""

DEFAULT_REPETITIONS: int = 50
"""Timed calls. Enough for a stable median without making a CI job crawl."""

_ORT_DTYPES: dict[str, str] = {
    "tensor(float)": "float32",
    "tensor(float16)": "float16",
    "tensor(double)": "float64",
    "tensor(bfloat16)": "float32",
    "tensor(int64)": "int64",
    "tensor(int32)": "int32",
    "tensor(int16)": "int16",
    "tensor(int8)": "int8",
    "tensor(uint8)": "uint8",
    "tensor(bool)": "bool",
}
"""ONNX Runtime type strings mapped to the numpy dtype to synthesize."""


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Return a linearly interpolated percentile of ``values``.

    Implemented here rather than pulled from numpy so the core benchmark
    keeps working with only the ``[modelops]`` extra installed.

    Args:
        values (Sequence[float]): Sample values; need not be sorted.
        fraction (float): Percentile as a fraction, e.g. ``0.95``.

    Returns:
        float: The interpolated percentile. Returns the single value for a
        one-element input.
    """
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _psutil_process() -> Any:
    """Return a ``psutil.Process`` for this process, or ``None``.

    Returns:
        Any: The process handle, or ``None`` when psutil is unavailable —
        memory and CPU fields then stay ``None`` instead of raising.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        return psutil.Process()
    except Exception:  # pragma: no cover - platform-specific psutil failure
        return None


def benchmark(
    call: Callable[[], Any],
    *,
    name: str = "callable",
    n_warmup: int = DEFAULT_WARMUP,
    n_repetitions: int = DEFAULT_REPETITIONS,
    batch_size: int = 1,
    device: str = "cpu",
    provider: str | None = None,
    static: StaticModelMetrics | None = None,
    power_sampler: PowerSampler | None = None,
    cpu_energy_sampler: PowerSampler | None = None,
    sync: Callable[[], None] | None = None,
    memory_probe: Callable[[], float | None] | None = None,
    keep_samples: bool = False,
) -> BenchmarkProfile:
    """Time a callable and report latency, memory and energy.

    Args:
        call (Callable[[], Any]): Zero-argument callable performing exactly
            one unit of work. Build the inputs *outside* it — anything done
            inside is measured as part of the model.
        name (str): Display name for the profile.
        n_warmup (int): Calls to run and discard before timing.
        n_repetitions (int): Timed calls. Must be at least 1.
        batch_size (int): Items processed per call; only used to turn
            latency into throughput.
        device (str): Device label recorded in the report.
        provider (str | None): Execution provider label, for ONNX Runtime.
        static (StaticModelMetrics | None): Static metrics to attach.
        power_sampler (PowerSampler | None): GPU sampler. By default one
            is resolved only when ``device`` is a CUDA device: attributing
            a shared card's idle draw and other processes' VRAM to a model
            running on the CPU would be worse than reporting nothing. Pass
            one explicitly to measure the GPU during a CPU run anyway.
        cpu_energy_sampler (PowerSampler | None): CPU energy sampler.
            Defaults to
            :func:`~tempest_fastapi_sdk.modelops.resolve_cpu_energy_sampler`.
        sync (Callable[[], None] | None): Called before starting and after
            stopping each timer. Required for asynchronous backends —
            without ``torch.cuda.synchronize`` a CUDA benchmark measures
            kernel *launch* time, which is roughly zero and entirely wrong.
        memory_probe (Callable[[], float | None] | None): Returns device
            memory in MB for the current call.
        keep_samples (bool): Keep the per-repetition samples on the profile.
            Off by default: 50 rows per model is noise in a summary and
            weight in a JSON response.

    Returns:
        BenchmarkProfile: Aggregate over the timed calls, plus the raw
        samples when ``keep_samples`` is set.

    Raises:
        ValueError: When ``n_repetitions`` is below 1.

    Example:

        >>> from tempest_fastapi_sdk.modelops import benchmark
        >>> profile = benchmark(
        ...     lambda: sum(range(10_000)),
        ...     name="sum",
        ...     n_warmup=2,
        ...     n_repetitions=5,
        ... )
        >>> profile.runtime.n_repetitions
        5
    """
    if n_repetitions < 1:
        raise ValueError("n_repetitions must be at least 1")

    if power_sampler is not None:
        gpu_sampler: PowerSampler = power_sampler
    elif device.startswith("cuda"):
        gpu_sampler = resolve_power_sampler()
    else:
        gpu_sampler = NullPowerSampler()
    cpu_sampler = (
        cpu_energy_sampler
        if cpu_energy_sampler is not None
        else resolve_cpu_energy_sampler()
    )
    process = _psutil_process()
    rss_before = _rss_mb(process)

    for _ in range(max(n_warmup, 0)):
        call()
    if sync is not None:
        sync()
    if process is not None:
        process.cpu_percent(interval=None)

    gpu_sampler.start()
    cpu_sampler.start()
    samples: list[RuntimeSample] = []
    for index in range(n_repetitions):
        if sync is not None:
            sync()
        started = time.perf_counter()
        call()
        if sync is not None:
            sync()
        latency_ms = (time.perf_counter() - started) * 1000.0
        samples.append(
            RuntimeSample(
                index=index,
                latency_ms=latency_ms,
                rss_mb=_rss_mb(process),
                cpu_percent=(
                    float(process.cpu_percent(interval=None))
                    if process is not None
                    else None
                ),
                gpu_memory_mb=memory_probe() if memory_probe is not None else None,
                gpu_power_w=gpu_sampler.latest_power_w(),
            )
        )
    cpu_sampler.stop()
    gpu_sampler.stop()

    aggregate = _aggregate(
        samples,
        device=device,
        provider=provider,
        n_warmup=max(n_warmup, 0),
        batch_size=batch_size,
        rss_before=rss_before,
        gpu_sampler=gpu_sampler,
        cpu_sampler=cpu_sampler,
    )
    return BenchmarkProfile(
        name=name,
        runtime=aggregate,
        static=static,
        samples=samples if keep_samples else [],
    )


def _rss_mb(process: Any) -> float | None:
    """Read the process resident set size in MB.

    Args:
        process (Any): A ``psutil.Process`` or ``None``.

    Returns:
        float | None: RSS in MB, or ``None`` without psutil.
    """
    if process is None:
        return None
    try:
        return float(process.memory_info().rss) / 1024.0**2
    except Exception:  # pragma: no cover - process vanished mid-read
        return None


def _aggregate(
    samples: list[RuntimeSample],
    *,
    device: str,
    provider: str | None,
    n_warmup: int,
    batch_size: int,
    rss_before: float | None,
    gpu_sampler: PowerSampler,
    cpu_sampler: PowerSampler,
) -> RuntimeAggregate:
    """Reduce the raw samples and the two energy readings into one row.

    When both a GPU and a CPU reading are present their energies are summed
    into ``energy_per_inference_j`` and ``energy_source`` names the GPU
    source, since that is the dominant term for accelerator inference. Read
    ``gpu_energy_j`` and ``cpu_energy_j`` when the split matters.

    GPU memory prefers the per-process probe when the caller supplied one,
    and only falls back to the sampler's reading — which is device-wide,
    counting every process on the card — when it did not.

    Args:
        samples (list[RuntimeSample]): Per-repetition samples.
        device (str): Device label.
        provider (str | None): Execution provider label.
        n_warmup (int): Warm-up count, recorded for reproducibility.
        batch_size (int): Items per call.
        rss_before (float | None): RSS captured before warm-up.
        gpu_sampler (PowerSampler): The GPU sampler, already stopped.
        cpu_sampler (PowerSampler): The CPU sampler, already stopped.

    Returns:
        RuntimeAggregate: The summarized run.
    """
    latencies = [sample.latency_ms for sample in samples]
    mean = statistics.fmean(latencies)
    rss_values = [s.rss_mb for s in samples if s.rss_mb is not None]
    cpu_values = [s.cpu_percent for s in samples if s.cpu_percent is not None]
    gpu_memory_values = [
        s.gpu_memory_mb for s in samples if s.gpu_memory_mb is not None
    ]

    gpu_reading = gpu_sampler.reading()
    cpu_reading = cpu_sampler.reading()
    energies = [
        value
        for value in (gpu_reading.energy_j, cpu_reading.energy_j)
        if value is not None
    ]
    total_energy = sum(energies) if energies else None
    if gpu_reading.energy_j is not None:
        energy_source = gpu_reading.source
    elif cpu_reading.energy_j is not None:
        energy_source = cpu_reading.source
    else:
        energy_source = EnergySource.UNAVAILABLE

    gpu_memory_peak: float | None = (
        max(gpu_memory_values) if gpu_memory_values else gpu_sampler.peak_memory_mb()
    )

    return RuntimeAggregate(
        device=device,
        provider=provider,
        n_warmup=n_warmup,
        n_repetitions=len(samples),
        batch_size=batch_size,
        latency_ms_median=statistics.median(latencies),
        latency_ms_iqr=_percentile(latencies, 0.75) - _percentile(latencies, 0.25),
        latency_ms_p95=_percentile(latencies, 0.95),
        latency_ms_p99=_percentile(latencies, 0.99),
        latency_ms_mean=mean,
        latency_ms_std=(statistics.stdev(latencies) if len(latencies) > 1 else 0.0),
        latency_ms_min=min(latencies),
        latency_ms_max=max(latencies),
        throughput_per_s=(batch_size * 1000.0 / mean) if mean > 0 else 0.0,
        rss_peak_mb=max(rss_values) if rss_values else None,
        rss_delta_mb=(
            rss_values[-1] - rss_before
            if rss_values and rss_before is not None
            else None
        ),
        cpu_percent_mean=statistics.fmean(cpu_values) if cpu_values else None,
        gpu_memory_peak_mb=gpu_memory_peak,
        gpu_power_mean_w=gpu_reading.mean_power_w,
        gpu_energy_j=gpu_reading.energy_j,
        cpu_energy_j=cpu_reading.energy_j,
        energy_per_inference_j=(
            total_energy / len(samples) if total_energy is not None else None
        ),
        energy_source=energy_source,
    )


def _resolve_shape(
    spec_name: str,
    declared: Sequence[Any],
    *,
    batch_size: int,
    dynamic_dims: Mapping[str, int],
) -> list[int]:
    """Turn a possibly symbolic ONNX shape into concrete dimensions.

    A symbolic dimension is resolved from ``dynamic_dims`` by name; the
    leading dimension falls back to ``batch_size`` because that is what it
    is in every real model. Anything still unresolved raises rather than
    guessing — feeding a 1x1 image to a convolutional model produces a
    latency figure that is confidently wrong.

    Args:
        spec_name (str): Input name, used in the error message.
        declared (Sequence[Any]): Shape as ONNX Runtime reports it.
        batch_size (int): Value for an unnamed leading dimension.
        dynamic_dims (Mapping[str, int]): Values for symbolic dimensions.

    Returns:
        list[int]: Fully resolved shape.

    Raises:
        ValueError: When a symbolic dimension has no value.
    """
    resolved: list[int] = []
    for position, dimension in enumerate(declared):
        if isinstance(dimension, int) and dimension > 0:
            resolved.append(dimension)
            continue
        key = dimension if isinstance(dimension, str) else ""
        if key and key in dynamic_dims:
            resolved.append(int(dynamic_dims[key]))
            continue
        if position == 0:
            resolved.append(batch_size)
            continue
        raise ValueError(
            f"input {spec_name!r} has an unresolved dimension "
            f"{dimension!r} at position {position}. Pass it via "
            f"dynamic_dims={{{key or 'dim_name'!r}: <value>}} or give the "
            "whole shape via input_shapes."
        )
    return resolved


def _random_feeds(
    session: Any,
    *,
    batch_size: int,
    dynamic_dims: Mapping[str, int],
    input_shapes: Mapping[str, Sequence[int]],
    seed: int,
) -> dict[str, Any]:
    """Synthesize one input dict for an ONNX Runtime session.

    Float tensors get uniform noise in ``[0, 1)``; integer and boolean
    tensors get zeros, because a random ``int64`` would be read as a token
    id far outside any vocabulary and crash the gather.

    Args:
        session (Any): An ``onnxruntime.InferenceSession``.
        batch_size (int): Value for unnamed leading dimensions.
        dynamic_dims (Mapping[str, int]): Values for symbolic dimensions.
        input_shapes (Mapping[str, Sequence[int]]): Explicit per-input
            shapes, bypassing resolution entirely.
        seed (int): Seed, so repeated runs feed identical data.

    Returns:
        dict[str, Any]: Feed dict ready for ``session.run``.

    Raises:
        ValueError: When a dimension cannot be resolved.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    feeds: dict[str, Any] = {}
    for spec in session.get_inputs():
        if spec.name in input_shapes:
            shape = [int(dim) for dim in input_shapes[spec.name]]
        else:
            shape = _resolve_shape(
                spec.name,
                spec.shape,
                batch_size=batch_size,
                dynamic_dims=dynamic_dims,
            )
        dtype = _ORT_DTYPES.get(str(spec.type), "float32")
        if dtype.startswith("float"):
            feeds[spec.name] = rng.random(size=shape, dtype="float32").astype(dtype)
        else:
            feeds[spec.name] = np.zeros(shape, dtype=dtype)
    return feeds


def benchmark_onnx(
    model_path: str | Path,
    *,
    name: str | None = None,
    providers: list[str] | None = None,
    feeds: Mapping[str, Any] | None = None,
    input_shapes: Mapping[str, Sequence[int]] | None = None,
    dynamic_dims: Mapping[str, int] | None = None,
    batch_size: int = 1,
    n_warmup: int = DEFAULT_WARMUP,
    n_repetitions: int = DEFAULT_REPETITIONS,
    keep_samples: bool = False,
    seed: int = 0,
    power_sampler: PowerSampler | None = None,
    cpu_energy_sampler: PowerSampler | None = None,
) -> BenchmarkProfile:
    """Benchmark an ``.onnx`` or ``.ort`` model through ONNX Runtime.

    Args:
        model_path (str | Path): Model to load.
        name (str | None): Display name. Defaults to the file stem.
        providers (list[str] | None): Execution providers, in preference
            order. Defaults to whatever the runtime offers.
        feeds (Mapping[str, Any] | None): Real inputs to run. Strongly
            preferred over synthetic data when latency depends on content
            (sequence length, number of detections). Synthesized when
            omitted.
        input_shapes (Mapping[str, Sequence[int]] | None): Explicit shape
            per input, for synthesized feeds.
        dynamic_dims (Mapping[str, int] | None): Value for each symbolic
            dimension, e.g. ``{"height": 224, "width": 224}``.
        batch_size (int): Value used for an unnamed leading dimension, and
            the divisor for throughput.
        n_warmup (int): Warm-up calls discarded.
        n_repetitions (int): Timed calls.
        keep_samples (bool): Keep per-repetition samples.
        seed (int): Seed for synthesized inputs.
        power_sampler (PowerSampler | None): GPU sampler override.
        cpu_energy_sampler (PowerSampler | None): CPU sampler override.

    Returns:
        BenchmarkProfile: Runtime aggregate plus static metrics when the
        artifact could be analyzed.

    Raises:
        ImportError: When the ``[modelops-onnx]`` extra is missing.
        FileNotFoundError: When the model does not exist.
        ValueError: When a symbolic dimension cannot be resolved.

    Example:

        >>> from tempest_fastapi_sdk.modelops import benchmark_onnx
        >>> profile = benchmark_onnx(
        ...     "models/classify.onnx",
        ...     dynamic_dims={"height": 224, "width": 224},
        ...     n_repetitions=20,
        ... )
        >>> profile.runtime.latency_ms_median
    """
    onnxruntime = _require_onnxruntime()
    path = Path(model_path)
    if not path.is_file():
        raise FileNotFoundError(f"model not found: {path}")

    session = onnxruntime.InferenceSession(
        str(path),
        providers=providers or default_providers(onnxruntime),
    )
    active = list(session.get_providers())
    provider = active[0] if active else None
    resolved_feeds = (
        dict(feeds)
        if feeds is not None
        else _random_feeds(
            session,
            batch_size=batch_size,
            dynamic_dims=dynamic_dims or {},
            input_shapes=input_shapes or {},
            seed=seed,
        )
    )
    output_names = [spec.name for spec in session.get_outputs()]

    static: StaticModelMetrics | None
    try:
        static = analyze_model(path, name=name)
    except (ImportError, ValueError, FileNotFoundError):
        static = None

    return benchmark(
        lambda: session.run(output_names, resolved_feeds),
        name=name or path.stem,
        n_warmup=n_warmup,
        n_repetitions=n_repetitions,
        batch_size=batch_size,
        device="cuda" if provider and "CUDA" in provider else "cpu",
        provider=provider,
        static=static,
        power_sampler=power_sampler,
        cpu_energy_sampler=cpu_energy_sampler,
        keep_samples=keep_samples,
    )


def benchmark_torch(
    module: Any,
    example_input: Any,
    *,
    name: str | None = None,
    device: str | None = None,
    batch_size: int = 1,
    n_warmup: int = DEFAULT_WARMUP,
    n_repetitions: int = DEFAULT_REPETITIONS,
    keep_samples: bool = False,
    collect_static: bool = True,
    power_sampler: PowerSampler | None = None,
    cpu_energy_sampler: PowerSampler | None = None,
) -> BenchmarkProfile:
    """Benchmark a ``torch.nn.Module`` forward pass.

    The module is switched to ``eval()`` and run under ``torch.no_grad()``,
    and on CUDA every timer is bracketed by ``torch.cuda.synchronize()``.
    Both inputs and module are moved to the target device before warm-up, so
    the timed window contains no host-to-device copy.

    Args:
        module (Any): The module to benchmark.
        example_input (Any): A tensor, or a tuple of tensors, to feed it.
        name (str | None): Display name. Defaults to the class name.
        device (str | None): ``"cuda"`` / ``"cpu"``. Auto-detected when
            omitted.
        batch_size (int): Items per call, for the throughput figure.
        n_warmup (int): Warm-up calls discarded.
        n_repetitions (int): Timed calls.
        keep_samples (bool): Keep per-repetition samples.
        collect_static (bool): Also count parameters and FLOPs. The FLOP
            count runs one extra forward pass.
        power_sampler (PowerSampler | None): GPU sampler override.
        cpu_energy_sampler (PowerSampler | None): CPU sampler override.

    Returns:
        BenchmarkProfile: Runtime aggregate, plus static metrics when
        ``collect_static`` is set.

    Raises:
        ImportError: When torch is not installed.

    Example:

        >>> import torch
        >>> from tempest_fastapi_sdk.modelops import benchmark_torch
        >>> profile = benchmark_torch(
        ...     torch.nn.Linear(128, 10),
        ...     torch.randn(1, 128),
        ...     n_warmup=2,
        ...     n_repetitions=5,
        ... )
        >>> profile.runtime.n_repetitions
        5
    """
    torch = _require_torch()
    target = device or ("cuda" if torch.cuda.is_available() else "cpu")
    module = module.to(target)
    module.eval()

    args = example_input if isinstance(example_input, tuple) else (example_input,)
    args = tuple(item.to(target) if hasattr(item, "to") else item for item in args)

    static = (
        analyze_torch(module, name=name, example_input=args) if collect_static else None
    )

    def run() -> Any:
        """Run one forward pass without building a graph."""
        with torch.no_grad():
            return module(*args)

    def cuda_memory() -> float | None:
        """Return currently allocated CUDA memory in MB."""
        return float(torch.cuda.memory_allocated()) / 1024.0**2

    sync: Callable[[], None] | None = None
    memory_probe: Callable[[], float | None] | None = None
    if target.startswith("cuda"):  # pragma: no cover - needs a real GPU
        sync = torch.cuda.synchronize
        memory_probe = cuda_memory
        torch.cuda.reset_peak_memory_stats()

    return benchmark(
        run,
        name=name or type(module).__name__,
        n_warmup=n_warmup,
        n_repetitions=n_repetitions,
        batch_size=batch_size,
        device=target,
        static=static,
        sync=sync,
        memory_probe=memory_probe,
        power_sampler=power_sampler,
        cpu_energy_sampler=cpu_energy_sampler,
        keep_samples=keep_samples,
    )


def benchmark_models(
    model_paths: Sequence[str | Path],
    *,
    quality: Mapping[str, float] | None = None,
    weights: Mapping[str, float] | None = None,
    share_samplers: bool = True,
    **kwargs: Any,
) -> BenchmarkReport:
    """Benchmark several ONNX/ORT models and rank them against each other.

    Every model is measured with the same settings and the same samplers,
    which is the only way the comparison means anything.

    Args:
        model_paths (Sequence[str | Path]): Models to compare.
        quality (Mapping[str, float] | None): Model name to task quality
            (accuracy, F1, mAP…). Needed for the Pareto frontier — cost
            alone cannot say which model is *better*.
        weights (Mapping[str, float] | None): Composite-score weights. See
            :data:`~tempest_fastapi_sdk.modelops.DEFAULT_COST_WEIGHTS`.
        share_samplers (bool): Reuse one CPU energy sampler across all
            models instead of re-scanning the powercap tree for each. The
            GPU sampler is still chosen per model from the device it ran
            on.
        **kwargs (Any): Forwarded to :func:`benchmark_onnx`.

    Returns:
        BenchmarkReport: Ranked profiles, Pareto annotation, the effective
        weights and the host description.

    Example:

        >>> from tempest_fastapi_sdk.modelops import benchmark_models
        >>> report = benchmark_models(
        ...     ["models/n.onnx", "models/s.onnx"],
        ...     quality={"n": 0.80, "s": 0.84},
        ...     n_repetitions=20,
        ... )
        >>> [p.name for p in report.profiles]
    """
    from tempest_fastapi_sdk.modelops.ranking import rank

    if share_samplers:
        kwargs.setdefault("cpu_energy_sampler", resolve_cpu_energy_sampler())

    profiles = [benchmark_onnx(path, **kwargs) for path in model_paths]
    return rank(profiles, weights=weights, quality=quality)


__all__: list[str] = [
    "DEFAULT_REPETITIONS",
    "DEFAULT_WARMUP",
    "benchmark",
    "benchmark_models",
    "benchmark_onnx",
    "benchmark_torch",
]
