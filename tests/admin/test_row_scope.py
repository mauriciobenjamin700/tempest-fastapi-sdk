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

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ColumnElement, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    AdminActionContext,
    AdminActionResult,
    Inline,
    admin_action,
)
from tempest_fastapi_sdk.admin.csv_import import sign_import_payload
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login
from tempest_fastapi_sdk.utils import LocalUploadStorage

SECRET = "s" * 48
OWN = "acme"
OTHER = "globex"


class ScopeUser(BaseUserModel):
    """Principal carrying the company its scope is keyed on."""

    __tablename__ = "admin_scope_users"
    company: Mapped[str] = mapped_column(String(32), nullable=False, default=OWN)


class ScopeCategory(BaseModel):
    """Autocomplete target of :class:`ScopeTicket`."""

    __tablename__ = "admin_scope_categories"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    company: Mapped[str] = mapped_column(String(32), nullable=False)


class ScopeTicket(BaseModel):
    """The scoped model under test."""

    __tablename__ = "admin_scope_tickets"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    company: Mapped[str] = mapped_column(String(32), nullable=False)
    attachment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_scope_categories.id"), nullable=True
    )


class ScopeNote(BaseModel):
    """Editable inline child of :class:`ScopeTicket`; its FK is a select."""

    __tablename__ = "admin_scope_notes"
    body: Mapped[str] = mapped_column(String(64), nullable=False)
    company: Mapped[str] = mapped_column(String(32), nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_scope_tickets.id"), nullable=False
    )


def _company_scope(column: Any) -> Callable[[Any, Any], ColumnElement[bool]]:
    """Build a sync scope keeping the principal's own company.

    Args:
        column (Any): The model's ``company`` column.

    Returns:
        Callable[[Any, Any], ColumnElement[bool]]: The ``row_scope``.
    """

    def scope(user: Any, admin: Any) -> ColumnElement[bool]:
        """Return ``company == user.company``.

        Args:
            user (Any): The principal.
            admin (Any): The admin asked about.

        Returns:
            ColumnElement[bool]: The clause.
        """
        return column == user.company

    return scope


async def _async_ticket_scope(user: Any, admin: Any) -> ColumnElement[bool]:
    """Async scope for tickets, so both call styles are exercised.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.

    Returns:
        ColumnElement[bool]: ``ScopeTicket.company == user.company``.
    """
    return ScopeTicket.company == user.company


ACTION_IDS: list[list[str]] = []
"""What :func:`record_selection` saw, one entry per run."""


@admin_action(label="Record selection")
async def record_selection(ctx: AdminActionContext) -> AdminActionResult:
    """Record the ids a custom action receives.

    Args:
        ctx (AdminActionContext): The selection.

    Returns:
        AdminActionResult: A plain success.
    """
    ACTION_IDS.append(sorted(str(row_id) for row_id in ctx.ids))
    return AdminActionResult(message="recorded")


@dataclass
class ScopeWorld:
    """What one case needs: a signed-in client and the seeded rows."""

    client: AsyncClient
    db: AsyncDatabaseManager
    csrf: str
    principal_id: str
    ids: dict[str, uuid.UUID] = field(default_factory=dict)

    async def ticket(self, key: str) -> ScopeTicket | None:
        """Reload a seeded ticket.

        Args:
            key (str): ``"in"`` or ``"out"``.

        Returns:
            ScopeTicket | None: The row, or ``None`` when deleted.
        """
        async with self.db.get_session_context() as session:
            repo: BaseRepository[ScopeTicket] = BaseRepository(
                session, model=ScopeTicket
            )
            return await repo.get_or_none({"id": self.ids[f"ticket_{key}"]})

    async def note(self, key: str) -> ScopeNote | None:
        """Reload a seeded note.

        Args:
            key (str): ``"in"`` or ``"out"``.

        Returns:
            ScopeNote | None: The row.
        """
        async with self.db.get_session_context() as session:
            repo: BaseRepository[ScopeNote] = BaseRepository(session, model=ScopeNote)
            return await repo.get_or_none({"id": self.ids[f"note_{key}"]})

    async def ticket_count(self) -> int:
        """Count every ticket, scope or not.

        Returns:
            int: The row count.
        """
        async with self.db.get_session_context() as session:
            repo: BaseRepository[ScopeTicket] = BaseRepository(
                session, model=ScopeTicket
            )
            return await repo.count()


