"""Typed schemas and enums for the model-ops module.

Everything here is plain Pydantic + :class:`~tempest_fastapi_sdk.core.BaseStrEnum`,
so this module imports with **no** optional extra installed. The heavy
dependencies (``onnx``, ``onnxruntime``, ``optimum``, ``psutil``, ``pynvml``)
are only touched by the functions that actually export, quantize or measure.

Unavailable measurements are modelled as ``None``, never as ``NaN``:
``NaN`` is not valid JSON, so a report containing it cannot be returned from
an endpoint or written with ``model_dump_json()``. Consumers therefore branch
on ``is None`` rather than on ``math.isnan``.
"""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk.core import BaseStrEnum
from tempest_fastapi_sdk.genai.schemas import HardwareInfo
from tempest_fastapi_sdk.schemas.base import BaseSchema


class ModelFormat(BaseStrEnum):
    """On-disk format of a model artifact.

    * ``TORCH`` — a PyTorch checkpoint (``.pt`` / ``.pth``) or a live
      ``torch.nn.Module``.
    * ``ONNX`` — an ONNX graph (``.onnx``), the interchange format.
    * ``ORT`` — an ONNX Runtime *format* file (``.ort``): the graph already
      optimized and serialized for the minimal mobile/edge runtime build.
    """

    TORCH = "torch"
    ONNX = "onnx"
    ORT = "ort"


class OrtOptimizationStyle(BaseStrEnum):
    """Optimization style applied when converting ``.onnx`` to ``.ort``.

    * ``FIXED`` — bake the optimizations in at conversion time. Smallest and
      fastest at load, but the file is tied to the operator set that the
      conversion machine resolved. This is the edge/mobile default.
    * ``RUNTIME`` — keep the graph closer to the original so the runtime can
      re-optimize for the device it actually lands on. Slightly slower to
      load, more portable across execution providers.
    """

    FIXED = "fixed"
    RUNTIME = "runtime"


class GraphOptimizationLevel(BaseStrEnum):
    """ONNX Runtime graph-optimization level.

    Mirrors ``onnxruntime.GraphOptimizationLevel``. ``ALL`` is the usual
    choice when writing an optimized ``.onnx`` back to disk; the lower levels
    exist to isolate a regression introduced by a fusion.
    """

    DISABLE_ALL = "disable_all"
    BASIC = "basic"
    EXTENDED = "extended"
    LAYOUT = "layout"
    ALL = "all"


class QuantWeightType(BaseStrEnum):
    """Integer/float type the weights are quantized to.

    Names map 1:1 onto ``onnxruntime.quantization.QuantType`` members. The
    exotic ones (``INT4`` / ``UINT4`` / ``FLOAT8E4M3FN``) only exist on newer
    ONNX Runtime builds — asking for one the installed runtime does not have
    raises a clear error instead of silently falling back.
    """

    INT8 = "int8"
    UINT8 = "uint8"
    INT16 = "int16"
    UINT16 = "uint16"
    INT4 = "int4"
    UINT4 = "uint4"
    FLOAT8E4M3FN = "float8e4m3fn"


class QuantizationFormat(BaseStrEnum):
    """How quantized operators are represented in the graph.

    * ``QDQ`` — insert explicit ``QuantizeLinear`` / ``DequantizeLinear``
      pairs. Portable, and what most execution providers expect.
    * ``QOPERATOR`` — use the fused quantized operators directly
      (``QLinearConv``, ``MatMulInteger``, …). Smaller graph, narrower
      provider support.
    """

    QDQ = "qdq"
    QOPERATOR = "qoperator"


class CalibrationMethod(BaseStrEnum):
    """Algorithm used to pick activation ranges during static quantization.

    ``MINMAX`` is the safe default. ``ENTROPY`` / ``PERCENTILE`` trade a
    little accuracy on outliers for a tighter range, which usually recovers
    accuracy on models that ``MINMAX`` degrades.
    """

    MINMAX = "minmax"
    ENTROPY = "entropy"
    PERCENTILE = "percentile"
    DISTRIBUTION = "distribution"


