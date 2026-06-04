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
    sniff_mime,
)

# Minimal valid magic-byte prefixes for the formats sniff_mime knows.
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 16
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_GIF = b"GIF89a" + b"\x00" * 16
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 8
_PDF = b"%PDF-1.7\n" + b"\x00" * 16


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

    def test_delete_rejects_path_outside_upload_dir(self, tmp_path: Path) -> None:
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        utils = UploadUtils(upload_dir)
        with pytest.raises(InvalidFileTypeException):
            utils.delete(outside)
        assert outside.exists()

    def test_delete_rejects_relative_traversal(self, tmp_path: Path) -> None:
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        utils = UploadUtils(upload_dir)
        with pytest.raises(InvalidFileTypeException):
            utils.delete("../outside.txt")
        assert outside.exists()


class TestSniffMime:
    def test_recognizes_jpeg(self) -> None:
        assert sniff_mime(_JPEG) == "image/jpeg"

    def test_recognizes_png(self) -> None:
        assert sniff_mime(_PNG) == "image/png"

    def test_recognizes_gif(self) -> None:
        assert sniff_mime(_GIF) == "image/gif"

    def test_recognizes_webp(self) -> None:
        assert sniff_mime(_WEBP) == "image/webp"

    def test_recognizes_pdf(self) -> None:
        assert sniff_mime(_PDF) == "application/pdf"

    @pytest.mark.parametrize(
        "payload",
        [
            b"<!DOCTYPE html><script>alert(1)</script>",
            b"GIF",  # too short / not a full signature
            b"plain text content here",
            b"",
        ],
    )
    def test_returns_none_for_unknown_bytes(self, payload: bytes) -> None:
        assert sniff_mime(payload) is None


class TestFilenameOverride:
    async def test_explicit_filename_is_used(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        path = await utils.save(
            _make_upload(_PNG, filename="whatever.png"),
            filename="42.jpg",
        )
        assert path.name == "42.jpg"

    async def test_filename_reduced_to_basename(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        path = await utils.save(
            _make_upload(_PNG),
            filename="../../etc/passwd",
        )
        assert path.name == "passwd"
        assert path.parent == tmp_path.resolve()

    @pytest.mark.parametrize("bad", [".", "..", "/"])
    async def test_invalid_filename_rejected(self, tmp_path: Path, bad: str) -> None:
        utils = UploadUtils(tmp_path)
        with pytest.raises(InvalidFileTypeException):
            await utils.save(_make_upload(_PNG), filename=bad)


class TestContentValidator:
    async def test_passing_validator_saves(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path)
        path = await utils.save(
            _make_upload(_PNG),
            content_validator=lambda b: sniff_mime(b) == "image/png",
        )
        assert path.exists()

    async def test_failing_validator_rejects_and_cleans_up(
        self, tmp_path: Path
    ) -> None:
        utils = UploadUtils(tmp_path)
        with pytest.raises(InvalidFileTypeException):
            await utils.save(
                _make_upload(b"<script>", filename="x.png"),
                content_validator=lambda b: sniff_mime(b) == "image/png",
            )
        assert list(tmp_path.iterdir()) == []


class TestVerifyMagicBytes:
    async def test_matching_content_passes(self, tmp_path: Path) -> None:
        utils = UploadUtils(
            tmp_path,
            allowed_mimetypes={"image/png", "image/jpeg"},
            verify_magic_bytes=True,
        )
        upload = _make_upload(_PNG, filename="a.png", content_type="image/png")
        path = await utils.save(upload)
        assert path.exists()

    async def test_polyglot_declared_as_image_rejected(self, tmp_path: Path) -> None:
        """HTML+JS payload sent as image/png must be rejected."""
        utils = UploadUtils(
            tmp_path,
            allowed_mimetypes={"image/png"},
            verify_magic_bytes=True,
        )
        upload = _make_upload(
            b"<!DOCTYPE html><script>alert(1)</script>" + b"\x00" * 16,
            filename="evil.png",
            content_type="image/png",
        )
        with pytest.raises(InvalidFileTypeException):
            await utils.save(upload)
        assert list(tmp_path.iterdir()) == []

    async def test_real_image_but_not_in_allowlist_rejected(
        self, tmp_path: Path
    ) -> None:
        """A genuine PNG is rejected when only JPEG is allowed."""
        utils = UploadUtils(
            tmp_path,
            allowed_mimetypes={"image/jpeg"},
            verify_magic_bytes=True,
        )
        # Extension/MIME checks happen first; declare jpeg so we reach
        # the magic-byte stage, where the PNG signature is caught.
        upload = _make_upload(_PNG, filename="a.jpg", content_type="image/jpeg")
        with pytest.raises(InvalidFileTypeException):
            await utils.save(upload)

    async def test_jpg_jpeg_alias_accepted(self, tmp_path: Path) -> None:
        """Declared image/jpg matches the sniffed image/jpeg."""
        utils = UploadUtils(tmp_path, verify_magic_bytes=True)
        upload = _make_upload(_JPEG, filename="a.jpg", content_type="image/jpg")
        path = await utils.save(upload)
        assert path.exists()

    async def test_mismatch_without_allowlist_rejected(self, tmp_path: Path) -> None:
        """Without an allow-list, sniffed type must match declared."""
        utils = UploadUtils(tmp_path, verify_magic_bytes=True)
        upload = _make_upload(_PNG, filename="a.jpg", content_type="image/jpeg")
        with pytest.raises(InvalidFileTypeException):
            await utils.save(upload)

    async def test_unrecognized_signature_rejected(self, tmp_path: Path) -> None:
        utils = UploadUtils(tmp_path, verify_magic_bytes=True)
        upload = _make_upload(
            b"just plain text" + b"\x00" * 16,
            filename="a.png",
            content_type="image/png",
        )
        with pytest.raises(InvalidFileTypeException):
            await utils.save(upload)