def _build(
    db: AsyncDatabaseManager, upload_root: Path, *, scoped: bool
) -> tuple[FastAPI, APIRouter]:
    """Mount the admin with or without ``row_scope`` on the three models.

    Args:
        db (AsyncDatabaseManager): The connected database.
        upload_root (Path): Directory of the upload storage.
        scoped (bool): Whether the models declare their ``row_scope``.

    Returns:
        tuple[FastAPI, APIRouter]: The app and the admin router.
    """
    site = AdminSite(title="Scope")
    site.register(
        AdminModel(
            ScopeCategory,
            search_fields=["name"],
            row_scope=_company_scope(ScopeCategory.company) if scoped else None,
        )
    )
    site.register(
        AdminModel(
            ScopeTicket,
            list_display=["name", "company"],
            list_filter=["category_id"],
            search_fields=["name"],
            autocomplete_fields=["category_id"],
            upload_fields=["attachment"],
            upload_storage=LocalUploadStorage(upload_root),
            can_import=True,
            inlines=[Inline(ScopeNote, "ticket_id", editable=True)],
            actions=[record_selection],
            row_scope=_async_ticket_scope if scoped else None,
        )
    )
    site.register(
        AdminModel(
            ScopeNote,
            search_fields=["body"],
            row_scope=_company_scope(ScopeNote.company) if scoped else None,
        )
    )
    router = make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(ScopeUser),
        secret_key=SECRET,
        cookie_secure=False,
    )
    app = FastAPI()
    app.include_router(router)
    return app, router


async def _seed(db: AsyncDatabaseManager, upload_root: Path) -> dict[str, uuid.UUID]:
    """Insert one row per company of each model, and the operator.

    Args:
        db (AsyncDatabaseManager): The connected database.
        upload_root (Path): Where the attachments are written.

    Returns:
        dict[str, uuid.UUID]: ``{ticket_in, ticket_out, category_in, ...}``.
    """
    async with db.get_session_context() as session:
        user = ScopeUser(
            email="op@example.com", hashed_password="", is_admin=True, company=OWN
        )
        user.set_password("hunter2")
        cat_in = ScopeCategory(name="CategoryInside", company=OWN)
        cat_out = ScopeCategory(name="CategoryOutside", company=OTHER)
        session.add_all([user, cat_in, cat_out])
        await session.flush()
        ticket_in = ScopeTicket(
            name="TicketInside",
            company=OWN,
            attachment="in.txt",
            category_id=cat_in.id,
        )
        ticket_out = ScopeTicket(
            name="TicketOutside",
            company=OTHER,
            attachment="out.txt",
            category_id=cat_out.id,
        )
        session.add_all([ticket_in, ticket_out])
        await session.flush()
        note_in = ScopeNote(body="NoteInside", company=OWN, ticket_id=ticket_in.id)
        note_out = ScopeNote(body="NoteOutside", company=OTHER, ticket_id=ticket_in.id)
        session.add_all([note_in, note_out])
        await session.commit()
        ids = {
            "operator": user.id,
            "ticket_in": ticket_in.id,
            "ticket_out": ticket_out.id,
            "category_in": cat_in.id,
            "category_out": cat_out.id,
            "note_in": note_in.id,
            "note_out": note_out.id,
        }
    (upload_root / "in.txt").write_text("inside")
    (upload_root / "out.txt").write_text("outside")
    return ids


@pytest.fixture
async def database() -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a fresh in-memory database with the tables created.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    yield db
    await db.drop_tables()
    await db.disconnect()


async def _world(
    db: AsyncDatabaseManager, tmp_path: Path, *, scoped: bool
) -> AsyncIterator[ScopeWorld]:
    """Seed, mount and sign in.

    Args:
        db (AsyncDatabaseManager): The database.
        tmp_path (Path): Scratch directory for uploads.
        scoped (bool): Whether the scope is declared.

    Yields:
        ScopeWorld: The signed-in world.
    """
    ids = await _seed(db, tmp_path)
    app, _router = _build(db, tmp_path, scoped=scoped)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        page = await client.get(f"/admin/m/{ScopeTicket.__tablename__}/")
        yield ScopeWorld(
            client=client,
            db=db,
            csrf=admin_csrf_token(page.text),
            principal_id=str(ids["operator"]),
            ids=ids,
        )


@pytest.fixture
async def scoped_world(
    database: AsyncDatabaseManager, tmp_path: Path
) -> AsyncIterator[ScopeWorld]:
    """World with ``row_scope`` declared on every model.

    Yields:
        ScopeWorld: The signed-in world.
    """
    async for world in _world(database, tmp_path, scoped=True):
        yield world


@pytest.fixture
async def unscoped_world(
    database: AsyncDatabaseManager, tmp_path: Path
) -> AsyncIterator[ScopeWorld]:
    """The same world with no ``row_scope``, for the proof the cases bite.

    Yields:
        ScopeWorld: The signed-in world.
    """
    async for world in _world(database, tmp_path, scoped=False):
        yield world


TICKETS = f"/admin/m/{ScopeTicket.__tablename__}"
NOTES = f"/admin/m/{ScopeNote.__tablename__}"


async def case_list(world: ScopeWorld) -> None:
    """List, count and FK filter options keep only the principal's rows."""
    page = await world.client.get(f"{TICKETS}/")
    assert page.status_code == 200
    assert "TicketInside" in page.text
    assert "TicketOutside" not in page.text
    assert "1 record" in page.text
    assert "CategoryInside" in page.text
    assert "CategoryOutside" not in page.text


async def case_export(world: ScopeWorld) -> None:
    """The CSV export holds only the principal's rows."""
    resp = await world.client.get(f"{TICKETS}/export.csv")
    assert resp.status_code == 200
    assert "TicketInside" in resp.text
    assert "TicketOutside" not in resp.text


