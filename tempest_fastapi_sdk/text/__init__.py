"""Text matching without a model: normalization, edit distance, fuzzy lookup.

For "what did the user mean?" over a known list of names — cities, products,
categories — lexical matching answers faster and more predictably than an
embedding model: no weights, no GPU, a typo costs one edit. Semantic search
over free-form passages lives in :mod:`tempest_fastapi_sdk.genai`
(``Embedder``, ``Retriever``, ``HybridRetriever``).

* :func:`normalize_text` / :func:`strip_accents` / :func:`tokenize` — one
  canonical form (no accents, case folded, punctuation as spaces), tokens
  with their offsets in the original string.
* :func:`levenshtein` / :func:`similarity` / :func:`trigrams` — edit
  distance with adjacent swaps, the ``0..1`` ratio, the trigram set.
* :class:`FuzzyIndex` — names to values, exact and typo-tolerant search
  through a trigram index.

No dependency beyond the standard library.
"""

from tempest_fastapi_sdk.text.fuzzy import FuzzyIndex as FuzzyIndex
from tempest_fastapi_sdk.text.fuzzy import FuzzyMatch as FuzzyMatch
from tempest_fastapi_sdk.text.fuzzy import max_edits as max_edits
from tempest_fastapi_sdk.text.normalize import Token as Token
from tempest_fastapi_sdk.text.normalize import normalize_text as normalize_text
from tempest_fastapi_sdk.text.normalize import strip_accents as strip_accents
from tempest_fastapi_sdk.text.normalize import tokenize as tokenize
from tempest_fastapi_sdk.text.similarity import levenshtein as levenshtein
from tempest_fastapi_sdk.text.similarity import similarity as similarity
from tempest_fastapi_sdk.text.similarity import trigrams as trigrams

__all__: list[str] = [
    "FuzzyIndex",
    "FuzzyMatch",
    "Token",
    "levenshtein",
    "max_edits",
    "normalize_text",
    "similarity",
    "strip_accents",
    "tokenize",
    "trigrams",
]
