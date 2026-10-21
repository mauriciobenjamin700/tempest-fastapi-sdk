"""Tests for tempest_fastapi_sdk.privacy.SubjectObjectStorage (offline fake)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest

from tempest_fastapi_sdk import AsyncMinIOClient, IntegrationDisabledException
from tempest_fastapi_sdk.privacy import SubjectErasureError, SubjectObjectStorage
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient


class _FakeMinio:
    """In-memory stand-in for the ``minio.Minio`` calls the storage makes.

    Kept beside the public :class:`FakeObjectStorageClient` on purpose: this
    one sits **under** the real ``AsyncMinIOClient``, so the tests built on
    it also cover the wrapper's translation to ``minio``; the public fake
    replaces the wrapper and covers what a service tests.
    """

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, bytes]] = {}
        self.batches: list[list[str]] = []
        self.single_deletes: list[str] = []
        self.refuse: set[str] = set()
        self.reads: list[tuple[str, dict[str, Any]]] = []

    def put_object(
        self,
        bucket: str,
        key: str,
        data: Any,
        length: int,
        **_: Any,
    ) -> Any:
        del length
        self.objects.setdefault(bucket, {})[key] = data.read()
        return type("R", (), {"etag": '"e"'})()

    def list_objects(
        self, bucket: str, prefix: str = "", recursive: bool = True
    ) -> list[Any]:
        del recursive
        return [
            type("O", (), {"object_name": key})()
            for key in self.objects.get(bucket, {})
            if key.startswith(prefix)
        ]

    def remove_object(
        self, bucket: str, key: str, version_id: str | None = None
    ) -> None:
        del version_id
        self.single_deletes.append(key)
        self.objects.get(bucket, {}).pop(key, None)

    def remove_objects(self, bucket: str, delete_object_list: Any) -> Any:
        names = [obj.name for obj in delete_object_list]
        self.batches.append(names)
        for name in names:
            if name not in self.refuse:
                self.objects.get(bucket, {}).pop(name, None)
        return iter(
            type("E", (), {"name": n, "code": "AccessDenied", "message": "denied"})()
            for n in names
            if n in self.refuse
        )

    def presigned_get_object(self, bucket: str, key: str, expires: timedelta) -> str:
        return f"https://fake/{bucket}/{key}?exp={int(expires.total_seconds())}"

    def presigned_put_object(self, bucket: str, key: str, expires: timedelta) -> str:
        return f"https://fake/{bucket}/{key}?put&exp={int(expires.total_seconds())}"

    def stat_object(self, bucket: str, key: str) -> Any:
        data = self.objects[bucket][key]
        return type(
            "S",
            (),
            {
                "size": len(data),
                "etag": '"e"',
                "content_type": "image/jpeg",
                "last_modified": None,
                "metadata": {"x-amz-meta-origin": "app"},
            },
        )()

    def get_object(self, bucket: str, key: str, **range_: Any) -> Any:
        """Serve the ``Range`` slice, recording what was asked for."""
        self.reads.append((key, range_))
        data = self.objects[bucket][key]
        offset = range_.get("offset", 0)
        length = range_.get("length", 0)
        return _FakeResponse(data[offset : offset + length] if length else data)


class _FakeResponse:
    """The ``urllib3`` response ``Minio.get_object`` returns, reduced."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        return None


class _RangeIgnoringClient(FakeObjectStorageClient):
    """A store that answers a ``Range`` read with the whole object."""

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        del offset, length
        return await super().stream_object(key, bucket=bucket)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeMinio:
    """Patch ``minio.Minio`` so the client speaks to the fake."""
    instance = _FakeMinio()
    monkeypatch.setattr("minio.Minio", lambda *a, **k: instance)
    return instance


@pytest.fixture
def storage(fake: _FakeMinio) -> SubjectObjectStorage:
    """Build a subject storage on a fake-backed client."""
    del fake
    client = AsyncMinIOClient("fake:9000", "ak", "sk", default_bucket="media")
    return SubjectObjectStorage(client, prefix="/users/")


class TestKeys:
    def test_key_layout(self, storage: SubjectObjectStorage) -> None:
        assert storage.prefix == "users"
        assert storage.subject_prefix(42) == "users/42/"
        assert storage.key(42, "docs/rg.pdf") == "users/42/docs/rg.pdf"

    @pytest.mark.parametrize("subject", ["", ".", "..", "a/b"])
    def test_invalid_subject(self, storage: SubjectObjectStorage, subject: str) -> None:
        with pytest.raises(ValueError, match="subject_id"):
            storage.subject_prefix(subject)

    @pytest.mark.parametrize("name", ["", "/abs", "a//b", "../x", "a/./b", "a/"])
    def test_name_cannot_leave_prefix(
        self, storage: SubjectObjectStorage, name: str
    ) -> None:
        with pytest.raises(ValueError, match="name segment"):
            storage.key(1, name)

    @pytest.mark.parametrize("prefix", ["", "/", "a//b", "a/../b"])
    def test_invalid_prefix(self, prefix: str) -> None:
        with pytest.raises(ValueError, match="prefix segment"):
            SubjectObjectStorage(object(), prefix=prefix)  # type: ignore[arg-type]


