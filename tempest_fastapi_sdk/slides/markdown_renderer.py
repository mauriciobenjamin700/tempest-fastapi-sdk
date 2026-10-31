"""Write a deck as reveal.js Markdown, and an answer key as plain Markdown.

The deck output follows reveal.js conventions — ``---`` between slides,
``Note:`` before speaker notes — so it opens in reveal.js, Slidev-like
tools or any Markdown viewer. Ported from ``slide_markdown_service.py`` and
``answer_key_markdown_service.py`` of Leviathan's ``slide-generator``.

Pure string work: no extra needed.
"""

from __future__ import annotations

from tempest_fastapi_sdk.slides.html_renderer import strip_tags
from tempest_fastapi_sdk.slides.schemas import AnswerKey, Slide, SlideDeck, SlideKind
from tempest_fastapi_sdk.slides.vocabulary import AnswerKeyVocabulary, SlideVocabulary

SLIDE_SEPARATOR: str = "\n\n---\n\n"
"""reveal.js horizontal slide separator."""


def _slide_block(slide: Slide, vocabulary: SlideVocabulary) -> str:
    """Write one slide as a Markdown block.

    Args:
        slide (Slide): The slide.
        vocabulary (SlideVocabulary): Labels in use.

    Returns:
        str: The block, without the separator.
    """
    if slide.kind == SlideKind.LESSON_TITLE:
        eyebrow = f"<small>{slide.subtitle}</small>\n\n" if slide.subtitle else ""
        block = f"{eyebrow}# {slide.title}"
    else:
        if slide.kind == SlideKind.CONCLUSION:
            label: str | None = vocabulary.conclusion_label
        elif slide.lesson_index is not None:
            label = vocabulary.lesson(slide.lesson_index)
        else:
            label = None
        eyebrow = f"<small>{label}</small>\n\n" if label else ""
        if slide.kind == SlideKind.BULLETS:
            body = "\n".join(
                f"- **{bullet.term}**: {bullet.description}"
                if bullet.term
                else f"- {bullet.description}"
                for bullet in slide.bullets
            )
        else:
            body = "\n\n".join(slide.paragraphs)
        block = f"{eyebrow}## {slide.title}\n\n{body}".rstrip()
    if slide.notes:
        block += f"\n\nNote:\n{slide.notes}"
    return block


def write_slides_markdown(deck: SlideDeck, *, vocabulary: SlideVocabulary) -> str:
    """Write a deck as one reveal.js Markdown document.

    Args:
        deck (SlideDeck): The deck.
        vocabulary (SlideVocabulary): Labels to print.

    Returns:
        str: The document, ending in a newline.
    """
    header = f"<!-- Deck: {deck.title} -->\n<!-- Total slides: {deck.total} -->\n"
    body = SLIDE_SEPARATOR.join(
        _slide_block(slide, vocabulary) for slide in deck.slides
    )
    return f"{header}\n{body}\n"


def write_answer_key_markdown(
    answer_key: AnswerKey, *, vocabulary: AnswerKeyVocabulary
) -> str:
    """Write an answer key as plain Markdown.

    Args:
        answer_key (AnswerKey): The answer key.
        vocabulary (AnswerKeyVocabulary): Labels to print.

    Returns:
        str: The document, ending in a newline.
    """
    lines: list[str] = [f"# {answer_key.title}", ""]
    if answer_key.subtitle:
        lines.extend([f"_{answer_key.subtitle}_", ""])
    for section in answer_key.sections:
        lines.extend([f"## {section.title}", ""])
        for item in section.items:
            question = strip_tags(item.question).strip()
            answer = strip_tags(item.answer).strip()
            lines.extend(
                [f"**{question}**", "", f"> {vocabulary.answer_label} {answer}", ""]
            )
    return "\n".join(lines).rstrip() + "\n"
