"""RAG evaluation harness — deterministic recall@k / MRR over a fixed corpus.

Feeds the measurable DoDs of roadmap items #4 (reranker), #6 (hybrid search)
and #13 (ONNX embeddings): a retrieval run produces a ranked list of doc ids
per query, and these pure metrics turn that into a reproducible number that a
PR can quote. No model or network here — the metrics are pure functions.
"""

from __future__ import annotations

from collections.abc import Sequence


def recall_at_k(
    ranked_ids: Sequence[str],
    relevant_ids: Sequence[str],
    k: int,
) -> float:
    """Return the fraction of relevant ids present in the top ``k`` results.

    Args:
        ranked_ids (Sequence[str]): Doc ids in retrieved order (best first).
        relevant_ids (Sequence[str]): The ids that count as relevant.
        k (int): Cutoff rank.

    Returns:
        float: ``|top_k ∩ relevant| / |relevant|`` in ``0..1``; ``0.0`` when
        ``relevant_ids`` is empty.
    """
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    top = ranked_ids[:k]
    hits = sum(1 for doc_id in top if doc_id in relevant)
    return hits / len(relevant)


def reciprocal_rank(ranked_ids: Sequence[str], relevant_id: str) -> float:
    """Return ``1/rank`` of the first hit, or ``0.0`` when absent.

    Args:
        ranked_ids (Sequence[str]): Doc ids in retrieved order (best first).
        relevant_id (str): The target id.

    Returns:
        float: Reciprocal of the 1-indexed rank of ``relevant_id``.
    """
    for rank, doc_id in enumerate(ranked_ids, start=1):
        if doc_id == relevant_id:
            return 1.0 / rank
    return 0.0


def mean_reciprocal_rank(cases: Sequence[tuple[Sequence[str], str]]) -> float:
    """Return the mean reciprocal rank over ``(ranked_ids, relevant_id)`` cases.

    Args:
        cases (Sequence[tuple[Sequence[str], str]]): One entry per query.

    Returns:
        float: Mean of the per-case reciprocal ranks; ``0.0`` when empty.
    """
    if not cases:
        return 0.0
    return sum(reciprocal_rank(ranked, target) for ranked, target in cases) / len(cases)
