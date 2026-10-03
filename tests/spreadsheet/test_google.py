"""Tests for the public Google Sheets reader.

Every unit test runs against ``httpx.MockTransport`` whose responses copy
what ``docs.google.com`` answered when measured with ``curl`` on
2026-10-03: the export replies ``307`` to a ``*.googleusercontent.com``
host, the CSV comes back as ``text/csv; charset=utf-8`` with CRLF line
endings and no trailing newline, a non-existent ID answers ``404``
``text/html`` and a ``gid`` that names no tab answers ``400``
``text/html``. :data:`SAMPLE_CSV` is the body of the public sample sheet,
byte for byte.

The one test that reaches the real endpoint is marked ``network`` and
stays out of the default run (``make test-network``).
"""

from __future__ import annotations

import subprocess
import sys
from typing import Final

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.spreadsheet import (
    GoogleSheetAccessError,
    GoogleSheetRowError,
    google_sheet_export_url,
    read_google_sheet,
    read_google_sheet_as,
)

SHEET_ID: Final[str] = "1h0ATstw2f6ryXvbwV-DW6zwsBRIF-2k5zHcm2uTEge8"
EXPORT_BASE: Final[str] = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export"
SHARE_LINK: Final[str] = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing"
)
REDIRECT_TARGET: Final[str] = (
    "https://doc-00-0o-sheets.googleusercontent.com/export/abc/sheet.csv"
)
SAMPLE_CSV: Final[bytes] = (
    b"item,valor,tamanho\r\n"
    b"Bota forza,500,41\r\n"
    b"Bota new forza,1000,41\r\n"
    b"Macacao forza,1700,X\r\n"
    b"Protetor de coluna,300,uni\r\n"
    b"macacao dainese,1000,XL\r\n"
    b"Jaqueta x11 Masc,200,consultar\r\n"
    b"Jaqueta x11 Fem,200,consultar\r\n"
    b"Bota Forma,400,vendida\r\n"
    b"Luva x11 Fem L,250,M\r\n"
    b"Luva alpinestar Gp Pro L,250,M\r\n"
    b"Capacete Ls2 62 arrow***,,consultar"
)
CSV_HEADERS: Final[dict[str, str]] = {"content-type": "text/csv; charset=utf-8"}
HTML_HEADERS: Final[dict[str, str]] = {"content-type": "text/html; charset=utf-8"}


class Product(BaseModel):
    """The sample sheet's row: ``tamanho`` mixes sizes, numbers and notes."""

    item: str
    valor: int | None = None
    tamanho: str


