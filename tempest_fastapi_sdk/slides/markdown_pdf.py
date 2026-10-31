"""Render Markdown — raw, or zipped with its images — to PDF.

The stylesheet is a parameter: :data:`~tempest_fastapi_sdk.slides.DEFAULT_MARKDOWN_CSS`
when none is given, :data:`~tempest_fastapi_sdk.slides.ABNT_MARKDOWN_CSS` for
academic work, or any CSS the service owns. Layout runs through
:class:`~tempest_fastapi_sdk.pdf.PdfRenderer`, so the asset policy that
governs every PDF of the SDK governs this one too: raw text may fetch
nothing, and an archive may read only the files it carried.

Ported from ``markdown_pdf_service.py`` and the ``/markdown`` router of
Leviathan's ``slide-generator``. Two things changed on the way, both
because the input is user content:

* **Assets are denied by default.** The original handed WeasyPrint its
  default fetcher, so ``![x](file:///etc/passwd)`` in the Markdown read the
  host and ``![x](http://10.0.0.1/)`` made a request from inside the
  network. Here the first is refused, and inside an archive a ``file://``
  path resolves only within the extraction directory.
* **Archives are bounded.** Member count and total uncompressed size are
  checked from the central directory before anything is written, so a
  small zip that inflates to gigabytes is refused without touching disk.
"""

from __future__ import annotations

import asyncio
import html
import io
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tempest_fastapi_sdk.pdf.assets import AssetPolicy, AssetRefused
from tempest_fastapi_sdk.pdf.renderer import DEFAULT_MAX_CONCURRENT_RENDERS, PdfRenderer
from tempest_fastapi_sdk.slides.exceptions import (
    MarkdownArchiveError,
    SlideSourceEmptyError,
    SlideSourceTooLargeError,
)
from tempest_fastapi_sdk.slides.mermaid import (
    DEFAULT_MERMAID_TIMEOUT,
    render_mermaid_blocks,
)
from tempest_fastapi_sdk.slides.parser import DEFAULT_MAX_SOURCE_CHARS
from tempest_fastapi_sdk.slides.styles import DEFAULT_MARKDOWN_CSS

MARKDOWN_EXTENSIONS: tuple[str, ...] = (
    "fenced_code",
    "codehilite",
    "tables",
    "toc",
    "attr_list",
    "footnotes",
)
"""Python-Markdown extensions enabled. Ported from Leviathan's
``slide-generator``; ``codehilite`` colours code only when Pygments is
installed and otherwise leaves it plain."""

DEFAULT_MAX_ARCHIVE_BYTES: int = 50 * 1024 * 1024
"""Largest total uncompressed size an archive may declare."""

DEFAULT_MAX_ARCHIVE_MEMBERS: int = 500
"""Most entries an archive may hold."""

DEFAULT_MERMAID_ERROR: str = "This Mermaid diagram could not be rendered."
"""Sentence printed in place of a diagram that did not render."""

DEFAULT_DOCUMENT_TITLE: str = "Document"
"""``<title>`` of a document rendered without one."""


@dataclass(frozen=True, slots=True)
class MarkdownPdf:
    """A rendered document and the title it was rendered under.

    Attributes:
        content (bytes): The PDF.
        title (str): The title used — the caller's, or for an archive the
            Markdown file's name without extension. Useful as the download
            file name.
    """

    content: bytes
    title: str


