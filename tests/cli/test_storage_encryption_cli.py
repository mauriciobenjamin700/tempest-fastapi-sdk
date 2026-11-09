"""``tempest storage encrypt-local`` and ``reencrypt-local``.

The commands run against a real ``LocalObjectStorageClient`` built from the
project's settings, over a store in ``tmp_path``: what they own is reading
``STORAGE_LOCAL_ENCRYPTION_KEYS``, walking every bucket, and the exit code
when there is no key to encrypt with.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.storage import (
    LOCAL_ENCRYPTION_MAGIC,
    LocalObjectStorageClient,
)

runner = CliRunner()

SETTINGS_SOURCE: str = """
from tempest_fastapi_sdk.settings import ServerSettings, StorageSettings


class Settings(ServerSettings, StorageSettings):
    pass


settings = Settings()
"""
"""Body of the scratch project's ``src/core/settings.py``."""

KEY_1: bytes = bytes(range(32))
"""First key."""

KEY_2: bytes = bytes(range(32, 64))
"""Second key, for rotation."""


def keys_env(*pairs: tuple[str, bytes]) -> str:
    """Render ``STORAGE_LOCAL_ENCRYPTION_KEYS`` for some keys.

    Args:
        *pairs (tuple[str, bytes]): Key id and raw key, in ring order.

    Returns:
        str: The JSON the environment carries.
    """
    return json.dumps({key_id: base64.b64encode(key).decode() for key_id, key in pairs})


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Stand inside a project whose local store lives in ``tmp_path/store``.

    Args:
        tmp_path (Path): Project root.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.

    Yields:
        Path: The store's root.
    """
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "settings.py").write_text(
        SETTINGS_SOURCE, encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("STORAGE_LOCAL_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("STORAGE_ENABLED", raising=False)
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    yield tmp_path / "store"
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]


def seed_plaintext(store: Path) -> LocalObjectStorageClient:
    """Write plaintext objects in two buckets.

    Args:
        store (Path): The store's root.

    Returns:
        LocalObjectStorageClient: The plaintext client that wrote them.
    """
    client = LocalObjectStorageClient(
        store, public_url="http://127.0.0.1:8000", signing_secret="s"
    )
    asyncio.run(client.put_object("a.txt", b"alpha", bucket="uploads"))
    asyncio.run(client.put_object("b/c.txt", b"bravo", bucket="docs"))
    return client


class TestEncryptLocal:
    def test_encrypts_every_bucket_once(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both buckets are converted, and a second run touches nothing."""
        client = seed_plaintext(project)
        monkeypatch.setenv("STORAGE_LOCAL_ENCRYPTION_KEYS", keys_env(("k1", KEY_1)))
        result = runner.invoke(app, ["storage", "encrypt-local"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "docs: 1 object(s) encrypted" in result.stdout
        assert "uploads: 1 object(s) encrypted" in result.stdout
        for key, bucket in (("a.txt", "uploads"), ("b/c.txt", "docs")):
            stored = client.object_path(key, bucket=bucket).read_bytes()
            assert stored.startswith(LOCAL_ENCRYPTION_MAGIC)
        again = runner.invoke(app, ["storage", "encrypt-local"])
        assert again.exit_code == 0, again.stdout + again.stderr
        assert "docs: 0 object(s) encrypted" in again.stdout

    def test_bucket_option_limits_the_run(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--bucket`` converts that bucket only."""
        client = seed_plaintext(project)
        monkeypatch.setenv("STORAGE_LOCAL_ENCRYPTION_KEYS", keys_env(("k1", KEY_1)))
        result = runner.invoke(app, ["storage", "encrypt-local", "--bucket", "docs"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "uploads" not in result.stdout
        assert client.object_path("a.txt", bucket="uploads").read_bytes() == b"alpha"

    def test_without_keys_exits_two(self, project: Path) -> None:
        """No key is a usage error, not a silent no-op."""
        seed_plaintext(project)
        result = runner.invoke(app, ["storage", "encrypt-local"])
        assert result.exit_code == 2
        assert "STORAGE_LOCAL_ENCRYPTION_KEYS" in result.stderr


class TestReencryptLocal:
    def test_moves_objects_to_the_first_key(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After the run the old key can leave the ring."""
        old = LocalObjectStorageClient(
            project,
            public_url="http://127.0.0.1:8000",
            signing_secret="s",
            encryption_keys={"k1": KEY_1},
        )
        asyncio.run(old.put_object("a.txt", b"alpha"))
        monkeypatch.setenv(
            "STORAGE_LOCAL_ENCRYPTION_KEYS", keys_env(("k2", KEY_2), ("k1", KEY_1))
        )
        result = runner.invoke(app, ["storage", "reencrypt-local"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "uploads: 1 object(s) re-encrypted" in result.stdout
        only_new = LocalObjectStorageClient(
            project,
            public_url="http://127.0.0.1:8000",
            signing_secret="s",
            encryption_keys={"k2": KEY_2},
        )

        async def read() -> bytes:
            stream = await only_new.stream_object("a.txt")
            return b"".join([chunk async for chunk in stream])

        assert asyncio.run(read()) == b"alpha"

    def test_unreadable_object_exits_one(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file whose key is gone stops the run with the error."""
        old = LocalObjectStorageClient(
            project,
            public_url="http://127.0.0.1:8000",
            signing_secret="s",
            encryption_keys={"k1": KEY_1},
        )
        asyncio.run(old.put_object("a.txt", b"alpha"))
        monkeypatch.setenv("STORAGE_LOCAL_ENCRYPTION_KEYS", keys_env(("k2", KEY_2)))
        result = runner.invoke(app, ["storage", "reencrypt-local"])
        assert result.exit_code == 1
        assert "StorageIntegrityException" in result.stderr
