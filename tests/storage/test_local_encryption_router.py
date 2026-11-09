"""The signed routes of an encrypting ``LocalObjectStorageClient``.

The app's clients never see the cipher: a signed ``PUT`` or ``POST`` stores
sealed bytes, a signed ``GET`` answers the plaintext with a plaintext
``Content-Length``, and a ``Range`` answers the plaintext slice. A stored
file that fails its check answers ``500 STORAGE_INTEGRITY_ERROR`` before any
byte of it is sent.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.storage import (
    LOCAL_ENCRYPTION_BLOCK_SIZE,
    LOCAL_ENCRYPTION_MAGIC,
    POST_FILE_FIELD,
    LocalObjectStorageClient,
    make_local_storage_router,
)

SECRET: str = "test-local-encryption-router-secret"
"""Signing secret of the store under test."""

KEY: bytes = bytes(range(32))
"""The store's only key."""

BLOCK: int = LOCAL_ENCRYPTION_BLOCK_SIZE
"""Plaintext bytes per block."""

BODY: bytes = (b"PHOTO-BYTES-" * (3 * BLOCK // 12 + 1))[: 3 * BLOCK + 9]
"""A payload of more than three blocks."""


def build(
    tmp_path: Path, **client_kwargs: object
) -> tuple[LocalObjectStorageClient, TestClient]:
    """Build an encrypting store, mount its router and wrap a client.

    Args:
        tmp_path (Path): The store's root.
        **client_kwargs (object): Keyword arguments replacing the defaults.

    Returns:
        tuple[LocalObjectStorageClient, TestClient]: The store and the HTTP
        client, which returns server errors as responses.
    """
    kwargs: dict[str, object] = {
        "public_url": "http://testserver",
        "signing_secret": SECRET,
        "default_bucket": "media",
        "route_prefix": "/api/storage",
        "max_upload_bytes": 4 * BLOCK,
        "encryption_keys": {"k1": KEY},
    }
    kwargs.update(client_kwargs)
    store = LocalObjectStorageClient(tmp_path, **kwargs)  # type: ignore[arg-type]
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_local_storage_router(store))
    return store, TestClient(app, raise_server_exceptions=False)


def relative(url: str) -> str:
    """Strip scheme and host from a signed URL.

    Args:
        url (str): The absolute URL.

    Returns:
        str: Path and query string.
    """
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


class TestSignedUpload:
    async def test_put_stores_sealed_bytes_and_get_serves_plaintext(
        self, tmp_path: Path
    ) -> None:
        """The app uploads and downloads plaintext; the disk holds ciphertext."""
        store, http = build(tmp_path)
        upload = http.put(
            relative(await store.presigned_put_url("teens/1/a.jpg")),
            content=BODY,
            headers={"content-type": "image/jpeg"},
        )
        assert upload.status_code == 200
        stored = store.object_path("teens/1/a.jpg").read_bytes()
        assert stored.startswith(LOCAL_ENCRYPTION_MAGIC)
        assert b"PHOTO-BYTES" not in stored
        download = http.get(relative(await store.presigned_get_url("teens/1/a.jpg")))
        assert download.status_code == 200
        assert download.content == BODY
        assert download.headers["content-length"] == str(len(BODY))
        assert download.headers["content-type"] == "image/jpeg"
        assert (await store.stat_object("teens/1/a.jpg")).size == len(BODY)

    async def test_put_cap_counts_plaintext(self, tmp_path: Path) -> None:
        """A body exactly at the cap fits; the header and tags do not count."""
        store, http = build(tmp_path, max_upload_bytes=BLOCK)
        url = relative(await store.presigned_put_url("a.bin"))
        assert http.put(url, content=b"x" * BLOCK).status_code == 200
        assert http.put(url, content=b"x" * (BLOCK + 1)).status_code == 413

    async def test_post_form_stores_sealed_bytes(self, tmp_path: Path) -> None:
        """The form upload goes through the same sealing writer."""
        store, http = build(tmp_path)
        post = await store.presigned_post_policy(
            "evidence/a.jpg", max_bytes=len(BODY), content_type="image/jpeg"
        )
        parts = urlsplit(post.url)
        response = http.post(
            f"{parts.path}?{parts.query}",
            data=post.fields,
            files={POST_FILE_FIELD: ("a.jpg", BODY, "image/jpeg")},
        )
        assert response.status_code == 204
        assert b"PHOTO-BYTES" not in store.object_path("evidence/a.jpg").read_bytes()
        download = http.get(relative(await store.presigned_get_url("evidence/a.jpg")))
        assert download.content == BODY


class TestSignedDownload:
    async def test_range_across_blocks_answers_the_plaintext_slice(
        self, tmp_path: Path
    ) -> None:
        """``Range`` counts plaintext bytes and crosses block boundaries."""
        store, http = build(tmp_path)
        await store.put_object("a.bin", BODY)
        start, end = BLOCK - 5, 2 * BLOCK + 4
        response = http.get(
            relative(await store.presigned_get_url("a.bin")),
            headers={"range": f"bytes={start}-{end}"},
        )
        assert response.status_code == 206
        assert response.content == BODY[start : end + 1]
        assert response.headers["content-range"] == f"bytes {start}-{end}/{len(BODY)}"

    async def test_suffix_range_answers_the_tail(self, tmp_path: Path) -> None:
        """``bytes=-N`` is resolved against the plaintext size."""
        store, http = build(tmp_path)
        await store.put_object("a.bin", BODY)
        response = http.get(
            relative(await store.presigned_get_url("a.bin")),
            headers={"range": "bytes=-20"},
        )
        assert response.status_code == 206
        assert response.content == BODY[-20:]

    async def test_range_past_the_plaintext_end_is_416(self, tmp_path: Path) -> None:
        """The ciphertext's extra bytes are not addressable."""
        store, http = build(tmp_path)
        await store.put_object("a.bin", b"0123456789")
        response = http.get(
            relative(await store.presigned_get_url("a.bin")),
            headers={"range": "bytes=10-20"},
        )
        assert response.status_code == 416

    async def test_tampered_file_answers_500_without_bytes(
        self, tmp_path: Path
    ) -> None:
        """A first block that fails is a clean error, not a partial body."""
        store, http = build(tmp_path)
        await store.put_object("a.bin", BODY)
        path = store.object_path("a.bin")
        data = bytearray(path.read_bytes())
        data[40] ^= 0x01
        path.write_bytes(bytes(data))
        response = http.get(relative(await store.presigned_get_url("a.bin")))
        assert response.status_code == 500
        assert response.json()["code"] == "STORAGE_INTEGRITY_ERROR"
        assert response.json()["details"] == {"reason": "authentication"}
        assert b"PHOTO-BYTES" not in response.content

    async def test_plaintext_file_is_refused_without_the_flag(
        self, tmp_path: Path
    ) -> None:
        """A file dropped in the tree unencrypted is not served."""
        store, http = build(tmp_path)
        target = store.object_path("legacy.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"legacy plaintext")
        response = http.get(relative(await store.presigned_get_url("legacy.txt")))
        assert response.status_code == 500
        assert response.json()["details"] == {"reason": "plaintext"}
        assert b"legacy plaintext" not in response.content

    async def test_plaintext_file_is_served_with_the_flag(self, tmp_path: Path) -> None:
        """``allow_plaintext_read`` keeps a not-yet-migrated store working."""
        store, http = build(tmp_path, allow_plaintext_read=True)
        target = store.object_path("legacy.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"legacy plaintext")
        response = http.get(relative(await store.presigned_get_url("legacy.txt")))
        assert response.status_code == 200
        assert response.content == b"legacy plaintext"