class MarkdownPdfRenderer:
    """Turns Markdown into PDF with a stylesheet the service chooses.

    Raw text is laid out by one renderer whose policy denies every fetch;
    an archive gets a renderer per call allowed to read only its own
    extraction directory. Both paths share one ``max_concurrent`` limit.

    Attributes:
        stylesheet (str): CSS inlined into every document.
        max_chars (int | None): Longest Markdown accepted, in characters.
        max_archive_bytes (int): Largest uncompressed archive accepted.
        max_archive_members (int): Most entries an archive may hold.
        mermaid_timeout (float): Seconds each Mermaid diagram has.
        mermaid_error_message (str): Sentence printed in place of a
            diagram that did not render.
        default_title (str): Title of a raw document rendered without one.
    """

    def __init__(
        self,
        *,
        stylesheet: str = DEFAULT_MARKDOWN_CSS,
        max_chars: int | None = DEFAULT_MAX_SOURCE_CHARS,
        max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
        max_archive_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
        mermaid_timeout: float = DEFAULT_MERMAID_TIMEOUT,
        mermaid_error_message: str = DEFAULT_MERMAID_ERROR,
        default_title: str = DEFAULT_DOCUMENT_TITLE,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_RENDERS,
    ) -> None:
        """Configure the renderer.

        Args:
            stylesheet (str): CSS inlined into every document.
            max_chars (int | None): Longest Markdown accepted, in
                characters; ``None`` accepts any length.
            max_archive_bytes (int): Largest total uncompressed size an
                archive may declare.
            max_archive_members (int): Most entries an archive may hold.
            mermaid_timeout (float): Seconds each Mermaid diagram has.
            mermaid_error_message (str): Sentence printed in place of a
                diagram that did not render.
            default_title (str): Title of a raw document rendered without
                one.
            max_concurrent (int): Documents laid out at once.

        Raises:
            ValueError: If ``max_concurrent`` is below 1.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.stylesheet: str = stylesheet
        self.max_chars: int | None = max_chars
        self.max_archive_bytes: int = max_archive_bytes
        self.max_archive_members: int = max_archive_members
        self.mermaid_timeout: float = mermaid_timeout
        self.mermaid_error_message: str = mermaid_error_message
        self.default_title: str = default_title
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._pdf: PdfRenderer = PdfRenderer(max_concurrent=max_concurrent)

    async def render(
        self,
        text: str,
        *,
        title: str | None = None,
        render_mermaid: bool = False,
    ) -> MarkdownPdf:
        """Render Markdown text to PDF.

        Args:
            text (str): The Markdown.
            title (str | None): Document title; ``default_title`` when
                omitted.
            render_mermaid (bool): Whether to draw ```` ```mermaid ````
                blocks (needs ``[slides-mermaid]`` and Chromium).

        Returns:
            MarkdownPdf: The PDF and the title used.

        Raises:
            SlideSourceEmptyError: If the text is empty or whitespace.
            SlideSourceTooLargeError: If the text exceeds ``max_chars``.
            AssetRefused: If the Markdown references any URL — raw text
                has no directory to resolve against. ``details`` carries
                only ``refused_count``, never the URLs.
            ImportError: When the ``[slides]`` extra is missing.
        """
        effective_title = title or self.default_title
        html_document = await self._document(text, effective_title, render_mermaid)
        async with self._semaphore:
            content = await _without_echo(self._pdf, html_document, base_url=None)
        return MarkdownPdf(content=content, title=effective_title)

    async def render_archive(
        self,
        data: bytes,
        *,
        title: str | None = None,
        render_mermaid: bool = False,
    ) -> MarkdownPdf:
        """Render a zip holding one Markdown file and the images it references.

        The first ``.md`` file in path order is the document; relative
        image paths resolve against its directory, and nothing outside the
        extracted archive can be read.

        Args:
            data (bytes): The zip archive.
            title (str | None): Document title; the Markdown file's name
                without extension when omitted.
            render_mermaid (bool): Whether to draw ```` ```mermaid ````
                blocks.

        Returns:
            MarkdownPdf: The PDF and the title used.

        Raises:
            MarkdownArchiveError: If the archive is not a zip, holds an
                entry that would land outside the extraction directory, is
                too large or has too many entries, has no ``.md`` file, or
                its Markdown is not UTF-8.
            SlideSourceEmptyError: If the Markdown file is empty.
            SlideSourceTooLargeError: If the Markdown exceeds ``max_chars``.
            AssetRefused: If the Markdown references a URL outside the
                archive. ``details`` carries only ``refused_count``.
        """
        with tempfile.TemporaryDirectory(prefix="tempest-md-") as scratch:
            root = Path(scratch).resolve()
            markdown_path = await asyncio.to_thread(self._extract, data, root)
            try:
                text = markdown_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise MarkdownArchiveError("not_utf8") from exc
            effective_title = title or markdown_path.stem
            html_document = await self._document(text, effective_title, render_mermaid)
            renderer = PdfRenderer(
                assets=AssetPolicy(allow_dirs=(root,)), max_concurrent=1
            )
            async with self._semaphore:
                content = await _without_echo(
                    renderer,
                    html_document,
                    base_url=markdown_path.parent.as_uri() + "/",
                )
        return MarkdownPdf(content=content, title=effective_title)

    async def _document(self, text: str, title: str, render_mermaid: bool) -> str:
        """Check the source and build the HTML document WeasyPrint lays out.

        Args:
            text (str): The Markdown.
            title (str): The ``<title>``.
            render_mermaid (bool): Whether to draw Mermaid blocks first.

        Returns:
            str: A standalone HTML document with the stylesheet inlined.

        Raises:
            SlideSourceEmptyError: If the text is empty or whitespace.
            SlideSourceTooLargeError: If the text exceeds ``max_chars``.
        """
        source = text.strip()
        if not source:
            raise SlideSourceEmptyError()
        if self.max_chars is not None and len(source) > self.max_chars:
            raise SlideSourceTooLargeError(self.max_chars)
        if render_mermaid:
            source = await render_mermaid_blocks(
                source,
                error_message=self.mermaid_error_message,
                timeout=self.mermaid_timeout,
            )
        body = await asyncio.to_thread(_markdown_to_html, source)
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{html.escape(title)}</title>"
            f"<style>{self.stylesheet}</style>"
            f"</head><body>{body}</body></html>"
        )

    def _extract(self, data: bytes, root: Path) -> Path:
        """Validate an archive and extract it, returning its Markdown file.

        Every check reads the central directory before a byte is written.

        Args:
            data (bytes): The zip archive.
            root (Path): Resolved extraction directory.

        Returns:
            Path: The first ``.md`` file, in path order.

        Raises:
            MarkdownArchiveError: As described in :meth:`render_archive`.
        """
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise MarkdownArchiveError("not_a_zip") from exc
        with archive:
            members = archive.infolist()
            if len(members) > self.max_archive_members:
                raise MarkdownArchiveError("too_many_members")
            if sum(member.file_size for member in members) > self.max_archive_bytes:
                raise MarkdownArchiveError("too_large")
            for member in members:
                target = (root / member.filename).resolve()
                if not target.is_relative_to(root) or target == root:
                    raise MarkdownArchiveError("unsafe_member")
            archive.extractall(root)
        candidates = sorted(
            (
                member.filename
                for member in members
                if not member.is_dir()
                and PurePosixPath(member.filename).suffix.lower() == ".md"
            ),
        )
        if not candidates:
            raise MarkdownArchiveError("no_markdown")
        return (root / candidates[0]).resolve()


async def _without_echo(
    renderer: PdfRenderer, html_document: str, *, base_url: str | None
) -> bytes:
    """Render, replacing a refusal's URL list with its length.

    :class:`~tempest_fastapi_sdk.pdf.AssetRefused` lists the refused URLs
    in ``details`` so a template author can fix them. Here the document is
    user content, so that list is the user's own input echoed back — and,
    inside an archive, resolved against the server's temporary directory,
    whose path it would disclose. The class and its code stay the same;
    only the list becomes a count — in practice ``1``, because WeasyPrint
    stops at the first refused fetch.

    Args:
        renderer (PdfRenderer): The renderer to use.
        html_document (str): The document.
        base_url (str | None): Base for relative URLs.

    Returns:
        bytes: The PDF.

    Raises:
        AssetRefused: With ``details={"refused_count": n}``.
    """
    try:
        return await renderer.render_html(html_document, base_url=base_url)
    except AssetRefused as exc:
        refused = exc.details.get("refused", [])
        count = len(refused) if isinstance(refused, list) else 1
        raise AssetRefused(
            message="the document referenced an asset that is not allowed",
            details={"refused_count": count},
        ) from None


def _markdown_to_html(source: str) -> str:
    """Convert Markdown to an HTML fragment.

    Args:
        source (str): The Markdown.

    Returns:
        str: The HTML body.

    Raises:
        ImportError: When ``markdown`` is missing.
    """
    import markdown

    converted: str = markdown.markdown(source, extensions=list(MARKDOWN_EXTENSIONS))
    return converted
