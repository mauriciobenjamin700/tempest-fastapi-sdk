"""Tests for ``hide_input_in_errors`` and the ``EnvironmentSettings`` guard."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import (
    BaseAppSettings,
    CORSSettings,
    DatabaseSettings,
    EnvironmentSettings,
    JWTSettings,
    ServerSettings,
    StorageSettings,
    TaskIQSettings,
    TokenSettings,
)

KNOWN_SECRET: str = "s3cr3t-value-that-must-never-print-0123456789"
"""A secret placed in the environment; no error message may contain it."""

SECRET_MARKER: str = KNOWN_SECRET[:6]
"""Prefix searched for: pydantic elides the middle of a long ``input_value``,
so the full secret never appears even when the input leaks."""

PRODUCTION_READY: dict[str, str] = {
    "ENV": "production",
    "DATABASE_URL": "postgresql+asyncpg://app:pw@db:5432/app",
    "JWT_SECRET": KNOWN_SECRET,
    "CORS_ORIGINS": '["https://app.example.com"]',
    "TOKEN_SECRET": "internal-token-value",
    "TASKIQ_BROKER_URL": "redis://redis:6379/2",
    "STORAGE_ACCESS_KEY": "AKIAEXAMPLE",
    "STORAGE_SECRET_KEY": "storage-secret-value",
    "STORAGE_SECURE": "true",
}
"""An environment every composed mixin accepts in production."""


class Composed(
    EnvironmentSettings,
    ServerSettings,
    DatabaseSettings,
    JWTSettings,
    CORSSettings,
    TokenSettings,
    TaskIQSettings,
    StorageSettings,
    BaseAppSettings,
):
    """Every mixin that declares production violations."""


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test away from any ``.env`` and with a clean environment.

    Args:
        tmp_path (Path): Empty working directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.
    """
    monkeypatch.chdir(tmp_path)
    for name in (*PRODUCTION_READY, "SERVER_DEBUG", "SERVER_RELOAD", "SERVER_PORT"):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(("MINIO_", "STORAGE_")):
            monkeypatch.delenv(name)


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    """Export ``values`` into the environment.

    Args:
        monkeypatch (pytest.MonkeyPatch): Environment patcher.
        values (dict[str, str]): Variables to set.
    """
    for name, value in values.items():
        monkeypatch.setenv(name, value)


class TestHideInputInErrors:
    def test_config_flag_is_on(self) -> None:
        assert BaseAppSettings.model_config.get("hide_input_in_errors") is True

    def test_missing_field_does_not_echo_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing required field must not print the secrets beside it.

        On the pre-#446 config the ``missing`` error carried
        ``input_value=`` with the whole merged environment, so the JWT
        secret appeared in the message.
        """
        monkeypatch.setenv("JWT_SECRET", KNOWN_SECRET)

        class Settings(ServerSettings, JWTSettings, BaseAppSettings):
            """A service field nobody set."""

            SERVICE_API_KEY: str

        with pytest.raises(ValidationError) as caught:
            Settings()
        assert "SERVICE_API_KEY" in str(caught.value)
        assert SECRET_MARKER not in str(caught.value)

    def test_invalid_value_is_not_echoed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A malformed value can itself be the secret (a URL with a password)."""
        monkeypatch.setenv("SERVER_PORT", KNOWN_SECRET)
        with pytest.raises(ValidationError) as caught:
            ServerSettings()
        assert "SERVER_PORT" in str(caught.value)
        assert SECRET_MARKER not in str(caught.value)

    def test_production_error_does_not_echo_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(monkeypatch, {**PRODUCTION_READY, "SERVER_DEBUG": "true"})
        with pytest.raises(ValidationError) as caught:
            Composed()
        for value in PRODUCTION_READY.values():
            if value not in {"production", "true"}:
                assert value not in str(caught.value)


