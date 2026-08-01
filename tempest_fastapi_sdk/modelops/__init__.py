"""Model ops — export, quantize and benchmark the models a service serves.

Three jobs that always travel together, because you cannot honestly do one
without the others: you quantize to make a model cheaper, you benchmark to
find out whether it actually got cheaper, and you export to the format the
target device runs.

    from tempest_fastapi_sdk.modelops import (
        benchmark_onnx,
        export_onnx_to_ort,
        quantize_onnx_dynamic,
    )

    quantized = quantize_onnx_dynamic("m.onnx", "m.int8.onnx")
    profile = benchmark_onnx("m.int8.onnx", n_repetitions=50)
    mobile = export_onnx_to_ort("m.int8.onnx", "dist/", target_platform="arm")

Two extras, so you install only the weight you need:

* ``[modelops]`` — psutil + nvidia-ml-py. Enough for :func:`benchmark` over
  any callable, with CPU/RAM/GPU/energy measurement.
* ``[modelops-onnx]`` — adds onnx + onnxruntime: static analysis, ONNX
  benchmarking, ``.onnx`` to ``.ort`` conversion, graph optimization, and
  quantization of both raw graphs and transformers exports.

This module itself imports with neither. Every heavy dependency is resolved
inside the function that needs it, and its absence raises an ``ImportError``
naming the extra to install.

There is no HuggingFace ONNX *export* here, on purpose. The only maintained
per-architecture export registry lives in `optimum`, which caps
``transformers`` to an upper bound that would propagate to every consumer of
this SDK. Run it as a build step in a throwaway environment instead::

    uvx --from "optimum[onnxruntime]" optimum-cli export onnx \\
        --model distilbert-base-uncased --task text-classification \\
        exports/distilbert

then point :func:`optimize_hf_onnx` and :func:`quantize_hf_onnx` at that
directory. Both run on ``onnxruntime``'s own transformers tooling, so nothing
here constrains your ``transformers`` version.
"""

from tempest_fastapi_sdk.modelops.bench import (
    DEFAULT_REPETITIONS as DEFAULT_REPETITIONS,
)
from tempest_fastapi_sdk.modelops.bench import DEFAULT_WARMUP as DEFAULT_WARMUP
from tempest_fastapi_sdk.modelops.bench import benchmark as benchmark
from tempest_fastapi_sdk.modelops.bench import (
    benchmark_models as benchmark_models,
)
from tempest_fastapi_sdk.modelops.bench import benchmark_onnx as benchmark_onnx
from tempest_fastapi_sdk.modelops.bench import benchmark_torch as benchmark_torch
from tempest_fastapi_sdk.modelops.energy import (
    DEFAULT_SAMPLE_INTERVAL_S as DEFAULT_SAMPLE_INTERVAL_S,
)
from tempest_fastapi_sdk.modelops.energy import RAPL_ROOT as RAPL_ROOT
from tempest_fastapi_sdk.modelops.energy import (
    NullPowerSampler as NullPowerSampler,
)
from tempest_fastapi_sdk.modelops.energy import (
    NvidiaSmiPowerSampler as NvidiaSmiPowerSampler,
)
from tempest_fastapi_sdk.modelops.energy import (
    NvmlPowerSampler as NvmlPowerSampler,
)
from tempest_fastapi_sdk.modelops.energy import PowerSampler as PowerSampler
from tempest_fastapi_sdk.modelops.energy import (
    RaplEnergySampler as RaplEnergySampler,
)
from tempest_fastapi_sdk.modelops.energy import (
    resolve_cpu_energy_sampler as resolve_cpu_energy_sampler,
)
from tempest_fastapi_sdk.modelops.energy import (
    resolve_power_sampler as resolve_power_sampler,
)
from tempest_fastapi_sdk.modelops.export import (
    ORT_CONFIG_SUFFIXES as ORT_CONFIG_SUFFIXES,
)
from tempest_fastapi_sdk.modelops.export import (
    export_onnx_to_ort as export_onnx_to_ort,
)
from tempest_fastapi_sdk.modelops.export import (
    export_torch_to_onnx as export_torch_to_onnx,
)
from tempest_fastapi_sdk.modelops.export import (
    optimize_onnx_graph as optimize_onnx_graph,
)
from tempest_fastapi_sdk.modelops.quantize import (
    optimize_hf_onnx as optimize_hf_onnx,
)
from tempest_fastapi_sdk.modelops.quantize import (
    quantize_hf_bnb as quantize_hf_bnb,
)
from tempest_fastapi_sdk.modelops.quantize import (
    quantize_hf_onnx as quantize_hf_onnx,
)
from tempest_fastapi_sdk.modelops.quantize import (
    quantize_onnx_dynamic as quantize_onnx_dynamic,
)
from tempest_fastapi_sdk.modelops.quantize import (
    quantize_onnx_static as quantize_onnx_static,
)
from tempest_fastapi_sdk.modelops.ranking import (
    DEFAULT_COST_WEIGHTS as DEFAULT_COST_WEIGHTS,
)
from tempest_fastapi_sdk.modelops.ranking import (
    composite_scores as composite_scores,
)
from tempest_fastapi_sdk.modelops.ranking import pareto_points as pareto_points
from tempest_fastapi_sdk.modelops.ranking import rank as rank
from tempest_fastapi_sdk.modelops.schemas import (
    BenchmarkProfile as BenchmarkProfile,
)
from tempest_fastapi_sdk.modelops.schemas import BenchmarkReport as BenchmarkReport
from tempest_fastapi_sdk.modelops.schemas import (
    CalibrationMethod as CalibrationMethod,
)
from tempest_fastapi_sdk.modelops.schemas import EnergyReading as EnergyReading
from tempest_fastapi_sdk.modelops.schemas import EnergySource as EnergySource
from tempest_fastapi_sdk.modelops.schemas import ExportResult as ExportResult
from tempest_fastapi_sdk.modelops.schemas import (
    GraphOptimizationLevel as GraphOptimizationLevel,
)
from tempest_fastapi_sdk.modelops.schemas import (
    HFOptimizationLevel as HFOptimizationLevel,
)
from tempest_fastapi_sdk.modelops.schemas import (
    HFQuantizationTarget as HFQuantizationTarget,
)
from tempest_fastapi_sdk.modelops.schemas import ModelFormat as ModelFormat
from tempest_fastapi_sdk.modelops.schemas import (
    OrtOptimizationStyle as OrtOptimizationStyle,
)
from tempest_fastapi_sdk.modelops.schemas import ParetoPoint as ParetoPoint
from tempest_fastapi_sdk.modelops.schemas import (
    QuantizationBackend as QuantizationBackend,
)
from tempest_fastapi_sdk.modelops.schemas import (
    QuantizationFormat as QuantizationFormat,
)
from tempest_fastapi_sdk.modelops.schemas import (
    QuantizationResult as QuantizationResult,
)
from tempest_fastapi_sdk.modelops.schemas import QuantWeightType as QuantWeightType
from tempest_fastapi_sdk.modelops.schemas import (
    RuntimeAggregate as RuntimeAggregate,
)
from tempest_fastapi_sdk.modelops.schemas import RuntimeSample as RuntimeSample
from tempest_fastapi_sdk.modelops.schemas import (
    StaticModelMetrics as StaticModelMetrics,
)
from tempest_fastapi_sdk.modelops.schemas import TensorSpec as TensorSpec
from tempest_fastapi_sdk.modelops.sklearn import DEFAULT_OPSET as DEFAULT_OPSET
from tempest_fastapi_sdk.modelops.sklearn import EdgeBundle as EdgeBundle
from tempest_fastapi_sdk.modelops.sklearn import EdgeStage as EdgeStage
from tempest_fastapi_sdk.modelops.sklearn import (
    ExportVerification as ExportVerification,
)
from tempest_fastapi_sdk.modelops.sklearn import SklearnExport as SklearnExport
from tempest_fastapi_sdk.modelops.sklearn import TensorDtype as TensorDtype
from tempest_fastapi_sdk.modelops.sklearn import edge_bundle as edge_bundle
from tempest_fastapi_sdk.modelops.sklearn import (
    export_sklearn_to_onnx as export_sklearn_to_onnx,
)
from tempest_fastapi_sdk.modelops.sklearn import uses_ml_domain as uses_ml_domain
from tempest_fastapi_sdk.modelops.sklearn import (
    verify_sklearn_onnx as verify_sklearn_onnx,
)
from tempest_fastapi_sdk.modelops.static import (
    REMOTE_PROVIDERS as REMOTE_PROVIDERS,
)
from tempest_fastapi_sdk.modelops.static import analyze_model as analyze_model
from tempest_fastapi_sdk.modelops.static import analyze_onnx as analyze_onnx
from tempest_fastapi_sdk.modelops.static import analyze_ort as analyze_ort
from tempest_fastapi_sdk.modelops.static import analyze_torch as analyze_torch
from tempest_fastapi_sdk.modelops.static import (
    default_providers as default_providers,
)

