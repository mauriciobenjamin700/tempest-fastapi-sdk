"""Tests for the typed admin theming layer (``AdminTheme``)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AdminSite,
    AdminTheme,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)

SECRET = "x" * 48


class TestAdminThemeUnit:
    def test_default_is_a_noop_block(self) -> None:
        css = AdminTheme().to_css()
        assert "--tempest-accent: #2563eb;" in css
        assert "--tempest-bg: #0f172a;" in css
        # No font / dark-mode rules unless asked for.
        assert "font-family" not in css
        assert "--tempest-page-bg" not in css

    def test_custom_colors_render(self) -> None:
        css = AdminTheme(accent="#7c3aed", header_bg="#1e1b4b").to_css()
        assert "--tempest-accent: #7c3aed;" in css
        assert "--tempest-bg: #1e1b4b;" in css

    def test_sidebar_bg_falls_back_to_header_bg(self) -> None:
        css = AdminTheme(header_bg="#111111").to_css()
        assert "--tempest-bg-soft: #111111;" in css

    def test_sidebar_bg_explicit(self) -> None:
        css = AdminTheme(header_bg="#111111", sidebar_bg="#222222").to_css()
        assert "--tempest-bg-soft: #222222;" in css

    def test_font_family_emits_var_and_body_rule(self) -> None:
        theme = AdminTheme(font_family="'Inter', sans-serif")
        assert theme.css_variables()["--tempest-font"] == "'Inter', sans-serif"
        assert "body { font-family: var(--tempest-font); }" in theme.to_css()

    def test_dark_mode_adds_surface_overrides(self) -> None:
        css = AdminTheme(dark_mode=True).to_css()
        assert "--tempest-page-bg: #0b1120;" in css
        assert "--tempest-fg: #e2e8f0;" in css

    def test_dark_mode_respects_explicit_page_bg(self) -> None:
        # An explicit page_bg wins — the dark surface block is skipped.
        css = AdminTheme(dark_mode=True, page_bg="#123456").to_css()
        assert "--tempest-page-bg: #123456;" in css
        assert "#0b1120" not in css

    @pytest.mark.parametrize("bad", ["</style>", "a{b}", 'x"y', "a<b", "a>b"])
    def test_forbidden_characters_raise(self, bad: str) -> None:
        with pytest.raises(ValueError, match="forbidden character"):
            AdminTheme(footer_text=bad)

    def test_default_site_has_stock_theme(self) -> None:
        site = AdminSite(title="X")
        assert isinstance(site.theme, AdminTheme)
        assert site.theme.accent == "#2563eb"


class _ThemeUser(BaseUserModel):
    __tablename__ = "admin_theme_users"


@pytest.fixture
async def themed_app() -> FastAPI:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    theme = AdminTheme(
        accent="#7c3aed",
        header_bg="#1e1b4b",
        logo_url="/admin/static/logo.svg",
        logo_alt="Servus",
        favicon_url="/admin/static/favicon.ico",
        footer_text="Servus | 2026",
        custom_css_url="/admin/static/custom.css",
        font_family="'Inter', sans-serif",
    )
    site = AdminSite(title="Themed Admin", theme=theme)
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(_ThemeUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app
    await db.drop_tables()
    await db.disconnect()


@pytest.mark.asyncio
async def test_login_page_injects_theme(themed_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=themed_app), base_url="http://test"
    ) as client:
        response = await client.get("/admin/login")
    assert response.status_code == 200
    html = response.text
    # Injected <style> override.
    assert "--tempest-accent: #7c3aed;" in html
    assert "body { font-family: var(--tempest-font); }" in html
    # Favicon + custom stylesheet links.
    assert '<link rel="icon" href="/admin/static/favicon.ico">' in html
    assert '<link rel="stylesheet" href="/admin/static/custom.css">' in html
    # Logo image instead of brand text.
    assert 'src="/admin/static/logo.svg"' in html
    assert 'alt="Servus"' in html
    # Custom footer.
    assert "Servus | 2026" in html
