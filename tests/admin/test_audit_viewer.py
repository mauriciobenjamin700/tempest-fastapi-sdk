"""End-to-end test for the admin per-row audit-history viewer."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseAuditLogModel,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.db.audit import snapshot_model


class AuditUser(BaseUserModel):
    __tablename__ = "admin_audit_users"


class AuditedThing(BaseModel):
    __tablename__ = "admin_audited_thing"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class ThingAuditLog(BaseAuditLogModel):
    __tablename__ = "admin_audited_thing_log"


SECRET = "x" * 48

thing_admin = AdminModel(model=AuditedThing, audit_model=ThingAuditLog)


@pytest.fixture
async def app_with_audit() -> AsyncIterator[tuple[FastAPI, str]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        user = AuditUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

        repo: BaseRepository[AuditedThing] = BaseRepository(
            session, model=AuditedThing, audit_model=ThingAuditLog
        )
        thing = await repo.add_audited(
            AuditedThing(name="original"), actor="root@example.com"
        )
        thing_id = str(thing.id)
        before = snapshot_model(thing)
        thing.name = "renamed"
        await repo.update_audited(thing, before, actor="root@example.com")

    site = AdminSite(title="Audit Admin")
    site.register(thing_admin)
    backend = UserModelAuthBackend(AuditUser)

    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=backend,
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, thing_id
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
async def test_detail_renders_audit_history(
    app_with_audit: tuple[FastAPI, str],
) -> None:
    app, thing_id = app_with_audit
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{AuditedThing.__tablename__}/{thing_id}")

    assert response.status_code == 200
    body = response.text
    # Both lifecycle entries show up in the timeline (match the item
    # classes, not the words "create"/"update" which also appear in the
    # "created at" / "updated at" stamp panel).
    assert "tempest-admin-history" in body
    assert "tempest-admin-history__item--create" in body
    assert "tempest-admin-history__item--update" in body
    # The update diff shows both values of the renamed field.
    assert "original" in body
    assert "renamed" in body


@pytest.mark.asyncio
async def test_history_absent_without_audit_model() -> None:
    """A model registered without audit_model shows no History panel."""
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = AuditUser(email="a@b.com", hashed_password="", is_admin=True)
        user.set_password("pw")
        session.add(user)
        thing = AuditedThing(name="x")
        session.add(thing)
        await session.commit()
        await session.refresh(thing)
        thing_id = str(thing.id)

    site = AdminSite(title="No Audit")
    site.register(AdminModel(model=AuditedThing))  # no audit_model
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(AuditUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    async with _client(app) as client:
        await client.post(
            "/admin/login", data={"identifier": "a@b.com", "password": "pw"}
        )
        response = await client.get(f"/admin/m/{AuditedThing.__tablename__}/{thing_id}")

    assert response.status_code == 200
    assert "History" not in response.text
    await db.drop_tables()
    await db.disconnect()
