"""Read a publicly shared Google Sheet as rows, with no API key.

A sheet shared as *Anyone with the link* answers on its ``/export``
endpoint with a tab rendered as CSV, or with the whole workbook as
``.xlsx``. That is the whole mechanism: no OAuth, no service account, no
``gspread``. What this module owns is the part every hand-written version
gets slightly wrong:

* **The link the user pastes is not the export URL.** It ends in
  ``/edit``, carries ``?usp=sharing``, and names the tab in the
  ``#gid=`` *fragment* — which the browser never sends to the server, so
  it has to be parsed out before the request is built.
  :func:`google_sheet_export_url` turns any of those shapes into the
  export URL.
* **The export answers with a redirect.** ``docs.google.com`` replies
  ``307`` to a ``*.googleusercontent.com`` host; an ``httpx`` client
  without ``follow_redirects`` hands back that ``307`` as the response.
* **A sheet that cannot be read does not answer with an error status you
  can rely on.** A non-existent ID answers ``404`` with an HTML page, and a
  ``gid`` that names no tab answers ``400`` with HTML — in both formats.
  Anything that is not a successful response of the expected media type
  raises :class:`GoogleSheetAccessError` instead of being parsed as data.

Two paths share those guarantees:

* **CSV** — :func:`read_google_sheet` / :func:`read_google_sheet_as`: one
  tab per call, picked by ``gid``, every cell a ``str`` as the export
  formats it. Needs nothing beyond the base install (``httpx`` and the
  standard ``csv`` module).
* **xlsx** — :func:`read_google_sheet_xlsx` /
  :func:`download_google_sheet_xlsx`: the whole workbook in one request,
  tabs by name, cells typed. Parsed by
  :mod:`~tempest_fastapi_sdk.spreadsheet.reader`, so it needs the
  ``[spreadsheet]`` extra — imported at call time, so the CSV path keeps
  importing without it.

    from pydantic import BaseModel

    from tempest_fastapi_sdk.spreadsheet import read_google_sheet_as


    class Product(BaseModel):
        item: str
        valor: int | None = None
        tamanho: str


    async def load() -> list[Product]:
        return await read_google_sheet_as(
            "https://docs.google.com/spreadsheets/d/<id>/edit?usp=sharing",
            Product,
        )
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Final, Literal, TypeVar
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import BaseModel

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.spreadsheet.reader import (
    DEFAULT_XLSX_MAX_COMPRESSION_RATIO,
    DEFAULT_XLSX_MAX_ROWS,
    DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES,
    InvalidSpreadsheetError,
    SpreadsheetRowError,
    SpreadsheetTooLargeError,
    XlsxCellValue,
    _check_limit,
    _number_rows,
    _require_openpyxl,
    _validate_rows,
    read_xlsx_sheets,
)
from tempest_fastapi_sdk.utils.media_types import XLSX_MEDIA_TYPE

ModelT = TypeVar("ModelT", bound=BaseModel)

_GOOGLE_SHEETS_HOST: Final[str] = "docs.google.com"
_CSV_MEDIA_TYPE: Final[str] = "text/csv"

_SHEET_PATH: Final[re.Pattern[str]] = re.compile(
    r"^/spreadsheets(?:/u/\d+)?/d/(?P<sheet_id>[A-Za-z0-9_-]+)(?:/|$)"
)
_SHEET_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")
_GID: Final[re.Pattern[str]] = re.compile(r"^\d+$")
_PUBLISHED_SEGMENT: Final[str] = "e"

DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES: Final[int] = 32 * 1024 * 1024
"""Bytes the ``.xlsx`` export may answer with before the download stops (32 MiB).

The body is streamed and counted; past the limit the transfer is closed and
:class:`~tempest_fastapi_sdk.spreadsheet.reader.SpreadsheetTooLargeError`
is raised, so at most this much is ever held.

