"""ONNX text embeddings — vectors without torch.

`OnnxEmbedder` runs a sentence-embedding model exported to ONNX through ONNX
Runtime, so a service can embed text on CPU with a light dependency set
(`onnxruntime` + `tokenizers`) instead of pulling the full `torch` /
`transformers` stack. It satisfies the same
:class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed` protocol as
:class:`~tempest_fastapi_sdk.genai.Embedder`, so it drops into a ``Retriever``
or ``make_genai_router`` unchanged.

Pooling is a property of the model, not a preference: a sentence-transformers
model trained with mean pooling wants the **attention-mask-weighted mean** of
the token embeddings (not a naive average over padding), while BGE-style
models are trained on the ``[CLS]`` token. ``pooling=`` picks between them; a
graph exported with its pooling head baked in (a 2-D ``sentence_embedding``
output) is used as is. ``normalize=True`` L2-normalizes so cosine similarity
is a dot product. Needs the ``[genai-onnx]`` extra.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from tempest_fastapi_sdk.genai.embeddings import _l2_normalize
from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle

_Pooling = Literal["mean", "cls"]
"""How token embeddings become one sentence vector."""

_PAD_TOKENS: tuple[str, ...] = ("[PAD]", "<pad>", "<|pad|>")
"""Pad token spellings, in the order they are tried.

