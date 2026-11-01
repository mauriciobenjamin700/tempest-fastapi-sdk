"""Tests for ``parse_slides``, ``parse_answer_key`` and the vocabularies.

Most cases run under a Portuguese vocabulary built the way the Leviathan
project builds it, with the texts of the ``slide-generator`` suite these
parsers were ported from: the port is only faithful if the original inputs
still come out the same way. The English defaults get their own cases.
"""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.slides import (
    AnswerKeyVocabulary,
    SlideKindEnum,
    SlideSourceEmptyError,
    SlideSourceTooLargeError,
    SlideVocabulary,
    parse_answer_key,
    parse_slides,
)

PT_BR_SLIDES: SlideVocabulary = SlideVocabulary(
    lesson_pattern=r"^Aula\s+(\d+)\s*:\s*(.+?)\s*$",
    notes_pattern=r"^(?:Notas|Nota|Notes|Note)\s*:\s*(.+)$",
    conclusion_marker="conclus",
    sentence_verbs=(
        " é ",
        " são ",
        " foi ",
        " foram ",
        " era ",
        " eram ",
        " será ",
        " serão ",
        " está ",
        " estão ",
        " esteve ",
        " tem ",
        " têm ",
        " possui ",
        " possuem ",
        " baseia-se ",
        " trata-se ",
    ),
    ignored_lines=frozenset(
        {
            "shutterstock",
            "explorar",
            "voltar",
            "compartilhar",
            "baixar",
            "ver mais",
            "fechar",
            "anúncio",
            "publicidade",
        }
    ),
    default_lesson_title="Conteúdo",
    lesson_label="Aula {index}",
    conclusion_label="Conclusão",
)

PT_BR_ANSWER_KEY: AnswerKeyVocabulary = AnswerKeyVocabulary(
    title_prefixes=("título:", "titulo:", "title:"),
    subtitle_prefixes=("subtítulo:", "subtitulo:", "subtitle:"),
    answer_pattern=r"^(?:R|Resposta|A|Answer)\s*[:.]\s*(.*)$",
    default_title="Gabarito",
    answer_label="Resposta:",
)

SAMPLE_DECK: str = (
    "Aula 1: Fundamentos da Lógica Digital e Portas Lógicas\n"
    "Introdução ao Pensamento Binário\n"
    "A eletrônica digital baseia-se na ideia de que toda informação "
    "pode ser representada por apenas dois estados: Ligado (1) ou "
    "Desligado (0). No nível do hardware, esses estados são "
    "representados por níveis de tensão.\n"
    "\n"
    "As Portas Lógicas Fundamentais\n"
    "As portas lógicas são os tijolos de qualquer processador ou "
    "circuito digital.\n"
    "\n"
    "Porta NOT (Inversora): É a mais simples. Se entrar 0, sai 1. "
    "Se entrar 1, sai 0.\n"
    "\n"
    "Porta AND (E): A saída só é nível alto se todas as entradas "
    "forem nível alto na eletrônica.\n"
    "\n"
    "Porta OR (OU): Basta uma entrada ser 1 para a saída ser 1 "
    "nesse circuito paralelo.\n"
    "\n"
    "Shutterstock\n"
    "Explorar\n"
    "\n"
    "Aula 2: Conversores Analógico-Digitais\n"
    "A Ponte entre dois Mundos\n"
    "O mundo real é analógico, mas o computador só entende números "
    "binários discretos.\n"
    "\n"
    "Conclusão da Unidade\n"
    "Ao final destas aulas o aluno entende sensores, conversores e "
    "portas lógicas em conjunto.\n"
)

SAMPLE_ANSWER_KEY: str = (
    "Título: Gabarito de Eletrônica Digital e Sensores\n"
    "Subtítulo: Disciplina: Eletrônica Digital | 2º Ano Técnico\n"
    "\n"
    "Parte 1: Portas Lógicas e Álgebra Booleana\n"
    "\n"
    "1. Explique a diferença entre sinal analógico e digital.\n"
    "R: O sinal analógico é contínuo no tempo. O sinal digital é "
    "discreto.\n"
    "\n"
    "2. Qual é a função da porta lógica NOT?\n"
    "R: Sua função é a inversão lógica. Se a entrada é 1, a saída é 0.\n"
    "\n"
    "Parte 2: Conversores (ADC e DAC)\n"
    "\n"
    "8. O que significa a sigla ADC?\n"
    "R: ADC é o conversor analógico-digital.\n"
)


def _kinds(text: str, kind: SlideKindEnum) -> int:
    """Count the slides of one kind a Portuguese text produces.

    Args:
        text (str): The source.
        kind (SlideKindEnum): The kind to count.

    Returns:
        int: How many slides have that kind.
    """
    deck = parse_slides(text, vocabulary=PT_BR_SLIDES)
    return sum(1 for slide in deck.slides if slide.kind == kind)


