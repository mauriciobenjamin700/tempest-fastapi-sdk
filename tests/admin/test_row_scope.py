"""``AdminModel(row_scope=...)`` on every admin route that touches a row.

The route list is read from the ``APIRouter`` that ``make_admin_router``
returns (``router.routes``; ``app.routes`` does not flatten an included
router), so a route added under ``/m/{slug}/`` without a case here fails
:func:`test_every_model_route_has_a_scope_case`. Each case asserts the
out-of-scope row is unreachable **and** the in-scope one is reachable, so
none passes by vacuity; :func:`test_case_fails_without_the_scope` reruns
every case on the same data with no ``row_scope`` and expects it to fail.
"""

from __future__ import annotations

import io
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from openpyxl import load_workbook
from sqlalchemy import true

from tempest_fastapi_sdk import AdminModel, AsyncDatabaseManager
from tempest_fastapi_sdk.admin import AdminRowScopeError
from tempest_fastapi_sdk.admin.csv_import import sign_import_payload
from tests.admin.row_scope_world import (
    ACTION_IDS,
    NOTES,
    OTHER,
    OWN,
    SECRET,
    TICKETS,
    ScopeLeakError,
    ScopeNote,
    ScopeTicket,
    ScopeWorld,
    _build,
    hidden,
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
async def scoped_world(
    database: AsyncDatabaseManager, tmp_path: Path
) -> AsyncIterator[ScopeWorld]:
    """World with ``row_scope`` declared on every model.

    Yields:
        ScopeWorld: The signed-in world.
    """
    async for world in open_world(database, tmp_path, scoped=True):
        yield world


@pytest.fixture
async def unscoped_world(
    database: AsyncDatabaseManager, tmp_path: Path
) -> AsyncIterator[ScopeWorld]:
    """The same world with no ``row_scope``, for the proof the cases bite.

    Yields:
        ScopeWorld: The signed-in world.
    """
    async for world in open_world(database, tmp_path, scoped=False):
        yield world


async def case_list(world: ScopeWorld) -> None:
    """List, count and FK filter options keep only the principal's rows."""
    page = await world.client.get(f"{TICKETS}/")
    assert page.status_code == 200
    assert "TicketInside" in page.text
    assert "CategoryInside" in page.text
    hidden("TicketOutside" not in page.text, "foreign ticket listed")
    hidden("1 record" in page.text, "foreign ticket counted")
    hidden("CategoryOutside" not in page.text, "foreign category in the filter")


async def case_export(world: ScopeWorld) -> None:
    """The CSV and XLSX exports hold only the principal's rows."""
    resp = await world.client.get(f"{TICKETS}/export.csv")
    assert resp.status_code == 200
    assert "TicketInside" in resp.text
    hidden("TicketOutside" not in resp.text, "foreign ticket exported")
    sheet = await world.client.get(f"{TICKETS}/export.xlsx")
    assert sheet.status_code == 200
    names = [
        row[0]
        for row in load_workbook(io.BytesIO(sheet.content))
        .worksheets[0]
        .iter_rows(min_row=2, values_only=True)
    ]
    assert "TicketInside" in names
    hidden("TicketOutside" not in names, "foreign ticket exported to xlsx")


async def case_duplicate(world: ScopeWorld) -> None:
    """Duplicating a foreign row is ``404``; an own row fills the create form."""
    out = await world.client.get(f"{TICKETS}/{world.ids['ticket_out']}/duplicate")
    hidden(out.status_code == 404, "foreign row duplicated")
    hidden("TicketOutside" not in out.text, "foreign values in the copy")
    inside = await world.client.get(f"{TICKETS}/{world.ids['ticket_in']}/duplicate")
    assert inside.status_code == 200
    assert 'value="TicketInside"' in inside.text


async def case_create_form(world: ScopeWorld) -> None:
    """The create form's FK select lists only reachable parents."""
    page = await world.client.get(f"{NOTES}/new")
    assert page.status_code == 200
    assert "TicketInside" in page.text
    hidden("TicketOutside" not in page.text, "foreign ticket offered as parent")


async def case_import_form(world: ScopeWorld) -> None:
    """The import page reads no row; it only has to render."""
    page = await world.client.get(f"{TICKETS}/import")
    assert page.status_code == 200


async def case_import_submit(world: ScopeWorld) -> None:
    """The preview refuses a row outside the scope and offers no confirm."""
    before = await world.ticket_count()
    resp = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={
            "file": (
                "t.csv",
                f"name,company\nNewInside,{OWN}\nNewOutside,{OTHER}\n".encode(),
                "text/csv",
            )
        },
    )
    assert resp.status_code == 200
    hidden(
        "1 of 2 row(s) will be imported." in resp.text
        and "outside the rows you may manage" in resp.text
        and 'name="payload"' not in resp.text,
        "foreign row accepted by the preview",
    )
    assert await world.ticket_count() == before