class TestEnvironmentGuard:
    def test_default_environment_is_development(self) -> None:
        assert Composed().ENV == "development"

    def test_development_builds_despite_violations(self) -> None:
        settings = Composed()
        assert settings.production_violations() != []

    def test_test_environment_accepts_every_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENV", "test")
        assert Composed().ENV == "test"

    def test_production_accepts_a_configured_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(monkeypatch, PRODUCTION_READY)
        settings = Composed()
        assert settings.ENV == "production"
        assert settings.production_violations() == []

    def test_production_refuses_every_default_and_lists_them_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("SERVER_DEBUG", "true")
        monkeypatch.setenv("SERVER_RELOAD", "true")
        monkeypatch.setenv("TASKIQ_BROKER_URL", "")
        with pytest.raises(ValidationError) as caught:
            Composed()
        message = str(caught.value)
        for field in (
            "SERVER_DEBUG",
            "SERVER_RELOAD",
            "DATABASE_URL",
            "JWT_SECRET",
            "CORS_ORIGINS",
            "TOKEN_SECRET",
            "TASKIQ_BROKER_URL",
            "STORAGE_ACCESS_KEY",
            "STORAGE_SECRET_KEY",
            "STORAGE_SECURE",
        ):
            assert f"- {field}:" in message

    @pytest.mark.parametrize(
        ("override", "field"),
        [
            ({"DATABASE_URL": "sqlite+aiosqlite:///./prod.db"}, "DATABASE_URL"),
            ({"JWT_SECRET": "change-me-change-me-change-me-32"}, "JWT_SECRET"),
            ({"CORS_ORIGINS": '["*"]'}, "CORS_ORIGINS"),
            ({"TOKEN_SECRET": ""}, "TOKEN_SECRET"),
            ({"TASKIQ_BROKER_URL": ""}, "TASKIQ_BROKER_URL"),
            ({"SERVER_DEBUG": "true"}, "SERVER_DEBUG"),
            ({"SERVER_RELOAD": "true"}, "SERVER_RELOAD"),
            ({"STORAGE_ACCESS_KEY": "minioadmin"}, "STORAGE_ACCESS_KEY"),
            ({"STORAGE_SECRET_KEY": "minioadmin"}, "STORAGE_SECRET_KEY"),
            ({"STORAGE_SECURE": "false"}, "STORAGE_SECURE"),
            ({"STORAGE_PUBLIC_SECURE": "false"}, "STORAGE_PUBLIC_SECURE"),
            ({"MINIO_SECRET_KEY": "minioadmin", "STORAGE_SECRET_KEY": ""}, None),
        ],
    )
    def test_each_development_value_is_refused_alone(
        self,
        monkeypatch: pytest.MonkeyPatch,
        override: dict[str, str],
        field: str | None,
    ) -> None:
        """One development value on an otherwise production environment.

        The last case reaches the storage default through the deprecated
        name: ``STORAGE_SECRET_KEY`` is removed, so ``MINIO_SECRET_KEY``
        is what the field reads.
        """
        environment = {**PRODUCTION_READY, **override}
        if field is None:
            del environment["STORAGE_SECRET_KEY"]
            field = "STORAGE_SECRET_KEY"
        _set(monkeypatch, environment)
        with pytest.raises(ValidationError) as caught:
            Composed()
        lines = [
            line for line in str(caught.value).splitlines() if line.startswith("- ")
        ]
        assert [line.split(":", 1)[0] for line in lines] == [f"- {field}"]

    def test_jwt_placeholder_follows_the_declared_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The check reads the mixin's default, not a copied literal."""
        placeholder = JWTSettings.model_fields["JWT_SECRET"].default
        monkeypatch.setattr(
            JWTSettings.model_fields["JWT_SECRET"], "default", KNOWN_SECRET
        )
        _set(monkeypatch, PRODUCTION_READY)
        with pytest.raises(ValidationError, match="JWT_SECRET"):
            Composed()
        monkeypatch.setenv("JWT_SECRET", placeholder)
        Composed()

    def test_guard_only_checks_the_mixins_composed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Small(EnvironmentSettings, ServerSettings, BaseAppSettings):
            """No database, no JWT, no storage."""

        monkeypatch.setenv("ENV", "production")
        assert Small().ENV == "production"

    def test_service_extends_and_waives_rules_cooperatively(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Service(EnvironmentSettings, DatabaseSettings, BaseAppSettings):
            """Accepts SQLite on purpose, adds a rule of its own."""

            PUBLIC_URL: str = "http://localhost:8000"

            def production_violations(self) -> list[str]:
                """Drop the SQLite rule and require an HTTPS public URL.

                Returns:
                    list[str]: The filtered parent list plus this rule.
                """
                violations = [
                    entry
                    for entry in super().production_violations()
                    if not entry.startswith("DATABASE_URL:")
                ]
                if not self.PUBLIC_URL.startswith("https://"):
                    violations.append("PUBLIC_URL: not HTTPS")
                return violations

        monkeypatch.setenv("ENV", "production")
        with pytest.raises(ValidationError) as caught:
            Service()
        assert "PUBLIC_URL: not HTTPS" in str(caught.value)
        assert "DATABASE_URL" not in str(caught.value)
        monkeypatch.setenv("PUBLIC_URL", "https://api.example.com")
        assert Service().ENV == "production"

    def test_unknown_environment_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENV", "staging")
        with pytest.raises(ValidationError, match="ENV"):
            Composed()
