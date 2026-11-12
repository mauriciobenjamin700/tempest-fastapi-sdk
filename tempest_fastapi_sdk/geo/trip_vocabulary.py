"""Brazilian Portuguese vocabulary for :func:`parse_trip_intent`.

Every word here is already normalized (lowercase, no accents), the form
:func:`~tempest_fastapi_sdk.text.normalize_text` produces, so the parser and
a table test read the same lists. Ported from the trip search of the BusCar
transport backend (``src/core/constants/route_search.py``), where each list
was fixed by a test over real phrases.
"""

from __future__ import annotations

import re
from datetime import time

ORIGIN_MARKERS: frozenset[str] = frozenset({"de", "da", "do", "desde"})
"""Prepositions that introduce the origin city."""

ORIGIN_VERBS: frozenset[str] = frozenset(
    {"saindo", "partindo", "sair", "sai", "saio", "partir"}
)
"""Departure verbs; the preposition after them is skipped."""

DESTINATION_MARKERS: frozenset[str] = frozenset(
    {"para", "pra", "pro", "ate", "destino", "rumo"}
)
"""Words that introduce the destination city."""

DESTINATION_VERBS: frozenset[str] = frozenset(
    {"indo", "ir", "vou", "chegando", "chegar", "viajar", "voltar"}
)
"""Arrival verbs; the preposition after them is skipped."""

ORIGIN_VERB_LINKERS: frozenset[str] = frozenset({"de", "da", "do"})
"""Prepositions skipped after a departure verb (``"saindo de"``)."""

DESTINATION_VERB_LINKERS: frozenset[str] = frozenset(
    {"para", "pra", "pro", "a", "ao", "em", "no", "na", "ate"}
)
"""Prepositions skipped after an arrival verb (``"indo para"``).

``"de"`` is left out on purpose: in ``"ir de Teresina"`` the arrival verb
comes before the origin, and the preposition decides.
"""

STATE_QUALIFIERS: frozenset[str] = frozenset({"no", "na", "em", "do", "da", "de"})
"""Prepositions that may tie a city to its state (``"Batalha no Piauí"``)."""

FALLBACK_STOPWORDS: frozenset[str] = frozenset(
    {
        "quero",
        "queria",
        "gostaria",
        "preciso",
        "uma",
        "um",
        "viagem",
        "passagem",
        "passagens",
        "carona",
        "onibus",
        "carro",
        "vaga",
        "vagas",
        "tem",
        "algum",
        "alguma",
        "com",
        "por",
        "favor",
        "e",
        "o",
        "a",
        "os",
        "as",
        "que",
    }
)
"""Words never read as a city when the phrase has no preposition."""

UNRECOGNIZED_CITY_IGNORED_WORDS: frozenset[str] = frozenset(
    {"aqui", "ali", "casa", "novo", "volta", "onde", "qualquer", "lugar"}
)
"""Words after a preposition that are not a city the user said.

``"pra casa"``, ``"de novo"`` and ``"de volta"`` must not come back as an
unrecognized city to ask about.
"""

VOCALIZED_L: re.Pattern[str] = re.compile(r"l(?=[bcdfgjklmnpqrstvwxyz]|\b)")
"""An ``l`` closing a syllable, which spoken Brazilian Portuguese says as ``u``.

Matches ``l`` before a consonant or at a word end (``"sobral"`` ->
``"sobrau"``, ``"altos"`` -> ``"autos"``, ``"palmas"`` -> ``"paumas"``); the
``lh`` digraph does not vocalize (``"ilha"``). Speech-to-text writes what it
hears, so ``"sobrau"`` is one edit from ``"sobral"`` — similarity ``0.833``,
just below :data:`TRIP_FUZZY_CUTOFF`, and lowering the cutoff would raise
false positives on short names. Both sides get the same key and match
exactly on it instead.
"""

ROMAN_ORDINALS: dict[str, str] = {
    "i": "primeiro",
    "ii": "segundo",
    "iii": "terceiro",
    "iv": "quarto",
    "v": "quinto",
    "vi": "sexto",
    "vii": "setimo",
    "viii": "oitavo",
    "ix": "nono",
    "x": "decimo",
    "xiii": "treze",
    "xxiii": "vinte e tres",
}
"""Roman numerals in municipality names to the way people say them.

``"Pedro II"`` is said ``"Pedro Segundo"`` and transcribed that way; the
index adds the spoken form as another name. Only the last word of a name is
rewritten, where these numerals appear (``"Pedro II"``, ``"Floriano
Peixoto"`` is untouched).
"""

