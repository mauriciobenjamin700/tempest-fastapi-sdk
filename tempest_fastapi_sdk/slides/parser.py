"""Turn structured plain text into a :class:`SlideDeck` or an :class:`AnswerKey`.

The input is the kind of text a person — or a language model told the
format — writes without markup:

.. code-block:: text

    Lesson 1: Logic gates
    What a gate is
    A logic gate maps binary inputs to a single binary output.
    - NOT gate: inverts its only input, so 0 becomes 1.
    Notes: ask for a real-world example first.
    Conclusion
    Every digital circuit is built from a handful of gates.

Lesson headers, notes lines and answer markers come from the vocabulary
(:class:`~tempest_fastapi_sdk.slides.SlideVocabulary`,
:class:`~tempest_fastapi_sdk.slides.AnswerKeyVocabulary`); section headings
and ``Term: description`` bullets are recognized by shape. Ported from the
``slide-generator`` service of the Leviathan project, whose heuristics are
kept as they were — only the words moved into the vocabulary.

Parsing is pure CPU over a string and finishes in milliseconds for the
default size limit, so these are plain functions rather than coroutines.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tempest_fastapi_sdk.slides.exceptions import (
    SlideSourceEmptyError,
    SlideSourceTooLargeError,
)
from tempest_fastapi_sdk.slides.schemas import (
    AnswerKey,
    AnswerKeyItem,
    AnswerKeySection,
    Slide,
    SlideBullet,
    SlideDeck,
    SlideKind,
)
from tempest_fastapi_sdk.slides.vocabulary import (
    DEFAULT_ANSWER_KEY_VOCABULARY,
    DEFAULT_SLIDE_VOCABULARY,
    AnswerKeyVocabulary,
    SlideVocabulary,
)

DEFAULT_MAX_SOURCE_CHARS: int = 200_000
"""Longest source text accepted, in characters. Ported from the
``MAX_TEXT_LENGTH`` default of Leviathan's ``slide-generator``."""

MAX_CHARS_PER_CONTENT_SLIDE: int = 700
"""Paragraph characters per slide before the section spills onto another."""

MAX_BULLETS_PER_SLIDE: int = 6
"""Bullets per slide before the section spills onto another."""

MAX_BULLET_HEAD_LENGTH: int = 80
"""Longest ``Term`` a ``Term: description`` bullet may have, in characters."""

MAX_BULLET_HEAD_WORDS: int = 8
"""Most words a bullet ``Term`` may have."""

MIN_BULLET_TAIL_LENGTH: int = 20
"""Shortest description a bullet may have; shorter reads as a label."""

MAX_SECTION_TITLE_LENGTH: int = 90
"""Longest line the slide parser treats as a section heading."""

MIN_SECTION_TITLE_LENGTH: int = 4
"""Shortest line the slide parser treats as a section heading."""

MAX_ANSWER_KEY_SECTION_TITLE_LENGTH: int = 120
"""Longest line the answer-key parser treats as a section heading."""

MIN_ANSWER_KEY_SECTION_TITLE_LENGTH: int = 3
"""Shortest line the answer-key parser treats as a section heading."""

BULLET_MARKERS: tuple[str, ...] = ("-", "*", "\u2022", "\u2013", "\u2014")
"""List markers stripped from the start of a bullet line: hyphen, asterisk,
bullet, en dash and em dash."""

_QUESTION_PREFIX_DIGITS_END: tuple[str, ...] = (".", ")")


def _check_source(text: str, max_chars: int | None) -> None:
    """Refuse an empty or oversized source before any work.

    Args:
        text (str): The source text.
        max_chars (int | None): The limit; ``None`` disables it.

    Raises:
        SlideSourceEmptyError: If the text is empty or whitespace.
        SlideSourceTooLargeError: If the text is longer than ``max_chars``.
    """
    if not text or not text.strip():
        raise SlideSourceEmptyError()
    if max_chars is not None and len(text) > max_chars:
        raise SlideSourceTooLargeError(max_chars)


def _strip_bullet_marker(line: str) -> str:
    """Remove one leading list marker followed by whitespace.

    An intraword hyphen survives because the marker must be followed by a
    space: ``- NOT gate: ...`` and ``NOT gate: ...`` parse identically,
    ``-5 volts: ...`` is left alone.

    Args:
        line (str): A trimmed line.

    Returns:
        str: The line without its marker, trimmed.
    """
    for marker in BULLET_MARKERS:
        if line.startswith(marker):
            rest = line[len(marker) :]
            if rest[:1].isspace():
                return rest.strip()
    return line


