"""Tests for ``tempest_fastapi_sdk.text``: normalization, edit distance, index."""

from __future__ import annotations

import random
import string

import pytest

from tempest_fastapi_sdk.text import (
    FuzzyIndex,
    levenshtein,
    max_edits,
    normalize_text,
    similarity,
    strip_accents,
    tokenize,
    trigrams,
)


class TestNormalize:
    def test_accents_case_and_punctuation(self) -> None:
        """Cenário: nome com acento, caixa e pontuação.

        Esperado: forma canônica com espaços simples.
        """
        assert normalize_text("  São José-dos Campos/SP ") == "sao jose dos campos sp"
        assert strip_accents("Ação Açaí") == "Acao Acai"
        assert normalize_text("!!!") == ""

    def test_tokens_keep_their_original_offsets(self) -> None:
        """Cenário: texto com acento e pontuação.

        Esperado: cada token normalizado aponta para o trecho original.
        """
        text = "Moro em São Paulo!"
        tokens = tokenize(text)
        assert [token.text for token in tokens] == ["moro", "em", "sao", "paulo"]
        assert [text[token.start : token.end] for token in tokens] == [
            "Moro",
            "em",
            "São",
            "Paulo",
        ]


class TestDistance:
    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            ("paulo", "paulo", 0),
            ("paolo", "paulo", 1),
            ("soa", "sao", 1),
            ("kitten", "sitting", 3),
            ("abc", "", 3),
            ("", "", 0),
        ],
    )
    def test_known_distances(self, left: str, right: str, expected: int) -> None:
        """Cenário: pares de distância conhecida, troca de vizinhos inclusa.

        Esperado: o valor exato, nos dois sentidos.
        """
        assert levenshtein(left, right) == expected
        assert levenshtein(right, left) == expected

    def test_limit_stops_early_without_undercounting(self) -> None:
        """Cenário: distância acima do limite.

        Esperado: ``limit + 1``; abaixo do limite, o valor exato.
        """
        assert levenshtein("kitten", "sitting", limit=1) == 2
        assert levenshtein("kitten", "sitting", limit=5) == 3

    def test_similarity_ratio(self) -> None:
        """Cenário: um erro em dez letras.

        Esperado: ``0.9``; iguais ``1.0``.
        """
        assert similarity("florianopo", "florianopx") == pytest.approx(0.9)
        assert similarity("", "") == 1.0

    def test_trigrams_are_padded(self) -> None:
        """Cenário: palavra curta.

        Esperado: as pontas entram nos trigramas.
        """
        assert trigrams("rio") == {" ri", "rio", "io "}


class TestFuzzyIndex:
    def test_exact_and_typo(self) -> None:
        """Cenário: índice com dois nomes.

        Esperado: busca exata ignora acento e caixa; erro de digitação acha
        o nome certo com nota abaixo de ``1.0``.
        """
        index = FuzzyIndex([("São Paulo", 1), ("Santos", 2)])
        assert index.exact("SAO PAULO") == [1]
        best = index.search("sao paolo")[0]
        assert (best.value, best.key) == (1, "sao paulo")
        assert 0.8 <= best.score < 1.0

    def test_one_key_many_values(self) -> None:
        """Cenário: dois valores sob o mesmo nome.

        Esperado: os dois voltam, mesma nota.
        """
        index = FuzzyIndex([("Bom Jesus", "PI"), ("Bom Jesus", "RS")])
        assert [match.value for match in index.search("bom jesus")] == ["PI", "RS"]
        assert len(index) == 1

    def test_cutoff_is_validated(self) -> None:
        """Cenário: ``cutoff`` fora de ``(0, 1]``.

        Esperado: ``ValueError``.
        """
        with pytest.raises(ValueError, match="cutoff"):
            FuzzyIndex([("a", 1)]).search("a", cutoff=0.0)

    def test_the_trigram_filter_loses_nothing(self) -> None:
        """Cenário: 300 consultas aleatórias contra 400 nomes, cutoff 0.75.

        Esperado: o índice devolve exatamente os nomes que uma comparação
        com todos aceitaria — o filtro de trigramas não descarta nenhum
        nome que a distância de edição aprovaria.
        """
        rng = random.Random(7)
        alphabet = string.ascii_lowercase[:8] + " "
        names = sorted(
            {
                "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 12))).strip()
                or "a"
                for _ in range(400)
            }
        )
        index = FuzzyIndex((name, name) for name in names)
        cutoff = 0.75
        for _ in range(300):
            base = rng.choice(names)
            query = list(base)
            for _ in range(rng.randint(0, 2)):
                if query:
                    query[rng.randrange(len(query))] = rng.choice(alphabet)
            text = normalize_text("".join(query))
            if not text:
                continue
            expected = {
                normalize_text(name)
                for name in names
                if normalize_text(name)
                and similarity(text, normalize_text(name)) >= cutoff
            }
            found = {
                match.key for match in index.search(text, limit=None, cutoff=cutoff)
            }
            assert found == expected, text

    def test_max_edits_bound(self) -> None:
        """Cenário: cutoff 0.8 em nove letras.

        Esperado: até duas edições (``0.2 * 9 / 0.8 = 2.25``).
        """
        assert max_edits(9, 0.8) == 2
        assert max_edits(9, 1.0) == 0
