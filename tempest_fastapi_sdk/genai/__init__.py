"""Self-hosted generative AI — run HuggingFace models on your own hardware.

The first slice ships the **capacity check**: before you download and
load a model, ask whether the host can actually run it. Everything here
imports without the ``[genai]`` extra; ``torch`` / ``transformers`` are
only needed to probe real GPUs and (later) run inference.
"""

from tempest_fastapi_sdk.genai.batching import BatchScheduler as BatchScheduler
from tempest_fastapi_sdk.genai.embeddings import (
    AsyncEmbeddingCache as AsyncEmbeddingCache,
)
from tempest_fastapi_sdk.genai.embeddings import Embedder as Embedder
from tempest_fastapi_sdk.genai.embeddings import EmbeddingCache as EmbeddingCache
from tempest_fastapi_sdk.genai.embeddings import (
    InMemoryEmbeddingCache as InMemoryEmbeddingCache,
)
from tempest_fastapi_sdk.genai.embeddings import (
    RedisEmbeddingCache as RedisEmbeddingCache,
)
from tempest_fastapi_sdk.genai.embeddings import cosine_similarity as cosine_similarity
from tempest_fastapi_sdk.genai.generation_cache import (
    AsyncGenerationCache as AsyncGenerationCache,
)
from tempest_fastapi_sdk.genai.generation_cache import (
    GenerationCache as GenerationCache,
)
from tempest_fastapi_sdk.genai.generation_cache import (
    InMemoryGenerationCache as InMemoryGenerationCache,
)
from tempest_fastapi_sdk.genai.generation_cache import (
    RedisGenerationCache as RedisGenerationCache,
)
from tempest_fastapi_sdk.genai.generation_cache import (
    cached_generate as cached_generate,
)
from tempest_fastapi_sdk.genai.generation_cache import (
    is_deterministic as is_deterministic,
)
from tempest_fastapi_sdk.genai.generation_cache import (
    make_generation_key as make_generation_key,
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
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics as GenAIMetrics
from tempest_fastapi_sdk.genai.moderation import (
    ClassifierModerator as ClassifierModerator,
)
from tempest_fastapi_sdk.genai.moderation import ModerationBackend as ModerationBackend
from tempest_fastapi_sdk.genai.moderation import ModerationResult as ModerationResult
from tempest_fastapi_sdk.genai.moderation import RuleModerator as RuleModerator
from tempest_fastapi_sdk.genai.ollama import (
    DEFAULT_OLLAMA_URL as DEFAULT_OLLAMA_URL,
)
from tempest_fastapi_sdk.genai.ollama import OllamaEmbedder as OllamaEmbedder
from tempest_fastapi_sdk.genai.ollama import OllamaGenerator as OllamaGenerator
from tempest_fastapi_sdk.genai.onnx_embed import OnnxEmbedder as OnnxEmbedder
from tempest_fastapi_sdk.genai.pipeline import AIChatPipeline as AIChatPipeline
from tempest_fastapi_sdk.genai.pipeline import AIChatResult as AIChatResult
from tempest_fastapi_sdk.genai.pipeline import Tool as Tool
from tempest_fastapi_sdk.genai.pipeline import (
    make_ai_chat_router as make_ai_chat_router,
)
from tempest_fastapi_sdk.genai.registry import ModelRegistry as ModelRegistry
from tempest_fastapi_sdk.genai.router import make_genai_router as make_genai_router
from tempest_fastapi_sdk.genai.schemas import (
    CapacityReport as CapacityReport,
)
from tempest_fastapi_sdk.genai.schemas import (
    GenerationConfig as GenerationConfig,
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
from tempest_fastapi_sdk.genai.structured import (
    build_prefix_allowed_tokens_fn as build_prefix_allowed_tokens_fn,
)
from tempest_fastapi_sdk.genai.structured import (
    parse_structured as parse_structured,
)
from tempest_fastapi_sdk.genai.text import (
    TextBackend as TextBackend,
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
from tempest_fastapi_sdk.genai.tokens import (
    count_message_tokens as count_message_tokens,
)
from tempest_fastapi_sdk.genai.tokens import (
    count_tokens as count_tokens,
)
from tempest_fastapi_sdk.genai.tokens import (
    truncate_messages as truncate_messages,
)
from tempest_fastapi_sdk.genai.vision_text import (
    VisionTextGenerator as VisionTextGenerator,
)

__all__: list[str] = [
    "DEFAULT_OLLAMA_URL",
    "AIChatPipeline",
    "AIChatResult",
    "AsyncEmbeddingCache",
    "AsyncGenerationCache",
    "BatchScheduler",
    "CapacityReport",
    "ClassifierModerator",
    "Embedder",
    "EmbeddingCache",
    "GPUInfo",
    "GenAIMetrics",
    "GenerationCache",
    "GenerationConfig",
    "HardwareInfo",
    "InMemoryEmbeddingCache",
    "InMemoryGenerationCache",
    "ModelDtype",
    "ModelRegistry",
    "ModerationBackend",
    "ModerationResult",
    "OllamaEmbedder",
    "OllamaGenerator",
    "OnnxEmbedder",
    "RedisEmbeddingCache",
    "RedisGenerationCache",
    "RuleModerator",
    "TextBackend",
    "TextGenerator",
    "Tool",
    "VisionTextGenerator",
    "auto_dtype_name",
    "build_prefix_allowed_tokens_fn",
    "bytes_per_param",
    "cached_generate",
    "can_run",
    "cosine_similarity",
    "count_message_tokens",
    "count_tokens",
    "estimate_model_bytes",
    "fetch_num_params",
    "is_deterministic",
    "make_ai_chat_router",
    "make_genai_router",
    "make_generation_key",
    "parse_structured",
    "probe_hardware",
    "recommend",
    "resolve_device",
    "truncate_messages",
]
