"""The words a deck or an answer key is written and printed in.

The parsers recognize structure by markers (``Lesson 1:``, ``Notes:``,
``Answer:``) and the renderers print labels (``Conclusion``, ``Speaker
notes``). None of that is fixed: both live here, as one frozen object per
document type, so a service writing its source text in Portuguese passes a
Portuguese vocabulary and gets ``Aula 1:`` parsed and ``Conclusão``
printed without forking a renderer.

The defaults are English. Ported from the ``slide-generator`` service of
the Leviathan project, whose markers and heuristics were pt-BR only; the
English defaults keep the same shape with English words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_SENTENCE_VERBS: tuple[str, ...] = (
    " is ",
    " are ",
    " was ",
    " were ",
    " will be ",
    " has ",
    " have ",
    " had ",
    " means ",
    " refers to ",
)
"""Verbs that mark a ``Head: tail`` line as a sentence, not a bullet.

``The result is: 42 after the second pass`` has a capitalized head and a
long tail, so it passes every shape test a ``Term: description`` bullet
has; the verb inside the head is what tells it apart. Each entry is padded
with spaces because the head is searched as `` <head lowercased> ``.
"""


@dataclass(frozen=True, slots=True)
class SlideVocabulary:
    """Markers the slide parser reads and labels the slide renderers print.

    Attributes:
        lesson_pattern (str): Regular expression matching a lesson header,
            case-insensitive, with group 1 the lesson number and group 2
            its title.
        notes_pattern (str): Regular expression matching a speaker-notes
            line, case-insensitive, with group 1 the note text. The note
            attaches to the paragraph or bullet right above it.
        conclusion_marker (str): Lowercase fragment that, found in a
            section title, renders that section as a conclusion slide.
        sentence_verbs (tuple[str, ...]): Space-padded verbs whose
            presence in a ``Head: tail`` head means the line is prose.
        ignored_lines (frozenset[str]): Lowercase lines dropped wherever
            they appear — the button labels and watermarks text copied
            from a web page carries along.
        default_lesson_title (str): Title of the implicit lesson created
            when content appears before any lesson header.
        default_deck_title (str): Deck title when the text has no lesson
            header and the caller passed none.
        lesson_label (str): Eyebrow of a lesson's slides; ``{index}`` is
            the lesson number.
        conclusion_label (str): Eyebrow of a conclusion slide.
        handout_subtitle (str): Line under the title on the DOCX cover.
        handout_cover_note (str): Placeholder text of a lesson-title page
            in the DOCX handout.
        notes_heading (str): Heading of the speaker-notes block in the
            DOCX handout.
        slide_label (str): Word before the number in the DOCX footer.
        language (str): BCP 47 tag written to ``<html lang>``.
        lesson_re (re.Pattern[str]): ``lesson_pattern``, compiled
            case-insensitively at construction.
        notes_re (re.Pattern[str]): ``notes_pattern``, compiled the same
            way.
    """

    lesson_pattern: str = r"^Lesson\s+(\d+)\s*:\s*(.+?)\s*$"
    notes_pattern: str = r"^(?:Notes|Note)\s*:\s*(.+)$"
    conclusion_marker: str = "conclusion"
    sentence_verbs: tuple[str, ...] = DEFAULT_SENTENCE_VERBS
    ignored_lines: frozenset[str] = field(default_factory=frozenset)
    default_lesson_title: str = "Content"
    default_deck_title: str = "Slides"
    lesson_label: str = "Lesson {index}"
    conclusion_label: str = "Conclusion"
    handout_subtitle: str = "Presenter handout"
    handout_cover_note: str = "Cover slide."
    notes_heading: str = "Speaker notes"
    slide_label: str = "Slide"
    language: str = "en"
    lesson_re: re.Pattern[str] = field(init=False, repr=False, compare=False)
    notes_re: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Compile both patterns once, so a typo fails at construction.

        Raises:
            ValueError: If a pattern does not compile, or
                ``lesson_pattern`` has fewer than two groups, or
                ``notes_pattern`` has none. A pattern without the groups
                the parser reads would match and then fail on the first
                lesson, far from where the vocabulary was written.
        """
        lesson = _compile(self.lesson_pattern, "lesson_pattern")
        notes = _compile(self.notes_pattern, "notes_pattern")
        if lesson.groups < 2:
            raise ValueError("lesson_pattern needs two groups: number and title")
        if notes.groups < 1:
            raise ValueError("notes_pattern needs one group: the note text")
        object.__setattr__(self, "lesson_re", lesson)
        object.__setattr__(self, "notes_re", notes)

    def lesson(self, index: int) -> str:
        """Format ``lesson_label`` for one lesson.

        Args:
            index (int): The lesson number.

        Returns:
            str: The label, e.g. ``"Lesson 2"``.
        """
        return self.lesson_label.format(index=index)


@dataclass(frozen=True, slots=True)
class AnswerKeyVocabulary:
    """Markers the answer-key parser reads and labels its renderers print.

    Attributes:
        title_prefixes (tuple[str, ...]): Lowercase prefixes of the line
            that sets the document title.
        subtitle_prefixes (tuple[str, ...]): Lowercase prefixes of the
            line that sets the subtitle.
        answer_pattern (str): Regular expression matching the first line
            of an answer, case-insensitive, with group 1 the text after
            the marker (possibly empty, when the answer starts on the
            next line).
        default_title (str): Title when neither the caller nor the text
            gives one and the first section has no heading.
        answer_label (str): Label printed before each answer.
        footer (str | None): Line closing the HTML and PDF document;
            ``None`` prints none.
        language (str): BCP 47 tag written to ``<html lang>``.
        answer_re (re.Pattern[str]): ``answer_pattern``, compiled
            case-insensitively at construction.
    """

    title_prefixes: tuple[str, ...] = ("title:",)
    subtitle_prefixes: tuple[str, ...] = ("subtitle:",)
    answer_pattern: str = r"^(?:A|Answer)\s*[:.]\s*(.*)$"
    default_title: str = "Answer key"
    answer_label: str = "Answer:"
    footer: str | None = None
    language: str = "en"
    answer_re: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Compile ``answer_pattern`` once, so a typo fails at construction.

        Raises:
            ValueError: If the pattern does not compile or has no group.
        """
        answer = _compile(self.answer_pattern, "answer_pattern")
        if answer.groups < 1:
            raise ValueError("answer_pattern needs one group: the answer text")
        object.__setattr__(self, "answer_re", answer)


def _compile(pattern: str, name: str) -> re.Pattern[str]:
    """Compile a vocabulary pattern, naming the field when it is invalid.

    Args:
        pattern (str): The regular expression.
        name (str): The field it came from.

    Returns:
        re.Pattern[str]: The pattern, compiled with ``re.IGNORECASE``.

    Raises:
        ValueError: If the pattern does not compile.
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"{name} is not a valid regular expression: {exc}") from exc


DEFAULT_SLIDE_VOCABULARY: SlideVocabulary = SlideVocabulary()
"""English markers and labels — what the slide functions use when given none."""

DEFAULT_ANSWER_KEY_VOCABULARY: AnswerKeyVocabulary = AnswerKeyVocabulary()
"""English markers and labels — what the answer-key functions use when given none."""
