"""Tests for tempest_fastapi_sdk.settings.base.BaseAppSettings."""

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import BaseAppSettings


class TestBaseAppSettings:
    def test_subclass_loads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class MySettings(BaseAppSettings):
            APP_NAME: str
            PORT: int

        monkeypatch.setenv("APP_NAME", "tempest")
        monkeypatch.setenv("PORT", "8000")

        settings = MySettings()
        assert settings.APP_NAME == "tempest"
        assert settings.PORT == 8000

    def test_settings_are_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class MySettings(BaseAppSettings):
            APP_NAME: str = "x"

        settings = MySettings()
        with pytest.raises(ValidationError):
            settings.APP_NAME = "y"  # type: ignore[misc]

    def test_extra_env_vars_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class MySettings(BaseAppSettings):
            APP_NAME: str

        monkeypatch.setenv("APP_NAME", "tempest")
        monkeypatch.setenv("UNKNOWN_VAR", "noise")
        settings = MySettings()
        assert settings.APP_NAME == "tempest"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class MySettings(BaseAppSettings):
            APP_NAME: str

        monkeypatch.setenv("APP_NAME", "  tempest  ")
        settings = MySettings()
        assert settings.APP_NAME == "tempest"
