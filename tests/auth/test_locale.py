"""Tests for the auth i18n layer — locale resolution, localized emails
and backend HTML pages (``AUTH_DEFAULT_LOCALE`` + ``Accept-Language``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.auth.locale import (
    auth_email_message,
    auth_page_message,
    format_expires_at,
    negotiate_locale,
    normalize_locale,
)
from tempest_fastapi_sdk.auth.page_renderer import render_auth_page
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.utils.email import EmailUtils


class _LocaleUser(BaseUserModel):
    __tablename__ = "auth_locale_users"


_LocaleUserToken = make_user_token_model(
    user_table="auth_locale_users",
    tablename="auth_locale_user_tokens",
    class_name="_LocaleUserToken",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestNormalizeLocale:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("pt-BR", "pt-BR"),
            ("PT-BR", "pt-BR"),
            ("pt_br", "pt-BR"),
            ("ptbr", "pt-BR"),
            ("pt", "pt-BR"),
            ("pt-PT", "pt-BR"),
            ("en-US", "en-US"),
            ("EN_us", "en-US"),
            ("enus", "en-US"),
            ("en", "en-US"),
            ("en-GB", "en-US"),
            ("", "pt-BR"),
            (None, "pt-BR"),
            ("klingon", "pt-BR"),
        ],
    )
    def test_normalizes_loose_values(self, raw: str | None, expected: str) -> None:
        assert normalize_locale(raw) == expected

    def test_custom_default(self) -> None:
        assert normalize_locale("zz", default="en-US") == "en-US"


class TestNegotiateLocale:
    def test_picks_first_supported_tag(self) -> None:
        assert negotiate_locale("en-US,en;q=0.9,pt;q=0.8") == "en-US"

    def test_primary_subtag_fallback(self) -> None:
        assert negotiate_locale("en-GB;q=0.9") == "en-US"

    def test_falls_back_to_default_when_absent(self) -> None:
        assert negotiate_locale(None) == "pt-BR"
        assert negotiate_locale("") == "pt-BR"

    def test_respects_quality_order(self) -> None:
        # pt wins on q even though en appears first.
        assert negotiate_locale("en;q=0.3,pt-BR;q=0.9") == "pt-BR"

    def test_custom_default_when_nothing_matches(self) -> None:
        assert negotiate_locale("fr,de;q=0.5", default="en-US") == "en-US"


class TestFormatExpiresAt:
    def _dt(self) -> datetime:
        return datetime(2026, 6, 21, 23, 25, 49, 742054, tzinfo=UTC)

    def test_ptbr_drops_seconds_and_micros(self) -> None:
        assert format_expires_at(self._dt(), "pt-BR") == "21/06/2026 23:25 (UTC)"

    def test_enus_drops_seconds_and_micros(self) -> None:
        assert format_expires_at(self._dt(), "en-US") == "2026-06-21 23:25 (UTC)"

    def test_no_raw_microseconds_leak(self) -> None:
        rendered = format_expires_at(self._dt(), "pt-BR")
        assert "742054" not in rendered
        assert ":49" not in rendered


class TestMessageCatalogs:
    def test_email_messages_localized(self) -> None:
        assert auth_email_message("pt-BR", "activation_subject") == "Ative sua conta"
        assert (
            auth_email_message("en-US", "activation_subject") == "Activate your account"
        )

    def test_email_body_has_url_placeholder(self) -> None:
        body = auth_email_message("pt-BR", "password_reset_body")
        assert "{url}" in body
        assert body.format(url="https://x/y") == (
            "Abra este link para redefinir sua senha: https://x/y"
        )

    def test_page_message_localized(self) -> None:
        assert (
            auth_page_message("pt-BR", "passwords_do_not_match")
            == "As senhas não coincidem."
        )
        assert (
            auth_page_message("en-US", "passwords_do_not_match")
            == "Passwords do not match."
        )

    def test_unknown_locale_falls_back_to_default(self) -> None:
        assert auth_email_message("xx", "activation_subject") == "Ative sua conta"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettingsDefaultLocale:
    def test_default_is_ptbr(self) -> None:
        assert AuthSettings().AUTH_DEFAULT_LOCALE == "pt-BR"

    def test_value_is_normalized(self) -> None:
        assert AuthSettings(AUTH_DEFAULT_LOCALE="EN_us").AUTH_DEFAULT_LOCALE == "en-US"
        assert AuthSettings(AUTH_DEFAULT_LOCALE="ptbr").AUTH_DEFAULT_LOCALE == "pt-BR"


# ---------------------------------------------------------------------------
# EmailUtils template resolution (bundled per-locale templates)
# ---------------------------------------------------------------------------


def _email() -> EmailUtils:
    return EmailUtils(host="localhost", port=25, from_addr="noreply@x.com")


class TestEmailUtilsLocale:
    def _ctx(self) -> dict[str, object]:
        from types import SimpleNamespace

        return {
            "user": SimpleNamespace(name="Ana", email="ana@x.com"),
            "activation_url": "https://app/activate?token=abc",
            "reset_url": "https://app/reset?token=abc",
            "expires_at": datetime(2026, 6, 21, 23, 25, tzinfo=UTC),
            "expires_at_str": "21/06/2026 23:25 (UTC)",
        }

    def test_renders_bundled_ptbr_activation(self) -> None:
        html = _email().render_template("activation.html", self._ctx(), locale="pt-BR")
        assert "Ativar conta" in html
        assert 'lang="pt-BR"' in html
        assert "21/06/2026 23:25 (UTC)" in html

    def test_renders_bundled_enus_activation(self) -> None:
        html = _email().render_template("activation.html", self._ctx(), locale="en-US")
        assert "Activate account" in html
        assert 'lang="en"' in html

    def test_renders_bundled_ptbr_password_reset(self) -> None:
        html = _email().render_template(
            "password_reset.html", self._ctx(), locale="pt-BR"
        )
        assert "Redefinir senha" in html

    def test_no_raw_datetime_in_rendered_email(self) -> None:
        html = _email().render_template("activation.html", self._ctx(), locale="pt-BR")
        # The microsecond-bearing ISO form must never reach the email.
        assert "23:25:49" not in html


# ---------------------------------------------------------------------------
# render_auth_page locale resolution + project shadowing (backward compat)
# ---------------------------------------------------------------------------


class TestRenderAuthPageLocale:
    def test_bundled_ptbr_success_page(self) -> None:
        html = render_auth_page(
            "activation_success.html",
            {"user": None, "login_url": None},
            locale="pt-BR",
        )
        assert "Conta ativada" in html

    def test_bundled_enus_success_page(self) -> None:
        html = render_auth_page(
            "activation_success.html",
            {"user": None, "login_url": None},
            locale="en-US",
        )
        assert "Account activated" in html

    def test_legacy_flat_template_dir_still_resolves(self, tmp_path: object) -> None:
        # A project shipping a FLAT template_dir (pre-0.59 layout, no
        # locale subdir) must still override the bundled page.
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        (tmp_path / "activation_success.html").write_text(
            "<html>CUSTOM FLAT PAGE</html>", encoding="utf-8"
        )
        html = render_auth_page(
            "activation_success.html",
            {"user": None, "login_url": None},
            template_dir=tmp_path,
            locale="pt-BR",
        )
        assert "CUSTOM FLAT PAGE" in html

    def test_locale_subdir_in_template_dir_wins(self, tmp_path: object) -> None:
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        (tmp_path / "pt-BR").mkdir()
        (tmp_path / "pt-BR" / "activation_success.html").write_text(
            "<html>CUSTOM PTBR</html>", encoding="utf-8"
        )
        (tmp_path / "activation_success.html").write_text(
            "<html>CUSTOM FLAT</html>", encoding="utf-8"
        )
        html = render_auth_page(
            "activation_success.html",
            {"user": None, "login_url": None},
            template_dir=tmp_path,
            locale="pt-BR",
        )
        assert "CUSTOM PTBR" in html


# ---------------------------------------------------------------------------
# Service-level localized email send
# ---------------------------------------------------------------------------


class _RecordingEmail:
    """Captures the subject/body/locale the service hands to ``send``."""

    def __init__(self) -> None:
        self.subject: str | None = None
        self.body: str | None = None
        self.html: str | None = None
        self.locale: str | None = None

    def render_template(
        self,
        template: str,
        context: dict[str, object],
        *,
        locale: str | None = None,
    ) -> str:
        self.locale = locale
        # Echo the formatted expiry so the test can assert it.
        return f"<html>{context.get('expires_at_str')}</html>"

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        html: str | None = None,
    ) -> None:
        self.subject = subject
        self.body = body
        self.html = html


def _service(*, locale: str, email: object) -> UserAuthService:
    return UserAuthService(
        user_model=_LocaleUser,
        token_model=_LocaleUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=False,
            AUTH_RETURN_TOKEN_IN_RESPONSE=False,
            AUTH_DEFAULT_LOCALE=locale,
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=email,  # type: ignore[arg-type]
    )


class TestServiceLocalizedEmail:
    async def test_ptbr_activation_email(self, session: AsyncSession) -> None:
        email = _RecordingEmail()
        service = _service(locale="pt-BR", email=email)
        await service.signup(session, email="a@x.com", password="strong-pass-12")
        await session.commit()
        assert email.subject == "Ative sua conta"
        assert email.body is not None and email.body.startswith(
            "Abra este link para ativar sua conta:"
        )
        assert email.locale == "pt-BR"
        # The HTML carries the short, no-seconds expiry string.
        assert email.html is not None and "(UTC)" in email.html
        assert ":49" not in email.html

    async def test_enus_activation_email(self, session: AsyncSession) -> None:
        email = _RecordingEmail()
        service = _service(locale="en-US", email=email)
        await service.signup(session, email="b@x.com", password="strong-pass-12")
        await session.commit()
        assert email.subject == "Activate your account"
        assert email.locale == "en-US"

    async def test_reset_email_localized(self, session: AsyncSession) -> None:
        email = _RecordingEmail()
        service = _service(locale="pt-BR", email=email)
        await service.signup(session, email="c@x.com", password="strong-pass-12")
        await session.commit()
        # With email wired + AUTH_RETURN_TOKEN_IN_RESPONSE=False the token
        # lives only in the email, so the method returns None — what we
        # assert is the localized email the service handed to ``send``.
        await service.request_password_reset(session, email="c@x.com")
        await session.commit()
        assert email.subject == "Redefina sua senha"
        # The formatted expiry (no seconds) must be in the rendered HTML.
        assert email.html is not None and "(UTC)" in email.html
        assert ":49" not in email.html


# ---------------------------------------------------------------------------
# Backend HTML pages negotiate Accept-Language
# ---------------------------------------------------------------------------


class TestBackendPageNegotiation:
    async def _app_and_token(
        self, session: AsyncSession, *, default_locale: str
    ) -> tuple[FastAPI, str]:
        service = UserAuthService(
            user_model=_LocaleUser,
            token_model=_LocaleUserToken,  # type: ignore[arg-type]
            auth_settings=AuthSettings(
                AUTH_AUTO_ACTIVATE=False,
                AUTH_RETURN_TOKEN_IN_RESPONSE=True,
                AUTH_BACKEND_LINKS=True,
                AUTH_DEFAULT_LOCALE=default_locale,
            ),
            jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        )
        _user, activation = await service.signup(
            session, email="page@x.com", password="strong-pass-12"
        )
        await session.commit()
        assert activation is not None

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))
        return app, activation.token

    async def test_accept_language_ptbr(self, session: AsyncSession) -> None:
        app, token = await self._app_and_token(session, default_locale="en-US")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get(
                f"/auth/activate/{token}",
                headers={"Accept-Language": "pt-BR,pt;q=0.9"},
            )
        assert r.status_code == 200, r.text
        assert "Conta ativada" in r.text

    async def test_accept_language_enus(self, session: AsyncSession) -> None:
        # Re-signup needed since the token above was consumed; use a
        # fresh session-scoped app with default pt-BR to prove the
        # header overrides the configured default.
        app, token = await self._app_and_token(session, default_locale="pt-BR")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get(
                f"/auth/activate/{token}",
                headers={"Accept-Language": "en-US,en;q=0.9"},
            )
        assert r.status_code == 200, r.text
        assert "Account activated" in r.text

    async def test_falls_back_to_default_locale_without_header(
        self, session: AsyncSession
    ) -> None:
        app, token = await self._app_and_token(session, default_locale="pt-BR")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get(f"/auth/activate/{token}")
        assert r.status_code == 200, r.text
        assert "Conta ativada" in r.text