class TestParseSlidesPortuguese:
    def test_each_lesson_header_becomes_a_cover(self) -> None:
        """``Aula N:`` opens a lesson whose cover carries the lesson label."""
        deck = parse_slides(SAMPLE_DECK, vocabulary=PT_BR_SLIDES)
        covers = [s for s in deck.slides if s.kind == SlideKindEnum.LESSON_TITLE]
        assert [c.subtitle for c in covers] == ["Aula 1", "Aula 2"]

    def test_bullets_of_one_section_share_a_slide(self) -> None:
        """Three ``Term: description`` lines under one heading make one slide."""
        deck = parse_slides(SAMPLE_DECK, vocabulary=PT_BR_SLIDES)
        bullets = [s for s in deck.slides if s.kind == SlideKindEnum.BULLETS]
        assert len(bullets) == 1
        assert {b.term for b in bullets[0].bullets} == {
            "Porta NOT (Inversora)",
            "Porta AND (E)",
            "Porta OR (OU)",
        }

    def test_ignored_lines_never_become_slides(self) -> None:
        """Watermark text copied from a page is dropped."""
        titles = [
            s.title for s in parse_slides(SAMPLE_DECK, vocabulary=PT_BR_SLIDES).slides
        ]
        assert "Shutterstock" not in titles
        assert "Explorar" not in titles

    def test_conclusion_marker_selects_the_conclusion_kind(self) -> None:
        """A heading containing ``conclus`` renders as a conclusion."""
        deck = parse_slides(SAMPLE_DECK, vocabulary=PT_BR_SLIDES)
        conclusions = [s for s in deck.slides if s.kind == SlideKindEnum.CONCLUSION]
        assert [c.title for c in conclusions] == ["Conclusão da Unidade"]

    def test_slides_are_numbered_in_order(self) -> None:
        """``slide_number`` runs 1..n."""
        deck = parse_slides(SAMPLE_DECK, vocabulary=PT_BR_SLIDES)
        assert [s.slide_number for s in deck.slides] == list(range(1, deck.total + 1))

    def test_deck_title_defaults_to_the_first_lesson(self) -> None:
        """Without ``deck_title`` the first lesson names the deck."""
        deck = parse_slides(SAMPLE_DECK, vocabulary=PT_BR_SLIDES)
        assert deck.title == "Fundamentos da Lógica Digital e Portas Lógicas"

    def test_explicit_deck_title_wins(self) -> None:
        """``deck_title`` overrides the lesson title."""
        deck = parse_slides(
            SAMPLE_DECK, deck_title="Eletrônica — 2º Ano", vocabulary=PT_BR_SLIDES
        )
        assert deck.title == "Eletrônica — 2º Ano"

    def test_late_colon_in_a_sentence_is_not_a_bullet(self) -> None:
        """A long head with a verb stays a paragraph."""
        text = (
            "Aula 1: Teste\nSeção\nA eletrônica digital baseia-se na ideia de "
            "que toda informação pode ser representada por apenas dois estados: "
            "Ligado (1) ou Desligado (0).\n"
        )
        assert _kinds(text, SlideKindEnum.BULLETS) == 0

    def test_copula_in_the_head_is_not_a_bullet(self) -> None:
        """``O mundo real é analógico: ...`` reads as prose."""
        text = (
            "Aula 1: Teste\nSeção\nO mundo real é analógico: a temperatura não "
            "pula de 20°C para 21°C; ela passa por todos os valores.\n"
        )
        assert _kinds(text, SlideKindEnum.BULLETS) == 0
        assert _kinds(text, SlideKindEnum.CONTENT) == 1

    def test_unbalanced_parenthesis_in_the_head_is_not_a_bullet(self) -> None:
        """An unclosed ``(`` before the colon means the colon is mid-sentence."""
        text = (
            "Aula 1: Teste\nSeção\nO processo de transformar uma tensão (ex: "
            "vinda de um microfone) em um número binário envolve três etapas.\n"
        )
        assert _kinds(text, SlideKindEnum.BULLETS) == 0

    def test_question_heading_opens_a_section(self) -> None:
        """A heading may end in ``?``."""
        text = (
            "Aula 1: Sensores\nO que são Sensores e Transdutores?\nUm sensor é "
            "um dispositivo que detecta uma mudança em uma grandeza física.\n"
        )
        deck = parse_slides(text, vocabulary=PT_BR_SLIDES)
        content = [s for s in deck.slides if s.kind == SlideKindEnum.CONTENT]
        assert [c.title for c in content] == ["O que são Sensores e Transdutores?"]

    def test_leading_dash_and_bare_bullets_parse_alike(self) -> None:
        """``- Term: description`` and ``Term: description`` are one form."""
        text = (
            "Aula 1: Periféricos\nCapacidades\n"
            "- Identificar dispositivos: reconhecer cada periférico do setup\n"
            "Verificar conexões: distinguir portas USB, HDMI e de rede\n"
        )
        deck = parse_slides(text, vocabulary=PT_BR_SLIDES)
        bullets = [s for s in deck.slides if s.kind == SlideKindEnum.BULLETS]
        assert {b.term for b in bullets[0].bullets} == {
            "Identificar dispositivos",
            "Verificar conexões",
        }

    def test_intraword_hyphen_is_kept_in_the_term(self) -> None:
        """``Plug-and-play`` is a term, not a marker plus ``and-play``."""
        text = (
            "Aula 1: Drivers\nConceitos\nPlug-and-play: reconhece o dispositivo "
            "automaticamente ao conectá-lo\n"
        )
        deck = parse_slides(text, vocabulary=PT_BR_SLIDES)
        bullets = [s for s in deck.slides if s.kind == SlideKindEnum.BULLETS]
        assert bullets[0].bullets[0].term == "Plug-and-play"

    def test_notes_attach_to_the_item_right_above(self) -> None:
        """A notes line lands on the preceding paragraph or bullet."""
        text = (
            "Aula 1: Portas\nIntrodução\nUm parágrafo qualquer sobre portas.\n"
            "Notas: Lembrar do exemplo do LED.\n\nTipos\n"
            "Porta NOT: inverte o sinal de entrada sempre.\n"
            "Nota: Mostrar tabela verdade.\n"
        )
        deck = parse_slides(text, vocabulary=PT_BR_SLIDES)
        content = next(s for s in deck.slides if s.kind == SlideKindEnum.CONTENT)
        bullets = next(s for s in deck.slides if s.kind == SlideKindEnum.BULLETS)
        assert content.notes == "Lembrar do exemplo do LED."
        assert bullets.notes == "Mostrar tabela verdade."

    def test_content_before_any_lesson_goes_to_an_implicit_lesson(self) -> None:
        """The implicit lesson takes ``default_lesson_title``."""
        deck = parse_slides(
            "Seção solta\nUm parágrafo sem aula.\n", vocabulary=PT_BR_SLIDES
        )
        assert deck.slides[0].title == "Conteúdo"
        assert deck.slides[0].subtitle == "Aula 1"


