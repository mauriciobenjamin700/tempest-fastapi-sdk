"""Edges of the resumable protocol: piece boundaries, stalls, rate limits, 404."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from tempest_fastapi_sdk.exceptions import (
    GoogleDriveException,
    GoogleDriveFolderNotFoundException,
    InvalidFileTypeException,
)
from tempest_fastapi_sdk.integrations.storage.google_drive import (
    UPLOAD_SESSION_NOT_FOUND_REASON,
    GoogleDriveUploadStorage,
)
from tests.integrations.storage.google_drive.conftest import (
    Failure,
    FakeDrive,
    Sleeps,
    stream,
)

KIB: int = 1024
PIECE: int = 256 * KIB

MakeStorage = Callable[..., GoogleDriveUploadStorage]


def _ranges(fake: FakeDrive) -> list[str]:
    return [r.headers["Content-Range"] for r in fake.piece_requests()]


class TestExactMultipleOfThePiece:
    async def test_one_whole_piece_closes_with_the_total(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        payload = b"a" * PIECE

        result = await storage.write_stream("one.bin", stream(payload))

        assert _ranges(fake) == [f"bytes 0-{PIECE - 1}/{PIECE}"]
        assert fake.contents["file-1"] == payload
        assert result.size == PIECE

    async def test_two_whole_pieces_close_with_the_total(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        payload = b"a" * PIECE + b"b" * PIECE

        await storage.write_stream("two.bin", stream(payload))

        assert _ranges(fake) == [
            f"bytes 0-{PIECE - 1}/*",
            f"bytes {PIECE}-{2 * PIECE - 1}/{2 * PIECE}",
        ]
        assert fake.contents["file-1"] == payload
        assert all(r.content for r in fake.piece_requests())

    async def test_whole_pieces_in_small_parts(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        payload = bytes(range(256)) * (2 * PIECE // 256)
        parts = [payload[i : i + 64 * KIB] for i in range(0, len(payload), 64 * KIB)]

        await storage.write_stream("parts.bin", stream(*parts))

        assert _ranges(fake)[-1] == f"bytes {PIECE}-{2 * PIECE - 1}/{2 * PIECE}"
        assert fake.contents["file-1"] == payload

    async def test_zero_bytes_still_closes_with_an_empty_put(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        result = await storage.write_stream("empty.bin", stream())
        assert _ranges(fake) == ["bytes */0"]
        assert result.size == 0


class TestStall:
    async def test_no_progress_backs_off_before_resending(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage()
        fake.keep_only = [0, 0]

        await storage.write_stream("k", stream(b"data"))

        assert _ranges(fake) == ["bytes 0-3/4"] * 3
        assert sleeps.delays == [0.5, 1.0]
        assert fake.contents["file-1"] == b"data"

    async def test_no_progress_past_the_retries_is_upload_stalled(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage(max_retries=2)
        fake.keep_only = [0, 0, 0, 0]

        with pytest.raises(GoogleDriveException) as raised:
            await storage.write_stream("k", stream(b"data"))

        assert raised.value.reason == "upload_stalled"
        assert sleeps.delays == [0.5, 1.0]

    async def test_server_holding_less_than_what_was_dropped_fails_at_once(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        fake.rewind = [None, 100_000]
        payload = b"x" * (3 * PIECE)

        with pytest.raises(GoogleDriveException) as raised:
            await storage.write_stream("k", stream(payload))

        assert raised.value.reason == "upload_range_regressed"
        assert "100000" in raised.value.message
        assert str(PIECE) in raised.value.message
        assert len(fake.piece_requests()) == 2
        assert sleeps.delays == []


class TestRateLimit:
    @pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded"])
    async def test_403_rate_limit_on_a_piece_is_retried(
        self,
        fake: FakeDrive,
        make_storage: MakeStorage,
        sleeps: Sleeps,
        reason: str,
    ) -> None:
        storage = make_storage()
        fake.piece_failures = [Failure(403, reason)]

        await storage.write_stream("k", stream(b"data"))

        assert sleeps.delays == [0.5]
        assert fake.contents["file-1"] == b"data"

    async def test_403_rate_limit_on_a_metadata_call_is_retried(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage()
        fake.api_failures = [Failure(403, "userRateLimitExceeded")]

        assert await storage.exists("k") is False
        assert sleeps.delays == [0.5]

    async def test_other_403_is_still_raised_at_once(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage()
        fake.api_failures = [Failure(403, "insufficientFilePermissions")]
        with pytest.raises(GoogleDriveException) as raised:
            await storage.exists("k")
        assert raised.value.reason == "insufficientFilePermissions"
        assert sleeps.delays == []

    @pytest.mark.parametrize("status", [429, 503])
    async def test_retry_after_seconds_is_honoured(
        self,
        fake: FakeDrive,
        make_storage: MakeStorage,
        sleeps: Sleeps,
        status: int,
    ) -> None:
        storage = make_storage()
        fake.api_failures = [Failure(status, retry_after="7")]
        fake.piece_failures = [Failure(status, retry_after="3")]

        await storage.write_stream("k", stream(b"data"))

        assert sleeps.delays == [7.0, 3.0]

    async def test_retry_after_is_capped_at_the_backoff_ceiling(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage()
        fake.api_failures = [Failure(429, retry_after="3600")]
        await storage.exists("k")
        assert sleeps.delays == [16.0]

    async def test_unparsable_retry_after_falls_back_to_backoff(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage()
        fake.api_failures = [Failure(503, retry_after="Wed, 21 Oct 2026 07:28:00 GMT")]
        await storage.exists("k")
        assert sleeps.delays == [0.5]


class TestSessionLost:
    async def test_404_on_a_piece_is_a_lost_session_not_a_missing_folder(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        fake.expire_sessions = True

        with pytest.raises(GoogleDriveException) as raised:
            await storage.write_stream("k", stream(b"data"))

        assert not isinstance(raised.value, GoogleDriveFolderNotFoundException)
        assert raised.value.upstream_status == 404
        assert raised.value.reason == UPLOAD_SESSION_NOT_FOUND_REASON
        assert "resumable" in raised.value.message
        assert "expired" in raised.value.message

    async def test_404_on_the_status_query_is_a_lost_session(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        fake.piece_failures = [503]
        fake.expire_after_piece_failure = True
        with pytest.raises(GoogleDriveException) as raised:
            await storage.write_stream("k", stream(b"data"))

        assert raised.value.reason == UPLOAD_SESSION_NOT_FOUND_REASON
        assert _ranges(fake) == ["bytes 0-3/4", "bytes */4"]


class TestCallerStreamIsClosed:
    async def test_failed_upload_closes_the_callers_generator(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        closed: list[bool] = []

        async def source() -> AsyncIterator[bytes]:
            try:
                for _ in range(8):
                    yield b"x" * PIECE
            finally:
                closed.append(True)

        fake.piece_failures = [Failure(403, "insufficientFilePermissions")]
        with pytest.raises(GoogleDriveException):
            await storage.write_stream("k", source())

        assert closed == [True]

    async def test_validator_rejection_closes_the_callers_generator(
        self, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        closed: list[bool] = []

        async def source() -> AsyncIterator[bytes]:
            try:
                yield b"MZ"
                yield b"more"
            finally:
                closed.append(True)

        with pytest.raises(InvalidFileTypeException):
            await storage.write_stream(
                "k", source(), validator=lambda b: b.startswith(b"PNG")
            )

        assert closed == [True]