TODAY_WORDS: frozenset[str] = frozenset({"hoje"})
"""Words meaning today."""

TOMORROW_WORD: str = "amanha"
"""Tomorrow, normalized."""

DAY_AFTER_TOMORROW: tuple[str, ...] = ("depois", "de", "amanha")
"""The day after tomorrow, in tokens."""

WEEKDAYS: dict[str, int] = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
}
"""Spoken weekday to Python's ``weekday()``."""

NEXT_WEEK_PREFIXES: frozenset[str] = frozenset({"proxima", "proximo"})
"""Prefixes that push the weekday past today (``"próxima sexta"``)."""

NEXT_WEEK_SUFFIX: tuple[str, ...] = ("que", "vem")
"""Suffix with the same effect (``"sexta que vem"``)."""

MONTHS: dict[str, int] = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
"""Spoken month to its number."""

DAY_WORD: str = "dia"
"""The word introducing a day of the month (``"dia 20"``)."""

PERIODS: dict[str, tuple[time, time]] = {
    "madrugada": (time(0, 0), time(5, 59)),
    "manha": (time(6, 0), time(11, 59)),
    "tarde": (time(12, 0), time(17, 59)),
    "noite": (time(18, 0), time(23, 59)),
}
"""Spoken part of the day to its departure time window."""

PERIOD_LINKERS: frozenset[str] = frozenset({"de", "a", "na", "pela", "da"})
"""Word before a period that belongs to it (``"de manhã"``, ``"à noite"``)."""

NUMERIC_DATE: re.Pattern[str] = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
"""A ``dd/mm`` or ``dd/mm/yyyy`` date in the raw text."""

MONTH_LOOKAHEAD: int = 12
"""Months searched ahead for a ``"dia N"`` that does not exist this month."""

TRIP_SEPARATOR: str = "|"
"""Token replacing a date or period, so it breaks a city n-gram."""

TRIP_FUZZY_CUTOFF: float = 0.84
"""Lowest :class:`difflib.SequenceMatcher` ratio accepted for a typo.

``"teresinha"`` -> ``"teresina"`` is ``0.941``, ``"pico"`` -> ``"picos"``
``0.889``; ``"sobrau"`` -> ``"sobral"`` is ``0.833``, below it, and resolved
by :data:`VOCALIZED_L` instead.
"""

TRIP_FUZZY_CANDIDATE_CUTOFF: float = 0.75
"""Edit-distance similarity that makes a name a candidate for the ratio.

Wider than :data:`TRIP_FUZZY_CUTOFF` on purpose: edit distance only gathers
candidates (:class:`~tempest_fastapi_sdk.text.FuzzyIndex`, with its trigram
filter), and the ratio decides. ``"pico"`` -> ``"picos"`` is ``0.8`` here.
"""

TRIP_FUZZY_MIN_CHARS: int = 4
"""A piece shorter than this never matches by similarity (``"pra lá"``)."""

TRIP_MAX_FUZZY_LOOKUPS: int = 8
"""Similarity lookups one phrase may trigger.

Each preposition triggers one over the whole index; a long phrase full of
``"de"`` would otherwise scan it dozens of times.
"""

__all__: list[str] = [
    "DAY_AFTER_TOMORROW",
    "DAY_WORD",
    "DESTINATION_MARKERS",
    "DESTINATION_VERBS",
    "DESTINATION_VERB_LINKERS",
    "FALLBACK_STOPWORDS",
    "MONTHS",
    "MONTH_LOOKAHEAD",
    "NEXT_WEEK_PREFIXES",
    "NEXT_WEEK_SUFFIX",
    "NUMERIC_DATE",
    "ORIGIN_MARKERS",
    "ORIGIN_VERBS",
    "ORIGIN_VERB_LINKERS",
    "PERIODS",
    "PERIOD_LINKERS",
    "ROMAN_ORDINALS",
    "STATE_QUALIFIERS",
    "TODAY_WORDS",
    "TOMORROW_WORD",
    "TRIP_FUZZY_CANDIDATE_CUTOFF",
    "TRIP_FUZZY_CUTOFF",
    "TRIP_FUZZY_MIN_CHARS",
    "TRIP_MAX_FUZZY_LOOKUPS",
    "TRIP_SEPARATOR",
    "UNRECOGNIZED_CITY_IGNORED_WORDS",
    "VOCALIZED_L",
    "WEEKDAYS",
]