def _parse_bullet(line: str, vocabulary: SlideVocabulary) -> tuple[str, str] | None:
    """Recognize a ``Term: description`` bullet.

    Every rejection below exists because a sentence with a colon in it
    otherwise passes: the head must look like a term (capitalized, short,
    no sentence punctuation, balanced parentheses, no verb) and the tail
    like a description (long enough to say something).

    Args:
        line (str): A trimmed line.
        vocabulary (SlideVocabulary): Supplies the verbs that mark prose.

    Returns:
        tuple[str, str] | None: ``(term, description)``, or ``None`` when
        the line is not a bullet.
    """
    line = _strip_bullet_marker(line)
    head, separator, tail = line.partition(":")
    if not separator:
        return None
    head = head.strip()
    tail = tail.strip()
    if not head or not tail or not head[0].isupper():
        return None
    if len(head) > MAX_BULLET_HEAD_LENGTH or len(tail) < MIN_BULLET_TAIL_LENGTH:
        return None
    if len(head.split()) > MAX_BULLET_HEAD_WORDS:
        return None
    if any(char in head for char in ".,!?;") or head.count("(") != head.count(")"):
        return None
    padded = f" {head.lower()} "
    if any(verb in padded for verb in vocabulary.sentence_verbs):
        return None
    return head, tail


def _looks_like_slide_section(line: str) -> bool:
    """Tell whether a line reads as a section heading of a deck.

    Args:
        line (str): A trimmed, non-empty line.

    Returns:
        bool: ``True`` for a short, capitalized line without closing
        punctuation; a single word must be at least eight characters, so
        a stray ``Yes`` or ``Note`` does not open a section.
    """
    if not MIN_SECTION_TITLE_LENGTH <= len(line) <= MAX_SECTION_TITLE_LENGTH:
        return False
    if line[-1] in ".!,;:" or not line[0].isupper():
        return False
    return " " in line or len(line) >= 8


def _looks_like_answer_key_section(line: str) -> bool:
    """Tell whether a line reads as a section heading of an answer key.

    Args:
        line (str): A trimmed, non-empty line.

    Returns:
        bool: ``True`` for a line of heading length that starts with an
        uppercase letter and does not end like a sentence.
    """
    if not (
        MIN_ANSWER_KEY_SECTION_TITLE_LENGTH
        <= len(line)
        <= MAX_ANSWER_KEY_SECTION_TITLE_LENGTH
    ):
        return False
    if line[-1] in ".!,;":
        return False
    return line[0].isalpha() and line[0].isupper()


def _is_question(line: str) -> bool:
    """Tell whether a line starts a numbered question (``1.`` or ``1)``).

    Args:
        line (str): A trimmed line.

    Returns:
        bool: ``True`` when digits are followed, after optional spaces,
        by ``.`` or ``)`` and then by some text.
    """
    digits_end = 0
    while digits_end < len(line) and line[digits_end].isdigit():
        digits_end += 1
    if digits_end == 0:
        return False
    rest = line[digits_end:].lstrip()
    return rest[:1] in _QUESTION_PREFIX_DIGITS_END and bool(rest[1:].strip())


def _strip_prefix(line: str, prefixes: tuple[str, ...]) -> str | None:
    """Return the value after a metadata prefix, matched case-insensitively.

    Args:
        line (str): A trimmed line.
        prefixes (tuple[str, ...]): Lowercase prefixes to try.

    Returns:
        str | None: The trimmed value, or ``None`` when no prefix matches.
    """
    lowered = line.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def _join_notes(notes: list[str | None]) -> str | None:
    """Join the non-empty notes of one slide, separated by blank lines.

    Args:
        notes (list[str | None]): Notes of the items on the slide.

    Returns:
        str | None: The joined notes, or ``None`` when there are none.
    """
    parts = [note for note in notes if note]
    return "\n\n".join(parts) if parts else None


@dataclass
class _Section:
    """Paragraphs and bullets gathered under one heading.

    Attributes:
        title (str): The heading.
        paragraphs (list[str]): Paragraphs, in order.
        paragraph_notes (list[str | None]): Note of each paragraph.
        bullets (list[SlideBullet]): Bullets, in order.
        bullet_notes (list[str | None]): Note of each bullet.
    """

    title: str
    paragraphs: list[str] = field(default_factory=list)
    paragraph_notes: list[str | None] = field(default_factory=list)
    bullets: list[SlideBullet] = field(default_factory=list)
    bullet_notes: list[str | None] = field(default_factory=list)


