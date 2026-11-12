"""Search a known list of names by meaning, and by meaning plus spelling.

:class:`~tempest_fastapi_sdk.text.FuzzyIndex` answers "which name is spelled
like this?" — a typo costs one edit, and it needs no model. It cannot answer
"which name means this?": ``"tênis de corrida"`` shares no letters with
``"calçado esportivo"``, and ``"cidade maravilhosa"`` none with ``"Rio de
Janeiro"``. That is what an embedding model is for.

* :class:`SemanticNameIndex` embeds every name once (in batches, through
  ``embed_passages`` when the embedder tells queries from passages apart)
  and ranks names by cosine similarity to the embedded query.
* :class:`HybridNameIndex` runs both — a word-level lexical index for
  spelling, the semantic one for meaning — and fuses the two rankings with
  :func:`~tempest_fastapi_sdk.genai.rag.reciprocal_rank_fusion`, so neither
  score has to be on the other's scale. Each match keeps both scores, so a
  caller can see why it came back.

Any embedder with ``embed`` works (:class:`~tempest_fastapi_sdk.genai.Embedder`,
``OllamaEmbedder``, ``OpenAICompatEmbedder``, ``OnnxEmbedder``). With NumPy
installed the similarity is one matrix product; without it, a plain loop.
"""

from __future__ import annotations

import importlib.util
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from tempest_fastapi_sdk.genai.rag.fusion import reciprocal_rank_fusion
from tempest_fastapi_sdk.genai.rag.retriever import (
    SupportsEmbed,
    _embed_passages,
    _embed_query,
)
from tempest_fastapi_sdk.text import FuzzyIndex, normalize_text, similarity

_HAS_NUMPY: bool = importlib.util.find_spec("numpy") is not None
"""Whether the vectorized similarity path is available."""

MIN_LEXICAL_WORD_LENGTH: int = 3
"""Shortest word the lexical side indexes or scores.

``"de"``, ``"da"`` and ``"e"`` sit in most Portuguese names; counted, they made
``"tênis de corrida"`` spell a third of every name with a ``"de"`` in it.
"""


@dataclass(frozen=True, slots=True)
class NameMatch[T]:
    """One entry a name search returned.

    Attributes:
        key (str): The name as it was added.
        value (T): The value stored under it.
        score (float): The ranking score: cosine similarity for
            :class:`SemanticNameIndex`; the fused RRF score for
            :class:`HybridNameIndex` (only meaningful relative to the other
            matches of the same search).
        lexical_score (float | None): How well the query's words are spelled
            in the name: the mean, over the query words, of the best
            :func:`~tempest_fastapi_sdk.text.similarity` against the name's
            words (``0`` for a query word with no match). Set when the
            lexical side found the name.
        semantic_score (float | None): The cosine similarity, when the
            semantic index ranked the name.
    """

    key: str
    value: T
    score: float
    lexical_score: float | None = None
    semantic_score: float | None = None


def _normalized(vector: Sequence[float]) -> list[float]:
    """Scale a vector to unit length.

    Args:
        vector (Sequence[float]): Any vector.

    Returns:
        list[float]: The unit vector; the zero vector unchanged.
    """
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return list(vector)
    return [component / norm for component in vector]


