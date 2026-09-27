"""``download_response`` ranges and validators against a real MinIO.

Opt-in (``make test-docker``): starts a MinIO container and serves one of
its objects through a FastAPI route built on
:meth:`AsyncMinIOClient.download_response`, so "``206`` for the three range
forms, ``416``, ``304`` by ETag and by date" is measured against the real
store — its ETag format, its ``Last-Modified`` resolution and its answer to
``get_object(offset=, length=)`` — rather than against the in-memory fake.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from starlette.responses import Response

from tempest_fastapi_sdk import AsyncMinIOClient

IMAGE: str = "minio/minio:RELEASE.2025-04-22T22-12-26Z"
CONTAINER: str = "tempest-storage-download-probe"
PORT: int = 59131
PAYLOAD: bytes = bytes(range(256)) * 4096
"""A 1 MiB object whose bytes encode their own offset modulo 256."""

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def minio_endpoint() -> Iterator[str]:
    """Start MinIO in a container and yield its ``host:port``.

    Yields:
        str: The endpoint the client connects to.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-p",
            f"127.0.0.1:{PORT}:9000",
            IMAGE,
            "server",
            "/data",
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(60):
            try:
                ready = httpx.get(
                    f"http://127.0.0.1:{PORT}/minio/health/live", timeout=1
                )
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            pytest.skip("minio never became ready")
        yield f"127.0.0.1:{PORT}"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


class _GetObjectSpy:
    """Record the ``offset``/``length`` every ``get_object`` call carries."""

    def __init__(self, inner: Any) -> None:
        """Wrap the real ``Minio.get_object``.

        Args:
            inner (Any): The bound method being spied on.
        """
        self.inner: Any = inner
        self.calls: list[tuple[int, int]] = []

    def __call__(self, bucket: str, key: str, **kwargs: Any) -> Any:
        """Record the call and forward it.

        Args:
            bucket (str): Bucket name.
            key (str): Object key.
            **kwargs (Any): Forwarded to ``Minio.get_object``.

        Returns:
            Any: Whatever the real client returned.
        """
        self.calls.append((kwargs.get("offset", 0), kwargs.get("length", 0)))
        return self.inner(bucket, key, **kwargs)


@pytest_asyncio.fixture
async def served(
    minio_endpoint: str,
) -> AsyncIterator[tuple[httpx.AsyncClient, _GetObjectSpy]]:
    """Yield an HTTP client for an app serving ``clip.mp4`` from MinIO.

    Args:
        minio_endpoint (str): The container endpoint.

    Yields:
        tuple[httpx.AsyncClient, _GetObjectSpy]: The client and the spy on
        the store reads.
    """
    storage = AsyncMinIOClient(
        endpoint=minio_endpoint,
        access_key="minioadmin",
        secret_key="minioadmin",
        default_bucket="media",
    )
    await storage.ensure_bucket()
    await storage.put_object("clip.mp4", PAYLOAD, content_type="video/mp4")
    spy = _GetObjectSpy(storage.client.get_object)
    storage.client.get_object = spy  # type: ignore[method-assign]

    app = FastAPI()

    @app.get("/files/{key}")
    async def download(key: str, request: Request) -> Response:
        """Serve the object with range and validator support."""
        return await storage.download_response(
            key, request=request, as_attachment=False
        )

    @app.get("/plain/{key}")
    async def plain(key: str) -> Response:
        """Serve the object without passing the request."""
        return await storage.download_response(key)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield http, spy


async def test_range_forms_are_206_with_only_the_slice_read(
    served: tuple[httpx.AsyncClient, _GetObjectSpy],
) -> None:
    http, spy = served
    size = len(PAYLOAD)
    for header, start, end in [
        ("bytes=1000-1999", 1000, 1999),
        (f"bytes={size - 10}-", size - 10, size - 1),
        ("bytes=-300", size - 300, size - 1),
    ]:
        spy.calls.clear()
        response = await http.get("/files/clip.mp4", headers={"Range": header})
        assert response.status_code == 206, header
        assert response.content == PAYLOAD[start : end + 1]
        assert response.headers["content-range"] == f"bytes {start}-{end}/{size}"
        assert response.headers["content-length"] == str(end - start + 1)
        assert response.headers["content-type"] == "video/mp4"
        assert spy.calls == [(start, end - start + 1)]


async def test_out_of_bounds_range_is_416(
    served: tuple[httpx.AsyncClient, _GetObjectSpy],
) -> None:
    http, spy = served
    spy.calls.clear()
    response = await http.get(
        "/files/clip.mp4", headers={"Range": f"bytes={len(PAYLOAD)}-"}
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(PAYLOAD)}"
    assert response.content == b""
    assert spy.calls == []


async def test_multiple_ranges_are_200_whole(
    served: tuple[httpx.AsyncClient, _GetObjectSpy],
) -> None:
    http, _ = served
    response = await http.get("/files/clip.mp4", headers={"Range": "bytes=0-1,5-6"})
    assert response.status_code == 200
    assert response.content == PAYLOAD


async def test_revalidation_by_etag_and_by_date_is_304(
    served: tuple[httpx.AsyncClient, _GetObjectSpy],
) -> None:
    http, spy = served
    first = await http.get("/files/clip.mp4")
    assert first.status_code == 200
    assert first.headers["accept-ranges"] == "bytes"
    etag = first.headers["etag"]
    last_modified = first.headers["last-modified"]
    assert etag.startswith('"') and etag.endswith('"')

    spy.calls.clear()
    by_etag = await http.get("/files/clip.mp4", headers={"If-None-Match": etag})
    assert by_etag.status_code == 304
    assert by_etag.content == b""
    assert by_etag.headers["etag"] == etag

    by_date = await http.get(
        "/files/clip.mp4", headers={"If-Modified-Since": last_modified}
    )
    assert by_date.status_code == 304
    assert spy.calls == []

    stale = await http.get("/files/clip.mp4", headers={"If-None-Match": '"stale"'})
    assert stale.status_code == 200


async def test_resume_with_if_range(
    served: tuple[httpx.AsyncClient, _GetObjectSpy],
) -> None:
    http, _ = served
    etag = (await http.get("/files/clip.mp4")).headers["etag"]
    same = await http.get(
        "/files/clip.mp4", headers={"Range": "bytes=10-", "If-Range": etag}
    )
    assert same.status_code == 206
    changed = await http.get(
        "/files/clip.mp4", headers={"Range": "bytes=10-", "If-Range": '"other"'}
    )
    assert changed.status_code == 200
    assert changed.content == PAYLOAD


async def test_without_request_is_always_200(
    served: tuple[httpx.AsyncClient, _GetObjectSpy],
) -> None:
    http, _ = served
    response = await http.get(
        "/plain/clip.mp4", headers={"Range": "bytes=0-9", "If-None-Match": "*"}
    )
    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["accept-ranges"] == "bytes"
