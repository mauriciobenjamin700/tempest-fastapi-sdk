"""Production refuses e-mailed auth links a real user could not open.

Measured end to end before this guard: with ``ENV=production`` the
activation e-mail went out carrying ``http://localhost:3000/activate?token=...``
— the ``AuthSettings`` default — because nothing checked the templates.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import (
    AuthSettings,
    BaseAppSettings,
    EmailSettings,
    EnvironmentSettings,
)
from tempest_fastapi_sdk.settings.mixins import AUTH_EMAIL_LINK_FIELDS

GOOD: dict[str, str] = {
    "AUTH_ACTIVATION_URL_TEMPLATE": "https://app.acme.com.br/activate?token={token}",
    "AUTH_PASSWORD_RESET_URL_TEMPLATE": "https://app.acme.com.br/reset/{token}",
    "AUTH_EMAIL_CHANGE_URL_TEMPLATE": "https://app.acme.com.br/confirm-email/{token}",
    "AUTH_EMAIL_VERIFICATION_URL_TEMPLATE": "https://api.acme.com.br/auth/email-verify/{token}",
}
"""One acceptable value per e-mail link field."""


class Composed(EnvironmentSettings, AuthSettings, EmailSettings, BaseAppSettings):
    """Auth with e-mail composed, as a service sending links has it."""


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run away from any ``.env`` and inherited ``AUTH_``/``SMTP_`` variables.

    Args:
        tmp_path (Path): Empty working directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.
    """
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(("AUTH_", "SMTP_", "EMAIL_")) or name == "ENV":
            monkeypatch.delenv(name)


def link_violations(**values: str) -> list[str]:
    """Return the fields ``AuthSettings`` refuses, starting from good links.

    Args:
        **values (str): Template overrides.

    Returns:
        list[str]: The refused field names.
    """
    settings = AuthSettings(**{**GOOD, **values})  # type: ignore[arg-type]
    return [v.split(":")[0] for v in settings.production_violations()]


def test_the_field_list_is_every_mailed_template() -> None:
    """Every ``*_URL_TEMPLATE`` field of ``AuthSettings`` is checked."""
    declared = {f for f in AuthSettings.model_fields if f.endswith("_URL_TEMPLATE")}
    assert declared == set(AUTH_EMAIL_LINK_FIELDS) == set(GOOD)


def test_good_links_pass() -> None:
    assert link_violations() == []


def test_the_defaults_are_refused_once_per_field() -> None:
    assert sorted(
        v.split(":")[0] for v in AuthSettings().production_violations()
    ) == sorted(AUTH_EMAIL_LINK_FIELDS)


@pytest.mark.parametrize("field", AUTH_EMAIL_LINK_FIELDS)
@pytest.mark.parametrize(
    "template",
    [
        "https://localhost:3000/x?token={token}",
        "https://127.0.0.1/x/{token}",
        "https://[::1]:8000/x/{token}",
        "https://0.0.0.0/x/{token}",
        "https://app.example.com/x/{token}",
        "https://app.localhost/x/{token}",
        "https://acme.test/x/{token}",
        "/activate?token={token}",
        "http://app.acme.com.br/x?token={token}",
    ],
)
def test_each_field_refuses_local_reserved_or_plain_http(
    field: str, template: str
) -> None:
    assert link_violations(**{field: template}) == [field]


def test_message_names_the_field_never_the_value() -> None:
    settings = AuthSettings(
        **{
            **GOOD,
            "AUTH_ACTIVATION_URL_TEMPLATE": "http://secret-host.acme.com/{token}",
        }  # type: ignore[arg-type]
    )
    (message,) = settings.production_violations()
    assert message.startswith("AUTH_ACTIVATION_URL_TEMPLATE:")
    assert "secret-host" not in message


def test_production_boot_is_refused_with_default_links() -> None:
    with pytest.raises(ValidationError) as error:
        Composed(  # type: ignore[call-arg]
            _env_file=None,
            ENV="production",
            SMTP_HOST="smtp.gmail.com",
            SMTP_USERNAME="app@gmail.com",
            SMTP_FROM_ADDR="app@gmail.com",
        )
    assert "AUTH_ACTIVATION_URL_TEMPLATE" in str(error.value)
    assert "localhost:3000" not in str(error.value)


def test_email_off_skips_the_link_rule() -> None:
    """With e-mail switched off the link never leaves the process."""
    settings = Composed(_env_file=None, ENV="production", EMAIL_ENABLED=False)  # type: ignore[call-arg]
    assert settings.production_violations() == []


def test_production_boot_passes_with_real_links() -> None:
    settings = Composed(  # type: ignore[call-arg]
        _env_file=None,
        ENV="production",
        SMTP_HOST="smtp.gmail.com",
        SMTP_USERNAME="app@gmail.com",
        SMTP_FROM_ADDR="app@gmail.com",
        **GOOD,
    )
    assert settings.production_violations() == []


def test_return_token_in_response_is_refused() -> None:
    """The dev flag that puts the link in the response is a violation."""
    settings = AuthSettings(**GOOD, AUTH_RETURN_TOKEN_IN_RESPONSE=True)  # type: ignore[arg-type]
    assert [v.split(":")[0] for v in settings.production_violations()] == [
        "AUTH_RETURN_TOKEN_IN_RESPONSE"
    ]


def test_return_token_in_response_is_refused_with_email_off() -> None:
    """``EMAIL_ENABLED=false`` skips the link rule, never the flag rule."""
    settings = Composed(  # type: ignore[call-arg]
        _env_file=None,
        ENV="development",
        EMAIL_ENABLED=False,
        AUTH_RETURN_TOKEN_IN_RESPONSE=True,
    )
    assert [v.split(":")[0] for v in settings.production_violations()] == [
        "AUTH_RETURN_TOKEN_IN_RESPONSE"
    ]


def test_production_boot_is_refused_with_return_token_in_response() -> None:
    with pytest.raises(ValidationError) as error:
        Composed(  # type: ignore[call-arg]
            _env_file=None,
            ENV="production",
            SMTP_HOST="smtp.gmail.com",
            SMTP_USERNAME="app@gmail.com",
            SMTP_FROM_ADDR="app@gmail.com",
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            **GOOD,
        )
    assert "AUTH_RETURN_TOKEN_IN_RESPONSE" in str(error.value)
