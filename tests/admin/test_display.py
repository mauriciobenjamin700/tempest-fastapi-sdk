"""How the admin shows a column name and a cell value."""

from __future__ import annotations

import enum
import hashlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

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
from tempest_fastapi_sdk.admin.dashboard import MetricCard, MetricPartition
from tempest_fastapi_sdk.admin.display import (
    cell_view,
    format_number,
    humanize_field,
    short_identity,
)
from tempest_fastapi_sdk.admin.router import STYLESHEET_VERSION_LENGTH
from tempest_fastapi_sdk.admin.theme import DARK_CHROME_BG, AdminTheme
from tempest_fastapi_sdk.testing import admin_login

SECRET: str = "x" * 48
CSS_PATH: Path = (
    Path(__file__).parents[2] / "tempest_fastapi_sdk" / "admin" / "static" / "admin.css"
)


class Color(enum.Enum):
    """An enum stored on a row."""

    DEEP_BLUE = "deep_blue"


class DisplayUser(BaseUserModel):
    """Signs in to the panel."""

    __tablename__ = "admin_display_users"


class DisplayCategory(BaseModel):
    """A model whose name pluralizes with ``-ies``."""

    __tablename__ = "admin_display_category"
    customer_email: Mapped[str] = mapped_column(String(64), nullable=False)


class TestHumanizeField:
    @pytest.mark.parametrize(
        ("key", "label"),
        [
            ("customer_email", "Customer email"),
            ("total_cents", "Total cents"),
            ("category_id", "Category"),
            ("id", "ID"),
            ("ip_address", "IP address"),
            ("is_active", "Is active"),
            ("PENDING", "Pending"),
            ("_private", "Private"),
        ],
    )
    def test_reads_like_a_label(self, key: str, label: str) -> None:
        assert humanize_field(key) == label

    def test_a_key_with_no_words_comes_back(self) -> None:
        assert humanize_field("__") == "__"


class TestCellView:
    def test_none_is_a_dash(self) -> None:
        view = cell_view(None)
        assert (view.kind, view.text) == ("none", "—")

    @pytest.mark.parametrize(
        ("value", "text", "state"), [(True, "Yes", "yes"), (False, "No", "no")]
    )
    def test_bool_is_a_badge(self, value: bool, text: str, state: str) -> None:
        view = cell_view(value)
        assert (view.kind, view.text, view.state) == ("bool", text, state)

    def test_datetime_drops_seconds_and_keeps_the_instant_in_the_title(self) -> None:
        moment = datetime(
            2026, 6, 15, 20, 0, 59, 123456, tzinfo=timezone(timedelta(hours=-3))
        )
        view = cell_view(moment)
        assert view.kind == "datetime"
        assert view.text == "2026-06-15 20:00"
        assert view.title == "2026-06-15T20:00:59.123456-03:00"
        assert view.suffix == "-03:00"

    def test_utc_and_naive_offsets(self) -> None:
        assert cell_view(datetime(2026, 1, 1, tzinfo=UTC)).suffix == "UTC"
        assert cell_view(datetime(2026, 1, 1)).suffix is None
        east = timezone(timedelta(hours=5, minutes=30))
        assert cell_view(datetime(2026, 1, 1, tzinfo=east)).suffix == "+05:30"

    def test_date_is_iso(self) -> None:
        assert cell_view(date(2026, 1, 2)).text == "2026-01-02"

    def test_uuid_keeps_the_prefix_and_the_whole_value_in_the_title(self) -> None:
        value = UUID("3aa4a64b-a258-45fe-85f5-5d55deba0313")
        view = cell_view(value)
        assert (view.kind, view.text, view.title) == ("uuid", "3aa4a64b", str(value))

    def test_enum_shows_the_member_name(self) -> None:
        view = cell_view(Color.DEEP_BLUE)
        assert (view.kind, view.text) == ("enum", "Deep blue")

    def test_number_and_text(self) -> None:
        assert cell_view(17.0).text == "17"
        assert cell_view(Decimal("9.90")).text == "9.90"
        assert cell_view("plain").kind == "text"


class TestFormatNumber:
    @pytest.mark.parametrize(
        ("value", "text"),
        [
            (17.0, "17"),
            (15, "15"),
            (2.5, "2.5"),
            (1.239, "1.24"),
            (True, "True"),
            ("x", "x"),
        ],
    )
    def test_whole_floats_lose_the_point(self, value: object, text: str) -> None:
        assert format_number(value) == text


class TestShortIdentity:
    def test_uuid_is_shortened(self) -> None:
        assert short_identity("3aa4a64b-a258-45fe-85f5-5d55deba0313") == "3aa4a64b"

    def test_anything_else_is_kept(self) -> None:
        assert short_identity("order-42") == "order-42"


class TestPlural:
    @pytest.mark.parametrize(
        ("name", "plural"),
        [
            ("Category", "Categories"),
            ("Day", "Days"),
            ("Address", "Addresses"),
            ("Box", "Boxes"),
            ("Batch", "Batches"),
            ("Order", "Orders"),
        ],
    )
    def test_regular_english_plurals(self, name: str, plural: str) -> None:
        admin = AdminModel(model=DisplayCategory, verbose_name=name)
        assert admin.get_verbose_name_plural() == plural


