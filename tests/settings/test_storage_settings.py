"""Tests for ``StorageSettings``, its ``MINIO_*`` fallback and the old class."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsError

from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseAppSettings,
    EnvironmentSettings,
    LocalObjectStorageClient,
    MinIOSettings,
    StorageSettings,
)
from tempest_fastapi_sdk.settings import reject_conflicting_env_aliases
from tempest_fastapi_sdk.settings.mixins import DEV_LOCAL_STORAGE_SIGNING_SECRET

RENAMES: dict[str, tuple[str, object]] = {
    "ENDPOINT": ("s3.amazonaws.com", "s3.amazonaws.com"),
    "ACCESS_KEY": ("AKIAEXAMPLE", "AKIAEXAMPLE"),
    "SECRET_KEY": ("secret-value", "secret-value"),
    "SECURE": ("true", True),
    "REGION": ("sa-east-1", "sa-east-1"),
    "DEFAULT_BUCKET": ("media", "media"),
    "PUBLIC_ENDPOINT": ("cdn.example.com", "cdn.example.com"),
    "PUBLIC_SECURE": ("true", True),
}
"""Field suffix → (raw environment value, value the field parses it to)."""


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run away from any ``.env`` with no ``MINIO_*``/``STORAGE_*`` set.

    Args:
        tmp_path (Path): Empty working directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.

    Returns:
        Path: The working directory, where a test may write a ``.env``.
    """
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(("MINIO_", "STORAGE_")):
            monkeypatch.delenv(name)
    return tmp_path


class TestRename:
    @pytest.mark.parametrize("suffix", sorted(RENAMES))
    def test_old_name_still_fills_the_field(
        self, monkeypatch: pytest.MonkeyPatch, suffix: str
    ) -> None:
        raw, parsed = RENAMES[suffix]
        monkeypatch.setenv(f"MINIO_{suffix}", raw)
        assert getattr(StorageSettings(), f"STORAGE_{suffix}") == parsed

    @pytest.mark.parametrize("suffix", sorted(RENAMES))
    def test_new_name_fills_the_field(
        self, monkeypatch: pytest.MonkeyPatch, suffix: str
    ) -> None:
        raw, parsed = RENAMES[suffix]
        monkeypatch.setenv(f"STORAGE_{suffix}", raw)
        assert getattr(StorageSettings(), f"STORAGE_{suffix}") == parsed

    def test_old_name_in_the_dotenv_file_still_works(self, isolated_env: Path) -> None:
        (isolated_env / ".env").write_text(
            "MINIO_ENDPOINT=minio.internal:9000\nMINIO_SECURE=true\n",
            encoding="utf-8",
        )
        settings = StorageSettings()
        assert settings.STORAGE_ENDPOINT == "minio.internal:9000"
        assert settings.STORAGE_SECURE is True

    def test_both_names_with_the_same_value_are_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MINIO_ENDPOINT", "s3.amazonaws.com")
        monkeypatch.setenv("STORAGE_ENDPOINT", "s3.amazonaws.com")
        assert StorageSettings().STORAGE_ENDPOINT == "s3.amazonaws.com"

    def test_both_names_with_different_values_are_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the check ``AliasChoices`` silently keeps the new name."""
        monkeypatch.setenv("MINIO_SECRET_KEY", "old-secret-value")
        monkeypatch.setenv("STORAGE_SECRET_KEY", "new-secret-value")
        with pytest.raises(SettingsError) as caught:
            StorageSettings()
        message = str(caught.value)
        assert "STORAGE_SECRET_KEY and MINIO_SECRET_KEY" in message
        assert "old-secret-value" not in message
        assert "new-secret-value" not in message

    def test_clash_across_environment_and_dotenv_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, isolated_env: Path
    ) -> None:
        (isolated_env / ".env").write_text(
            "MINIO_ENDPOINT=localhost:9000\n", encoding="utf-8"
        )
        monkeypatch.setenv("STORAGE_ENDPOINT", "minio:9000")
        with pytest.raises(SettingsError, match="STORAGE_ENDPOINT and MINIO_ENDPOINT"):
            StorageSettings()

    def test_every_clash_is_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIO_REGION", "us-east-1")
        monkeypatch.setenv("STORAGE_REGION", "sa-east-1")
        monkeypatch.setenv("MINIO_DEFAULT_BUCKET", "a")
        monkeypatch.setenv("STORAGE_DEFAULT_BUCKET", "b")
        with pytest.raises(SettingsError) as caught:
            StorageSettings()
        assert "STORAGE_REGION and MINIO_REGION" in str(caught.value)
        assert "STORAGE_DEFAULT_BUCKET and MINIO_DEFAULT_BUCKET" in str(caught.value)

    def test_composed_settings_inherit_the_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Settings(StorageSettings, BaseAppSettings):
            """A service composing the mixin."""

        monkeypatch.setenv("MINIO_ENDPOINT", "a:9000")
        monkeypatch.setenv("STORAGE_ENDPOINT", "b:9000")
        with pytest.raises(SettingsError):
            Settings()

    def test_every_deprecated_alias_is_a_validation_alias(self) -> None:
        """The mapping and the ``AliasChoices`` cannot drift apart."""
        for old, new in StorageSettings.DEPRECATED_ENV_ALIASES.items():
            alias = StorageSettings.model_fields[new].validation_alias
            choices = getattr(alias, "choices", [])
            assert choices == [new, old]

    def test_case_insensitive_source_folds_the_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A consumer turning ``case_sensitive`` off keeps the check."""
        from pydantic_settings import EnvSettingsSource, SettingsConfigDict

        class Loose(StorageSettings, BaseAppSettings):
            """Case-insensitive settings."""

            model_config = SettingsConfigDict(case_sensitive=False)

        monkeypatch.setenv("minio_endpoint", "a:9000")
        monkeypatch.setenv("STORAGE_ENDPOINT", "b:9000")
        with pytest.raises(SettingsError):
            reject_conflicting_env_aliases(Loose, (EnvSettingsSource(Loose),))