class QuantizationBackend(BaseStrEnum):
    """Which engine produced a quantized artifact.

    * ``ONNXRUNTIME_DYNAMIC`` — weights quantized ahead of time, activations
      quantized on the fly. No calibration data needed.
    * ``ONNXRUNTIME_STATIC`` — weights *and* activations quantized using
      calibration samples. Faster, needs representative inputs.
    * ``OPTIMUM_ONNX`` — HuggingFace `optimum` driving ONNX Runtime over a
      transformers model directory.
    * ``BITSANDBYTES`` — transformers + bitsandbytes int8/int4 weights, kept
      in the PyTorch format.
    """

    ONNXRUNTIME_DYNAMIC = "onnxruntime_dynamic"
    ONNXRUNTIME_STATIC = "onnxruntime_static"
    OPTIMUM_ONNX = "optimum_onnx"
    BITSANDBYTES = "bitsandbytes"


class HFOptimizationLevel(BaseStrEnum):
    """`optimum` graph-optimization preset for a transformers ONNX model.

    * ``O1`` — basic general optimizations.
    * ``O2`` — ``O1`` plus transformers-specific fusions (attention, layer
      norm). The usual pick.
    * ``O3`` — ``O2`` plus GELU approximation (small numeric drift).
    * ``O4`` — ``O3`` plus float16 conversion. **GPU only.**
    """

    O1 = "O1"
    O2 = "O2"
    O3 = "O3"
    O4 = "O4"


class HFQuantizationTarget(BaseStrEnum):
    """Instruction set the `optimum` quantization config targets.

    Picking the wrong one still produces a valid model, just a slower one —
    the fused integer kernels are only selected when the CPU supports them.

    * ``ARM64`` — phones, Raspberry Pi, Apple silicon, Graviton.
    * ``AVX2`` — any x86-64 since ~2013.
    * ``AVX512`` — Skylake-SP and newer server CPUs.
    * ``AVX512_VNNI`` — Cascade Lake and newer; the fastest int8 path.
    * ``TENSORRT`` — NVIDIA GPUs through the TensorRT provider.
    """

    ARM64 = "arm64"
    AVX2 = "avx2"
    AVX512 = "avx512"
    AVX512_VNNI = "avx512_vnni"
    TENSORRT = "tensorrt"


class EnergySource(BaseStrEnum):
    """Where an energy figure came from — it changes how it must be read.

    * ``NVML_COUNTER`` — NVML's monotonic total-energy counter
      (``nvmlDeviceGetTotalEnergyConsumption``). The most accurate option:
      the driver integrates for us. Volta and newer.
    * ``NVML_SAMPLING`` — periodic NVML power reads integrated over the
      measured window. Accurate to the sampling interval.
    * ``NVIDIA_SMI`` — same integration, but polling the ``nvidia-smi``
      binary. Used when ``pynvml`` is missing.
    * ``RAPL`` — Intel/AMD running-average-power-limit counters under
      ``/sys/class/powercap``. CPU package energy, Linux bare metal only.
    * ``UNAVAILABLE`` — nothing to measure with; every energy field is
      ``None``.
    """

    NVML_COUNTER = "nvml_counter"
    NVML_SAMPLING = "nvml_sampling"
    NVIDIA_SMI = "nvidia_smi"
    RAPL = "rapl"
    UNAVAILABLE = "unavailable"


class TensorSpec(BaseSchema):
    """Name, element type and shape of one model input or output.

    Dimensions come through as declared: an ``int`` for a fixed dimension and
    a ``str`` for a symbolic one (``"batch"``, ``"sequence"``). A symbolic
    dimension must be resolved to a concrete value before a benchmark can
    feed the model.

    Attributes:
        name (str): Tensor name as declared in the graph.
        dtype (str): Element type (``"tensor(float)"``, ``"tensor(int64)"``).
        shape (list[int | str]): One entry per dimension.
    """

    name: str = Field(
        title="name",
        description="Tensor name as declared in the graph.",
        examples=["images"],
    )
    dtype: str = Field(
        title="dtype",
        description="Element type as reported by the runtime.",
        examples=["tensor(float)"],
    )
    shape: list[int | str] = Field(
        default_factory=list,
        title="shape",
        description=("One entry per dimension: int when fixed, str when symbolic."),
        examples=[["batch", 3, 224, 224]],
    )