async def case_import_confirm(world: ScopeWorld) -> None:
    """A signed file whose row left the scope is refused, all or nothing."""
    before = await world.ticket_count()
    payload = sign_import_payload(
        SECRET,
        slug=ScopeTicket.__tablename__,
        principal_id=world.principal_id,
        text=f"name,company\nNewInside,{OWN}\nNewOutside,{OTHER}\n",
    )
    page = await world.client.get(f"{TICKETS}/")
    nonce = re.search(r'name="submission_nonce" value="([^"]+)"', page.text)
    assert nonce is not None
    resp = await world.client.post(
        f"{TICKETS}/import/confirm",
        data={
            "csrf_token": world.csrf,
            "payload": payload,
            "submission_nonce": nonce.group(1),
        },
    )
    hidden(resp.status_code == 400, "foreign row committed by the confirm")
    assert "outside the rows you may manage" in resp.text
    assert await world.ticket_count() == before


async def case_create_submit(world: ScopeWorld) -> None:
    """A row created outside the scope is rolled back; inside is kept."""
    before = await world.ticket_count()
    refused = await world.client.post(
        f"{TICKETS}/new",
        data={"csrf_token": world.csrf, "name": "MadeOutside", "company": OTHER},
    )
    hidden(refused.status_code == 400, "foreign row created")
    assert "outside the rows you may manage" in refused.text
    assert await world.ticket_count() == before
    accepted = await world.client.post(
        f"{TICKETS}/new",
        data={"csrf_token": world.csrf, "name": "MadeInside", "company": OWN},
    )
    assert accepted.status_code == 303
    assert await world.ticket_count() == before + 1


async def case_bulk(world: ScopeWorld) -> None:
    """Bulk delete and bulk edit touch only the rows the principal reaches.

    The bulk edit that would move a row out of the scope is rolled back.
    """
    resp = await world.client.post(
        f"{TICKETS}/bulk",
        data={
            "csrf_token": world.csrf,
            "action": "delete",
            "ids": [str(world.ids["ticket_out"])],
        },
    )
    assert resp.status_code == 303
    hidden(await world.ticket("out") is not None, "foreign ticket bulk-deleted")
    resp = await world.client.post(
        f"{TICKETS}/bulk",
        data={
            "csrf_token": world.csrf,
            "action": "deactivate",
            "ids": [str(world.ids["ticket_in"])],
        },
    )
    assert resp.status_code == 303
    inside = await world.ticket("in")
    assert inside is not None and inside.is_active is False
    step = await world.client.post(
        f"{TICKETS}/bulk",
        data={
            "csrf_token": world.csrf,
            "action": "bulk_edit",
            "ids": [str(world.ids["ticket_in"]), str(world.ids["ticket_out"])],
        },
    )
    assert step.status_code == 200
    hidden("1 row(s) selected." in step.text, "foreign ticket kept in the selection")
    nonce = re.search(r'name="submission_nonce" value="([^"]+)"', step.text)
    assert nonce is not None
    moved = await world.client.post(
        f"{TICKETS}/bulk",
        data={
            "csrf_token": world.csrf,
            "action": "bulk_edit",
            "ids": [str(world.ids["ticket_in"])],
            "bulk_field": "company",
            "bulk_input": "1",
            "company": OTHER,
            "submission_nonce": nonce.group(1),
        },
    )
    assert moved.status_code == 303
    inside = await world.ticket("in")
    hidden(
        inside is not None and inside.company == OWN,
        "bulk edit moved a row out of the scope",
    )


async def case_upload_download(world: ScopeWorld) -> None:
    """The attachment of an out-of-scope row answers ``404``."""
    out = await world.client.get(
        f"{TICKETS}/{world.ids['ticket_out']}/upload/attachment"
    )
    hidden(out.status_code == 404, "foreign attachment served")
    inside = await world.client.get(
        f"{TICKETS}/{world.ids['ticket_in']}/upload/attachment"
    )
    assert inside.status_code == 200


