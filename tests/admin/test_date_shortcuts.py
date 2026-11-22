"""Date-range filter shortcuts ("Today", "7 days", ...) on the admin list."""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin.router import _date_shortcut_ranges
from tempest_fastapi_sdk.testing import admin_login

SECRET = "d" * 48
ZONE = "Pacific/Kiritimati"
SHORTCUT = re.compile(
    r'<a class="tempest-admin-list__shortcut[^"]*" href="([^"]+)"[^>]*>([^<]+)</a>'
)


class ShortcutUser(BaseUserModel):
    """Operator of the shortcut tests."""

    __tablename__ = "admin_shortcut_users"


class ShortcutEvent(BaseModel):
    """Row filtered by ``created_at``."""

    __tablename__ = "admin_shortcut_events"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="a")


LIST = f"/admin/m/{ShortcutEvent.__tablename__}/"


def test_ranges_mid_month() -> None:
    assert _date_shortcut_ranges(date(2026, 3, 15)) == [
        (date(2026, 3, 15), date(2026, 3, 15)),
        (date(2026, 3, 9), date(2026, 3, 15)),
        (date(2026, 2, 14), date(2026, 3, 15)),
        (date(2026, 3, 1), date(2026, 3, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
    ]


def test_ranges_cross_the_year_and_leap_february() -> None:
    january = _date_shortcut_ranges(date(2028, 1, 3))
    assert january[3] == (date(2028, 1, 1), date(2028, 1, 31))
    assert january[4] == (date(2027, 12, 1), date(2027, 12, 31))
    march = _date_shortcut_ranges(date(2028, 3, 31))
    assert march[4] == (date(2028, 2, 1), date(2028, 2, 29))


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Mount the admin with a date filter in a far-east zone and sign in.

    Yields:
        AsyncClient: The signed-in client.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    now = datetime.now(UTC)
    async with db.get_session_context() as session:
        user = ShortcutUser(email="op@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add_all(
            [
                user,
                ShortcutEvent(name="RecentEvent", created_at=now),
                ShortcutEvent(name="OldEvent", created_at=now - timedelta(days=40)),
            ]
        )
        await session.commit()
    site = AdminSite(title="Shortcuts")
    site.register(
        AdminModel(
            ShortcutEvent,
            list_filter=["created_at", "kind"],
            search_fields=["name"],
            display_timezone=ZONE,
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ShortcutUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        await admin_login(http, "op@example.com", "hunter2")
        yield http
    await db.drop_tables()
    await db.disconnect()


def _shortcuts(page: str) -> dict[str, dict[str, list[str]]]:
    """Map each shortcut label to its parsed query.

    Args:
        page (str): The list HTML.

    Returns:
        dict[str, dict[str, list[str]]]: Label to ``parse_qs`` of its link.
    """
    return {
        label: parse_qs(urlsplit(html.unescape(href)).query)
        for href, label in SHORTCUT.findall(page)
    }


@pytest.mark.asyncio
async def test_shortcuts_use_the_display_zone_and_keep_the_query(
    client: AsyncClient,
) -> None:
    before = datetime.now(ZoneInfo(ZONE)).date()
    page = await client.get(LIST, params={"q": "Event", "filter_kind": "a", "page": 2})
    after = datetime.now(ZoneInfo(ZONE)).date()
    links = _shortcuts(page.text)
    assert list(links) == ["Today", "7 days", "30 days", "This month", "Last month"]
    today = links["Today"]
    assert today["filter_created_at_from"] == today["filter_created_at_to"]
    assert today["filter_created_at_from"][0] in {before.isoformat(), after.isoformat()}
    for query in links.values():
        assert query["q"] == ["Event"]
        assert query["filter_kind"] == ["a"]
        assert "page" not in query


@pytest.mark.asyncio
async def test_shortcut_filters_and_marks_itself_active(client: AsyncClient) -> None:
    page = await client.get(LIST)
    query = _shortcuts(page.text)["30 days"]
    filtered = await client.get(LIST, params={k: v[0] for k, v in query.items()})
    assert "RecentEvent" in filtered.text
    assert "OldEvent" not in filtered.text
    assert 'tempest-admin-list__shortcut--active" href=' in filtered.text
    active = re.search(
        r'tempest-admin-list__shortcut--active"[^>]*>([^<]+)</a>', filtered.text
    )
    assert active is not None and active.group(1) == "30 days"
