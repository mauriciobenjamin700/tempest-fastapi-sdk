"""Ollama backend for text generation and embeddings.

Runs generation and embeddings against a local (or remote) `Ollama
<https://ollama.com>`_ daemon over HTTP instead of loading HuggingFace
weights with ``torch``. This is the drop-in alternative to the
``torch``/``transformers`` path: :class:`OllamaGenerator` mirrors the
public surface of :class:`~tempest_fastapi_sdk.genai.text.TextGenerator`
(``generate`` / ``chat`` / ``stream`` / ``unload``) and
:class:`OllamaEmbedder` satisfies the
:class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed` protocol, so both
plug straight into :func:`~tempest_fastapi_sdk.genai.make_genai_router`
and :class:`~tempest_fastapi_sdk.genai.rag.Retriever` with no other
changes.

Ollama manages model download, quantization and VRAM itself, so there is
no ``load()`` step and no GPU code here — the only dependency is
``httpx`` (the ``[genai-ollama]`` extra). The daemon must already be
running and the model already pulled (``ollama pull <model>``).

Example:

    >>> gen = OllamaGenerator("llama3.2")
    >>> await gen.generate("Explain PIX in one sentence.")
    >>> async for token in gen.stream("Tell me a joke."):
    ...     print(token, end="")
    >>> emb = OllamaEmbedder("nomic-embed-text")
    >>> vectors = await emb.embed(["hello", "world"])
"""

from tempest_fastapi_sdk.genai.ollama._client import (
    _build_options as _build_options,
)
from tempest_fastapi_sdk.genai.ollama._client import (
    _OllamaClientMixin as _OllamaClientMixin,
)
from tempest_fastapi_sdk.genai.ollama._client import (
    _require_httpx as _require_httpx,
)
from tempest_fastapi_sdk.genai.ollama._constants import (
    DEFAULT_OLLAMA_URL as DEFAULT_OLLAMA_URL,
)
from tempest_fastapi_sdk.genai.ollama._constants import ThinkLevel as ThinkLevel
from tempest_fastapi_sdk.genai.ollama.embedder import (
    OllamaEmbedder as OllamaEmbedder,
)
from tempest_fastapi_sdk.genai.ollama.errors import (
    OllamaContextExceededError as OllamaContextExceededError,
)
from tempest_fastapi_sdk.genai.ollama.errors import OllamaError as OllamaError
from tempest_fastapi_sdk.genai.ollama.errors import (
    OllamaFormatIgnoredError as OllamaFormatIgnoredError,
)
from tempest_fastapi_sdk.genai.ollama.errors import (
    OllamaThinkingUnsupportedError as OllamaThinkingUnsupportedError,
)
from tempest_fastapi_sdk.genai.ollama.generator import (
    OllamaGenerator as OllamaGenerator,
)

__all__: list[str] = [
    "DEFAULT_OLLAMA_URL",
    "OllamaEmbedder",
    "OllamaError",
    "OllamaFormatIgnoredError",
    "OllamaGenerator",
    "OllamaThinkingUnsupportedError",
    "ThinkLevel",
]