class SemanticNameIndex[T]:
    """Names mapped to values, ranked by meaning through an embedder.

    Names are embedded lazily, all at once, on the first :meth:`search` after
    an :meth:`add` (or explicitly with :meth:`build`), so adding many names
    costs one batched embedding call, not one per name.

    Example::

        index = SemanticNameIndex(embedder, [("Calçado esportivo", 1)])
        matches = await index.search("tênis de corrida")
    """

    def __init__(
        self,
        embedder: SupportsEmbed,
        entries: Iterable[tuple[str, T]] = (),
    ) -> None:
        """Build the index; nothing is embedded yet.

        Args:
            embedder (SupportsEmbed): Turns texts into vectors.
            entries (Iterable[tuple[str, T]]): ``(name, value)`` pairs.
        """
        self.embedder: SupportsEmbed = embedder
        self._names: list[str] = []
        self._values: list[T] = []
        self._vectors: list[list[float]] = []
        self._matrix: Any = None
        for name, value in entries:
            self.add(name, value)

    def add(self, name: str, value: T) -> None:
        """Store ``value`` under ``name``; it is embedded on the next search.

        Args:
            name (str): The name. Blank names are ignored.
            value (T): The value to return for it.
        """
        if not name.strip():
            return
        self._names.append(name)
        self._values.append(value)

    def __len__(self) -> int:
        """Return the number of names.

        Returns:
            int: The name count.
        """
        return len(self._names)

    async def build(self) -> None:
        """Embed every name not embedded yet."""
        pending = self._names[len(self._vectors) :]
        if not pending:
            return
        vectors = await _embed_passages(self.embedder, pending)
        self._vectors.extend(_normalized(vector) for vector in vectors)
        self._matrix = None

    def _similarities(self, query: list[float]) -> list[float]:
        """Return the cosine similarity of ``query`` to every name.

        Args:
            query (list[float]): The unit query vector.

        Returns:
            list[float]: One similarity per name, in insertion order.
        """
        if _HAS_NUMPY:
            import numpy

            if self._matrix is None:
                self._matrix = numpy.asarray(self._vectors, dtype=numpy.float32)
            product = self._matrix @ numpy.asarray(query, dtype=numpy.float32)
            return [float(score) for score in product]
        return [
            sum(left * right for left, right in zip(vector, query, strict=True))
            for vector in self._vectors
        ]

    async def search(
        self,
        query: str,
        *,
        limit: int | None = 5,
        min_score: float = 0.0,
    ) -> list[NameMatch[T]]:
        """Return the names closest in meaning to ``query``.

        Args:
            query (str): What the user wrote.
            limit (int | None): Most matches to return; ``None`` for all.
            min_score (float): Lowest cosine similarity kept. What counts as
                "close" depends on the model; measure it on your names.

        Returns:
            list[NameMatch[T]]: Best first, with ``semantic_score`` set.
        """
        if not query.strip() or not self._names:
            return []
        await self.build()
        (vector,) = await _embed_query(self.embedder, [query])
        scores = self._similarities(_normalized(vector))
        ranked = sorted(range(len(scores)), key=lambda position: -scores[position])
        matches = [
            NameMatch(
                key=self._names[position],
                value=self._values[position],
                score=scores[position],
                semantic_score=scores[position],
            )
            for position in ranked
            if scores[position] >= min_score
        ]
        return matches if limit is None else matches[:limit]


