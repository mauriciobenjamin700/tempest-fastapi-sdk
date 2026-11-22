"""``export.xlsx`` and ``.xlsx`` uploads on the import page."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.admin import router as admin_router
from tempest_fastapi_sdk.admin.spreadsheet_io import (
    XLSX_MEDIA_TYPE,
    is_xlsx_upload,
    xlsx_to_csv,
)
from tests.admin import wave2_world as wave2
from tests.admin.row_scope_world import (
    OTHER,
    OWN,
    TICKETS,
    ScopeTicket,
    ScopeWorld,
    new_database,
    open_world,
)


@pytest.fixture
async def database() -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a fresh in-memory database with the tables created.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    db = await new_database()
    yield db
    await db.drop_tables()
    await db.disconnect()


@pytest.fixture
async def world(
    database: AsyncDatabaseManager, tmp_path: Path
) -> AsyncIterator[ScopeWorld]:
    """World with ``row_scope`` declared on every model.

    Yields:
        ScopeWorld: The signed-in world.
    """
    async for opened in open_world(database, tmp_path, scoped=True):
        yield opened


def _workbook(rows: list[list[Any]]) -> bytes:
    """Write rows into a one-sheet ``.xlsx``.

    Args:
        rows (list[list[Any]]): Header first.

    Returns:
        bytes: The file.
    """
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _sheet(payload: bytes) -> list[list[Any]]:
    """Read every row of the first sheet.

    Args:
        payload (bytes): The ``.xlsx`` file.

    Returns:
        list[list[Any]]: The rows, header first.
    """
    sheet = load_workbook(io.BytesIO(payload)).worksheets[0]
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def _no_spreadsheet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the router believe the ``[spreadsheet]`` extra is missing.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture.
    """
    monkeypatch.setattr(admin_router, "spreadsheet_available", lambda: False)


@pytest.mark.asyncio
async def test_export_holds_the_scoped_rows_as_a_sheet(world: ScopeWorld) -> None:
    resp = await world.client.get(f"{TICKETS}/export.xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == XLSX_MEDIA_TYPE
    assert 'filename="admin_scope_tickets.xlsx"' in resp.headers["content-disposition"]
    assert _sheet(resp.content) == [["name", "company"], ["TicketInside", OWN]]


@pytest.mark.asyncio
async def test_export_honours_filters_and_the_row_cap(
    database: AsyncDatabaseManager, tmp_path: Path
) -> None:
    async for world in open_world(database, tmp_path, scoped=False):
        filtered = await world.client.get(
            f"{TICKETS}/export.xlsx?filter_category_id={world.ids['category_out']}"
        )
        assert _sheet(filtered.content) == [
            ["name", "company"],
            ["TicketOutside", OTHER],
        ]
        searched = await world.client.get(f"{TICKETS}/export.xlsx?q=Inside")
        assert [row[0] for row in _sheet(searched.content)[1:]] == ["TicketInside"]


@pytest.mark.asyncio
async def test_export_stops_at_export_max_rows() -> None:
    db = await wave2.new_database()
    try:
        async for world in wave2.open_world(db, scoped=False, export_max_rows=1):
            resp = await world.client.get(f"{wave2.ARTICLES}/export.xlsx")
            assert len(_sheet(resp.content)) == 2
    finally:
        await db.drop_tables()
        await db.disconnect()


@pytest.mark.asyncio
async def test_datetime_is_the_csv_text_and_formulas_stay_text(
    world: ScopeWorld,
) -> None:
    async with world.db.get_session_context() as session:
        ticket = await session.get(ScopeTicket, world.ids["ticket_in"])
        assert ticket is not None
        ticket.name = '=HYPERLINK("https://evil.example")'
        await session.commit()
    resp = await world.client.get(f"{TICKETS}/export.xlsx")
    sheet = load_workbook(io.BytesIO(resp.content)).worksheets[0]
    cell = sheet.cell(row=2, column=1)
    assert cell.data_type == "s"
    assert cell.value == '=HYPERLINK("https://evil.example")'


def test_datetime_cell_matches_the_csv_export() -> None:
    stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert admin_router._xlsx_cell(stamp) == "2026-01-02T03:04:05+00:00"
    assert admin_router._xlsx_cell(stamp) == admin_router._export_value(stamp)
    assert admin_router._xlsx_cell(True) is True
    assert admin_router._xlsx_cell(None) is None


@pytest.mark.asyncio
async def test_without_the_extra_the_button_goes_and_the_route_names_it(
    world: ScopeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_extra = await world.client.get(f"{TICKETS}/")
    assert "export.xlsx" in with_extra.text
    _no_spreadsheet(monkeypatch)
    without = await world.client.get(f"{TICKETS}/")
    assert "export.xlsx" not in without.text
    resp = await world.client.get(f"{TICKETS}/export.xlsx")
    assert resp.status_code == 303
    follow = await world.client.get(resp.headers["location"])
    assert "tempest-fastapi-sdk[spreadsheet]" in follow.text


@pytest.mark.asyncio
async def test_import_previews_and_commits_an_xlsx(world: ScopeWorld) -> None:
    before = await world.ticket_count()
    page = await world.client.get(f"{TICKETS}/import")
    assert ".xlsx" in page.text
    preview = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={
            "file": (
                "tickets.xlsx",
                _workbook([["name", "company"], ["FromSheet", OWN]]),
                XLSX_MEDIA_TYPE,
            )
        },
    )
    assert preview.status_code == 200
    assert "1 of 1 row(s) will be imported." in preview.text
    payload = re.search(r'name="payload" value="([^"]+)"', preview.text)
    nonce = re.search(r'name="submission_nonce" value="([^"]+)"', preview.text)
    assert payload is not None and nonce is not None
    confirm = await world.client.post(
        f"{TICKETS}/import/confirm",
        data={
            "csrf_token": world.csrf,
            "payload": payload.group(1),
            "submission_nonce": nonce.group(1),
        },
    )
    assert confirm.status_code == 303
    assert await world.ticket_count() == before + 1


@pytest.mark.asyncio
async def test_xlsx_row_outside_the_scope_is_refused(world: ScopeWorld) -> None:
    before = await world.ticket_count()
    preview = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={
            "file": (
                "tickets.xlsx",
                _workbook([["name", "company"], ["SheetIn", OWN], ["SheetOut", OTHER]]),
                XLSX_MEDIA_TYPE,
            )
        },
    )
    assert preview.status_code == 200
    assert "1 of 2 row(s) will be imported." in preview.text
    assert "outside the rows you may manage" in preview.text
    assert 'name="payload"' not in preview.text
    assert await world.ticket_count() == before


