"""Self-hosted generative AI — run HuggingFace models on your own hardware.

The first slice ships the **capacity check**: before you download and
load a model, ask whether the host can actually run it. Everything here
imports without the ``[genai]`` extra; ``torch`` / ``transformers`` are
only needed to probe real GPUs and (later) run inference.
"""

from tempest_fastapi_sdk.genai.batching import BatchScheduler as BatchScheduler
from tempest_fastapi_sdk.genai.embeddings import Embedder as Embedder
from tempest_fastapi_sdk.genai.embeddings import EmbeddingCache as EmbeddingCache
from tempest_fastapi_sdk.genai.embeddings import (
    InMemoryEmbeddingCache as InMemoryEmbeddingCache,
)
from tempest_fastapi_sdk.genai.hardware import (
    bytes_per_param as bytes_per_param,
)
from tempest_fastapi_sdk.genai.hardware import (
    can_run as can_run,
)
from tempest_fastapi_sdk.genai.hardware import (
    estimate_model_bytes as estimate_model_bytes,
)
from tempest_fastapi_sdk.genai.hardware import (
    fetch_num_params as fetch_num_params,
)
from tempest_fastapi_sdk.genai.hardware import (
    probe_hardware as probe_hardware,
)
from tempest_fastapi_sdk.genai.hardware import (
    recommend as recommend,
)
from tempest_fastapi_sdk.genai.registry import ModelRegistry as ModelRegistry
from tempest_fastapi_sdk.genai.schemas import (
    CapacityReport as CapacityReport,
)
from tempest_fastapi_sdk.genai.schemas import (
    GPUInfo as GPUInfo,
)
from tempest_fastapi_sdk.genai.schemas import (
    HardwareInfo as HardwareInfo,
)
from tempest_fastapi_sdk.genai.schemas import (
    ModelDtype as ModelDtype,
)
from tempest_fastapi_sdk.genai.text import (
    TextGenerator as TextGenerator,
)
from tempest_fastapi_sdk.genai.text import (
    auto_dtype_name as auto_dtype_name,
)
from tempest_fastapi_sdk.genai.text import (
    resolve_device as resolve_device,
)

__all__: list[str] = [
    "BatchScheduler",
    "CapacityReport",
    "Embedder",
    "EmbeddingCache",
    "GPUInfo",
    "HardwareInfo",
    "InMemoryEmbeddingCache",
    "ModelDtype",
    "ModelRegistry",
    "TextGenerator",
    "auto_dtype_name",
    "bytes_per_param",
    "can_run",
    "estimate_model_bytes",
    "fetch_num_params",
    "probe_hardware",
    "recommend",
    "resolve_device",
]