class StaticModelMetrics(BaseSchema):
    """Model properties that do not depend on running inference.

    Everything here is cheap to collect and stable across machines, which
    makes it the right thing to quote in a paper or a README: parameter
    count, on-disk size and (when a shape is known) forward GFLOPs.

    Attributes:
        name (str): Display name; defaults to the file stem.
        path (str): Path that was inspected. Empty for an in-memory module.
        format (ModelFormat): Artifact format.
        n_parameters (int): Total parameter count.
        n_trainable_parameters (int | None): Parameters with gradients, when
            the format tracks that (PyTorch). ``None`` for ONNX/ORT.
        disk_size_mb (float): Artifact size in MB. ``0.0`` in memory.
        gflops (float | None): Forward-pass GFLOPs at the profiled shape, or
            ``None`` when no shape was supplied or the counter is missing.
        opset (int | None): ONNX opset version, when applicable.
        producer (str | None): Tool that produced the artifact.
        inputs (list[TensorSpec]): Declared inputs.
        outputs (list[TensorSpec]): Declared outputs.
    """

    name: str = Field(
        title="name",
        description="Display name of the model.",
        examples=["yolov8n-cls"],
    )
    path: str = Field(
        default="",
        title="path",
        description="Path inspected; empty for an in-memory module.",
        examples=["models/yolov8n-cls.onnx"],
    )
    format: ModelFormat = Field(
        title="format",
        description="Artifact format (torch / onnx / ort).",
        examples=[ModelFormat.ONNX],
    )
    n_parameters: int = Field(
        default=0,
        title="n_parameters",
        description="Total parameter count.",
        examples=[3_180_000],
    )
    n_trainable_parameters: int | None = Field(
        default=None,
        title="n_trainable_parameters",
        description="Parameters with gradients (PyTorch only).",
        examples=[3_180_000],
    )
    disk_size_mb: float = Field(
        default=0.0,
        title="disk_size_mb",
        description="Artifact size on disk in MB.",
        examples=[6.2],
    )
    gflops: float | None = Field(
        default=None,
        title="gflops",
        description="Forward GFLOPs at the profiled input shape.",
        examples=[4.3],
    )
    opset: int | None = Field(
        default=None,
        title="opset",
        description="ONNX opset version of the graph.",
        examples=[17],
    )
    producer: str | None = Field(
        default=None,
        title="producer",
        description="Tool that produced the artifact.",
        examples=["pytorch"],
    )
    inputs: list[TensorSpec] = Field(
        default_factory=list,
        title="inputs",
        description="Declared model inputs.",
    )
    outputs: list[TensorSpec] = Field(
        default_factory=list,
        title="outputs",
        description="Declared model outputs.",
    )


class EnergyReading(BaseSchema):
    """Energy drawn over one measured window.

    Attributes:
        source (EnergySource): How the figure was obtained. Always check it
            before quoting a number — a ``RAPL`` reading is CPU package only
            and an ``NVML_*`` reading is GPU only. Neither is wall-plug.
        duration_s (float): Length of the measured window in seconds.
        energy_j (float | None): Energy over the window in Joules.
        mean_power_w (float | None): Mean power draw in Watts.
        peak_power_w (float | None): Highest sampled power draw in Watts.
        samples (int): Number of power samples collected. ``0`` when the
            counter path was used instead of sampling.
        device_index (int | None): GPU index, for the NVML/SMI sources.
    """

    source: EnergySource = Field(
        title="source",
        description="How the energy figure was obtained.",
        examples=[EnergySource.NVML_COUNTER],
    )
    duration_s: float = Field(
        default=0.0,
        title="duration_s",
        description="Length of the measured window in seconds.",
        examples=[2.5],
    )
    energy_j: float | None = Field(
        default=None,
        title="energy_j",
        description="Energy consumed over the window, in Joules.",
        examples=[118.4],
    )
    mean_power_w: float | None = Field(
        default=None,
        title="mean_power_w",
        description="Mean power draw over the window, in Watts.",
        examples=[47.4],
    )
    peak_power_w: float | None = Field(
        default=None,
        title="peak_power_w",
        description="Peak sampled power draw, in Watts.",
        examples=[61.0],
    )
    samples: int = Field(
        default=0,
        title="samples",
        description="Power samples collected (0 when a counter was used).",
        examples=[125],
    )
    device_index: int | None = Field(
        default=None,
        title="device_index",
        description="GPU index the reading refers to.",
        examples=[0],
    )