class TestParseSlidesSplitting:
    def test_more_than_six_bullets_spill_onto_another_slide(self) -> None:
        """Bullets are grouped six per slide."""
        lines = "\n".join(
            f"Term {i}: a description long enough to count" for i in range(8)
        )
        deck = parse_slides(f"Lesson 1: Many\nThe list\n{lines}\n")
        sizes = [len(s.bullets) for s in deck.slides if s.kind == SlideKindEnum.BULLETS]
        assert sizes == [6, 2]

    def test_long_paragraphs_spill_without_being_cut(self) -> None:
        """A paragraph never splits; the group closes before it overflows."""
        paragraph = "word " * 100
        text = f"Lesson 1: Long\nThe section\n{paragraph}\n\n{paragraph}\n"
        deck = parse_slides(text)
        content = [s for s in deck.slides if s.kind == SlideKindEnum.CONTENT]
        assert [len(s.paragraphs) for s in content] == [1, 1]


class TestParseSlidesEnglishDefaults:
    def test_lesson_and_conclusion_use_english_markers(self) -> None:
        """The default vocabulary reads ``Lesson N:`` and ``Conclusion``."""
        deck = parse_slides(
            "Lesson 2: Gates\nWrapping up the conclusion\nGates compose circuits.\n"
        )
        assert deck.slides[0].subtitle == "Lesson 2"
        assert deck.slides[1].kind == SlideKindEnum.CONCLUSION

    def test_portuguese_header_is_plain_content_under_defaults(self) -> None:
        """``Aula 1:`` is not a marker in English; it is a heading."""
        deck = parse_slides("Aula 1: Portas\nUm parágrafo.\n")
        assert deck.slides[0].title == "Content"

    def test_english_verb_marks_prose(self) -> None:
        """``The result is: ...`` stays a paragraph."""
        deck = parse_slides(
            "Lesson 1: X\nA section\nThe result is: forty-two after two passes\n"
        )
        assert all(s.kind != SlideKindEnum.BULLETS for s in deck.slides)


