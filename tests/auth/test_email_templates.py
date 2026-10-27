"""The bundled auth emails: one layout, the brand from settings (#645).

Every bundled email extends ``_email_layout.html`` and reads ``brand`` from
``UserAuthService._render_email``. Pinned: all five render in both
languages with and without a brand, the brand settings reach the markup,
a pale brand color gets dark button text and a dark link, and a value that
would break out of an attribute is refused at boot.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import EmailUtils, UserAuthService
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.utils.color import is_light, relative_luminance

URL: str = "https://app.acme.com.br/activate?token=abc"
USER: SimpleNamespace = SimpleNamespace(name="Ana", email="ana@acme.com.br")
EXPIRES: datetime = datetime(2026, 10, 28, 12, 0, tzinfo=UTC) + timedelta(hours=1)

CONTEXTS: dict[str, dict[str, Any]] = {
    "activation": {"activation_url": URL},
    "password_reset": {"reset_url": URL},
    "email_change": {"confirm_url": URL, "new_email": "ana.nova@acme.com.br"},
    "email_verification": {"verify_url": URL},
    "email_changed_notice": {
        "old_email": "ana@acme.com.br",
        "new_email": "ana.nova@acme.com.br",
    },
}


def mailer() -> EmailUtils:
    return EmailUtils("127.0.0.1", 1, from_addr="noreply@acme.com.br")


def service(**settings: Any) -> UserAuthService:
    """Build a service whose only job here is rendering.

    Args:
        **settings (Any): ``AuthSettings`` overrides.

    Returns:
        UserAuthService: The service.
    """
    return UserAuthService(
        user_model=object,  # type: ignore[arg-type]
        token_model=object,  # type: ignore[arg-type]
        auth_settings=AuthSettings(**settings),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=mailer(),
    )


def render(svc: UserAuthService, name: str, locale: str) -> str:
    context = {"user": USER, "expires_at": EXPIRES, "expires_at_str": "28/10/2026"}
    return svc._render_email(
        f"{name}.html", {**context, **CONTEXTS[name]}, locale=locale
    )


@pytest.mark.parametrize("locale", ["pt-BR", "en-US"])
@pytest.mark.parametrize("name", sorted(CONTEXTS))
class TestEveryEmail:
    def test_renders_on_the_layout(self, name: str, locale: str) -> None:
        html = render(service(), name, locale)

        assert 'name="color-scheme"' in html
        assert 'role="presentation"' in html
        assert "{{" not in html and "{%" not in html

    def test_carries_its_link_twice_when_it_has_one(
        self, name: str, locale: str
    ) -> None:
        html = render(service(), name, locale)

        expected = 0 if name == "email_changed_notice" else 2
        assert html.count(f'href="{URL}"') == expected

    def test_renders_without_a_brand_in_the_context(
        self, name: str, locale: str
    ) -> None:
        """A project calling ``EmailUtils.render_template`` itself has no ``brand``."""
        context = {"user": USER, "expires_at_str": "28/10/2026", **CONTEXTS[name]}

        html = mailer().render_template(f"{name}.html", context, locale=locale)

        assert "<h1" in html


class TestBrand:
    def test_default_has_no_header(self) -> None:
        html = render(service(), "activation", "pt-BR")

        assert "<img" not in html
        assert "#2563eb" in html

    def test_name_color_and_footer_reach_the_markup(self) -> None:
        svc = service(
            AUTH_EMAIL_BRAND_NAME="Acme",
            AUTH_EMAIL_BRAND_COLOR="#7c3aed",
            AUTH_EMAIL_FOOTER_TEXT="Acme Ltda",
        )

        html = render(svc, "password_reset", "en-US")

        assert ">Acme</td>" in html
        assert 'bgcolor="#7c3aed"' in html
        assert "color: #ffffff" in html
        assert "Acme Ltda" in html

    def test_a_logo_replaces_the_name_mark(self) -> None:
        svc = service(
            AUTH_EMAIL_BRAND_NAME="Acme",
            AUTH_EMAIL_BRAND_LOGO_URL="https://acme.com/logo.png",
        )

        html = render(svc, "activation", "pt-BR")

        assert '<img src="https://acme.com/logo.png" alt="Acme"' in html

    def test_a_pale_color_gets_dark_button_text_and_a_dark_link(self) -> None:
        html = render(service(AUTH_EMAIL_BRAND_COLOR="#facc15"), "activation", "pt-BR")

        assert "color: #0f172a" in html
        assert "color: #1f2937; text-decoration: underline" in html
        assert "color: #facc15; text-decoration: underline" not in html

    @pytest.mark.parametrize("color", ["red", "#12", "#1234567", "#fff; x:y", '#fff"'])
    def test_a_color_that_is_not_hex_is_refused(self, color: str) -> None:
        with pytest.raises(ValidationError, match="AUTH_EMAIL_BRAND_COLOR"):
            AuthSettings(AUTH_EMAIL_BRAND_COLOR=color)

    @pytest.mark.parametrize(
        "url", ["/static/logo.png", "javascript:alert(1)", "logo.png"]
    )
    def test_a_logo_that_is_not_absolute_http_is_refused(self, url: str) -> None:
        with pytest.raises(ValidationError, match="AUTH_EMAIL_BRAND_LOGO_URL"):
            AuthSettings(AUTH_EMAIL_BRAND_LOGO_URL=url)


class TestColor:
    @pytest.mark.parametrize(
        ("color", "luminance"), [("#ffffff", 1.0), ("#000", 0.0), ("#fff", 1.0)]
    )
    def test_relative_luminance_extremes(self, color: str, luminance: float) -> None:
        assert relative_luminance(color) == pytest.approx(luminance)

    @pytest.mark.parametrize(
        ("color", "light"),
        [
            ("#facc15", True),
            ("#2563eb", False),
            ("#7c3aed", False),
            ("rebeccapurple", False),
        ],
    )
    def test_is_light(self, color: str, light: bool) -> None:
        assert is_light(color) is light

    def test_not_hex_has_no_luminance(self) -> None:
        assert relative_luminance("rgb(0 0 0)") is None