class HybridNameIndex[T]:
    """Spelling and meaning together: fuzzy lookup and embeddings, fused.

    A name typed with a typo ranks high in the lexical list; a name the user
    described with other words ranks high in the semantic list; reciprocal
    rank fusion merges the two without putting edit distance and cosine
    similarity on one scale.

    The lexical side works **word by word**: ``"camizeta basica"`` is two
    typo-tolerant word lookups against the words of every name, so it finds
    ``"Camiseta básica de algodão"`` although the whole strings are far
    apart. A name ranks by the share of the query's words it spells.

    Example::

        index = HybridNameIndex(embedder, [("Rio de Janeiro", 3304557)])
        await index.search("rio de janiero")       # spelling
        await index.search("cidade maravilhosa")   # meaning
    """

    def __init__(
        self,
        embedder: SupportsEmbed,
        entries: Iterable[tuple[str, T]] = (),
        *,
        rrf_k: int = 60,
    ) -> None:
        """Build both indexes.

        Args:
            embedder (SupportsEmbed): Turns texts into vectors.
            entries (Iterable[tuple[str, T]]): ``(name, value)`` pairs. Two
                entries with the same name keep both values.
            rrf_k (int): RRF damping constant
                (:func:`~tempest_fastapi_sdk.genai.rag.reciprocal_rank_fusion`).
        """
        self.rrf_k: int = rrf_k
        self.lexical: FuzzyIndex[int] = FuzzyIndex()
        self.semantic: SemanticNameIndex[int] = SemanticNameIndex(embedder)
        self._words: list[frozenset[str]] = []
        self._names: list[str] = []
        self._values: list[T] = []
        for name, value in entries:
            self.add(name, value)

    def add(self, name: str, value: T) -> None:
        """Store ``value`` under ``name`` in both indexes.

        Args:
            name (str): The name. Blank names are ignored.
            value (T): The value to return for it.
        """
        if not name.strip():
            return
        position = len(self._names)
        self._names.append(name)
        self._values.append(value)
        words = frozenset(
            word
            for word in normalize_text(name).split()
            if len(word) >= MIN_LEXICAL_WORD_LENGTH
        )
        self._words.append(words)
        for word in words:
            self.lexical.add(word, position)
        self.semantic.add(name, position)

    def __len__(self) -> int:
        """Return the number of entries.

        Returns:
            int: The entry count.
        """
        return len(self._names)

    async def build(self) -> None:
        """Embed every name not embedded yet."""
        await self.semantic.build()

    async def search(
        self,
        query: str,
        *,
        limit: int | None = 5,
        cutoff: float = 0.8,
        candidates: int = 20,
        min_semantic_score: float = 0.0,
    ) -> list[NameMatch[T]]:
        """Return the entries whose name is spelled or means like ``query``.

        Args:
            query (str): What the user wrote.
            limit (int | None): Most matches to return; ``None`` for all.
            cutoff (float): Lowest spelling similarity for the lexical side.
            candidates (int): How many entries each side ranks before the
                fusion.
            min_semantic_score (float): Lowest cosine similarity the semantic
                side keeps.

        Returns:
            list[NameMatch[T]]: Best fused first; ``score`` is the RRF score,
            and ``lexical_score`` / ``semantic_score`` say which side found
            each entry.
        """
        if not normalize_text(query) or not self._names:
            return []
        lexical_scores = self._lexical_scores(query, cutoff)
        lexical_ranking = sorted(
            lexical_scores, key=lambda position: (-lexical_scores[position], position)
        )[:candidates]
        semantic = await self.semantic.search(
            query, limit=candidates, min_score=min_semantic_score
        )
        semantic_scores = {match.value: match.score for match in semantic}
        semantic_ranking = [match.value for match in semantic]
        fused = reciprocal_rank_fusion(
            [
                [str(position) for position in lexical_ranking],
                [str(position) for position in semantic_ranking],
            ],
            k=self.rrf_k,
        )
        rank_scores = {
            int(identifier): sum(
                1.0 / (self.rrf_k + rank)
                for ranking in (lexical_ranking, semantic_ranking)
                for rank, position in enumerate(ranking, start=1)
                if position == int(identifier)
            )
            for identifier in fused
        }
        matches = [
            NameMatch(
                key=self._names[int(identifier)],
                value=self._values[int(identifier)],
                score=rank_scores[int(identifier)],
                lexical_score=lexical_scores.get(int(identifier)),
                semantic_score=semantic_scores.get(int(identifier)),
            )
            for identifier in fused
        ]
        return matches if limit is None else matches[:limit]

    def _lexical_scores(self, query: str, cutoff: float) -> dict[int, float]:
        """Score every entry by how well it spells the query's words.

        Args:
            query (str): What the user wrote.
            cutoff (float): Lowest word similarity that counts as a match.

        Returns:
            dict[int, float]: Entry position to the mean, over the query
            words of at least :data:`MIN_LEXICAL_WORD_LENGTH` letters, of the
            best similarity among that entry's words; only entries matching
            at least one query word.
        """
        words = [
            word
            for word in normalize_text(query).split()
            if len(word) >= MIN_LEXICAL_WORD_LENGTH
        ]
        if not words:
            return {}
        best: dict[int, list[float]] = {}
        for index, word in enumerate(words):
            for match in self.lexical.search(word, limit=None, cutoff=cutoff):
                scores = best.setdefault(match.value, [0.0] * len(words))
                scores[index] = max(scores[index], similarity(word, match.key))
        return {position: sum(scores) / len(words) for position, scores in best.items()}


__all__: list[str] = [
    "MIN_LEXICAL_WORD_LENGTH",
    "HybridNameIndex",
    "NameMatch",
    "SemanticNameIndex",
]
