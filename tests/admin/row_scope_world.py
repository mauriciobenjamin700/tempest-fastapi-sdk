"""Shared models, data and helpers of the ``row_scope`` tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
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
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login
from tempest_fastapi_sdk.utils import LocalUploadStorage


class ScopeLeakError(AssertionError):
    """A row outside the principal's scope was reachable."""


def hidden(condition: bool, what: str) -> None:
    """Assert an out-of-scope row stayed out of reach.

    The proof that the cases bite expects this exact error, so a case that
    breaks for another reason (a setup change) does not count as proof.

    Args:
        condition (bool): ``True`` when the row was kept out.
        what (str): What leaked, for the failure message.

    Raises:
        ScopeLeakError: When ``condition`` is false.
    """
    if not condition:
        raise ScopeLeakError(what)


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
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_scope_categories.id"), nullable=True
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


async def new_database() -> AsyncDatabaseManager:
    """Open a fresh in-memory database with the tables created.

    Returns:
        AsyncDatabaseManager: The connected manager.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    return db


async def open_world(
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


TICKETS = f"/admin/m/{ScopeTicket.__tablename__}"
NOTES = f"/admin/m/{ScopeNote.__tablename__}"
