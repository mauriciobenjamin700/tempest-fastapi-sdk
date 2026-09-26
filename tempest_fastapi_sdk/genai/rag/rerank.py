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
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai._lifecycle import ModelLifecycle
from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.genai.schemas import (
    HardwareInfo,
    ModelDtype,
    precision_kwarg,
)
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
        revision: str | None = None,
        local_files_only: bool | None = None,
        trust_remote_code: bool = False,
        max_length: int = 512,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
    ) -> None:
        """Configure the reranker (does not load weights yet).

        Args:
            model_id (str): HuggingFace cross-encoder id.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            dtype (str | ModelDtype): Compute precision, or ``"auto"``.
            cache_dir (str | None): Where the downloaded weights are
                written and read back from. ``None`` uses the
                ``huggingface_hub`` default — ``$HF_HOME/hub``, or
                ``~/.cache/huggingface/hub`` when ``HF_HOME`` is unset —
                which is why the second run of a script starts instantly
                instead of downloading again. Point it at a mounted
                volume when the process is a container, so the layer does
                not re-download the model on every restart.
            hf_token (str | None): Hub token for gated/private models.
            revision (str | None): Branch, tag or commit sha to load;
                ``None`` follows the moving Hub default.
            local_files_only (bool | None): Load from the cache without
                touching the network — what an air-gapped or deploy-frozen
                host wants. ``None`` (the default) takes ``GENAI_OFFLINE``
                from the environment; passing the argument overrides it.
            trust_remote_code (bool): Allow the repository's own Python to
                run at load time.
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
        self.source = ModelRef(
            model_id=model_id,
            revision=revision,
            cache_dir=cache_dir,
            token=hf_token,
            local_files_only=local_files_only,
            trust_remote_code=trust_remote_code,
        )
        self.max_length = max_length
        self.idle_unload_seconds = idle_unload_seconds
        self._model: Any = None
        self._tokenizer: Any = None
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self._model is not None,
        )

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the weights are in memory."""
        return self._model is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the model was last in use.

        Reads ``0.0`` while a rerank is in flight.

        Returns:
            float: Idle time in seconds.
        """
        return self._lifecycle.seconds_idle()

    def load(self) -> None:
        """Download (if needed) and load the cross-encoder + tokenizer.

        Idempotent — a no-op once loaded. Called automatically by
        :meth:`rerank`. Safe to call from several threads at once:
        concurrent callers on a cold instance wait for one build.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        self._lifecycle.load()

    def _build(self) -> None:  # pragma: no cover - needs torch + a real model
        """Load the tokenizer and weights; called once, under the load lock.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        torch, transformers = _require_transformers()
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            self.model_id,
            **self.source.loader_kwargs(),
        )
        self._model = transformers.AutoModelForSequenceClassification.from_pretrained(
            self.model_id,
            **precision_kwarg(getattr(torch, self.dtype.value)),
            **self.source.loader_kwargs(),
        )
        self._model = self._model.to(self.device if self.device != "cpu" else "cpu")
        self._model.eval()

    def unload(self) -> None:
        """Free the model and its memory (VRAM/RAM). Safe when not loaded.

        While a rerank is in flight the release waits for it: the last call
        to finish drops the weights.
        """
        self._lifecycle.unload()

    def _release(self) -> None:
        """Drop the weights and tokenizer and return cached CUDA memory."""
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
            bool: ``True`` when it unloaded, ``False`` otherwise — including
            while a rerank is in flight.
        """
        return self._lifecycle.unload_if_idle(self.idle_unload_seconds)

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

    def _score_sync(
        self,
        query: str,
        chunks: list[Chunk],
    ) -> list[float]:
        """Load if needed and score, holding the model for the whole call.

        Args:
            query (str): The query text.
            chunks (list[Chunk]): The candidates.

        Returns:
            list[float]: One score per chunk.
        """
        with self._lifecycle.use():
            return self._score_pairs(query, chunks)

    def _score_pairs(  # pragma: no cover - needs torch + a real model
        self,
        query: str,
        chunks: list[Chunk],
    ) -> list[float]:
        """Score every ``(query, chunk)`` pair with the cross-encoder."""
        import torch

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
        return [float(score) for score in scores.tolist()]


__all__: list[str] = [
    "Reranker",
    "SupportsRerank",
]
