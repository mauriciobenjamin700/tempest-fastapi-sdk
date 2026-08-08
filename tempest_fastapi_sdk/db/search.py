"""Text search conditions, portable across PostgreSQL and SQLite.

Two layers, because they answer different questions:

* :func:`like_search_condition` — tokenized, escaped ``ILIKE``. Behaves
  **identically** on both backends, needs no extension, no index and no
  migration. This is what "find the row whose name contains what the
  user typed" should use.
* :func:`full_text_condition` / :func:`full_text_rank` — PostgreSQL
  ``to_tsvector`` / ``websearch_to_tsquery`` / ``ts_rank``: stemming
  (``comprou`` matches ``comprar``), stop-word removal, per-field
  weighting and a relevance score. On any other backend these fall back
  to the ``ILIKE`` layer, which is the honest degradation for a test
  database that has no such engine — the query still returns the right
  rows, it just cannot rank them.

Column references are accepted either as a plain name or as the mapped
attribute itself (``UserModel.name``), so a call site that wants the
type-checker to catch a renamed column can have it:

```python
condition = like_search_condition(UserModel, "joao", [UserModel.name])
```
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import reduce
from typing import Any, Final, cast

from sqlalchemy import and_, func, literal_column, or_
from sqlalchemy.inspection import inspect
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.db.expressions import escape_like
from tempest_fastapi_sdk.exceptions.validation import ValidationException

POSTGRESQL_DIALECT: Final[str] = "postgresql"
"""SQLAlchemy's dialect name for PostgreSQL.

Named so the full-text branch is greppable and cannot drift from a typo
in an inline literal.
"""

ColumnRef = str | InstrumentedAttribute[Any]
"""A searchable column: its name, or the mapped attribute itself."""


class TextSearchLanguage(BaseStrEnum):
    """PostgreSQL text-search configuration used for stemming.

    The configuration decides which stemmer and stop-word list apply, so
    it is what makes ``comprou`` match ``comprar`` in Portuguese and
    ``buying`` match ``buy`` in English. :attr:`SIMPLE` disables both —
    it lowercases and splits on non-word characters, nothing more, which
    is the right choice for identifiers, SKUs and tags.

    Members cover the Snowball configurations shipped by every supported
    PostgreSQL release. A database with a custom configuration created
    through ``CREATE TEXT SEARCH CONFIGURATION`` is out of scope; the
    value reaches PostgreSQL as a ``regconfig``, which rejects an unknown
    name with a clear error rather than silently matching nothing.

    Ignored entirely on backends without full-text support, where the
    query degrades to the ``ILIKE`` layer.
    """

    SIMPLE = "simple"
    DANISH = "danish"
    DUTCH = "dutch"
    ENGLISH = "english"
    FINNISH = "finnish"
    FRENCH = "french"
    GERMAN = "german"
    HUNGARIAN = "hungarian"
    ITALIAN = "italian"
    NORWEGIAN = "norwegian"
    PORTUGUESE = "portuguese"
    ROMANIAN = "romanian"
    RUSSIAN = "russian"
    SPANISH = "spanish"
    SWEDISH = "swedish"
    TURKISH = "turkish"


class TokenMatch(BaseStrEnum):
    """How the words of a search term combine.

    Attributes:
        ALL: Every token must appear somewhere in the searched columns.
            Typing more words narrows the result — what a user expects
            from a search box, and the default.
        ANY: One token is enough. Typing more words widens the result;
            useful for "related to any of these" lookups.
    """

    ALL = "all"
    ANY = "any"


class TextSearchWeight(BaseStrEnum):
    """Relative importance of a column in the relevance score.

    PostgreSQL ranks ``A`` highest and ``D`` lowest, so a term found in a
    title outranks the same term buried in a body. Only meaningful for
    :func:`full_text_condition` and :func:`full_text_rank`; the
    ``ILIKE`` layer has no score to weight.
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


def _regconfig(language: TextSearchLanguage) -> ColumnElement[Any]:
    """Render the text-search configuration as a SQL literal.

    ``to_tsvector`` / ``websearch_to_tsquery`` take a ``regconfig`` as
    their first argument. Passing it as a bind parameter looks safer but
    breaks on the production driver: asyncpg prepares statements
    server-side, and PostgreSQL cannot infer ``regconfig`` for an
    untyped placeholder — the query fails with "could not determine data
    type of parameter". The configuration is therefore inlined, which is
    also what SQLAlchemy's own ``postgresql_regconfig`` option does.

    Inlining is safe here because the value can only come from
    :class:`TextSearchLanguage`, a closed enum of lowercase ASCII names;
    the assertion below pins that so a future member with a quote in it
    fails loudly instead of becoming an injection point.

    Args:
        language (TextSearchLanguage): The configuration to render.

    Returns:
        ColumnElement[Any]: The quoted configuration name as SQL.

    Raises:
        ValueError: When the enum member's value is not a bare lowercase
            identifier.
    """
    value = language.value
    if not value.isalpha() or not value.islower():
        raise ValueError(
            f"TextSearchLanguage value {value!r} is not a bare lowercase name; "
            "it cannot be inlined into SQL safely.",
        )
    return literal_column(f"'{value}'")


