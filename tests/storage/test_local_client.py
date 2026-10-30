"""``LocalObjectStorageClient`` — the disk-backed ``SubjectStorageClient``.

The client answers the same calls as ``AsyncMinIOClient`` from a directory,
so these tests pin the S3-shaped behaviour (``NoSuchKey`` on a missing key,
a no-op delete of a missing key, a ``/``-ended "directory" in a
non-recursive listing) and what a disk adds: atomic replacement, keys that
cannot leave the store, and links signed to the app's own routes.
"""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tempest_fastapi_sdk import IntegrationDisabledException
from tempest_fastapi_sdk.exceptions.upload import FileTooLargeException
from tempest_fastapi_sdk.privacy import SubjectErasureError, SubjectObjectStorage
from tempest_fastapi_sdk.storage import (
    LOCAL_STORAGE_GET_PURPOSE,
    LOCAL_STORAGE_PUT_PURPOSE,
    LocalObjectNotFoundError,
    LocalObjectStorageClient,
)
from tempest_fastapi_sdk.utils.signed_url import verify_path

SECRET: str = "test-local-storage-secret"
"""Signing secret of every client built here."""


def make_client(base: Path, **overrides: object) -> LocalObjectStorageClient:
    """Build a client rooted at ``base``.

    Args:
        base (Path): The store's root.
        **overrides (object): Keyword arguments replacing the defaults.

    Returns:
        LocalObjectStorageClient: The client.
    """
    kwargs: dict[str, object] = {
        "public_url": "https://api.example.com/",
        "signing_secret": SECRET,
        "default_bucket": "media",
    }
    kwargs.update(overrides)
    return LocalObjectStorageClient(base, **kwargs)  # type: ignore[arg-type]


async def collect(stream: AsyncIterator[bytes]) -> bytes:
    """Drain a chunk stream.

    Args:
        stream (AsyncIterator[bytes]): The stream.

    Returns:
        bytes: Every chunk, joined.
    """
    return b"".join([chunk async for chunk in stream])


async def chunks_of(*parts: bytes) -> AsyncIterator[bytes]:
    """Yield ``parts`` as an async chunk stream.

    Args:
        *parts (bytes): The chunks.

    Yields:
        bytes: The next chunk.
    """
    for part in parts:
        yield part