class RuntimeSample(BaseSchema):
    """One timed repetition of a benchmark.

    Attributes:
        index (int): Zero-based repetition number.
        latency_ms (float): Wall-clock time for the call, in milliseconds.
        rss_mb (float | None): Process resident set size right after the
            call, in MB. ``None`` without ``psutil``.
        cpu_percent (float | None): Process CPU usage since the previous
            sample, normalized per core.
        gpu_memory_mb (float | None): GPU memory in use, in MB.
        gpu_power_w (float | None): Most recent GPU power sample, in Watts.
    """

    index: int = Field(
        title="index",
        description="Zero-based repetition number.",
        examples=[0],
    )
    latency_ms: float = Field(
        title="latency_ms",
        description="Wall-clock latency of the call, in milliseconds.",
        examples=[12.4],
    )
    rss_mb: float | None = Field(
        default=None,
        title="rss_mb",
        description="Process resident set size in MB.",
        examples=[412.5],
    )
    cpu_percent: float | None = Field(
        default=None,
        title="cpu_percent",
        description="Process CPU usage normalized per core.",
        examples=[98.2],
    )
    gpu_memory_mb: float | None = Field(
        default=None,
        title="gpu_memory_mb",
        description="GPU memory in use during the call, in MB.",
        examples=[512.0],
    )
    gpu_power_w: float | None = Field(
        default=None,
        title="gpu_power_w",
        description="Most recent GPU power sample, in Watts.",
        examples=[47.4],
    )


