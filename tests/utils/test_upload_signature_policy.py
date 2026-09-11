"""Text uploads under ``verify_magic_bytes``, and hashing the whole file.

Two limits that a chat's media path runs into on day one:

* ``verify_magic_bytes=True`` rejected **everything without a
  signature**, so a ``.txt`` or a ``.csv`` could not be uploaded at all
  — text carries no magic bytes to find.
* ``content_validator`` sees only the first chunk. The docstring said so;
  what it did not say is that this makes it useless for a checksum, and
  a SHA-256 accumulated inside it digests the first chunk while looking
  like a digest of the file.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from tempest_fastapi_sdk import (
    SNIFFABLE_MIMETYPES,
    InvalidFileTypeException,
    UploadUtils,
)

_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 16
_TEXT = b"nome,email\nana,ana@example.com\n"


def _upload(
    content: bytes,
    *,
    filename: str,
    content_type: str,
) -> UploadFile:
    """Build an in-memory upload.

    Args:
        content (bytes): The file bytes.
        filename (str): The client-supplied name.
        content_type (str): The declared content type.

    Returns:
        UploadFile: The upload.
    """
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


class TestStrictIsStillStrict:
    """The default must not move — it is the safe one for images."""

    async def test_unsigned_file_is_rejected_by_default(
        self,
        tmp_path: Path,
    ) -> None:
        utils = UploadUtils(tmp_path, verify_magic_bytes=True)

        with pytest.raises(InvalidFileTypeException):
            await utils.save(_upload(_TEXT, filename="a.csv", content_type="text/csv"))

    def test_require_known_signature_defaults_to_true(self, tmp_path: Path) -> None:
        assert UploadUtils(tmp_path).require_known_signature is True


class TestContradictionOnly:
    """``require_known_signature=False`` — reject only a contradiction."""

    async def test_text_is_accepted(self, tmp_path: Path) -> None:
        utils = UploadUtils(
            tmp_path,
            verify_magic_bytes=True,
            require_known_signature=False,
        )

        key = await utils.save(
            _upload(_TEXT, filename="a.csv", content_type="text/csv"),
        )

        assert (tmp_path / key).read_bytes() == _TEXT

    async def test_claiming_an_image_without_its_signature_still_fails(
        self,
        tmp_path: Path,
    ) -> None:
        """The security case the flag must not weaken."""
        utils = UploadUtils(
            tmp_path,
            verify_magic_bytes=True,
            require_known_signature=False,
        )

        with pytest.raises(InvalidFileTypeException):
            await utils.save(
                _upload(
                    b"<html><script>alert(1)</script></html>",
                    filename="x.jpg",
                    content_type="image/jpeg",
                ),
            )

    async def test_text_declared_but_actually_a_pdf_still_fails(
        self,
        tmp_path: Path,
    ) -> None:
        utils = UploadUtils(
            tmp_path,
            verify_magic_bytes=True,
            require_known_signature=False,
        )

        with pytest.raises(InvalidFileTypeException):
            await utils.save(
                _upload(
                    b"%PDF-1.7\n" + b"\x00" * 16,
                    filename="a.txt",
                    content_type="text/plain",
                ),
            )

    async def test_a_real_image_still_passes(self, tmp_path: Path) -> None:
        utils = UploadUtils(
            tmp_path,
            verify_magic_bytes=True,
            require_known_signature=False,
        )

        key = await utils.save(
            _upload(_JPEG, filename="a.jpg", content_type="image/jpeg"),
        )

        assert (tmp_path / key).exists()

    def test_the_sniffable_set_is_what_decides(self) -> None:
        assert "image/jpeg" in SNIFFABLE_MIMETYPES
        assert "text/csv" not in SNIFFABLE_MIMETYPES


class TestHasher:
    """Digesting the file, which the first-chunk validator cannot do."""

    async def test_digest_covers_every_chunk(self, tmp_path: Path) -> None:
        payload = b"a" * (1024 * 1024) + b"b" * 4096
        utils = UploadUtils(tmp_path, chunk_size=64 * 1024)
        digest = hashlib.sha256()

        await utils.save(
            _upload(
                payload, filename="big.bin", content_type="application/octet-stream"
            ),
            hasher=digest,
        )

        assert digest.hexdigest() == hashlib.sha256(payload).hexdigest()

    async def test_content_validator_only_sees_the_first_chunk(
        self,
        tmp_path: Path,
    ) -> None:
        """Pinned as the measured limit that makes ``hasher`` necessary."""
        payload = b"a" * (1024 * 1024) + b"b" * 4096
        utils = UploadUtils(tmp_path, chunk_size=64 * 1024)
        seen: list[int] = []

        await utils.save(
            _upload(
                payload, filename="big.bin", content_type="application/octet-stream"
            ),
            content_validator=lambda chunk: (seen.append(len(chunk)), True)[1],
        )

        assert seen == [64 * 1024]
        assert sum(seen) < len(payload)
