"""String similarity for typo-tolerant matching.

:func:`levenshtein` counts the single-character edits (insert, delete,
substitute, and swap of two neighbours — the optimal-string-alignment
variant) between two strings; :func:`similarity` turns that into a ratio
between ``0.0`` and ``1.0`` so a cutoff reads the same for short and long
names. :func:`trigrams` is the fingerprint :class:`~tempest_fastapi_sdk.text.FuzzyIndex`
uses to pick candidates before the costlier edit distance runs.
"""

from __future__ import annotations


def levenshtein(left: str, right: str, *, limit: int | None = None) -> int:
    """Return the edit distance between two strings.

    Insert, delete and substitute cost one each, and so does swapping two
    adjacent characters (``"paolo"`` -> ``"paulo"`` is one substitution,
    ``"soa"`` -> ``"sao"`` one swap): the optimal-string-alignment distance,
    which matches the way people mistype.

    Args:
        left (str): One string.
        right (str): The other.
        limit (int | None): Stop early and return ``limit + 1`` once every
            path is already longer than ``limit``. ``None`` computes the
            exact distance.

    Returns:
        int: The number of edits; ``limit + 1`` when ``limit`` is given and
        exceeded.
    """
    if left == right:
        return 0
    if len(left) < len(right):
        left, right = right, left
    if not right:
        return len(left)
    if limit is not None and len(left) - len(right) > limit:
        return limit + 1
    previous_previous: list[int] = []
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i] + [0] * len(right)
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            best = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if (
                i > 1
                and j > 1
                and left_char == right[j - 2]
                and left[i - 2] == right_char
            ):
                best = min(best, previous_previous[j - 2] + 1)
            current[j] = best
        if limit is not None and min(current) > limit:
            return limit + 1
        previous_previous, previous = previous, current
    return previous[-1]


def similarity(left: str, right: str) -> float:
    """Return how alike two strings are, from ``0.0`` to ``1.0``.

    ``1 - levenshtein / length of the longer string``: identical strings
    give ``1.0``, one typo in a ten-letter name ``0.9``.

    Args:
        left (str): One string.
        right (str): The other.

    Returns:
        float: The ratio; ``1.0`` for two empty strings.
    """
    longest = max(len(left), len(right))
    if longest == 0:
        return 1.0
    return 1.0 - levenshtein(left, right) / longest


def trigrams(text: str) -> frozenset[str]:
    """Return the set of three-character slices of ``text`` (padded).

    The string is padded with one space on each side, so the first and last
    letters take part (``"rio"`` -> ``{" ri", "rio", "io "}``) and even a
    one- or two-letter string has a trigram.

    Args:
        text (str): A normalized string.

    Returns:
        frozenset[str]: Its trigrams.
    """
    padded = f" {text} "
    return frozenset(padded[index : index + 3] for index in range(len(padded) - 2))


__all__: list[str] = [
    "levenshtein",
    "similarity",
    "trigrams",
]
