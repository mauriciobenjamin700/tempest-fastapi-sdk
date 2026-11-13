"""Tests for ``STORAGE_BUCKET_CREDENTIALS`` on ``StorageSettings``."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BucketRoutedMinIOClient,
    EnvironmentSettings,
    StorageSettings,
)

DOCUMENTS: dict[str, dict[str, str]] = {
    "documents": {"access_key": "DOCSKEY", "secret_key": "docs-very-secret"}
}
"""One bucket with its own credential, as the env var carries it."""


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run away from any ``.env`` with no ``MINIO_*``/``STORAGE_*`` set.

    Args:
        tmp_path (Path): Empty working directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.

    Returns:
        Path: The working directory.
    """
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(("MINIO_", "STORAGE_")):
            monkeypatch.delenv(name)
    return tmp_path


class TestParsing:
    def test_default_is_empty(self) -> None:
        assert StorageSettings().STORAGE_BUCKET_CREDENTIALS == {}

    def test_json_env_var_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STORAGE_BUCKET_CREDENTIALS", json.dumps(DOCUMENTS))
        credential = StorageSettings().STORAGE_BUCKET_CREDENTIALS["documents"]
        assert credential.access_key == "DOCSKEY"
        assert credential.secret_key.get_secret_value() == "docs-very-secret"

    def test_secret_out_of_repr_and_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("STORAGE_BUCKET_CREDENTIALS", json.dumps(DOCUMENTS))
        settings = StorageSettings()
        logging.getLogger("test").warning("settings: %r", settings)
        assert "docs-very-secret" not in repr(settings)
        assert "docs-very-secret" not in str(settings.model_dump())
        assert "docs-very-secret" not in caplog.text

    @pytest.mark.parametrize(
        "credential",
        [
            {"access_key": "DOCSKEY"},
            {"secret_key": "docs-very-secret"},
            {"access_key": "", "secret_key": "docs-very-secret"},
            {"access_key": "DOCSKEY", "secret_key": ""},
            {"access_key": "DOCSKEY", "secret": "docs-very-secret"},
        ],
    )
    def test_partial_credential_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, credential: dict[str, str]
    ) -> None:
        monkeypatch.setenv(
            "STORAGE_BUCKET_CREDENTIALS", json.dumps({"documents": credential})
        )
        with pytest.raises(ValidationError) as caught:
            StorageSettings()
        assert "STORAGE_BUCKET_CREDENTIALS" in str(caught.value)
        assert "docs-very-secret" not in str(caught.value)

    def test_empty_bucket_name_refused(self) -> None:
        with pytest.raises(ValidationError, match="empty bucket name"):
            StorageSettings(
                STORAGE_BUCKET_CREDENTIALS={"": DOCUMENTS["documents"]},
            )


class TestBuild:
    def test_without_credentials_builds_the_plain_client(self) -> None:
        client = StorageSettings().build_storage_client()
        assert type(client) is AsyncMinIOClient

    def test_with_credentials_builds_the_router(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORAGE_BUCKET_CREDENTIALS", json.dumps(DOCUMENTS))
        client = StorageSettings().build_storage_client()
        assert isinstance(client, BucketRoutedMinIOClient)
        assert client.routed_buckets == frozenset({"documents"})

    def test_storage_kwargs_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STORAGE_BUCKET_CREDENTIALS", json.dumps(DOCUMENTS))
        assert "bucket_credentials" not in StorageSettings().storage_kwargs()

    def test_disabled_storage_builds_a_refusing_router(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORAGE_BUCKET_CREDENTIALS", json.dumps(DOCUMENTS))
        monkeypatch.setenv("STORAGE_ENABLED", "false")
        client = StorageSettings().build_storage_client()
        assert isinstance(client, BucketRoutedMinIOClient)
        assert client.enabled is False


class TestProduction:
    def _settings(self, **overrides: object) -> StorageSettings:
        """Build S3 settings that pass production apart from ``overrides``."""
        values: dict[str, object] = {
            "STORAGE_ACCESS_KEY": "IMAGESKEY",
            "STORAGE_SECRET_KEY": "images-secret",
            "STORAGE_SECURE": True,
        }
        values.update(overrides)
        return StorageSettings(**values)  # type: ignore[arg-type]

    def test_real_credentials_pass(self) -> None:
        settings = self._settings(STORAGE_BUCKET_CREDENTIALS=DOCUMENTS)
        assert settings.production_violations() == []

    def test_default_secret_on_a_bucket_is_refused(self) -> None:
        settings = self._settings(
            STORAGE_BUCKET_CREDENTIALS={
                "documents": {"access_key": "DOCSKEY", "secret_key": "minioadmin"}
            }
        )
        assert settings.production_violations() == [
            "STORAGE_BUCKET_CREDENTIALS['documents']: still the MinIO default "
            "credential"
        ]

    def test_local_backend_with_credentials_is_refused(self) -> None:
        settings = StorageSettings(
            STORAGE_BACKEND="local",
            STORAGE_LOCAL_SIGNING_SECRET="a-long-random-production-secret",
            STORAGE_LOCAL_PUBLIC_URL="https://api.example.com",
            STORAGE_LOCAL_DIR="/var/lib/app/storage",
            STORAGE_LOCAL_ENCRYPTION_KEYS={
                "k1": "C8MnsQiqOFRJoZgg4kT0gvXzknnci8XsEHs3+emnLJ4="
            },
            STORAGE_BUCKET_CREDENTIALS=DOCUMENTS,  # type: ignore[arg-type]
        )
        assert settings.production_violations() == [
            "STORAGE_BUCKET_CREDENTIALS: set, but STORAGE_BACKEND=local never reads it"
        ]

    def test_production_boot_refuses_and_hides_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Composed(EnvironmentSettings, StorageSettings):
            """Environment guard plus storage, as a service composes them."""

        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("STORAGE_ACCESS_KEY", "IMAGESKEY")
        monkeypatch.setenv("STORAGE_SECRET_KEY", "images-secret")
        monkeypatch.setenv("STORAGE_SECURE", "true")
        monkeypatch.setenv(
            "STORAGE_BUCKET_CREDENTIALS",
            json.dumps(
                {"documents": {"access_key": "DOCS", "secret_key": "minioadmin"}}
            ),
        )
        with pytest.raises(ValidationError) as caught:
            Composed()
        assert "STORAGE_BUCKET_CREDENTIALS['documents']" in str(caught.value)
