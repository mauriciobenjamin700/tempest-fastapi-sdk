"""Tests for file_digest / object_digest (streamed + memoized)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

import tempest_fastapi_sdk.artifacts.digest as digest_mod
from tempest_fastapi_sdk import file_digest, object_digest


class _FakeMinio:
    """Minimal MinIO stub that streams fixed bytes and counts reads."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.calls = 0

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        """Return an async iterator over ``self.data`` in ``chunk_size`` slices."""
        self.calls += 1
        data = self.data

        async def _gen() -> AsyncIterator[bytes]:
            for start in range(0, len(data), chunk_size):
                yield data[start : start + chunk_size]

        return _gen()


class TestFileDigest:
    async def test_streams_correct_sha_and_size(self, tmp_path: Path) -> None:
        data = b"artifact-bytes-" * 5000
        path = tmp_path / "a.bin"
        path.write_bytes(data)
        digest_mod._file_cache.clear()

        sha256, size = await file_digest(path)

        assert sha256 == hashlib.sha256(data).hexdigest()
        assert size == len(data)

    async def test_memoized_second_call_does_not_reread(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data = b"hello world"
        path = tmp_path / "b.bin"
        path.write_bytes(data)
        digest_mod._file_cache.clear()

        calls = {"n": 0}
        real = digest_mod._read_file_digest

        def spy(target: Path) -> tuple[str, int]:
            calls["n"] += 1
            return real(target)

        monkeypatch.setattr(digest_mod, "_read_file_digest", spy)

        first = await file_digest(path)
        second = await file_digest(path)

        assert first == second == (hashlib.sha256(data).hexdigest(), len(data))
        assert calls["n"] == 1


class TestObjectDigest:
    async def test_streams_and_memoizes(self) -> None:
        data = b"model-weights-" * 5000
        fake = _FakeMinio(data)
        client: Any = fake
        digest_mod._object_cache.clear()

        sha256, size = await object_digest(client, "bucket", "key")
        assert sha256 == hashlib.sha256(data).hexdigest()
        assert size == len(data)

        again = await object_digest(client, "bucket", "key")
        assert again == (sha256, size)
        assert fake.calls == 1

    async def test_distinct_keys_are_not_shared(self) -> None:
        fake = _FakeMinio(b"aaa")
        client: Any = fake
        digest_mod._object_cache.clear()

        await object_digest(client, "bucket", "k1")
        await object_digest(client, "bucket", "k2")

        assert fake.calls == 2