class TestRoundTrip:
    async def test_put_stat_stream_list_remove(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        etag = await client.put_object(
            "docs/a.txt",
            b"hello world",
            content_type="text/plain",
            metadata={"owner": "42"},
        )
        assert etag == hashlib.md5(b"hello world", usedforsecurity=False).hexdigest()

        stat = await client.stat_object("docs/a.txt")
        assert (stat.bucket, stat.key, stat.size) == ("media", "docs/a.txt", 11)
        assert stat.etag == etag
        assert stat.content_type == "text/plain"
        assert stat.metadata == {"owner": "42"}
        assert stat.last_modified is not None

        assert await collect(await client.stream_object("docs/a.txt")) == b"hello world"
        assert await client.list_objects() == ["docs/a.txt"]

        await client.remove_object("docs/a.txt")
        assert await client.list_objects() == []
        with pytest.raises(LocalObjectNotFoundError):
            await client.stat_object("docs/a.txt")

    async def test_bytes_land_where_the_layout_says(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("a/b.bin", b"x")
        assert (tmp_path / "objects" / "media" / "a" / "b.bin").read_bytes() == b"x"
        assert (tmp_path / "meta" / "media" / "a" / "b.bin.meta.json").is_file()
        assert list((tmp_path / "tmp").iterdir()) == []

    async def test_stream_put_respects_length(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("s.bin", io.BytesIO(b"abcdef"), length=4)
        assert await collect(await client.stream_object("s.bin")) == b"abcd"

    async def test_stream_put_without_length_reads_to_the_end(
        self, tmp_path: Path
    ) -> None:
        client = make_client(tmp_path)
        await client.put_object("s.bin", io.BytesIO(b"abcdef"), length=-1)
        assert await collect(await client.stream_object("s.bin")) == b"abcdef"

    async def test_byte_range(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("r.bin", b"0123456789")
        part = await client.stream_object("r.bin", offset=2, length=3)
        assert await collect(part) == b"234"
        tail = await client.stream_object("r.bin", offset=8, length=50)
        assert await collect(tail) == b"89"

    async def test_small_chunks(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("c.bin", b"abcdefg")
        chunks = [c async for c in await client.stream_object("c.bin", chunk_size=3)]
        assert chunks == [b"abc", b"def", b"g"]

    async def test_bad_range_arguments(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("r.bin", b"x")
        with pytest.raises(ValueError, match="offset"):
            await client.stream_object("r.bin", offset=-1)
        with pytest.raises(ValueError, match="length"):
            await client.stream_object("r.bin", length=0)

    async def test_missing_object_carries_the_s3_code(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        with pytest.raises(LocalObjectNotFoundError) as caught:
            await client.stream_object("nope.bin")
        assert caught.value.code == "NoSuchKey"
        assert isinstance(caught.value, FileNotFoundError)

    async def test_buckets_are_separate(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("k", b"default")
        await client.put_object("k", b"other", bucket="other")
        assert await collect(await client.stream_object("k")) == b"default"
        assert (
            await collect(await client.stream_object("k", bucket="other")) == b"other"
        )
        assert await client.list_objects(bucket="empty") == []


class TestListing:
    async def test_prefix_filter_is_a_string_prefix(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        for key in ("users/1/a", "users/10/b", "users/1/c/d"):
            await client.put_object(key, b"x")
        assert await client.list_objects("users/1/") == ["users/1/a", "users/1/c/d"]

    async def test_non_recursive_lists_one_level(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        for key in ("top.txt", "dir/a", "dir/sub/b"):
            await client.put_object(key, b"x")
        assert await client.list_objects(recursive=False) == ["dir/", "top.txt"]
        assert await client.list_objects("dir/", recursive=False) == [
            "dir/a",
            "dir/sub/",
        ]

    @pytest.mark.parametrize(
        "prefix",
        ["../", "../../", "../../../", "dir/../../", "./", "dir//", "dir/./"],
    )
    async def test_non_recursive_prefix_cannot_leave_the_bucket(
        self, tmp_path: Path, prefix: str
    ) -> None:
        """A ``../`` prefix matches nothing, as it would on S3."""
        store = tmp_path / "store"
        client = make_client(store)
        await client.put_object("dir/a", b"x")
        await client.put_object("own.txt", b"x", bucket="otherbucket")
        (tmp_path / "outside-dir").mkdir()
        assert await client.list_objects(prefix, recursive=False) == []

    async def test_non_recursive_prefix_cannot_follow_a_symlink_out(
        self, tmp_path: Path
    ) -> None:
        store = tmp_path / "store"
        client = make_client(store)
        await client.put_object("dir/a", b"x")
        outside = tmp_path / "outside-dir"
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"secret")
        (store / "objects" / "media" / "escape").symlink_to(outside)
        assert await client.list_objects("escape/", recursive=False) == []


class TestRemoval:
    async def test_missing_key_is_not_an_error(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.remove_object("never-stored")
        assert await client.list_objects() == []

    async def test_empty_directories_are_pruned(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("a/b/c.txt", b"x")
        await client.remove_object("a/b/c.txt")
        assert not (tmp_path / "objects" / "media" / "a").exists()
        assert not (tmp_path / "meta" / "media" / "a").exists()

    async def test_remove_objects_in_batch(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        for key in ("x/1", "x/2", "y/3"):
            await client.put_object(key, b"x")
        errors = await client.remove_objects(["x/1", "x/2", "x/2", "missing"])
        assert errors == []
        assert await client.list_objects() == ["y/3"]

    async def test_remove_objects_reports_invalid_keys(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("ok", b"x")
        errors = await client.remove_objects(["ok", "../escape"])
        assert [(e.key, e.code) for e in errors] == [("../escape", "InternalError")]
        assert await client.list_objects() == []


class TestPathSafety:
    @pytest.mark.parametrize(
        "key",
        [
            "../outside",
            "a/../../outside",
            "/absolute",
            "a//b",
            "a/./b",
            "",
            "a\\b",
            "name.meta.json",
            "dir.meta.json/x",
        ],
    )
    async def test_key_that_could_leave_the_bucket_is_refused(
        self, tmp_path: Path, key: str
    ) -> None:
        client = make_client(tmp_path)
        with pytest.raises(ValueError):
            await client.put_object(key, b"x")
        with pytest.raises(ValueError):
            await client.presigned_get_url(key)

    @pytest.mark.parametrize("bucket", ["..", ".hidden", "a/b", ""])
    async def test_invalid_bucket_is_refused(self, tmp_path: Path, bucket: str) -> None:
        client = make_client(tmp_path)
        with pytest.raises(ValueError):
            await client.put_object("k", b"x", bucket=bucket or "a/")

    async def test_symlink_out_of_the_store_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret")
        client = make_client(tmp_path / "store")
        bucket_dir = tmp_path / "store" / "objects" / "media"
        bucket_dir.mkdir(parents=True)
        os.symlink(outside, bucket_dir / "link")
        with pytest.raises(ValueError, match="escapes"):
            await client.stream_object("link/secret.txt")


class TestAtomicWrite:
    async def test_replacement_is_one_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = make_client(tmp_path)
        await client.put_object("f.bin", b"old")
        target = tmp_path / "objects" / "media" / "f.bin"
        seen: list[bytes] = []
        real_replace = os.replace

        def spy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            if Path(dst) == target:
                seen.append(target.read_bytes())
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy)
        await client.put_object("f.bin", b"new-and-longer")
        assert seen == [b"old"]
        assert target.read_bytes() == b"new-and-longer"

    async def test_failed_stream_leaves_the_old_object(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        await client.put_object("f.bin", b"old")
        with pytest.raises(FileTooLargeException):
            await client.write_stream(
                "f.bin", chunks_of(b"12345", b"67890"), max_bytes=6
            )
        assert await collect(await client.stream_object("f.bin")) == b"old"
        assert list((tmp_path / "tmp").iterdir()) == []

    async def test_write_stream_within_the_cap(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        etag = await client.write_stream(
            "w.bin",
            chunks_of(b"ab", b"cd"),
            content_type="image/png",
            max_bytes=4,
        )
        stat = await client.stat_object("w.bin")
        assert (stat.size, stat.content_type, stat.etag) == (4, "image/png", etag)


class TestPresignedLinks:
    async def test_get_link_is_signed_for_the_route(self, tmp_path: Path) -> None:
        client = make_client(tmp_path, route_prefix="/api/storage/")
        url = await client.presigned_get_url(
            "docs/report 1.pdf", expires=timedelta(minutes=5)
        )
        parts = urlsplit(url)
        assert (parts.scheme, parts.netloc) == ("https", "api.example.com")
        assert parts.path == "/api/storage/media/docs/report%201.pdf"
        query = parse_qs(parts.query)
        verify_path(
            "/api/storage/media/docs/report 1.pdf",
            expires=int(query["expires"][0]),
            signature=query["signature"][0],
            secret=SECRET,
            purpose=LOCAL_STORAGE_GET_PURPOSE,
        )

    async def test_put_link_uses_the_upload_purpose(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        url = await client.presigned_put_url("up.jpg", bucket="other")
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        verify_path(
            "/storage/other/up.jpg",
            expires=int(query["expires"][0]),
            signature=query["signature"][0],
            secret=SECRET,
            purpose=LOCAL_STORAGE_PUT_PURPOSE,
        )

    async def test_expiry_over_seven_days_is_refused(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        with pytest.raises(ValueError, match="7 days"):
            await client.presigned_get_url("k", expires=timedelta(days=8))


class TestConstruction:
    def test_empty_secret_fails_at_build_time(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="signing_secret"):
            make_client(tmp_path, signing_secret="")

    def test_public_url_needs_a_scheme(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="public_url"):
            make_client(tmp_path, public_url="api.example.com")

    def test_route_prefix_must_be_absolute(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="route_prefix"):
            make_client(tmp_path, route_prefix="storage")

    async def test_disabled_client_refuses_every_call(self, tmp_path: Path) -> None:
        client = make_client(tmp_path / "never", enabled=False, signing_secret="")
        assert not (tmp_path / "never").exists()
        calls = [
            client.put_object("k", b"x"),
            client.presigned_get_url("k"),
            client.presigned_put_url("k"),
            client.stat_object("k"),
            client.stream_object("k"),
            client.list_objects(),
            client.remove_object("k"),
            client.remove_objects(["k"]),
            client.write_stream("k", chunks_of(b"x")),
        ]
        for call in calls:
            with pytest.raises(IntegrationDisabledException):
                await call


class TestUnderSubjectObjectStorage:
    async def test_the_whole_subject_lifecycle(self, tmp_path: Path) -> None:
        client = make_client(tmp_path)
        storage = SubjectObjectStorage(client, prefix="teens")
        key = await storage.put(
            7, "export.json", b"{}", content_type="application/json"
        )
        assert key == "teens/7/export.json"
        await storage.put(7, "photos/a.jpg", b"jpeg-bytes", content_type="image/jpeg")
        await storage.put(70, "other.txt", b"x")

        assert await storage.names(7) == ["export.json", "photos/a.jpg"]
        stat = await storage.stat(7, "photos/a.jpg")
        assert (stat.size, stat.content_type) == (10, "image/jpeg")
        assert await storage.read(7, "photos/a.jpg", max_bytes=4) == b"jpeg"
        assert "/storage/media/teens/7/export.json?" in await storage.presign(
            7, "export.json"
        )
        assert "/storage/media/teens/7/up.jpg?" in await storage.presign_put(
            7, "up.jpg"
        )

        await storage.delete(7, "export.json")
        assert await storage.delete_all(7) == 1
        assert await storage.names(7) == []
        assert await storage.names(70) == ["other.txt"]
        assert await storage.delete_all(7) == 0

    async def test_refused_delete_surfaces_as_erasure_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = make_client(tmp_path)
        storage = SubjectObjectStorage(client, prefix="teens")
        await storage.put(1, "a.txt", b"x")
        real_unlink = Path.unlink

        def refuse(self: Path, missing_ok: bool = False) -> None:
            if self.name == "a.txt":
                raise PermissionError("read-only")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", refuse)
        with pytest.raises(SubjectErasureError) as caught:
            await storage.delete_all(1)
        assert [(e.key, e.code) for e in caught.value.errors] == [
            ("teens/1/a.txt", "AccessDenied")
        ]
