"""Media types that do not depend on the host's ``/etc/mime.types``.

Python's compiled ``mimetypes`` table has no ``.xlsx`` (nor ``.docx``,
``.pptx``, ``.odt``, ``.ods``, ``.ogg``), so ``mimetypes.guess_type`` only
knows them where the host ships ``/etc/mime.types``. Measured in
``python:3.13-slim`` — the base of the Dockerfile ``tempest new``
generates — it returned ``None`` for all six, and a download that relied on
the guess was served as ``application/octet-stream``.

The container is simulated here by swapping ``mimetypes.guess_type`` for a
``MimeTypes`` built with no system files, which is the same lookup the slim
image ends up doing.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

import pytest

from tempest_fastapi_sdk.spreadsheet import XLSX_MEDIA_TYPE as SPREADSHEET_XLSX
from tempest_fastapi_sdk.utils import (
    DOCX_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    DownloadUtils,
    guess_media_type,
)
from tempest_fastapi_sdk.utils.media_types import _KNOWN_MEDIA_TYPES

PINNED: dict[str, str] = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ogg": "audio/ogg",
    ".webp": "image/webp",
}
"""The values read from ``media-types`` 10.1.0's ``/etc/mime.types``."""


@pytest.fixture
def slim_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``mimetypes.guess_type`` see no system files, like a slim image.

    Args:
        monkeypatch (pytest.MonkeyPatch): Restores the real lookup after.
    """
    bare = mimetypes.MimeTypes(filenames=())
    monkeypatch.setattr(mimetypes, "guess_type", bare.guess_type)


class TestTheTable:
    """The table's values are pinned, so drift fails here."""

    def test_values_match_the_source(self) -> None:
        assert _KNOWN_MEDIA_TYPES == PINNED

    def test_constants_are_the_table_entries(self) -> None:
        assert PINNED[".xlsx"] == XLSX_MEDIA_TYPE
        assert PINNED[".docx"] == DOCX_MEDIA_TYPE
        assert PINNED[".pptx"] == PPTX_MEDIA_TYPE

    def test_spreadsheet_reexports_the_same_constant(self) -> None:
        assert SPREADSHEET_XLSX is XLSX_MEDIA_TYPE

    @pytest.mark.parametrize("extension", sorted(PINNED))
    def test_the_stdlib_agrees_wherever_it_knows_the_extension(
        self,
        extension: str,
    ) -> None:
        stdlib = mimetypes.MimeTypes(filenames=()).guess_type(f"a{extension}")[0]
        assert stdlib in (None, PINNED[extension])


class TestGuessWithoutSystemFiles:
    """The guess no longer depends on the image having ``/etc/mime.types``."""

    @pytest.mark.usefixtures("slim_image")
    @pytest.mark.parametrize("extension", sorted(PINNED))
    def test_known_extensions_resolve(self, extension: str) -> None:
        assert guess_media_type(f"relatorio{extension}") == PINNED[extension]

    @pytest.mark.usefixtures("slim_image")
    def test_case_and_path_do_not_matter(self) -> None:
        assert guess_media_type("exports/Orçamento.XLSX") == XLSX_MEDIA_TYPE

    @pytest.mark.usefixtures("slim_image")
    def test_other_extensions_fall_through_to_the_stdlib(self) -> None:
        assert guess_media_type("a.pdf") == "application/pdf"
        assert guess_media_type("a.csv") == "text/csv"

    @pytest.mark.usefixtures("slim_image")
    def test_unknown_extension_is_none(self) -> None:
        assert guess_media_type("a.unknownext") is None
        assert guess_media_type("no-extension") is None


class TestDownloadsUseTheGuess:
    """``DownloadUtils`` serves the right type inside a slim image."""

    @pytest.mark.usefixtures("slim_image")
    def test_stream_of_a_workbook_is_served_as_xlsx(self, tmp_path: Path) -> None:
        response = DownloadUtils(tmp_path).stream(
            b"PK\x03\x04",
            filename="orcamento-7.xlsx",
        )

        assert response.media_type == XLSX_MEDIA_TYPE

    @pytest.mark.usefixtures("slim_image")
    def test_file_response_of_a_workbook_is_served_as_xlsx(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "relatorio.xlsx").write_bytes(b"PK\x03\x04")

        response = DownloadUtils(tmp_path).file_response("relatorio.xlsx")

        assert response.media_type == XLSX_MEDIA_TYPE

    @pytest.mark.usefixtures("slim_image")
    def test_an_explicit_media_type_still_wins(self, tmp_path: Path) -> None:
        response = DownloadUtils(tmp_path).stream(
            b"x",
            filename="a.xlsx",
            media_type="text/plain",
        )

        assert response.media_type == "text/plain"
