"""One round trip against the real Google Drive, when a service account exists.

Opt-in twice: the ``network`` marker (``make test-network``) and two
variables — ``TEST_GOOGLE_DRIVE_CREDENTIALS`` (path to, or inline content of,
a service-account JSON key) and ``TEST_GOOGLE_DRIVE_FOLDER_ID`` (a folder, or
a folder inside a Shared Drive, shared with that account). Without both the
test is skipped. ``TEST_GOOGLE_DRIVE_DRIVE_ID`` is optional.

It uploads a file in more than one resumable piece, overwrites it, checks
``exists`` and deletes it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest

from tempest_fastapi_sdk.integrations.storage.google_drive import (
    UPLOAD_CHUNK_GRANULARITY,
    GoogleDriveUploadStorage,
)

pytestmark = pytest.mark.network

CREDENTIALS: str = os.environ.get("TEST_GOOGLE_DRIVE_CREDENTIALS", "")
FOLDER_ID: str = os.environ.get("TEST_GOOGLE_DRIVE_FOLDER_ID", "")
DRIVE_ID: str = os.environ.get("TEST_GOOGLE_DRIVE_DRIVE_ID", "")


async def _stream(payload: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(payload), size):
        yield payload[start : start + size]


@pytest.mark.skipif(
    not (CREDENTIALS and FOLDER_ID),
    reason="set TEST_GOOGLE_DRIVE_CREDENTIALS and TEST_GOOGLE_DRIVE_FOLDER_ID",
)
async def test_upload_overwrite_exists_delete_round_trip() -> None:
    inline = CREDENTIALS.lstrip().startswith("{")
    storage = GoogleDriveUploadStorage.from_service_account(
        folder_id=FOLDER_ID,
        credentials_json=CREDENTIALS if inline else None,
        credentials_path=None if inline else CREDENTIALS,
        drive_id=DRIVE_ID or None,
        chunk_size=UPLOAD_CHUNK_GRANULARITY,
    )
    key = f"tempest-live/{uuid.uuid4().hex}/it's.bin"
    payload = os.urandom(UPLOAD_CHUNK_GRANULARITY * 2 + 1234)
    try:
        result = await storage.write_stream(key, _stream(payload, 100_000))
        assert result.size == len(payload)
        assert result.url
        assert await storage.exists(key) is True

        again = await storage.write_stream(key, _stream(b"second", 6))
        assert again.size == 6
        assert again.url == result.url
        assert len(await storage.find(key)) == 1
    finally:
        removed = await storage.delete(key)
        await storage.aclose()
    assert removed is True
    storage = GoogleDriveUploadStorage.from_service_account(
        folder_id=FOLDER_ID,
        credentials_json=CREDENTIALS if inline else None,
        credentials_path=None if inline else CREDENTIALS,
        drive_id=DRIVE_ID or None,
    )
    try:
        assert await storage.exists(key) is False
    finally:
        await storage.aclose()