@dataclass
class _Lesson:
    """Sections gathered under one lesson header.

    Attributes:
        index (int): The lesson number.
        title (str): The lesson title.
        sections (list[_Section]): Sections, in order.
    """

    index: int
    title: str
    sections: list[_Section] = field(default_factory=list)


class _DeckReader:
    """Walks the source line by line, grouping it into lessons and sections.

    A class rather than one long function because five pieces of state —
    the current lesson, section, paragraph buffer and what was added last —
    move together on every line, and a note attaches to whichever of
    paragraph or bullet came right before it.

    Attributes:
        vocabulary (SlideVocabulary): Markers in use.
        lessons (list[_Lesson]): Lessons read so far.
    """

    def __init__(self, vocabulary: SlideVocabulary) -> None:
        """Start an empty read.

        Args:
            vocabulary (SlideVocabulary): Markers in use.
        """
        self.vocabulary: SlideVocabulary = vocabulary
        self.lessons: list[_Lesson] = []
        self._lesson: _Lesson | None = None
        self._section: _Section | None = None
        self._buffer: list[str] = []
        self._last_added: str | None = None

    def read(self, text: str) -> list[_Lesson]:
        """Read every line of the source.

        Args:
            text (str): The source text.

        Returns:
            list[_Lesson]: The lessons, in source order.
        """
        for raw in text.splitlines():
            self._feed(raw.strip())
        self._flush_paragraph()
        return self.lessons

    def _feed(self, line: str) -> None:
        """Classify one trimmed line and file it.

        Args:
            line (str): The line.
        """
        if not line or line.lower() in self.vocabulary.ignored_lines:
            self._flush_paragraph()
            return
        lesson_match = self.vocabulary.lesson_re.match(line)
        if lesson_match:
            self._flush_paragraph()
            self._lesson = _Lesson(
                index=int(lesson_match.group(1)),
                title=lesson_match.group(2).strip(),
            )
            self._section = None
            self.lessons.append(self._lesson)
            self._last_added = "lesson"
            return
        lesson = self._current_lesson()
        notes_match = self.vocabulary.notes_re.match(line)
        if notes_match:
            self._flush_paragraph()
            self._attach_note(notes_match.group(1).strip())
            return
        bullet = _parse_bullet(line, self.vocabulary)
        if bullet is not None:
            self._flush_paragraph()
            section = self._current_section(lesson)
            section.bullets.append(SlideBullet(term=bullet[0], description=bullet[1]))
            section.bullet_notes.append(None)
            self._last_added = "bullet"
            return
        if _looks_like_slide_section(line):
            self._flush_paragraph()
            self._section = _Section(title=line)
            lesson.sections.append(self._section)
            self._last_added = "section"
            return
        self._buffer.append(line)

    def _current_lesson(self) -> _Lesson:
        """Return the open lesson, opening an implicit one when needed.

        Returns:
            _Lesson: The lesson new content belongs to.
        """
        if self._lesson is None:
            self._lesson = _Lesson(index=1, title=self.vocabulary.default_lesson_title)
            self.lessons.append(self._lesson)
        return self._lesson

    def _current_section(self, lesson: _Lesson) -> _Section:
        """Return the open section, opening one titled after the lesson.

        Args:
            lesson (_Lesson): The lesson the section belongs to.

        Returns:
            _Section: The section new content belongs to.
        """
        if self._section is None:
            self._section = _Section(title=lesson.title)
            lesson.sections.append(self._section)
        return self._section

    def _flush_paragraph(self) -> None:
        """Close the running paragraph into the current section."""
        if not self._buffer:
            return
        if self._lesson is not None:
            section = self._current_section(self._lesson)
            section.paragraphs.append(" ".join(self._buffer).strip())
            section.paragraph_notes.append(None)
            self._last_added = "paragraph"
        self._buffer.clear()

    def _attach_note(self, note: str) -> None:
        """Attach a note to the paragraph or bullet added just before it.

        A note that follows a heading or a lesson header has nothing to
        attach to and is dropped, as the original service did.

        Args:
            note (str): The note text.
        """
        section = self._section
        if section is None:
            return
        if self._last_added == "paragraph" and section.paragraph_notes:
            existing = section.paragraph_notes[-1]
            section.paragraph_notes[-1] = f"{existing}\n{note}" if existing else note
        elif self._last_added == "bullet" and section.bullet_notes:
            existing = section.bullet_notes[-1]
            section.bullet_notes[-1] = f"{existing}\n{note}" if existing else note


