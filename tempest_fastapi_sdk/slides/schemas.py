"""Typed documents the parsers produce and the renderers consume.

A deck and an answer key are plain data. Parsing turns structured text into
one of them; every renderer — HTML, PDF, PPTX, DOCX, Markdown — reads the
same object, so the formats cannot disagree about what the document says.
A caller that already has the structure (from a database, from a model's
JSON output) builds the schema directly and skips the parser.
"""

from __future__ import annotations

from pydantic import Field, computed_field

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema


class SlideKind(BaseStrEnum):
    """The visual variant a slide is rendered as.

    Attributes:
        LESSON_TITLE: The cover of a lesson — eyebrow plus a large title.
        SECTION: A section divider. No parser emits it today; it exists
            so a deck built by hand can mark one.
        CONTENT: Paragraphs under a title.
        BULLETS: A list of ``term: description`` items under a title.
        CONCLUSION: Paragraphs under a title, labelled as the conclusion.
    """

    LESSON_TITLE = "lesson_title"
    SECTION = "section"
    CONTENT = "content"
    BULLETS = "bullets"
    CONCLUSION = "conclusion"


class SlideTheme(BaseStrEnum):
    """The colour scheme applied by the HTML, PDF and PPTX renderers.

    Attributes:
        DEFAULT: Dark text on white, blue accents.
        DARK: Light text on a near-black slate background.
    """

    DEFAULT = "default"
    DARK = "dark"


class SlideBullet(BaseSchema):
    """One item of a ``BULLETS`` slide.

    Attributes:
        term (str | None): Highlighted lead shown before the description.
        description (str): The item's body.
    """

    term: str | None = Field(
        default=None,
        title="Term",
        description="Highlighted lead shown before the description.",
        examples=["NOT gate", None],
    )
    description: str = Field(
        title="Description",
        description="The item's body text.",
        examples=["Inverts its single input: 0 becomes 1 and 1 becomes 0."],
    )


class Slide(BaseSchema):
    """A single slide, ready to render.

    Attributes:
        kind (SlideKind): Which layout the slide uses.
        title (str): Title shown at the top.
        subtitle (str | None): Eyebrow of a lesson-title slide.
        paragraphs (list[str]): Body paragraphs of a content or
            conclusion slide.
        bullets (list[SlideBullet]): Items of a bullets slide.
        lesson_index (int | None): 1-based index of the lesson the slide
            belongs to, shown as the eyebrow of content slides.
        slide_number (int): 1-based position in the deck, shown in the
            footer.
        notes (str | None): Speaker notes. PPTX puts them in the notes
            pane, DOCX under each page, Markdown in a reveal.js ``Note:``
            block; HTML and PDF leave them out.
    """

    kind: SlideKind = Field(
        title="Kind",
        description="Which layout the slide uses.",
        examples=[SlideKind.CONTENT],
    )
    title: str = Field(
        title="Title",
        description="Title shown at the top of the slide.",
        examples=["Logic gates"],
    )
    subtitle: str | None = Field(
        default=None,
        title="Subtitle",
        description="Eyebrow of a lesson-title slide.",
        examples=["Lesson 1", None],
    )
    paragraphs: list[str] = Field(
        default_factory=list,
        title="Paragraphs",
        description="Body paragraphs of a content or conclusion slide.",
        examples=[["A logic gate maps binary inputs to one binary output."]],
    )
    bullets: list[SlideBullet] = Field(
        default_factory=list,
        title="Bullets",
        description="Items of a bullets slide.",
        examples=[[]],
    )
    lesson_index: int | None = Field(
        default=None,
        ge=1,
        title="Lesson index",
        description="1-based index of the lesson the slide belongs to.",
        examples=[1, None],
    )
    slide_number: int = Field(
        default=0,
        ge=0,
        title="Slide number",
        description="1-based position in the deck; 0 until the parser numbers it.",
        examples=[3],
    )
    notes: str | None = Field(
        default=None,
        title="Speaker notes",
        description="Notes for whoever presents the slide.",
        examples=["Ask the class for a real-world example first.", None],
    )


class SlideDeck(BaseSchema):
    """The ordered slides produced from one source text.

    Attributes:
        title (str): Deck title, printed in every footer.
        slides (list[Slide]): The slides, in presentation order.
    """

    title: str = Field(
        title="Title",
        description="Deck title, printed in every footer.",
        examples=["Digital electronics"],
    )
    slides: list[Slide] = Field(
        default_factory=list,
        title="Slides",
        description="The slides, in presentation order.",
        examples=[[]],
    )

    @computed_field(  # type: ignore[prop-decorator]
        title="Total",
        description="Number of slides in the deck.",
        examples=[12],
    )
    @property
    def total(self) -> int:
        """Return how many slides the deck has.

        Returns:
            int: ``len(self.slides)``.
        """
        return len(self.slides)


class AnswerKeyItem(BaseSchema):
    """One question and its answer.

    Attributes:
        question (str): The question, numbering included.
        answer (str): The expected answer.
    """

    question: str = Field(
        title="Question",
        description="The question text, numbering included.",
        examples=["1. What does a NOT gate do?"],
    )
    answer: str = Field(
        title="Answer",
        description="The expected answer.",
        examples=["It inverts its input."],
    )


class AnswerKeySection(BaseSchema):
    """A titled group of questions.

    Attributes:
        title (str): Heading above the items; empty for questions that
            appear before any heading.
        items (list[AnswerKeyItem]): The questions, in order.
    """

    title: str = Field(
        title="Title",
        description="Heading above the items; empty when the text had none.",
        examples=["Part 1: Logic gates", ""],
    )
    items: list[AnswerKeyItem] = Field(
        default_factory=list,
        title="Items",
        description="The questions of this section, in order.",
        examples=[[]],
    )


class AnswerKey(BaseSchema):
    """A whole answer key.

    Attributes:
        title (str): Document title.
        subtitle (str | None): Line under the title.
        sections (list[AnswerKeySection]): The sections, in order.
    """

    title: str = Field(
        title="Title",
        description="Document title.",
        examples=["Logic gates — answer key"],
    )
    subtitle: str | None = Field(
        default=None,
        title="Subtitle",
        description="Line printed under the title.",
        examples=["Class 3B, 2026", None],
    )
    sections: list[AnswerKeySection] = Field(
        default_factory=list,
        title="Sections",
        description="The sections, in order.",
        examples=[[]],
    )

    @computed_field(  # type: ignore[prop-decorator]
        title="Total items",
        description="Number of questions across every section.",
        examples=[10],
    )
    @property
    def total_items(self) -> int:
        """Return how many questions the answer key has.

        Returns:
            int: The item count summed over every section.
        """
        return sum(len(section.items) for section in self.sections)
