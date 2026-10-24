"""``make_local_storage_router`` — the routes behind the local signed links.

Every link here comes from the client itself (``presigned_get_url`` /
``presigned_put_url``), so the tests exercise the same path string the
signature covers. What is pinned: a valid link works, an expired or tampered
one is ``403`` with the signed-URL codes, a download link cannot upload, and
an upload past the cap leaves nothing on disk.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.storage import (
    LOCAL_STORAGE_GET_PURPOSE,
    LocalObjectStorageClient,
    make_local_storage_router,
)
from tempest_fastapi_sdk.utils.signed_url import sign_path

SECRET: str = "test-local-router-secret"
"""Signing secret of the store under test."""


def build(
    tmp_path: Path, **router_kwargs: object
) -> tuple[LocalObjectStorageClient, TestClient]:
    """Build a store, mount its router at the app root, and wrap a client.

    Args:
        tmp_path (Path): The store's root.
        **router_kwargs (object): Keyword arguments for the router factory.

    Returns:
        tuple[LocalObjectStorageClient, TestClient]: The store and the HTTP
        client.
    """
    store = LocalObjectStorageClient(
        tmp_path,
        public_url="http://testserver",
        signing_secret=SECRET,
        default_bucket="media",
        route_prefix="/api/storage",
        max_upload_bytes=10,
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_local_storage_router(store, **router_kwargs))  # type: ignore[arg-type]
    return store, TestClient(app)


def relative(url: str) -> str:
    """Strip scheme and host from a signed URL.

    Args:
        url (str): The absolute URL.

    Returns:
        str: Path and query string.
    """
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


class TestDownload:
    async def test_valid_link_streams_the_object(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        await store.put_object(
            "teens/1/a.png", b"\x89PNG-bytes", content_type="image/png"
        )
        response = http.get(relative(await store.presigned_get_url("teens/1/a.png")))
        assert response.status_code == 200
        assert response.content == b"\x89PNG-bytes"
        assert response.headers["content-type"] == "image/png"
        assert response.headers["content-disposition"].startswith("inline")
        assert response.headers["cache-control"] == "private, no-store"

    async def test_range_request(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        await store.put_object("v.bin", b"0123456789")
        response = http.get(
            relative(await store.presigned_get_url("v.bin")),
            headers={"Range": "bytes=2-4"},
        )
        assert response.status_code == 206
        assert response.content == b"234"
        assert response.headers["content-range"] == "bytes 2-4/10"

    async def test_missing_object_is_404(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        response = http.get(relative(await store.presigned_get_url("gone.bin")))
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"

    async def test_unsigned_request_is_403(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        await store.put_object("a.txt", b"x")
        response = http.get("/api/storage/media/a.txt")
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"

    async def test_tampered_path_is_403(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        await store.put_object("a.txt", b"x")
        await store.put_object("b.txt", b"y")
        link = relative(await store.presigned_get_url("a.txt"))
        response = http.get(link.replace("a.txt", "b.txt"))
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"

    async def test_expired_link_is_403(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        await store.put_object("a.txt", b"x")
        past = datetime.now(UTC) - timedelta(hours=2)
        link = sign_path(
            "/api/storage/media/a.txt",
            secret=SECRET,
            expires_in=timedelta(minutes=1),
            purpose=LOCAL_STORAGE_GET_PURPOSE,
            now=past,
        )
        response = http.get(link)
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_EXPIRED"

    async def test_attachment_mode(self, tmp_path: Path) -> None:
        store, http = build(tmp_path, as_attachment=True)
        await store.put_object("a.png", b"x", content_type="image/png")
        response = http.get(relative(await store.presigned_get_url("a.png")))
        assert response.headers["content-disposition"].startswith("attachment")


class TestUpload:
    async def test_valid_link_stores_the_body(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        link = relative(await store.presigned_put_url("teens/1/up.jpg"))
        response = http.put(
            link, content=b"jpeg", headers={"Content-Type": "image/jpeg"}
        )
        assert response.status_code == 200
        stat = await store.stat_object("teens/1/up.jpg")
        assert (stat.size, stat.content_type) == (4, "image/jpeg")
        assert response.headers["etag"] == f'"{stat.etag}"'

    async def test_download_link_cannot_upload(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        link = relative(await store.presigned_get_url("x.bin"))
        response = http.put(link, content=b"payload")
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"
        assert await store.list_objects() == []

    async def test_upload_link_cannot_download(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        await store.put_object("x.bin", b"secret")
        response = http.get(relative(await store.presigned_put_url("x.bin")))
        assert response.status_code == 403

    async def test_declared_oversize_body_is_413(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        link = relative(await store.presigned_put_url("big.bin"))
        response = http.put(link, content=b"x" * 11)
        assert response.status_code == 413
        assert response.json()["code"] == "FILE_TOO_LARGE"
        assert await store.list_objects() == []

    async def test_streamed_oversize_body_is_cut(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        link = relative(await store.presigned_put_url("big.bin"))

        def body() -> object:
            yield b"x" * 6
            yield b"x" * 6

        response = http.put(link, content=body())  # type: ignore[arg-type]
        assert response.status_code == 413
        assert await store.list_objects() == []
        assert list((tmp_path / "tmp").iterdir()) == []

    async def test_routes_stay_out_of_the_schema(self, tmp_path: Path) -> None:
        _store, http = build(tmp_path)
        assert http.app.openapi()["paths"] == {}  # type: ignore[attr-defined]


@pytest.mark.parametrize("prefix", ["/storage", "/api/v1/files"])
async def test_router_carries_the_client_prefix(tmp_path: Path, prefix: str) -> None:
    store = LocalObjectStorageClient(
        tmp_path,
        public_url="http://testserver",
        signing_secret=SECRET,
        route_prefix=prefix,
    )
    router = make_local_storage_router(store)
    assert {route.path for route in router.routes} == {  # type: ignore[attr-defined]
        f"{prefix}/{{bucket}}/{{key:path}}"
    }
