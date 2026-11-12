"""Tests for SemanticNameIndex and HybridNameIndex with a deterministic embedder."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.genai.rag import HybridNameIndex, SemanticNameIndex
from tempest_fastapi_sdk.genai.rag import name_search as module
from tempest_fastapi_sdk.text import normalize_text

CONCEPTS: dict[str, int] = {
    "tenis": 0,
    "calcado": 0,
    "corrida": 1,
    "esportivo": 1,
    "camiseta": 2,
    "algodao": 2,
    "fone": 3,
    "headphone": 3,
    "garrafa": 4,
}
"""Word to concept dimension: synonyms share a dimension, so a fake vector
has "meaning" without a model."""


class ConceptEmbedder:
    """Embeds by counting concept words; records which role was asked."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[tuple[str, int]] = []

    def _vector(self, text: str) -> list[float]:
        """Count each known concept in ``text``.

        Args:
            text (str): Any text.

        Returns:
            list[float]: One count per concept dimension, plus a constant.
        """
        vector = [0.0] * 6
        for word in normalize_text(text).split():
            if word in CONCEPTS:
                vector[CONCEPTS[word]] += 1.0
        vector[5] = 0.1
        return vector

    async def embed(
        self, texts: str | list[str], *, batch_size: int = 32
    ) -> list[list[float]]:
        """Embed without a role.

        Args:
            texts (str | list[str]): The texts.
            batch_size (int): Unused.

        Returns:
            list[list[float]]: One vector per text.
        """
        items = [texts] if isinstance(texts, str) else texts
        self.calls.append(("embed", len(items)))
        return [self._vector(text) for text in items]

    async def embed_query(
        self, texts: str | list[str], *, batch_size: int = 32
    ) -> list[list[float]]:
        """Embed queries.

        Args:
            texts (str | list[str]): The queries.
            batch_size (int): Unused.

        Returns:
            list[list[float]]: One vector per query.
        """
        items = [texts] if isinstance(texts, str) else texts
        self.calls.append(("query", len(items)))
        return [self._vector(text) for text in items]

    async def embed_passages(
        self, texts: str | list[str], *, batch_size: int = 32
    ) -> list[list[float]]:
        """Embed passages.

        Args:
            texts (str | list[str]): The passages.
            batch_size (int): Unused.

        Returns:
            list[list[float]]: One vector per passage.
        """
        items = [texts] if isinstance(texts, str) else texts
        self.calls.append(("passages", len(items)))
        return [self._vector(text) for text in items]


PRODUCTS: list[tuple[str, int]] = [
    ("Calçado esportivo", 1),
    ("Camiseta de algodão", 2),
    ("Fone bluetooth", 3),
    ("Garrafa térmica", 4),
]


class TestSemantic:
    async def test_meaning_without_shared_letters(self) -> None:
        """Cenário: ``"tênis de corrida"`` contra ``"Calçado esportivo"``.

        Esperado: o calçado primeiro, sem nenhuma letra em comum relevante.
        """
        index = SemanticNameIndex(ConceptEmbedder(), PRODUCTS)
        best = (await index.search("tênis de corrida"))[0]
        assert (best.value, best.lexical_score) == (1, None)
        assert best.semantic_score == best.score > 0.9

    async def test_names_are_embedded_once_in_one_batch_as_passages(self) -> None:
        """Cenário: quatro nomes, duas buscas.

        Esperado: uma chamada ``embed_passages`` com os quatro, uma
        ``embed_query`` por busca.
        """
        embedder = ConceptEmbedder()
        index = SemanticNameIndex(embedder, PRODUCTS)
        await index.search("fone")
        await index.search("garrafa")
        assert embedder.calls == [("passages", 4), ("query", 1), ("query", 1)]

    async def test_a_name_added_later_is_embedded_on_the_next_search(self) -> None:
        """Cenário: nome adicionado depois da primeira busca.

        Esperado: só ele é embedado, e passa a ser achado.
        """
        embedder = ConceptEmbedder()
        index = SemanticNameIndex(embedder, PRODUCTS)
        await index.search("fone")
        index.add("Headphone gamer", 5)
        best = await index.search("headphone", limit=None)
        assert embedder.calls[-2] == ("passages", 1)
        assert {match.value for match in best[:2]} == {3, 5}

    async def test_pure_python_path_matches_numpy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cenário: a mesma busca com e sem NumPy.

        Esperado: as mesmas notas.
        """
        with_numpy = await SemanticNameIndex(ConceptEmbedder(), PRODUCTS).search(
            "tênis de corrida", limit=None
        )
        monkeypatch.setattr(module, "_HAS_NUMPY", False)
        plain = await SemanticNameIndex(ConceptEmbedder(), PRODUCTS).search(
            "tênis de corrida", limit=None
        )
        assert [match.value for match in plain] == [match.value for match in with_numpy]
        for left, right in zip(plain, with_numpy, strict=True):
            assert left.score == pytest.approx(right.score, abs=1e-6)

    async def test_empty_query_or_index(self) -> None:
        """Cenário: consulta em branco, índice vazio.

        Esperado: lista vazia, sem chamar o embedder.
        """
        embedder = ConceptEmbedder()
        assert await SemanticNameIndex(embedder, PRODUCTS).search("  ") == []
        assert await SemanticNameIndex(embedder).search("fone") == []
        assert embedder.calls == []


class TestHybrid:
    async def test_typo_is_found_by_spelling(self) -> None:
        """Cenário: ``"camizeta"`` (o embedder falso não conhece a palavra).

        Esperado: achado pelo lado léxico, com ``lexical_score`` alto.
        """
        index = HybridNameIndex(ConceptEmbedder(), PRODUCTS)
        best = (await index.search("camizeta"))[0]
        assert best.value == 2
        assert best.lexical_score is not None and best.lexical_score >= 0.85

    async def test_synonym_is_found_by_meaning(self) -> None:
        """Cenário: ``"tênis de corrida"``.

        Esperado: o calçado, achado só pelo lado semântico.
        """
        index = HybridNameIndex(ConceptEmbedder(), PRODUCTS)
        best = (await index.search("tênis de corrida"))[0]
        assert (best.value, best.lexical_score) == (1, None)

    async def test_both_sides_agreeing_rank_first(self) -> None:
        """Cenário: ``"garrafa"`` — escrita e sentido apontam o mesmo item.

        Esperado: primeiro, com as duas notas e RRF maior que o de um lado só.
        """
        index = HybridNameIndex(ConceptEmbedder(), PRODUCTS)
        matches = await index.search("garrafa", limit=None)
        best = matches[0]
        assert best.value == 4
        assert best.lexical_score is not None and best.semantic_score is not None
        assert all(best.score > match.score for match in matches[1:])

    async def test_short_words_do_not_count_as_spelling(self) -> None:
        """Cenário: ``"de"`` é a única palavra em comum com dois nomes.

        Esperado: nenhum achado léxico por ela.
        """
        index = HybridNameIndex(ConceptEmbedder(), PRODUCTS)
        matches = await index.search("xyz de", limit=None)
        assert all(match.lexical_score is None for match in matches)