async def case_detail(world: ScopeWorld) -> None:
    """Detail answers ``404`` outside; inside, the inline hides foreign notes."""
    out = await world.client.get(f"{TICKETS}/{world.ids['ticket_out']}")
    hidden(out.status_code == 404, "foreign detail served")
    inside = await world.client.get(f"{TICKETS}/{world.ids['ticket_in']}")
    assert inside.status_code == 200
    assert "NoteInside" in inside.text
    hidden("NoteOutside" not in inside.text, "foreign note in the inline")


async def case_autocomplete(world: ScopeWorld) -> None:
    """The autocomplete searches only the target model's reachable rows."""
    resp = await world.client.get(f"{TICKETS}/autocomplete/category_id?q=Category")
    assert resp.status_code == 200
    assert "CategoryInside" in resp.text
    hidden("CategoryOutside" not in resp.text, "foreign category autocompleted")


async def case_edit_form(world: ScopeWorld) -> None:
    """The edit form answers ``404`` outside the scope."""
    out = await world.client.get(f"{TICKETS}/{world.ids['ticket_out']}/edit")
    hidden(out.status_code == 404, "foreign edit form served")
    inside = await world.client.get(f"{TICKETS}/{world.ids['ticket_in']}/edit")
    assert inside.status_code == 200


async def case_edit_submit(world: ScopeWorld) -> None:
    """Editing outside is ``404``; editing a row out of the scope is refused."""
    out = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_out']}/edit",
        data={"csrf_token": world.csrf, "name": "Hijacked", "company": OTHER},
    )
    hidden(out.status_code == 404, "foreign row edited")
    outside = await world.ticket("out")
    assert outside is not None and outside.name == "TicketOutside"
    moved = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_in']}/edit",
        data={"csrf_token": world.csrf, "name": "TicketInside", "company": OTHER},
    )
    hidden(moved.status_code == 400, "row moved out of the scope")
    assert "outside the rows you may manage" in moved.text
    inside = await world.ticket("in")
    assert inside is not None and inside.company == OWN


async def case_inline_submit(world: ScopeWorld) -> None:
    """The formset of a foreign parent is ``404``; a foreign child is ignored."""
    out = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_out']}/inlines/{ScopeNote.__tablename__}",
        data={"csrf_token": world.csrf},
    )
    hidden(out.status_code == 404, "foreign formset served")
    resp = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_in']}/inlines/{ScopeNote.__tablename__}",
        data={
            "csrf_token": world.csrf,
            f"row.{world.ids['note_out']}.body": "Overwritten",
            f"row.{world.ids['note_out']}.company": OTHER,
            f"row.{world.ids['note_in']}.body": "EditedInside",
            f"row.{world.ids['note_in']}.company": OWN,
        },
    )
    assert resp.status_code == 303
    foreign = await world.note("out")
    hidden(
        foreign is not None and foreign.body == "NoteOutside",
        "foreign note edited through the formset",
    )
    own = await world.note("in")
    assert own is not None and own.body == "EditedInside"


async def case_delete(world: ScopeWorld) -> None:
    """Deleting outside the scope is ``404`` and leaves the row."""
    out = await world.client.post(
        f"{NOTES}/{world.ids['note_out']}/delete",
        data={"csrf_token": world.csrf},
    )
    hidden(out.status_code == 404, "foreign note deleted")
    assert await world.note("out") is not None
    inside = await world.client.post(
        f"{NOTES}/{world.ids['note_in']}/delete",
        data={"csrf_token": world.csrf},
    )
    assert inside.status_code == 303
    assert await world.note("in") is None


ScopeCase = Callable[[ScopeWorld], Awaitable[None]]

CASES: dict[tuple[str, str], ScopeCase] = {
    ("GET", "/admin/m/{slug}/"): case_list,
    ("GET", "/admin/m/{slug}/export.{fmt}"): case_export,
    ("GET", "/admin/m/{slug}/new"): case_create_form,
    ("POST", "/admin/m/{slug}/new"): case_create_submit,
    ("GET", "/admin/m/{slug}/import"): case_import_form,
    ("POST", "/admin/m/{slug}/import"): case_import_submit,
    ("POST", "/admin/m/{slug}/import/confirm"): case_import_confirm,
    ("POST", "/admin/m/{slug}/bulk"): case_bulk,
    ("GET", "/admin/m/{slug}/{identity}/upload/{field}"): case_upload_download,
    ("GET", "/admin/m/{slug}/{identity}"): case_detail,
    ("GET", "/admin/m/{slug}/autocomplete/{field}"): case_autocomplete,
    ("GET", "/admin/m/{slug}/{identity}/edit"): case_edit_form,
    ("GET", "/admin/m/{slug}/{identity}/duplicate"): case_duplicate,
    ("POST", "/admin/m/{slug}/{identity}/edit"): case_edit_submit,
    ("POST", "/admin/m/{slug}/{identity}/inlines/{child_slug}"): case_inline_submit,
    ("POST", "/admin/m/{slug}/{identity}/delete"): case_delete,
}
"""One case per ``(method, path)`` under ``/m/{slug}/``."""

