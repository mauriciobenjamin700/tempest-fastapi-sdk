"""Text normalization for matching what people type against known names.

Matching "sao paulo", "São Paulo" and "SÃO  PAULO!" as the same thing needs
one canonical form: accents stripped, case folded, punctuation turned into
spaces, whitespace collapsed. :func:`normalize_text` is that form, and
:func:`tokenize` splits it while remembering where each token came from in
the original string, so a match can be reported (or highlighted) on the text
the user actually wrote.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_WORD: re.Pattern[str] = re.compile(r"[^\W_]+", re.UNICODE)
"""A run of letters or digits; everything else separates tokens."""


def strip_accents(text: str) -> str:
    """Remove combining marks: ``"São Tomé"`` -> ``"Sao Tome"``.

    Decomposes to NFKD and drops every combining character, so a letter keeps
    its base and loses its accent; ``"ç"`` becomes ``"c"``.

    Args:
        text (str): Any string.

    Returns:
        str: The string without combining marks.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_text(text: str) -> str:
    """Return the canonical form used for matching.

    Accents stripped (:func:`strip_accents`), case folded, every run of
    characters that is not a letter or digit replaced by one space, and the
    ends trimmed: ``"  São José-dos Campos/SP "`` ->
    ``"sao jose dos campos sp"``.

    Args:
        text (str): Any string.

    Returns:
        str: The normalized string; empty when ``text`` has no letter or
        digit.
    """
    return " ".join(_WORD.findall(strip_accents(text).casefold()))


@dataclass(frozen=True, slots=True)
class Token:
    """One normalized word and where it sits in the original text.

    Attributes:
        text (str): The normalized word (:func:`normalize_text` of it).
        start (int): Offset of its first character in the original string.
        end (int): Offset just past its last character.
        original (str): The word as written, ``original[start:end]``.
    """

    text: str
    start: int
    end: int
    original: str


def tokenize(text: str) -> list[Token]:
    """Split ``text`` into normalized words with their original offsets.

    Args:
        text (str): Any string.

    Returns:
        list[Token]: One token per run of letters or digits, in order. A
        word whose normalized form is empty (a lone combining mark) is
        skipped.
    """
    tokens: list[Token] = []
    for match in _WORD.finditer(text):
        normalized = normalize_text(match.group())
        if normalized:
            tokens.append(
                Token(
                    text=normalized,
                    start=match.start(),
                    end=match.end(),
                    original=match.group(),
                )
            )
    return tokens


__all__: list[str] = [
    "Token",
    "normalize_text",
    "strip_accents",
    "tokenize",
]
