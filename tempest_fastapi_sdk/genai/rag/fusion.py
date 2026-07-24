"""Reciprocal Rank Fusion — merge several ranked lists into one.

RRF combines rankings from different retrievers (dense, BM25, …) without
needing their scores on a common scale: each item's fused score is the sum of
``1 / (k + rank)`` across the lists it appears in. A small ``k`` (default 60,
the value from the original RRF paper) damps the weight of low ranks. Pure and
dependency-free.
"""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = 60,
) -> list[str]:
    """Fuse several ranked id lists into a single ranking.

    Args:
        rankings (Sequence[Sequence[str]]): One ranked list of ids per
            retriever (best first). Ids absent from a list simply do not
            contribute that list's term.
        k (int): RRF damping constant; larger flattens the rank weighting.

    Returns:
        list[str]: The fused ids, best first. Ties keep first-seen order.
    """
    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    seen = 0
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            if item not in order:
                order[item] = seen
                seen += 1
    return sorted(scores, key=lambda item: (-scores[item], order[item]))


__all__: list[str] = [
    "reciprocal_rank_fusion",
]
