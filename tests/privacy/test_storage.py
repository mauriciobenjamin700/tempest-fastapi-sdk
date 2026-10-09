"""Tests for tempest_fastapi_sdk.privacy.SubjectObjectStorage (offline fake)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from tempest_fastapi_sdk import AsyncMinIOClient
from tempest_fastapi_sdk.privacy import SubjectErasureError, SubjectObjectStorage


class _FakeMinio:
    """In-memory stand-in for the ``minio.Minio`` calls the storage makes."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, bytes]] = {}
        self.batches: list[list[str]] = []
        self.single_deletes: list[str] = []
        self.refuse: set[str] = set()

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

    async def test_bucket_override(self, fake: _FakeMinio) -> None:
        client = AsyncMinIOClient("fake:9000", "ak", "sk", default_bucket="media")
        storage = SubjectObjectStorage(client, bucket="private")
        await storage.put("u", "a", b"x")
        assert list(fake.objects) == ["private"]
        assert await storage.delete_all("u") == 1
