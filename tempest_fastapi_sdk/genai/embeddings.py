"""Local text embeddings over HuggingFace transformers.

`Embedder` loads an embedding model once and turns text into vectors on
your own hardware — for semantic search, RAG retrieval, clustering. It
batches, caches per-text vectors (optional), resolves device/precision
automatically and frees memory when idle, mirroring
:class:`~tempest_fastapi_sdk.genai.TextGenerator`.

``torch`` / ``transformers`` import lazily, so the module and its cache
helper import without the ``[genai]`` extra.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.schemas import HardwareInfo, ModelDtype
from tempest_fastapi_sdk.genai.text import auto_dtype_name, resolve_device


@runtime_checkable
class EmbeddingCache(Protocol):
    """A per-text vector cache (a subset of a dict / Redis wrapper)."""

    def get(self, key: str) -> list[float] | None:
        """Return the cached vector for ``key``, or ``None`` on a miss."""
        ...

    def set(self, key: str, value: list[float]) -> None:
        """Store ``value`` under ``key``."""
        ...


class InMemoryEmbeddingCache:
    """A trivial in-process embedding cache backed by a dict.

    Fine for a single process; for multi-worker reuse, pass a Redis-backed
    object satisfying :class:`EmbeddingCache` instead.
    """

    def __init__(self) -> None:
        """Initialize an empty cache."""
        self._store: dict[str, list[float]] = {}

    def get(self, key: str) -> list[float] | None:
        """Return the cached vector for ``key`` or ``None``."""
        return self._store.get(key)

    def set(self, key: str, value: list[float]) -> None:
        """Store ``value`` under ``key``."""
        self._store[key] = value


class Embedder:
    """A lazily-loaded local text-embedding model with optional caching.

    Example:

        >>> emb = Embedder("sentence-transformers/all-MiniLM-L6-v2")
        >>> vectors = await emb.embed(["hello", "world"])
        >>> emb.unload()

    Attributes:
        model_id (str): The HuggingFace model id.
        device (str): Resolved device.
        dtype (ModelDtype): Resolved compute precision.
        idle_unload_seconds (float | None): Idle threshold for
            :meth:`unload_if_idle`.
    """

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "auto",
        dtype: str | ModelDtype = "auto",
        cache: EmbeddingCache | None = None,
        cache_dir: str | None = None,
        hf_token: str | None = None,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
    ) -> None:
        """Configure the embedder (does not load weights yet).

        Args:
            model_id (str): HuggingFace model id.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            dtype (str | ModelDtype): Compute precision or ``"auto"``.
            cache (EmbeddingCache | None): Optional per-text vector cache;
                a cache hit skips loading the model entirely.
            cache_dir (str | None): Weights cache directory.
            hf_token (str | None): Hub token for gated/private models.
            idle_unload_seconds (float | None): Idle threshold for
                :meth:`unload_if_idle`.
            hardware (HardwareInfo | None): Injected snapshot for device
                resolution (tests); probed when ``None``.
        """
        self.model_id = model_id
        self.device = resolve_device(device, hardware)
        self.dtype = (
            ModelDtype(auto_dtype_name(self.device))
            if dtype == "auto"
            else ModelDtype(dtype)
        )
        self.cache = cache
        self.cache_dir = cache_dir
        self.hf_token = hf_token
        self.idle_unload_seconds = idle_unload_seconds
        self._model: Any = None
        self._tokenizer: Any = None
        self._last_used: float = time.monotonic()

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the weights are in memory."""
        return self._model is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the last embed (or load)."""
        return time.monotonic() - self._last_used

    def _touch(self) -> None:
        """Reset the idle clock."""
        self._last_used = time.monotonic()

    def _cache_key(self, text: str) -> str:
        """Return the cache key for one text under this model."""
        return f"{self.model_id}::{text}"

    def load(self) -> None:  # pragma: no cover - needs torch + a real model
        """Load the embedding model + tokenizer into memory (idempotent).

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        if self.is_loaded:
            return
        from tempest_fastapi_sdk.genai.text import _require_transformers

        torch, transformers = _require_transformers()
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            token=self.hf_token,
        )
        model = transformers.AutoModel.from_pretrained(
            self.model_id,
            torch_dtype=getattr(torch, self.dtype.value),
            cache_dir=self.cache_dir,
            token=self.hf_token,
        )
        self._model = model.to(self.device)
        self._touch()

    def unload(self) -> None:
        """Free the model and its memory. Safe when not loaded."""
        if self._model is None:
            return
        self._model = None
        self._tokenizer = None
        try:  # pragma: no cover - only meaningful with torch + CUDA
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def unload_if_idle(self) -> bool:
        """Unload the model once idle past ``idle_unload_seconds``.

        Returns:
            bool: ``True`` when it unloaded, ``False`` otherwise.
        """
        if (
            self.idle_unload_seconds is None
            or not self.is_loaded
            or self.seconds_idle < self.idle_unload_seconds
        ):
            return False
        self.unload()
        return True

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Embed one or many texts into vectors.

        Checks the cache per text first; only cache misses hit the model
        (so an all-hit call never loads the weights). Misses are embedded
        in batches and written back to the cache.

        Args:
            texts (str | list[str]): A single text or a list of texts.
            batch_size (int): Max texts per model forward pass.

        Returns:
            list[list[float]]: One vector per input text, in input order.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        results: list[list[float] | None] = [None] * len(items)
        missing: list[int] = []
        for index, text in enumerate(items):
            cached = self.cache.get(self._cache_key(text)) if self.cache else None
            if cached is not None:
                results[index] = cached
            else:
                missing.append(index)

        if missing:
            to_embed = [items[i] for i in missing]
            vectors = await asyncio.to_thread(
                self._embed_many,
                to_embed,
                batch_size,
            )
            for index, vector in zip(missing, vectors, strict=True):
                results[index] = vector
                if self.cache is not None:
                    self.cache.set(self._cache_key(items[index]), vector)

        return [vector for vector in results if vector is not None]

    def _embed_many(  # pragma: no cover - needs torch + a real model
        self,
        texts: list[str],
        batch_size: int,
    ) -> list[list[float]]:
        """Blocking batched embedding with mean pooling over tokens."""
        self.load()
        import torch

        out: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                return_tensors="pt",
            ).to(self.device)
            with torch.no_grad():
                model_out = self._model(**encoded)
            hidden = model_out.last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).type_as(hidden)
            summed = (hidden * mask).sum(dim=1)
            counts = mask.sum(dim=1).clamp(min=1e-9)
            pooled = summed / counts
            out.extend(pooled.cpu().tolist())
        self._touch()
        return out


__all__: list[str] = [
    "Embedder",
    "EmbeddingCache",
    "InMemoryEmbeddingCache",
]