class TestOperations:
    async def test_put_names_presign(
        self, storage: SubjectObjectStorage, fake: _FakeMinio
    ) -> None:
        key = await storage.put(1, "a.txt", b"x")
        assert key == "users/1/a.txt"
        await storage.put(1, "sub/b.txt", b"y")
        assert fake.objects["media"]["users/1/sub/b.txt"] == b"y"
        assert await storage.names(1) == ["a.txt", "sub/b.txt"]
        assert await storage.names(2) == []
        url = await storage.presign(1, "a.txt", expires=timedelta(minutes=5))
        assert url == "https://fake/media/users/1/a.txt?exp=300"

    async def test_delete_all_is_one_batch_and_spares_other_subjects(
        self, storage: SubjectObjectStorage, fake: _FakeMinio
    ) -> None:
        for name in ("a", "b", "c"):
            await storage.put(1, name, b"x")
        await storage.put(10, "a", b"x")
        await storage.put(2, "a", b"x")
        assert await storage.delete_all(1) == 3
        assert fake.batches == [["users/1/a", "users/1/b", "users/1/c"]]
        assert fake.single_deletes == []
        assert sorted(fake.objects["media"]) == ["users/10/a", "users/2/a"]
        assert await storage.delete_all(1) == 0
        assert len(fake.batches) == 1

    async def test_delete_all_raises_with_refused_keys(
        self, storage: SubjectObjectStorage, fake: _FakeMinio
    ) -> None:
        await storage.put(1, "a", b"x")
        await storage.put(1, "b", b"x")
        fake.refuse.add("users/1/b")
        with pytest.raises(SubjectErasureError) as info:
            await storage.delete_all(1)
        assert info.value.subject_id == "1"
        assert [e.key for e in info.value.errors] == ["users/1/b"]
        assert "users/1/b (AccessDenied)" in str(info.value)
        assert list(fake.objects["media"]) == ["users/1/b"]

    async def test_delete_one(
        self, storage: SubjectObjectStorage, fake: _FakeMinio
    ) -> None:
        await storage.put(1, "a", b"x")
        await storage.delete(1, "a")
        assert fake.single_deletes == ["users/1/a"]

    async def test_presign_put_signs_the_subject_key(
        self, storage: SubjectObjectStorage
    ) -> None:
        url = await storage.presign_put(1, "ev/a.jpg", expires=timedelta(minutes=5))
        default = await storage.presign_put(1, "ev/a.jpg")
        assert url == "https://fake/media/users/1/ev/a.jpg?put&exp=300"
        assert default.endswith("exp=900")

    async def test_stat_reads_the_subject_key(
        self, storage: SubjectObjectStorage
    ) -> None:
        await storage.put(1, "a.jpg", b"12345")
        stat = await storage.stat(1, "a.jpg")
        assert (stat.bucket, stat.key, stat.size) == ("media", "users/1/a.jpg", 5)
        assert stat.content_type == "image/jpeg"
        assert stat.metadata == {"origin": "app"}

    async def test_read_with_max_bytes_sends_a_range(
        self, storage: SubjectObjectStorage, fake: _FakeMinio
    ) -> None:
        """Only ``max_bytes`` cross the wire: ``get_object`` gets the range."""
        await storage.put(1, "a.jpg", b"0123456789")
        assert await storage.read(1, "a.jpg", max_bytes=4) == b"0123"
        assert await storage.read(1, "a.jpg") == b"0123456789"
        assert fake.reads == [
            ("users/1/a.jpg", {"offset": 0, "length": 4}),
            ("users/1/a.jpg", {}),
        ]

    async def test_bucket_override(self, fake: _FakeMinio) -> None:
        client = AsyncMinIOClient("fake:9000", "ak", "sk", default_bucket="media")
        storage = SubjectObjectStorage(client, bucket="private")
        await storage.put("u", "a", b"x")
        assert list(fake.objects) == ["private"]
        assert await storage.delete_all("u") == 1