__all__: list[str] = [
    "DEFAULT_COST_WEIGHTS",
    "DEFAULT_OPSET",
    "DEFAULT_REPETITIONS",
    "DEFAULT_SAMPLE_INTERVAL_S",
    "DEFAULT_WARMUP",
    "ORT_CONFIG_SUFFIXES",
    "RAPL_ROOT",
    "REMOTE_PROVIDERS",
    "BenchmarkProfile",
    "BenchmarkReport",
    "CalibrationMethod",
    "EdgeBundle",
    "EdgeStage",
    "EnergyReading",
    "EnergySource",
    "ExportResult",
    "ExportVerification",
    "GraphOptimizationLevel",
    "HFOptimizationLevel",
    "HFQuantizationTarget",
    "ModelFormat",
    "NullPowerSampler",
    "NvidiaSmiPowerSampler",
    "NvmlPowerSampler",
    "OrtOptimizationStyle",
    "ParetoPoint",
    "PowerSampler",
    "QuantWeightType",
    "QuantizationBackend",
    "QuantizationFormat",
    "QuantizationResult",
    "RaplEnergySampler",
    "RuntimeAggregate",
    "RuntimeSample",
    "SklearnExport",
    "StaticModelMetrics",
    "TensorDtype",
    "TensorSpec",
    "analyze_model",
    "analyze_onnx",
    "analyze_ort",
    "analyze_torch",
    "benchmark",
    "benchmark_models",
    "benchmark_onnx",
    "benchmark_torch",
    "composite_scores",
    "default_providers",
    "edge_bundle",
    "export_onnx_to_ort",
    "export_sklearn_to_onnx",
    "export_torch_to_onnx",
    "optimize_hf_onnx",
    "optimize_onnx_graph",
    "pareto_points",
    "quantize_hf_bnb",
    "quantize_hf_onnx",
    "quantize_onnx_dynamic",
    "quantize_onnx_static",
    "rank",
    "resolve_cpu_energy_sampler",
    "resolve_power_sampler",
    "uses_ml_domain",
    "verify_sklearn_onnx",
]