class RuntimeAggregate(BaseSchema):
    """Latency, memory and energy summary over the timed repetitions.

    Latency is reported as **median plus IQR** first because inference
    latency is heavy-tailed: a single cold-start or scheduler hiccup moves
    the mean far more than the median. The mean is kept because throughput
    and energy-per-inference derive from it.

    Attributes:
        device (str): Device the model ran on (``"cpu"``, ``"cuda"``).
        provider (str | None): Execution provider, for ONNX Runtime.
        n_warmup (int): Warm-up calls discarded before timing.
        n_repetitions (int): Timed calls.
        batch_size (int): Batch size of a single call.
        latency_ms_median (float): Median latency.
        latency_ms_iqr (float): Inter-quartile range of latency.
        latency_ms_p95 (float): 95th-percentile latency.
        latency_ms_p99 (float): 99th-percentile latency.
        latency_ms_mean (float): Mean latency.
        latency_ms_std (float): Sample standard deviation (Bessel).
        latency_ms_min (float): Fastest call.
        latency_ms_max (float): Slowest call.
        throughput_per_s (float): ``batch_size * 1000 / latency_ms_mean``.
        rss_peak_mb (float | None): Highest RSS sampled.
        rss_delta_mb (float | None): RSS growth from before warm-up to the
            last repetition — a rough leak signal.
        cpu_percent_mean (float | None): Mean process CPU usage per core.
        gpu_memory_peak_mb (float | None): Highest GPU memory sampled. Per
            process when a probe was supplied (the torch path), otherwise
            device-wide, counting every process on the card.
        gpu_power_mean_w (float | None): Mean GPU power draw.
        gpu_energy_j (float | None): GPU energy over the timed window.
        cpu_energy_j (float | None): CPU package energy over the window.
        energy_per_inference_j (float | None): Total measured energy divided
            by ``n_repetitions``.
        energy_source (EnergySource): Provenance of the energy figures.
    """

    device: str = Field(
        title="device",
        description="Device the model ran on.",
        examples=["cpu"],
    )
    provider: str | None = Field(
        default=None,
        title="provider",
        description="ONNX Runtime execution provider used.",
        examples=["CPUExecutionProvider"],
    )
    n_warmup: int = Field(
        title="n_warmup",
        description="Warm-up calls discarded before timing.",
        examples=[10],
    )
    n_repetitions: int = Field(
        title="n_repetitions",
        description="Number of timed calls.",
        examples=[50],
    )
    batch_size: int = Field(
        default=1,
        title="batch_size",
        description="Batch size of a single call.",
        examples=[1],
    )
    latency_ms_median: float = Field(
        title="latency_ms_median",
        description="Median latency in milliseconds.",
        examples=[12.4],
    )
    latency_ms_iqr: float = Field(
        title="latency_ms_iqr",
        description="Inter-quartile range of latency in milliseconds.",
        examples=[0.8],
    )
    latency_ms_p95: float = Field(
        title="latency_ms_p95",
        description="95th-percentile latency in milliseconds.",
        examples=[14.1],
    )
    latency_ms_p99: float = Field(
        title="latency_ms_p99",
        description="99th-percentile latency in milliseconds.",
        examples=[15.9],
    )
    latency_ms_mean: float = Field(
        title="latency_ms_mean",
        description="Mean latency in milliseconds.",
        examples=[12.6],
    )
    latency_ms_std: float = Field(
        title="latency_ms_std",
        description="Sample standard deviation of latency.",
        examples=[0.6],
    )
    latency_ms_min: float = Field(
        title="latency_ms_min",
        description="Fastest timed call in milliseconds.",
        examples=[11.8],
    )
    latency_ms_max: float = Field(
        title="latency_ms_max",
        description="Slowest timed call in milliseconds.",
        examples=[16.2],
    )
    throughput_per_s: float = Field(
        title="throughput_per_s",
        description="Mean throughput in samples per second.",
        examples=[79.4],
    )
    rss_peak_mb: float | None = Field(
        default=None,
        title="rss_peak_mb",
        description="Highest process RSS sampled, in MB.",
        examples=[412.5],
    )
    rss_delta_mb: float | None = Field(
        default=None,
        title="rss_delta_mb",
        description="RSS growth across the run, in MB.",
        examples=[3.1],
    )
    cpu_percent_mean: float | None = Field(
        default=None,
        title="cpu_percent_mean",
        description="Mean process CPU usage normalized per core.",
        examples=[97.5],
    )
    gpu_memory_peak_mb: float | None = Field(
        default=None,
        title="gpu_memory_peak_mb",
        description=(
            "Highest GPU memory sampled in MB; device-wide unless a "
            "per-process probe was supplied."
        ),
        examples=[512.0],
    )
    gpu_power_mean_w: float | None = Field(
        default=None,
        title="gpu_power_mean_w",
        description="Mean GPU power draw, in Watts.",
        examples=[47.4],
    )
    gpu_energy_j: float | None = Field(
        default=None,
        title="gpu_energy_j",
        description="GPU energy over the timed window, in Joules.",
        examples=[31.2],
    )
    cpu_energy_j: float | None = Field(
        default=None,
        title="cpu_energy_j",
        description="CPU package energy over the window, in Joules.",
        examples=[18.7],
    )
    energy_per_inference_j: float | None = Field(
        default=None,
        title="energy_per_inference_j",
        description="Measured energy divided by the repetition count.",
        examples=[0.62],
    )
    energy_source: EnergySource = Field(
        default=EnergySource.UNAVAILABLE,
        title="energy_source",
        description="Provenance of the energy figures.",
        examples=[EnergySource.NVML_COUNTER],
    )


class BenchmarkProfile(BaseSchema):
    """Everything measured for one model: static, runtime and ranking.

    Attributes:
        name (str): Display name of the model.
        runtime (RuntimeAggregate): Aggregate over the timed repetitions.
        static (StaticModelMetrics | None): Static metrics, when collected.
        samples (list[RuntimeSample]): Raw per-repetition samples. Empty
            unless the benchmark was asked to keep them.
        quality (float | None): Task quality (accuracy, F1, mAP…) supplied
            by the caller. Never measured here — the SDK has no idea what
            "good" means for your task.
        composite_score (float | None): Weighted cost score, lower is
            better. Filled by :func:`~tempest_fastapi_sdk.modelops.rank`.
        is_pareto (bool): Whether the model is non-dominated on cost vs
            quality.
    """

    name: str = Field(
        title="name",
        description="Display name of the model.",
        examples=["yolov8n-cls"],
    )
    runtime: RuntimeAggregate = Field(
        title="runtime",
        description="Aggregate over the timed repetitions.",
    )
    static: StaticModelMetrics | None = Field(
        default=None,
        title="static",
        description="Static model metrics, when collected.",
    )
    samples: list[RuntimeSample] = Field(
        default_factory=list,
        title="samples",
        description="Raw per-repetition samples, when kept.",
    )
    quality: float | None = Field(
        default=None,
        title="quality",
        description="Caller-supplied task quality; higher is better.",
        examples=[0.81],
    )
    composite_score: float | None = Field(
        default=None,
        title="composite_score",
        description="Weighted cost score; lower is better.",
        examples=[0.24],
    )
    is_pareto: bool = Field(
        default=False,
        title="is_pareto",
        description="True when no other profile dominates this one.",
        examples=[True],
    )


