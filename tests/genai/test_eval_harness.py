"""Tests for the RAG evaluation harness (camada 1)."""

from __future__ import annotations

from tests.genai._eval import (
    mean_reciprocal_rank,
    recall_at_k,
    reciprocal_rank,
)
from tests.genai._eval.corpus import CORPUS, PROPER_NOUN_QUERIES


class TestRecallAtK:
    def test_hit_within_k(self) -> None:
        assert recall_at_k(["a", "b", "c"], ["b"], k=3) == 1.0

    def test_miss_outside_k(self) -> None:
        assert recall_at_k(["a", "b", "c"], ["c"], k=2) == 0.0

    def test_empty_relevant_is_zero(self) -> None:
        assert recall_at_k(["a"], [], k=1) == 0.0

    def test_partial_recall(self) -> None:
        assert recall_at_k(["a", "b"], ["a", "x"], k=2) == 0.5


class TestReciprocalRank:
    def test_first_position(self) -> None:
        assert reciprocal_rank(["a", "b"], "a") == 1.0

    def test_second_position(self) -> None:
        assert reciprocal_rank(["a", "b"], "b") == 0.5

    def test_absent(self) -> None:
        assert reciprocal_rank(["a", "b"], "z") == 0.0


class TestMeanReciprocalRank:
    def test_average(self) -> None:
        cases: list[tuple[list[str], str]] = [(["a", "b"], "a"), (["a", "b"], "b")]
        assert mean_reciprocal_rank(cases) == 0.75

    def test_empty(self) -> None:
        assert mean_reciprocal_rank([]) == 0.0


class TestCorpus:
    def test_queries_reference_real_docs(self) -> None:
        ids = {doc.id for doc in CORPUS}
        assert all(query.relevant_id in ids for query in PROPER_NOUN_QUERIES)
