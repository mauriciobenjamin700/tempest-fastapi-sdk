"""End-to-end tests for admin inlines (related child tables on detail)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKey, String, Uuid
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