class ParetoPoint(BaseSchema):
    """One model reduced to the axes the Pareto comparison uses.

    Attributes:
        name (str): Model name.
        latency_ms (float): Median latency; lower is better.
        energy_j (float | None): Energy per inference; lower is better.
        gflops (float | None): Static forward GFLOPs; lower is better.
        memory_mb (float | None): Peak memory (GPU when present, else RSS);
            lower is better.
        disk_size_mb (float | None): Artifact size; lower is better.
        quality (float | None): Caller-supplied quality; higher is better.
        is_pareto (bool): True when no other point dominates this one.
    """

    name: str = Field(
        title="name",
        description="Model name.",
        examples=["yolov8n-cls"],
    )
    latency_ms: float = Field(
        title="latency_ms",
        description="Median latency in milliseconds; lower is better.",
        examples=[12.4],
    )
    energy_j: float | None = Field(
        default=None,
        title="energy_j",
        description="Energy per inference in Joules; lower is better.",
        examples=[0.62],
    )
    gflops: float | None = Field(
        default=None,
        title="gflops",
        description="Static forward GFLOPs; lower is better.",
        examples=[4.3],
    )
    memory_mb: float | None = Field(
        default=None,
        title="memory_mb",
        description="Peak memory in MB (GPU when present, else RSS).",
        examples=[512.0],
    )
    disk_size_mb: float | None = Field(
        default=None,
        title="disk_size_mb",
        description="Artifact size in MB; lower is better.",
        examples=[6.2],
    )
    quality: float | None = Field(
        default=None,
        title="quality",
        description="Caller-supplied quality; higher is better.",
        examples=[0.81],
    )
    is_pareto: bool = Field(
        default=False,
        title="is_pareto",
        description="True when the point is non-dominated.",
        examples=[True],
    )


class BenchmarkReport(BaseSchema):
    """Comparison of several models measured under the same conditions.

    Attributes:
        profiles (list[BenchmarkProfile]): One entry per model, sorted by
            ``composite_score`` ascending once ranked.
        pareto (list[ParetoPoint]): Pareto annotation, one entry per model.
        weights (dict[str, float]): Weights the composite score used, after
            renormalization over the dimensions that had data.
        hardware (HardwareInfo | None): Host the measurements ran on. Quote
            it with the numbers — latency on a workstation says nothing
            about latency on a phone.
    """

    profiles: list[BenchmarkProfile] = Field(
        default_factory=list,
        title="profiles",
        description="One measured profile per model.",
    )
    pareto: list[ParetoPoint] = Field(
        default_factory=list,
        title="pareto",
        description="Pareto annotation, one entry per model.",
    )
    weights: dict[str, float] = Field(
        default_factory=dict,
        title="weights",
        description="Effective composite-score weights after renormalizing.",
        examples=[{"latency_ms_median": 0.5, "rss_peak_mb": 0.5}],
    )
    hardware: HardwareInfo | None = Field(
        default=None,
        title="hardware",
        description="Host the measurements were taken on.",
    )


