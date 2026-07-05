"""Self-hosted generative AI — run HuggingFace models on your own hardware.

The first slice ships the **capacity check**: before you download and
load a model, ask whether the host can actually run it. Everything here
imports without the ``[genai]`` extra; ``torch`` / ``transformers`` are
only needed to probe real GPUs and (later) run inference.
"""

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

__all__: list[str] = [
    "CapacityReport",
    "GPUInfo",
    "HardwareInfo",
    "ModelDtype",
    "bytes_per_param",
    "can_run",
    "estimate_model_bytes",
    "fetch_num_params",
    "probe_hardware",
    "recommend",
]
