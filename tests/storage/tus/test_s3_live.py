"""``S3TusStore`` against a real S3 server, across a killed server process.

Opt-in (``make test-docker``). The S3 server is SeaweedFS's S3 gateway:
on 2026-11-22 ``docker pull minio/minio`` (and the ``quay.io`` mirror)
answered "pull access denied" on this machine, and SeaweedFS speaks the same
S3 API the ``minio`` client calls. The payload is 6 MiB sent in 256 KiB
chunks — the chunk size of ``tempest-react-sdk``'s field-app recipe, well
under S3's 5 MiB multipart minimum — so the assembly path that exists
because of that minimum is the one exercised.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from collections.abc import Iterator

import httpx
import pytest

from tempest_fastapi_sdk import AsyncMinIOClient
from tempest_fastapi_sdk.storage.tus import S3TusStore
from tests.storage.tus.test_restart import TUS, ServerProcess, free_port

IMAGE: str = "chrislusf/seaweedfs:latest"
CONTAINER: str = "tempest-tus-s3-probe"
BUCKET: str = "tus"
PAYLOAD: bytes = os.urandom(6 * 1024 * 1024)
CHUNK: int = 256 * 1024

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def s3_endpoint() -> Iterator[str]:
    """Start the S3 server in a container and yield its ``host:port``.

    Yields:
        str: The endpoint.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    port = free_port()
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
            f"127.0.0.1:{port}:8333",
            IMAGE,
            "server",
            "-s3",
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {started.stderr.strip()}")
    endpoint = f"127.0.0.1:{port}"
    try:
        client = AsyncMinIOClient(
            endpoint=endpoint,
            access_key="minioadmin",
            secret_key="minioadmin",
            default_bucket=BUCKET,
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                asyncio.run(client.ensure_bucket())
                break
            except Exception:
                if time.monotonic() > deadline:
                    pytest.skip("S3 server never became ready")
                time.sleep(1)
        yield endpoint
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


@pytest.fixture
def server(s3_endpoint: str) -> Iterator[ServerProcess]:
    """Yield a tus server handle backed by the bucket.

    Args:
        s3_endpoint (str): The S3 endpoint.

    Yields:
        ServerProcess: The handle; killed on teardown.
    """
    handle = ServerProcess(f"minio:{s3_endpoint}:{BUCKET}")
    try:
        yield handle
    finally:
        handle.kill()


def _send(base: str, path: str, start: int, stop: int) -> int:
    """Send chunks from ``start`` until the offset reaches ``stop``.

    Args:
        base (str): Server base URL.
        path (str): Upload path.
        start (int): First offset.
        stop (int): Offset to stop at (or past).

    Returns:
        int: The offset reached.
    """
    offset = start
    while offset < stop:
        answer = httpx.patch(
            f"{base}{path}",
            content=PAYLOAD[offset : offset + CHUNK],
            headers={
                **TUS,
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": str(offset),
            },
            timeout=60,
        )
        assert answer.status_code == 204, answer.text
        offset = int(answer.headers["upload-offset"])
    return offset


async def _read_back(s3_endpoint: str, upload_id: str) -> tuple[bytes, list[str]]:
    """Read the assembled file and list what is left under the upload.

    Args:
        s3_endpoint (str): The S3 endpoint.
        upload_id (str): The upload id.

    Returns:
        tuple[bytes, list[str]]: The file and the keys under the upload.
    """
    client = AsyncMinIOClient(
        endpoint=s3_endpoint,
        access_key="minioadmin",
        secret_key="minioadmin",
        default_bucket=BUCKET,
    )
    store = S3TusStore(client)
    data = b"".join([block async for block in store.stream(upload_id)])
    keys = await client.list_objects(f"tus/{upload_id}/")
    return data, keys


def test_s3_upload_survives_a_restart_of_the_server_process(
    server: ServerProcess,
    s3_endpoint: str,
) -> None:
    """``SIGKILL`` mid-upload; a new process resumes from the bucket's record."""
    base = server.start()
    created = httpx.post(
        f"{base}/api/uploads",
        headers={**TUS, "Upload-Length": str(len(PAYLOAD))},
        timeout=60,
    )
    assert created.status_code == 201, created.text
    path = created.headers["location"]
    reached = _send(base, path, 0, len(PAYLOAD) // 2)
    assert isinstance(server.process, subprocess.Popen)
    first_pid = server.process.pid
    server.kill()

    base = server.start()
    assert server.process is not None and server.process.pid != first_pid
    head = httpx.head(f"{base}{path}", headers=TUS, timeout=60)
    assert head.status_code == 200
    assert int(head.headers["upload-offset"]) == reached == len(PAYLOAD) // 2
    _send(base, path, reached, len(PAYLOAD))

    upload_id = path.rsplit("/", 1)[-1]
    data, keys = asyncio.run(_read_back(s3_endpoint, upload_id))
    assert data == PAYLOAD
    assert sorted(key.rsplit("/", 1)[-1] for key in keys) == ["data", "info.json"]
