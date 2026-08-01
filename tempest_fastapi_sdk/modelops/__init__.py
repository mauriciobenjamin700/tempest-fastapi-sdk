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
from tempest_fastapi_sdk.modelops.edge import (
    BASELINE_FILENAME as BASELINE_FILENAME,
)
from tempest_fastapi_sdk.modelops.edge import MANIFEST_FILENAME as MANIFEST_FILENAME
from tempest_fastapi_sdk.modelops.edge import (
    MANIFEST_SCHEMA_VERSION as MANIFEST_SCHEMA_VERSION,
)
from tempest_fastapi_sdk.modelops.edge import EdgeManifest as EdgeManifest
from tempest_fastapi_sdk.modelops.edge import EdgePackage as EdgePackage
from tempest_fastapi_sdk.modelops.edge import (
    LoadedEdgePackage as LoadedEdgePackage,
)
from tempest_fastapi_sdk.modelops.edge import ModelFile as ModelFile
from tempest_fastapi_sdk.modelops.edge import ModelInput as ModelInput
from tempest_fastapi_sdk.modelops.edge import ModelOutput as ModelOutput
from tempest_fastapi_sdk.modelops.edge import edge_pipeline as edge_pipeline
from tempest_fastapi_sdk.modelops.edge import (
    load_edge_package as load_edge_package,
)
from tempest_fastapi_sdk.modelops.edge import read_manifest as read_manifest
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
from tempest_fastapi_sdk.modelops.monitoring import DEFAULT_BINS as DEFAULT_BINS
from tempest_fastapi_sdk.modelops.monitoring import (
    DEFAULT_WINDOW_ROWS as DEFAULT_WINDOW_ROWS,
)
from tempest_fastapi_sdk.modelops.monitoring import (
    MIN_ROWS_FOR_DRIFT as MIN_ROWS_FOR_DRIFT,
)
from tempest_fastapi_sdk.modelops.monitoring import PSI_MODERATE as PSI_MODERATE
from tempest_fastapi_sdk.modelops.monitoring import (
    PSI_SIGNIFICANT as PSI_SIGNIFICANT,
)
from tempest_fastapi_sdk.modelops.monitoring import DriftReport as DriftReport
from tempest_fastapi_sdk.modelops.monitoring import DriftVerdict as DriftVerdict
from tempest_fastapi_sdk.modelops.monitoring import FeatureBaseline as FeatureBaseline
from tempest_fastapi_sdk.modelops.monitoring import FeatureBins as FeatureBins
from tempest_fastapi_sdk.modelops.monitoring import FeatureDrift as FeatureDrift
from tempest_fastapi_sdk.modelops.monitoring import LatencyReport as LatencyReport
from tempest_fastapi_sdk.modelops.monitoring import (
    MonitoringReport as MonitoringReport,
)
from tempest_fastapi_sdk.modelops.monitoring import (
    PredictionDistribution as PredictionDistribution,
)
from tempest_fastapi_sdk.modelops.monitoring import (
    PredictionMetrics as PredictionMetrics,
)
from tempest_fastapi_sdk.modelops.monitoring import (
    PredictionMonitor as PredictionMonitor,
)
from tempest_fastapi_sdk.modelops.monitoring import (
    baseline_from_samples as baseline_from_samples,
)
from tempest_fastapi_sdk.modelops.monitoring import (
    population_stability_index as population_stability_index,
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
from tempest_fastapi_sdk.modelops.router import (
    PredictRequestSchema as PredictRequestSchema,
)
from tempest_fastapi_sdk.modelops.router import (
    PredictResponseSchema as PredictResponseSchema,
)
from tempest_fastapi_sdk.modelops.router import (
    RegistryModelSource as RegistryModelSource,
)
from tempest_fastapi_sdk.modelops.router import (
    make_prediction_router as make_prediction_router,
)
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
from tempest_fastapi_sdk.modelops.serving import (
    DEFAULT_INTRA_OP_THREADS as DEFAULT_INTRA_OP_THREADS,
)
from tempest_fastapi_sdk.modelops.serving import OnnxPredictor as OnnxPredictor
from tempest_fastapi_sdk.modelops.serving import Prediction as Prediction
from tempest_fastapi_sdk.modelops.serving import PredictorInfo as PredictorInfo
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
    "BASELINE_FILENAME",
    "DEFAULT_BINS",
    "DEFAULT_COST_WEIGHTS",
    "DEFAULT_INTRA_OP_THREADS",
    "DEFAULT_OPSET",
    "DEFAULT_REPETITIONS",
    "DEFAULT_SAMPLE_INTERVAL_S",
    "DEFAULT_WARMUP",
    "DEFAULT_WINDOW_ROWS",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "MIN_ROWS_FOR_DRIFT",
    "ORT_CONFIG_SUFFIXES",
    "PSI_MODERATE",
    "PSI_SIGNIFICANT",
    "RAPL_ROOT",
    "REMOTE_PROVIDERS",
    "BenchmarkProfile",
    "BenchmarkReport",
    "CalibrationMethod",
    "DriftReport",
    "DriftVerdict",
    "EdgeBundle",
    "EdgeManifest",
    "EdgePackage",
    "EdgeStage",
    "EnergyReading",
    "EnergySource",
    "ExportResult",
    "ExportVerification",
    "FeatureBaseline",
    "FeatureBins",
    "FeatureDrift",
    "GraphOptimizationLevel",
    "HFOptimizationLevel",
    "HFQuantizationTarget",
    "LatencyReport",
    "LoadedEdgePackage",
    "ModelFile",
    "ModelFormat",
    "ModelInput",
    "ModelOutput",
    "MonitoringReport",
    "NullPowerSampler",
    "NvidiaSmiPowerSampler",
    "NvmlPowerSampler",
    "OnnxPredictor",
    "OrtOptimizationStyle",
    "ParetoPoint",
    "PowerSampler",
    "PredictRequestSchema",
    "PredictResponseSchema",
    "Prediction",
    "PredictionDistribution",
    "PredictionMetrics",
    "PredictionMonitor",
    "PredictorInfo",
    "QuantWeightType",
    "QuantizationBackend",
    "QuantizationFormat",
    "QuantizationResult",
    "RaplEnergySampler",
    "RegistryModelSource",
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
    "baseline_from_samples",
    "benchmark",
    "benchmark_models",
    "benchmark_onnx",
    "benchmark_torch",
    "composite_scores",
    "default_providers",
    "edge_bundle",
    "edge_pipeline",
    "export_onnx_to_ort",
    "export_sklearn_to_onnx",
    "export_torch_to_onnx",
    "load_edge_package",
    "make_prediction_router",
    "optimize_hf_onnx",
    "optimize_onnx_graph",
    "pareto_points",
    "population_stability_index",
    "quantize_hf_bnb",
    "quantize_hf_onnx",
    "quantize_onnx_dynamic",
    "quantize_onnx_static",
    "rank",
    "read_manifest",
    "resolve_cpu_energy_sampler",
    "resolve_power_sampler",
    "uses_ml_domain",
    "verify_sklearn_onnx",
]
