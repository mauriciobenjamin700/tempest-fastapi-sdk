"""Write a deck or an answer key as one standalone HTML document.

The HTML is both the browser preview and the input of the PDF renderer, so
the CSS is inlined and nothing is fetched: the document renders the same
offline, in an ``<iframe srcdoc>``, and under WeasyPrint's default-deny
asset policy. Ported from ``slide_html_service.py`` and
``answer_key_html_service.py`` of Leviathan's ``slide-generator``.

Every value is autoescaped. The original answer-key template marked the
question and answer ``| safe``, which let a ``<script>`` in the source text
run inside whatever page embeds the preview; here tags are stripped first
and the rest is escaped, matching what the DOCX and Markdown outputs show.

``jinja2`` is imported on first render, so importing this module does not
need the ``[slides]`` extra.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from tempest_fastapi_sdk.slides.exceptions import missing_extra_message
from tempest_fastapi_sdk.slides.schemas import AnswerKey, SlideDeck, SlideTheme
from tempest_fastapi_sdk.slides.vocabulary import AnswerKeyVocabulary, SlideVocabulary

if TYPE_CHECKING:
    from jinja2 import Environment

TEMPLATE_DIR: Path = Path(__file__).parent / "templates"
"""Directory holding the bundled templates and stylesheets."""

HTML_TAG_RE: re.Pattern[str] = re.compile(r"<[^>]+>")
"""Inline HTML tag. Answer keys written by a model sometimes carry ``<b>``;
every renderer strips tags so no format shows markup another one hides."""


def strip_tags(text: str) -> str:
    """Remove inline HTML tags, keeping the text between them.

    Args:
        text (str): Text that may carry tags.

    Returns:
        str: The text without tags.
    """
    return HTML_TAG_RE.sub("", text)


@lru_cache(maxsize=1)
def _environment() -> Environment:
    """Build the Jinja environment once per process.

    Returns:
        Environment: Loads from :data:`TEMPLATE_DIR`, autoescaping every
        template regardless of extension.

    Raises:
        ImportError: When ``jinja2`` is missing.
    """
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError as exc:  # pragma: no cover - extra-gated
        raise ImportError(missing_extra_message("jinja2")) from exc

    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["strip_tags"] = strip_tags
    return environment


@lru_cache(maxsize=4)
def _stylesheet(name: str) -> str:
    """Read a bundled stylesheet once per process.

    Args:
        name (str): File name inside :data:`TEMPLATE_DIR`.

    Returns:
        str: The CSS.
    """
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def write_slides_html(
    deck: SlideDeck, *, theme: SlideTheme, vocabulary: SlideVocabulary
) -> str:
    """Write a deck as a standalone HTML document, one 1280x720 frame per slide.

    Args:
        deck (SlideDeck): The deck.
        theme (SlideTheme): Colour scheme.
        vocabulary (SlideVocabulary): Labels and ``<html lang>``.

    Returns:
        str: The document.

    Raises:
        ImportError: When ``jinja2`` is missing.
    """
    template = _environment().get_template("slides.html")
    return template.render(
        deck=deck,
        theme=SlideTheme(theme).value,
        vocabulary=vocabulary,
        styles=_stylesheet("slide_styles.css"),
    )


def write_answer_key_html(
    answer_key: AnswerKey, *, theme: SlideTheme, vocabulary: AnswerKeyVocabulary
) -> str:
    """Write an answer key as a standalone HTML document, A4 pages.

    Args:
        answer_key (AnswerKey): The answer key.
        theme (SlideTheme): Colour scheme.
        vocabulary (AnswerKeyVocabulary): Labels and ``<html lang>``.

    Returns:
        str: The document.

    Raises:
        ImportError: When ``jinja2`` is missing.
    """
    template = _environment().get_template("answer_key.html")
    return template.render(
        answer_key=answer_key,
        theme=SlideTheme(theme).value,
        vocabulary=vocabulary,
        styles=_stylesheet("answer_key_styles.css"),
    )
