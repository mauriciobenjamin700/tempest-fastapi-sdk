"""Tests for the size limits of the ``.xlsx`` readers and the Google readers.

Every hostile workbook is built here, small on disk: the sheet XML is
written through a streaming ZIP writer, so a member that decompresses to
101 MiB costs about 100 KB of file and never sits whole in memory. The
measurements behind the defaults (RSS per row, compression ratios of real
workbooks, the 505 MB bomb) live in the constants' docstrings; these tests
pin the behaviour, not the numbers.
"""

from __future__ import annotations

import io
import zipfile
import zlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, Final

import httpx
import openpyxl
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.exceptions.upload import FileTooLargeException
from tempest_fastapi_sdk.spreadsheet import (
    DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES,
    DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES,
    DEFAULT_XLSX_MAX_COMPRESSION_RATIO,
    DEFAULT_XLSX_MAX_ROWS,
    DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES,
    XLSX_MEDIA_TYPE,
    GoogleSheetAccessError,
    InvalidSpreadsheetError,
    SpreadsheetTooLargeError,
    download_google_sheet_xlsx,
    new_workbook,
    read_google_sheet,
    read_google_sheet_as,
    read_google_sheet_xlsx,
    read_xlsx,
    read_xlsx_as,
    read_xlsx_sheets,
    workbook_to_bytes,
)
from tempest_fastapi_sdk.spreadsheet.reader import _number_rows

SHEET_PATH: Final[str] = "xl/worksheets/sheet1.xml"
SHEET_HEAD: Final[bytes] = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    b'<sheetData><row><c t="inlineStr"><is><t>nome</t></is></c>'
    b'<c t="inlineStr"><is><t>valor</t></is></c></row>'
)
SHEET_ROW: Final[bytes] = (
    b'<row><c t="inlineStr"><is><t>item</t></is></c><c><v>1</v></c></row>'
)
SHEET_TAIL: Final[bytes] = b"</sheetData></worksheet>"
MIB: Final[int] = 1024 * 1024
SHARE_LINK: Final[str] = (
    "https://docs.google.com/spreadsheets/d/"
    "1h0ATstw2f6ryXvbwV-DW6zwsBRIF-2k5zHcm2uTEge8/edit"
)


def _shell() -> bytes:
    """Build a one-tab workbook whose sheet XML the tests replace.

    Returns:
        bytes: A valid ``.xlsx`` with a header-only tab.
    """
    workbook = new_workbook("Dados")
    workbook["Dados"].append(["nome", "valor"])
    return workbook_to_bytes(workbook)


