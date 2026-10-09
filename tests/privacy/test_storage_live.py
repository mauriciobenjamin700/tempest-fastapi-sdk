"""``SubjectObjectStorage.delete_all`` against a real MinIO.

Opt-in (``make test-docker``). The offline fake proves the call shape; this
proves the store agrees: a subject with more than 1000 objects (more than one
``DeleteObjects`` request) is erased completely, and neither a subject whose
id shares the prefix (``"7"`` vs ``"77"``) nor another subject loses an
object.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator

import httpx
import pytest

from tempest_fastapi_sdk import AsyncMinIOClient, PutObjectItem
from tempest_fastapi_sdk.privacy import SubjectObjectStorage

IMAGE: str = "minio/minio:RELEASE.2025-04-22T22-12-26Z"
CONTAINER: str = "tempest-privacy-subject-storage-probe"
PORT: int = 59147
OBJECTS: int = 1203
"""More than one 1000-key ``DeleteObjects`` batch."""

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


async def test_delete_all_erases_only_the_subject(minio_endpoint: str) -> None:
    """Erase 1203 objects of subject 7; subjects 77 and 8 keep theirs."""
    client = AsyncMinIOClient(
        endpoint=minio_endpoint,
        access_key="minioadmin",
        secret_key="minioadmin",
        default_bucket="subjects",
    )
    await client.ensure_bucket()
    storage = SubjectObjectStorage(client, prefix="users")
    await client.put_objects(
        [
            PutObjectItem(key=storage.key(7, f"files/{n:05d}.bin"), data=b"x")
            for n in range(OBJECTS)
        ],
        max_concurrency=32,
    )
    await storage.put(77, "keep.bin", b"y")
    await storage.put(8, "keep.bin", b"z")
    assert len(await storage.names(7)) == OBJECTS

    batches: list[int] = []
    real_remove = client.client.remove_objects

    def _spy(bucket: str, objects: list[object]) -> object:
        """Count the keys handed to ``Minio.remove_objects`` per call.

        Args:
            bucket (str): Bucket name.
            objects (list[object]): The ``DeleteObject`` list.

        Returns:
            object: The real iterator of delete errors.
        """
        batches.append(len(objects))
        return real_remove(bucket, objects)  # type: ignore[arg-type]

    client.client.remove_objects = _spy  # type: ignore[method-assign,assignment]
    requests: list[int] = []
    real_delete = client.client._delete_objects

    def _request_spy(bucket: str, objects: list[object], **kwargs: object) -> object:
        """Count the keys sent per ``DeleteObjects`` HTTP request.

        Args:
            bucket (str): Bucket name.
            objects (list[object]): The keys of this request.
            **kwargs (object): Forwarded to ``Minio._delete_objects``.

        Returns:
            object: The real ``DeleteResult``.
        """
        requests.append(len(objects))
        return real_delete(bucket, objects, **kwargs)  # type: ignore[arg-type]

    client.client._delete_objects = _request_spy  # type: ignore[method-assign,assignment]

    assert await storage.delete_all(7) == OBJECTS
    assert batches == [OBJECTS]
    assert requests == [1000, OBJECTS - 1000]
    assert await storage.names(7) == []
    assert await storage.names(77) == ["keep.bin"]
    assert await storage.names(8) == ["keep.bin"]
    assert await storage.delete_all(7) == 0
