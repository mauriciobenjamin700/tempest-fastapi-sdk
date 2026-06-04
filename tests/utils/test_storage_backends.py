"""Tests for the upload storage backend protocol + implementations."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    FileTooLargeException,
    InvalidFileTypeException,
    LocalUploadStorage,
    MinIOUploadStorage,
    UploadResult,
    UploadStorage,
    UploadUtils,
)


async def _stream(payload: bytes, chunk: int = 4):
    for i in range(0, len(payload), chunk):
        yield payload[i : i + chunk]


class TestLocalUploadStorage:
    async def test_write_persists_and_returns_path(self, tmp_path: Path) -> None:
        storage = LocalUploadStorage(tmp_path)
        result = await storage.write_stream("a.bin", _stream(b"hello"))
        assert isinstance(result, UploadResult)
        assert result.key == "a.bin"
        assert result.size == 5
        assert result.path == (tmp_path / "a.bin").resolve()
        assert result.path.read_bytes() == b"hello"
        assert result.url is None

    async def test_write_rejects_traversal(self, tmp_path: Path) -> None:
        storage = LocalUploadStorage(tmp_path)
        with pytest.raises(InvalidFileTypeException):
            await storage.write_stream("../escape.bin", _stream(b"x"))

    async def test_write_enforces_max_size(self, tmp_path: Path) -> None:
        storage = LocalUploadStorage(tmp_path)
        with pytest.raises(FileTooLargeException):
            await storage.write_stream(
                "big.bin",
                _stream(b"a" * 100),
                max_size_bytes=10,
            )
        assert not (tmp_path / "big.bin").exists()

    async def test_validator_rejects_first_chunk(self, tmp_path: Path) -> None:
        storage = LocalUploadStorage(tmp_path)
        with pytest.raises(InvalidFileTypeException):
            await storage.write_stream(
                "bad.bin",
                _stream(b"reject me"),
                validator=lambda _: False,
            )
        assert not (tmp_path / "bad.bin").exists()

    async def test_delete_existing(self, tmp_path: Path) -> None:
        storage = LocalUploadStorage(tmp_path)
        await storage.write_stream("k.bin", _stream(b"x"))
        assert await storage.delete("k.bin") is True
        assert await storage.delete("k.bin") is False

    async def test_exists(self, tmp_path: Path) -> None:
        storage = LocalUploadStorage(tmp_path)
        assert await storage.exists("k") is False
        await storage.write_stream("k", _stream(b"x"))
        assert await storage.exists("k") is True

    async def test_presigned_url_returns_none(self, tmp_path: Path) -> None:
        storage = LocalUploadStorage(tmp_path)
        assert await storage.presigned_url("k") is None

    def test_satisfies_protocol(self, tmp_path: Path) -> None:
        storage = LocalUploadStorage(tmp_path)
        assert isinstance(storage, UploadStorage)


class _FakeMinioObject:
    def __init__(self, *, key: str, data: bytes, content_type: str) -> None:
        self.bucket_name: str = "uploads"
        self.object_name: str = key
        self.size: int = len(data)
        self.etag: str = f'"etag-{key}"'
        self.content_type: str = content_type
        self.last_modified: Any = None
        self.metadata: dict[str, str] = {}
        self._data: bytes = data


class _FakeMinioInline:
    """Compact stand-in covering only the methods MinIOUploadStorage uses."""

    def __init__(self) -> None:
        self.objects: dict[str, _FakeMinioObject] = {}

    def bucket_exists(self, bucket: str) -> bool:
        del bucket
        return True

    def put_object(
        self,
        bucket: str,
        key: str,
        data: Any,
        length: int,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        part_size: int = 0,
    ) -> Any:
        del bucket, length, metadata, part_size
        payload = data.read()
        self.objects[key] = _FakeMinioObject(
            key=key,
            data=payload,
            content_type=content_type,
        )
        return type("R", (), {"etag": f'"etag-{key}"'})()

    def stat_object(self, bucket: str, key: str) -> _FakeMinioObject:
        del bucket
        return self.objects[key]

    def remove_object(
        self,
        bucket: str,
        key: str,
        version_id: str | None = None,
    ) -> None:
        del bucket, version_id
        self.objects.pop(key, None)

    def presigned_get_object(
        self,
        bucket: str,
        key: str,
        expires: timedelta,
    ) -> str:
        return f"https://fake/{bucket}/{key}?exp={int(expires.total_seconds())}"


@pytest.fixture
def minio_client(monkeypatch: pytest.MonkeyPatch) -> AsyncMinIOClient:
    fake = _FakeMinioInline()
    monkeypatch.setattr("minio.Minio", lambda *a, **kw: fake)
    return AsyncMinIOClient(
        endpoint="x",
        access_key="ak",
        secret_key="sk",
        default_bucket="uploads",
    )


class TestMinIOUploadStorage:
    async def test_write_via_client(self, minio_client: AsyncMinIOClient) -> None:
        storage = MinIOUploadStorage(minio_client)
        result = await storage.write_stream(
            "x.bin",
            _stream(b"abcdefgh"),
            content_type="application/octet-stream",
        )
        assert result.key == "x.bin"
        assert result.size == 8
        assert result.path is None
        assert result.url is not None and "x.bin" in result.url

    async def test_write_enforces_size(self, minio_client: AsyncMinIOClient) -> None:
        storage = MinIOUploadStorage(minio_client)
        with pytest.raises(FileTooLargeException):
            await storage.write_stream(
                "big",
                _stream(b"a" * 50, chunk=10),
                max_size_bytes=20,
            )

    async def test_delete_then_exists(self, minio_client: AsyncMinIOClient) -> None:
        storage = MinIOUploadStorage(minio_client)
        await storage.write_stream("k", _stream(b"x"))
        assert await storage.exists("k") is True
        await storage.delete("k")
        assert await storage.exists("k") is False

    async def test_presigned_url(self, minio_client: AsyncMinIOClient) -> None:
        storage = MinIOUploadStorage(minio_client)
        await storage.write_stream("k", _stream(b"x"))
        url = await storage.presigned_url("k")
        assert url and "k" in url

    def test_satisfies_protocol(self, minio_client: AsyncMinIOClient) -> None:
        storage = MinIOUploadStorage(minio_client)
        assert isinstance(storage, UploadStorage)


class TestUploadUtilsBackendIntegration:
    async def test_save_with_minio_storage(
        self,
        tmp_path: Path,
        minio_client: AsyncMinIOClient,
    ) -> None:
        utils = UploadUtils(tmp_path)
        storage = MinIOUploadStorage(minio_client)
        upload = UploadFile(
            file=BytesIO(b"\x89PNG\r\n\x1a\nrest"),
            filename="logo.png",
            headers=Headers({"content-type": "image/png"}),
        )
        key = await utils.save(upload, storage=storage, filename="logo.png")
        assert str(key) == "logo.png"
        # Local disk untouched.
        assert list(tmp_path.iterdir()) == []

    async def test_save_default_backend_still_writes_locally(
        self, tmp_path: Path
    ) -> None:
        utils = UploadUtils(tmp_path)
        upload = UploadFile(
            file=BytesIO(b"hi"),
            filename="x.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        path = await utils.save(upload, filename="x.txt")
        assert path.exists()
        assert path.read_bytes() == b"hi"
