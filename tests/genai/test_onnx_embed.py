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


class TestPooling:
    def test_cls_pooling_takes_the_first_token(self) -> None:
        """BGE-style models are trained on ``[CLS]``; mean pooling drifts."""
        from tempest_fastapi_sdk.genai.onnx_embed import _pool

        hidden = np.array([[[1.0, 2.0], [9.0, 9.0]]], dtype=np.float32)
        mask = np.array([[1, 1]], dtype=np.int64)

        assert _pool(hidden, mask, "cls")[0].tolist() == [1.0, 2.0]
        assert _pool(hidden, mask, "mean")[0].tolist() == [5.0, 5.5]

    def test_pooled_output_is_used_as_is(self) -> None:
        """A 2-D ``(batch, dim)`` output is already a sentence vector."""
        from tempest_fastapi_sdk.genai.onnx_embed import _pool

        pooled = np.array([[0.5, 0.25]], dtype=np.float32)
        mask = np.array([[1, 1, 1]], dtype=np.int64)

        assert _pool(pooled, mask, "mean")[0].tolist() == [0.5, 0.25]

    def test_unexpected_rank_is_an_error(self) -> None:
        from tempest_fastapi_sdk.genai.onnx_embed import _pool

        with pytest.raises(ValueError, match="batch, tokens, dim"):
            _pool(np.zeros(3, dtype=np.float32), np.ones((1, 3)), "mean")

    def test_rejects_unknown_pooling(self) -> None:
        with pytest.raises(ValueError, match="pooling"):
            OnnxEmbedder("m.onnx", tokenizer="t.json", pooling="max")  # type: ignore[arg-type]

    async def test_cls_pooling_through_embed(self) -> None:
        class _Rising(_FakeSession):
            def run(self, _outputs: Any, feeds: dict[str, Any]) -> list[Any]:
                batch, tokens = feeds["input_ids"].shape
                hidden = np.arange(batch * tokens * 2, dtype=np.float32)
                return [hidden.reshape(batch, tokens, 2)]

        emb = OnnxEmbedder("m.onnx", tokenizer="t.json", pooling="cls")
        emb._session = _Rising()
        emb._tokenizer = _FakeTokenizer()

        assert await emb.embed(["a"]) == [[0.0, 1.0]]

    async def test_embed_prefers_the_named_embedding_output(self) -> None:
        """``outputs[0]`` is not always the embedding a graph exposes."""

        class _Output:
            def __init__(self, name: str) -> None:
                self.name = name

        class _TwoOutputs(_FakeSession):
            def get_outputs(self) -> list[_Output]:
                return [_Output("logits"), _Output("sentence_embedding")]

            def run(self, _outputs: Any, feeds: dict[str, Any]) -> list[Any]:
                batch = feeds["input_ids"].shape[0]
                return [
                    np.full((batch, 3, 4), 7.0, dtype=np.float32),
                    np.full((batch, 2), 0.5, dtype=np.float32),
                ]

        emb = OnnxEmbedder("m.onnx", tokenizer="t.json")
        emb._session = _TwoOutputs()
        emb._tokenizer = _FakeTokenizer()

        assert await emb.embed(["a"]) == [[0.5, 0.5]]


class TestPadding:
    def _tokenizer(self, vocab: dict[str, int]) -> Any:
        tokenizers = pytest.importorskip("tokenizers")
        model = tokenizers.models.WordLevel(vocab=vocab, unk_token="<unk>")
        tokenizer = tokenizers.Tokenizer(model)
        tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
        return tokenizer

    def test_roberta_style_vocab_pads_with_its_own_id(self) -> None:
        """RoBERTa/XLM-R put ``<pad>`` at id 1; id 0 is ``<s>``."""
        from tempest_fastapi_sdk.genai.onnx_embed import _configure_padding

        tokenizer = self._tokenizer(
            {"<s>": 0, "<pad>": 1, "</s>": 2, "<unk>": 3, "hi": 4, "there": 5},
        )
        _configure_padding(tokenizer)
        short, _ = tokenizer.encode_batch(["hi", "hi there"])

        assert short.ids == [4, 1]
        assert short.attention_mask == [1, 0]

    def test_bert_style_vocab_pads_with_zero(self) -> None:
        from tempest_fastapi_sdk.genai.onnx_embed import _configure_padding

        tokenizer = self._tokenizer({"[PAD]": 0, "<unk>": 1, "hi": 2, "there": 3})
        _configure_padding(tokenizer)
        short, _ = tokenizer.encode_batch(["hi", "hi there"])

        assert short.ids == [2, 0]

    def test_exported_padding_is_kept(self) -> None:
        from tempest_fastapi_sdk.genai.onnx_embed import _configure_padding

        tokenizer = self._tokenizer({"<unk>": 0, "<pad>": 1, "hi": 2, "x": 3})
        tokenizer.enable_padding(pad_id=3, pad_token="x", length=512)
        _configure_padding(tokenizer)

        assert tokenizer.padding["pad_id"] == 3
        assert tokenizer.padding["pad_token"] == "x"
        assert tokenizer.padding["length"] is None
