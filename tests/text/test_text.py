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

    @pytest.mark.parametrize("cutoff", [0.5, 0.6, 0.75, 0.8, 0.85, 0.9])
    def test_the_trigram_filter_loses_nothing(self, cutoff: float) -> None:
        """Cenário: 300 consultas aleatórias contra 600 nomes curtos de um
        alfabeto pequeno, em cada ``cutoff`` (o caso em que o filtro mais
        corta).

        Esperado: o índice devolve exatamente os nomes que a comparação com
        todos aceitaria. Abaixo de ``0.75`` o limite ``|Q| - 4d`` chega a zero
        e o índice tem de varrer; antes da correção, ``"ebfg"`` não achava
        ``"efbg"`` em ``0.75``.
        """
        rng = random.Random(int(cutoff * 100))
        alphabet = string.ascii_lowercase[:7]
        names = sorted(
            {
                "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 9)))
                for _ in range(600)
            }
        )
        index = FuzzyIndex((name, name) for name in names)
        for _ in range(300):
            query = list(rng.choice(names))
            for _ in range(rng.randint(0, 2)):
                position = rng.randrange(len(query))
                if rng.random() < 0.5 and position + 1 < len(query):
                    query[position], query[position + 1] = (
                        query[position + 1],
                        query[position],
                    )
                else:
                    query[position] = rng.choice(alphabet)
            text = "".join(query)
            expected = {name for name in names if similarity(text, name) >= cutoff}
            found = {
                match.key for match in index.search(text, limit=None, cutoff=cutoff)
            }
            assert found == expected, (cutoff, text)

    def test_a_swap_with_no_shared_trigram_is_found(self) -> None:
        """Cenário: o par que a auditoria achou, ``"ebfg"`` x ``"efbg"``.

        Esperado: achado em ``0.75`` (uma troca de vizinhos).
        """
        index = FuzzyIndex([("efbg", 1)])
        assert [match.value for match in index.search("ebfg", cutoff=0.75)] == [1]

    def test_max_edits_bound(self) -> None:
        """Cenário: cutoff 0.8 em nove letras.

        Esperado: até duas edições (``0.2 * 9 / 0.8 = 2.25``).
        """
        assert max_edits(9, 0.8) == 2
        assert max_edits(9, 1.0) == 0


def test_the_banded_distance_matches_the_full_one() -> None:
    """Cenário: 5000 pares aleatórios, com e sem ``limit``.

    Esperado: com ``limit``, o valor exato quando cabe e ``limit + 1`` quando
    não cabe — a faixa em volta da diagonal não muda o resultado.
    """
    rng = random.Random(11)
    for _ in range(5000):
        left = "".join(rng.choice("abcd") for _ in range(rng.randint(0, 9)))
        right = "".join(rng.choice("abcd") for _ in range(rng.randint(0, 9)))
        full = levenshtein(left, right)
        limit = rng.randint(0, 4)
        expected = full if full <= limit else limit + 1
        assert levenshtein(left, right, limit=limit) == expected, (left, right, limit)
