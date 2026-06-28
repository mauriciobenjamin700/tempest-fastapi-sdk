"""Tests for admin file/image upload fields."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

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
from tempest_fastapi_sdk.utils import LocalUploadStorage


class UploadUser(BaseUserModel):
    __tablename__ = "admin_upload_users"


class DocModel(BaseModel):
    __tablename__ = "admin_upload_doc"
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    attachment: Mapped[str | None] = mapped_column(String(255), nullable=True)


SECRET = "x" * 48
_SLUG = DocModel.__tablename__


def test_upload_fields_without_storage_raises() -> None:
    with pytest.raises(ValueError, match="requires an `upload_storage`"):
        AdminModel(model=DocModel, upload_fields=[DocModel.attachment])


@pytest.fixture
async def app_with_upload(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, Path]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UploadUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

    site = AdminSite(title="Test Admin")
    site.register(
        AdminModel(
            model=DocModel,
            list_display=[DocModel.id, DocModel.title],
            upload_fields=[DocModel.attachment],
            upload_storage=LocalUploadStorage(tmp_path),
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(UploadUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, tmp_path
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login_csrf(client: AsyncClient) -> str:
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )
    form = await client.get(f"/admin/m/{_SLUG}/new")
    match = re.search(r'name="csrf_token" value="([^"]+)"', form.text)
    assert match is not None
    return match.group(1)


class TestUploadField:
    @pytest.mark.asyncio
    async def test_form_renders_file_input(
        self, app_with_upload: tuple[FastAPI, Path]
    ) -> None:
        app, _ = app_with_upload
        async with _client(app) as client:
            await client.post(
                "/admin/login",
                data={"identifier": "root@example.com", "password": "hunter2"},
            )
            form = await client.get(f"/admin/m/{_SLUG}/new")
        assert 'enctype="multipart/form-data"' in form.text
        assert 'type="file" name="attachment"' in form.text

    @pytest.mark.asyncio
    async def test_create_saves_file_and_key(
        self, app_with_upload: tuple[FastAPI, Path]
    ) -> None:
        app, base = app_with_upload
        async with _client(app) as client:
            token = await _login_csrf(client)
            resp = await client.post(
                f"/admin/m/{_SLUG}/new",
                data={"csrf_token": token, "title": "Report"},
                files={
                    "attachment": ("report.pdf", b"%PDF-1.4 data", "application/pdf")
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            detail = await client.get(resp.headers["location"])
        # The stored key was written to the column and the file exists.
        assert f"{_SLUG}/attachment/" in detail.text
        written = list(base.rglob("*.pdf"))
        assert len(written) == 1
        assert written[0].read_bytes() == b"%PDF-1.4 data"

    @pytest.mark.asyncio
    async def test_edit_without_file_keeps_existing(
        self, app_with_upload: tuple[FastAPI, Path]
    ) -> None:
        app, base = app_with_upload
        async with _client(app) as client:
            token = await _login_csrf(client)
            create = await client.post(
                f"/admin/m/{_SLUG}/new",
                data={"csrf_token": token, "title": "Report"},
                files={"attachment": ("a.pdf", b"one", "application/pdf")},
                follow_redirects=False,
            )
            detail_url = create.headers["location"]
            identity = detail_url.rsplit("/", 1)[-1]
            edit = await client.get(f"/admin/m/{_SLUG}/{identity}/edit")
            etoken = re.search(r'name="csrf_token" value="([^"]+)"', edit.text).group(1)  # type: ignore[union-attr]
            assert "Current:" in edit.text
            # Edit the title, send no file → attachment stays.
            await client.post(
                f"/admin/m/{_SLUG}/{identity}/edit",
                data={"csrf_token": etoken, "title": "Renamed"},
                follow_redirects=False,
            )
            detail = await client.get(detail_url)
        assert "Renamed" in detail.text
        assert f"{_SLUG}/attachment/" in detail.text
        assert len(list(base.rglob("*.pdf"))) == 1
