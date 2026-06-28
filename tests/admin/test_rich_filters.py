"""Tests for admin rich list filters (enum / FK / date-range)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseStrEnum,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)


class FilterUser(BaseUserModel):
    __tablename__ = "rf_users"


class Status(BaseStrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Owner(BaseModel):
    __tablename__ = "rf_owner"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Item(BaseModel):
    __tablename__ = "rf_item"
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[Status] = mapped_column(
        SAEnum(Status, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("rf_owner.id"), nullable=False, index=True
    )


SECRET = "x" * 48
_SLUG = Item.__tablename__


@pytest.fixture
async def app_rf() -> AsyncIterator[tuple[FastAPI, dict[str, UUID]]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    ids: dict[str, UUID] = {}
    async with db.get_session_context() as session:
        user = FilterUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        alice = Owner(name="Alice")
        bob = Owner(name="Bob")
        session.add_all([alice, bob])
        await session.flush()
        ids["alice"] = alice.id
        ids["bob"] = bob.id
        session.add_all(
            [
                Item(title="a1", status=Status.ACTIVE, owner_id=alice.id),
                Item(title="a2", status=Status.ARCHIVED, owner_id=alice.id),
                Item(title="b1", status=Status.ACTIVE, owner_id=bob.id),
            ]
        )
        await session.commit()

    site = AdminSite(title="Test Admin")
    site.register(AdminModel(model=Owner, search_fields=[Owner.name]))
    site.register(
        AdminModel(
            model=Item,
            list_display=[Item.id, Item.title, Item.status],
            list_filter=[Item.status, Item.owner_id, Item.created_at],
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(FilterUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, ids
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient) -> None:
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )


class TestFilterWidgets:
    @pytest.mark.asyncio
    async def test_widgets_render(self, app_rf: tuple[FastAPI, dict]) -> None:
        app, _ = app_rf
        async with _client(app) as client:
            await _login(client)
            page = await client.get(f"/admin/m/{_SLUG}/")
        # enum select options
        assert 'value="archived"' in page.text
        # FK select lists related owners by name
        assert "Alice" in page.text and "Bob" in page.text
        # date-range inputs for created_at
        assert 'name="filter_created_at_from"' in page.text
        assert 'name="filter_created_at_to"' in page.text


class TestFiltering:
    @pytest.mark.asyncio
    async def test_enum_filter(self, app_rf: tuple[FastAPI, dict]) -> None:
        app, _ = app_rf
        async with _client(app) as client:
            await _login(client)
            page = await client.get(f"/admin/m/{_SLUG}/?filter_status=archived")
        assert "<td>a2</td>" in page.text
        assert "<td>a1</td>" not in page.text
        assert "<td>b1</td>" not in page.text

    @pytest.mark.asyncio
    async def test_fk_filter(self, app_rf: tuple[FastAPI, dict]) -> None:
        app, ids = app_rf
        async with _client(app) as client:
            await _login(client)
            page = await client.get(f"/admin/m/{_SLUG}/?filter_owner_id={ids['bob']}")
        assert "<td>b1</td>" in page.text
        assert "<td>a1</td>" not in page.text
        assert "<td>a2</td>" not in page.text

    @pytest.mark.asyncio
    async def test_date_range_excludes_all(self, app_rf: tuple[FastAPI, dict]) -> None:
        app, _ = app_rf
        async with _client(app) as client:
            await _login(client)
            # Upper bound before any row was created → empty.
            page = await client.get(
                f"/admin/m/{_SLUG}/?filter_created_at_to=1900-01-01"
            )
        assert "No records." in page.text

    @pytest.mark.asyncio
    async def test_date_range_includes_today(
        self, app_rf: tuple[FastAPI, dict]
    ) -> None:
        app, _ = app_rf
        async with _client(app) as client:
            await _login(client)
            page = await client.get(
                f"/admin/m/{_SLUG}/?filter_created_at_from=2000-01-01"
            )
        assert "<td>a1</td>" in page.text
        assert "<td>b1</td>" in page.text
