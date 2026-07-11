"""End-to-end tests for admin inlines (related child tables on detail)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKey, Integer, String, Uuid, func, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    Inline,
    UserModelAuthBackend,
    make_admin_router,
)


class InlineUser(BaseUserModel):
    __tablename__ = "admin_inline_users"


class Team(BaseModel):
    __tablename__ = "admin_inline_team"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Member(BaseModel):
    __tablename__ = "admin_inline_member"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    team_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_inline_team.id"), nullable=False
    )


SECRET = "x" * 48


@pytest.fixture
async def app_inline() -> AsyncIterator[tuple[FastAPI, str]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        user = InlineUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        team = Team(name="Platform")
        session.add(team)
        await session.commit()
        await session.refresh(team)
        team_id = str(team.id)
        session.add_all(
            [
                Member(name="Ada", team_id=team.id),
                Member(name="Linus", team_id=team.id),
            ]
        )
        await session.commit()

    site = AdminSite(title="Inline Admin")
    site.register(
        AdminModel(
            model=Team,
            inlines=[Inline(Member, Member.team_id, list_display=[Member.name])],
        )
    )
    site.register(AdminModel(model=Member))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(InlineUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, team_id
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient) -> None:
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )


@pytest.mark.asyncio
async def test_detail_lists_inline_children(
    app_inline: tuple[FastAPI, str],
) -> None:
    app, team_id = app_inline
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{Team.__tablename__}/{team_id}")

    assert response.status_code == 200
    body = response.text
    assert "tempest-admin-inline" in body
    assert "Ada" in body
    assert "Linus" in body
    # Add link pre-fills the parent FK.
    assert f"/m/{Member.__tablename__}/new?team_id={team_id}" in body


@pytest.mark.asyncio
async def test_add_link_prefills_fk(app_inline: tuple[FastAPI, str]) -> None:
    app, team_id = app_inline
    async with _client(app) as client:
        await _login(client)
        response = await client.get(
            f"/admin/m/{Member.__tablename__}/new?team_id={team_id}"
        )
    assert response.status_code == 200
    # The team_id field is pre-filled with the parent id.
    assert team_id in response.text


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


@pytest.fixture
async def app_editable() -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager, str]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        user = InlineUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        team = Team(name="Platform")
        session.add(team)
        await session.commit()
        await session.refresh(team)
        team_id = str(team.id)
        session.add_all(
            [
                Member(name="Ada", team_id=team.id),
                Member(name="Linus", team_id=team.id),
            ]
        )
        await session.commit()

    site = AdminSite(title="Editable Inline Admin")
    site.register(
        AdminModel(
            model=Team,
            inlines=[
                Inline(
                    Member,
                    Member.team_id,
                    list_display=[Member.name],
                    editable=True,
                    can_delete=True,
                )
            ],
        )
    )
    site.register(AdminModel(model=Member))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(InlineUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, db, team_id
    await db.drop_tables()
    await db.disconnect()


async def _member_names(db: AsyncDatabaseManager) -> list[str]:
    async with db.get_session_context() as session:
        result = await session.execute(select(Member.name).order_by(Member.name))
        return list(result.scalars().all())


async def _member_count(db: AsyncDatabaseManager) -> int:
    async with db.get_session_context() as session:
        result = await session.execute(select(func.count()).select_from(Member))
        return int(result.scalar_one())


async def _member_ids(db: AsyncDatabaseManager) -> dict[str, str]:
    async with db.get_session_context() as session:
        result = await session.execute(select(Member.name, Member.id))
        return {name: str(mid) for name, mid in result.all()}


@pytest.mark.asyncio
async def test_editable_inline_renders_formset(
    app_editable: tuple[FastAPI, AsyncDatabaseManager, str],
) -> None:
    app, _db, team_id = app_editable
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{Team.__tablename__}/{team_id}")
    assert response.status_code == 200
    body = response.text
    # Editable inline posts back to the parent detail's inline endpoint.
    assert f"/m/{Team.__tablename__}/{team_id}/inlines/{Member.__tablename__}" in body
    # Existing rows render as inputs, plus a blank add row.
    assert 'value="Ada"' in body
    assert "row.new0.name" in body


@pytest.mark.asyncio
async def test_editable_inline_updates_existing_row(
    app_editable: tuple[FastAPI, AsyncDatabaseManager, str],
) -> None:
    app, db, team_id = app_editable
    ids = await _member_ids(db)
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{Team.__tablename__}/{team_id}")
        token = _csrf_from(detail.text)
        response = await client.post(
            f"/admin/m/{Team.__tablename__}/{team_id}/inlines/{Member.__tablename__}",
            data={
                "csrf_token": token,
                f"row.{ids['Ada']}.name": "Ada Lovelace",
                f"row.{ids['Linus']}.name": "Linus",
            },
        )
    assert response.status_code == 303
    assert "Ada Lovelace" in await _member_names(db)


@pytest.mark.asyncio
async def test_editable_inline_adds_new_row(
    app_editable: tuple[FastAPI, AsyncDatabaseManager, str],
) -> None:
    app, db, team_id = app_editable
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{Team.__tablename__}/{team_id}")
        token = _csrf_from(detail.text)
        response = await client.post(
            f"/admin/m/{Team.__tablename__}/{team_id}/inlines/{Member.__tablename__}",
            data={"csrf_token": token, "row.new0.name": "Grace"},
        )
    assert response.status_code == 303
    assert await _member_count(db) == 3
    assert "Grace" in await _member_names(db)


@pytest.mark.asyncio
async def test_editable_inline_blank_new_row_is_ignored(
    app_editable: tuple[FastAPI, AsyncDatabaseManager, str],
) -> None:
    app, db, team_id = app_editable
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{Team.__tablename__}/{team_id}")
        token = _csrf_from(detail.text)
        response = await client.post(
            f"/admin/m/{Team.__tablename__}/{team_id}/inlines/{Member.__tablename__}",
            data={"csrf_token": token, "row.new0.name": ""},
        )
    assert response.status_code == 303
    assert await _member_count(db) == 2


@pytest.mark.asyncio
async def test_editable_inline_deletes_checked_row(
    app_editable: tuple[FastAPI, AsyncDatabaseManager, str],
) -> None:
    app, db, team_id = app_editable
    ids = await _member_ids(db)
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{Team.__tablename__}/{team_id}")
        token = _csrf_from(detail.text)
        response = await client.post(
            f"/admin/m/{Team.__tablename__}/{team_id}/inlines/{Member.__tablename__}",
            data={
                "csrf_token": token,
                f"row.{ids['Linus']}.name": "Linus",
                f"row.{ids['Linus']}.__delete": "true",
            },
        )
    assert response.status_code == 303
    names = await _member_names(db)
    assert "Linus" not in names
    assert "Ada" in names


@pytest.mark.asyncio
async def test_editable_inline_validation_error_re_renders(
    app_editable: tuple[FastAPI, AsyncDatabaseManager, str],
) -> None:
    app, db, team_id = app_editable
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{Team.__tablename__}/{team_id}")
        token = _csrf_from(detail.text)
        # A new row with a name but a non-numeric level must fail coercion
        # and re-render (no insert).
        response = await client.post(
            f"/admin/m/{Team.__tablename__}/{team_id}/inlines/{Member.__tablename__}",
            data={
                "csrf_token": token,
                "row.new0.name": "Grace",
                "row.new0.level": "not-a-number",
            },
        )
    assert response.status_code == 400
    assert "Invalid value for Level." in response.text
    assert await _member_count(db) == 2