async def case_create_form(world: ScopeWorld) -> None:
    """The create form's FK select lists only reachable parents."""
    page = await world.client.get(f"{NOTES}/new")
    assert page.status_code == 200
    assert "TicketInside" in page.text
    assert "TicketOutside" not in page.text


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
    assert "1 of 2 row(s) will be imported." in resp.text
    assert "outside the rows you may manage" in resp.text
    assert 'name="payload"' not in resp.text
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
    assert resp.status_code == 400
    assert "outside the rows you may manage" in resp.text
    assert await world.ticket_count() == before


async def case_create_submit(world: ScopeWorld) -> None:
    """A row created outside the scope is rolled back; inside is kept."""
    before = await world.ticket_count()
    refused = await world.client.post(
        f"{TICKETS}/new",
        data={"csrf_token": world.csrf, "name": "MadeOutside", "company": OTHER},
    )
    assert refused.status_code == 400
    assert "outside the rows you may manage" in refused.text
    assert await world.ticket_count() == before
    accepted = await world.client.post(
        f"{TICKETS}/new",
        data={"csrf_token": world.csrf, "name": "MadeInside", "company": OWN},
    )
    assert accepted.status_code == 303
    assert await world.ticket_count() == before + 1


async def case_bulk(world: ScopeWorld) -> None:
    """A bulk delete touches only the selected rows the principal reaches."""
    resp = await world.client.post(
        f"{TICKETS}/bulk",
        data={
            "csrf_token": world.csrf,
            "action": "delete",
            "ids": [str(world.ids["ticket_out"])],
        },
    )
    assert resp.status_code == 303
    assert await world.ticket("out") is not None
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


async def case_upload_download(world: ScopeWorld) -> None:
    """The attachment of an out-of-scope row answers ``404``."""
    out = await world.client.get(
        f"{TICKETS}/{world.ids['ticket_out']}/upload/attachment"
    )
    assert out.status_code == 404
    inside = await world.client.get(
        f"{TICKETS}/{world.ids['ticket_in']}/upload/attachment"
    )
    assert inside.status_code == 200


async def case_detail(world: ScopeWorld) -> None:
    """Detail answers ``404`` outside; inside, the inline hides foreign notes."""
    out = await world.client.get(f"{TICKETS}/{world.ids['ticket_out']}")
    assert out.status_code == 404
    inside = await world.client.get(f"{TICKETS}/{world.ids['ticket_in']}")
    assert inside.status_code == 200
    assert "NoteInside" in inside.text
    assert "NoteOutside" not in inside.text


async def case_autocomplete(world: ScopeWorld) -> None:
    """The autocomplete searches only the target model's reachable rows."""
    resp = await world.client.get(f"{TICKETS}/autocomplete/category_id?q=Category")
    assert resp.status_code == 200
    assert "CategoryInside" in resp.text
    assert "CategoryOutside" not in resp.text


async def case_edit_form(world: ScopeWorld) -> None:
    """The edit form answers ``404`` outside the scope."""
    out = await world.client.get(f"{TICKETS}/{world.ids['ticket_out']}/edit")
    assert out.status_code == 404
    inside = await world.client.get(f"{TICKETS}/{world.ids['ticket_in']}/edit")
    assert inside.status_code == 200


async def case_edit_submit(world: ScopeWorld) -> None:
    """Editing outside is ``404``; editing a row out of the scope is refused."""
    out = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_out']}/edit",
        data={"csrf_token": world.csrf, "name": "Hijacked", "company": OTHER},
    )
    assert out.status_code == 404
    outside = await world.ticket("out")
    assert outside is not None and outside.name == "TicketOutside"
    moved = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_in']}/edit",
        data={"csrf_token": world.csrf, "name": "TicketInside", "company": OTHER},
    )
    assert moved.status_code == 400
    assert "outside the rows you may manage" in moved.text
    inside = await world.ticket("in")
    assert inside is not None and inside.company == OWN


async def case_inline_submit(world: ScopeWorld) -> None:
    """The formset of a foreign parent is ``404``; a foreign child is ignored."""
    out = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_out']}/inlines/{ScopeNote.__tablename__}",
        data={"csrf_token": world.csrf},
    )
    assert out.status_code == 404
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
    assert foreign is not None and foreign.body == "NoteOutside"
    own = await world.note("in")
    assert own is not None and own.body == "EditedInside"


async def case_delete(world: ScopeWorld) -> None:
    """Deleting outside the scope is ``404`` and leaves the row."""
    out = await world.client.post(
        f"{NOTES}/{world.ids['note_out']}/delete",
        data={"csrf_token": world.csrf},
    )
    assert out.status_code == 404
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
    with pytest.raises(AssertionError):
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
async def test_scope_returning_none_leaves_rows_unrestricted() -> None:
    admin = AdminModel(ScopeTicket, row_scope=lambda user, admin: None)
    assert await admin.scope_for(object()) is None
    assert await AdminModel(ScopeTicket).scope_for(object()) is None
