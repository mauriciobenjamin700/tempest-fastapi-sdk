"""Tests for tempest_fastapi_sdk.storage.BucketRoutedMinIOClient.

``minio.Minio`` is replaced by a fake that remembers the access key it was
built with and talks to one shared in-memory store that grants each key a
set of buckets — the shape of a store that binds a key to a bucket. A call
signed with the wrong key raises ``AccessDenied``, so every test that
passes proves the call was routed to the credential of its bucket.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BucketRoutedMinIOClient,
    IntegrationDisabledException,
    PutObjectItem,
    StorageBucketCredential,
)

PRIMARY_KEY: str = "images-key"
DOCUMENTS_KEY: str = "documents-key"
ARCHIVE_KEY: str = "archive-key"


class _AccessDeniedError(Exception):
    """What the fake store raises for a key used outside its bucket."""


class _Object:
    """One stored object of the fake store."""

    def __init__(
        self, data: bytes, content_type: str, metadata: dict[str, str]
    ) -> None:
        self.data: bytes = data
        self.size: int = len(data)
        self.etag: str = f'"etag-{len(data)}"'
        self.content_type: str = content_type
        self.last_modified: None = None
        self.metadata: dict[str, str] = {
            f"x-amz-meta-{k}": v for k, v in metadata.items()
        }


class _Response:
    """urllib3-like body returned by ``get_object``."""

    def __init__(self, payload: bytes) -> None:
        self._buffer: BytesIO = BytesIO(payload)

    def read(self, amt: int | None = None) -> bytes:
        return self._buffer.read() if amt is None else self._buffer.read(amt)

    def close(self) -> None:
        return None

    def release_conn(self) -> None:
        return None


class _Store:
    """The server side: objects per bucket and the buckets each key reaches."""

    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, _Object]] = {
            "images": {},
            "documents": {},
            "archive": {},
        }
        self.grants: dict[str, set[str]] = {
            PRIMARY_KEY: {"images", "shared"},
            DOCUMENTS_KEY: {"documents"},
            ARCHIVE_KEY: {"archive"},
        }
        self.calls: list[tuple[str, str, str]] = []
        self.hosts: dict[str, str] = {}


class _FakeMinio:
    """``minio.Minio`` stand-in that checks its key against the bucket."""

    def __init__(self, store: _Store, endpoint: str, access_key: str) -> None:
        self.store: _Store = store
        self.endpoint: str = endpoint
        self.access_key: str = access_key

    def _check(self, operation: str, bucket: str) -> None:
        self.store.calls.append((self.access_key, operation, bucket))
        if bucket not in self.store.grants[self.access_key]:
            raise _AccessDeniedError(f"{self.access_key} cannot reach {bucket}")

    def bucket_exists(self, bucket: str) -> bool:
        self._check("bucket_exists", bucket)
        return bucket in self.store.buckets

    def make_bucket(self, bucket: str, location: str | None = None) -> None:
        del location
        self._check("make_bucket", bucket)
        self.store.buckets[bucket] = {}

    def remove_bucket(self, bucket: str) -> None:
        self._check("remove_bucket", bucket)
        del self.store.buckets[bucket]

    def list_buckets(self) -> list[Any]:
        self.store.calls.append((self.access_key, "list_buckets", ""))
        return [
            type("B", (), {"name": name})()
            for name in sorted(self.store.grants[self.access_key])
            if name in self.store.buckets
        ]

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
        del length, part_size
        self._check("put_object", bucket)
        self.store.buckets[bucket][key] = _Object(
            data.read(), content_type, metadata or {}
        )
        return type("R", (), {"etag": f'"put-{key}"'})()

    def fput_object(
        self,
        bucket: str,
        key: str,
        file_path: str,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> Any:
        self._check("fput_object", bucket)
        payload = Path(file_path).read_bytes()
        self.store.buckets[bucket][key] = _Object(payload, content_type, metadata or {})
        return type("R", (), {"etag": f'"fput-{key}"'})()

    def get_object(
        self, bucket: str, key: str, offset: int = 0, length: int = 0
    ) -> _Response:
        self._check("get_object", bucket)
        data = self.store.buckets[bucket][key].data
        end = offset + length if length else len(data)
        return _Response(data[offset:end])

    def fget_object(self, bucket: str, key: str, file_path: str) -> None:
        self._check("fget_object", bucket)
        Path(file_path).write_bytes(self.store.buckets[bucket][key].data)

    def stat_object(self, bucket: str, key: str) -> _Object:
        self._check("stat_object", bucket)
        return self.store.buckets[bucket][key]

    def list_objects(
        self, bucket: str, prefix: str = "", recursive: bool = True
    ) -> list[Any]:
        del recursive
        self._check("list_objects", bucket)
        return [
            type("O", (), {"object_name": key})()
            for key in self.store.buckets[bucket]
            if key.startswith(prefix)
        ]

    def remove_object(
        self, bucket: str, key: str, version_id: str | None = None
    ) -> None:
        del version_id
        self._check("remove_object", bucket)
        self.store.buckets[bucket].pop(key, None)

    def remove_objects(self, bucket: str, delete_object_list: Any) -> Any:
        self._check("remove_objects", bucket)
        for obj in delete_object_list:
            self.store.buckets[bucket].pop(obj.name, None)
        return iter([])

    def copy_object(self, bucket: str, key: str, source: Any) -> Any:
        self._check("copy_object", bucket)
        self._check("copy_object_source", source.bucket_name)
        src = self.store.buckets[source.bucket_name][source.object_name]
        self.store.buckets[bucket][key] = _Object(
            src.data,
            src.content_type,
            {k.removeprefix("x-amz-meta-"): v for k, v in src.metadata.items()},
        )
        return type("R", (), {"etag": f'"copy-{key}"'})()

    def presigned_get_object(
        self,
        bucket: str,
        key: str,
        expires: timedelta,
        response_headers: dict[str, str] | None = None,
    ) -> str:
        del expires, response_headers
        self._check("presigned_get_object", bucket)
        return (
            f"https://{self.endpoint}/{bucket}/{key}?X-Amz-Credential={self.access_key}"
        )

    def presigned_put_object(self, bucket: str, key: str, expires: timedelta) -> str:
        del expires
        self._check("presigned_put_object", bucket)
        return (
            f"https://{self.endpoint}/{bucket}/{key}?X-Amz-Credential={self.access_key}"
        )

    def presigned_post_policy(self, policy: Any) -> dict[str, str]:
        self._check("presigned_post_policy", policy.bucket_name)
        return {"x-amz-credential": self.access_key}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    """Patch ``minio.Minio`` so every client speaks to one fake store."""
    shared = _Store()

    def _factory(endpoint: str, *, access_key: str, **kwargs: Any) -> _FakeMinio:
        del kwargs
        return _FakeMinio(shared, endpoint, access_key)

    monkeypatch.setattr("minio.Minio", _factory)
    return shared


def _credential(access_key: str) -> StorageBucketCredential:
    """Build a credential whose secret is derived from the key."""
    return StorageBucketCredential(
        access_key=access_key, secret_key=SecretStr(f"{access_key}-secret")
    )


@pytest.fixture
def client(store: _Store) -> BucketRoutedMinIOClient:
    """A router: ``images`` on the primary key, two buckets on their own."""
    del store
    return BucketRoutedMinIOClient(
        "storage.local:9000",
        PRIMARY_KEY,
        "images-secret",
        default_bucket="images",
        bucket_credentials={
            "documents": _credential(DOCUMENTS_KEY),
            "archive": _credential(ARCHIVE_KEY),
        },
    )


def _keys(store: _Store) -> set[str]:
    """Return the access keys the store saw since the last reset."""
    return {key for key, _, _ in store.calls}


class TestConstruction:
    def test_is_an_async_minio_client(self, client: BucketRoutedMinIOClient) -> None:
        assert isinstance(client, AsyncMinIOClient)

    def test_routed_buckets(self, client: BucketRoutedMinIOClient) -> None:
        assert client.routed_buckets == frozenset({"documents", "archive"})

    def test_empty_bucket_name_refused(self, store: _Store) -> None:
        del store
        with pytest.raises(ValueError, match="empty bucket name"):
            BucketRoutedMinIOClient(
                "s:9000",
                PRIMARY_KEY,
                "x",
                bucket_credentials={"": _credential(DOCUMENTS_KEY)},
            )

    async def test_disabled_builds_no_route_and_refuses(self) -> None:
        disabled = BucketRoutedMinIOClient(
            "s:9000",
            PRIMARY_KEY,
            "x",
            bucket_credentials={"documents": _credential(DOCUMENTS_KEY)},
            enabled=False,
        )
        assert disabled.routed_buckets == frozenset()
        with pytest.raises(IntegrationDisabledException):
            await disabled.put_object("a.txt", b"x", bucket="documents")


class TestDispatch:
    """Every bucket-scoped operation is signed with its bucket's key."""

    async def test_bucket_lifecycle(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        assert await client.bucket_exists("documents") is True
        assert await client.ensure_bucket("documents") is False
        store.buckets.pop("archive")
        assert await client.ensure_bucket("archive") is True
        await client.remove_bucket("archive")
        assert _keys(store) == {DOCUMENTS_KEY, ARCHIVE_KEY}

    async def test_object_io(
        self, client: BucketRoutedMinIOClient, store: _Store, tmp_path: Path
    ) -> None:
        await client.put_object("a.txt", b"doc", bucket="documents")
        source = tmp_path / "in.txt"
        source.write_bytes(b"from disk")
        await client.fput_object("b.txt", source, bucket="documents")
        assert await client.get_object_bytes("a.txt", bucket="documents") == b"doc"
        out = await client.fget_object(
            "b.txt", tmp_path / "out.txt", bucket="documents"
        )
        assert out.read_bytes() == b"from disk"
        chunks = [
            chunk
            async for chunk in await client.stream_object(
                "a.txt", bucket="documents", offset=1
            )
        ]
        assert b"".join(chunks) == b"oc"
        stat = await client.stat_object("a.txt", bucket="documents")
        assert stat.bucket == "documents"
        assert await client.list_objects(bucket="documents") == ["a.txt", "b.txt"]
        await client.remove_object("a.txt", bucket="documents")
        assert await client.remove_objects(["b.txt"], bucket="documents") == []
        assert store.buckets["documents"] == {}
        assert _keys(store) == {DOCUMENTS_KEY}

    async def test_presigned(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        get_url = await client.presigned_get_url("a.pdf", bucket="documents")
        put_url = await client.presigned_put_url("a.pdf", bucket="documents")
        post = await client.presigned_post_policy(
            "a.pdf", max_bytes=1024, bucket="documents"
        )
        assert f"X-Amz-Credential={DOCUMENTS_KEY}" in get_url
        assert f"X-Amz-Credential={DOCUMENTS_KEY}" in put_url
        assert post.fields["x-amz-credential"] == DOCUMENTS_KEY
        assert post.url.endswith("/documents")
        assert _keys(store) == {DOCUMENTS_KEY}

    async def test_presigned_signed_against_public_endpoint(
        self, store: _Store
    ) -> None:
        router = BucketRoutedMinIOClient(
            "storage:9000",
            PRIMARY_KEY,
            "x",
            public_endpoint="https://files.example.com",
            bucket_credentials={"documents": _credential(DOCUMENTS_KEY)},
        )
        url = await router.presigned_get_url("a.pdf", bucket="documents")
        assert url.startswith("https://files.example.com/documents/a.pdf")
        assert f"X-Amz-Credential={DOCUMENTS_KEY}" in url
        assert _keys(store) == {DOCUMENTS_KEY}

    async def test_delivery(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        await client.put_object(
            "a.txt", b"doc", bucket="documents", content_type="text/plain"
        )
        streamed = await client.download_response("a.txt", bucket="documents")
        assert streamed.status_code == 200
        served = await client.serve_object("a.txt", bucket="documents")
        assert served.status_code == 200
        redirect = await client.accel_redirect_response("a.txt", bucket="documents")
        assert DOCUMENTS_KEY in redirect.headers["x-accel-redirect"]
        assert _keys(store) == {DOCUMENTS_KEY}

    async def test_batches(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        await client.put_objects(
            [
                PutObjectItem(key="a.txt", data=b"1"),
                PutObjectItem(key="b.txt", data=b"2"),
            ],
            bucket="archive",
        )
        blobs = await client.get_objects_bytes(["a.txt", "b.txt"], bucket="archive")
        assert blobs == {"a.txt": b"1", "b.txt": b"2"}
        urls = await client.presigned_get_urls(["a.txt"], bucket="archive")
        assert f"X-Amz-Credential={ARCHIVE_KEY}" in urls["a.txt"]
        assert _keys(store) == {ARCHIVE_KEY}


class TestFallback:
    async def test_unlisted_bucket_uses_primary(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        store.buckets["shared"] = {}
        await client.put_object("a.txt", b"x", bucket="shared")
        url = await client.presigned_get_url("a.txt", bucket="shared")
        assert f"X-Amz-Credential={PRIMARY_KEY}" in url
        assert _keys(store) == {PRIMARY_KEY}

    async def test_no_bucket_uses_default_bucket_route(self, store: _Store) -> None:
        router = BucketRoutedMinIOClient(
            "s:9000",
            PRIMARY_KEY,
            "x",
            default_bucket="documents",
            bucket_credentials={"documents": _credential(DOCUMENTS_KEY)},
        )
        await router.put_object("a.txt", b"x")
        assert _keys(store) == {DOCUMENTS_KEY}

    async def test_list_buckets_and_client_stay_primary(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        assert await client.list_buckets() == ["images"]
        assert client.client.access_key == PRIMARY_KEY  # type: ignore[attr-defined]
        assert _keys(store) == {PRIMARY_KEY}

    async def test_wrong_key_is_what_the_fake_refuses(self, store: _Store) -> None:
        plain = AsyncMinIOClient("s:9000", PRIMARY_KEY, "x", default_bucket="images")
        with pytest.raises(_AccessDeniedError):
            await plain.put_object("a.txt", b"x", bucket="documents")
        del store


class TestCopy:
    async def test_same_credential_is_server_side(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        await client.put_object("a.txt", b"doc", bucket="documents")
        etag = await client.copy_object(
            "a.txt", "b.txt", source_bucket="documents", dest_bucket="documents"
        )
        assert etag == "copy-b.txt"
        assert ("documents-key", "copy_object", "documents") in store.calls

    async def test_cross_credential_reads_then_writes(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        await client.put_object(
            "a.txt",
            b"doc",
            bucket="documents",
            content_type="text/plain",
            metadata={"owner": "42"},
        )
        store.calls.clear()
        etag = await client.copy_object(
            "a.txt", "a.txt", source_bucket="documents", dest_bucket="archive"
        )
        assert etag == "fput-a.txt"
        copied = store.buckets["archive"]["a.txt"]
        assert copied.data == b"doc"
        assert copied.content_type == "text/plain"
        assert copied.metadata == {"x-amz-meta-owner": "42"}
        assert [op for _, op, _ in store.calls] == [
            "stat_object",
            "fget_object",
            "fput_object",
        ]
        assert [key for key, _, _ in store.calls] == [
            DOCUMENTS_KEY,
            DOCUMENTS_KEY,
            ARCHIVE_KEY,
        ]

    async def test_cross_credential_from_default_bucket(
        self, client: BucketRoutedMinIOClient, store: _Store
    ) -> None:
        await client.put_object("a.png", b"img")
        await client.copy_object("a.png", "a.png", dest_bucket="documents")
        assert store.buckets["documents"]["a.png"].data == b"img"


class TestCredential:
    def test_secret_masked(self) -> None:
        credential = StorageBucketCredential(
            access_key="AK", secret_key=SecretStr("very-secret")
        )
        assert "very-secret" not in repr(credential)
        assert "very-secret" not in str(credential.model_dump())

    @pytest.mark.parametrize(
        "payload",
        [
            {"access_key": "AK"},
            {"secret_key": "very-secret"},
            {"access_key": "", "secret_key": "very-secret"},
            {"access_key": "AK", "secret_key": ""},
            {"access_key": "AK", "secret": "very-secret"},
        ],
    )
    def test_partial_refused_without_echoing_secret(
        self, payload: dict[str, str]
    ) -> None:
        with pytest.raises(ValueError) as caught:
            StorageBucketCredential.model_validate(payload)
        assert "very-secret" not in str(caught.value)
