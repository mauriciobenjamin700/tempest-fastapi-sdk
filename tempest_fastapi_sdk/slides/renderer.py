"""Render a parsed deck or answer key to every supported format.

:class:`SlideRenderer` and :class:`AnswerKeyRenderer` bind a vocabulary and
a concurrency limit once, so a route renders with one call per format and
the labels printed always match the markers the text was parsed with.

The HTML and Markdown outputs are string work and return synchronously.
PDF, PPTX and DOCX are CPU-bound for long decks, so they run in a worker
thread behind a semaphore and never block the event loop.
"""

from __future__ import annotations

import asyncio

from tempest_fastapi_sdk.pdf.renderer import DEFAULT_MAX_CONCURRENT_RENDERS, PdfRenderer
from tempest_fastapi_sdk.slides.docx_renderer import (
    write_answer_key_docx,
    write_slides_docx,
)
from tempest_fastapi_sdk.slides.exceptions import missing_extra_message
from tempest_fastapi_sdk.slides.html_renderer import (
    write_answer_key_html,
    write_slides_html,
)
from tempest_fastapi_sdk.slides.markdown_renderer import (
    write_answer_key_markdown,
    write_slides_markdown,
)
from tempest_fastapi_sdk.slides.pptx_renderer import write_slides_pptx
from tempest_fastapi_sdk.slides.schemas import AnswerKey, SlideDeck, SlideTheme
from tempest_fastapi_sdk.slides.vocabulary import (
    DEFAULT_ANSWER_KEY_VOCABULARY,
    DEFAULT_SLIDE_VOCABULARY,
    AnswerKeyVocabulary,
    SlideVocabulary,
)


class SlideRenderer:
    """Renders a :class:`SlideDeck` as HTML, PDF, PPTX, DOCX or Markdown.

    Attributes:
        vocabulary (SlideVocabulary): Labels printed on the slides.
    """

    def __init__(
        self,
        *,
        vocabulary: SlideVocabulary = DEFAULT_SLIDE_VOCABULARY,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_RENDERS,
    ) -> None:
        """Configure the renderer.

        Args:
            vocabulary (SlideVocabulary): Labels printed on the slides —
                pass the one the deck was parsed with.
            max_concurrent (int): PDF, PPTX and DOCX renders allowed at
                once.

        Raises:
            ValueError: If ``max_concurrent`` is below 1.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.vocabulary: SlideVocabulary = vocabulary
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._pdf: PdfRenderer = PdfRenderer(max_concurrent=max_concurrent)

    def html(self, deck: SlideDeck, *, theme: SlideTheme = SlideTheme.DEFAULT) -> str:
        """Render the deck as a standalone HTML document.

        Args:
            deck (SlideDeck): The deck.
            theme (SlideTheme): Colour scheme.

        Returns:
            str: HTML with the CSS inlined and every value escaped.
        """
        return write_slides_html(deck, theme=theme, vocabulary=self.vocabulary)

    def markdown(self, deck: SlideDeck) -> str:
        """Render the deck as reveal.js Markdown.

        Args:
            deck (SlideDeck): The deck.

        Returns:
            str: Slides separated by ``---``, notes under ``Note:``.
        """
        return write_slides_markdown(deck, vocabulary=self.vocabulary)

    async def pdf(
        self, deck: SlideDeck, *, theme: SlideTheme = SlideTheme.DEFAULT
    ) -> bytes:
        """Render the deck as a PDF, one 1280x720 page per slide.

        Args:
            deck (SlideDeck): The deck.
            theme (SlideTheme): Colour scheme.

        Returns:
            bytes: The PDF.

        Raises:
            ImportError: When the ``[slides]`` extra is missing.
        """
        async with self._semaphore:
            try:
                return await self._pdf.render_html(self.html(deck, theme=theme))
            except ImportError as exc:  # pragma: no cover - extra-gated
                raise ImportError(missing_extra_message("weasyprint")) from exc

    async def pptx(
        self, deck: SlideDeck, *, theme: SlideTheme = SlideTheme.DEFAULT
    ) -> bytes:
        """Render the deck as a PowerPoint file, speaker notes included.

        Args:
            deck (SlideDeck): The deck.
            theme (SlideTheme): Colour scheme.

        Returns:
            bytes: The ``.pptx`` file.

        Raises:
            ImportError: When the ``[slides]`` extra is missing.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                write_slides_pptx, deck, theme=theme, vocabulary=self.vocabulary
            )

    async def docx(self, deck: SlideDeck) -> bytes:
        """Render the deck as a presenter handout, one slide per page.

        Args:
            deck (SlideDeck): The deck.

        Returns:
            bytes: The ``.docx`` file.

        Raises:
            ImportError: When the ``[slides]`` extra is missing.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                write_slides_docx, deck, vocabulary=self.vocabulary
            )


class AnswerKeyRenderer:
    """Renders an :class:`AnswerKey` as HTML, PDF, DOCX or Markdown.

    Attributes:
        vocabulary (AnswerKeyVocabulary): Labels printed on the document.
    """

    def __init__(
        self,
        *,
        vocabulary: AnswerKeyVocabulary = DEFAULT_ANSWER_KEY_VOCABULARY,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_RENDERS,
    ) -> None:
        """Configure the renderer.

        Args:
            vocabulary (AnswerKeyVocabulary): Labels printed on the
                document — pass the one the answer key was parsed with.
            max_concurrent (int): PDF and DOCX renders allowed at once.

        Raises:
            ValueError: If ``max_concurrent`` is below 1.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.vocabulary: AnswerKeyVocabulary = vocabulary
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._pdf: PdfRenderer = PdfRenderer(max_concurrent=max_concurrent)

    def html(
        self, answer_key: AnswerKey, *, theme: SlideTheme = SlideTheme.DEFAULT
    ) -> str:
        """Render the answer key as a standalone HTML document.

        Args:
            answer_key (AnswerKey): The answer key.
            theme (SlideTheme): Colour scheme.

        Returns:
            str: HTML with the CSS inlined, tags stripped and every value
            escaped.
        """
        return write_answer_key_html(
            answer_key, theme=theme, vocabulary=self.vocabulary
        )

    def markdown(self, answer_key: AnswerKey) -> str:
        """Render the answer key as plain Markdown.

        Args:
            answer_key (AnswerKey): The answer key.

        Returns:
            str: The document.
        """
        return write_answer_key_markdown(answer_key, vocabulary=self.vocabulary)

    async def pdf(
        self, answer_key: AnswerKey, *, theme: SlideTheme = SlideTheme.DEFAULT
    ) -> bytes:
        """Render the answer key as an A4 PDF.

        Args:
            answer_key (AnswerKey): The answer key.
            theme (SlideTheme): Colour scheme.

        Returns:
            bytes: The PDF.

        Raises:
            ImportError: When the ``[slides]`` extra is missing.
        """
        async with self._semaphore:
            try:
                return await self._pdf.render_html(self.html(answer_key, theme=theme))
            except ImportError as exc:  # pragma: no cover - extra-gated
                raise ImportError(missing_extra_message("weasyprint")) from exc

    async def docx(self, answer_key: AnswerKey) -> bytes:
        """Render the answer key as a Word document.

        Args:
            answer_key (AnswerKey): The answer key.

        Returns:
            bytes: The ``.docx`` file.

        Raises:
            ImportError: When the ``[slides]`` extra is missing.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                write_answer_key_docx, answer_key, vocabulary=self.vocabulary
            )
