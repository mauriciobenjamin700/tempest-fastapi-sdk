"""The admin storage browser: list, preview, upload, edit, rename, delete.

Measured against :class:`LocalObjectStorageClient` on a temporary directory —
the same five calls :class:`AsyncMinIOClient` answers — so every assertion is
about bytes that really landed on (or left) the store, not about a fake's
bookkeeping.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AdminSite,
    AdminSurface,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    StorageBrowserError,
    StorageBrowserService,
    StorageObjectMissingError,
    StoragePreview,
    check_storage_key,
    storage_preview_for,
)
from tempest_fastapi_sdk.storage import LocalObjectStorageClient
from tempest_fastapi_sdk.testing import admin_login

SECRET = "x" * 48

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)
"""A valid 1x1 transparent PNG."""


class StorageUser(BaseUserModel):
    __tablename__ = "admin_storage_users"


def _store(tmp_path: Path) -> LocalObjectStorageClient:
    """Build a local store rooted in ``tmp_path``.

    Args:
        tmp_path (Path): Pytest temporary directory.

    Returns:
        LocalObjectStorageClient: The store.
    """
    return LocalObjectStorageClient(
        tmp_path / "store",
        public_url="http://test",
        signing_secret="s" * 32,
    )


class _Reader:
    """An ``UploadFile.read`` stand-in over fixed bytes."""

    def __init__(self, payload: bytes) -> None:
        """Keep the payload.

        Args:
            payload (bytes): What the reader returns, chunk by chunk.
        """
        self._payload = payload
        self._offset = 0

    async def read(self, size: int) -> bytes:
        """Return the next ``size`` bytes, ``b""`` at the end.

        Args:
            size (int): Bytes to read.

        Returns:
            bytes: The chunk.
        """
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


@pytest.fixture
async def db() -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a connected in-memory database with an admin user.

    Yields:
        AsyncDatabaseManager: The manager the admin uses.
    """
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    async with manager.get_session_context() as session:
        user = StorageUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
    yield manager
    await manager.drop_tables()
    await manager.disconnect()


def _app(
    manager: AsyncDatabaseManager,
    browser: StorageBrowserService | None,
    **options: Any,
) -> FastAPI:
    """Mount the admin with the storage browser.

    Args:
        manager (AsyncDatabaseManager): The database.
        browser (StorageBrowserService | None): The browser, if mounted.
        **options (Any): Forwarded to ``make_admin_router``.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    app.include_router(
        make_admin_router(
            AdminSite(title="Storage Admin"),
            db=manager,
            auth_backend=UserModelAuthBackend(StorageUser),
            secret_key=SECRET,
            cookie_secure=False,
            storage=browser,
            **options,
        )
    )
    return app


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Open a client and sign the admin in.

    Args:
        app (FastAPI): The app.

    Yields:
        AsyncClient: The signed-in client.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "root@example.com", "hunter2")
        yield client


def _csrf(html: str) -> str:
    """Pull the CSRF token out of a rendered page.

    Args:
        html (str): The page.

    Returns:
        str: The token.
    """
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None, "the page carries no csrf_token"
    return match.group(1)


class TestKeysAndPreview:
    @pytest.mark.parametrize(
        "key",
        ["", "/a", "a/", "a//b", "a/../b", "./a", "a\\b", "a\x00b", "x" * 1025],
    )
    def test_a_key_that_cannot_be_a_path_is_refused(self, key: str) -> None:
        with pytest.raises(StorageBrowserError):
            check_storage_key(key)

    def test_a_plain_nested_key_is_accepted(self) -> None:
        assert check_storage_key("avatars/2026/a b.png") == "avatars/2026/a b.png"

    @pytest.mark.parametrize(
        ("content_type", "key", "expected"),
        [
            ("image/png", "a.png", StoragePreview.IMAGE),
            ("video/mp4", "a.mp4", StoragePreview.VIDEO),
            ("audio/mpeg", "a.mp3", StoragePreview.AUDIO),
            ("application/pdf", "a.pdf", StoragePreview.PDF),
            ("text/plain; charset=utf-8", "a.txt", StoragePreview.TEXT),
            ("application/json", "a.json", StoragePreview.TEXT),
            ("application/octet-stream", "a.pdf", StoragePreview.PDF),
            (None, "notes.md", StoragePreview.TEXT),
            ("application/zip", "a.zip", StoragePreview.NONE),
        ],
    )
    def test_the_preview_follows_the_type(
        self,
        content_type: str | None,
        key: str,
        expected: StoragePreview,
    ) -> None:
        assert storage_preview_for(content_type, key) is expected

    @pytest.mark.parametrize("content_type", ["image/svg+xml", "text/html"])
    def test_script_capable_types_preview_as_source_never_rendered(
        self, content_type: str
    ) -> None:
        """SVG and HTML can run script on the admin's origin if rendered."""
        assert storage_preview_for(content_type, "x") is StoragePreview.TEXT


