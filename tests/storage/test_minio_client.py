"""Tests for tempest_fastapi_sdk.storage.AsyncMinIOClient.

Uses an in-memory fake of the ``minio.Minio`` surface so tests run
offline. The real client is exercised in the bundled example
project documented in ``docs/recipes/storage.md``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk import AsyncMinIOClient


class _FakeObject:
    """Stand-in for ``minio.datatypes.Object``."""

    def __init__(
        self,
        *,
        bucket: str,
        key: str,
        size: int,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> None:
        self.bucket_name: str = bucket
        self.object_name: str = key
        self.size: int = size
        self.etag: str = f'"etag-{key}"'
        self.content_type: str = content_type
        self.last_modified: datetime = datetime(2026, 1, 1)
        self.metadata: dict[str, str] = {
            f"x-amz-meta-{k}": v for k, v in metadata.items()
        }
        self._data: bytes = data


class _FakeResponse:
    """Minimal urllib3-like response surface used by ``Minio.get_object``."""

    def __init__(self, payload: bytes) -> None:
        self._buffer: BytesIO = BytesIO(payload)
        self.closed: bool = False
        self.released: bool = False

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            return self._buffer.read()
        return self._buffer.read(amt)

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class _FakeMinio:
    """In-memory fake covering the methods the SDK calls."""

    def __init__(self, *_: Any, **__: Any) -> None:
        self.buckets: dict[str, dict[str, _FakeObject]] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str, location: str | None = None) -> None:
        del location
        self.buckets[bucket] = {}

    def list_buckets(self) -> list[Any]:
        return [type("B", (), {"name": name})() for name in self.buckets]

    def remove_bucket(self, bucket: str) -> None:
        del self.buckets[bucket]

    def put_object(
        self,
        bucket: str,
        key: str,
        data: Any,
        length: int,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        part_size: int = 10 * 1024 * 1024,
    ) -> Any:
        del length, part_size
        payload = data.read()
        self.buckets.setdefault(bucket, {})[key] = _FakeObject(
            bucket=bucket,
            key=key,
            size=len(payload),
            data=payload,
            content_type=content_type,
            metadata=metadata or {},
        )
        return type("R", (), {"etag": f'"etag-{key}"'})()

    def fput_object(
        self,
        bucket: str,
        key: str,
        file_path: str,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> Any:
        payload = Path(file_path).read_bytes()
        self.buckets.setdefault(bucket, {})[key] = _FakeObject(
            bucket=bucket,
            key=key,
            size=len(payload),
            data=payload,
            content_type=content_type,
            metadata=metadata or {},
        )
        return type("R", (), {"etag": f'"etag-{key}"'})()

    def get_object(self, bucket: str, key: str) -> _FakeResponse:
        return _FakeResponse(self.buckets[bucket][key]._data)

    def fget_object(self, bucket: str, key: str, file_path: str) -> None:
        Path(file_path).write_bytes(self.buckets[bucket][key]._data)

    def stat_object(self, bucket: str, key: str) -> _FakeObject:
        return self.buckets[bucket][key]

    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        recursive: bool = True,
    ) -> list[_FakeObject]:
        del recursive
        return [
            obj
            for key, obj in self.buckets.get(bucket, {}).items()
            if key.startswith(prefix)
        ]

    def remove_object(
        self,
        bucket: str,
        key: str,
        version_id: str | None = None,
    ) -> None:
        del version_id
        self.buckets.get(bucket, {}).pop(key, None)

    def copy_object(
        self,
        dst_bucket: str,
        dst_key: str,
        source: Any,
    ) -> Any:
        src = self.buckets[source.bucket_name][source.object_name]
        self.buckets.setdefault(dst_bucket, {})[dst_key] = _FakeObject(
            bucket=dst_bucket,
            key=dst_key,
            size=src.size,
            data=src._data,
            content_type=src.content_type,
            metadata={
                k.removeprefix("x-amz-meta-"): v for k, v in src.metadata.items()
            },
        )
        return type("R", (), {"etag": f'"etag-{dst_key}"'})()

    def presigned_get_object(
        self,
        bucket: str,
        key: str,
        expires: timedelta,
    ) -> str:
        return (
            f"https://fake.minio/{bucket}/{key}"
            f"?op=GET&exp={int(expires.total_seconds())}"
        )

    def presigned_put_object(
        self,
        bucket: str,
        key: str,
        expires: timedelta,
    ) -> str:
        return (
            f"https://fake.minio/{bucket}/{key}"
            f"?op=PUT&exp={int(expires.total_seconds())}"
        )


@pytest.fixture
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> _FakeMinio:
    """Patch ``minio.Minio`` so the client speaks to the fake."""
    fake = _FakeMinio()

    def _factory(*args: Any, **kwargs: Any) -> _FakeMinio:
        del args, kwargs
        return fake

    monkeypatch.setattr("minio.Minio", _factory)
    return fake


@pytest.fixture
def client(fake_minio: _FakeMinio) -> AsyncMinIOClient:
    """Build an ``AsyncMinIOClient`` backed by the fake."""
    del fake_minio
    return AsyncMinIOClient(
        endpoint="fake:9000",
        access_key="ak",
        secret_key="sk",
        default_bucket="uploads",
    )


class TestBuckets:
    async def test_ensure_bucket_creates_then_no_op(
        self, client: AsyncMinIOClient
    ) -> None:
        assert await client.ensure_bucket() is True
        assert await client.ensure_bucket() is False

    async def test_bucket_exists_roundtrip(self, client: AsyncMinIOClient) -> None:
        assert await client.bucket_exists() is False
        await client.ensure_bucket()
        assert await client.bucket_exists() is True

    async def test_list_buckets(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket("a")
        await client.ensure_bucket("b")
        assert sorted(await client.list_buckets()) == ["a", "b"]

    async def test_remove_bucket(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket("ephemeral")
        await client.remove_bucket("ephemeral")
        assert await client.bucket_exists("ephemeral") is False


class TestObjectIO:
    async def test_put_then_get_bytes(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        etag = await client.put_object("hello.txt", b"world")
        assert etag == "etag-hello.txt"
        assert await client.get_object_bytes("hello.txt") == b"world"

    async def test_put_object_requires_length_for_streams(
        self, client: AsyncMinIOClient
    ) -> None:
        await client.ensure_bucket()
        with pytest.raises(ValueError, match="length must be provided"):
            await client.put_object("k", BytesIO(b"x"))

    async def test_fput_and_fget(
        self, client: AsyncMinIOClient, tmp_path: Path
    ) -> None:
        await client.ensure_bucket()
        src = tmp_path / "src.bin"
        src.write_bytes(b"on-disk")
        await client.fput_object("dest.bin", src)
        dst = tmp_path / "dst.bin"
        await client.fget_object("dest.bin", dst)
        assert dst.read_bytes() == b"on-disk"

    async def test_stat_object_returns_metadata(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        await client.put_object(
            "with-meta.txt",
            b"abc",
            content_type="text/plain",
            metadata={"owner": "ana"},
        )
        stat = await client.stat_object("with-meta.txt")
        assert stat.size == 3
        assert stat.content_type == "text/plain"
        assert stat.metadata == {"owner": "ana"}
        assert stat.etag == "etag-with-meta.txt"

    async def test_list_objects_prefix(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        await client.put_object("img/a.png", b"a")
        await client.put_object("img/b.png", b"b")
        await client.put_object("doc/c.pdf", b"c")
        assert sorted(await client.list_objects("img/")) == [
            "img/a.png",
            "img/b.png",
        ]

    async def test_list_objects_empty_returns_list(
        self, client: AsyncMinIOClient
    ) -> None:
        await client.ensure_bucket()
        assert await client.list_objects("missing/") == []

    async def test_remove_object(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        await client.put_object("temp.txt", b"x")
        await client.remove_object("temp.txt")
        assert await client.list_objects() == []

    async def test_copy_object(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        await client.put_object("src.txt", b"copy me")
        etag = await client.copy_object("src.txt", "dst.txt")
        assert etag == "etag-dst.txt"
        assert await client.get_object_bytes("dst.txt") == b"copy me"

    async def test_stream_object_chunks(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        payload = b"abcdefghij" * 2  # 20 bytes
        await client.put_object("big.bin", payload)
        stream = await client.stream_object("big.bin", chunk_size=7)
        collected = b""
        async for chunk in stream:
            collected += chunk
        assert collected == payload


class TestPresigned:
    async def test_get_url(self, client: AsyncMinIOClient) -> None:
        url = await client.presigned_get_url("k", expires=timedelta(hours=2))
        assert "op=GET" in url
        assert "exp=7200" in url

    async def test_put_url(self, client: AsyncMinIOClient) -> None:
        url = await client.presigned_put_url("k", expires=timedelta(minutes=5))
        assert "op=PUT" in url
        assert "exp=300" in url
