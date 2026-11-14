"""``MERCADOPAGO_BASE_URL``: aiming a development service at the simulator.

The access token travels to whatever host the field names, so the default is
the provider's and anything else is a production violation.
"""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.integrations.payment.mercado_pago import DEFAULT_BASE_URL
from tempest_fastapi_sdk.settings import MercadoPagoSettings


def test_the_default_is_the_provider_host() -> None:
    """Unset, the client points where it always did."""
    settings = MercadoPagoSettings(_env_file=None)

    assert settings.mercado_pago_kwargs()["base_url"] == DEFAULT_BASE_URL
    assert settings.production_violations() == []


def test_the_field_moves_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env var reaches the ``HTTPClient`` kwargs.

    Args:
        monkeypatch (pytest.MonkeyPatch): Sets the variable.
    """
    monkeypatch.setenv("MERCADOPAGO_BASE_URL", "http://127.0.0.1:8090")

    settings = MercadoPagoSettings(_env_file=None)

    assert settings.mercado_pago_kwargs()["base_url"] == "http://127.0.0.1:8090"
    assert settings.production_violations() == [
        "MERCADOPAGO_BASE_URL: not the Mercado Pago API host "
        "(the access token is sent there)"
    ]


def test_a_trailing_slash_is_still_the_provider() -> None:
    """``https://api.mercadopago.com/`` is not a violation."""
    settings = MercadoPagoSettings(
        _env_file=None, MERCADOPAGO_BASE_URL=f"{DEFAULT_BASE_URL}/"
    )

    assert settings.production_violations() == []


def test_production_refuses_another_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ENV=production`` with the simulator's host fails to build.

    Args:
        monkeypatch (pytest.MonkeyPatch): Sets the variables.
    """
    from pydantic import ValidationError

    from tempest_fastapi_sdk.settings import EnvironmentSettings

    class Settings(EnvironmentSettings, MercadoPagoSettings):
        """A service composing the environment gate."""

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("MERCADOPAGO_BASE_URL", "http://127.0.0.1:8090")

    with pytest.raises(ValidationError, match="MERCADOPAGO_BASE_URL"):
        Settings(_env_file=None)
