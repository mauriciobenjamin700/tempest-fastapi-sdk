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

from tempest_fastapi_sdk import AsyncMinIOClient, PutObjectItem


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


class TestBatch:
    async def test_presigned_get_urls_maps_each_key(
        self, client: AsyncMinIOClient
    ) -> None:
        urls = await client.presigned_get_urls(
            ["a.txt", "b.txt"], expires=timedelta(hours=2)
        )
        assert set(urls) == {"a.txt", "b.txt"}
        assert "op=GET" in urls["a.txt"]
        assert "exp=7200" in urls["b.txt"]

    async def test_presigned_get_urls_collapses_duplicates(
        self, client: AsyncMinIOClient
    ) -> None:
        urls = await client.presigned_get_urls(["dup", "dup", "other"])
        assert set(urls) == {"dup", "other"}

    async def test_presigned_get_urls_empty(self, client: AsyncMinIOClient) -> None:
        assert await client.presigned_get_urls([]) == {}

    async def test_put_objects_uploads_all(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        etags = await client.put_objects(
            [
                PutObjectItem(key="one.txt", data=b"1"),
                PutObjectItem(key="two.txt", data=b"22", content_type="text/plain"),
            ]
        )
        assert etags == {"one.txt": "etag-one.txt", "two.txt": "etag-two.txt"}
        assert await client.get_object_bytes("one.txt") == b"1"
        assert await client.get_object_bytes("two.txt") == b"22"

    async def test_get_objects_bytes_maps_each_key(
        self, client: AsyncMinIOClient
    ) -> None:
        await client.ensure_bucket()
        await client.put_object("x.txt", b"xx")
        await client.put_object("y.txt", b"yy")
        blobs = await client.get_objects_bytes(["x.txt", "y.txt", "x.txt"])
        assert blobs == {"x.txt": b"xx", "y.txt": b"yy"}

    async def test_batch_rejects_non_positive_concurrency(
        self, client: AsyncMinIOClient
    ) -> None:
        with pytest.raises(ValueError, match="max_concurrency must be at least 1"):
            await client.presigned_get_urls(["a"], max_concurrency=0)


class TestDownloadResponse:
    async def test_streams_object_with_headers(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        await client.put_object(
            "docs/report.txt", b"hello world", content_type="text/plain"
        )
        response = await client.download_response("docs/report.txt")

        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        assert b"".join(chunks) == b"hello world"

        assert response.media_type == "text/plain"
        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        assert "report.txt" in disposition
        assert response.headers["content-length"] == "11"

    async def test_inline_with_custom_filename(self, client: AsyncMinIOClient) -> None:
        await client.ensure_bucket()
        await client.put_object("k", b"x", content_type="application/pdf")
        response = await client.download_response(
            "k", filename="invoice.pdf", as_attachment=False
        )
        disposition = response.headers["content-disposition"]
        assert "inline" in disposition
        assert "invoice.pdf" in disposition


class TestDownloadUtilsWithMinio:
    async def test_download_delegates_to_minio(self, client: AsyncMinIOClient) -> None:
        from tempest_fastapi_sdk import DownloadUtils

        await client.ensure_bucket()
        await client.put_object("a/b.txt", b"payload", content_type="text/plain")
        downloads = DownloadUtils(client)
        response = await downloads.download("b.txt", subdir="a")

        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        assert b"".join(chunks) == b"payload"
        assert "b.txt" in response.headers["content-disposition"]

    def test_resolve_raises_in_minio_mode(self, client: AsyncMinIOClient) -> None:
        from tempest_fastapi_sdk import DownloadUtils

        downloads = DownloadUtils(client)
        with pytest.raises(RuntimeError, match="MinIO-backed"):
            downloads.resolve("anything")


class _RecordingMinio:
    """Fake that records the endpoint/secure it was built with.

    ``presigned_*`` echo the recording instance's own host so a test
    can assert which underlying client signed the URL.
    """

    def __init__(self, endpoint: str, *_: Any, secure: bool = False, **__: Any) -> None:
        self.endpoint: str = endpoint
        self.secure: bool = secure

    def presigned_get_object(self, bucket: str, key: str, expires: timedelta) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}/{bucket}/{key}?op=GET"

    def presigned_put_object(self, bucket: str, key: str, expires: timedelta) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}/{bucket}/{key}?op=PUT"


@pytest.fixture
def recording_minio(monkeypatch: pytest.MonkeyPatch) -> list[_RecordingMinio]:
    """Patch ``minio.Minio`` to record every constructed client."""
    built: list[_RecordingMinio] = []

    def _factory(endpoint: str, *args: Any, **kwargs: Any) -> _RecordingMinio:
        del args
        inst = _RecordingMinio(endpoint, secure=kwargs.get("secure", False))
        built.append(inst)
        return inst

    monkeypatch.setattr("minio.Minio", _factory)
    return built


class TestSplitEndpoint:
    async def test_presign_uses_public_endpoint(
        self, recording_minio: list[_RecordingMinio]
    ) -> None:
        client = AsyncMinIOClient(
            endpoint="internal:9000",
            access_key="ak",
            secret_key="sk",
            default_bucket="uploads",
            public_endpoint="storage.example.com",
            public_secure=True,
        )
        # Two clients built: internal (ops) + public (presign).
        assert len(recording_minio) == 2
        assert client.public_endpoint == "storage.example.com"
        assert client.public_secure is True

        url = await client.presigned_get_url("k")
        assert url.startswith("https://storage.example.com/uploads/k")

        put = await client.presigned_put_url("k")
        assert put.startswith("https://storage.example.com/uploads/k")

    async def test_ops_still_use_internal_endpoint(
        self, recording_minio: list[_RecordingMinio]
    ) -> None:
        client = AsyncMinIOClient(
            endpoint="internal:9000",
            access_key="ak",
            secret_key="sk",
            public_endpoint="storage.example.com",
        )
        # The ops client is the internal one; the presign client is public.
        assert client.client.endpoint == "internal:9000"
        assert client._presign_client.endpoint == "storage.example.com"

    async def test_no_public_endpoint_signs_with_internal(
        self, recording_minio: list[_RecordingMinio]
    ) -> None:
        client = AsyncMinIOClient(
            endpoint="internal:9000",
            access_key="ak",
            secret_key="sk",
            default_bucket="uploads",
        )
        # Single client; presign falls back to it.
        assert len(recording_minio) == 1
        assert client.public_endpoint is None
        assert client._presign_client is client.client

        url = await client.presigned_get_url("k")
        assert url.startswith("http://internal:9000/uploads/k")

    def test_scheme_in_public_endpoint_implies_secure(
        self, recording_minio: list[_RecordingMinio]
    ) -> None:
        client = AsyncMinIOClient(
            endpoint="internal:9000",
            access_key="ak",
            secret_key="sk",
            public_endpoint="https://storage.example.com/base/",
        )
        # scheme stripped to bare host, https → secure, trailing path dropped.
        assert client.public_endpoint == "storage.example.com"
        assert client.public_secure is True

    def test_explicit_public_secure_overrides_scheme(
        self, recording_minio: list[_RecordingMinio]
    ) -> None:
        client = AsyncMinIOClient(
            endpoint="internal:9000",
            access_key="ak",
            secret_key="sk",
            public_endpoint="https://storage.example.com",
            public_secure=False,
        )
        assert client.public_secure is False
