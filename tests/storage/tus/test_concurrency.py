"""Two ``PATCH`` at the same offset of one upload, with the interleaving forced.

The first request is held inside :meth:`TusStore.write`; only then is the
second sent. No ``sleep`` decides the order: each step waits on an event the
code under test sets.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

import httpx
import pytest

from tempest_fastapi_sdk.storage.tus import LocalTusStore, TusUpload
from tempest_fastapi_sdk.storage.tus import store as tus_store
from tests.storage.tus._support import CHUNK, TUS, build_app


class GatedStore(LocalTusStore):
    """A local store whose first ``write`` waits for a release signal."""

    def __init__(self, root: Path) -> None:
        """Initialize the gates.

        Args:
            root (Path): Store directory.
        """
        super().__init__(root)
        self.first_inside: asyncio.Event = asyncio.Event()
        self.release_first: asyncio.Event = asyncio.Event()
        self.second_inside: asyncio.Event = asyncio.Event()
        self.writes: int = 0
        self.concurrent: int = 0
        self.max_concurrent: int = 0

    async def write(self, upload: TusUpload, data: BinaryIO, size: int) -> None:
        """Count overlapping writes and hold the first one at the gate.

        Args:
            upload (TusUpload): The upload.
            data (BinaryIO): The chunk.
            size (int): Its size.
        """
        self.writes += 1
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self.writes == 1:
                self.first_inside.set()
                await self.release_first.wait()
            else:
                self.second_inside.set()
            await super().write(upload, data, size)
        finally:
            self.concurrent -= 1


async def _race(
    store: GatedStore,
    *,
    second_writes_first: bool = False,
) -> tuple[httpx.Response, httpx.Response, str]:
    """Run two same-offset ``PATCH`` with the second arriving mid-write.

    Args:
        store (GatedStore): The store.
        second_writes_first (bool): Wait until the second request is inside
            ``write`` before releasing the first — only reachable when
            nothing serializes them.

    Returns:
        tuple[httpx.Response, httpx.Response, str]: Both answers and the URL.
    """
    app = build_app(store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-User": "ana"},
    ) as http:
        created = await http.post(
            "/api/uploads",
            headers={**TUS, "Upload-Length": "12"},
        )
        url = created.headers["location"]
        headers = {**CHUNK, "Upload-Offset": "0"}
        first = asyncio.create_task(http.patch(url, content=b"A" * 6, headers=headers))
        await asyncio.wait_for(store.first_inside.wait(), timeout=5)
        second = asyncio.create_task(http.patch(url, content=b"B" * 6, headers=headers))
        if second_writes_first:
            await asyncio.wait_for(store.second_inside.wait(), timeout=5)
        else:
            await asyncio.wait_for(asyncio.shield(second), timeout=5)
        store.release_first.set()
        first_answer, second_answer = await asyncio.wait_for(
            asyncio.gather(first, second),
            timeout=5,
        )
    return first_answer, second_answer, url


async def test_second_same_offset_patch_gets_409_and_bytes_stay_whole(
    tmp_path: Path,
) -> None:
    """One writer wins, the other answers ``409``; the file holds only the winner."""
    store = GatedStore(tmp_path)
    first, second, url = await _race(store)
    assert first.status_code == 204
    assert second.status_code == 409
    assert second.json()["code"] == "TUS_UPLOAD_LOCKED"
    assert store.writes == 1
    assert store.max_concurrent == 1
    upload_id = url.rsplit("/", 1)[-1]
    assert store.data_path(upload_id).read_bytes() == b"A" * 6
    upload = await store.load(upload_id)
    assert upload is not None and upload.offset == 6


async def test_without_the_lock_the_same_race_corrupts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The harness detects the defect: with the lock removed both chunks land.

    Proves the test above can fail: the second write goes through on the
    offset it read before the first committed, both requests answer ``204``
    for the same byte range, and one chunk silently overwrites the other.
    """

    @contextlib.asynccontextmanager
    async def no_lock(self: object, key: str) -> AsyncIterator[bool]:
        """Pretend the lock was taken.

        Args:
            self (object): The lock table.
            key (str): Ignored.

        Yields:
            bool: Always ``True``.
        """
        yield True

    monkeypatch.setattr(tus_store._KeyedLocks, "try_hold", no_lock)
    store = GatedStore(tmp_path)
    first, second, _url = await _race(store, second_writes_first=True)
    assert first.status_code == 204
    assert second.status_code == 204
    assert store.writes == 2
    assert store.max_concurrent == 2
