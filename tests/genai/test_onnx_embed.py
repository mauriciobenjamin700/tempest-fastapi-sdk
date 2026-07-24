"""Tests for the ONNX embedder — pooling + wiring (no onnxruntime/model)."""

from __future__ import annotations

from typing import Any

import pytest

from tempest_fastapi_sdk.genai import OnnxEmbedder
from tempest_fastapi_sdk.genai.onnx_embed import _mean_pool

np = pytest.importorskip("numpy")


class TestMeanPool:
    def test_masks_out_padding(self) -> None:
        token_embeddings = np.array(
            [[[2.0, 4.0], [4.0, 8.0], [999.0, 999.0]]],
            dtype=np.float32,
        )
        attention_mask = np.array([[1, 1, 0]], dtype=np.int64)
        pooled = _mean_pool(token_embeddings, attention_mask)
        assert pooled.shape == (1, 2)
        assert pooled[0].tolist() == pytest.approx([3.0, 6.0])

    def test_all_tokens_when_mask_full(self) -> None:
        token_embeddings = np.array([[[1.0], [3.0]]], dtype=np.float32)
        attention_mask = np.array([[1, 1]], dtype=np.int64)
        pooled = _mean_pool(token_embeddings, attention_mask)
        assert pooled[0].tolist() == pytest.approx([2.0])


class _FakeEncoding:
    def __init__(self, ids: list[int], mask: list[int]) -> None:
        self.ids = ids
        self.attention_mask = mask


class _FakeTokenizer:
    def encode_batch(self, batch: list[str]) -> list[_FakeEncoding]:
        return [_FakeEncoding([5, 6, 0], [1, 1, 0]) for _ in batch]


class _FakeInput:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSession:
    def get_inputs(self) -> list[_FakeInput]:
        return [_FakeInput("input_ids"), _FakeInput("attention_mask")]

    def run(self, _outputs: Any, feeds: dict[str, Any]) -> list[Any]:
        batch, tokens = feeds["input_ids"].shape
        return [np.ones((batch, tokens, 2), dtype=np.float32)]


class TestEmbedWiring:
    async def test_embed_pools_and_returns_vectors(self) -> None:
        emb = OnnxEmbedder("m.onnx", tokenizer="t.json")
        emb._session = _FakeSession()
        emb._tokenizer = _FakeTokenizer()
        vectors = await emb.embed(["a", "b"])
        assert vectors == [[1.0, 1.0], [1.0, 1.0]]

    async def test_normalize_makes_unit_vectors(self) -> None:
        emb = OnnxEmbedder("m.onnx", tokenizer="t.json", normalize=True)
        emb._session = _FakeSession()
        emb._tokenizer = _FakeTokenizer()
        (vector, _) = await emb.embed(["a", "b"])
        norm = sum(x * x for x in vector) ** 0.5
        assert norm == pytest.approx(1.0)

    async def test_empty_input_returns_empty(self) -> None:
        emb = OnnxEmbedder("m.onnx", tokenizer="t.json")
        assert await emb.embed([]) == []
