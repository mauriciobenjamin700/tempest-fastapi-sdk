"""``GoogleDriveSettings`` and the ``TypedDict`` its mapper returns."""

from __future__ import annotations

import inspect
import json
from typing import get_type_hints

import pytest

from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveUploadStorage,
)
from tempest_fastapi_sdk.settings import GoogleDriveSettings, GoogleDriveStorageKwargs

NOT_FROM_SETTINGS: frozenset[str] = frozenset({"http_client", "scopes", "chunk_size"})
"""Factory keywords the settings do not model: runtime objects and tuning."""


@pytest.fixture
def credentials_json() -> str:
    """A minimal service-account key with a freshly generated RSA key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "client_email": "sa@demo.iam.gserviceaccount.com",
            "private_key": pem,
        }
    )


def test_typeddict_keys_match_the_factory_signature() -> None:
    keys = set(get_type_hints(GoogleDriveStorageKwargs))
    parameters = inspect.signature(GoogleDriveUploadStorage.from_service_account)
    keyword_only = {
        name
        for name, parameter in parameters.parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert keys <= keyword_only
    assert keyword_only - keys == NOT_FROM_SETTINGS


def test_empty_settings_map_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in GoogleDriveSettings.model_fields:
        monkeypatch.delenv(name, raising=False)
    settings = GoogleDriveSettings(_env_file=None)
    assert settings.google_drive_kwargs() == {
        "folder_id": "",
        "credentials_path": None,
        "credentials_json": None,
        "drive_id": None,
    }


async def test_kwargs_splat_into_the_factory(
    monkeypatch: pytest.MonkeyPatch, credentials_json: str
) -> None:
    monkeypatch.setenv("GOOGLE_DRIVE_CREDENTIALS_JSON", credentials_json)
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-xyz")
    monkeypatch.setenv("GOOGLE_DRIVE_DRIVE_ID", "0ADrive")
    monkeypatch.delenv("GOOGLE_DRIVE_CREDENTIALS_PATH", raising=False)
    settings = GoogleDriveSettings(_env_file=None)

    storage = GoogleDriveUploadStorage.from_service_account(
        **settings.google_drive_kwargs()
    )
    try:
        assert storage.folder_id == "folder-xyz"
        assert storage.drive_id == "0ADrive"
        assert (
            storage.client.principal_email
            == json.loads(credentials_json)["client_email"]
        )
    finally:
        await storage.aclose()
