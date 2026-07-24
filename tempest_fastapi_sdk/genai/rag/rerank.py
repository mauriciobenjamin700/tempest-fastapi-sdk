"""Cross-encoder reranking for RAG retrieval.

A bi-encoder retriever (embed query, embed chunks, cosine) is fast but coarse:
it never sees query and chunk together. A **cross-encoder** scores each
``(query, chunk)`` pair jointly and is far more precise — too slow to run over a
whole corpus, but ideal as a second stage over the top-N candidates a retriever
already narrowed down.

`Reranker` wraps an ``AutoModelForSequenceClassification`` cross-encoder (e.g.
``cross-encoder/ms-marco-MiniLM-L-6-v2``), runs on your own hardware, lazily
loads the weights, and can free VRAM when idle — same lifecycle as
:class:`~tempest_fastapi_sdk.genai.text.TextGenerator`. Needs the ``[genai]``
extra (``torch`` / ``transformers``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.schemas import HardwareInfo, ModelDtype
from tempest_fastapi_sdk.genai.text import (
    _require_transformers,
    auto_dtype_name,
    resolve_device,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.rag.schemas import Chunk


@runtime_checkable
class SupportsRerank(Protocol):
    """Anything that reorders chunks by relevance to a query."""

    async def rerank(
        self,
        query: str,
        chunks: Sequence[Chunk],
        *,
        top_k: int | None = ...,
    ) -> list[Chunk]:
        """Return ``chunks`` reordered by relevance (best first)."""
        ...


def _rank_by_scores(
    chunks: Sequence[Chunk],
    scores: Sequence[float],
    top_k: int | None,
) -> list[Chunk]:
    """Attach ``scores`` to ``chunks``, sort desc, and truncate to ``top_k``.

    Args:
        chunks (Sequence[Chunk]): The candidates, aligned with ``scores``.
        scores (Sequence[float]): One relevance score per chunk.
        top_k (int | None): Keep only the best ``top_k`` (all when ``None``).

    Returns:
        list[Chunk]: Chunks with ``.score`` set, ordered best-first.
    """
    for chunk, score in zip(chunks, scores, strict=True):
        chunk.score = float(score)
    ranked = sorted(chunks, key=lambda chunk: chunk.score or 0.0, reverse=True)
    return ranked[:top_k] if top_k is not None else ranked


class Reranker:
    """A lazily-loaded cross-encoder that reranks retrieved chunks.

    Example:

        >>> from tempest_fastapi_sdk.genai.rag import Reranker
        >>> reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2")
        >>> best = await reranker.rerank("how to refund?", candidates, top_k=5)

    Attributes:
        model_id (str): The HuggingFace cross-encoder id.
        device (str): The resolved device (``cuda`` / ``mps`` / ``cpu``).
        dtype (ModelDtype): The resolved compute precision.
        idle_unload_seconds (float | None): Idle threshold for
            :meth:`unload_if_idle`.
    """

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "auto",
        dtype: str | ModelDtype = "auto",
        cache_dir: str | None = None,
        hf_token: str | None = None,
        max_length: int = 512,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
    ) -> None:
        """Configure the reranker (does not load weights yet).

        Args:
            model_id (str): HuggingFace cross-encoder id.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            dtype (str | ModelDtype): Compute precision, or ``"auto"``.
            cache_dir (str | None): Where to cache downloaded weights.
            hf_token (str | None): Hub token for gated/private models.
            max_length (int): Max tokens per ``(query, chunk)`` pair.
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` frees the model after this idle window.
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
        self.cache_dir = cache_dir
        self.hf_token = hf_token
        self.max_length = max_length
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
        """Return seconds since the last rerank (or load)."""
        return time.monotonic() - self._last_used

    def load(self) -> None:  # pragma: no cover - needs torch + a real model
        """Download (if needed) and load the cross-encoder + tokenizer.

        Idempotent — a no-op once loaded. Called automatically by
        :meth:`rerank`.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        if self.is_loaded:
            return
        torch, transformers = _require_transformers()
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            token=self.hf_token,
        )
        self._model = transformers.AutoModelForSequenceClassification.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            token=self.hf_token,
            torch_dtype=getattr(torch, self.dtype.value),
        )
        self._model = self._model.to(self.device if self.device != "cpu" else "cpu")
        self._model.eval()
        self._last_used = time.monotonic()

    def unload(self) -> None:
        """Free the model and its memory (VRAM/RAM). Safe when not loaded."""
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
        """Unload the model when idle past ``idle_unload_seconds``.

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

    async def rerank(
        self,
        query: str,
        chunks: Sequence[Chunk],
        *,
        top_k: int | None = None,
    ) -> list[Chunk]:
        """Reorder ``chunks`` by cross-encoder relevance to ``query``.

        Args:
            query (str): The query text.
            chunks (Sequence[Chunk]): Candidate chunks (e.g. a retriever's
                top-N). Each chunk's ``.score`` is overwritten with the
                cross-encoder score.
            top_k (int | None): Keep only the best ``top_k`` (all when
                ``None``).

        Returns:
            list[Chunk]: Reranked chunks, best first. Empty input → empty list.
        """
        if not chunks:
            return []
        scores = await asyncio.to_thread(self._score_sync, query, list(chunks))
        return _rank_by_scores(chunks, scores, top_k)

    def _score_sync(  # pragma: no cover - needs torch + a real model
        self,
        query: str,
        chunks: list[Chunk],
    ) -> list[float]:
        """Score every ``(query, chunk)`` pair with the cross-encoder."""
        import torch

        self.load()
        pairs = [[query, chunk.text] for chunk in chunks]
        inputs = self._tokenizer(
            pairs,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        ).to(self._model.device)
        with torch.no_grad():
            logits = self._model(**inputs).logits
        scores = logits[:, 0] if logits.shape[-1] == 1 else logits[:, -1]
        self._last_used = time.monotonic()
        return [float(score) for score in scores.tolist()]


__all__: list[str] = [
    "Reranker",
    "SupportsRerank",
]
