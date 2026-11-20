"""Tests for admin rich list filters (enum / FK / date-range)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
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
from tempest_fastapi_sdk.testing import admin_login


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
    await admin_login(client, "root@example.com", "hunter2")


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


class Stamp(BaseModel):
    __tablename__ = "rf_stamp"
    title: Mapped[str] = mapped_column(String(64), nullable=False)


_STAMPS: dict[str, datetime] = {
    "late_20": datetime(2026, 11, 21, 1, 0, tzinfo=UTC),
    "last_second_20": datetime(2026, 11, 21, 2, 59, 59, 500_000, tzinfo=UTC),
    "early_21": datetime(2026, 11, 21, 3, 30, tzinfo=UTC),
    "utc_last_second_21": datetime(2026, 11, 21, 23, 59, 59, 500_000, tzinfo=UTC),
}
"""20/11 22:00, 20/11 23:59:59.5 and 21/11 00:30 in Sao Paulo (UTC-3), plus
21/11 23:59:59.5 in UTC, which a ``<= 23:59:59`` upper bound left out."""


async def _stamp_app(display_timezone: str | None) -> tuple[FastAPI, Any]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = FilterUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add_all(
            [Stamp(title=title, created_at=at) for title, at in _STAMPS.items()]
        )
        await session.commit()
    site = AdminSite(title="Test Admin", display_timezone=display_timezone)
    site.register(
        AdminModel(
            model=Stamp,
            list_display=[Stamp.id, Stamp.title],
            list_filter=[Stamp.created_at],
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
    return app, db


async def _titles_for_day(display_timezone: str | None, day: str) -> set[str]:
    app, db = await _stamp_app(display_timezone)
    try:
        async with _client(app) as client:
            await _login(client)
            page = await client.get(
                f"/admin/m/rf_stamp/?filter_created_at_from={day}"
                f"&filter_created_at_to={day}"
            )
    finally:
        await db.drop_tables()
        await db.disconnect()
    assert page.status_code == 200
    return {title for title in _STAMPS if f"<td>{title}</td>" in page.text}


class TestDateRangeInDisplayTimezone:
    """#800: the day of the filter is the day the list shows."""

    async def test_day_is_read_in_the_site_timezone(self) -> None:
        assert await _titles_for_day("America/Sao_Paulo", "2026-11-20") == {
            "late_20",
            "last_second_20",
        }
        assert await _titles_for_day("America/Sao_Paulo", "2026-11-21") == {
            "early_21",
            "utc_last_second_21",
        }

    async def test_without_a_timezone_the_day_is_utc(self) -> None:
        assert await _titles_for_day(None, "2026-11-20") == set()
        assert await _titles_for_day(None, "2026-11-21") == set(_STAMPS)

    async def test_filtered_export_uses_the_same_day(self) -> None:
        app, db = await _stamp_app("America/Sao_Paulo")
        try:
            async with _client(app) as client:
                await _login(client)
                export = await client.get(
                    "/admin/m/rf_stamp/export.csv"
                    "?filter_created_at_from=2026-11-20"
                    "&filter_created_at_to=2026-11-20"
                )
        finally:
            await db.drop_tables()
            await db.disconnect()
        assert export.status_code == 200
        rows = export.text.strip().splitlines()[1:]
        assert {row.split(",")[1] for row in rows} == {"late_20", "last_second_20"}