def resolve_search_column(
    model: type[Any],
    field: ColumnRef,
) -> InstrumentedAttribute[Any]:
    """Resolve a column reference to a mapped attribute on ``model``.

    Search field names routinely arrive from a query parameter, so a
    bare ``getattr`` would turn a wrong name into an ``AttributeError``
    and an HTTP 500 on a request that is merely invalid. Resolution goes
    through the mapper's column set, which also rejects class attributes
    that exist but are not columns (``metadata``, ``registry``).

    Args:
        model (type[Any]): The model the column belongs to.
        field (ColumnRef): The column name, or the mapped attribute.

    Returns:
        InstrumentedAttribute[Any]: The resolved column.

    Raises:
        ValidationException: When the name is not a mapped column of
            ``model``, or the attribute belongs to a different model.
    """
    mapper = inspect(model)
    name = field if isinstance(field, str) else field.key
    if name not in mapper.columns:
        raise ValidationException(
            message=f"{model.__name__!r} has no column {name!r}",
            details={"field": name, "allowed": sorted(mapper.columns.keys())},
        )
    return cast("InstrumentedAttribute[Any]", getattr(model, name))


def tokenize(term: str) -> list[str]:
    """Split a search term into non-empty whitespace-separated tokens.

    Args:
        term (str): The raw term as typed by the user.

    Returns:
        list[str]: The tokens; empty when the term is blank.
    """
    return term.split()


def like_search_condition(
    model: type[Any],
    term: str,
    fields: Sequence[ColumnRef],
    *,
    token_match: TokenMatch = TokenMatch.ALL,
) -> ColumnElement[bool] | None:
    """Build a portable substring-search condition.

    Each token is matched case-insensitively against every listed column
    (``OR`` across columns), and the tokens combine per ``token_match``.
    Wildcards in the user's input are escaped, so a term containing
    ``%`` or ``_`` searches for those characters literally instead of
    silently matching everything.

    Args:
        model (type[Any]): The model being searched.
        term (str): The raw search term.
        fields (Sequence[ColumnRef]): Columns to search, by name or as
            mapped attributes. Must not be empty.
        token_match (TokenMatch): Whether every token must match or any
            single one is enough.

    Returns:
        ColumnElement[bool] | None: The condition, or ``None`` when the
        term holds no tokens — the caller then applies no filter, so a
        blank search box returns the unfiltered list rather than nothing.

    Raises:
        ValidationException: When ``fields`` is empty or names a column
            the model does not have.
    """
    columns = _resolve_columns(model, fields)
    tokens = tokenize(term)
    if not tokens:
        return None

    per_token: list[ColumnElement[bool]] = [
        or_(
            *[
                column.ilike(f"%{escape_like(token)}%", escape="\\")
                for column in columns
            ],
        )
        for token in tokens
    ]
    combined = and_(*per_token) if token_match is TokenMatch.ALL else or_(*per_token)
    return combined


def full_text_condition(
    model: type[Any],
    term: str,
    fields: Sequence[ColumnRef],
    *,
    language: TextSearchLanguage = TextSearchLanguage.PORTUGUESE,
    weights: Mapping[str, TextSearchWeight] | None = None,
    dialect: str = POSTGRESQL_DIALECT,
    token_match: TokenMatch = TokenMatch.ALL,
) -> ColumnElement[bool] | None:
    """Build a full-text match condition, or the portable fallback.

    On PostgreSQL this compiles to ``to_tsvector(...) @@
    websearch_to_tsquery(language, term)``. ``websearch_to_tsquery`` is
    what makes the term safe to take straight from a user: it accepts
    the search-engine syntax people already type — ``"exact phrase"``,
    ``-excluded``, ``or`` — and never raises a syntax error on stray
    punctuation, unlike ``to_tsquery``.

    On any other dialect it delegates to :func:`like_search_condition`,
    so the same call site works against the SQLite test database.

    Args:
        model (type[Any]): The model being searched.
        term (str): The raw search term.
        fields (Sequence[ColumnRef]): Columns to search.
        language (TextSearchLanguage): Stemming configuration.
        weights (Mapping[str, TextSearchWeight] | None): Per-column
            weight, keyed by column name. Columns left out default to
            ``D``. Only affects :func:`full_text_rank`; supplying it
            changes how the vector is built, so the same mapping must be
            passed to both calls for the score to line up.
        dialect (str): The active SQLAlchemy dialect name. Compared
            against :data:`POSTGRESQL_DIALECT`.
        token_match (TokenMatch): Used only by the fallback path;
            PostgreSQL's ``websearch_to_tsquery`` already treats
            space-separated words as ``AND``.

    Returns:
        ColumnElement[bool] | None: The condition, or ``None`` for a
        blank term.

    Raises:
        ValidationException: When ``fields`` is empty or names a column
            the model does not have.
    """
    if dialect != POSTGRESQL_DIALECT:
        return like_search_condition(model, term, fields, token_match=token_match)
    if not tokenize(term):
        return None

    vector = _tsvector(model, fields, language, weights)
    query = func.websearch_to_tsquery(_regconfig(language), term)
    return cast("ColumnElement[bool]", vector.bool_op("@@")(query))