def _chunk_paragraphs(
    paragraphs: list[str], notes: list[str | None]
) -> list[tuple[list[str], list[str | None]]]:
    """Split a section's paragraphs into slide-sized groups.

    A paragraph is never cut: a group closes before the paragraph that
    would push it past ``MAX_CHARS_PER_CONTENT_SLIDE``, so one paragraph
    longer than the limit still lands alone on its own slide.

    Args:
        paragraphs (list[str]): The paragraphs.
        notes (list[str | None]): The note of each paragraph.

    Returns:
        list[tuple[list[str], list[str | None]]]: ``(paragraphs, notes)``
        per slide.
    """
    chunks: list[tuple[list[str], list[str | None]]] = []
    current: list[str] = []
    current_notes: list[str | None] = []
    length = 0
    for paragraph, note in zip(paragraphs, notes, strict=True):
        if current and length + len(paragraph) > MAX_CHARS_PER_CONTENT_SLIDE:
            chunks.append((current, current_notes))
            current, current_notes, length = [], [], 0
        current.append(paragraph)
        current_notes.append(note)
        length += len(paragraph)
    if current:
        chunks.append((current, current_notes))
    return chunks


def _build_slides(lessons: list[_Lesson], vocabulary: SlideVocabulary) -> list[Slide]:
    """Flatten lessons into numbered slides.

    Args:
        lessons (list[_Lesson]): The lessons read from the source.
        vocabulary (SlideVocabulary): Supplies the lesson label and the
            conclusion marker.

    Returns:
        list[Slide]: Slides with ``slide_number`` set from 1.
    """
    slides: list[Slide] = []
    for lesson in lessons:
        slides.append(
            Slide(
                kind=SlideKind.LESSON_TITLE,
                title=lesson.title,
                subtitle=vocabulary.lesson(lesson.index),
                lesson_index=lesson.index,
            )
        )
        for section in lesson.sections:
            is_conclusion = vocabulary.conclusion_marker in section.title.lower()
            kind = SlideKind.CONCLUSION if is_conclusion else SlideKind.CONTENT
            for chunk, chunk_notes in _chunk_paragraphs(
                section.paragraphs, section.paragraph_notes
            ):
                slides.append(
                    Slide(
                        kind=kind,
                        title=section.title,
                        paragraphs=chunk,
                        lesson_index=lesson.index,
                        notes=_join_notes(chunk_notes),
                    )
                )
            for start in range(0, len(section.bullets), MAX_BULLETS_PER_SLIDE):
                end = start + MAX_BULLETS_PER_SLIDE
                slides.append(
                    Slide(
                        kind=SlideKind.BULLETS,
                        title=section.title,
                        bullets=section.bullets[start:end],
                        lesson_index=lesson.index,
                        notes=_join_notes(section.bullet_notes[start:end]),
                    )
                )
    for number, slide in enumerate(slides, start=1):
        slide.slide_number = number
    return slides


def parse_slides(
    text: str,
    *,
    deck_title: str | None = None,
    vocabulary: SlideVocabulary = DEFAULT_SLIDE_VOCABULARY,
    max_chars: int | None = DEFAULT_MAX_SOURCE_CHARS,
) -> SlideDeck:
    """Parse structured text into a slide deck.

    Each lesson header opens a lesson and becomes its cover slide. Inside
    it, a short capitalized line opens a section; paragraphs and bullets
    under a section become content and bullet slides, split so none
    overflows; a section whose title contains the vocabulary's
    ``conclusion_marker`` becomes conclusion slides. Content before the
    first lesson header goes into an implicit lesson 1.

    Args:
        text (str): The source text.
        deck_title (str | None): Overrides the deck title, which is
            otherwise the first lesson's title.
        vocabulary (SlideVocabulary): Markers to recognize and labels to
            print. English by default.
        max_chars (int | None): Longest accepted source, in characters;
            ``None`` accepts any length.

    Returns:
        SlideDeck: The deck, slides numbered from 1.

    Raises:
        SlideSourceEmptyError: If ``text`` is empty or whitespace.
        SlideSourceTooLargeError: If ``text`` is longer than ``max_chars``.
    """
    _check_source(text, max_chars)
    lessons = _DeckReader(vocabulary).read(text)
    slides = _build_slides(lessons, vocabulary)
    title = deck_title or (
        lessons[0].title if lessons else vocabulary.default_deck_title
    )
    return SlideDeck(title=title, slides=slides)


@dataclass
class _QuestionDraft:
    """A question being read, with the answer lines seen so far.

    Attributes:
        question (str): The question text, continuation lines joined.
        answer_lines (list[str]): Lines of the answer.
        in_answer (bool): Whether the answer marker was seen.
    """

    question: str
    answer_lines: list[str] = field(default_factory=list)
    in_answer: bool = False

    def item(self) -> AnswerKeyItem:
        """Close the draft into an item.

        Returns:
            AnswerKeyItem: The question and its joined answer.
        """
        answer = " ".join(line for line in self.answer_lines if line).strip()
        return AnswerKeyItem(question=self.question, answer=answer)


