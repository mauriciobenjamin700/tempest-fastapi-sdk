"""Tests for tempest_fastapi_sdk.utils.upload.UploadUtils."""

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from tempest_fastapi_sdk import (
    FileTooLargeException,
    InvalidFileTypeException,
    UploadUtils,
)


def _make_upload(
    content: bytes,
    *,
    filename: str = "test.png",
    content_type: str = "image/png",
) -> UploadFile:
    """Build a FastAPI UploadFile backed by an in-memory buffer."""
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


class TestSave:
    async def test_persists_to_disk(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        upload = _make_upload(b"hello world")
        path = await utils.save(upload)
        assert path.exists()
        assert path.read_bytes() == b"hello world"

    async def test_uuid_filename_by_default(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        upload = _make_upload(b"x", filename="original.png")
        path = await utils.save(upload)
        # The hashed filename keeps the original extension only.
        assert path.suffix == ".png"
        assert path.name != "original.png"

    async def test_keep_original_name(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        upload = _make_upload(b"x", filename="original.png")
        path = await utils.save(upload, keep_original_name=True)
        assert path.name == "original.png"

    async def test_subdir_creates_directory(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        upload = _make_upload(b"x")
        path = await utils.save(upload, subdir="avatars")
        assert path.parent.name == "avatars"


class TestValidation:
    async def test_rejects_disallowed_extension(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path, allowed_extensions={"jpg"})
        upload = _make_upload(
            b"x",
            filename="bad.exe",
            content_type="application/octet-stream",
        )
        with pytest.raises(InvalidFileTypeException):
            await utils.save(upload)

    async def test_accepts_allowed_extension_case_insensitive(
        self, tmp_path: Path
    ) -> None:
        utils = UploadUtils(tmp_path, allowed_extensions={"PNG"})
        upload = _make_upload(b"x", filename="img.png")
        path = await utils.save(upload)
        assert path.exists()

    async def test_rejects_disallowed_mimetype(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path, allowed_mimetypes={"image/png"})
        upload = _make_upload(
            b"x",
            filename="virus.exe",
            content_type="application/octet-stream",
        )
        with pytest.raises(InvalidFileTypeException):
            await utils.save(upload)

    async def test_rejects_oversize_upload(self, tmp_path: Path) -> None:
        utils = UploadUtils(
            tmp_path,
            max_size_bytes=10,
            chunk_size=4,
        )
        upload = _make_upload(b"x" * 100)
        with pytest.raises(FileTooLargeException):
            await utils.save(upload)
        # Partial file must be cleaned up on overflow.
        leftovers = list(tmp_path.iterdir())
        assert leftovers == []


class TestDelete:
    async def test_delete_removes_file(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        path = await utils.save(_make_upload(b"x"))
        assert utils.delete(path) is True
        assert not path.exists()

    def test_delete_missing_returns_false(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        assert utils.delete(tmp_path / "ghost") is False
