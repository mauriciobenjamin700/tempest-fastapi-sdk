"""Admin write errors must re-render the page, not answer 500.

A failed write rolls the session back, and a rollback expires every
object in the identity map -- the signed-in principal included, even
though it was loaded before the write and never touched by it. Reading
an expired column from async code emits sync IO, which SQLAlchemy
rejects with ``MissingGreenlet``: the operator got a 500 that reads like
a broken server instead of the form carrying the error.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermission,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    Inline,
    UserModelAuthBackend,
    make_admin_router,
)


class RollbackUser(BaseUserModel):
    __tablename__ = "admin_rollback_users"


class UniqueThing(BaseModel):
    __tablename__ = "admin_rollback_thing"
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)


class RollbackTeam(BaseModel):
    __tablename__ = "admin_rollback_team"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class RollbackSeat(BaseModel):
    __tablename__ = "admin_rollback_seat"
    label: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    team_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_rollback_team.id"), nullable=False
    )


_SLUG = UniqueThing.__tablename__
_TEAM_SLUG = RollbackTeam.__tablename__
_SEAT_SLUG = RollbackSeat.__tablename__
SECRET = "r" * 48


def _policy(principal: object, admin: object, action: AdminPermission) -> bool:
    """Allow admins only, reading a principal column like a real policy.

    Args:
        principal (object): The signed-in principal.
        admin (object): The admin being accessed.
        action (AdminPermission): The attempted action.

    Returns:
        bool: True when the principal is an administrator.
    """
    return bool(getattr(principal, "is_admin", False))


@pytest.fixture
async def app_rollback() -> AsyncIterator[tuple[FastAPI, str, str]]:
    """App with a unique-constrained model, plus a parent/child inline."""
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        user = RollbackUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        taken = UniqueThing(code="taken")
        free = UniqueThing(code="free")
        team = RollbackTeam(name="Platform")
        session.add_all([taken, free, team])
        await session.commit()
        await session.refresh(free)
        await session.refresh(team)
        free_id = str(free.id)
        team_id = str(team.id)
        session.add(RollbackSeat(label="seat-1", team_id=team.id))
        await session.commit()

    site = AdminSite(title="Rollback Admin")
    site.register(AdminModel(model=UniqueThing, can_import=True))
    site.register(
        AdminModel(
            model=RollbackTeam,
            inlines=[
                Inline(
                    RollbackSeat,
                    RollbackSeat.team_id,
                    list_display=[RollbackSeat.label],
                    editable=True,
                )
            ],
        )
    )
    site.register(AdminModel(model=RollbackSeat))

    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(RollbackUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=_policy,
        )
    )
    yield app, free_id, team_id
    await db.drop_tables()
    await db.disconnect()


def _csrf_from(html: str) -> str:
    """Pull the CSRF token out of a rendered form.

    Args:
        html (str): The rendered page.

    Returns:
        str: The token value.
    """
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None, "csrf token not found in form"
    return match.group(1)


def _client(app: FastAPI) -> AsyncClient:
    """Return an unopened client bound to the app under test.

    Args:
        app (FastAPI): The app under test.

    Returns:
        AsyncClient: The client, to be used as an async context manager.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient) -> None:
    """Sign the client in as the seeded administrator.

    Args:
        client (AsyncClient): The open client.
    """
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )


@pytest.mark.asyncio
async def test_create_conflict_rerenders_form_with_error(
    app_rollback: tuple[FastAPI, str, str],
) -> None:
    app, _free_id, _team_id = app_rollback
    async with _client(app) as client:
        await _login(client)
        form = await client.get(f"/admin/m/{_SLUG}/new")
        token = _csrf_from(form.text)
        response = await client.post(
            f"/admin/m/{_SLUG}/new",
            data={"csrf_token": token, "code": "taken", "is_active": "true"},
        )
    assert response.status_code == 400, response.text[:400]
    assert "Conflict creating UniqueThing" in response.text
    assert "root@example.com" in response.text


@pytest.mark.asyncio
async def test_edit_conflict_rerenders_form_with_error(
    app_rollback: tuple[FastAPI, str, str],
) -> None:
    app, free_id, _team_id = app_rollback
    async with _client(app) as client:
        await _login(client)
        form = await client.get(f"/admin/m/{_SLUG}/{free_id}/edit")
        token = _csrf_from(form.text)
        response = await client.post(
            f"/admin/m/{_SLUG}/{free_id}/edit",
            data={"csrf_token": token, "code": "taken", "is_active": "true"},
        )
    assert response.status_code == 400, response.text[:400]
    assert "Conflict updating UniqueThing" in response.text
    assert "root@example.com" in response.text


@pytest.mark.asyncio
async def test_csv_import_conflict_rerenders_page_with_row_error(
    app_rollback: tuple[FastAPI, str, str],
) -> None:
    app, _free_id, _team_id = app_rollback
    async with _client(app) as client:
        await _login(client)
        page = await client.get(f"/admin/m/{_SLUG}/import")
        token = _csrf_from(page.text)
        response = await client.post(
            f"/admin/m/{_SLUG}/import",
            data={"csrf_token": token},
            files={"file": ("rows.csv", b"code\ntaken\nfresh\n", "text/csv")},
        )
    assert response.status_code == 200, response.text[:400]
    assert "Conflict creating UniqueThing" in response.text
    assert "root@example.com" in response.text


@pytest.mark.asyncio
async def test_inline_conflict_rerenders_detail_with_error(
    app_rollback: tuple[FastAPI, str, str],
) -> None:
    app, _free_id, team_id = app_rollback
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{_TEAM_SLUG}/{team_id}")
        token = _csrf_from(detail.text)
        response = await client.post(
            f"/admin/m/{_TEAM_SLUG}/{team_id}/inlines/{_SEAT_SLUG}",
            data={"csrf_token": token, "row.new0.label": "seat-1"},
        )
    assert response.status_code == 400, response.text[:400]
    assert "Conflict creating RollbackSeat" in response.text
    assert "Platform" in response.text
    assert "root@example.com" in response.text