def full_text_rank(
    model: type[Any],
    term: str,
    fields: Sequence[ColumnRef],
    *,
    language: TextSearchLanguage = TextSearchLanguage.PORTUGUESE,
    weights: Mapping[str, TextSearchWeight] | None = None,
    dialect: str = POSTGRESQL_DIALECT,
) -> ColumnElement[Any] | None:
    """Build the relevance-score expression to order results by.

    Args:
        model (type[Any]): The model being searched.
        term (str): The raw search term.
        fields (Sequence[ColumnRef]): Columns to search.
        language (TextSearchLanguage): Stemming configuration.
        weights (Mapping[str, TextSearchWeight] | None): Per-column
            weight; must match what was passed to
            :func:`full_text_condition`.
        dialect (str): The active SQLAlchemy dialect name.

    Returns:
        ColumnElement[Any] | None: The ``ts_rank`` expression, or
        ``None`` when the backend has no ranking to offer or the term is
        blank. A ``None`` return is the signal to leave the caller's own
        ordering in place rather than to order by nothing.

    Raises:
        ValidationException: When ``fields`` is empty or names a column
            the model does not have.
    """
    if dialect != POSTGRESQL_DIALECT or not tokenize(term):
        return None
    vector = _tsvector(model, fields, language, weights)
    query = func.websearch_to_tsquery(_regconfig(language), term)
    return cast("ColumnElement[Any]", func.ts_rank(vector, query))


def supports_full_text(dialect: str) -> bool:
    """Report whether ``dialect`` has a real full-text engine here.

    Lets a caller tell a ranked result from an unranked one — a UI that
    shows a relevance bar needs to know it will be empty against SQLite.

    Args:
        dialect (str): The SQLAlchemy dialect name.

    Returns:
        bool: ``True`` only for PostgreSQL.
    """
    return dialect == POSTGRESQL_DIALECT


def _resolve_columns(
    model: type[Any],
    fields: Sequence[ColumnRef],
) -> list[InstrumentedAttribute[Any]]:
    """Resolve every reference in ``fields``, rejecting an empty list.

    Args:
        model (type[Any]): The model being searched.
        fields (Sequence[ColumnRef]): The references to resolve.

    Returns:
        list[InstrumentedAttribute[Any]]: The resolved columns.

    Raises:
        ValidationException: When ``fields`` is empty — searching zero
            columns would silently match every row, which is the
            opposite of what the caller asked for.
    """
    if not fields:
        raise ValidationException(
            message="A text search needs at least one column to search",
            details={"model": model.__name__},
        )
    return [resolve_search_column(model, field) for field in fields]


def _tsvector(
    model: type[Any],
    fields: Sequence[ColumnRef],
    language: TextSearchLanguage,
    weights: Mapping[str, TextSearchWeight] | None,
) -> ColumnElement[Any]:
    """Build the ``tsvector`` document from the searched columns.

    Every column is wrapped in ``coalesce(col, '')`` so a ``NULL`` in one
    field does not annihilate the whole document — in PostgreSQL string
    concatenation, one ``NULL`` operand yields ``NULL``, which would make
    the row unmatchable through no fault of its content.

    Without weights the columns are concatenated into a single
    ``to_tsvector`` call. With weights each column becomes its own
    weighted vector and they are concatenated with ``||``, which is the
    only way PostgreSQL can attribute a weight per field.

    Args:
        model (type[Any]): The model being searched.
        fields (Sequence[ColumnRef]): Columns forming the document.
        language (TextSearchLanguage): Stemming configuration.
        weights (Mapping[str, TextSearchWeight] | None): Per-column
            weight keyed by column name; absent columns get ``D``.

    Returns:
        ColumnElement[Any]: The ``tsvector`` expression.
    """
    columns = _resolve_columns(model, fields)
    if not weights:
        document = func.concat_ws(
            " ",
            *[func.coalesce(column, "") for column in columns],
        )
        return cast(
            "ColumnElement[Any]", func.to_tsvector(_regconfig(language), document)
        )

    vectors: list[ColumnElement[Any]] = [
        func.setweight(
            func.to_tsvector(_regconfig(language), func.coalesce(column, "")),
            weights.get(column.key, TextSearchWeight.D).value,
        )
        for column in columns
    ]
    return reduce(lambda left, right: left.op("||")(right), vectors)


__all__: list[str] = [
    "POSTGRESQL_DIALECT",
    "ColumnRef",
    "TextSearchLanguage",
    "TextSearchWeight",
    "TokenMatch",
    "full_text_condition",
    "full_text_rank",
    "like_search_condition",
    "resolve_search_column",
    "supports_full_text",
    "tokenize",
]
