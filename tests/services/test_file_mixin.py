"""Tests for tempest_fastapi_sdk.services.StoredFileServiceMixin."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    BaseResponseSchema,
    BaseService,
    StoredFileServiceMixin,
)
from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


class Doc(BaseModel):
    __tablename__ = "doc_for_file_mixin_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    attachment: Mapped[str | None] = mapped_column(String(256), default=None)


class DocResponse(BaseResponseSchema):
    name: str
    attachment: str | None = None


class DocRepository(BaseRepository[Doc]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=Doc)

    def map_to_response(self, instance: Doc) -> DocResponse:
        return DocResponse(
            id=instance.id,
            is_active=instance.is_active,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            name=instance.name,
            attachment=instance.attachment,
        )


class FakeUpload:
    """Records replace/delete calls; returns the new key as a ``Path``."""

    def __init__(self, *, new_name: str = "stored.bin") -> None:
        self.new_name = new_name
        self.replaced: list[tuple[Any, str]] = []
        self.deleted: list[Any] = []

    async def replace(
        self,
        old_key: Path | str | None,
        file: Any,
        *,
        subdir: str = "",
        filename: str | None = None,
        keep_original_name: bool = False,
    ) -> Path:
        name = filename or self.new_name
        self.replaced.append((old_key, subdir))
        return Path(subdir) / name if subdir else Path(name)

    async def delete(self, key: Path | str) -> bool:
        self.deleted.append(key)
        return True


class FakePresign:
    """Records presign calls; returns a deterministic URL."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, timedelta]] = []

    async def presigned_get_url(
        self, key: str, *, expires: timedelta = timedelta(hours=1)
    ) -> str:
        self.calls.append((key, expires))
        return f"https://signed.example/{key}"

    async def presigned_get_urls(
        self,
        keys: Any,
        *,
        expires: timedelta = timedelta(hours=1),
        max_concurrency: int = 16,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in dict.fromkeys(keys):
            self.calls.append((key, expires))
            result[key] = f"https://signed.example/{key}"
        return result


class DocService(
    BaseService[DocRepository, DocResponse],
    StoredFileServiceMixin[Doc],
):
    def __init__(
        self,
        repository: DocRepository,
        upload_utils: FakeUpload,
        storage: FakePresign,
    ) -> None:
        super().__init__(repository)
        self.upload_utils = upload_utils
        self.storage = storage


@pytest.fixture
def upload() -> FakeUpload:
    return FakeUpload()


@pytest.fixture
def presign() -> FakePresign:
    return FakePresign()


@pytest.fixture
def service(
    session: AsyncSession, upload: FakeUpload, presign: FakePresign
) -> DocService:
    return DocService(DocRepository(session), upload, presign)


class TestSetFile:
    async def test_uploads_and_persists_key(
        self, service: DocService, session: AsyncSession
    ) -> None:
        doc = await service.repository.add(Doc(name="report"))

        updated = await service.set_file(
            doc, object(), field="attachment", subdir="docs", filename="a.pdf"
        )

        assert updated.attachment == str(Path("docs") / "a.pdf")
        reloaded = await session.get(Doc, doc.id)
        assert reloaded is not None
        assert reloaded.attachment == str(Path("docs") / "a.pdf")

    async def test_replace_receives_previous_key(
        self, service: DocService, upload: FakeUpload
    ) -> None:
        doc = await service.repository.add(Doc(name="report", attachment="old/key.bin"))

        await service.set_file(doc, object(), field="attachment")

        assert upload.replaced[-1][0] == "old/key.bin"

    async def test_reattaches_detached_entity(
        self,
        db: AsyncDatabaseManager,
        upload: FakeUpload,
        presign: FakePresign,
    ) -> None:
        """A detached entity is re-attached before the write (no error)."""
        async with db.get_session_context() as first:
            doc = Doc(name="report")
            first.add(doc)
            await first.commit()
        # ``first`` is closed → ``doc`` is detached.

        async with db.get_session_context() as second:
            service = DocService(DocRepository(second), upload, presign)
            updated = await service.set_file(
                doc, object(), field="attachment", filename="x.bin"
            )
            assert updated.attachment == "x.bin"

        async with db.get_session_context() as verify:
            reloaded = await verify.get(Doc, doc.id)
            assert reloaded is not None
            assert reloaded.attachment == "x.bin"


class TestClearFile:
    async def test_deletes_and_nulls(
        self, service: DocService, upload: FakeUpload, session: AsyncSession
    ) -> None:
        doc = await service.repository.add(Doc(name="report", attachment="docs/a.pdf"))

        updated = await service.clear_file(doc, field="attachment")

        assert updated.attachment is None
        assert upload.deleted == ["docs/a.pdf"]
        reloaded = await session.get(Doc, doc.id)
        assert reloaded is not None
        assert reloaded.attachment is None

    async def test_noop_when_already_empty(
        self, service: DocService, upload: FakeUpload
    ) -> None:
        doc = await service.repository.add(Doc(name="report"))

        updated = await service.clear_file(doc, field="attachment")

        assert updated.attachment is None
        assert upload.deleted == []  # nothing to delete → no storage call


class TestFileUrl:
    async def test_returns_presigned_url(
        self, service: DocService, presign: FakePresign
    ) -> None:
        url = await service.file_url("docs/a.pdf")
        assert url == "https://signed.example/docs/a.pdf"
        assert presign.calls[-1][0] == "docs/a.pdf"

    async def test_none_key_returns_none(
        self, service: DocService, presign: FakePresign
    ) -> None:
        assert await service.file_url(None) is None
        assert presign.calls == []  # no presign call for an empty key

    async def test_custom_expiry_forwarded(
        self, service: DocService, presign: FakePresign
    ) -> None:
        await service.file_url("k", expires=timedelta(minutes=5))
        assert presign.calls[-1][1] == timedelta(minutes=5)


class TestFileUrls:
    async def test_maps_each_key(
        self, service: DocService, presign: FakePresign
    ) -> None:
        urls = await service.file_urls(["docs/a.pdf", "docs/b.pdf"])
        assert urls == {
            "docs/a.pdf": "https://signed.example/docs/a.pdf",
            "docs/b.pdf": "https://signed.example/docs/b.pdf",
        }

    async def test_skips_empty_keys(
        self, service: DocService, presign: FakePresign
    ) -> None:
        urls = await service.file_urls(["a", None, "", "b"])
        assert set(urls) == {"a", "b"}
        assert urls.get(None) is None

    async def test_all_empty_returns_empty_without_presign(
        self, service: DocService, presign: FakePresign
    ) -> None:
        assert await service.file_urls([None, ""]) == {}
        assert presign.calls == []

    async def test_custom_expiry_forwarded(
        self, service: DocService, presign: FakePresign
    ) -> None:
        await service.file_urls(["k"], expires=timedelta(minutes=5))
        assert presign.calls[-1][1] == timedelta(minutes=5)
