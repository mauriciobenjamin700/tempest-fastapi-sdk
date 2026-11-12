"""Typo-tolerant lookup over a fixed set of names.

:class:`FuzzyIndex` maps names to values (a city name to the city, a product
name to its id) and answers "which names look like this?" without comparing
the query with every entry. It keeps a trigram inverted index: a query's
trigrams pull only the names that share enough of them, and the exact edit
distance (:func:`~tempest_fastapi_sdk.text.levenshtein`) runs on that short
list alone.

The filter is lossless for the cutoff it is given. A name within ``d`` edits
of the query shares at least ``|trigrams(query)| - 4 * d`` trigrams with it
(one edit touches at most three trigrams, a swap of neighbours four), and
the longest distance a ``cutoff`` allows is bounded by the query length, so
every name the edit distance would accept reaches the edit distance.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from tempest_fastapi_sdk.text.normalize import normalize_text
from tempest_fastapi_sdk.text.similarity import levenshtein, trigrams


@dataclass(frozen=True, slots=True)
class FuzzyMatch[T]:
    """One entry that matched a query.

    Attributes:
        key (str): The normalized name that matched.
        value (T): The value stored under it.
        score (float): ``1.0`` for an exact match, lower for each edit
            (:func:`~tempest_fastapi_sdk.text.similarity`).
    """

    key: str
    value: T
    score: float


def max_edits(length: int, cutoff: float) -> int:
    """Return the most edits a query of ``length`` may have at ``cutoff``.

    ``similarity = 1 - d / L`` with ``L`` the longer length, and ``L`` is at
    most ``length + d``, so ``d <= (1 - cutoff) * length / cutoff``.

    Args:
        length (int): The normalized query length.
        cutoff (float): The lowest similarity accepted, in ``(0, 1]``.

    Returns:
        int: The bound on the edit distance.
    """
    return math.floor((1.0 - cutoff) * length / cutoff + 1e-9)


class FuzzyIndex[T]:
    """Names mapped to values, searchable exactly and with typos.

    Keys are normalized with :func:`~tempest_fastapi_sdk.text.normalize_text`
    on the way in and on every query, so ``"São Paulo"``, ``"sao paulo"``
    and ``"SAO PAULO"`` are one key. One key may hold several values (two
    towns with the same name); every value comes back.

    Example::

        index = FuzzyIndex([("São Paulo", 1), ("Santos", 2)])
        index.search("sao paolo")[0].value  # 1
    """

    def __init__(self, entries: Iterable[tuple[str, T]] = ()) -> None:
        """Build the index.

        Args:
            entries (Iterable[tuple[str, T]]): ``(name, value)`` pairs.
        """
        self._values: dict[str, list[T]] = {}
        self._keys: list[str] = []
        self._grams: list[frozenset[str]] = []
        self._postings: dict[str, list[int]] = {}
        for name, value in entries:
            self.add(name, value)

    def add(self, name: str, value: T) -> None:
        """Store ``value`` under ``name``.

        Args:
            name (str): The name, normalized here. A name that normalizes to
                an empty string is ignored.
            value (T): The value to return for it.
        """
        key = normalize_text(name)
        if not key:
            return
        bucket = self._values.get(key)
        if bucket is not None:
            bucket.append(value)
            return
        self._values[key] = [value]
        position = len(self._keys)
        self._keys.append(key)
        grams = trigrams(key)
        self._grams.append(grams)
        for gram in grams:
            self._postings.setdefault(gram, []).append(position)

    def __len__(self) -> int:
        """Return the number of distinct keys.

        Returns:
            int: The key count.
        """
        return len(self._keys)

    def exact(self, query: str) -> list[T]:
        """Return the values whose name normalizes to the query's.

        Args:
            query (str): The name to look up.

        Returns:
            list[T]: The values, in insertion order; empty when none.
        """
        return list(self._values.get(normalize_text(query), ()))

    def search(
        self,
        query: str,
        *,
        limit: int | None = 5,
        cutoff: float = 0.8,
    ) -> list[FuzzyMatch[T]]:
        """Return the entries whose name is at least ``cutoff`` similar.

        Args:
            query (str): What the user typed.
            limit (int | None): Most matches to return; ``None`` for all.
            cutoff (float): Lowest similarity accepted, in ``(0, 1]``.
                ``0.8`` accepts one edit in five letters.

        Returns:
            list[FuzzyMatch[T]]: Best first; ties by key. One match per
            value, so a key holding two values yields two matches with the
            same score.

        Raises:
            ValueError: When ``cutoff`` is outside ``(0, 1]``.
        """
        if not 0.0 < cutoff <= 1.0:
            raise ValueError(f"cutoff must be in (0, 1], got {cutoff}")
        key = normalize_text(query)
        if not key:
            return []
        if cutoff == 1.0:
            return [FuzzyMatch(key, value, 1.0) for value in self._values.get(key, ())]
        edits = max_edits(len(key), cutoff)
        query_grams = trigrams(key)
        needed = max(1, len(query_grams) - 4 * edits)
        shared: dict[int, int] = {}
        for gram in query_grams:
            for position in self._postings.get(gram, ()):
                shared[position] = shared.get(position, 0) + 1
        scored: list[tuple[float, str]] = []
        for position, count in shared.items():
            if count < needed:
                continue
            candidate = self._keys[position]
            if abs(len(candidate) - len(key)) > edits:
                continue
            distance = levenshtein(key, candidate, limit=edits)
            if distance > edits:
                continue
            score = 1.0 - distance / max(len(candidate), len(key))
            if score >= cutoff:
                scored.append((score, candidate))
        scored.sort(key=lambda item: (-item[0], item[1]))
        matches = [
            FuzzyMatch(candidate, value, score)
            for score, candidate in scored
            for value in self._values[candidate]
        ]
        return matches if limit is None else matches[:limit]


__all__: list[str] = [
    "FuzzyIndex",
    "FuzzyMatch",
    "max_edits",
]
