"""A signed app URL in front of ``download_response`` against a real MinIO.

Opt-in (``make test-docker``). Measures the composition the storage recipe
teaches: the mapper signs ``/api/files/<key>`` with :func:`sign_path`, the
route verifies it with :func:`make_signed_path_dependency` and streams with
``download_response(..., request=)`` (or ``serve_object`` in redirect mode) —
so ``Range`` still answers ``206`` behind the signature, and a rejected URL
never reaches the store.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from starlette.responses import Response

from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    make_signed_path_dependency,
    register_exception_handlers,
    sign_path,
)

IMAGE: str = "minio/minio:RELEASE.2025-04-22T22-12-26Z"
CONTAINER: str = "tempest-storage-signed-url-probe"
PORT: int = 59133
SECRET: str = "app-secret"
KEY: str = "aulas/aula 01.mp4"
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


@pytest_asyncio.fixture
async def served(
    minio_endpoint: str,
) -> AsyncIterator[tuple[httpx.AsyncClient, list[str]]]:
    """Yield a client for an app serving ``KEY`` behind a signed URL.

    Args:
        minio_endpoint (str): The container endpoint.

    Yields:
        tuple[httpx.AsyncClient, list[str]]: The client and the keys the
        store was asked to ``stat``.
    """
    storage = AsyncMinIOClient(
        endpoint=minio_endpoint,
        access_key="minioadmin",
        secret_key="minioadmin",
        default_bucket="media",
    )
    await storage.ensure_bucket()
    await storage.put_object(KEY, PAYLOAD, content_type="video/mp4")
    stats: list[str] = []
    real_stat = storage.client.stat_object

    def _stat_spy(bucket: str, key: str, **kwargs: Any) -> Any:
        """Record the key and forward to ``Minio.stat_object``.

        Args:
            bucket (str): Bucket name.
            key (str): Object key.
            **kwargs (Any): Forwarded to ``Minio.stat_object``.

        Returns:
            Any: Whatever the real client returned.
        """
        stats.append(key)
        return real_stat(bucket, key, **kwargs)

    storage.client.stat_object = _stat_spy  # type: ignore[method-assign]
    signed_file = make_signed_path_dependency(secret=SECRET, purpose="files")

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/api/files/{key:path}", dependencies=[Depends(signed_file)])
    async def download(key: str, request: Request) -> Response:
        """Stream the object once the signature verified."""
        return await storage.download_response(
            key, request=request, as_attachment=False
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield http, stats


def _signed() -> str:
    """Sign the download URL of ``KEY``.

    Returns:
        str: The signed URL.
    """
    return sign_path(
        f"/api/files/{KEY}",
        secret=SECRET,
        expires_in=timedelta(minutes=5),
        purpose="files",
    )


async def test_signed_url_streams_the_object(
    served: tuple[httpx.AsyncClient, list[str]],
) -> None:
    http, stats = served
    response = await http.get(_signed())
    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert stats == [KEY]


async def test_range_behind_the_signature_is_206(
    served: tuple[httpx.AsyncClient, list[str]],
) -> None:
    http, _ = served
    response = await http.get(_signed(), headers={"Range": "bytes=1000-1999"})
    assert response.status_code == 206
    assert response.content == PAYLOAD[1000:2000]
    assert response.headers["content-range"] == f"bytes 1000-1999/{len(PAYLOAD)}"


async def test_rejected_url_never_reaches_the_store(
    served: tuple[httpx.AsyncClient, list[str]],
) -> None:
    http, stats = served
    tampered = _signed().replace("aula%2001", "aula%2002")
    response = await http.get(tampered)
    assert response.status_code == 403
    assert response.json()["code"] == "SIGNED_URL_INVALID"
    unsigned = await http.get("/api/files/aulas/aula%2001.mp4")
    assert unsigned.status_code == 403
    assert stats == []


async def test_serve_object_in_redirect_mode_behind_the_signature(
    minio_endpoint: str,
) -> None:
    storage = AsyncMinIOClient(
        endpoint=minio_endpoint,
        access_key="minioadmin",
        secret_key="minioadmin",
        default_bucket="media",
        accel_redirect=True,
    )
    signed_file = make_signed_path_dependency(secret=SECRET, purpose="files")
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/api/files/{key:path}", dependencies=[Depends(signed_file)])
    async def download(key: str, request: Request) -> Response:
        """Hand the object to nginx once the signature verified."""
        return await storage.serve_object(key, request=request, as_attachment=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        accepted = await http.get(_signed())
        rejected = await http.get(_signed().replace("aula%2001", "aula%2002"))
    assert accepted.status_code == 200
    assert accepted.headers["x-accel-redirect"].startswith("/_bucket/media/aulas/")
    assert rejected.status_code == 403
    assert "x-accel-redirect" not in rejected.headers
