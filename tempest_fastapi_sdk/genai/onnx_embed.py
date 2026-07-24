"""ONNX text embeddings — vectors without torch.

`OnnxEmbedder` runs a sentence-embedding model exported to ONNX through ONNX
Runtime, so a service can embed text on CPU with a light dependency set
(`onnxruntime` + `tokenizers`) instead of pulling the full `torch` /
`transformers` stack. It satisfies the same
:class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed` protocol as
:class:`~tempest_fastapi_sdk.genai.Embedder`, so it drops into a ``Retriever``
or ``make_genai_router`` unchanged.

Pooling matters: a correct sentence embedding is the **attention-mask-weighted
mean** of the token embeddings, not a naive average over padding. :func:`_mean_pool`
does the masked mean; ``normalize=True`` L2-normalizes so cosine similarity is a
dot product. Needs the ``[genai-onnx]`` extra.
"""

from __future__ import annotations

import asyncio
from typing import Any

from tempest_fastapi_sdk.genai.embeddings import _l2_normalize


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
    """

    def __init__(
        self,
        model_path: str,
        *,
        tokenizer: str,
        normalize: bool = False,
        max_length: int = 512,
        providers: list[str] | None = None,
    ) -> None:
        """Configure the embedder (does not load the model yet).

        Args:
            model_path (str): Path to the ONNX model file.
            tokenizer (str): A HuggingFace tokenizer id (loaded via
                ``tokenizers.Tokenizer.from_pretrained``) or a path to a
                ``tokenizer.json`` (loaded via ``from_file``).
            normalize (bool): L2-normalize the output vectors.
            max_length (int): Truncate/pad tokenization to this length.
            providers (list[str] | None): ONNX Runtime execution providers;
                ``None`` uses the runtime default (CPU).
        """
        self.model_path = model_path
        self.tokenizer_ref = tokenizer
        self.normalize = normalize
        self.max_length = max_length
        self.providers = providers
        self._session: Any = None
        self._tokenizer: Any = None

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the session and tokenizer are ready."""
        return self._session is not None

    def load(self) -> None:  # pragma: no cover - needs onnxruntime + a real model
        """Load the ONNX session and tokenizer. Idempotent."""
        if self.is_loaded:
            return
        onnxruntime, tokenizer_cls = _require_onnx()
        self._session = onnxruntime.InferenceSession(
            self.model_path,
            providers=self.providers,
        )
        loader = (
            tokenizer_cls.from_file
            if self.tokenizer_ref.endswith(".json")
            else tokenizer_cls.from_pretrained
        )
        self._tokenizer = loader(self.tokenizer_ref)
        self._tokenizer.enable_truncation(max_length=self.max_length)
        self._tokenizer.enable_padding()

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

    def _embed_sync(  # pragma: no cover - needs onnxruntime + a real model
        self,
        items: list[str],
        batch_size: int,
    ) -> list[list[float]]:
        """Blocking batched ONNX embedding with masked mean pooling."""
        import numpy as np

        self.load()
        expected = {inp.name for inp in self._session.get_inputs()}
        vectors: list[list[float]] = []
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            encodings = self._tokenizer.encode_batch(batch)
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
            outputs = self._session.run(None, feeds)
            pooled = _mean_pool(outputs[0], attention_mask)
            vectors.extend(pooled.tolist())
        return vectors


__all__: list[str] = [
    "OnnxEmbedder",
]
