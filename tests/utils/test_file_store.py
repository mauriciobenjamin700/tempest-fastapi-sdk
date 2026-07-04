"""Tests for tempest_fastapi_sdk.utils.file_store.FileStoreUtils."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.datastructures import Headers

from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    FileStoreUtils,
    InvalidFileTypeException,
    LocalUploadStorage,
    MinIOUploadStorage,
    NotFoundException,
    UploadStorage,
    UploadUtils,
)


def _make_upload(
    content: bytes,
    *,
    filename: str = "test.png",
    content_type: str = "image/png",
) -> UploadFile:
    """Build a FastAPI UploadFile backed by an in-memory buffer."""
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


class _FakeGetResponse:
    """Minimal stand-in for a ``minio`` get_object HTTP response."""

    def __init__(self, data: bytes) -> None:
        self._buffer: BytesIO = BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def close(self) -> None:
        self._buffer.close()

    def release_conn(self) -> None:
        pass


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
    """Compact stand-in covering only the methods the store exercises."""

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

    def get_object(self, bucket: str, key: str) -> Any:
        del bucket
        return _FakeGetResponse(self.objects[key]._data)

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
        return f"https://fake/{bucket}/{key}?get={int(expires.total_seconds())}"

    def presigned_put_object(
        self,
        bucket: str,
        key: str,
        expires: timedelta,
    ) -> str:
        return f"https://fake/{bucket}/{key}?put={int(expires.total_seconds())}"


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


class TestLocalBackend:
    def test_selects_local_backend(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path)
        assert store.client is None
        assert isinstance(store.backend, LocalUploadStorage)
        assert isinstance(store.uploader, UploadUtils)
        assert isinstance(store.backend, UploadStorage)

    def test_shares_one_backend_with_uploader(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path)
        assert store.uploader._storage is store.backend

    async def test_save_then_read_back(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path)
        key = await store.save(_make_upload(b"hello world"))
        assert (tmp_path / key).read_bytes() == b"hello world"

    async def test_exists_reflects_save_and_delete(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path)
        key = await store.save(_make_upload(b"x"))
        assert await store.exists(key) is True
        assert await store.delete(key) is True
        assert await store.exists(key) is False

    async def test_replace_swaps_object(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path)
        old = await store.save(_make_upload(b"old"), filename="a.png")
        new = await store.replace(old, _make_upload(b"new"), filename="b.png")
        assert await store.exists(old) is False
        assert (tmp_path / new).read_bytes() == b"new"

    async def test_download_returns_file_response(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path)
        key = await store.save(_make_upload(b"data"), keep_original_name=True)
        response = await store.download(str(key))
        assert isinstance(response, FileResponse)

    def test_file_response_and_resolve(self, tmp_path: Path) -> None:
        (tmp_path / "report.pdf").write_bytes(b"pdf")
        store = FileStoreUtils(tmp_path)
        assert store.resolve("report.pdf") == (tmp_path / "report.pdf").resolve()
        assert isinstance(store.file_response("report.pdf"), FileResponse)

    def test_stream_wraps_bytes(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path)
        response = store.stream(b"generated", filename="out.txt")
        assert isinstance(response, StreamingResponse)

    def test_validate_rejects_extension(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path, allowed_extensions={"png"})
        with pytest.raises(InvalidFileTypeException):
            store.validate(_make_upload(b"x", filename="evil.exe"))

    async def test_presigned_urls_are_none_for_local(self, tmp_path: Path) -> None:
        store = FileStoreUtils(tmp_path)
        assert await store.presigned_get_url("k") is None
        assert await store.presigned_put_url("k") is None

    def test_resolve_traversal_raises(self, tmp_path: Path) -> None:
        base = tmp_path / "public"
        base.mkdir()
        (tmp_path / "secret.txt").write_bytes(b"secret")
        store = FileStoreUtils(base)
        with pytest.raises(NotFoundException):
            store.resolve("../secret.txt")


class TestMinIOBackend:
    def test_selects_minio_backend(self, minio_client: AsyncMinIOClient) -> None:
        store = FileStoreUtils(minio_client)
        assert store.client is minio_client
        assert isinstance(store.backend, MinIOUploadStorage)

    def test_shares_client_across_halves(self, minio_client: AsyncMinIOClient) -> None:
        store = FileStoreUtils(minio_client)
        # Upload backend and download half reference the same client object,
        # so the connection pool is shared, not duplicated.
        assert store.backend.client is minio_client
        assert store.downloader._minio is minio_client

    async def test_save_then_exists_then_delete(
        self, minio_client: AsyncMinIOClient
    ) -> None:
        store = FileStoreUtils(minio_client)
        key = await store.save(_make_upload(b"abc"), filename="k.png")
        assert await store.exists(key) is True
        await store.delete(key)
        assert await store.exists(key) is False

    async def test_download_returns_streaming_response(
        self, minio_client: AsyncMinIOClient
    ) -> None:
        store = FileStoreUtils(minio_client)
        key = await store.save(_make_upload(b"abc"), filename="k.png")
        response = await store.download(str(key))
        assert isinstance(response, StreamingResponse)

    async def test_presigned_get_and_put(self, minio_client: AsyncMinIOClient) -> None:
        store = FileStoreUtils(minio_client)
        get_url = await store.presigned_get_url("k")
        put_url = await store.presigned_put_url("k")
        assert get_url and "get=" in get_url
        assert put_url and "put=" in put_url

    async def test_presigned_get_honours_expires(
        self, minio_client: AsyncMinIOClient
    ) -> None:
        store = FileStoreUtils(minio_client)
        url = await store.presigned_get_url("k", expires=timedelta(minutes=5))
        assert url and "get=300" in url


class TestUploadUtilsBackendInjection:
    def test_uses_injected_backend(self, tmp_path: Path) -> None:
        backend = LocalUploadStorage(tmp_path)
        utils = UploadUtils(backend=backend)
        assert utils._storage is backend
        assert utils.upload_dir is None

    def test_injected_backend_keeps_upload_dir_from_path(self, tmp_path: Path) -> None:
        backend = LocalUploadStorage(tmp_path)
        utils = UploadUtils(tmp_path, backend=backend)
        assert utils._storage is backend
        assert utils.upload_dir == Path(tmp_path)

    def test_requires_source_or_backend(self) -> None:
        with pytest.raises(ValueError):
            UploadUtils()