The arithmetic: a workbook the reader accepts decompresses to at most
:data:`~tempest_fastapi_sdk.spreadsheet.reader.DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`
(100 MiB). Measured workbooks compress 7.2 to 8.1 times, so one at that
limit downloads as about 14 MiB; 32 MiB still admits it when it compresses
only 3.2 times, and anything larger would be refused by the decompressed
limit anyway. The 16-tab public sheet measured downloads as 785 152 bytes.
"""


class GoogleSheetAccessError(AppException):
    """The export URL did not answer with the requested document.

    Covers every way a readable sheet fails to come back: a link to a sheet
    that does not exist, a ``gid`` that names no tab, a sheet not shared
    as *Anyone with the link*, an upstream outage. Google answers those
    with an HTML page rather than a distinct status, so the class does not
    try to tell them apart — ``details`` carries the ``status_code`` and
    ``content_type`` that were actually received, for whoever debugs it.

    The status is ``502``: from the point of view of the API that calls
    the reader, an upstream refused to hand over the data.
    """

    message: str = (
        "The Google Sheet could not be downloaded. Check the link and share "
        "the sheet as 'Anyone with the link'."
    )
    code: str = "GOOGLE_SHEET_UNAVAILABLE"
    status_code: int = 502


class GoogleSheetRowError(SpreadsheetRowError):
    """One row of the sheet did not validate against the target model.

    ``details["row"]`` is the row number as the spreadsheet shows it — the
    header is row 1, so the first data row is row 2 — and
    ``details["errors"]`` is Pydantic's error list for that row, without
    the ``url`` and ``ctx`` keys (``ctx`` may hold the raised exception,
    which does not serialize to JSON).

    A subclass of
    :class:`~tempest_fastapi_sdk.spreadsheet.reader.SpreadsheetRowError`,
    so ``except SpreadsheetRowError`` covers the CSV and the ``.xlsx``
    readers alike.
    """

    message: str = "A row of the Google Sheet failed validation."
    code: str = "GOOGLE_SHEET_ROW_INVALID"
    status_code: int = 422


def _gid_from(url_query: str, fragment: str) -> str | None:
    """Pick the tab id out of a link's query string or fragment.

    The query string wins when both carry one; Google writes the same
    value in both when it builds the link of a tab.

    Args:
        url_query (str): The link's query string, without ``?``.
        fragment (str): The link's fragment, without ``#``.

    Returns:
        str | None: The ``gid``, or ``None`` when the link names no tab.

    Raises:
        ValueError: If a ``gid`` is present but is not a number.
    """
    for source in (url_query, fragment):
        values: list[str] = parse_qs(source).get("gid", [])
        if values:
            gid: str = values[0]
            if not _GID.match(gid):
                raise ValueError(f"gid must be a number, got {gid!r}.")
            return gid
    return None


def _parse_sheet_link(url: str) -> tuple[str, str | None]:
    """Split a shared link into the spreadsheet ID and the tab's ``gid``.

    Args:
        url (str): The shared link, or the bare spreadsheet ID.

    Returns:
        tuple[str, str | None]: The ID, and the ``gid`` when the link
        names a tab.

    Raises:
        ValueError: If ``url`` is not a Google Sheets link, is a
            *Publish to the web* link, or carries a non-numeric ``gid``.
    """
    candidate: str = url.strip()
    if _SHEET_ID.match(candidate):
        return candidate, None
    if candidate.startswith(f"{_GOOGLE_SHEETS_HOST}/"):
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    match: re.Match[str] | None = _SHEET_PATH.match(parts.path)
    if (
        parts.scheme not in ("http", "https")
        or parts.hostname != _GOOGLE_SHEETS_HOST
        or match is None
    ):
        raise ValueError(f"Not a Google Sheets link: {url!r}.")
    sheet_id: str = match.group("sheet_id")
    if sheet_id == _PUBLISHED_SEGMENT:
        raise ValueError(
            "A 'Publish to the web' link (/d/e/...) has no export "
            "endpoint; use the sheet's share link instead."
        )
    return sheet_id, _gid_from(parts.query, parts.fragment)


def _export_url(sheet_id: str, export_format: str, gid: str | None) -> str:
    """Build the export URL from its parts.

    Args:
        sheet_id (str): The spreadsheet ID.
        export_format (str): ``csv`` or ``xlsx``.
        gid (str | None): The tab, or ``None`` to name none.

    Returns:
        str: The export URL.
    """
    export_url: str = (
        f"https://{_GOOGLE_SHEETS_HOST}/spreadsheets/d/{sheet_id}"
        f"/export?format={export_format}"
    )
    return export_url if gid is None else f"{export_url}&gid={gid}"


def google_sheet_export_url(
    url: str,
    *,
    export_format: Literal["csv", "xlsx"] = "csv",
) -> str:
    """Build the export URL of a Google Sheet from the link a user shares.

    Accepts the shapes a link takes in practice: the ``/edit`` link with
    ``?usp=sharing``, with the tab in ``?gid=`` or in the ``#gid=``
    fragment, without ``/edit``, under ``/u/<n>/`` (multi-account), with
    or without the scheme — or the bare spreadsheet ID. The tab is kept
    when the link names one; without a ``gid`` the export URL leaves the
    choice of tab to Google.

    Args:
        url (str): The shared link, or the bare spreadsheet ID.
        export_format (Literal["csv", "xlsx"]): Format the export answers
            with. ``csv`` exports one tab. ``xlsx`` exports the whole
            workbook — unless a ``gid`` is kept, which narrows it to that
            one tab (measured); :func:`download_google_sheet_xlsx` drops
            it for that reason.

    Returns:
        str: ``https://docs.google.com/spreadsheets/d/<id>/export?format=<f>``,
        followed by ``&gid=<gid>`` when the link names a tab.

    Raises:
        ValueError: If ``url`` is not a Google Sheets link, is a
            *Publish to the web* link (``/d/e/...``, which has no
            ``/export`` endpoint), carries a non-numeric ``gid``, or
            ``export_format`` is not one of the two supported values.
    """
    if export_format not in ("csv", "xlsx"):
        raise ValueError(
            f"export_format must be 'csv' or 'xlsx', got {export_format!r}."
        )
    sheet_id, gid = _parse_sheet_link(url)
    return _export_url(sheet_id, export_format, gid)


def _parse_csv(text: str) -> list[tuple[int, dict[str, str]]]:
    """Split a CSV export into numbered rows keyed by the header.

    The first record is the header; the rest follow the rules of
    :func:`~tempest_fastapi_sdk.spreadsheet.reader._number_rows` — the
    same ones the ``.xlsx`` reader applies — with ``""`` as the padding.

    Args:
        text (str): The decoded CSV body.

    Returns:
        list[tuple[int, dict[str, str]]]: ``(row_number, row)`` pairs, the
        header being row 1. Empty when the export has no data row.
    """
    records: list[list[str]] = list(csv.reader(io.StringIO(text, newline="")))
    if not records:
        return []
    return _number_rows(records[0], records[1:], "")


async def _receive(
    client: httpx.AsyncClient,
    export_url: str,
    media_type: str,
    max_bytes: int | None,
) -> bytes:
    """Stream an export's body, refusing it once it passes ``max_bytes``.

    The status and the media type are checked before any of the body is
    read. A ``Content-Length`` over the limit is refused without reading
    the body; otherwise the decoded body is counted chunk by chunk, so a
    response that omits or understates the header is still cut off one
    chunk past the limit.

    Args:
        client (httpx.AsyncClient): The client to send the request through.
        export_url (str): The export URL.
        media_type (str): The media type a successful export carries.
        max_bytes (int | None): Most bytes to accept; ``None`` accepts any.

    Returns:
        bytes: The response body.

    Raises:
        GoogleSheetAccessError: If the export does not answer with a
            successful response of ``media_type``.
        SpreadsheetTooLargeError: If the body passes ``max_bytes``, with
            ``details["limit"] == "download_bytes"``.
        httpx.HTTPError: If the request itself fails.
    """
    async with client.stream("GET", export_url, follow_redirects=True) as response:
        content_type: str = response.headers.get("content-type", "")
        received: str = content_type.split(";", 1)[0].strip().lower()
        if not response.is_success or received != media_type:
            raise GoogleSheetAccessError(
                details={
                    "export_url": export_url,
                    "status_code": response.status_code,
                    "content_type": content_type,
                },
            )
        if max_bytes is None:
            return await response.aread()
        declared: str = response.headers.get("content-length", "")
        size: int = int(declared) if declared.isdecimal() else 0
        body: bytearray = bytearray()
        if size <= max_bytes:
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    break
            size = len(body)
        if size > max_bytes:
            raise SpreadsheetTooLargeError(
                message=(f"The export passed the download limit of {max_bytes} bytes."),
                details={
                    "limit": "download_bytes",
                    "max": max_bytes,
                    "actual": size,
                    "export_url": export_url,
                },
            )
        return bytes(body)


async def _download(
    export_url: str,
    media_type: str,
    client: httpx.AsyncClient | None,
    timeout: float,
    max_bytes: int | None = None,
) -> bytes:
    """Fetch an export URL and insist on the media type it must answer with.

    Args:
        export_url (str): The export URL.
        media_type (str): The media type a successful export carries.
        client (httpx.AsyncClient | None): Client to send the request
            through. Left open. ``None`` creates one, closed on return.
        timeout (float): Seconds the created client waits. Ignored when a
            client is injected — its own timeout applies.
        max_bytes (int | None): Most bytes of body to accept; ``None``
            accepts any.

    Returns:
        bytes: The response body.

    Raises:
        GoogleSheetAccessError: If the export does not answer with a
            successful response of ``media_type``.
        SpreadsheetTooLargeError: If the body passes ``max_bytes``.
        httpx.HTTPError: If the request itself fails (timeout, DNS,
            connection refused).
    """
    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as owned:
            return await _receive(owned, export_url, media_type, max_bytes)
    return await _receive(client, export_url, media_type, max_bytes)


async def _download_csv(
    url: str,
    client: httpx.AsyncClient | None,
    timeout: float,
) -> str:
    """Fetch the CSV export of the sheet a link points at.

    Args:
        url (str): Anything :func:`google_sheet_export_url` accepts.
        client (httpx.AsyncClient | None): Client to reuse; left open.
        timeout (float): Seconds the created client waits.

    Returns:
        str: The CSV body, decoded as UTF-8 (a leading BOM is dropped).

    Raises:
        ValueError: If ``url`` is not a Google Sheets link.
        GoogleSheetAccessError: If the export does not answer with a
            successful ``text/csv`` response.
        httpx.HTTPError: If the request itself fails.
    """
    export_url: str = google_sheet_export_url(url, export_format="csv")
    body: bytes = await _download(export_url, _CSV_MEDIA_TYPE, client, timeout)
    return body.decode("utf-8-sig")


async def read_google_sheet(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
) -> list[dict[str, str]]:
    """Read one tab of a publicly shared Google Sheet as a list of rows.

    Each row is a ``dict`` keyed by the header row, every value a ``str``
    exactly as the CSV export renders it — an empty cell is ``""``. Blank
    rows are skipped. A sheet with only a header, or nothing at all,
    returns ``[]``.

    Args:
        url (str): The shared link (any shape
            :func:`google_sheet_export_url` accepts) or the bare ID. The
            tab is the one its ``gid`` names.
        client (httpx.AsyncClient | None): Client to reuse. The reader
            never closes an injected client. ``None`` creates one for the
            call and closes it.
        timeout (float): Seconds the created client waits for the export.
            Ignored when ``client`` is given.

    Returns:
        list[dict[str, str]]: The data rows, in sheet order.

    Raises:
        ValueError: If ``url`` is not a Google Sheets link.
        GoogleSheetAccessError: If the export does not answer with CSV —
            the sheet does not exist, is not shared as *Anyone with the
            link*, or the ``gid`` names no tab.
        httpx.HTTPError: If the request itself fails.
    """
    text: str = await _download_csv(url, client, timeout)
    return [row for _, row in _parse_csv(text)]


def _google_row_error(row_number: int, errors: list[dict[str, Any]]) -> AppException:
    """Build the error for a CSV row that failed validation.

    Args:
        row_number (int): The row's number in the sheet.
        errors (list[dict[str, Any]]): Pydantic's errors for the row.

    Returns:
        AppException: The :class:`GoogleSheetRowError`.
    """
    return GoogleSheetRowError(
        message=f"Row {row_number} of the Google Sheet failed validation.",
        message_params={"row": row_number},
        details={"row": row_number, "errors": errors},
    )


async def read_google_sheet_as(
    url: str,
    schema: type[ModelT],
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
    omit_blank: bool = True,
) -> list[ModelT]:
    """Read one tab of a public Google Sheet and validate each row.

    The header names are the keys handed to ``schema.model_validate``, so
    a column maps to the field of the same name (use
    ``validation_alias`` for a header that is not a valid identifier).

    Args:
        url (str): The shared link or the bare ID; see
            :func:`read_google_sheet`.
        schema (type[ModelT]): The Pydantic model each row validates into.
        client (httpx.AsyncClient | None): Client to reuse; never closed
            by the reader. ``None`` creates and closes one.
        timeout (float): Seconds the created client waits. Ignored when
            ``client`` is given.
        omit_blank (bool): Drop empty cells from the row before
            validating, so a blank cell means *absent*: the field falls
            back to its default, and a required field reports ``missing``.
            ``False`` passes the empty string through.

    Returns:
        list[ModelT]: One instance per data row, in sheet order. ``[]``
        when the sheet has no data row.

    Raises:
        ValueError: If ``url`` is not a Google Sheets link.
        GoogleSheetAccessError: If the export does not answer with CSV.
        GoogleSheetRowError: On the first row that fails validation;
            ``details["row"]`` is its number in the sheet (header = 1).
        httpx.HTTPError: If the request itself fails.
    """
    text: str = await _download_csv(url, client, timeout)
    return _validate_rows(
        _parse_csv(text),
        schema,
        omit_blank=omit_blank,
        row_error=_google_row_error,
    )


async def download_google_sheet_xlsx(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
    max_bytes: int | None = DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES,
) -> bytes:
    """Download a public Google Sheet as one ``.xlsx`` workbook.

    Every tab comes in the one request. The link's ``gid`` is dropped on
    purpose: with it, the ``.xlsx`` export narrows to that single tab
    (measured). Hand the bytes to
    :func:`~tempest_fastapi_sdk.spreadsheet.reader.read_xlsx`,
    :func:`~tempest_fastapi_sdk.spreadsheet.reader.read_xlsx_as` or
    :func:`~tempest_fastapi_sdk.spreadsheet.reader.read_xlsx_sheets` to
    read as many tabs as needed without downloading again. Needs no extra
    by itself; reading the bytes does.

    Args:
        url (str): The shared link (any shape
            :func:`google_sheet_export_url` accepts) or the bare ID.
        client (httpx.AsyncClient | None): Client to reuse. The reader
            never closes an injected client. ``None`` creates one for the
            call and closes it.
        timeout (float): Seconds the created client waits for the export.
            Ignored when ``client`` is given.
        max_bytes (int | None): Most bytes the export may answer with; the
            body is streamed and the transfer stops past it. Defaults to
            :data:`DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES`; ``None``
            removes the limit.

    Returns:
        bytes: The ``.xlsx`` file.

    Raises:
        ValueError: If ``url`` is not a Google Sheets link, or
            ``max_bytes`` is zero or negative.
        GoogleSheetAccessError: If the export does not answer with a
            successful ``.xlsx`` response — the sheet does not exist or is
            not shared as *Anyone with the link*.
        SpreadsheetTooLargeError: If the body passes ``max_bytes``.
        httpx.HTTPError: If the request itself fails.
    """
    _check_limit("max_bytes", max_bytes)
    sheet_id, _ = _parse_sheet_link(url)
    export_url: str = _export_url(sheet_id, "xlsx", None)
    return await _download(export_url, XLSX_MEDIA_TYPE, client, timeout, max_bytes)


async def read_google_sheet_xlsx(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
    max_bytes: int | None = DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES,
    max_rows: int | None = DEFAULT_XLSX_MAX_ROWS,
    max_uncompressed_bytes: int | None = DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: float | None = DEFAULT_XLSX_MAX_COMPRESSION_RATIO,
) -> dict[str, list[dict[str, XlsxCellValue]]]:
    """Read every tab of a public Google Sheet in one request, by tab name.

    Downloads with :func:`download_google_sheet_xlsx` and parses with
    :func:`~tempest_fastapi_sdk.spreadsheet.reader.read_xlsx_sheets`: each
    row is a ``dict`` keyed by the tab's header row, and each cell keeps
    its type — number, ``datetime``, ``bool`` or ``str``; an empty cell is
    ``None``. A formula reads as the value Google computed.

    Args:
        url (str): The shared link or the bare ID. A ``gid`` in it is
            ignored: the whole workbook is read.
        client (httpx.AsyncClient | None): Client to reuse; never closed
            by the reader. ``None`` creates and closes one.
        timeout (float): Seconds the created client waits. Ignored when
            ``client`` is given.
        max_bytes (int | None): Most bytes the export may answer with.
            Defaults to :data:`DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES`;
            ``None`` removes the limit.
        max_rows (int | None): Most data rows each tab may hold. Defaults
            to :data:`~tempest_fastapi_sdk.spreadsheet.reader.DEFAULT_XLSX_MAX_ROWS`;
            ``None`` removes the limit.
        max_uncompressed_bytes (int | None): Most bytes the workbook may
            decompress to. Defaults to
            :data:`~tempest_fastapi_sdk.spreadsheet.reader.DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`;
            ``None`` removes the limit.
        max_compression_ratio (float | None): Highest decompressed-to-
            compressed ratio for a part of at least 1 MiB. Defaults to
            :data:`~tempest_fastapi_sdk.spreadsheet.reader.DEFAULT_XLSX_MAX_COMPRESSION_RATIO`;
            ``None`` removes the limit.

    Returns:
        dict[str, list[dict[str, XlsxCellValue]]]: The rows of each tab, in
        tab order. Tab names are kept verbatim, trailing spaces included.

    Raises:
        ImportError: When the ``[spreadsheet]`` extra is not installed —
            checked before the download.
        ValueError: If ``url`` is not a Google Sheets link, or a limit is
            zero or negative — both checked before the download.
        GoogleSheetAccessError: If the export does not answer with an
            ``.xlsx`` workbook, or the body it answers with does not open
            as one.
        SpreadsheetTooLargeError: If the download or the workbook passes a
            limit.
        httpx.HTTPError: If the request itself fails.
    """
    _require_openpyxl()
    _check_limit("max_rows", max_rows)
    _check_limit("max_uncompressed_bytes", max_uncompressed_bytes)
    _check_limit("max_compression_ratio", max_compression_ratio)
    body: bytes = await download_google_sheet_xlsx(
        url, client=client, timeout=timeout, max_bytes=max_bytes
    )
    try:
        return read_xlsx_sheets(
            body,
            max_rows=max_rows,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        )
    except InvalidSpreadsheetError as exc:
        sheet_id, _ = _parse_sheet_link(url)
        raise GoogleSheetAccessError(
            details={
                "export_url": _export_url(sheet_id, "xlsx", None),
                "reason": exc.details.get("reason"),
            },
        ) from exc


__all__: list[str] = [
    "DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES",
    "GoogleSheetAccessError",
    "GoogleSheetRowError",
    "download_google_sheet_xlsx",
    "google_sheet_export_url",
    "read_google_sheet",
    "read_google_sheet_as",
    "read_google_sheet_xlsx",
]