def _google(
    csv_body: bytes = SAMPLE_CSV,
    *,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """Mimic the export endpoint: a ``307`` hop, then the CSV.

    Args:
        csv_body (bytes): Body served by the redirect target.
        seen (list[httpx.Request] | None): Collects every request sent.

    Returns:
        httpx.MockTransport: The fake transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer like Google does.

        Args:
            request (httpx.Request): The incoming request.

        Returns:
            httpx.Response: ``307`` from ``docs.google.com``, CSV from the
            redirect target.
        """
        if seen is not None:
            seen.append(request)
        if request.url.host == "docs.google.com":
            return httpx.Response(307, headers={"location": REDIRECT_TARGET})
        return httpx.Response(200, headers=CSV_HEADERS, content=csv_body)

    return httpx.MockTransport(handler)


def _fixed(status: int, headers: dict[str, str], body: bytes) -> httpx.MockTransport:
    """Answer every request with the same response.

    Args:
        status (int): Status code.
        headers (dict[str, str]): Response headers.
        body (bytes): Response body.

    Returns:
        httpx.MockTransport: The fake transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Return the fixed response.

        Args:
            request (httpx.Request): The incoming request (unused).

        Returns:
            httpx.Response: The fixed response.
        """
        return httpx.Response(status, headers=headers, content=body)

    return httpx.MockTransport(handler)


class TestExportUrl:
    """Every shape of shared link maps to the export URL."""

    @pytest.mark.parametrize(
        ("link", "expected"),
        [
            (SHARE_LINK, f"{EXPORT_BASE}?format=csv"),
            (
                f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0",
                f"{EXPORT_BASE}?format=csv&gid=0",
            ),
            (
                f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=123#gid=123",
                f"{EXPORT_BASE}?format=csv&gid=123",
            ),
            (
                f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing#gid=77",
                f"{EXPORT_BASE}?format=csv&gid=77",
            ),
            (
                f"https://docs.google.com/spreadsheets/d/{SHEET_ID}",
                f"{EXPORT_BASE}?format=csv",
            ),
            (
                f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/",
                f"{EXPORT_BASE}?format=csv",
            ),
            (
                f"https://docs.google.com/spreadsheets/u/1/d/{SHEET_ID}/edit#gid=5",
                f"{EXPORT_BASE}?format=csv&gid=5",
            ),
            (
                f"docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
                f"{EXPORT_BASE}?format=csv",
            ),
            (f"  {SHEET_ID}  ", f"{EXPORT_BASE}?format=csv"),
            (
                f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=9&range=A1",
                f"{EXPORT_BASE}?format=csv&gid=9",
            ),
        ],
    )
    def test_link_shapes(self, link: str, expected: str) -> None:
        """Each shape yields the same export URL, keeping the tab."""
        assert google_sheet_export_url(link) == expected

    def test_query_gid_wins_over_fragment(self) -> None:
        """When both carry a tab, the query string is the one used."""
        link = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=1#gid=2"
        assert google_sheet_export_url(link).endswith("&gid=1")

    def test_xlsx_format(self) -> None:
        """``export_format`` switches the ``format`` query parameter."""
        assert (
            google_sheet_export_url(SHARE_LINK, export_format="xlsx")
            == f"{EXPORT_BASE}?format=xlsx"
        )

    @pytest.mark.parametrize(
        "link",
        [
            "https://example.com/spreadsheets/d/abc/edit",
            "https://docs.google.com/document/d/abc/edit",
            "ftp://docs.google.com/spreadsheets/d/abc",
            "https://docs.google.com.evil.example/spreadsheets/d/abc",
            "not a link",
            "",
        ],
    )
    def test_rejects_non_sheet_links(self, link: str) -> None:
        """Anything that is not a Google Sheets link raises ``ValueError``."""
        with pytest.raises(ValueError, match="Not a Google Sheets link"):
            google_sheet_export_url(link)

    def test_rejects_published_link(self) -> None:
        """A *Publish to the web* link has no ``/export`` endpoint."""
        with pytest.raises(ValueError, match="Publish to the web"):
            google_sheet_export_url(
                "https://docs.google.com/spreadsheets/d/e/2PACX-1vabc/pubhtml"
            )

    def test_rejects_non_numeric_gid(self) -> None:
        """A ``gid`` that is not a number is refused, not forwarded."""
        with pytest.raises(ValueError, match="gid must be a number"):
            google_sheet_export_url(f"{SHARE_LINK}#gid=abc")

    def test_rejects_unknown_format(self) -> None:
        """A format outside the two supported ones is refused at runtime."""
        with pytest.raises(ValueError, match="export_format"):
            google_sheet_export_url(SHARE_LINK, export_format="ods")  # type: ignore[arg-type]


class TestReadGoogleSheet:
    """``read_google_sheet`` fetches, follows the redirect and parses."""

    async def test_follows_redirect_and_parses(self) -> None:
        """The ``307`` is followed even on a client built without it."""
        seen: list[httpx.Request] = []
        async with httpx.AsyncClient(transport=_google(seen=seen)) as client:
            rows = await read_google_sheet(f"{SHARE_LINK}#gid=0", client=client)
        assert [str(request.url) for request in seen] == [
            f"{EXPORT_BASE}?format=csv&gid=0",
            REDIRECT_TARGET,
        ]
        assert len(rows) == 11
        assert rows[0] == {"item": "Bota forza", "valor": "500", "tamanho": "41"}
        assert rows[-1] == {
            "item": "Capacete Ls2 62 arrow***",
            "valor": "",
            "tamanho": "consultar",
        }

    async def test_injected_client_is_left_open(self) -> None:
        """The reader never closes a client it did not create."""
        client = httpx.AsyncClient(transport=_google())
        await read_google_sheet(SHARE_LINK, client=client)
        assert not client.is_closed
        await client.aclose()

    async def test_created_client_is_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without an injected client, the one created is closed on return."""
        created: list[httpx.AsyncClient] = []
        original = httpx.AsyncClient

        def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
            """Build the client over the fake transport and record it.

            Args:
                *args (object): Ignored positional arguments.
                **kwargs (object): Keyword arguments; ``timeout`` is kept.

            Returns:
                httpx.AsyncClient: The recorded client.
            """
            client = original(transport=_google(), timeout=kwargs["timeout"])  # type: ignore[arg-type]
            created.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        rows = await read_google_sheet(SHARE_LINK, timeout=7.5)
        assert len(rows) == 11
        assert len(created) == 1
        assert created[0].is_closed
        assert created[0].timeout == httpx.Timeout(7.5)

    @pytest.mark.parametrize(
        ("status", "headers"),
        [
            (404, HTML_HEADERS),
            (400, HTML_HEADERS),
            (200, HTML_HEADERS),
            (500, CSV_HEADERS),
            (200, {}),
        ],
    )
    async def test_non_csv_answer_is_a_typed_error(
        self, status: int, headers: dict[str, str]
    ) -> None:
        """Anything but a successful ``text/csv`` raises, never parses."""
        async with httpx.AsyncClient(
            transport=_fixed(status, headers, b"<html>login</html>")
        ) as client:
            with pytest.raises(GoogleSheetAccessError) as caught:
                await read_google_sheet(SHARE_LINK, client=client)
        error = caught.value
        assert error.status_code == 502
        assert error.code == "GOOGLE_SHEET_UNAVAILABLE"
        assert error.details["status_code"] == status
        assert error.details["export_url"] == f"{EXPORT_BASE}?format=csv"
        assert "Anyone with the link" in error.message

    @pytest.mark.parametrize("body", [b"", b"item,valor,tamanho\r\n"])
    async def test_empty_sheet_is_an_empty_list(self, body: bytes) -> None:
        """No data row is success: ``[]``, not an error."""
        async with httpx.AsyncClient(transport=_google(body)) as client:
            assert await read_google_sheet(SHARE_LINK, client=client) == []

    async def test_blank_rows_skipped_short_rows_padded(self) -> None:
        """A blank row is dropped; a short record is padded with ``""``."""
        body = b"a,b,c\r\n1,2,3\r\n,,\r\n\r\n4\r\n5,6,7,8\r\n"
        async with httpx.AsyncClient(transport=_google(body)) as client:
            rows = await read_google_sheet(SHARE_LINK, client=client)
        assert rows == [
            {"a": "1", "b": "2", "c": "3"},
            {"a": "4", "b": "", "c": ""},
            {"a": "5", "b": "6", "c": "7"},
        ]

    async def test_utf8_with_bom_and_quoted_newline(self) -> None:
        """Accents decode, a BOM is dropped, a quoted newline stays a cell."""
        body = '﻿nome,obs\r\nJoão,"linha 1\nlinha 2"\r\n'.encode()
        async with httpx.AsyncClient(transport=_google(body)) as client:
            rows = await read_google_sheet(SHARE_LINK, client=client)
        assert rows == [{"nome": "João", "obs": "linha 1\nlinha 2"}]

    async def test_bad_link_raises_before_any_request(self) -> None:
        """A non-sheet link fails fast, without touching the network."""
        seen: list[httpx.Request] = []
        async with httpx.AsyncClient(transport=_google(seen=seen)) as client:
            with pytest.raises(ValueError):
                await read_google_sheet("https://example.com/x", client=client)
        assert seen == []


class TestReadGoogleSheetAs:
    """``read_google_sheet_as`` validates each row into a model."""

    async def test_sample_sheet_validates(self) -> None:
        """The mixed ``tamanho`` column fits ``str``; blank ``valor`` is None."""
        async with httpx.AsyncClient(transport=_google()) as client:
            products = await read_google_sheet_as(SHARE_LINK, Product, client=client)
        assert len(products) == 11
        assert products[0] == Product(item="Bota forza", valor=500, tamanho="41")
        assert products[2].tamanho == "X"
        assert products[-1].valor is None

    async def test_error_names_the_sheet_row(self) -> None:
        """The failing row is reported by its number in the sheet."""

        class StrictProduct(BaseModel):
            """Wrongly types the mixed column as a number."""

            item: str
            valor: int | None = None
            tamanho: int

        async with httpx.AsyncClient(transport=_google()) as client:
            with pytest.raises(GoogleSheetRowError) as caught:
                await read_google_sheet_as(SHARE_LINK, StrictProduct, client=client)
        error = caught.value
        assert error.status_code == 422
        assert error.code == "GOOGLE_SHEET_ROW_INVALID"
        assert error.details["row"] == 4
        assert error.message_params == {"row": 4}
        assert "Row 4" in error.message
        assert error.details["errors"][0]["loc"] == ("tamanho",)
        assert error.details["errors"][0]["input"] == "X"

    async def test_row_number_counts_skipped_blank_rows(self) -> None:
        """A blank row still counts toward the sheet's numbering."""

        class Numbers(BaseModel):
            """One integer column."""

            n: int

        body = b"n\r\n1\r\n\r\nx\r\n"
        async with httpx.AsyncClient(transport=_google(body)) as client:
            with pytest.raises(GoogleSheetRowError) as caught:
                await read_google_sheet_as(SHARE_LINK, Numbers, client=client)
        assert caught.value.details["row"] == 4

    async def test_omit_blank_false_passes_empty_string(self) -> None:
        """With ``omit_blank=False`` the empty cell reaches the validator."""
        async with httpx.AsyncClient(transport=_google()) as client:
            with pytest.raises(GoogleSheetRowError) as caught:
                await read_google_sheet_as(
                    SHARE_LINK, Product, client=client, omit_blank=False
                )
        assert caught.value.details["row"] == 12

    async def test_blank_required_cell_reports_missing(self) -> None:
        """A blank cell in a required column surfaces as ``missing``."""

        class Priced(BaseModel):
            """``valor`` is required here."""

            item: str
            valor: int

        async with httpx.AsyncClient(transport=_google()) as client:
            with pytest.raises(GoogleSheetRowError) as caught:
                await read_google_sheet_as(SHARE_LINK, Priced, client=client)
        assert caught.value.details["row"] == 12
        assert caught.value.details["errors"][0]["type"] == "missing"

    async def test_empty_sheet_is_an_empty_list(self) -> None:
        """No data row validates to ``[]``."""
        async with httpx.AsyncClient(transport=_google(b"")) as client:
            assert await read_google_sheet_as(SHARE_LINK, Product, client=client) == []


def test_importable_without_openpyxl() -> None:
    """The reader imports with ``openpyxl`` blocked — it needs no extra."""
    code = (
        "import sys; sys.modules['openpyxl'] = None; "
        "from tempest_fastapi_sdk.spreadsheet import read_google_sheet_as; "
        "print(read_google_sheet_as.__name__)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "read_google_sheet_as"


@pytest.mark.network
async def test_live_public_sheet() -> None:
    """The public sample sheet reads and validates against the real endpoint."""
    products = await read_google_sheet_as(f"{SHARE_LINK}#gid=0", Product)
    assert products[0] == Product(item="Bota forza", valor=500, tamanho="41")
    assert products[-1].valor is None
    assert {product.tamanho for product in products} >= {"41", "X", "uni"}


@pytest.mark.network
async def test_live_unknown_id_is_a_typed_error() -> None:
    """A spreadsheet ID that does not exist raises the typed error."""
    with pytest.raises(GoogleSheetAccessError) as caught:
        await read_google_sheet("1" + "x" * 43)
    assert caught.value.details["status_code"] == 404
