"""Tests for tempest_fastapi_sdk.utils.download.DownloadUtils."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.responses import FileResponse, StreamingResponse

from tempest_fastapi_sdk import (
    DownloadUtils,
    NotFoundException,
    build_content_disposition,
)


def _write(base: Path, relative: str, data: bytes = b"data") -> Path:
    """Create a file under ``base`` and return its path."""
    target = base / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


class TestResolve:
    def test_resolves_existing_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "report.pdf")
        utils = DownloadUtils(tmp_path)
        resolved = utils.resolve("report.pdf")
        assert resolved == (tmp_path / "report.pdf").resolve()

    def test_resolves_within_subdir(self, tmp_path: Path) -> None:
        _write(tmp_path, "invoices/2026.pdf")
        utils = DownloadUtils(tmp_path)
        resolved = utils.resolve("2026.pdf", subdir="invoices")
        assert resolved == (tmp_path / "invoices" / "2026.pdf").resolve()

    def test_missing_file_raises_not_found(self, tmp_path: Path) -> None:
        utils = DownloadUtils(tmp_path)
        with pytest.raises(NotFoundException):
            utils.resolve("ghost.pdf")

    def test_directory_is_not_a_file(self, tmp_path: Path) -> None:
        (tmp_path / "folder").mkdir()
        utils = DownloadUtils(tmp_path)
        with pytest.raises(NotFoundException):
            utils.resolve("folder")

    def test_traversal_escape_raises_not_found(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_bytes(b"top secret")
        base = tmp_path / "public"
        base.mkdir()
        utils = DownloadUtils(base)
        with pytest.raises(NotFoundException):
            utils.resolve("../secret.txt")

    def test_absolute_path_escape_raises_not_found(self, tmp_path: Path) -> None:
        secret = _write(tmp_path, "secret.txt")
        base = tmp_path / "public"
        base.mkdir()
        utils = DownloadUtils(base)
        with pytest.raises(NotFoundException):
            utils.resolve(str(secret))


class TestFileResponse:
    def test_returns_file_response(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.pdf")
        utils = DownloadUtils(tmp_path)
        response = utils.file_response("a.pdf")
        assert isinstance(response, FileResponse)
        assert response.media_type == "application/pdf"

    def test_attachment_disposition_by_default(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.pdf")
        utils = DownloadUtils(tmp_path)
        response = utils.file_response("a.pdf")
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment")
        assert 'filename="a.pdf"' in disposition

    def test_inline_disposition(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.pdf")
        utils = DownloadUtils(tmp_path)
        response = utils.file_response("a.pdf", as_attachment=False)
        assert response.headers["content-disposition"].startswith("inline")

    def test_custom_filename_and_media_type(self, tmp_path: Path) -> None:
        _write(tmp_path, "internal-id.bin")
        utils = DownloadUtils(tmp_path)
        response = utils.file_response(
            "internal-id.bin",
            filename="fatura.pdf",
            media_type="application/pdf",
        )
        assert response.media_type == "application/pdf"
        assert 'filename="fatura.pdf"' in response.headers["content-disposition"]

    def test_unknown_extension_falls_back_to_octet_stream(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "blob.unknownext")
        utils = DownloadUtils(tmp_path)
        response = utils.file_response("blob.unknownext")
        assert response.media_type == "application/octet-stream"

    def test_missing_file_raises_not_found(self, tmp_path: Path) -> None:
        utils = DownloadUtils(tmp_path)
        with pytest.raises(NotFoundException):
            utils.file_response("ghost.pdf")


class TestStream:
    def test_streams_raw_bytes(self, tmp_path: Path) -> None:
        utils = DownloadUtils(tmp_path)
        response = utils.stream(b"hello", filename="greeting.txt")
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/plain"

    def test_attachment_disposition(self, tmp_path: Path) -> None:
        utils = DownloadUtils(tmp_path)
        response = utils.stream(b"x", filename="data.csv")
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment")
        assert 'filename="data.csv"' in disposition

    async def test_streams_async_iterable(self, tmp_path: Path) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"a"
            yield b"b"

        utils = DownloadUtils(tmp_path)
        response = utils.stream(
            chunks(),
            filename="report.bin",
            media_type="application/octet-stream",
        )
        assert isinstance(response, StreamingResponse)


class TestBuildContentDisposition:
    def test_ascii_filename(self) -> None:
        value = build_content_disposition("file.pdf")
        assert value == "attachment; filename=\"file.pdf\"; filename*=UTF-8''file.pdf"

    def test_inline(self) -> None:
        value = build_content_disposition("file.pdf", as_attachment=False)
        assert value.startswith("inline")

    def test_non_ascii_filename_is_percent_encoded(self) -> None:
        value = build_content_disposition("relatório.pdf")
        assert "filename*=UTF-8''relat%C3%B3rio.pdf" in value
        # ASCII fallback drops the non-ascii char rather than emitting it raw.
        assert 'filename="relatrio.pdf"' in value

    def test_path_is_reduced_to_basename(self) -> None:
        value = build_content_disposition("../../etc/passwd")
        assert 'filename="passwd"' in value
