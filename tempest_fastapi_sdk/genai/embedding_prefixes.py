"""Task-prefix presets for asymmetric embedding models.

Some embedding models were trained with a role marker on each side of the
search: one string in front of the question, another in front of the
corpus passage. Without them the vectors still come out, but retrieval is
measurably worse. Both :class:`~tempest_fastapi_sdk.genai.Embedder` and
:class:`~tempest_fastapi_sdk.genai.OllamaEmbedder` take the two strings as
``query_prefix`` / ``passage_prefix``; the presets here splat straight into
either constructor.

The preset is chosen by the caller, never inferred from the model name at
runtime: the same weights ship under many tags (``nomic-embed-text``,
``nomic-embed-text:v1.5``, a local fine-tune), and a wrong guess would
silently change every stored vector.

Example:

    >>> emb = OllamaEmbedder("nomic-embed-text", **NOMIC_EMBED_TEXT_PREFIXES)
"""

from __future__ import annotations

from typing import Final, TypedDict


class EmbeddingPrefixes(TypedDict):
    """The two role markers of an asymmetric embedding model.

    Splat into an embedder constructor:
    ``OllamaEmbedder(model, **prefixes)``.

    Attributes:
        query_prefix (str): Prepended to every search query by
            ``embed_query``.
        passage_prefix (str): Prepended to every corpus passage by
            ``embed_passages``.
    """

    query_prefix: str
    passage_prefix: str


NOMIC_EMBED_TEXT_PREFIXES: Final[EmbeddingPrefixes] = {
    "query_prefix": "search_query: ",
    "passage_prefix": "search_document: ",
}
"""Prefixes of ``nomic-embed-text`` (Ollama tag and the ``nomic-ai`` weights).

Strings published on the model card for the retrieval task.
"""

MULTILINGUAL_E5_PREFIXES: Final[EmbeddingPrefixes] = {
    "query_prefix": "query: ",
    "passage_prefix": "passage: ",
}
"""Prefixes of the ``intfloat/multilingual-e5-*`` and ``intfloat/e5-*`` family."""


__all__: list[str] = [
    "MULTILINGUAL_E5_PREFIXES",
    "NOMIC_EMBED_TEXT_PREFIXES",
    "EmbeddingPrefixes",
]