BERT-family vocabularies spell it ``[PAD]`` (id 0); RoBERTa, XLM-R and most
SentencePiece models spell it ``<pad>`` (id 1 in RoBERTa and XLM-R). The id is
always read from the vocabulary, never assumed.
"""

_POOLED_OUTPUT_NAMES: tuple[str, ...] = ("sentence_embedding", "pooler_output")
"""Output names that carry an already-pooled ``(batch, dim)`` embedding."""


def _require_onnx() -> tuple[Any, Any]:
    """Import ``onnxruntime`` + ``tokenizers`` or raise a helpful error.

    Returns:
        tuple[Any, Any]: ``(onnxruntime, tokenizers.Tokenizer)``.

    Raises:
        ImportError: When the ``[genai-onnx]`` extra is not installed.
    """
    try:
        import onnxruntime
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise ImportError(
            "ONNX embeddings require the optional [genai-onnx] extra "
            "(onnxruntime + tokenizers). Install with: "
            "pip install tempest-fastapi-sdk[genai-onnx]",
        ) from exc
    return onnxruntime, Tokenizer


def _mean_pool(token_embeddings: Any, attention_mask: Any) -> Any:
    """Attention-mask-weighted mean over the token axis.

    Args:
        token_embeddings (Any): ``(batch, tokens, dim)`` float array (the
            model's last hidden state).
        attention_mask (Any): ``(batch, tokens)`` 0/1 array marking real
            tokens vs padding.

    Returns:
        Any: ``(batch, dim)`` pooled embeddings — real tokens only, never
        diluted by padding.
    """
    import numpy as np

    mask = np.asarray(attention_mask, dtype=np.float32)[:, :, None]
    summed = np.sum(np.asarray(token_embeddings, dtype=np.float32) * mask, axis=1)
    counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
    return summed / counts


def _configure_padding(tokenizer: Any) -> None:
    """Enable batch padding with the tokenizer's own pad token.

    A bare ``enable_padding()`` pads with id ``0`` (``tokenizers`` 0.22),
    which in RoBERTa and XLM-R is ``<s>``, not ``<pad>``. So the pad token
    comes from the tokenizer itself: a ``tokenizer.json`` exported with a
    padding section keeps its pad id and token (the cached
    ``textdetox/xlmr-large-toxicity-classifier`` export carries
    ``pad_id=1``); otherwise the pad token is looked up in the vocabulary.

    Padding length stays dynamic (longest in the batch) either way: the
    exported sections checked set a fixed ``length`` (128 for
    ``all-MiniLM-L6-v2``, 512 for the XLM-R export), which would pad every
    short text to the full window.

    Args:
        tokenizer (Any): A ``tokenizers.Tokenizer``.
    """
    exported = getattr(tokenizer, "padding", None)
    if exported:
        tokenizer.enable_padding(
            pad_id=exported["pad_id"],
            pad_token=exported["pad_token"],
            pad_type_id=exported.get("pad_type_id", 0),
            direction=exported.get("direction", "right"),
        )
        return
    for token in _PAD_TOKENS:
        pad_id = tokenizer.token_to_id(token)
        if pad_id is not None:
            tokenizer.enable_padding(pad_id=pad_id, pad_token=token)
            return
    tokenizer.enable_padding()


def _pool(output: Any, attention_mask: Any, pooling: _Pooling) -> Any:
    """Turn one model output into ``(batch, dim)`` sentence vectors.

    Args:
        output (Any): The chosen output — ``(batch, tokens, dim)`` token
            embeddings, or an already-pooled ``(batch, dim)`` array.
        attention_mask (Any): ``(batch, tokens)`` mask for mean pooling.
        pooling (Literal["mean", "cls"]): ``"mean"`` or ``"cls"``.

    Returns:
        Any: ``(batch, dim)`` float32 array.

    Raises:
        ValueError: When the output is neither 2-D nor 3-D.
    """
    import numpy as np

    array = np.asarray(output, dtype=np.float32)
    if array.ndim == 2:
        return array
    if array.ndim != 3:
        raise ValueError(
            "expected a (batch, tokens, dim) or (batch, dim) output, "
            f"got shape {array.shape}",
        )
    if pooling == "cls":
        return array[:, 0, :]
    return _mean_pool(array, attention_mask)


def _select_output(session: Any, outputs: list[Any]) -> Any:
    """Pick the output that holds the embedding.

    A graph may expose several outputs, and ``outputs[0]`` is not always the
    token embeddings. An output named like a pooled embedding wins, then
    ``last_hidden_state``; only a graph with neither falls back to the first.

    Args:
        session (Any): The ONNX Runtime session.
        outputs (list[Any]): The arrays ``session.run(None, ...)`` returned.

    Returns:
        Any: The array to pool.
    """
    get_outputs = getattr(session, "get_outputs", None)
    names = [out.name for out in get_outputs()] if callable(get_outputs) else []
    by_name = dict(zip(names, outputs, strict=False))
    for name in (*_POOLED_OUTPUT_NAMES, "last_hidden_state"):
        if name in by_name:
            return by_name[name]
    return outputs[0]


class OnnxEmbedder:
    """Torch-free text embedder over ONNX Runtime.

    Example:

        >>> from tempest_fastapi_sdk.genai import OnnxEmbedder
        >>> emb = OnnxEmbedder(
        ...     "model.onnx",
        ...     tokenizer="sentence-transformers/all-MiniLM-L6-v2",
        ...     normalize=True,
        ... )
        >>> vectors = await emb.embed(["hello", "world"])

    Attributes:
        model_path (str): Path to the exported ONNX model.
        normalize (bool): Whether embeddings are L2-normalized.
        max_length (int): Max tokens per text.
        pooling (Literal["mean", "cls"]): ``"mean"`` or ``"cls"``.
        idle_unload_seconds (float | None): Idle threshold for
            :meth:`unload_if_idle`.
    """

    def __init__(
        self,
        model_path: str,
        *,
        tokenizer: str,
        tokenizer_revision: str | None = None,
        hf_token: str | None = None,
        normalize: bool = False,
        max_length: int = 512,
        providers: list[str] | None = None,
        pooling: Literal["mean", "cls"] = "mean",
        idle_unload_seconds: float | None = None,
    ) -> None:
        """Configure the embedder (does not load the model yet).

        The ONNX graph is already on local disk, so only the tokenizer can
        come from the Hub — the pinning keywords apply to it alone.
        ``tokenizers.Tokenizer.from_pretrained`` accepts ``revision`` and
        ``token`` and nothing else, so there is no offline flag to forward
        here; point ``tokenizer`` at a local ``tokenizer.json`` when the
        host must not reach the network.

        Args:
            model_path (str): Path to the ONNX model file.
            tokenizer (str): A HuggingFace tokenizer id (loaded via
                ``tokenizers.Tokenizer.from_pretrained``) or a path to a
                ``tokenizer.json`` (loaded via ``from_file``).
            tokenizer_revision (str | None): Branch, tag or commit sha for
                the Hub tokenizer; ignored for a local ``tokenizer.json``.
            hf_token (str | None): Hub token for a gated/private tokenizer.
            normalize (bool): L2-normalize the output vectors.
            max_length (int): Truncate/pad tokenization to this length.
            providers (list[str] | None): ONNX Runtime execution providers;
                ``None`` uses the runtime default (CPU).
            pooling (Literal["mean", "cls"]): How token embeddings become a sentence
                vector — ``"mean"`` (masked mean, what sentence-transformers
                models such as MiniLM are trained with) or ``"cls"`` (the
                first token, what BGE-style models are trained with). Match
                the model card; ignored when the graph already outputs a
                pooled ``(batch, dim)`` embedding.
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` drops the session after this many
                idle seconds, so ``ModelRegistry.unload_idle`` frees it
                like the torch loaders.

        Raises:
            ValueError: When ``pooling`` is not ``"mean"`` or ``"cls"``.
        """
        if pooling not in ("mean", "cls"):
            raise ValueError("pooling must be 'mean' or 'cls'")
        self.model_path = model_path
        self.tokenizer_ref = tokenizer
        self.tokenizer_source = ModelRef(
            model_id=tokenizer,
            revision=tokenizer_revision,
            token=hf_token,
        )
        self.normalize = normalize
        self.max_length = max_length
        self.providers = providers
        self.pooling: Literal["mean", "cls"] = pooling
        self.idle_unload_seconds = idle_unload_seconds
        self._session: Any = None
        self._tokenizer: Any = None
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self._session is not None,
        )

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the session and tokenizer are ready."""
        return self._session is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the session was last in use.

        Reads ``0.0`` while an embedding batch is in flight.

        Returns:
            float: Idle time in seconds.
        """
        return self._lifecycle.seconds_idle()

    def unload(self) -> None:
        """Drop the ONNX session and tokenizer. Safe when not loaded.

        Present so the embedder can live in a
        :class:`~tempest_fastapi_sdk.genai.ModelRegistry` and be reported by
        the runtime inventory alongside the torch loaders. The session holds
        far less than a torch model, but "far less" is not "nothing" on a
        box running several. While a batch is in flight the release waits
        for it.
        """
        self._lifecycle.unload()

    def _release(self) -> None:
        """Drop the session and tokenizer."""
        self._session = None
        self._tokenizer = None

    def unload_if_idle(self) -> bool:
        """Drop the session when it has been idle past the threshold.

        Returns:
            bool: ``True`` when this call unloaded it, ``False`` when it was
            already free, still in use, or no ``idle_unload_seconds`` was
            configured.
        """
        return self._lifecycle.unload_if_idle(self.idle_unload_seconds)

    def load(self) -> None:
        """Load the ONNX session and tokenizer. Idempotent and thread-safe."""
        self._lifecycle.load()

    def _build(self) -> None:  # pragma: no cover - needs onnxruntime + a real model
        """Create the session and tokenizer; called once, under the load lock."""
        onnxruntime, tokenizer_cls = _require_onnx()
        if self.tokenizer_ref.endswith(".json"):
            tokenizer = tokenizer_cls.from_file(self.tokenizer_ref)
        else:
            tokenizer = tokenizer_cls.from_pretrained(
                self.tokenizer_ref,
                **self.tokenizer_source.loader_kwargs(),
            )
        tokenizer.enable_truncation(max_length=self.max_length)
        _configure_padding(tokenizer)
        self._tokenizer = tokenizer
        self._session = onnxruntime.InferenceSession(
            self.model_path,
            providers=self.providers,
        )

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Embed one or many texts into vectors.

        Args:
            texts (str | list[str]): A single text or a list.
            batch_size (int): Max texts per ONNX run.

        Returns:
            list[list[float]]: One vector per input text; empty list for empty
            input.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        if not items:
            return []
        vectors = await asyncio.to_thread(self._embed_sync, items, batch_size)
        if self.normalize:
            return [_l2_normalize(vector) for vector in vectors]
        return vectors

    def _embed_sync(
        self,
        items: list[str],
        batch_size: int,
    ) -> list[list[float]]:
        """Blocking batched ONNX embedding, holding the session throughout.

        Args:
            items (list[str]): The texts.
            batch_size (int): Max texts per run.

        Returns:
            list[list[float]]: One vector per text.
        """
        import numpy as np

        with self._lifecycle.use():
            session = self._session
            tokenizer = self._tokenizer
            expected = {inp.name for inp in session.get_inputs()}
            vectors: list[list[float]] = []
            for start in range(0, len(items), batch_size):
                batch = items[start : start + batch_size]
                encodings = tokenizer.encode_batch(batch)
                input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
                attention_mask = np.array(
                    [e.attention_mask for e in encodings],
                    dtype=np.int64,
                )
                feeds: dict[str, Any] = {}
                if "input_ids" in expected:
                    feeds["input_ids"] = input_ids
                if "attention_mask" in expected:
                    feeds["attention_mask"] = attention_mask
                if "token_type_ids" in expected:
                    feeds["token_type_ids"] = np.zeros_like(input_ids)
                outputs = session.run(None, feeds)
                pooled = _pool(
                    _select_output(session, outputs),
                    attention_mask,
                    self.pooling,
                )
                vectors.extend(pooled.tolist())
            return vectors


__all__: list[str] = [
    "OnnxEmbedder",
]