@pytest.mark.asyncio
async def test_import_without_the_extra_names_it(
    world: ScopeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_spreadsheet(monkeypatch)
    page = await world.client.get(f"{TICKETS}/import")
    assert ".xlsx" not in page.text
    resp = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={
            "file": (
                "tickets.xlsx",
                _workbook([["name", "company"], ["FromSheet", OWN]]),
                XLSX_MEDIA_TYPE,
            )
        },
    )
    assert resp.status_code == 400
    assert "tempest-fastapi-sdk[spreadsheet]" in resp.text


@pytest.mark.asyncio
async def test_broken_or_oversized_xlsx_is_refused(
    world: ScopeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("nothing.txt", "no workbook here")
    resp = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={"file": ("t.xlsx", broken.getvalue(), XLSX_MEDIA_TYPE)},
    )
    assert resp.status_code == 400
    assert "Could not read the file as an .xlsx workbook." in resp.text
    sheet = _workbook([["name", "company"], *[["x" * 200, OWN]] * 2000])
    assert len(sheet) < 2000 * 200
    monkeypatch.setattr(admin_router, "ADMIN_IMPORT_MAX_BYTES", len(sheet) + 10)
    big = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={"file": ("t.xlsx", sheet, XLSX_MEDIA_TYPE)},
    )
    assert big.status_code == 400
    assert "The file is larger than" in big.text


@pytest.mark.asyncio
async def test_xlsx_cells_become_csv_text() -> None:
    raw = _workbook(
        [
            ["name", "flag", "count", "price", "when", "empty"],
            ["a", True, 3.0, 2.5, datetime(2026, 1, 2, 3, 4), None],
        ]
    )
    text = await xlsx_to_csv(raw)
    assert text.splitlines() == [
        "name,flag,count,price,when,empty",
        "a,true,3,2.5,2026-01-02T03:04:00,",
    ]
    assert await xlsx_to_csv(_workbook([["name"]])) == ""


def test_upload_kind_is_read_from_the_name_or_the_zip_signature() -> None:
    assert is_xlsx_upload("a.XLSX", b"name\n")
    assert is_xlsx_upload("a.csv", b"PK\x03\x04rest")
    assert not is_xlsx_upload("a.csv", b"name,company\n")
    assert not is_xlsx_upload(None, b"")


@pytest.mark.asyncio
async def test_export_drops_control_characters_instead_of_failing(
    world: ScopeWorld,
) -> None:
    async with world.db.get_session_context() as session:
        ticket = await session.get(ScopeTicket, world.ids["ticket_in"])
        assert ticket is not None
        ticket.name = "abc\x0bdef\x00"
        await session.commit()
    resp = await world.client.get(f"{TICKETS}/export.xlsx")
    assert resp.status_code == 200
    assert _sheet(resp.content)[1][0] == "abcdef"


@pytest.mark.asyncio
async def test_formula_without_a_cached_value_refuses_the_import(
    world: ScopeWorld,
) -> None:
    before = await world.ticket_count()
    raw = _workbook([["name", "company"], ["Plain", OWN], ["=1+1", OWN]])
    resp = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={"file": ("t.xlsx", raw, XLSX_MEDIA_TYPE)},
    )
    assert resp.status_code == 400
    assert "A3: formula with no calculated value." in resp.text
    assert 'name="payload"' not in resp.text
    assert await world.ticket_count() == before