class TestSourceLimits:
    @pytest.mark.parametrize("text", ["", "   \n\n  "])
    def test_empty_source_is_refused(self, text: str) -> None:
        """Whitespace is not a deck."""
        with pytest.raises(SlideSourceEmptyError):
            parse_slides(text)
        with pytest.raises(SlideSourceEmptyError):
            parse_answer_key(text)

    def test_oversized_source_is_refused_with_the_limit(self) -> None:
        """The 413 carries the limit, never the text."""
        with pytest.raises(SlideSourceTooLargeError) as caught:
            parse_slides("x" * 11, max_chars=10)
        assert caught.value.status_code == 413
        assert caught.value.details == {"limit": 10}
        assert "xxxx" not in caught.value.message

    def test_none_disables_the_limit(self) -> None:
        """``max_chars=None`` accepts any length."""
        assert parse_slides("Lesson 1: X\n" + "y " * 150_000, max_chars=None).total >= 1


class TestParseAnswerKey:
    def test_title_and_subtitle_come_from_their_lines(self) -> None:
        """``Título:`` and ``Subtítulo:`` are metadata, not sections."""
        key = parse_answer_key(SAMPLE_ANSWER_KEY, vocabulary=PT_BR_ANSWER_KEY)
        assert key.title == "Gabarito de Eletrônica Digital e Sensores"
        assert key.subtitle == "Disciplina: Eletrônica Digital | 2º Ano Técnico"

    def test_questions_are_grouped_by_section(self) -> None:
        """Each ``Parte N:`` heading opens a section."""
        key = parse_answer_key(SAMPLE_ANSWER_KEY, vocabulary=PT_BR_ANSWER_KEY)
        assert [s.title for s in key.sections] == [
            "Parte 1: Portas Lógicas e Álgebra Booleana",
            "Parte 2: Conversores (ADC e DAC)",
        ]
        assert [len(s.items) for s in key.sections] == [2, 1]
        assert key.total_items == 3

    def test_numbering_stays_in_the_question(self) -> None:
        """The original ``1.`` is kept."""
        key = parse_answer_key(SAMPLE_ANSWER_KEY, vocabulary=PT_BR_ANSWER_KEY)
        assert key.sections[0].items[0].question.startswith("1. ")

    def test_multiline_answers_join_with_spaces(self) -> None:
        """Continuation lines extend the answer."""
        key = parse_answer_key(
            "1. Pergunta?\nR: Linha um.\nLinha dois continua aqui.\n",
            vocabulary=PT_BR_ANSWER_KEY,
        )
        assert key.sections[0].items[0].answer == "Linha um. Linha dois continua aqui."

    def test_arguments_override_the_text(self) -> None:
        """``title`` and ``subtitle`` win over the metadata lines."""
        key = parse_answer_key(
            SAMPLE_ANSWER_KEY,
            title="Override",
            subtitle="Override sub",
            vocabulary=PT_BR_ANSWER_KEY,
        )
        assert (key.title, key.subtitle) == ("Override", "Override sub")

    def test_questions_without_a_heading_get_an_untitled_section(self) -> None:
        """The title then falls back to the vocabulary default."""
        key = parse_answer_key(
            "1. Pergunta?\nR: Resposta.\n", vocabulary=PT_BR_ANSWER_KEY
        )
        assert key.sections[0].title == ""
        assert key.sections[0].items[0].answer == "Resposta."
        assert key.title == "Gabarito"

    def test_resposta_marker_and_answer_on_next_line(self) -> None:
        """``Resposta:`` alone opens an answer written below it."""
        key = parse_answer_key(
            "1) Pergunta longa?\nResposta:\nTexto da resposta longa.\n",
            vocabulary=PT_BR_ANSWER_KEY,
        )
        assert key.sections[0].items[0].answer == "Texto da resposta longa."

    def test_english_defaults(self) -> None:
        """``Title:`` and ``Answer:`` are the default markers."""
        key = parse_answer_key("Title: Quiz\n1. Why?\nAnswer: Because.\n")
        assert key.title == "Quiz"
        assert key.sections[0].items[0].answer == "Because."


class TestVocabularyValidation:
    def test_lesson_pattern_needs_two_groups(self) -> None:
        """A pattern the parser cannot read fails at construction."""
        with pytest.raises(ValueError, match="two groups"):
            SlideVocabulary(lesson_pattern=r"^Aula\s+\d+:")

    def test_invalid_regex_names_the_field(self) -> None:
        """A typo says which field it is in."""
        with pytest.raises(ValueError, match="notes_pattern"):
            SlideVocabulary(notes_pattern=r"^(Notes")

    def test_answer_pattern_needs_a_group(self) -> None:
        """The answer text group is required."""
        with pytest.raises(ValueError, match="one group"):
            AnswerKeyVocabulary(answer_pattern=r"^R:")

    def test_lesson_label_formats_the_index(self) -> None:
        """``lesson()`` fills ``{index}``."""
        assert PT_BR_SLIDES.lesson(3) == "Aula 3"