NO_ROW_READ: frozenset[tuple[str, str]] = frozenset({("GET", "/admin/m/{slug}/import")})
"""Routes that read no row of any model, so no scope can change them."""


def _model_routes(router: APIRouter) -> set[tuple[str, str]]:
    """Return every ``(method, path)`` the router serves under ``/m/``.

    Args:
        router (APIRouter): The router ``make_admin_router`` returned.

    Returns:
        set[tuple[str, str]]: The model routes.
    """
    found: set[tuple[str, str]] = set()
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/admin/m/"):
            for method in route.methods:
                found.add((method, route.path))
    return found


def _uncovered(router: APIRouter) -> set[tuple[str, str]]:
    """Return the model routes with no case in :data:`CASES`.

    Args:
        router (APIRouter): The admin router.

    Returns:
        set[tuple[str, str]]: Routes missing a case.
    """
    return _model_routes(router) - set(CASES)


def test_every_model_route_has_a_scope_case(tmp_path: Path) -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    _app, router = _build(db, tmp_path, scoped=True)
    assert _model_routes(router), "the router lists no model route"
    assert _uncovered(router) == set()
    assert set(CASES) - _model_routes(router) == set()


def test_coverage_check_fires_on_a_new_route(tmp_path: Path) -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    _app, router = _build(db, tmp_path, scoped=True)

    async def stray() -> dict[str, str]:
        """Stand in for a route added without a scope case.

        Returns:
            dict[str, str]: Nothing meaningful.
        """
        return {}

    router.add_api_route("/m/{slug}/{identity}/clone", stray, methods=["POST"])
    assert _uncovered(router) == {("POST", "/admin/m/{slug}/{identity}/clone")}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(CASES), ids=lambda r: f"{r[0]} {r[1]}")
async def test_route_honours_row_scope(
    route: tuple[str, str], scoped_world: ScopeWorld
) -> None:
    await CASES[route](scoped_world)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route",
    sorted(set(CASES) - NO_ROW_READ),
    ids=lambda r: f"{r[0]} {r[1]}",
)
async def test_case_fails_without_the_scope(
    route: tuple[str, str], unscoped_world: ScopeWorld
) -> None:
    with pytest.raises(ScopeLeakError):
        await CASES[route](unscoped_world)


@pytest.mark.asyncio
async def test_custom_action_receives_only_reachable_ids(
    scoped_world: ScopeWorld,
) -> None:
    ACTION_IDS.clear()
    resp = await scoped_world.client.post(
        f"{TICKETS}/bulk",
        data={
            "csrf_token": scoped_world.csrf,
            "action": "custom:record_selection",
            "ids": [
                str(scoped_world.ids["ticket_in"]),
                str(scoped_world.ids["ticket_out"]),
            ],
        },
    )
    assert resp.status_code == 303
    recorded = list(ACTION_IDS)
    assert recorded == [[str(scoped_world.ids["ticket_in"])]]


@pytest.mark.asyncio
async def test_dashboard_count_is_scoped(scoped_world: ScopeWorld) -> None:
    page = await scoped_world.client.get("/admin/")
    assert page.status_code == 200
    marker = 'tempest-admin-model-card__count">'
    counts = [chunk.split("<", 1)[0] for chunk in page.text.split(marker)[1:]]
    assert counts == ["1", "1", "1"]


@pytest.mark.asyncio
async def test_scope_returning_none_fails_closed() -> None:
    forgot_return = AdminModel(ScopeTicket, row_scope=lambda user, admin: None)
    with pytest.raises(AdminRowScopeError, match="not a SQLAlchemy clause"):
        await forgot_return.scope_for(object())


@pytest.mark.asyncio
async def test_true_is_the_explicit_every_row() -> None:
    every_row = AdminModel(ScopeTicket, row_scope=lambda user, admin: true())
    clause = await every_row.scope_for(object())
    assert clause is not None
    assert await AdminModel(ScopeTicket).scope_for(object()) is None
