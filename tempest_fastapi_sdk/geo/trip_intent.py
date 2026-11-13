"""Read a trip request in Brazilian Portuguese: from where, to where, when.

:func:`parse_trip_intent` takes what a passenger typed or said (``"quero ir
de Teresina pra Parnaíba sexta de manhã"``) and returns the origin, the
destination, the departure date and the part of the day. No model and no
network: cities come from the IBGE municipalities
(:func:`~tempest_fastapi_sdk.geo.br_municipalities`), dates and periods from
the fixed vocabulary in :mod:`~tempest_fastapi_sdk.geo.trip_vocabulary`.

What it handles, each case fixed by a test:

* **Roles by preposition**: ``"de X"``, ``"pra Y"``, ``"saindo de X"``,
  ``"indo para Y"``, ``"X pra Y"``; without any, the cities count in order.
* **Speech-to-text errors**: typos by edit distance
  (:func:`~tempest_fastapi_sdk.text.similarity`, ``"teresinha"``), the
  spoken ``l`` written as ``u`` (``"sobrau"``, ``"natau"``) by an exact
  phonetic key, and Roman numerals said as words (``"pedro segundo"``).
* **Nicknames and old spellings** of the IBGE data (``"floripa"``,
  ``"sampa"``, ``"bh"``).
* **Homonyms**: ``"Batalha no Piauí"`` keeps the one in the state; without
  a state both come back, for the app to ask.
* **Leaving now**: ``"agora"``, ``"já"``, ``"o quanto antes"`` and the
  rest of :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.NOW_PHRASES` set
  the date to today and :attr:`TripIntent.departure_now`; an explicit date
  in the phrase wins, and the flag stays set only when that date is today.
* **A city the user said but nothing matched** comes back in
  ``unrecognized`` with what was said, instead of disappearing.
* **Restriction**: pass ``municipalities=`` (the cities a service actually
  serves) and only those are recognized; a real city outside it is
  reported as unrecognized.

Ported from the BusCar transport backend, where it ran in production on
typed and transcribed phrases.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from difflib import SequenceMatcher
from functools import lru_cache

from tempest_fastapi_sdk.geo.enums import TripRoleEnum
from tempest_fastapi_sdk.geo.municipalities import (
    BR_CITY_NICKNAMES,
    BRASILIA_IBGE_CODE,
    Municipality,
    br_municipalities,
)
from tempest_fastapi_sdk.geo.trip_vocabulary import (
    DAY_AFTER_TOMORROW,
    DAY_WORD,
    DESTINATION_MARKERS,
    DESTINATION_VERB_LINKERS,
    DESTINATION_VERBS,
    FALLBACK_STOPWORDS,
    MONTH_LOOKAHEAD,
    MONTHS,
    NEXT_WEEK_PREFIXES,
    NEXT_WEEK_SUFFIX,
    NOW_PHRASES,
    NUMERIC_DATE,
    ORIGIN_MARKERS,
    ORIGIN_VERB_LINKERS,
    ORIGIN_VERBS,
    PERIOD_LINKERS,
    PERIODS,
    ROMAN_ORDINALS,
    STATE_QUALIFIERS,
    TODAY_WORDS,
    TOMORROW_WORD,
    TRIP_FUZZY_CANDIDATE_CUTOFF,
    TRIP_FUZZY_CUTOFF,
    TRIP_FUZZY_MIN_CHARS,
    TRIP_MAX_FUZZY_LOOKUPS,
    TRIP_SEPARATOR,
    UNRECOGNIZED_CITY_IGNORED_WORDS,
    VOCALIZED_L,
    WEEKDAYS,
)
from tempest_fastapi_sdk.text import FuzzyIndex, normalize_text, tokenize
from tempest_fastapi_sdk.utils.locations import UFEnum, list_states


def vocalized_key(text: str) -> str:
    """Return the phonetic key of the spoken syllable-final ``l``.

    Replaces with ``u`` every ``l`` before a consonant or at a word end
    (:data:`~tempest_fastapi_sdk.geo.trip_vocabulary.VOCALIZED_L`), as
    Brazilian Portuguese is spoken and transcribed: ``"sobral"`` and
    ``"sobrau"`` get the same key. Applied to both the index and the phrase.

    Args:
        text (str): A normalized text
            (:func:`~tempest_fastapi_sdk.text.normalize_text`).

    Returns:
        str: The text with the syllable-final ``l`` replaced by ``u``.
    """
    return VOCALIZED_L.sub("u", text)


def spoken_names(municipality: Municipality) -> list[str]:
    """Return every normalized name a municipality is called by.

    The official name, the IBGE aliases (old spellings) and, for a name
    ending in a Roman numeral, the way it is said (``"pedro ii"`` and
    ``"pedro segundo"``).

    Args:
        municipality (Municipality): The municipality.

    Returns:
        list[str]: The distinct normalized names, official first.
    """
    names: list[str] = []
    for raw in (municipality.name, *municipality.aliases):
        key = normalize_text(raw)
        if key and key not in names:
            names.append(key)
        words = key.split()
        if len(words) > 1 and words[-1] in ROMAN_ORDINALS:
            spoken = " ".join([*words[:-1], ROMAN_ORDINALS[words[-1]]])
            if spoken not in names:
                names.append(spoken)
    return names


@dataclass
class TripCityIndex:
    """The municipalities a trip phrase may name, indexed for the parser.

    Build it with :func:`build_trip_city_index` and reuse it across phrases;
    the country-wide index is built once per process.

    Attributes:
        by_name (dict[str, tuple[Municipality, ...]]): Normalized name to
            the municipalities with it (more than one is a homonym).
        by_vocalized (dict[str, tuple[str, ...]]): Phonetic key
            (:func:`vocalized_key`) to the names that share it; more than
            one is a collision, which becomes an ambiguity.
        fuzzy (dict[int, FuzzyIndex[str]]): The names by word count,
            searchable with typos; a piece only competes with names of its
            own length, so a typo lookup scans one bucket, not the country.
        max_words (int): Words in the longest name.
        restricted (bool): Whether the index holds a chosen subset rather
            than the whole country. A subset accepts a lowercase one-word
            city with no preposition (``"picos sobral"``); the whole
            country does not, because ``"estou feliz"`` would be the town of
            Feliz (RS).
        fuzzy_cache (dict[tuple[str, int], tuple[float, tuple[str, ...]]]):
            Similarity results already computed, per phrase and word count.
    """

    by_name: dict[str, tuple[Municipality, ...]]
    by_vocalized: dict[str, tuple[str, ...]]
    fuzzy: dict[int, FuzzyIndex[str]]
    max_words: int
    restricted: bool
    fuzzy_cache: dict[tuple[str, int], tuple[float, tuple[str, ...]]] = field(
        default_factory=dict, repr=False
    )


def build_trip_city_index(
    municipalities: Iterable[Municipality] | None = None,
) -> TripCityIndex:
    """Index municipalities for :func:`parse_trip_intent`.

    Every name of :func:`spoken_names`, the nicknames of
    :data:`~tempest_fastapi_sdk.geo.BR_CITY_NICKNAMES` whose city is in the
    set, and the Federal District's administrative regions when Brasília is.

    Args:
        municipalities (Iterable[Municipality] | None): The cities to
            recognize, such as the ones a service has routes for. ``None``
            is the whole country, built once and cached.

    Returns:
        TripCityIndex: The index.
    """
    if municipalities is None:
        return _country_index()
    return _build_index(tuple(municipalities), restricted=True)


@lru_cache(maxsize=1)
def _country_index() -> TripCityIndex:
    """Build the index of every Brazilian municipality, once.

    Returns:
        TripCityIndex: The country-wide index.
    """
    return _build_index(br_municipalities(), restricted=False)


def _add_name(
    by_name: dict[str, list[Municipality]], name: str, municipality: Municipality
) -> None:
    """Store a municipality under a normalized name, once.

    Args:
        by_name (dict[str, list[Municipality]]): The name table.
        name (str): The normalized name.
        municipality (Municipality): The municipality.
    """
    bucket = by_name.setdefault(name, [])
    if all(item.ibge_code != municipality.ibge_code for item in bucket):
        bucket.append(municipality)


def _build_index(
    municipalities: tuple[Municipality, ...], *, restricted: bool
) -> TripCityIndex:
    """Build the index over a set of municipalities.

    Args:
        municipalities (tuple[Municipality, ...]): The cities.
        restricted (bool): Whether this is a subset of the country.

    Returns:
        TripCityIndex: The index.
    """
    by_name: dict[str, list[Municipality]] = {}
    by_code = {municipality.ibge_code: municipality for municipality in municipalities}
    for municipality in municipalities:
        for name in spoken_names(municipality):
            _add_name(by_name, name, municipality)
    for nickname, code in BR_CITY_NICKNAMES.items():
        if code in by_code:
            _add_name(by_name, normalize_text(nickname), by_code[code])
    brasilia = by_code.get(BRASILIA_IBGE_CODE)
    if brasilia is not None:
        for state in list_states():
            if state.uf is UFEnum.DF:
                for region in state.cities:
                    key = normalize_text(region)
                    if key and key not in by_name:
                        _add_name(by_name, key, brasilia)
    by_vocalized: dict[str, list[str]] = {}
    fuzzy: dict[int, FuzzyIndex[str]] = {}
    for name in by_name:
        by_vocalized.setdefault(vocalized_key(name), []).append(name)
        fuzzy.setdefault(len(name.split()), FuzzyIndex()).add(name, name)
    return TripCityIndex(
        by_name={name: tuple(items) for name, items in by_name.items()},
        by_vocalized={key: tuple(names) for key, names in by_vocalized.items()},
        fuzzy=fuzzy,
        max_words=max((len(name.split()) for name in by_name), default=1),
        restricted=restricted,
    )


@dataclass(frozen=True)
class CityMention:
    """A city recognized in the phrase.

    Attributes:
        spoken (str): The normalized name that matched; on a phonetic
            collision or a similarity tie, the normalized words said.
        candidates (tuple[Municipality, ...]): The municipalities it may
            be; more than one for a homonym the phrase did not settle, or
            for names the spoken words cannot tell apart.
    """

    spoken: str
    candidates: tuple[Municipality, ...]

    @property
    def resolved(self) -> Municipality | None:
        """Return the municipality when a single candidate is left.

        Returns:
            Municipality | None: The only candidate, or ``None``.
        """
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def name(self) -> str | None:
        """Return the official name every candidate shares.

        A homonym (``"Batalha"`` in PI and AL) has one name and a search may
        filter by it; a phonetic collision has different names, and picking
        one would be a guess.

        Returns:
            str | None: The shared name, or ``None`` when they differ.
        """
        names = {candidate.name for candidate in self.candidates}
        return self.candidates[0].name if len(names) == 1 else None


@dataclass
class TripIntent:
    """What a trip phrase asks for.

    Attributes:
        origin (CityMention | None): The origin city.
        destination (CityMention | None): The destination city.
        departure_date (date | None): The departure date.
        period (str | None): The part of the day said (``"manha"``,
            ``"noite"``...), a key of
            :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.PERIODS`.
        period_start (time | None): Start of that part of the day.
        period_end (time | None): End of it.
        unresolved_date (str | None): A date said that does not exist
            (``"30/02"``, ``"dia 31 de abril"``), for the app to ask instead
            of searching with no date.
        unrecognized (dict[TripRoleEnum, str]): Role to the words said after
            its preposition that matched no city (``{"destination":
            "natal"}`` when Natal is outside ``municipalities``). Only a
            role left without a city is reported.
        departure_now (bool): Whether the phrase asked to leave now
            (``"agora"``, ``"já"``, ``"o quanto antes"``... the
            :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.NOW_PHRASES`)
            and ``departure_date`` is ``today``. The parser receives a date,
            not a clock, so it sets no time: the caller keeps only the trips
            that leave from its own current time on. An explicit date in the
            phrase wins: one other than today (``"agora ... amanhã"``) or one
            that does not exist makes this ``False``, while one that falls
            on today (``"hoje agora"``) keeps it ``True``.
    """

    origin: CityMention | None = None
    destination: CityMention | None = None
    departure_date: date | None = None
    period: str | None = None
    period_start: time | None = None
    period_end: time | None = None
    unresolved_date: str | None = None
    unrecognized: dict[TripRoleEnum, str] = field(default_factory=dict)
    departure_now: bool = False


@dataclass
class _Parse:
    """Working state of one parse.

    Attributes:
        intent (TripIntent): The result being filled.
        tokens (list[str]): Normalized words; consumed ones become
            :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.TRIP_SEPARATOR`.
        originals (list[str]): Each word as written, for case.
        index (TripCityIndex): The cities.
        consumed (set[int]): Token positions read as date or period.
        fuzzy_budget (int): Similarity lookups still allowed.
        asked_now (bool): Whether a phrase of
            :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.NOW_PHRASES` was
            said.
    """

    intent: TripIntent
    tokens: list[str]
    originals: list[str]
    index: TripCityIndex
    consumed: set[int] = field(default_factory=set)
    fuzzy_budget: int = TRIP_MAX_FUZZY_LOOKUPS
    asked_now: bool = False


def next_weekday(today: date, weekday: int, *, strictly_after: bool) -> date:
    """Return the next date falling on ``weekday``.

    Args:
        today (date): The reference date.
        weekday (int): ``0`` (Monday) to ``6`` (Sunday).
        strictly_after (bool): Whether today does not count even on that day.

    Returns:
        date: The date.
    """
    delta = (weekday - today.weekday()) % 7
    if delta == 0 and strictly_after:
        delta = 7
    return today + timedelta(days=delta)


def day_of_month(today: date, day: int, month: int | None) -> date | None:
    """Resolve ``"dia N"`` (and ``"dia N de <mês>"``) to the next valid date.

    Without a month, the first month from the current one where the day
    exists and has not passed: ``"dia 31"`` said on 14 April is 31 May, not a
    search with no date. With a month, this year unless it passed, else next
    year; a day that does not exist in that month is not corrected.

    Args:
        today (date): The reference date.
        day (int): The day of the month.
        month (int | None): The month, when said.

    Returns:
        date | None: The date, or ``None`` when it does not exist.
    """
    if month is not None:
        try:
            candidate = date(today.year, month, day)
            if candidate < today:
                candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
        return candidate
    for offset in range(MONTH_LOOKAHEAD + 1):
        absolute = today.month - 1 + offset
        year, month_index = today.year + absolute // 12, absolute % 12 + 1
        try:
            candidate = date(year, month_index, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def _numeric_date(
    text: str, today: date
) -> tuple[date | None, tuple[int, int] | None, str | None]:
    """Find a ``dd/mm[/yyyy]`` date in the raw text.

    Args:
        text (str): The text as written.
        today (date): The reference date, for an omitted year.

    Returns:
        tuple[date | None, tuple[int, int] | None, str | None]: The date
        (``None`` when absent or invalid), the span it occupies, and the
        text said when the date does not exist (``"30/02"``).
    """
    match = NUMERIC_DATE.search(text)
    if not match:
        return None, None, None
    day, month, year = match.group(1), match.group(2), match.group(3)
    found: date | None
    try:
        if year:
            full_year = int(year) + (2000 if len(year) == 2 else 0)
            found = date(full_year, int(month), int(day))
        else:
            found = day_of_month(today, int(day), int(month))
    except ValueError:
        found = None
    return found, match.span(), None if found is not None else match.group(0)


def _now_phrase_at(tokens: list[str], start: int) -> int:
    """Return the length of the "leave now" phrase starting at ``start``.

    Args:
        tokens (list[str]): The normalized phrase tokens.
        start (int): The position to read from.

    Returns:
        int: The token count of the longest
        :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.NOW_PHRASES` entry
        there, or ``0``.
    """
    for phrase in NOW_PHRASES:
        if tuple(tokens[start : start + len(phrase)]) == phrase:
            return len(phrase)
    return 0


def _extract_dates(state: _Parse, today: date) -> None:
    """Read relative dates, weekdays, ``"dia N"`` and "leave now" phrases.

    A "leave now" phrase only marks :attr:`_Parse.asked_now` and consumes its
    tokens; :func:`parse_trip_intent` turns it into today once every explicit
    date had the chance to win.

    Args:
        state (_Parse): The parse; dates and consumed tokens are written here.
        today (date): The reference date.
    """
    tokens, intent = state.tokens, state.intent
    size = len(DAY_AFTER_TOMORROW)
    for i, token in enumerate(tokens):
        if i in state.consumed:
            continue
        now_size = _now_phrase_at(tokens, i)
        if now_size:
            state.asked_now = True
            state.consumed.update(range(i, i + now_size))
        elif tuple(tokens[i : i + size]) == DAY_AFTER_TOMORROW:
            intent.departure_date = today + timedelta(days=2)
            state.consumed.update(range(i, i + size))
        elif token == TOMORROW_WORD:
            intent.departure_date = today + timedelta(days=1)
            state.consumed.add(i)
        elif token in TODAY_WORDS:
            intent.departure_date = today
            state.consumed.add(i)
        elif token in WEEKDAYS:
            span = [i]
            if i + 1 < len(tokens) and tokens[i + 1] == "feira":
                span.append(i + 1)
            after = span[-1] + 1
            strictly = i > 0 and tokens[i - 1] in NEXT_WEEK_PREFIXES
            if strictly:
                span.append(i - 1)
            if tuple(tokens[after : after + len(NEXT_WEEK_SUFFIX)]) == NEXT_WEEK_SUFFIX:
                strictly = True
                span.extend(range(after, after + len(NEXT_WEEK_SUFFIX)))
            intent.departure_date = next_weekday(
                today, WEEKDAYS[token], strictly_after=strictly
            )
            state.consumed.update(span)
        elif token == DAY_WORD and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            span = [i, i + 1]
            month: int | None = None
            if (
                i + 3 < len(tokens)
                and tokens[i + 2] == "de"
                and tokens[i + 3] in MONTHS
            ):
                month = MONTHS[tokens[i + 3]]
                span.extend([i + 2, i + 3])
            resolved = day_of_month(today, int(tokens[i + 1]), month)
            state.consumed.update(span)
            if resolved is not None:
                intent.departure_date = resolved
            else:
                intent.unresolved_date = " ".join(tokens[k] for k in span)


def _extract_period(state: _Parse) -> None:
    """Read the part of the day (``"de manhã"``, ``"à noite"``).

    Args:
        state (_Parse): The parse; the period and consumed tokens are
            written here.
    """
    for i, token in enumerate(state.tokens):
        if i in state.consumed or token not in PERIODS:
            continue
        intent = state.intent
        intent.period = token
        intent.period_start, intent.period_end = PERIODS[token]
        state.consumed.add(i)
        if i > 0 and state.tokens[i - 1] in PERIOD_LINKERS:
            state.consumed.add(i - 1)


@lru_cache(maxsize=1)
def _state_names() -> tuple[tuple[tuple[str, ...], UFEnum], ...]:
    """Return every state name in words, longest first.

    Returns:
        tuple[tuple[tuple[str, ...], UFEnum], ...]: ``(("mato", "grosso",
        "do", "sul"), UFEnum.MS)`` before ``(("mato", "grosso"), ...)``.
    """
    names = [
        (tuple(normalize_text(state.name).split()), state.uf) for state in list_states()
    ]
    return tuple(sorted(names, key=lambda item: -len(item[0])))


def _state_from(tokens: list[str], start: int) -> tuple[UFEnum | None, int]:
    """Read a state right after a city (``"no Piauí"``, ``"do Pará"``, ``"PI"``).

    The acronym counts alone (``"Teresina PI"``) or after a preposition; the
    state name only after a preposition. Without that rule, ``"Belém para
    Teresina"`` would read the preposition ``"para"`` as the state of Pará.

    Args:
        tokens (list[str]): The phrase tokens, with separators.
        start (int): The position right after the city.

    Returns:
        tuple[UFEnum | None, int]: The state (or ``None``) and the position
        after it.
    """
    position = start
    qualified = position < len(tokens) and tokens[position] in STATE_QUALIFIERS
    if qualified:
        position += 1
    if position >= len(tokens):
        return None, start
    token = tokens[position]
    if len(token) == 2 and token.upper() in UFEnum.__members__:
        return UFEnum(token.upper()), position + 1
    if not qualified:
        return None, start
    for words, uf in _state_names():
        if tuple(tokens[position : position + len(words)]) == words:
            return uf, position + len(words)
    return None, start


def _closest(state: _Parse, phrase: str, size: int) -> tuple[float, tuple[str, ...]]:
    """Return the most similar names with ``size`` words, cached.

    Two steps. The typo index (:class:`~tempest_fastapi_sdk.text.FuzzyIndex`)
    finds every name within
    :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.TRIP_FUZZY_CANDIDATE_CUTOFF`
    by edit distance, fast and without losing a candidate; then
    :class:`difflib.SequenceMatcher` decides, at
    :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.TRIP_FUZZY_CUTOFF`. Edit
    distance alone is too strict on a dropped letter (``"pico"`` ->
    ``"picos"`` is ``0.8``; the ratio gives ``0.889``) and cannot tell
    ``"teresinha"`` from Teresina and Terezinha (one edit each; the ratio
    prefers Teresina, ``0.941`` to ``0.889``). That is the rule the
    transport backend ran in production.

    Args:
        state (_Parse): The parse, for the index.
        phrase (str): The normalized piece.
        size (int): Its word count; only names of the same length compete.

    Returns:
        tuple[float, tuple[str, ...]]: The best ratio and every name
        reaching it (a tie keeps all), or ``(0.0, ())`` below the cutoff.
    """
    index = state.index
    key = (phrase, size)
    if key not in index.fuzzy_cache:
        best = 0.0
        names: list[str] = []
        bucket = index.fuzzy.get(size)
        hits = (
            bucket.search(phrase, limit=None, cutoff=TRIP_FUZZY_CANDIDATE_CUTOFF)
            if bucket is not None
            else []
        )
        for hit in hits:
            ratio = SequenceMatcher(None, phrase, hit.key).ratio()
            if ratio < TRIP_FUZZY_CUTOFF:
                continue
            if ratio > best:
                best, names = ratio, [hit.value]
            elif ratio == best:
                names.append(hit.value)
        index.fuzzy_cache[key] = (best, tuple(names))
    return index.fuzzy_cache[key]


def _match_city(
    state: _Parse, start: int, *, fuzzy: bool
) -> tuple[CityMention | None, int]:
    """Match the longest city starting at ``start``.

    Exact first, longest piece first; then, with ``fuzzy``, exact on the
    phonetic key (:func:`vocalized_key`) and, without it, the most similar
    piece above the cutoff. A phonetic key or a similarity tie naming
    different cities returns them all, as an ambiguity. A state right after
    it narrows a homonym.

    Args:
        state (_Parse): The parse.
        start (int): Where the city would start.
        fuzzy (bool): Whether similarity is allowed.

    Returns:
        tuple[CityMention | None, int]: The city (or ``None``) and the
        position after what it consumed.
    """
    tokens, index = state.tokens, state.index
    limit = min(index.max_words, len(tokens) - start)
    pieces = [
        (size, " ".join(tokens[start : start + size]))
        for size in range(limit, 0, -1)
        if TRIP_SEPARATOR not in tokens[start : start + size]
    ]
    found: tuple[int, str, tuple[str, ...]] | None = None
    for size, phrase in pieces:
        if phrase in index.by_name:
            found = (size, phrase, (phrase,))
            break
    if found is None and fuzzy:
        for size, phrase in pieces:
            names = index.by_vocalized.get(vocalized_key(phrase), ())
            if names:
                found = (size, names[0] if len(names) == 1 else phrase, names)
                break
    if found is None and fuzzy:
        best = 0.0
        for size, phrase in pieces:
            if len(phrase) < TRIP_FUZZY_MIN_CHARS:
                continue
            score, names = _closest(state, phrase, size)
            if names and score > best:
                best = score
                found = (size, names[0] if len(names) == 1 else phrase, names)
    if found is None:
        return None, start
    size, spoken, names = found
    end = start + size
    candidates: list[Municipality] = []
    for name in names:
        for municipality in index.by_name[name]:
            if all(item.ibge_code != municipality.ibge_code for item in candidates):
                candidates.append(municipality)
    uf, after_state = _state_from(tokens, end)
    if uf is not None:
        narrowed = [candidate for candidate in candidates if candidate.uf is uf]
        if narrowed:
            candidates, end = narrowed, after_state
    return CityMention(spoken=spoken, candidates=tuple(candidates)), end


def _spoken_after_marker(state: _Parse, start: int) -> str | None:
    """Return the words said after a preposition, when they look like a city.

    Reads from ``start`` until a separator, another preposition or verb, a
    fallback stopword, a state preposition or the longest name length. A
    first word in
    :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.UNRECOGNIZED_CITY_IGNORED_WORDS`
    (``"pra casa"``) or a piece shorter than
    :data:`~tempest_fastapi_sdk.geo.trip_vocabulary.TRIP_FUZZY_MIN_CHARS`
    (``"pra lá"``) is not a city.

    Args:
        state (_Parse): The parse.
        start (int): The position right after the preposition.

    Returns:
        str | None: The normalized words, or ``None``.
    """
    stops = (
        FALLBACK_STOPWORDS
        | ORIGIN_MARKERS
        | DESTINATION_MARKERS
        | ORIGIN_VERBS
        | DESTINATION_VERBS
        | STATE_QUALIFIERS
    )
    words: list[str] = []
    for token in state.tokens[start : start + state.index.max_words]:
        if token == TRIP_SEPARATOR or token in stops:
            break
        words.append(token)
    if not words or words[0] in UNRECOGNIZED_CITY_IGNORED_WORDS:
        return None
    spoken = " ".join(words)
    return spoken if len(spoken) >= TRIP_FUZZY_MIN_CHARS else None


def _match_city_ending_at(
    state: _Parse, end: int, used: set[int]
) -> tuple[CityMention, int] | None:
    """Match the city ending right before ``end``, with no preposition.

    The ``"Teresina pra Parnaíba"`` pattern: the origin stands alone before
    the destination preposition, which bounds it, so similarity is allowed
    (``"Teresinha"``).

    Args:
        state (_Parse): The parse.
        end (int): The position of the destination preposition.
        used (set[int]): Positions already consumed.

    Returns:
        tuple[CityMention, int] | None: The city and where it starts.
    """
    for size in range(min(state.index.max_words, end), 0, -1):
        first = end - size
        span = range(first, end)
        if any(k in used for k in span):
            continue
        if any(state.tokens[k] in FALLBACK_STOPWORDS for k in span):
            continue
        mention, stop = _match_city(state, first, fuzzy=True)
        if mention is not None and stop == end:
            return mention, first
    return None


def _looks_like_place(state: _Parse, start: int, end: int) -> bool:
    """Tell a loose one-word city from the same word in prose.

    Only for the whole-country index (a restricted one accepts any match): a
    name of several words or written with an uppercase first letter is a
    place; ``"estou feliz"`` is not the town of Feliz.

    Args:
        state (_Parse): The parse.
        start (int): Where the name starts.
        end (int): Where it ends.

    Returns:
        bool: Whether the match counts on its own.
    """
    if state.index.restricted or end - start > 1:
        return True
    return state.originals[start][:1].isupper()


def _fill_without_markers(state: _Parse, used: set[int]) -> None:
    """Fill origin and destination with cities said without a preposition.

    ``"Teresina Parnaíba amanhã"`` is Teresina to Parnaíba. A single loose
    city with nothing else (``"Parnaíba amanhã"``) is the destination:
    someone naming one place is saying where to go. When the phrase already
    gave one role, the loose city fills the other.

    On the whole-country index a lowercase one-word city counts only when
    the cities found are all the phrase says (``"picos sobral"``,
    ``"natal"``): with other words around (``"estou feliz"``), it is more
    likely an ordinary word (:func:`_looks_like_place`).

    Args:
        state (_Parse): The parse.
        used (set[int]): Positions consumed by prepositions.
    """
    intent = state.intent
    found: list[tuple[CityMention, int, int]] = []
    i = 0
    while i < len(state.tokens):
        if i in used or state.tokens[i] in FALLBACK_STOPWORDS:
            i += 1
            continue
        mention, end = _match_city(state, i, fuzzy=False)
        if mention is None:
            i += 1
            continue
        found.append((mention, i, end))
        i = end
    covered = {k for _, start, end in found for k in range(start, end)}
    rest = {
        k
        for k, token in enumerate(state.tokens)
        if k not in used and token != TRIP_SEPARATOR and token not in FALLBACK_STOPWORDS
    }
    only_cities = rest <= covered
    mentions = [
        mention
        for mention, start, end in found
        if (only_cities or _looks_like_place(state, start, end))
        and mention not in (intent.origin, intent.destination)
    ]
    if not mentions:
        return
    if intent.origin is None and intent.destination is None:
        if len(mentions) == 1:
            intent.destination = mentions[0]
            return
        intent.origin, intent.destination = mentions[0], mentions[1]
        return
    if intent.origin is None:
        intent.origin = mentions[0]
    elif intent.destination is None:
        intent.destination = mentions[0]


def parse_trip_intent(
    text: str,
    today: date,
    *,
    municipalities: Iterable[Municipality] | TripCityIndex | None = None,
) -> TripIntent:
    """Read origin, destination, date and part of the day from a trip phrase.

    Origin and destination come from prepositions and verbs (``"de X"``,
    ``"pra Y"``, ``"saindo de X"``, ``"indo para Y"``, ``"X pra Y"``);
    without them, the cities said count in order (first origin, second
    destination), by exact name only. Dates: ``hoje``, ``amanhã``, ``depois
    de amanhã``, a weekday (``"próxima sexta"``, ``"sexta que vem"``),
    ``"dia 20"``, ``"dia 20 de outubro"``, ``"20/10"``; periods:
    ``madrugada``, ``manhã``, ``tarde``, ``noite``. A "leave now" phrase
    (``"agora"``, ``"já"``, ``"o quanto antes"``...) with no other date is
    today, with :attr:`TripIntent.departure_now` set.

    Args:
        text (str): The phrase as typed or transcribed.
        today (date): The reference date for ``hoje``, ``amanhã``, weekdays
            and ``"dia N"``.
        municipalities (Iterable[Municipality] | TripCityIndex | None): The
            cities that may be recognized. ``None`` is the whole country. A
            service passes the cities it serves (or a prebuilt
            :func:`build_trip_city_index`, reused across phrases), and a real
            city outside them comes back in ``unrecognized``.

    Returns:
        TripIntent: What was recognized; ``None`` is what the phrase did not
        say or could not be read.
    """
    if isinstance(municipalities, TripCityIndex):
        index = municipalities
    else:
        index = build_trip_city_index(municipalities)
    intent = TripIntent()
    numeric, span, unresolved = _numeric_date(text, today)
    if numeric is not None:
        intent.departure_date = numeric
    intent.unresolved_date = unresolved
    words = tokenize(text)
    state = _Parse(
        intent=intent,
        tokens=[word.text for word in words],
        originals=[word.original for word in words],
        index=index,
    )
    if span is not None:
        state.consumed.update(
            k
            for k, word in enumerate(words)
            if word.start < span[1] and span[0] < word.end
        )
    _extract_dates(state, today)
    if (
        state.asked_now
        and intent.departure_date is None
        and intent.unresolved_date is None
    ):
        intent.departure_date = today
    intent.departure_now = state.asked_now and intent.departure_date == today
    _extract_period(state)
    state.tokens = [
        TRIP_SEPARATOR if k in state.consumed else token
        for k, token in enumerate(state.tokens)
    ]
    tokens = state.tokens
    used: set[int] = set()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        role: TripRoleEnum | None = None
        start = i + 1
        if token in ORIGIN_VERBS:
            role = TripRoleEnum.ORIGIN
            while start < len(tokens) and tokens[start] in ORIGIN_VERB_LINKERS:
                start += 1
        elif token in DESTINATION_VERBS:
            if start < len(tokens) and tokens[start] in DESTINATION_VERB_LINKERS:
                role = TripRoleEnum.DESTINATION
                while start < len(tokens) and tokens[start] in DESTINATION_VERB_LINKERS:
                    start += 1
        elif token in ORIGIN_MARKERS:
            role = TripRoleEnum.ORIGIN
        elif token in DESTINATION_MARKERS:
            role = TripRoleEnum.DESTINATION
            if intent.origin is None and state.fuzzy_budget > 0:
                state.fuzzy_budget -= 1
                before = _match_city_ending_at(state, i, used)
                if before is not None:
                    intent.origin, first = before
                    used.update(range(first, i))
        if role is None:
            i += 1
            continue
        fuzzy = (
            state.fuzzy_budget > 0
            and start < len(tokens)
            and tokens[start] not in FALLBACK_STOPWORDS
        )
        if fuzzy:
            state.fuzzy_budget -= 1
        mention, end = _match_city(state, start, fuzzy=fuzzy)
        if mention is None:
            if fuzzy and role not in intent.unrecognized:
                spoken = _spoken_after_marker(state, start)
                if spoken is not None:
                    intent.unrecognized[role] = spoken
            i += 1
            continue
        if role is TripRoleEnum.ORIGIN and intent.origin is None:
            intent.origin = mention
        elif role is TripRoleEnum.DESTINATION and intent.destination is None:
            intent.destination = mention
        used.update(range(i, end))
        i = end
    if intent.origin is None or intent.destination is None:
        _fill_without_markers(state, used)
    if intent.origin is not None:
        intent.unrecognized.pop(TripRoleEnum.ORIGIN, None)
    if intent.destination is not None:
        intent.unrecognized.pop(TripRoleEnum.DESTINATION, None)
    return intent


__all__: list[str] = [
    "CityMention",
    "TripCityIndex",
    "TripIntent",
    "build_trip_city_index",
    "day_of_month",
    "next_weekday",
    "parse_trip_intent",
    "spoken_names",
    "vocalized_key",
]