class TestService:
    async def test_a_folder_lists_folders_first_then_files(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("b.txt", b"b", content_type="text/plain")
        await store.put_object("a/x.txt", b"x")
        await store.put_object("a/y/z.txt", b"z")
        await store.put_object("c/w.txt", b"w")
        browser = StorageBrowserService(store)

        root = await browser.browse("")
        assert [(e.name, e.is_folder) for e in root.entries] == [
            ("a", True),
            ("c", True),
            ("b.txt", False),
        ]
        assert root.entries[2].size == 1
        assert root.entries[2].content_type == "text/plain"

        nested = await browser.browse("a")
        assert nested.prefix == "a/"
        assert [(e.key, e.is_folder) for e in nested.entries] == [
            ("a/y/", True),
            ("a/x.txt", False),
        ]
        assert [c.prefix for c in nested.crumbs] == ["", "a/"]

    async def test_an_unknown_folder_is_an_empty_listing(self, tmp_path: Path) -> None:
        listing = await StorageBrowserService(_store(tmp_path)).browse("nope/")
        assert listing.entries == []
        assert listing.total == 0

    async def test_a_listing_pages(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        for index in range(5):
            await store.put_object(f"f{index}.txt", b".")
        browser = StorageBrowserService(store, page_size=2)
        last = await browser.browse("", page=99)
        assert (last.page, last.pages, last.total) == (3, 3, 5)
        assert [e.name for e in last.entries] == ["f4.txt"]

    async def test_upload_refuses_a_taken_key_unless_overwrite(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        browser = StorageBrowserService(store)
        key = await browser.upload("docs", "a.txt", _Reader(b"one").read)
        assert key == "docs/a.txt"
        stat = await store.stat_object(key)
        assert stat.content_type == "text/plain"
        with pytest.raises(StorageBrowserError, match="already exists"):
            await browser.upload("docs/", "a.txt", _Reader(b"two").read)
        await browser.upload("docs/", "a.txt", _Reader(b"two").read, overwrite=True)
        assert (await browser.describe(key)).text == "two"

    async def test_upload_over_the_cap_is_refused_and_nothing_is_written(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        browser = StorageBrowserService(store, max_upload_bytes=4)
        with pytest.raises(StorageBrowserError, match="larger than 4 bytes"):
            await browser.upload("", "big.bin", _Reader(b"12345").read)
        assert await store.list_objects("") == []

    async def test_describe_reads_text_and_marks_it_editable(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("n.json", b'{"a": 1}', content_type="application/json")
        view = await StorageBrowserService(store).describe("n.json")
        assert view.preview is StoragePreview.TEXT
        assert view.text == '{"a": 1}'
        assert view.editable is True

    async def test_text_that_does_not_decode_offers_download_only(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("bad.txt", b"\xff\xfe\x00", content_type="text/plain")
        view = await StorageBrowserService(store).describe("bad.txt")
        assert view.preview is StoragePreview.NONE
        assert view.text is None
        assert view.editable is False

    async def test_text_over_the_cap_is_not_loaded(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.put_object("log.txt", b"x" * 20, content_type="text/plain")
        view = await StorageBrowserService(store, max_text_bytes=10).describe("log.txt")
        assert view.preview is StoragePreview.TEXT
        assert view.text is None
        assert view.editable is False

    async def test_save_text_keeps_type_and_metadata(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.put_object(
            "a.txt", b"one\n", content_type="text/plain", metadata={"owner": "x"}
        )
        browser = StorageBrowserService(store)
        view = await browser.describe("a.txt")
        await browser.save_text("a.txt", "two\r\n", expected_etag=view.etag)
        stat = await store.stat_object("a.txt")
        assert (stat.content_type, stat.metadata) == ("text/plain", {"owner": "x"})
        assert (await browser.describe("a.txt")).text == "two\n"

    async def test_save_text_refuses_a_stale_editor(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.put_object("a.txt", b"one", content_type="text/plain")
        browser = StorageBrowserService(store)
        opened = await browser.describe("a.txt")
        await store.put_object("a.txt", b"someone else", content_type="text/plain")
        with pytest.raises(StorageBrowserError, match="changed since you opened it"):
            await browser.save_text("a.txt", "mine", expected_etag=opened.etag)
        assert (await browser.describe("a.txt")).text == "someone else"

    async def test_rename_moves_bytes_type_and_metadata(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.put_object(
            "a/p.png", PNG_1X1, content_type="image/png", metadata={"k": "v"}
        )
        browser = StorageBrowserService(store)
        assert await browser.rename("a/p.png", "/b/q.png") == "b/q.png"
        assert await store.list_objects("") == ["b/q.png"]
        stat = await store.stat_object("b/q.png")
        assert (stat.size, stat.content_type, stat.metadata) == (
            len(PNG_1X1),
            "image/png",
            {"k": "v"},
        )

    async def test_rename_refuses_a_taken_target(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.put_object("a.txt", b"a")
        await store.put_object("b.txt", b"b")
        with pytest.raises(StorageBrowserError, match="already exists"):
            await StorageBrowserService(store).rename("a.txt", "b.txt")
        assert await store.list_objects("") == ["a.txt", "b.txt"]

    async def test_delete_removes_and_a_second_delete_reports_missing(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("a.txt", b"a")
        browser = StorageBrowserService(store)
        await browser.delete("a.txt")
        assert await store.list_objects("") == []
        with pytest.raises(StorageObjectMissingError):
            await browser.delete("a.txt")

    async def test_read_only_refuses_every_write(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.put_object("a.txt", b"a", content_type="text/plain")
        browser = StorageBrowserService(store, read_only=True)
        assert (await browser.describe("a.txt")).editable is False
        for call in (
            browser.upload("", "b.txt", _Reader(b"b").read),
            browser.replace("a.txt", _Reader(b"b").read),
            browser.save_text("a.txt", "b", expected_etag=None),
            browser.rename("a.txt", "c.txt"),
            browser.delete("a.txt"),
        ):
            with pytest.raises(StorageBrowserError, match="read-only"):
                await call
        assert await store.list_objects("") == ["a.txt"]

    def test_limits_must_be_positive(self, tmp_path: Path) -> None:
        for option in ("page_size", "max_upload_bytes", "max_text_bytes"):
            with pytest.raises(ValueError, match=option):
                StorageBrowserService(_store(tmp_path), **{option: 0})


class TestPages:
    async def test_the_sidebar_links_the_browser_only_when_mounted(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        async with _client(_app(db, StorageBrowserService(_store(tmp_path)))) as c:
            assert 'href="/admin/storage"' in (await c.get("/admin/")).text
        async with _client(_app(db, None)) as c:
            assert 'href="/admin/storage"' not in (await c.get("/admin/")).text
            assert (await c.get("/admin/storage")).status_code == 404

    async def test_listing_and_object_pages_render(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("img/p.png", PNG_1X1, content_type="image/png")
        await store.put_object("readme.txt", b"<b>hi</b>", content_type="text/plain")
        async with _client(_app(db, StorageBrowserService(store))) as c:
            root = await c.get("/admin/storage")
            assert root.status_code == 200
            assert "img/</a>" in root.text
            assert "readme.txt</a>" in root.text

            image = await c.get("/admin/storage/object", params={"key": "img/p.png"})
            assert image.status_code == 200
            assert '<img src="/admin/storage/raw?key=img%2Fp.png"' in image.text

            text = await c.get("/admin/storage/object", params={"key": "readme.txt"})
            assert "&lt;b&gt;hi&lt;/b&gt;</textarea>" in text.text

            missing = await c.get("/admin/storage/object", params={"key": "nope"})
            assert missing.status_code == 404
            bad = await c.get("/admin/storage", params={"prefix": "../x"})
            assert bad.status_code == 400

    async def test_raw_is_inline_only_for_inline_safe_types(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("p.png", PNG_1X1, content_type="image/png")
        await store.put_object(
            "x.svg", b"<svg onload=alert(1)/>", content_type="image/svg+xml"
        )
        async with _client(_app(db, StorageBrowserService(store))) as c:
            png = await c.get("/admin/storage/raw", params={"key": "p.png"})
            assert png.status_code == 200
            assert png.content == PNG_1X1
            assert png.headers["content-disposition"].startswith("inline")
            assert png.headers["x-content-type-options"] == "nosniff"
            assert png.headers["cache-control"] == "private, no-store"

            svg = await c.get("/admin/storage/raw", params={"key": "x.svg"})
            assert svg.headers["content-disposition"].startswith("attachment")

            forced = await c.get(
                "/admin/storage/raw", params={"key": "p.png", "download": "true"}
            )
            assert forced.headers["content-disposition"].startswith("attachment")

            missing = await c.get("/admin/storage/raw", params={"key": "nope"})
            assert missing.status_code == 404

    async def test_upload_edit_rename_delete_round_trip(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        async with _client(_app(db, StorageBrowserService(store))) as c:
            token = _csrf((await c.get("/admin/storage")).text)

            uploaded = await c.post(
                "/admin/storage/upload",
                data={"csrf_token": token, "prefix": "docs/", "name": ""},
                files={"file": ("a.txt", b"one\n", "text/plain")},
            )
            assert uploaded.status_code == 303
            assert uploaded.headers["location"].startswith(
                "/admin/storage/object?key=docs%2Fa.txt"
            )
            assert await store.list_objects("") == ["docs/a.txt"]

            page = await c.get("/admin/storage/object", params={"key": "docs/a.txt"})
            etag = re.search(r'name="etag" value="([^"]*)"', page.text)
            assert etag is not None
            edited = await c.post(
                "/admin/storage/object/edit",
                data={
                    "csrf_token": token,
                    "key": "docs/a.txt",
                    "text": "two\n",
                    "etag": etag.group(1),
                },
            )
            assert edited.status_code == 303
            assert "flash_cat=error" not in edited.headers["location"]

            renamed = await c.post(
                "/admin/storage/object/rename",
                data={"csrf_token": token, "key": "docs/a.txt", "new_key": "b.txt"},
            )
            assert renamed.status_code == 303
            assert await store.list_objects("") == ["b.txt"]

            deleted = await c.post(
                "/admin/storage/object/delete",
                data={"csrf_token": token, "key": "b.txt"},
            )
            assert deleted.status_code == 303
            assert await store.list_objects("") == []

    async def test_a_refused_write_comes_back_as_an_error_flash(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("a.txt", b"a")
        async with _client(_app(db, StorageBrowserService(store))) as c:
            token = _csrf((await c.get("/admin/storage")).text)
            response = await c.post(
                "/admin/storage/upload",
                data={"csrf_token": token, "prefix": ""},
                files={"file": ("a.txt", b"b", "text/plain")},
            )
            assert response.status_code == 303
            assert "flash_cat=error" in response.headers["location"]
            followed = await c.get(response.headers["location"])
            assert "already exists" in followed.text

    async def test_writes_need_the_csrf_token(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("a.txt", b"a")
        async with _client(_app(db, StorageBrowserService(store))) as c:
            response = await c.post(
                "/admin/storage/object/delete",
                data={"csrf_token": "forged", "key": "a.txt"},
            )
            assert response.status_code == 403
        assert await store.list_objects("") == ["a.txt"]

    async def test_read_only_hides_the_forms_and_answers_403(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)
        await store.put_object("a.txt", b"a", content_type="text/plain")
        browser = StorageBrowserService(store, read_only=True)
        async with _client(_app(db, browser)) as c:
            listing = await c.get("/admin/storage")
            assert "Read-only" in listing.text
            assert "/admin/storage/upload" not in listing.text
            page = await c.get("/admin/storage/object", params={"key": "a.txt"})
            assert "/admin/storage/object/delete" not in page.text
            assert "<textarea" not in page.text
            token = _csrf((await c.get("/admin/")).text)
            response = await c.post(
                "/admin/storage/object/delete",
                data={"csrf_token": token, "key": "a.txt"},
            )
            assert response.status_code == 403
        assert await store.list_objects("") == ["a.txt"]

    async def test_a_denied_surface_is_403_and_off_the_sidebar(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        def deny_storage(_: Any, surface: AdminSurface) -> bool:
            return surface is not AdminSurface.STORAGE

        app = _app(
            db,
            StorageBrowserService(_store(tmp_path)),
            surface_policy=deny_storage,
        )
        async with _client(app) as c:
            assert 'href="/admin/storage"' not in (await c.get("/admin/")).text
            assert (await c.get("/admin/storage")).status_code == 403
            assert (
                await c.get("/admin/storage/raw", params={"key": "a"})
            ).status_code == 403

    async def test_signed_out_requests_go_to_login(
        self, db: AsyncDatabaseManager, tmp_path: Path
    ) -> None:
        app = _app(db, StorageBrowserService(_store(tmp_path)))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            response = await c.get("/admin/storage")
            assert response.status_code in {302, 303}
            assert "/admin/login" in response.headers["location"]