class TestKwargs:
    def test_minio_kwargs_matches_storage_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORAGE_ENDPOINT", "s3.amazonaws.com")
        settings = StorageSettings()
        assert settings.minio_kwargs() == settings.storage_kwargs()
        assert settings.storage_kwargs()["endpoint"] == "s3.amazonaws.com"


class TestDeprecatedClass:
    def test_composing_minio_settings_warns_at_the_class_statement(self) -> None:
        with pytest.warns(DeprecationWarning, match="compose StorageSettings") as rec:

            class Legacy(MinIOSettings, BaseAppSettings):
                """A service still on the old name."""

        assert rec[0].filename == __file__

    def test_instantiating_minio_settings_warns_at_the_call(self) -> None:
        with pytest.warns(DeprecationWarning, match="use StorageSettings") as rec:
            MinIOSettings()
        assert rec[0].filename == __file__

    def test_subclass_of_a_composed_class_does_not_warn_again(self) -> None:
        with pytest.warns(DeprecationWarning):

            class Legacy(MinIOSettings, BaseAppSettings):
                """Composes the old name."""

        with warnings.catch_warnings():
            warnings.simplefilter("error")

            class Child(Legacy):
                """Inherits it, does not list it."""

            Child()

    def test_old_attributes_and_env_vars_keep_working(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MINIO_ENDPOINT", "minio.internal:9000")
        monkeypatch.setenv("MINIO_DEFAULT_BUCKET", "media")
        monkeypatch.setenv("MINIO_PUBLIC_SECURE", "true")
        with pytest.warns(DeprecationWarning):

            class Legacy(MinIOSettings, BaseAppSettings):
                """A service still on the old name."""

        settings = Legacy()
        assert settings.MINIO_ENDPOINT == "minio.internal:9000"
        assert settings.STORAGE_ENDPOINT == "minio.internal:9000"
        assert settings.MINIO_DEFAULT_BUCKET == "media"
        assert settings.MINIO_ACCESS_KEY == "minioadmin"
        assert settings.MINIO_SECRET_KEY == "minioadmin"
        assert settings.MINIO_SECURE is False
        assert settings.MINIO_REGION == "us-east-1"
        assert settings.MINIO_PUBLIC_ENDPOINT is None
        assert settings.MINIO_PUBLIC_SECURE is True
        assert settings.minio_kwargs() == settings.storage_kwargs()

    def test_minio_settings_is_a_storage_settings(self) -> None:
        assert issubclass(MinIOSettings, StorageSettings)


class TestLocalBackend:
    def test_s3_is_the_default_backend(self) -> None:
        settings = StorageSettings()
        assert settings.STORAGE_BACKEND == "s3"
        assert isinstance(settings.build_storage_client(), AsyncMinIOClient)

    def test_local_backend_builds_the_local_client(
        self, monkeypatch: pytest.MonkeyPatch, isolated_env: Path
    ) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        monkeypatch.setenv("STORAGE_LOCAL_DIR", str(isolated_env / "store"))
        monkeypatch.setenv("STORAGE_LOCAL_PUBLIC_URL", "https://api.example.com")
        monkeypatch.setenv("STORAGE_LOCAL_ROUTE_PREFIX", "/api/storage")
        monkeypatch.setenv("STORAGE_LOCAL_MAX_UPLOAD_BYTES", "1024")
        monkeypatch.setenv("STORAGE_DEFAULT_BUCKET", "media")
        client = StorageSettings().build_storage_client()
        assert isinstance(client, LocalObjectStorageClient)
        assert client.base_dir == (isolated_env / "store").resolve()
        assert (client.public_url, client.route_prefix) == (
            "https://api.example.com",
            "/api/storage",
        )
        assert (client.default_bucket, client.max_upload_bytes) == ("media", 1024)

    def test_disabled_flag_reaches_the_local_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        monkeypatch.setenv("STORAGE_ENABLED", "false")
        assert StorageSettings().local_storage_kwargs()["enabled"] is False
        assert (
            "enabled"
            not in StorageSettings(STORAGE_ENABLED=True).local_storage_kwargs()
        )

    def test_unknown_backend_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STORAGE_BACKEND", "ftp")
        with pytest.raises(ValidationError):
            StorageSettings()

    def test_local_production_checks_replace_the_s3_ones(self) -> None:
        settings = StorageSettings(STORAGE_BACKEND="local")
        assert settings.production_violations() == [
            "STORAGE_LOCAL_SIGNING_SECRET: empty or still the development placeholder",
            "STORAGE_LOCAL_PUBLIC_URL: local links over plain HTTP",
            "STORAGE_LOCAL_DIR: relative path (use an absolute one)",
        ]

    def test_configured_local_backend_passes(self) -> None:
        settings = StorageSettings(
            STORAGE_BACKEND="local",
            STORAGE_LOCAL_SIGNING_SECRET="a-long-random-production-secret",
            STORAGE_LOCAL_PUBLIC_URL="https://api.example.com",
            STORAGE_LOCAL_DIR="/var/lib/app/storage",
        )
        assert settings.production_violations() == []

    def test_disabled_storage_skips_the_local_checks(self) -> None:
        settings = StorageSettings(STORAGE_BACKEND="local", STORAGE_ENABLED=False)
        assert settings.production_violations() == []

    def test_production_refuses_the_local_placeholder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Composed(EnvironmentSettings, StorageSettings):
            """Environment guard plus storage, as a service composes them."""

        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        monkeypatch.setenv("STORAGE_LOCAL_PUBLIC_URL", "https://api.example.com")
        monkeypatch.setenv("STORAGE_LOCAL_DIR", "/var/lib/app/storage")
        with pytest.raises(ValidationError) as caught:
            Composed()
        assert "STORAGE_LOCAL_SIGNING_SECRET" in str(caught.value)
        assert DEV_LOCAL_STORAGE_SIGNING_SECRET not in str(caught.value)
