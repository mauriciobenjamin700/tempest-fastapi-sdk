"""``object_download_response`` behaves the same over every SDK object store.

The response is built once, in ``storage/_download.py``, and fed by whatever
store the caller hands it. Until #757 the in-memory
:class:`~tempest_fastapi_sdk.testing.fakes.FakeObjectStorageClient` refused
the ``chunk_size`` keyword the response always passes, so any test serving a
file through it died with ``TypeError`` while the two real clients worked.

Every case here runs against the three stores — MinIO (a ``minio.Minio``
stand-in, no server), local disk and the fake — so a store that stops
honouring the call fails beside the ones that still do.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk.storage import (
    AsyncMinIOClient,
    DownloadableStore,
    LocalObjectStorageClient,
    object_download_response,
)
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient

BUCKET: str = "media"
"""Bucket every store serves from."""

KEY: str = "docs/report.txt"
"""Key of the object every case downloads."""

PAYLOAD: bytes = bytes(range(97))
"""A payload whose size no chunk size used below divides evenly."""

CHUNK_SIZE: int = 10
"""Chunk size passed to the response, small enough to force pagination."""


class _MinioObject:
    """The ``minio.datatypes.Object`` fields ``AsyncMinIOClient.stat_object`` reads."""

    def __init__(self, payload: bytes) -> None:
        """Describe ``payload``.

        Args:
            payload (bytes): The stored bytes.
        """
        self.size: int = len(payload)
        self.etag: str = '"contract-etag"'
        self.content_type: str = "text/plain"
        self.last_modified: datetime = datetime(2026, 1, 1)
        self.metadata: dict[str, str] = {}


class _MinioResponse:
    """The urllib3 response surface ``AsyncMinIOClient.stream_object`` reads."""

    def __init__(self, payload: bytes) -> None:
        """Wrap the slice the store sends.

        Args:
            payload (bytes): The bytes the ranged ``GET`` returns.
        """
        self._buffer: BytesIO = BytesIO(payload)

    def read(self, amt: int | None = None) -> bytes:
        """Read up to ``amt`` bytes.

        Args:
            amt (int | None): Most bytes to read; ``None`` reads the rest.

        Returns:
            bytes: The next bytes, empty at the end.
        """
        return self._buffer.read(amt)

    def close(self) -> None:
        """Accept the close the client makes after the stream ends."""

    def release_conn(self) -> None:
        """Accept the release the client makes after the stream ends."""


class _Minio:
    """A one-object ``minio.Minio`` covering ``stat_object`` and ``get_object``."""

    def __init__(self, *_: Any, **__: Any) -> None:
        """Hold :data:`PAYLOAD` under :data:`KEY` in :data:`BUCKET`.

        Args:
            *_ (Any): Ignored constructor arguments.
            **__ (Any): Ignored constructor keywords.
        """
        self.objects: dict[tuple[str, str], bytes] = {(BUCKET, KEY): PAYLOAD}

    def stat_object(self, bucket: str, key: str) -> _MinioObject:
        """Describe a stored object.

        Args:
            bucket (str): Bucket.
            key (str): Key.

        Returns:
            _MinioObject: The object's attributes.
        """
        return _MinioObject(self.objects[(bucket, key)])

    def get_object(
        self, bucket: str, key: str, offset: int = 0, length: int = 0
    ) -> _MinioResponse:
        """Answer a ``GET``, ranged the way ``Minio.get_object`` ranges it.

        Args:
            bucket (str): Bucket.
            key (str): Key.
            offset (int): First byte.
            length (int): Bytes from ``offset``; ``0`` reads to the end.

        Returns:
            _MinioResponse: The body.
        """
        payload = self.objects[(bucket, key)]
        end = offset + length if length else len(payload)
        return _MinioResponse(payload[offset:end])


async def _minio_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncMinIOClient:
    """Build an ``AsyncMinIOClient`` whose ``minio.Minio`` is :class:`_Minio`.

    Args:
        tmp_path (Path): Unused; keeps the builders interchangeable.
        monkeypatch (pytest.MonkeyPatch): Patches ``minio.Minio``.

    Returns:
        AsyncMinIOClient: The client, with :data:`KEY` stored.
    """
    del tmp_path
    monkeypatch.setattr("minio.Minio", _Minio)
    return AsyncMinIOClient(
        endpoint="fake:9000",
        access_key="ak",
        secret_key="sk",
        default_bucket=BUCKET,
    )


async def _local_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> LocalObjectStorageClient:
    """Build a ``LocalObjectStorageClient`` under ``tmp_path``.

    Args:
        tmp_path (Path): The store's root.
        monkeypatch (pytest.MonkeyPatch): Unused; keeps the builders
            interchangeable.

    Returns:
        LocalObjectStorageClient: The client, with :data:`KEY` stored.
    """
    del monkeypatch
    store = LocalObjectStorageClient(
        tmp_path,
        public_url="https://api.example.com/",
        signing_secret="contract-secret-with-enough-bytes-0123456789",
        default_bucket=BUCKET,
    )
    await store.put_object(KEY, PAYLOAD, content_type="text/plain")
    return store


async def _fake_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FakeObjectStorageClient:
    """Build a ``FakeObjectStorageClient``.

    Args:
        tmp_path (Path): Unused; keeps the builders interchangeable.
        monkeypatch (pytest.MonkeyPatch): Unused; keeps the builders
            interchangeable.

    Returns:
        FakeObjectStorageClient: The client, with :data:`KEY` stored.
    """
    del tmp_path, monkeypatch
    store = FakeObjectStorageClient(default_bucket=BUCKET)
    await store.put_object(KEY, PAYLOAD, content_type="text/plain")
    return store


StoreBuilder = Callable[[Path, pytest.MonkeyPatch], Any]
"""An async builder returning a store with :data:`KEY` already stored."""

BUILDERS: dict[str, StoreBuilder] = {
    "minio": _minio_store,
    "local": _local_store,
    "fake": _fake_store,
}
"""Every SDK store, by the id the parametrized cases report."""


@pytest.fixture(params=sorted(BUILDERS))
async def store(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DownloadableStore:
    """Each SDK store in turn, holding :data:`PAYLOAD` under :data:`KEY`.

    Args:
        request (pytest.FixtureRequest): Carries the store id.
        tmp_path (Path): Root for the local store.
        monkeypatch (pytest.MonkeyPatch): Patches ``minio.Minio``.

    Returns:
        DownloadableStore: The store.
    """
    built: DownloadableStore = await BUILDERS[request.param](tmp_path, monkeypatch)
    return built


def _request(**headers: str) -> Request:
    """Build a ``GET`` carrying ``headers``.

    Args:
        **headers (str): Header values; ``_`` in a name becomes ``-``.

    Returns:
        Request: The request.
    """
    raw = [
        (name.replace("_", "-").lower().encode(), value.encode())
        for name, value in headers.items()
    ]
    return Request({"type": "http", "method": "GET", "headers": raw})


async def _chunks(response: StreamingResponse) -> list[bytes]:
    """Drain a streaming response, keeping chunk boundaries.

    Args:
        response (StreamingResponse): The response.

    Returns:
        list[bytes]: The chunks, in order.
    """
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        assert isinstance(chunk, bytes)
        chunks.append(chunk)
    return chunks


def _split(payload: bytes, size: int) -> list[bytes]:
    """Cut ``payload`` the way a ``size`` chunk stream does.

    Args:
        payload (bytes): The bytes.
        size (int): Bytes per piece.

    Returns:
        list[bytes]: The pieces; the last may be shorter.
    """
    return [payload[start : start + size] for start in range(0, len(payload), size)]


class TestWholeObject:
    """No ``Range``: ``200`` with the whole payload."""

    async def test_without_request(self, store: DownloadableStore) -> None:
        """No request at all serves the whole object in ``chunk_size`` pieces."""
        response = await object_download_response(store, KEY, chunk_size=CHUNK_SIZE)

        assert response.status_code == 200
        assert response.headers["content-length"] == str(len(PAYLOAD))
        assert await _chunks(response) == _split(PAYLOAD, CHUNK_SIZE)

    async def test_with_request_without_range(self, store: DownloadableStore) -> None:
        """A request with no ``Range`` header serves the whole object."""
        response = await object_download_response(
            store, KEY, request=_request(), chunk_size=CHUNK_SIZE
        )

        assert response.status_code == 200
        assert response.headers["accept-ranges"] == "bytes"
        assert await _chunks(response) == _split(PAYLOAD, CHUNK_SIZE)

    async def test_default_chunk_size(self, store: DownloadableStore) -> None:
        """The default chunk size fits the payload in one chunk."""
        response = await object_download_response(store, KEY)

        assert await _chunks(response) == [PAYLOAD]


class TestRange:
    """``Range``: ``206`` with the slice, still paginated by ``chunk_size``."""

    async def test_bounded_range(self, store: DownloadableStore) -> None:
        """``bytes=5-34`` answers 30 bytes in three chunks."""
        response = await object_download_response(
            store, KEY, request=_request(range="bytes=5-34"), chunk_size=CHUNK_SIZE
        )

        assert response.status_code == 206
        assert response.headers["content-range"] == f"bytes 5-34/{len(PAYLOAD)}"
        assert response.headers["content-length"] == "30"
        assert await _chunks(response) == _split(PAYLOAD[5:35], CHUNK_SIZE)

    async def test_suffix_range(self, store: DownloadableStore) -> None:
        """``bytes=-15`` answers the last 15 bytes, the last chunk shorter."""
        response = await object_download_response(
            store, KEY, request=_request(range="bytes=-15"), chunk_size=CHUNK_SIZE
        )

        assert response.status_code == 206
        assert await _chunks(response) == _split(PAYLOAD[-15:], CHUNK_SIZE)

    async def test_unsatisfiable_range(self, store: DownloadableStore) -> None:
        """A range past the end answers ``416`` with no body."""
        response = await object_download_response(
            store, KEY, request=_request(range="bytes=500-600")
        )

        assert response.status_code == 416
        assert response.headers["content-range"] == f"bytes */{len(PAYLOAD)}"


class TestStreamObjectArguments:
    """``stream_object`` refuses the same arguments on every store."""

    @pytest.mark.parametrize("chunk_size", [0, -1])
    async def test_chunk_size_must_be_positive(
        self, store: DownloadableStore, chunk_size: int
    ) -> None:
        """A chunk size below one is refused before any byte is read.

        Before #757, MinIO took both: ``0`` answered ``200`` with a
        ``content-length`` of the whole object and an empty body, and ``-1``
        read the whole object as one chunk.
        """
        with pytest.raises(ValueError, match="chunk_size"):
            stream: AsyncIterator[bytes] = await store.stream_object(
                KEY, chunk_size=chunk_size
            )
            async for _ in stream:
                pass

    async def test_offset_and_length_are_honoured(
        self, store: DownloadableStore
    ) -> None:
        """``offset``/``length`` slice the object; ``chunk_size`` cuts the slice."""
        stream = await store.stream_object(KEY, chunk_size=7, offset=40, length=20)

        assert [chunk async for chunk in stream] == _split(PAYLOAD[40:60], 7)
