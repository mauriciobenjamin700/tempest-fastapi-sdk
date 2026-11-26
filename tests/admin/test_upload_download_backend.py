"""The admin download route against a backend that refuses presigned URLs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.exceptions import PresignedUrlUnsupportedException
from tempest_fastapi_sdk.testing import admin_login
from tempest_fastapi_sdk.utils import SupportsWebViewLink, UploadResult, UploadStorage
from tempest_fastapi_sdk.utils.storage_backends import ContentValidator


class DriveDownloadUser(BaseUserModel):
    __tablename__ = "admin_drive_download_users"


class DriveDocModel(BaseModel):
    __tablename__ = "admin_drive_download_doc"
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    attachment: Mapped[str | None] = mapped_column(String(255), nullable=True)


SECRET: str = "y" * 48
SLUG: str = DriveDocModel.__tablename__
KEY: str = "docs/report.pdf"
LINK: str = "https://drive.google.com/file/d/abc/view"


class RefusingStorage:
    """A backend whose ``presigned_url`` raises, like the Google Drive one."""

    def __init__(self) -> None:
        self.keys: set[str] = {KEY}

    async def write_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        max_size_bytes: int | None = None,
        validator: ContentValidator | None = None,
    ) -> UploadResult:
        size = 0
        async for chunk in chunks:
            size += len(chunk)
        self.keys.add(key)
        return UploadResult(key=key, size=size)

    async def delete(self, key: str) -> bool:
        removed = key in self.keys
        self.keys.discard(key)
        return removed

    async def exists(self, key: str) -> bool:
        return key in self.keys

    async def presigned_url(
        self, key: str, *, expires: timedelta = timedelta(hours=1)
    ) -> str | None:
        raise PresignedUrlUnsupportedException("fake_drive")


class LinkingStorage(RefusingStorage):
    """The same backend, exposing a permanent link per key."""

    async def web_view_link(self, key: str) -> str | None:
        return LINK if key in self.keys else None


async def _app(storage: UploadStorage) -> tuple[FastAPI, AsyncDatabaseManager, str]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = DriveDownloadUser(
            email="root@example.com", hashed_password="", is_admin=True
        )
        user.set_password("hunter2")
        doc = DriveDocModel(title="Report", attachment=KEY)
        session.add_all([user, doc])
        await session.commit()
        identity = str(doc.id)
    site = AdminSite(title="Test Admin")
    site.register(
        AdminModel(
            model=DriveDocModel,
            list_display=[DriveDocModel.id, DriveDocModel.title],
            upload_fields=[DriveDocModel.attachment],
            upload_storage=storage,
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(DriveDownloadUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    return app, db, identity


async def _download(storage: UploadStorage) -> tuple[int, str | None]:
    app, db, identity = await _app(storage)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await admin_login(client, "root@example.com", "hunter2")
            response = await client.get(
                f"/admin/m/{SLUG}/{identity}/upload/attachment",
                follow_redirects=False,
            )
        return response.status_code, response.headers.get("location")
    finally:
        await db.drop_tables()
        await db.disconnect()


class TestPresignedUrlUnsupported:
    def test_fakes_match_the_protocols(self) -> None:
        assert isinstance(LinkingStorage(), UploadStorage)
        assert isinstance(LinkingStorage(), SupportsWebViewLink)
        assert not isinstance(RefusingStorage(), SupportsWebViewLink)

    @pytest.mark.asyncio
    async def test_redirects_to_the_permanent_link(self) -> None:
        status, location = await _download(LinkingStorage())
        assert status == 303
        assert location == LINK

    @pytest.mark.asyncio
    async def test_without_a_link_is_file_not_available(self) -> None:
        status, location = await _download(RefusingStorage())
        assert status == 404
        assert location is None

    @pytest.mark.asyncio
    async def test_link_for_a_missing_file_is_file_not_available(self) -> None:
        storage = LinkingStorage()
        storage.keys.clear()
        status, _ = await _download(storage)
        assert status == 404