class _AnswerKeyReader:
    """Walks the source line by line, grouping questions into sections.

    Attributes:
        vocabulary (AnswerKeyVocabulary): Markers in use.
        title (str | None): Title read from the text.
        subtitle (str | None): Subtitle read from the text.
        sections (list[AnswerKeySection]): Sections read so far.
    """

    def __init__(self, vocabulary: AnswerKeyVocabulary) -> None:
        """Start an empty read.

        Args:
            vocabulary (AnswerKeyVocabulary): Markers in use.
        """
        self.vocabulary: AnswerKeyVocabulary = vocabulary
        self.title: str | None = None
        self.subtitle: str | None = None
        self.sections: list[AnswerKeySection] = []
        self._section: AnswerKeySection | None = None
        self._draft: _QuestionDraft | None = None
        self._previous_blank: bool = True

    def read(self, text: str) -> None:
        """Read every line of the source.

        Args:
            text (str): The source text.
        """
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                self._previous_blank = True
                continue
            self._feed(line)
            self._previous_blank = False
        self._flush()

    def _feed(self, line: str) -> None:
        """Classify one non-empty trimmed line and file it.

        Args:
            line (str): The line.
        """
        title = _strip_prefix(line, self.vocabulary.title_prefixes)
        if title is not None:
            self._flush()
            self.title = title
            return
        subtitle = _strip_prefix(line, self.vocabulary.subtitle_prefixes)
        if subtitle is not None:
            self._flush()
            self.subtitle = subtitle
            return
        if _is_question(line):
            self._flush()
            self._draft = _QuestionDraft(question=line)
            return
        answer_match = self.vocabulary.answer_re.match(line)
        if answer_match and self._draft is not None:
            self._draft.in_answer = True
            first = answer_match.group(1).strip()
            if first:
                self._draft.answer_lines.append(first)
            return
        heading_allowed = self._draft is None or self._previous_blank
        if heading_allowed and _looks_like_answer_key_section(line):
            self._open_section(line)
            return
        if self._draft is None:
            self._open_section(line)
        elif self._draft.in_answer:
            self._draft.answer_lines.append(line)
        else:
            self._draft.question = f"{self._draft.question} {line}"

    def _open_section(self, title: str) -> None:
        """Close the running question and start a new section.

        Args:
            title (str): The section heading.
        """
        self._flush()
        self._section = AnswerKeySection(title=title)
        self.sections.append(self._section)

    def _flush(self) -> None:
        """Close the running question into the current section."""
        if self._draft is None:
            return
        if self._section is None:
            self._section = AnswerKeySection(title="")
            self.sections.append(self._section)
        self._section.items.append(self._draft.item())
        self._draft = None


def parse_answer_key(
    text: str,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    vocabulary: AnswerKeyVocabulary = DEFAULT_ANSWER_KEY_VOCABULARY,
    max_chars: int | None = DEFAULT_MAX_SOURCE_CHARS,
) -> AnswerKey:
    """Parse structured text into an answer key.

    A numbered line (``1.`` or ``1)``) opens a question; the vocabulary's
    answer marker opens its answer, which runs until the next question or
    heading. A capitalized line that does not end like a sentence opens a
    section, but only outside a question or after a blank line, so a
    wrapped question keeps its second line. Sections left without
    questions are dropped.

    Args:
        text (str): The source text.
        title (str | None): Overrides the title line found in the text.
        subtitle (str | None): Overrides the subtitle line found in the
            text.
        vocabulary (AnswerKeyVocabulary): Markers to recognize and labels
            to print. English by default.
        max_chars (int | None): Longest accepted source, in characters;
            ``None`` accepts any length.

    Returns:
        AnswerKey: The answer key. Its title falls back, in order, to the
        title line, the first section's heading and the vocabulary's
        ``default_title``.

    Raises:
        SlideSourceEmptyError: If ``text`` is empty or whitespace.
        SlideSourceTooLargeError: If ``text`` is longer than ``max_chars``.
    """
    _check_source(text, max_chars)
    reader = _AnswerKeyReader(vocabulary)
    reader.read(text)
    first_heading = reader.sections[0].title if reader.sections else ""
    return AnswerKey(
        title=title or reader.title or first_heading or vocabulary.default_title,
        subtitle=subtitle or reader.subtitle,
        sections=[section for section in reader.sections if section.items],
    )