class TestChromeContrast:
    def test_light_default_takes_dark_text(self) -> None:
        variables = AdminTheme().css_variables()
        assert variables["--tempest-bg"] == "#ffffff"
        assert variables["--tempest-chrome-fg"] == "#0f172a"

    def test_dark_header_takes_light_text(self) -> None:
        variables = AdminTheme(header_bg="#1e1b4b").css_variables()
        assert variables["--tempest-chrome-fg"] == "#f8fafc"
        assert variables["--tempest-sidebar-fg"] == "#f8fafc"

    def test_sidebar_is_judged_on_its_own_background(self) -> None:
        variables = AdminTheme(header_bg="#ffffff", sidebar_bg="#000").css_variables()
        assert variables["--tempest-chrome-fg"] == "#0f172a"
        assert variables["--tempest-sidebar-fg"] == "#f8fafc"

    def test_explicit_header_fg_wins(self) -> None:
        variables = AdminTheme(
            header_bg="rebeccapurple", header_fg="#fde68a"
        ).css_variables()
        assert variables["--tempest-chrome-fg"] == "#fde68a"
        assert variables["--tempest-sidebar-fg-soft"] == "#fde68a"

    def test_dark_mode_darkens_the_default_chrome_only(self) -> None:
        assert (
            AdminTheme(dark_mode=True).css_variables()["--tempest-bg"] == DARK_CHROME_BG
        )
        pinned = AdminTheme(dark_mode=True, header_bg="#7c3aed").css_variables()
        assert pinned["--tempest-bg"] == "#7c3aed"

    def test_dark_mode_covers_the_card_surface(self) -> None:
        assert "--tempest-surface: #111827;" in AdminTheme(dark_mode=True).to_css()


@pytest.fixture
async def panel() -> AsyncIterator[tuple[FastAPI, str]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = DisplayUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        row = DisplayCategory(customer_email="ana@example.com")
        session.add_all([user, row])
        await session.commit()
        row_id = str(row.id)

    async def partition(_session: object) -> MetricPartition:
        return MetricPartition(segments=[("paid", 17.0)])

    site = AdminSite(
        title="Display Admin",
        dashboard_cards=[MetricCard("By status", partition)],
    )
    site.register(
        AdminModel(
            model=DisplayCategory,
            list_display=[
                DisplayCategory.customer_email,
                DisplayCategory.id,
                DisplayCategory.is_active,
                DisplayCategory.created_at,
            ],
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(DisplayUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, row_id
    await db.drop_tables()
    await db.disconnect()


@asynccontextmanager
async def _signed_in(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "root@example.com", "hunter2")
        yield client


class TestRenderedPanel:
    @pytest.mark.asyncio
    async def test_list_headers_are_labels_and_cells_are_formatted(
        self, panel: tuple[FastAPI, str]
    ) -> None:
        app, row_id = panel
        async with _signed_in(app) as client:
            page = await client.get("/admin/m/admin_display_category/")

        assert page.status_code == 200
        assert "<h1>Display Categories</h1>" in page.text
        assert "Customer email" in page.text
        assert ">customer_email<" not in page.text
        assert f'title="{row_id}">{row_id[:8]}</code>' in page.text
        assert 'tempest-admin-badge--yes">Yes<' in page.text
        assert not re.search(r">\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", page.text)

    @pytest.mark.asyncio
    async def test_detail_title_is_short_and_the_field_keeps_the_whole_id(
        self, panel: tuple[FastAPI, str]
    ) -> None:
        app, row_id = panel
        async with _signed_in(app) as client:
            page = await client.get(f"/admin/m/admin_display_category/{row_id}")

        assert page.status_code == 200
        assert f"<title>Display Category · {row_id[:8]}</title>" in page.text
        assert f">{row_id}</code>" in page.text
        assert "<dt>Customer email</dt>" in page.text

    @pytest.mark.asyncio
    async def test_dashboard_prints_whole_floats_as_integers(
        self, panel: tuple[FastAPI, str]
    ) -> None:
        app, _row_id = panel
        async with _signed_in(app) as client:
            page = await client.get("/admin/")

        assert 'part-value">17<' in page.text
        assert "17.0" not in page.text

    @pytest.mark.asyncio
    async def test_stylesheet_url_carries_the_file_digest(
        self, panel: tuple[FastAPI, str]
    ) -> None:
        app, _row_id = panel
        digest = hashlib.sha256(CSS_PATH.read_bytes()).hexdigest()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            page = await client.get("/admin/login")

        expected = f"/admin/static/admin.css?v={digest[:STYLESHEET_VERSION_LENGTH]}"
        assert f'href="{expected}"' in page.text

    @pytest.mark.asyncio
    async def test_guest_pages_hide_the_app_chrome(
        self, panel: tuple[FastAPI, str]
    ) -> None:
        app, _row_id = panel
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            page = await client.get("/admin/login")

        assert '<body class="tempest-admin--guest">' in page.text
        assert "tempest-admin-sidebar" not in page.text
