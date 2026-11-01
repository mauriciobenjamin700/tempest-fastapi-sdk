"""The recall algorithm every chat-memory backend shares.

:class:`~tempest_fastapi_sdk.genai.rag.ChatMemory` (ChromaDB) and
:class:`~tempest_fastapi_sdk.genai.rag.PgChatMemory` (pgvector) store
messages differently but rank them the same way: a similarity floor on the
raw cosine similarity, then a blend with an exponential recency decay. That
ranking lives here, once, so the two backends cannot drift apart. Nothing in
this module imports a database driver.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import NamedTuple

from tempest_fastapi_sdk.schemas.base import BaseSchema


class MemoryHit(BaseSchema):
    """One recalled chat message, ranked for relevance and recency.

    Attributes:
        content (str): The message text.
        role (str): The originating role (``"user"``, ``"assistant"``, …).
        chat_id (str): The chat the message belongs to.
        created_at (datetime | None): When the message was created (UTC),
            or ``None`` when the stored timestamp could not be parsed.
        similarity (float): Raw cosine similarity in ``[0, 1]`` (the value
            the similarity floor is applied to).
        score (float): Final ranking score after the recency-decay blend
            (equals ``similarity`` when recency re-ranking is disabled).
    """

    content: str
    role: str
    chat_id: str
    created_at: datetime | None = None
    similarity: float
    score: float


class _MemoryCandidate(NamedTuple):
    """A nearest-neighbour row before the floor and the recency re-rank.

    Attributes:
        content (str): The message text.
        role (str): The stored role.
        chat_id (str): The stored chat id.
        created_at (datetime | None): The stored creation instant.
        distance (float): Cosine distance to the query (``1 - cosine``).
    """

    content: str
    role: str
    chat_id: str
    created_at: datetime | None
    distance: float


def _check_candidate_multiplier(value: int) -> None:
    """Refuse an over-fetch factor below one.

    Args:
        value (int): The requested ``candidate_multiplier``.

    Raises:
        ValueError: When ``value`` is below 1.
    """
    if value < 1:
        raise ValueError("candidate_multiplier must be >= 1")


def _rank_hits(
    candidates: Iterable[_MemoryCandidate],
    *,
    floor: float,
    top_k: int,
    recency_weight: float,
    recency_halflife_days: float,
) -> list[MemoryHit]:
    """Apply the similarity floor, blend in recency and keep the best ``top_k``.

    The floor is applied to the *raw* similarity (``1 - distance`` clamped
    to ``[0, 1]``) so freshness can never drag a junk match above the
    threshold. The final score is
    ``(1 - recency_weight) * sim + recency_weight * sim * decay``; with
    ``recency_weight`` or ``recency_halflife_days`` at ``0`` it is the raw
    similarity.

    Args:
        candidates (Iterable[_MemoryCandidate]): The over-fetched rows.
        floor (float): Minimum raw similarity to keep a hit.
        top_k (int): Maximum number of hits to return.
        recency_weight (float): Recency blend weight in ``[0, 1]``.
        recency_halflife_days (float): Age (days) at which the decay halves.

    Returns:
        list[MemoryHit]: Hits ordered by descending final score.
    """
    now = datetime.now(UTC)
    scored: list[MemoryHit] = []
    for candidate in candidates:
        similarity: float = max(0.0, min(1.0, 1.0 - candidate.distance))
        if similarity < floor:
            continue
        if recency_weight > 0 and recency_halflife_days > 0:
            decay = _recency_decay(candidate.created_at, recency_halflife_days, now)
            effective = (
                1.0 - recency_weight
            ) * similarity + recency_weight * similarity * decay
        else:
            effective = similarity
        scored.append(
            MemoryHit(
                content=candidate.content,
                role=candidate.role,
                chat_id=candidate.chat_id,
                created_at=candidate.created_at,
                similarity=similarity,
                score=effective,
            )
        )
    scored.sort(key=lambda hit: hit.score, reverse=True)
    return scored[:top_k]


def _recency_decay(
    created_at: datetime | None,
    halflife_days: float,
    now: datetime,
) -> float:
    """Exponential decay factor in ``[0, 1]`` from a message's age.

    ``0.5 ** (age_days / halflife_days)`` — a hit created now returns 1.0,
    one exactly a half-life old returns 0.5. Missing/invalid timestamps
    return 1.0 (treat as fresh rather than penalise on a parser bug).

    Args:
        created_at (datetime | None): When the message was created.
        halflife_days (float): Age in days at which the factor halves.
        now (datetime): Reference "now" (UTC).

    Returns:
        float: The decay factor.
    """
    if created_at is None or halflife_days <= 0:
        return 1.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    age_seconds = max((now - created_at).total_seconds(), 0.0)
    age_days = age_seconds / 86400.0
    decay: float = 0.5 ** (age_days / halflife_days)
    return decay


__all__: list[str] = ["MemoryHit"]
