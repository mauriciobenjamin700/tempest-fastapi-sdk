"""``STORAGE_LOCAL_ENCRYPTION_KEYS`` and the production notice about it.

Pinned: the JSON of base64 keys reaches the client decoded and in order, a
malformed key refuses to build without echoing the value, a store without
keys builds the same kwargs as before, and ``ENV=production`` with a local
store and no key boots with a ``WARNING`` instead of refusing.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import (
    EnvironmentSettings,
    LocalObjectStorageClient,
    StorageSettings,
)

KEY_1: bytes = bytes(range(32))
"""First test key."""

KEY_2: bytes = bytes(range(32, 64))
"""Second test key."""

PRODUCTION_LOCAL: dict[str, str] = {
    "ENV": "production",
    "STORAGE_BACKEND": "local",
    "STORAGE_LOCAL_SIGNING_SECRET": "a-long-random-production-secret",
    "STORAGE_LOCAL_PUBLIC_URL": "https://api.example.com",
    "STORAGE_LOCAL_DIR": "/var/lib/app/storage",
}
"""A local store that passes every production check."""


class ProductionSettings(EnvironmentSettings, StorageSettings):
    """Environment guard plus storage, as a service composes them."""


def encoded(key: bytes) -> str:
    """Base64-encode a key the way the environment carries it.

    Args:
        key (bytes): Raw key.

    Returns:
        str: Its base64.
    """
    return base64.b64encode(key).decode()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run away from any ``.env`` with no ``STORAGE_*``/``ENV`` set.

    Args:
        tmp_path (Path): Empty working directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.

    Returns:
        Path: The working directory.
    """
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(("MINIO_", "STORAGE_")) or name == "ENV":
            monkeypatch.delenv(name)
    return tmp_path


class TestKeys:
    def test_keys_reach_the_client_decoded_and_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first key in the JSON is the write key."""
        monkeypatch.setenv(
            "STORAGE_LOCAL_ENCRYPTION_KEYS",
            json.dumps({"k2": encoded(KEY_2), "k1": encoded(KEY_1)}),
        )
        monkeypatch.setenv("STORAGE_LOCAL_ALLOW_PLAINTEXT_READ", "true")
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        settings = StorageSettings()
        kwargs = settings.local_storage_kwargs()
        assert kwargs["encryption_keys"] == {"k2": KEY_2, "k1": KEY_1}
        assert list(kwargs["encryption_keys"]) == ["k2", "k1"]
        assert kwargs["allow_plaintext_read"] is True
        client = settings.build_storage_client()
        assert isinstance(client, LocalObjectStorageClient)
        assert client.encrypted
        assert client.write_key_id == "k2"
        assert client.allow_plaintext_read

    def test_no_keys_leaves_the_kwargs_as_they_were(self) -> None:
        """Without keys neither new kwarg appears."""
        kwargs = StorageSettings(
            STORAGE_LOCAL_ALLOW_PLAINTEXT_READ=True
        ).local_storage_kwargs()
        assert "encryption_keys" not in kwargs
        assert "allow_plaintext_read" not in kwargs

    def test_short_key_is_refused_without_echoing_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 31-byte key fails to build; the message names only the id."""
        short = encoded(b"\x07" * 31)
        monkeypatch.setenv("STORAGE_LOCAL_ENCRYPTION_KEYS", json.dumps({"prod": short}))
        with pytest.raises(ValidationError) as caught:
            StorageSettings()
        message = str(caught.value)
        assert "prod" in message
        assert "32 bytes" in message
        assert short not in message

    def test_non_base64_key_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A value that is not base64 fails to build."""
        monkeypatch.setenv(
            "STORAGE_LOCAL_ENCRYPTION_KEYS", json.dumps({"prod": "not base64!"})
        )
        with pytest.raises(ValidationError) as caught:
            StorageSettings()
        assert "not valid base64" in str(caught.value)
        assert "not base64!" not in str(caught.value)

    def test_field_examples_are_valid(self) -> None:
        """The documented example parses."""
        examples = StorageSettings.model_fields[
            "STORAGE_LOCAL_ENCRYPTION_KEYS"
        ].examples
        assert examples is not None
        for example in examples:
            StorageSettings(STORAGE_LOCAL_ENCRYPTION_KEYS=example)


class TestProductionNotice:
    def test_local_without_keys_warns_and_boots(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Not a violation: a service already in production keeps booting."""
        for name, value in PRODUCTION_LOCAL.items():
            monkeypatch.setenv(name, value)
        with caplog.at_level(logging.WARNING, logger="tempest_fastapi_sdk.settings"):
            ProductionSettings()
        notices = [
            record
            for record in caplog.records
            if "STORAGE_LOCAL_ENCRYPTION_KEYS" in record.getMessage()
        ]
        assert len(notices) == 1
        assert notices[0].levelno == logging.WARNING

    def test_local_with_keys_is_silent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """With a key there is nothing to warn about."""
        for name, value in PRODUCTION_LOCAL.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv(
            "STORAGE_LOCAL_ENCRYPTION_KEYS", json.dumps({"k1": encoded(KEY_1)})
        )
        with caplog.at_level(logging.WARNING, logger="tempest_fastapi_sdk.settings"):
            ProductionSettings()
        assert not [
            record
            for record in caplog.records
            if "STORAGE_LOCAL_ENCRYPTION_KEYS" in record.getMessage()
        ]

    def test_development_never_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The notice belongs to the production guard only."""
        monkeypatch.setenv("ENV", "development")
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        with caplog.at_level(logging.WARNING, logger="tempest_fastapi_sdk.settings"):
            ProductionSettings()
        assert not caplog.records
