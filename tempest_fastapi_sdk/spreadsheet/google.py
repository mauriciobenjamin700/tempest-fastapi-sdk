"""Read a publicly shared Google Sheet as rows, with no API key.

A sheet shared as *Anyone with the link* answers on its ``/export``
endpoint with the tab rendered as CSV. That is the whole mechanism: no
OAuth, no service account, no ``gspread``. What this module owns is the
part every hand-written version gets slightly wrong:

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
  ``gid`` that names no tab answers ``400`` with HTML. Anything that is not
  a successful ``text/csv`` response raises
  :class:`GoogleSheetAccessError` instead of being parsed as data.

Only the CSV path ships: one tab per call, picked by ``gid``. It needs
nothing beyond the base install (``httpx`` and the standard ``csv``
module), so importing it does not require the ``[spreadsheet]`` extra.

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
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.exceptions.base import AppException

ModelT = TypeVar("ModelT", bound=BaseModel)

_GOOGLE_SHEETS_HOST: Final[str] = "docs.google.com"
_CSV_MEDIA_TYPE: Final[str] = "text/csv"

_SHEET_PATH: Final[re.Pattern[str]] = re.compile(
    r"^/spreadsheets(?:/u/\d+)?/d/(?P<sheet_id>[A-Za-z0-9_-]+)(?:/|$)"
)
_SHEET_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")
_GID: Final[re.Pattern[str]] = re.compile(r"^\d+$")
_PUBLISHED_SEGMENT: Final[str] = "e"


class GoogleSheetAccessError(AppException):
    """The export URL did not answer with a CSV document.

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
        "The Google Sheet could not be read as CSV. Check the link and share "
        "the sheet as 'Anyone with the link'."
    )
    code: str = "GOOGLE_SHEET_UNAVAILABLE"
    status_code: int = 502


class GoogleSheetRowError(AppException):
    """One row of the sheet did not validate against the target model.

    ``details["row"]`` is the row number as the spreadsheet shows it — the
    header is row 1, so the first data row is row 2 — and
    ``details["errors"]`` is Pydantic's error list for that row, without
    the ``url`` and ``ctx`` keys (``ctx`` may hold the raised exception,
    which does not serialize to JSON).
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
            with. ``csv`` exports one tab; ``xlsx`` exports the workbook.

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
    candidate: str = url.strip()
    sheet_id: str
    gid: str | None = None
    if _SHEET_ID.match(candidate):
        sheet_id = candidate
    else:
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
        sheet_id = match.group("sheet_id")
        if sheet_id == _PUBLISHED_SEGMENT:
            raise ValueError(
                "A 'Publish to the web' link (/d/e/...) has no export "
                "endpoint; use the sheet's share link instead."
            )
        gid = _gid_from(parts.query, parts.fragment)
    export_url: str = (
        f"https://{_GOOGLE_SHEETS_HOST}/spreadsheets/d/{sheet_id}"
        f"/export?format={export_format}"
    )
    if gid is not None:
        export_url = f"{export_url}&gid={gid}"
    return export_url


def _parse_csv(text: str) -> list[tuple[int, dict[str, str]]]:
    """Split a CSV export into numbered rows keyed by the header.

    The first record is the header. A record whose cells are all empty —
    a blank row in the sheet — is skipped, but still counts toward the
    numbering, so the number stays the one the spreadsheet shows. A record
    shorter than the header is padded with ``""``; cells beyond the
    header's width are ignored.

    Args:
        text (str): The decoded CSV body.

    Returns:
        list[tuple[int, dict[str, str]]]: ``(row_number, row)`` pairs, the
        header being row 1. Empty when the export has no data row.
    """
    records: list[list[str]] = list(csv.reader(io.StringIO(text, newline="")))
    if not records:
        return []
    header: list[str] = records[0]
    width: int = len(header)
    rows: list[tuple[int, dict[str, str]]] = []
    for offset, record in enumerate(records[1:]):
        if not any(cell for cell in record):
            continue
        padded: list[str] = record + [""] * (width - len(record))
        rows.append((offset + 2, dict(zip(header, padded, strict=False))))
    return rows


async def _download_csv(
    url: str,
    client: httpx.AsyncClient | None,
    timeout: float,
) -> str:
    """Fetch the CSV export of the sheet a link points at.

    Args:
        url (str): Anything :func:`google_sheet_export_url` accepts.
        client (httpx.AsyncClient | None): Client to send the request
            through. Left open. ``None`` creates one, closed on return.
        timeout (float): Seconds the created client waits. Ignored when a
            client is injected — its own timeout applies.

    Returns:
        str: The CSV body, decoded as UTF-8 (a leading BOM is dropped).

    Raises:
        ValueError: If ``url`` is not a Google Sheets link.
        GoogleSheetAccessError: If the export does not answer with a
            successful ``text/csv`` response.
        httpx.HTTPError: If the request itself fails (timeout, DNS,
            connection refused).
    """
    export_url: str = google_sheet_export_url(url, export_format="csv")
    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as owned:
            response: httpx.Response = await owned.get(
                export_url, follow_redirects=True
            )
    else:
        response = await client.get(export_url, follow_redirects=True)
    content_type: str = response.headers.get("content-type", "")
    media_type: str = content_type.split(";", 1)[0].strip().lower()
    if not response.is_success or media_type != _CSV_MEDIA_TYPE:
        raise GoogleSheetAccessError(
            details={
                "export_url": export_url,
                "status_code": response.status_code,
                "content_type": content_type,
            },
        )
    return response.content.decode("utf-8-sig")


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
    models: list[ModelT] = []
    for row_number, row in _parse_csv(text):
        payload: dict[str, str] = (
            {key: value for key, value in row.items() if value != ""}
            if omit_blank
            else row
        )
        try:
            models.append(schema.model_validate(payload))
        except ValidationError as exc:
            errors: list[dict[str, Any]] = [
                dict(error)
                for error in exc.errors(include_url=False, include_context=False)
            ]
            raise GoogleSheetRowError(
                message=f"Row {row_number} of the Google Sheet failed validation.",
                message_params={"row": row_number},
                details={"row": row_number, "errors": errors},
            ) from exc
    return models


__all__: list[str] = [
    "GoogleSheetAccessError",
    "GoogleSheetRowError",
    "google_sheet_export_url",
    "read_google_sheet",
    "read_google_sheet_as",
]