def _workbook_with_sheet(rows: int, *, padding: int = 0) -> bytes:
    """Build a workbook whose first tab holds ``rows`` identical data rows.

    The sheet is streamed into the archive, so the decompressed size can be
    far larger than anything the test holds in memory.

    Args:
        rows (int): Data rows after the header.
        padding (int): Bytes of XML whitespace appended after the rows —
            the most compressible content there is, used to inflate the
            member without adding rows.

    Returns:
        bytes: The ``.xlsx`` file.
    """
    source = zipfile.ZipFile(io.BytesIO(_shell()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            if info.filename != SHEET_PATH:
                archive.writestr(info, source.read(info.filename))
                continue
            with archive.open(SHEET_PATH, "w", force_zip64=True) as member:
                member.write(SHEET_HEAD)
                block: bytes = SHEET_ROW * 1000
                for _ in range(rows // 1000):
                    member.write(block)
                member.write(SHEET_ROW * (rows % 1000))
                chunk: bytes = b" " * MIB
                for _ in range(padding // MIB):
                    member.write(chunk)
                member.write(b" " * (padding % MIB))
                member.write(SHEET_TAIL)
    return buffer.getvalue()


def _with_lying_directory(data: bytes, claimed: int, *, matching_crc: bool) -> bytes:
    """Rewrite the central directory so the sheet claims ``claimed`` bytes.

    Args:
        data (bytes): A workbook built by :func:`_workbook_with_sheet`.
        claimed (int): The decompressed size the directory will declare.
        matching_crc (bool): Also rewrite the CRC to the one of the first
            ``claimed`` bytes, so the lie is self-consistent.

    Returns:
        bytes: The workbook with the lying directory.
    """
    source = zipfile.ZipFile(io.BytesIO(data))
    real_sheet: bytes = source.read(SHEET_PATH)
    buffer = io.BytesIO()
    archive = zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED)
    for info in source.infolist():
        archive.writestr(info.filename, source.read(info.filename))
    for info in archive.filelist:
        if info.filename == SHEET_PATH:
            info.file_size = claimed
            if matching_crc:
                info.CRC = zlib.crc32(real_sheet[:claimed])
    archive.close()
    return buffer.getvalue()


def _forbid_openpyxl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any attempt to open the workbook fail the test.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture.
    """

    def refuse(*args: Any, **kwargs: Any) -> None:
        """Fail: the guard should have refused before the engine ran.

        Args:
            *args (Any): Ignored.
            **kwargs (Any): Ignored.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("openpyxl.load_workbook was called")

    monkeypatch.setattr(openpyxl, "load_workbook", refuse)


class TestDecompressedSize:
    """The sum of the members' declared sizes is checked first."""

    def test_default_refuses_a_bomb_before_openpyxl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ~100 KB file inflating past 100 MiB never reaches the engine."""
        bomb = _workbook_with_sheet(10, padding=DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES)
        assert len(bomb) < 1 * MIB
        _forbid_openpyxl(monkeypatch)
        with pytest.raises(SpreadsheetTooLargeError) as caught:
            read_xlsx(bomb)
        details = caught.value.details
        assert details["limit"] == "uncompressed_bytes"
        assert details["max"] == DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES
        assert details["actual"] > DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES

    def test_custom_limit_and_status(self) -> None:
        """A lower limit trips on a small workbook; the error is a 413."""
        data = _workbook_with_sheet(10, padding=512 * 1024)
        with pytest.raises(SpreadsheetTooLargeError) as caught:
            read_xlsx(data, max_uncompressed_bytes=256 * 1024)
        error = caught.value
        assert isinstance(error, FileTooLargeException)
        assert error.status_code == 413
        assert error.code == "SPREADSHEET_TOO_LARGE"

    def test_none_disables_the_limit(self) -> None:
        """``None`` reads the same workbook."""
        data = _workbook_with_sheet(10, padding=512 * 1024)
        rows = read_xlsx(data, max_uncompressed_bytes=None)
        assert len(rows) == 10

    def test_every_reader_enforces_it(self) -> None:
        """``read_xlsx_as`` and ``read_xlsx_sheets`` share the check."""

        class Item(BaseModel):
            """One row."""

            nome: str
            valor: int

        data = _workbook_with_sheet(10, padding=512 * 1024)
        with pytest.raises(SpreadsheetTooLargeError):
            read_xlsx_as(data, Item, max_uncompressed_bytes=256 * 1024)
        with pytest.raises(SpreadsheetTooLargeError):
            read_xlsx_sheets(data, max_uncompressed_bytes=256 * 1024)

    def test_path_and_file_object_sources(self, tmp_path: Path) -> None:
        """A path and a file object are checked too; the stream is rewound."""
        data = _workbook_with_sheet(10, padding=512 * 1024)
        path = tmp_path / "bomb.xlsx"
        path.write_bytes(data)
        with pytest.raises(SpreadsheetTooLargeError):
            read_xlsx(path, max_uncompressed_bytes=256 * 1024)
        stream = io.BytesIO(data)
        stream.seek(7)
        with pytest.raises(SpreadsheetTooLargeError):
            read_xlsx(stream, max_uncompressed_bytes=256 * 1024)
        assert stream.tell() == 7
        stream.seek(0)
        assert len(read_xlsx(stream)) == 10

    def test_not_a_zip_is_still_invalid(self) -> None:
        """The archive check keeps the old error for a non-ZIP source."""
        with pytest.raises(InvalidSpreadsheetError):
            read_xlsx(b"nome,valor\nitem,1\n")


class TestCompressionRatio:
    """A member that inflates far beyond its stored size is refused."""

    def test_bomb_under_the_size_limit_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2 MiB of whitespace compresses ~1 000 times: refused by ratio."""
        data = _workbook_with_sheet(10, padding=2 * MIB)
        _forbid_openpyxl(monkeypatch)
        with pytest.raises(SpreadsheetTooLargeError) as caught:
            read_xlsx(data)
        details = caught.value.details
        assert details["limit"] == "compression_ratio"
        assert details["member"] == SHEET_PATH
        assert details["actual"] > DEFAULT_XLSX_MAX_COMPRESSION_RATIO

    def test_small_members_are_not_judged(self) -> None:
        """Below 1 MiB decompressed, a high ratio is not a reason to refuse."""
        data = _workbook_with_sheet(10, padding=512 * 1024)
        info = zipfile.ZipFile(io.BytesIO(data)).getinfo(SHEET_PATH)
        assert info.file_size / info.compress_size > DEFAULT_XLSX_MAX_COMPRESSION_RATIO
        assert len(read_xlsx(data)) == 10

    def test_none_disables_the_ratio(self) -> None:
        """``None`` lets the same workbook through."""
        data = _workbook_with_sheet(10, padding=2 * MIB)
        assert len(read_xlsx(data, max_compression_ratio=None)) == 10

    def test_writer_output_passes(self) -> None:
        """A 20 000-row tab of identical rows stays far below the default."""
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        sheet.append(["id", "cliente", "total"])
        for _ in range(20_000):
            sheet.append([1, "Cliente", 9.9])
        data = workbook_to_bytes(workbook)
        info = zipfile.ZipFile(io.BytesIO(data)).getinfo(SHEET_PATH)
        assert info.file_size >= MIB
        assert info.file_size / info.compress_size < DEFAULT_XLSX_MAX_COMPRESSION_RATIO
        assert len(read_xlsx(data)) == 20_000


class TestLyingCentralDirectory:
    """``file_size`` comes from the central directory, and it can lie."""

    def test_zipfile_stops_at_the_declared_size(self) -> None:
        """Pin the property the guard relies on: output stops at the claim."""
        data = _with_lying_directory(
            _workbook_with_sheet(50_000), 4096, matching_crc=True
        )
        archive = zipfile.ZipFile(io.BytesIO(data))
        assert archive.getinfo(SHEET_PATH).compress_size > 4096
        with archive.open(SHEET_PATH) as member:
            assert len(member.read()) == 4096

    def test_understated_size_with_wrong_crc_is_invalid(self) -> None:
        """A smaller claim without a matching CRC fails the CRC check."""
        data = _with_lying_directory(
            _workbook_with_sheet(50_000), 4096, matching_crc=False
        )
        with pytest.raises(InvalidSpreadsheetError) as caught:
            read_xlsx(data)
        assert "CRC" in caught.value.details["reason"]

    def test_understated_size_with_matching_crc_is_invalid(self) -> None:
        """A self-consistent lie truncates the XML: invalid, not a 500."""
        data = _with_lying_directory(
            _workbook_with_sheet(50_000), 4096, matching_crc=True
        )
        with pytest.raises(InvalidSpreadsheetError):
            read_xlsx(data)

    def test_overstated_size_is_refused_by_the_sum(self) -> None:
        """A claim larger than the limit is refused on the claim alone."""
        data = _with_lying_directory(
            _workbook_with_sheet(10),
            DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES + 1,
            matching_crc=False,
        )
        with pytest.raises(SpreadsheetTooLargeError) as caught:
            read_xlsx(data)
        assert caught.value.details["limit"] == "uncompressed_bytes"


class TestMaxRows:
    """Past ``max_rows`` the reader stops and raises — it never truncates."""

    def test_one_row_over_raises(self) -> None:
        """Eleven data rows with ``max_rows=10`` raise on the eleventh."""
        with pytest.raises(SpreadsheetTooLargeError) as caught:
            read_xlsx(_workbook_with_sheet(11), max_rows=10)
        assert caught.value.details == {
            "limit": "rows",
            "max": 10,
            "actual": 11,
            "sheet": "Dados",
            "row": 12,
        }

    def test_exactly_the_limit_passes(self) -> None:
        """Ten data rows with ``max_rows=10`` are all returned."""
        assert len(read_xlsx(_workbook_with_sheet(10), max_rows=10)) == 10

    def test_blank_rows_do_not_count(self) -> None:
        """Only the rows the reader returns count toward the limit."""
        workbook = new_workbook("Dados")
        sheet = workbook["Dados"]
        sheet.append(["nome"])
        sheet.append(["a"])
        sheet.append([None])
        sheet.append([None])
        sheet.append(["b"])
        rows = read_xlsx(workbook_to_bytes(workbook), max_rows=2)
        assert rows == [{"nome": "a"}, {"nome": "b"}]

    def test_every_reader_enforces_it(self) -> None:
        """``read_xlsx_as`` and ``read_xlsx_sheets`` (per tab) raise too."""

        class Item(BaseModel):
            """One row."""

            nome: str
            valor: int

        data = _workbook_with_sheet(11)
        with pytest.raises(SpreadsheetTooLargeError):
            read_xlsx_as(data, Item, max_rows=10)
        with pytest.raises(SpreadsheetTooLargeError) as caught:
            read_xlsx_sheets(data, max_rows=10)
        assert caught.value.details["sheet"] == "Dados"

    def test_default_and_none(self) -> None:
        """The default refuses one row past it; ``None`` reads them all.

        The generated rows are identical and carry no ``r`` attribute, so
        they compress ~290 times; the ratio check is switched off to reach
        the row limit.
        """
        data = _workbook_with_sheet(DEFAULT_XLSX_MAX_ROWS + 1)
        with pytest.raises(SpreadsheetTooLargeError) as caught:
            read_xlsx(data, max_compression_ratio=None)
        assert caught.value.details["max"] == DEFAULT_XLSX_MAX_ROWS
        rows = read_xlsx(data, max_rows=None, max_compression_ratio=None)
        assert len(rows) == DEFAULT_XLSX_MAX_ROWS + 1

    def test_records_past_the_limit_are_never_read(self) -> None:
        """The rows are consumed lazily: nothing after the refused one."""
        consumed: list[int] = []

        def records() -> Iterator[list[str]]:
            """Yield rows forever, recording how many were taken.

            Yields:
                list[str]: One row.
            """
            number = 0
            while True:
                number += 1
                consumed.append(number)
                yield [f"r{number}"]

        with pytest.raises(SpreadsheetTooLargeError):
            _number_rows(["nome"], records(), "", max_rows=3, sheet="Dados")
        assert consumed == [1, 2, 3, 4]


class TestLimitValidation:
    """A limit of zero or less is a programming error."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_rows": 0},
            {"max_uncompressed_bytes": -1},
            {"max_compression_ratio": 0.0},
        ],
    )
    def test_non_positive_limit_raises(self, kwargs: dict[str, Any]) -> None:
        """``ValueError`` names the parameter."""
        with pytest.raises(ValueError, match=next(iter(kwargs))):
            read_xlsx(_workbook_with_sheet(1), **kwargs)


def _export(
    body: bytes,
    *,
    chunked: bool = False,
    served: list[int] | None = None,
    media_type: str = XLSX_MEDIA_TYPE,
    declared: int | None = None,
) -> httpx.MockTransport:
    """Answer every request with an export.

    Args:
        body (bytes): The workbook, or the CSV.
        chunked (bool): Stream the body in 64 KiB chunks with no
            ``Content-Length``, as a server that does not announce the size.
        served (list[int] | None): Collects the size of every chunk sent.
        media_type (str): The ``Content-Type`` of the answer.
        declared (int | None): A ``Content-Length`` to announce on a
            chunked body, so a test can tell whether any chunk was pulled.

    Returns:
        httpx.MockTransport: The fake transport.
    """

    async def stream() -> AsyncIterator[bytes]:
        """Yield the body in chunks, recording each.

        Yields:
            bytes: A chunk of the body.
        """
        for start in range(0, len(body), 64 * 1024):
            chunk = body[start : start + 64 * 1024]
            if served is not None:
                served.append(len(chunk))
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        """Return the export.

        Args:
            request (httpx.Request): The incoming request (unused).

        Returns:
            httpx.Response: ``200`` with ``media_type``.
        """
        headers = {"content-type": media_type}
        if declared is not None:
            headers["content-length"] = str(declared)
        if chunked:
            return httpx.Response(200, headers=headers, content=stream())
        return httpx.Response(200, headers=headers, content=body)

    return httpx.MockTransport(handler)


class TestGoogleDownloadLimit:
    """The export body is streamed and cut off past ``max_bytes``."""

    async def test_content_length_over_the_limit(self) -> None:
        """An announced size over the limit is refused."""
        body = b"x" * 4096
        async with httpx.AsyncClient(transport=_export(body)) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await download_google_sheet_xlsx(
                    SHARE_LINK, client=client, max_bytes=1024
                )
        details = caught.value.details
        assert details["limit"] == "download_bytes"
        assert details["max"] == 1024
        assert details["actual"] == 4096

    async def test_unannounced_body_stops_one_chunk_past(self) -> None:
        """Without ``Content-Length`` the transfer stops past the limit."""
        body = b"x" * (1024 * 1024)
        served: list[int] = []
        transport = _export(body, chunked=True, served=served)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await download_google_sheet_xlsx(
                    SHARE_LINK, client=client, max_bytes=100 * 1024
                )
        assert caught.value.details["actual"] <= 100 * 1024 + 64 * 1024
        assert sum(served) < len(body)

    async def test_under_the_limit_returns_the_body(self) -> None:
        """A body at the limit comes back whole, streamed or not."""
        body = _workbook_with_sheet(10)
        for chunked in (False, True):
            transport = _export(body, chunked=chunked)
            async with httpx.AsyncClient(transport=transport) as client:
                data = await download_google_sheet_xlsx(
                    SHARE_LINK, client=client, max_bytes=len(body)
                )
            assert data == body

    async def test_default_and_none(self) -> None:
        """The default admits a normal export; ``None`` disables the limit."""
        assert DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES == 32 * MIB
        body = _workbook_with_sheet(10)
        async with httpx.AsyncClient(transport=_export(body)) as client:
            assert await download_google_sheet_xlsx(SHARE_LINK, client=client) == body
            assert (
                await download_google_sheet_xlsx(
                    SHARE_LINK, client=client, max_bytes=None
                )
                == body
            )

    async def test_zero_limit_raises_before_the_request(self) -> None:
        """A non-positive limit is rejected without touching the network."""
        with pytest.raises(ValueError, match="max_bytes"):
            await download_google_sheet_xlsx(SHARE_LINK, max_bytes=0)


class TestReadGoogleSheetXlsxLimits:
    """``read_google_sheet_xlsx`` forwards every limit."""

    async def test_download_limit(self) -> None:
        """``max_bytes`` reaches the download."""
        body = _workbook_with_sheet(10)
        async with httpx.AsyncClient(transport=_export(body)) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet_xlsx(SHARE_LINK, client=client, max_bytes=100)
        assert caught.value.details["limit"] == "download_bytes"

    async def test_row_limit_is_not_an_access_error(self) -> None:
        """A sheet over ``max_rows`` raises the size error, not a ``502``."""
        body = _workbook_with_sheet(11)
        async with httpx.AsyncClient(transport=_export(body)) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet_xlsx(SHARE_LINK, client=client, max_rows=10)
        assert not isinstance(caught.value, GoogleSheetAccessError)
        assert caught.value.details["limit"] == "rows"

    async def test_archive_limits(self) -> None:
        """The decompressed size and the ratio reach the reader."""
        body = _workbook_with_sheet(10, padding=2 * MIB)
        async with httpx.AsyncClient(transport=_export(body)) as client:
            with pytest.raises(SpreadsheetTooLargeError) as ratio:
                await read_google_sheet_xlsx(SHARE_LINK, client=client)
            with pytest.raises(SpreadsheetTooLargeError) as size:
                await read_google_sheet_xlsx(
                    SHARE_LINK,
                    client=client,
                    max_compression_ratio=None,
                    max_uncompressed_bytes=MIB,
                )
            sheets = await read_google_sheet_xlsx(
                SHARE_LINK, client=client, max_compression_ratio=None
            )
        assert ratio.value.details["limit"] == "compression_ratio"
        assert size.value.details["limit"] == "uncompressed_bytes"
        assert len(sheets["Dados"]) == 10

    async def test_invalid_limit_raises_before_the_request(self) -> None:
        """A bad reader limit is caught before the download."""
        with pytest.raises(ValueError, match="max_rows"):
            await read_google_sheet_xlsx(SHARE_LINK, max_rows=-5)


CSV_MEDIA_TYPE: Final[str] = "text/csv; charset=utf-8"


class Item(BaseModel):
    """A row of the CSV the tests serve."""

    nome: str
    valor: int


def _csv(rows: int, *, blank_every: int = 0) -> bytes:
    """Build a CSV export with ``rows`` data rows.

    Args:
        rows (int): Data rows after the header.
        blank_every (int): Insert a blank record (``,``) after every this
            many data rows; ``0`` inserts none.

    Returns:
        bytes: The CSV, with ``\\r\\n`` line ends like the Google export.
    """
    lines: list[str] = ["nome,valor"]
    for number in range(rows):
        lines.append(f"item {number},{number}")
        if blank_every and (number + 1) % blank_every == 0:
            lines.append(",")
    return ("\r\n".join(lines) + "\r\n").encode()


def _refuse_requests() -> httpx.MockTransport:
    """Fail the test if any request is sent.

    Returns:
        httpx.MockTransport: A transport whose handler always fails.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Fail on contact.

        Args:
            request (httpx.Request): The request that should not exist.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError(f"unexpected request to {request.url}")

    return httpx.MockTransport(handler)


class TestReadGoogleSheetCsvDownloadLimit:
    """The CSV export is streamed and cut off past ``max_bytes``."""

    async def test_content_length_over_the_limit_reads_no_body(self) -> None:
        """An announced size over the limit is refused before any chunk."""
        body = _csv(1000)
        served: list[int] = []
        transport = _export(
            body,
            chunked=True,
            served=served,
            media_type=CSV_MEDIA_TYPE,
            declared=len(body),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet(SHARE_LINK, client=client, max_bytes=1024)
        assert served == []
        details = caught.value.details
        assert details["limit"] == "download_bytes"
        assert details["max"] == 1024
        assert details["actual"] == len(body)
        assert details["export_url"].endswith("/export?format=csv")
        assert caught.value.status_code == 413
        assert isinstance(caught.value, FileTooLargeException)

    async def test_unannounced_body_stops_one_chunk_past(self) -> None:
        """Without ``Content-Length`` the transfer stops past the limit."""
        body = _csv(100_000)
        served: list[int] = []
        transport = _export(
            body, chunked=True, served=served, media_type=CSV_MEDIA_TYPE
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet_as(
                    SHARE_LINK, Item, client=client, max_bytes=100 * 1024
                )
        assert caught.value.details["limit"] == "download_bytes"
        assert caught.value.details["actual"] <= 100 * 1024 + 64 * 1024
        assert sum(served) < len(body)

    async def test_body_at_the_limit_is_read(self) -> None:
        """A body exactly ``max_bytes`` long comes back whole."""
        body = _csv(10)
        for chunked in (False, True):
            transport = _export(body, chunked=chunked, media_type=CSV_MEDIA_TYPE)
            async with httpx.AsyncClient(transport=transport) as client:
                rows = await read_google_sheet(
                    SHARE_LINK, client=client, max_bytes=len(body)
                )
            assert len(rows) == 10


class TestReadGoogleSheetCsvRowLimit:
    """``max_rows`` is counted while the CSV is parsed."""

    async def test_one_row_over_raises(self) -> None:
        """Row ``max_rows + 1`` is refused with its sheet row number."""
        transport = _export(_csv(11), media_type=CSV_MEDIA_TYPE)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet(SHARE_LINK, client=client, max_rows=10)
            rows = await read_google_sheet(SHARE_LINK, client=client, max_rows=11)
        assert caught.value.details == {
            "limit": "rows",
            "max": 10,
            "actual": 11,
            "sheet": None,
            "row": 12,
        }
        assert caught.value.message.startswith("The sheet has more than 10")
        assert len(rows) == 11

    async def test_blank_rows_do_not_count(self) -> None:
        """Blank records are skipped, not counted, but keep the numbering."""
        transport = _export(_csv(11, blank_every=2), media_type=CSV_MEDIA_TYPE)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet(SHARE_LINK, client=client, max_rows=10)
            rows = await read_google_sheet(SHARE_LINK, client=client, max_rows=11)
        assert caught.value.details["actual"] == 11
        assert caught.value.details["row"] == 17
        assert len(rows) == 11

    async def test_records_past_the_limit_are_never_decoded(self) -> None:
        """The parse stops at the limit: bytes after it are never decoded.

        The tail is not UTF-8, so decoding the whole body first would raise
        ``UnicodeDecodeError`` instead of the row limit.
        """
        body = _csv(20_000) + b"\xff\xfe broken,1\r\n"
        transport = _export(body, media_type=CSV_MEDIA_TYPE)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet(SHARE_LINK, client=client, max_rows=10)
            with pytest.raises(UnicodeDecodeError):
                await read_google_sheet(SHARE_LINK, client=client, max_rows=None)
        assert caught.value.details["row"] == 12

    async def test_limit_is_checked_before_validation(self) -> None:
        """``read_google_sheet_as`` refuses the size before any row error."""
        body = b"nome,valor\r\n" + b"item,not-a-number\r\n" * 11
        transport = _export(body, media_type=CSV_MEDIA_TYPE)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet_as(SHARE_LINK, Item, client=client, max_rows=10)
        assert caught.value.details["limit"] == "rows"


class TestReadGoogleSheetCsvDefaults:
    """The defaults admit a real sheet, and every limit can be switched off."""

    def test_default_download_limit(self) -> None:
        """10 MiB, below the ``.xlsx`` download limit."""
        assert DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES == 10 * MIB
        assert DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES < (
            DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES
        )

    async def test_defaults_admit_the_measured_public_tab(self) -> None:
        """A tab larger than the largest public one measured passes.

        The real tab (16-tab public sheet, measured once over the network)
        is 18 577 bytes with 1 004 data rows; this stand-in is larger in
        both, so the suite stays offline.
        """
        body = _csv(2_000)
        transport = _export(body, media_type=CSV_MEDIA_TYPE)
        async with httpx.AsyncClient(transport=transport) as client:
            rows = await read_google_sheet(SHARE_LINK, client=client)
            items = await read_google_sheet_as(SHARE_LINK, Item, client=client)
        assert len(body) > 18_577
        assert len(rows) == len(items) == 2_000

    async def test_default_row_limit_is_the_xlsx_one(self) -> None:
        """The default refuses row ``DEFAULT_XLSX_MAX_ROWS + 1``."""
        body = _csv(DEFAULT_XLSX_MAX_ROWS + 1)
        transport = _export(body, media_type=CSV_MEDIA_TYPE)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet(SHARE_LINK, client=client)
            rows = await read_google_sheet(SHARE_LINK, client=client, max_rows=None)
        assert caught.value.details["max"] == DEFAULT_XLSX_MAX_ROWS
        assert len(rows) == DEFAULT_XLSX_MAX_ROWS + 1

    async def test_none_disables_the_download_limit(self) -> None:
        """``max_bytes=None`` reads a body past the default."""
        body = _csv(10) + (b"x," + b"y" * 1_000 + b"\r\n") * 11_000
        assert len(body) > DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES
        transport = _export(body, media_type=CSV_MEDIA_TYPE)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SpreadsheetTooLargeError) as caught:
                await read_google_sheet(SHARE_LINK, client=client)
            rows = await read_google_sheet(SHARE_LINK, client=client, max_bytes=None)
        assert caught.value.details["limit"] == "download_bytes"
        assert len(rows) == 11_010

    @pytest.mark.parametrize(
        "kwargs",
        [{"max_bytes": 0}, {"max_bytes": -1}, {"max_rows": 0}, {"max_rows": -5}],
    )
    async def test_non_positive_limit_raises_before_the_request(
        self, kwargs: dict[str, Any]
    ) -> None:
        """``ValueError`` names the parameter, and nothing is sent."""
        async with httpx.AsyncClient(transport=_refuse_requests()) as client:
            with pytest.raises(ValueError, match=next(iter(kwargs))):
                await read_google_sheet(SHARE_LINK, client=client, **kwargs)
            with pytest.raises(ValueError, match=next(iter(kwargs))):
                await read_google_sheet_as(SHARE_LINK, Item, client=client, **kwargs)