@pytest.mark.asyncio
async def test_sheet_past_the_row_limit_is_refused_clearly(
    world: ScopeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tempest_fastapi_sdk.admin import spreadsheet_io

    monkeypatch.setattr(spreadsheet_io, "ADMIN_XLSX_IMPORT_MAX_ROWS", 2)
    monkeypatch.setattr(admin_router, "ADMIN_XLSX_IMPORT_MAX_ROWS", 2)
    raw = _workbook([["name", "company"], *[[f"R{i}", OWN] for i in range(3)]])
    resp = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={"file": ("t.xlsx", raw, XLSX_MEDIA_TYPE)},
    )
    assert resp.status_code == 400
    assert "The workbook passes the import limits (2 rows, 16 MiB" in resp.text


def test_reader_limits_scale_with_the_import_cap() -> None:
    from tempest_fastapi_sdk.admin import spreadsheet_io
    from tempest_fastapi_sdk.admin.csv_import import ADMIN_IMPORT_MAX_BYTES

    assert spreadsheet_io.ADMIN_XLSX_IMPORT_MAX_ROWS == ADMIN_IMPORT_MAX_BYTES // 16
    assert (
        spreadsheet_io.ADMIN_XLSX_IMPORT_MAX_UNCOMPRESSED_BYTES
        == 32 * ADMIN_IMPORT_MAX_BYTES
    )


def _with_cell(raw: bytes, reference: str, cell_xml: str) -> bytes:
    """Replace one cell of the first sheet's XML.

    Args:
        raw (bytes): An ``.xlsx`` written by openpyxl.
        reference (str): The cell to replace (``"C2"``).
        cell_xml (str): The new ``<c>`` element.

    Returns:
        bytes: The rewritten workbook.
    """
    source = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode()
                pattern = rf'<c r="{reference}"[^>]*?(/>|>.*?</c>)'
                text, count = re.subn(pattern, cell_xml, text, count=1)
                assert count == 1
                data = text.encode()
            target.writestr(item, data)
    return out.getvalue()


@pytest.mark.asyncio
async def test_formula_cached_as_empty_text_is_accepted(world: ScopeWorld) -> None:
    raw = _with_cell(
        _workbook([["name", "company", "attachment"], ["Cached", OWN, "x"]]),
        "C2",
        '<c r="C2" t="str"><f>IF(1,"","")</f><v></v></c>',
    )
    resp = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={"file": ("t.xlsx", raw, XLSX_MEDIA_TYPE)},
    )
    assert resp.status_code == 200
    assert "1 of 1 row(s) will be imported." in resp.text
    assert "formula with no calculated value" not in resp.text


@pytest.mark.asyncio
async def test_formula_in_a_column_without_header_is_ignored(
    world: ScopeWorld,
) -> None:
    raw = _workbook([["name", "company"], ["Plain", OWN, None, "=1+1"]])
    resp = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={"file": ("t.xlsx", raw, XLSX_MEDIA_TYPE)},
    )
    assert resp.status_code == 200
    assert "1 of 1 row(s) will be imported." in resp.text


@pytest.mark.asyncio
async def test_conversion_waits_on_the_reader_semaphore() -> None:
    import asyncio

    from tempest_fastapi_sdk.spreadsheet import reader

    semaphore = reader._read_semaphore(None)
    held = reader.DEFAULT_MAX_CONCURRENT_XLSX_READS
    for _ in range(held):
        await semaphore.acquire()
    raw = _workbook([["name"], ["x"]])
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(xlsx_to_csv(raw), timeout=0.5)
    finally:
        for _ in range(held):
            semaphore.release()
    assert await xlsx_to_csv(raw) == "name\r\nx\r\n"


@pytest.mark.asyncio
async def test_formula_cell_with_no_value_element_is_refused(
    world: ScopeWorld,
) -> None:
    raw = _with_cell(
        _workbook([["name", "company", "attachment"], ["Forged", OWN, "x"]]),
        "C2",
        '<c r="C2"><f>1+1</f></c>',
    )
    resp = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={"file": ("t.xlsx", raw, XLSX_MEDIA_TYPE)},
    )
    assert resp.status_code == 400
    assert "C2: formula with no calculated value." in resp.text


@pytest.mark.asyncio
async def test_conversion_opens_the_workbook_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openpyxl

    calls: list[bool] = []
    original = openpyxl.load_workbook

    def counting(*args: Any, **kwargs: Any) -> Any:
        """Count every workbook opened through openpyxl.

        Args:
            *args (Any): Passed through.
            **kwargs (Any): Passed through.

        Returns:
            Any: The workbook.
        """
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(openpyxl, "load_workbook", counting)
    raw = _workbook([["name", "company"], ["x", OWN]])
    assert await xlsx_to_csv(raw) == f"name,company\r\nx,{OWN}\r\n"
    assert len(calls) == 1
