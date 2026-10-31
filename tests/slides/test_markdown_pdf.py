"""Tests for ``MarkdownPdfRenderer``, raw text and archives.

The archive cases build real zips in memory and render through WeasyPrint:
what is being held is that the asset policy reads only what the archive
carried, and that an archive is refused before anything is written when
its central directory declares too much.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from pypdf import PdfReader

from tempest_fastapi_sdk.pdf import AssetRefused
from tempest_fastapi_sdk.slides import (
    ABNT_MARKDOWN_CSS,
    DEFAULT_MARKDOWN_CSS,
    MarkdownArchiveError,
    MarkdownPdfRenderer,
    SlideSourceEmptyError,
    SlideSourceTooLargeError,
)

SQUARE_SVG: str = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    '<rect width="10" height="10"/></svg>'
)


def _zip(members: dict[str, str | bytes]) -> bytes:
    """Build a zip archive in memory.

    Args:
        members (dict[str, str | bytes]): Entry name to content.

    Returns:
        bytes: The archive.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _text(pdf: bytes) -> str:
    """Extract the text of every page.

    Args:
        pdf (bytes): The PDF.

    Returns:
        str: The pages' text, joined.
    """
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


class TestRawMarkdown:
    async def test_renders_with_the_default_title(self) -> None:
        """No title means ``default_title``."""
        result = await MarkdownPdfRenderer().render("# Heading\n\nSome text.")
        assert result.title == "Document"
        assert "Heading" in _text(result.content)

    async def test_abnt_uppercases_the_first_heading(self) -> None:
        """The stylesheet is applied: ABNT sets ``text-transform: uppercase``."""
        renderer = MarkdownPdfRenderer(stylesheet=ABNT_MARKDOWN_CSS)
        result = await renderer.render("# Introdução\n\nTexto.", title="TCC")
        assert result.title == "TCC"
        assert "INTRODUÇÃO" in _text(result.content)

    async def test_default_stylesheet_keeps_heading_case(self) -> None:
        """The neutral stylesheet does not transform text."""
        assert DEFAULT_MARKDOWN_CSS != ABNT_MARKDOWN_CSS
        result = await MarkdownPdfRenderer().render("# Introdução\n")
        assert "Introdução" in _text(result.content)

    async def test_local_file_reference_is_refused(self) -> None:
        """Raw text cannot read the host through ``file://``."""
        with pytest.raises(AssetRefused):
            await MarkdownPdfRenderer().render("![x](file:///etc/passwd)")

    async def test_remote_reference_is_refused(self) -> None:
        """Raw text cannot make a request from inside the network."""
        with pytest.raises(AssetRefused):
            await MarkdownPdfRenderer().render("![x](http://127.0.0.1:9/x.png)")

    async def test_empty_and_oversized_sources_are_refused(self) -> None:
        """The same limits as the parsers."""
        with pytest.raises(SlideSourceEmptyError):
            await MarkdownPdfRenderer().render("  \n")
        with pytest.raises(SlideSourceTooLargeError):
            await MarkdownPdfRenderer(max_chars=5).render("123456")

    async def test_title_is_escaped(self) -> None:
        """A title cannot close ``<title>`` and inject markup."""
        result = await MarkdownPdfRenderer().render("text", title="</title><img src=x>")
        assert result.content.startswith(b"%PDF-")


class TestArchive:
    async def test_image_inside_the_archive_is_embedded(self) -> None:
        """A relative path resolves against the Markdown file's directory."""
        data = _zip(
            {
                "doc/readme.md": "# Zipped\n\n![s](img/s.svg)\n",
                "doc/img/s.svg": SQUARE_SVG,
            }
        )
        result = await MarkdownPdfRenderer().render_archive(data)
        assert result.title == "readme"
        assert "Zipped" in _text(result.content)

    async def test_reference_outside_the_archive_is_refused(self) -> None:
        """``../`` from the Markdown cannot climb out of the extraction."""
        data = _zip({"readme.md": "![x](../../../../etc/passwd)\n"})
        with pytest.raises(AssetRefused):
            await MarkdownPdfRenderer().render_archive(data)

    async def test_first_markdown_in_path_order_wins(self) -> None:
        """Several ``.md`` files: the lexicographically first is the document."""
        data = _zip({"b.md": "# Second\n", "a.md": "# First\n"})
        result = await MarkdownPdfRenderer().render_archive(data, title="Override")
        assert result.title == "Override"
        assert "First" in _text(result.content)

    @pytest.mark.parametrize(
        ("members", "reason"),
        [
            ({"../evil.md": "# x"}, "unsafe_member"),
            ({"/abs/evil.md": "# x"}, "unsafe_member"),
            ({"notes.txt": "nothing"}, "no_markdown"),
            ({"readme.md": b"\xff\xfe\x00bad"}, "not_utf8"),
        ],
    )
    async def test_invalid_archives_name_the_reason(
        self, members: dict[str, str | bytes], reason: str
    ) -> None:
        """``details.reason`` says which check failed, never the member name."""
        with pytest.raises(MarkdownArchiveError) as caught:
            await MarkdownPdfRenderer().render_archive(_zip(members))
        assert caught.value.details == {"reason": reason}

    async def test_bytes_that_are_not_a_zip(self) -> None:
        """Garbage is ``not_a_zip``."""
        with pytest.raises(MarkdownArchiveError) as caught:
            await MarkdownPdfRenderer().render_archive(b"plain text")
        assert caught.value.details == {"reason": "not_a_zip"}

    async def test_declared_size_is_checked_before_extraction(self) -> None:
        """A small zip that inflates past the limit is refused."""
        data = _zip({"readme.md": "a" * 5000})
        assert len(data) < 5000
        with pytest.raises(MarkdownArchiveError) as caught:
            await MarkdownPdfRenderer(max_archive_bytes=1000).render_archive(data)
        assert caught.value.details == {"reason": "too_large"}

    async def test_member_count_is_bounded(self) -> None:
        """Too many entries is refused."""
        data = _zip({f"{i}.md": "x" for i in range(4)})
        with pytest.raises(MarkdownArchiveError) as caught:
            await MarkdownPdfRenderer(max_archive_members=3).render_archive(data)
        assert caught.value.details == {"reason": "too_many_members"}

    async def test_empty_markdown_in_archive(self) -> None:
        """An empty document is the same refusal as empty text."""
        with pytest.raises(SlideSourceEmptyError):
            await MarkdownPdfRenderer().render_archive(_zip({"readme.md": "\n"}))


class TestRefusalsDoNotEcho:
    async def test_refused_url_becomes_a_count(self) -> None:
        """The refusal names neither the URL nor the server's temporary path.

        WeasyPrint stops at the first refused fetch, so a document with two
        bad references reports one.
        """
        data = _zip(
            {"readme.md": "![a](../../../etc/passwd)\n![b](http://10.0.0.1/x.png)\n"}
        )
        with pytest.raises(AssetRefused) as caught:
            await MarkdownPdfRenderer().render_archive(data)
        assert caught.value.details == {"refused_count": 1}
        assert "passwd" not in caught.value.message
        assert "tempest-md-" not in str(caught.value.details)