class TestWithThePublicFake:
    """``SubjectObjectStorage`` over ``FakeObjectStorageClient`` (#509)."""

    async def test_put_names_and_delete_all_leave_other_subjects_alone(self) -> None:
        """Export and erasure without MinIO; subject 7 survives subject 42."""
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="users")

        key = await storage.put(42, "avatar.png", b"png")
        await storage.put(7, "doc.pdf", b"pdf")
        names = await storage.names(42)
        removed = await storage.delete_all(42)

        assert key == "users/42/avatar.png"
        assert names == ["avatar.png"]
        assert removed == 1
        assert dict(client.objects) == {"users/7/doc.pdf": b"pdf"}
        assert client.deleted == ["users/42/avatar.png"]

    async def test_a_refused_key_raises_subject_erasure_error(self) -> None:
        """The branch a real store will not give on demand."""
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="users")
        await storage.put(42, "a.png", b"x")
        await storage.put(42, "b.png", b"y")
        client.fail_delete("users/42/a.png")

        with pytest.raises(SubjectErasureError) as caught:
            await storage.delete_all(42)

        assert [error.key for error in caught.value.errors] == ["users/42/a.png"]
        assert dict(client.objects) == {"users/42/a.png": b"x"}


class TestDirectUpload:
    """``presign_put``, ``stat`` and ``read`` over the public fake."""

    async def test_presign_put_is_scoped_to_the_subject(self) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens", bucket="ev")

        url = await storage.presign_put(42, "uploads/a.jpg")

        assert (
            url
            == "https://fake-storage/ev/teens/42/uploads/a.jpg?method=PUT&expires=900"
        )
        assert client.calls == ["presigned_put_url"]

    @pytest.mark.parametrize("name", ["", "/abs", "../7/a.jpg", "a/../../7/x"])
    async def test_a_name_leaving_the_prefix_is_refused(self, name: str) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens")

        with pytest.raises(ValueError, match="name segment"):
            await storage.presign_put(42, name)
        with pytest.raises(ValueError, match="name segment"):
            await storage.stat(42, name)
        with pytest.raises(ValueError, match="name segment"):
            await storage.read(42, name)
        assert client.calls == []

    async def test_stat_reports_what_was_put(self) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens")
        await storage.put(42, "a.jpg", b"jpeg!", content_type="image/jpeg")

        stat = await storage.stat(42, "a.jpg")

        assert (stat.key, stat.size, stat.content_type) == (
            "teens/42/a.jpg",
            5,
            "image/jpeg",
        )

    async def test_read_whole_and_capped(self) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens")
        await storage.put(42, "a.bin", b"x" * 100)

        assert await storage.read(42, "a.bin") == b"x" * 100
        assert await storage.read(42, "a.bin", max_bytes=11) == b"x" * 11
        assert await storage.read(42, "a.bin", max_bytes=500) == b"x" * 100

    async def test_limit_plus_one_detects_an_oversized_object(self) -> None:
        """The documented recipe: ``limit + 1`` bytes tell "too large" apart."""
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens")
        limit = 10
        await storage.put(42, "fits.bin", b"a" * limit)
        await storage.put(42, "big.bin", b"b" * 10_000)

        fits = await storage.read(42, "fits.bin", max_bytes=limit + 1)
        big = await storage.read(42, "big.bin", max_bytes=limit + 1)

        assert len(fits) == limit
        assert len(big) == limit + 1

    async def test_read_stops_at_max_bytes_when_the_store_ignores_range(self) -> None:
        """Memory stays bounded even if the store answers with everything."""
        client = _RangeIgnoringClient()
        storage = SubjectObjectStorage(client, prefix="teens")
        await storage.put(42, "big.bin", b"z" * (3 * 64 * 1024))

        data = await storage.read(42, "big.bin", max_bytes=10)

        assert data == b"z" * 10

    @pytest.mark.parametrize("max_bytes", [0, -1])
    async def test_max_bytes_must_be_positive(self, max_bytes: int) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens")

        with pytest.raises(ValueError, match="max_bytes"):
            await storage.read(42, "a.bin", max_bytes=max_bytes)
        assert client.calls == []

    async def test_a_missing_object_propagates_the_client_error(self) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens")

        with pytest.raises(FileNotFoundError, match=r"NoSuchKey: teens/42/a\.jpg"):
            await storage.stat(42, "a.jpg")
        with pytest.raises(FileNotFoundError, match="NoSuchKey"):
            await storage.read(42, "a.jpg", max_bytes=5)

    async def test_another_subject_is_never_read(self) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens")
        await storage.put(7, "a.jpg", b"seven")

        with pytest.raises(FileNotFoundError):
            await storage.read(77, "a.jpg")
        assert await storage.read(7, "a.jpg") == b"seven"

    async def test_disabled_storage_refuses_with_503(self) -> None:
        client = AsyncMinIOClient(
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
            enabled=False,
        )
        storage = SubjectObjectStorage(client, prefix="teens")

        with pytest.raises(IntegrationDisabledException) as put_error:
            await storage.presign_put(42, "a.jpg")
        with pytest.raises(IntegrationDisabledException):
            await storage.stat(42, "a.jpg")
        with pytest.raises(IntegrationDisabledException):
            await storage.read(42, "a.jpg", max_bytes=10)
        assert put_error.value.status_code == 503
        assert put_error.value.details == {"integration": "storage"}