class ExportResult(BaseSchema):
    """Outcome of one export or graph-optimization step.

    Attributes:
        source_path (str): Input artifact.
        output_path (str): Artifact that was written.
        format (ModelFormat): Format of the output.
        source_size_mb (float): Input size in MB.
        output_size_mb (float): Output size in MB.
        size_ratio (float): ``source_size_mb / output_size_mb``. Above 1.0
            means the output is smaller.
        opset (int | None): ONNX opset of the output, when known.
        optimization_style (OrtOptimizationStyle | None): Style used for an
            ``.ort`` conversion.
        extra_files (list[str]): Side artifacts written next to the output,
            such as the ``.required_operators.config`` that a minimal ONNX
            Runtime build needs.
    """

    source_path: str = Field(
        title="source_path",
        description="Input artifact path.",
        examples=["models/classify.onnx"],
    )
    output_path: str = Field(
        title="output_path",
        description="Artifact that was written.",
        examples=["models/classify.ort"],
    )
    format: ModelFormat = Field(
        title="format",
        description="Format of the written artifact.",
        examples=[ModelFormat.ORT],
    )
    source_size_mb: float = Field(
        default=0.0,
        title="source_size_mb",
        description="Input size in MB.",
        examples=[6.2],
    )
    output_size_mb: float = Field(
        default=0.0,
        title="output_size_mb",
        description="Output size in MB.",
        examples=[6.0],
    )
    size_ratio: float = Field(
        default=1.0,
        title="size_ratio",
        description="source_size_mb / output_size_mb; >1 means smaller.",
        examples=[1.03],
    )
    opset: int | None = Field(
        default=None,
        title="opset",
        description="ONNX opset of the output.",
        examples=[17],
    )
    optimization_style: OrtOptimizationStyle | None = Field(
        default=None,
        title="optimization_style",
        description="Style used for an .ort conversion.",
        examples=[OrtOptimizationStyle.FIXED],
    )
    extra_files: list[str] = Field(
        default_factory=list,
        title="extra_files",
        description="Side artifacts written next to the output.",
        examples=[["models/classify.required_operators.config"]],
    )


class QuantizationResult(BaseSchema):
    """Outcome of one quantization step.

    Attributes:
        source_path (str): Model that was quantized.
        output_path (str): Quantized artifact.
        backend (QuantizationBackend): Engine that did the work.
        weight_type (QuantWeightType | None): Type the weights landed in.
        source_size_mb (float): Input size in MB.
        output_size_mb (float): Output size in MB.
        compression_ratio (float): ``source_size_mb / output_size_mb``.
        per_channel (bool): Whether weights were quantized per channel.
        notes (list[str]): Anything the caller should know — skipped
            operators, calibration sample count, provider caveats.
    """

    source_path: str = Field(
        title="source_path",
        description="Model that was quantized.",
        examples=["models/classify.onnx"],
    )
    output_path: str = Field(
        title="output_path",
        description="Quantized artifact.",
        examples=["models/classify.int8.onnx"],
    )
    backend: QuantizationBackend = Field(
        title="backend",
        description="Engine that produced the artifact.",
        examples=[QuantizationBackend.ONNXRUNTIME_DYNAMIC],
    )
    weight_type: QuantWeightType | None = Field(
        default=None,
        title="weight_type",
        description="Type the weights were quantized to.",
        examples=[QuantWeightType.INT8],
    )
    source_size_mb: float = Field(
        default=0.0,
        title="source_size_mb",
        description="Input size in MB.",
        examples=[6.2],
    )
    output_size_mb: float = Field(
        default=0.0,
        title="output_size_mb",
        description="Output size in MB.",
        examples=[1.7],
    )
    compression_ratio: float = Field(
        default=1.0,
        title="compression_ratio",
        description="source_size_mb / output_size_mb.",
        examples=[3.6],
    )
    per_channel: bool = Field(
        default=False,
        title="per_channel",
        description="Whether weights were quantized per channel.",
        examples=[False],
    )
    notes: list[str] = Field(
        default_factory=list,
        title="notes",
        description="Caveats worth surfacing to the caller.",
        examples=[["calibrated on 128 samples"]],
    )


__all__: list[str] = [
    "BenchmarkProfile",
    "BenchmarkReport",
    "CalibrationMethod",
    "EnergyReading",
    "EnergySource",
    "ExportResult",
    "GraphOptimizationLevel",
    "HFOptimizationLevel",
    "HFQuantizationTarget",
    "ModelFormat",
    "OrtOptimizationStyle",
    "ParetoPoint",
    "QuantWeightType",
    "QuantizationBackend",
    "QuantizationFormat",
    "QuantizationResult",
    "RuntimeAggregate",
    "RuntimeSample",
    "StaticModelMetrics",
    "TensorSpec",
]
