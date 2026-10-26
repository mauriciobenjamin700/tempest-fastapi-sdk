"""The S3 POST policy and the signed GET overrides against a real MinIO.

Opt-in (``make test-docker``). ``test_presign.py`` pins what is signed; this
measures the store enforcing it (#637): a file one byte over the cap is
``400 EntityTooLarge``, another type or key is ``403``, nothing is stored on
refusal, and a download link serves the signed type and disposition.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio

from tempest_fastapi_sdk import AsyncMinIOClient, PresignedPost
from tempest_fastapi_sdk.storage import POST_FILE_FIELD

IMAGE: str = "minio/minio:RELEASE.2025-04-22T22-12-26Z"
CONTAINER: str = "tempest-storage-presign-probe"
PORT: int = 59134
KEY: str = "evidence/photo.jpg"

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
async def storage(minio_endpoint: str) -> AsyncIterator[AsyncMinIOClient]:
    """Yield a client over an empty bucket.

    Args:
        minio_endpoint (str): The container endpoint.

    Yields:
        AsyncMinIOClient: The client.
    """
    client = AsyncMinIOClient(
        endpoint=minio_endpoint,
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
        default_bucket="presign",
    )
    await client.ensure_bucket()
    yield client
    for key in await client.list_objects():
        await client.remove_object(key)


async def upload(post: PresignedPost, body: bytes, **changes: str) -> httpx.Response:
    """Post the form the way a browser would, the file last.

    Args:
        post (PresignedPost): The signed form.
        body (bytes): The file.
        **changes (str): Fields to override, to forge a form.

    Returns:
        httpx.Response: The store's answer.
    """
    async with httpx.AsyncClient() as http:
        return await http.post(
            post.url,
            data={**post.fields, **changes},
            files={POST_FILE_FIELD: ("photo.jpg", body, "image/jpeg")},
        )


def s3_code(response: httpx.Response) -> str:
    """Extract the S3 error code from an XML error body.

    Args:
        response (httpx.Response): The store's answer.

    Returns:
        str: The ``<Code>``, or ``""``.
    """
    if "<Code>" not in response.text:
        return ""
    return response.text.split("<Code>")[1].split("</Code>")[0]


class TestPostPolicy:
    async def test_a_file_at_the_cap_is_stored(self, storage: AsyncMinIOClient) -> None:
        post = await storage.presigned_post_policy(
            KEY, max_bytes=1000, content_type="image/jpeg"
        )

        response = await upload(post, b"x" * 1000)

        assert response.status_code == 204
        stat = await storage.stat_object(KEY)
        assert (stat.size, stat.content_type) == (1000, "image/jpeg")

    async def test_one_byte_over_is_entity_too_large(
        self, storage: AsyncMinIOClient
    ) -> None:
        post = await storage.presigned_post_policy(KEY, max_bytes=1000)

        response = await upload(post, b"x" * 1001)

        assert (response.status_code, s3_code(response)) == (400, "EntityTooLarge")
        assert await storage.list_objects() == []

    async def test_under_the_minimum_is_entity_too_small(
        self, storage: AsyncMinIOClient
    ) -> None:
        post = await storage.presigned_post_policy(KEY, max_bytes=1000, min_bytes=10)

        response = await upload(post, b"x" * 9)

        assert (response.status_code, s3_code(response)) == (400, "EntityTooSmall")

    async def test_another_type_or_key_is_refused(
        self, storage: AsyncMinIOClient
    ) -> None:
        post = await storage.presigned_post_policy(
            KEY, max_bytes=1000, content_type="image/jpeg"
        )

        other_type = await upload(post, b"x", **{"Content-Type": "text/html"})
        other_key = await upload(post, b"x", key="evidence/other.jpg")

        assert (other_type.status_code, s3_code(other_type)) == (403, "AccessDenied")
        assert (other_key.status_code, s3_code(other_key)) == (403, "AccessDenied")
        assert await storage.list_objects() == []


class TestGetOverrides:
    async def test_the_link_serves_the_signed_headers(
        self, storage: AsyncMinIOClient
    ) -> None:
        await storage.put_object(KEY, b"data", content_type="text/html")
        url = await storage.presigned_get_url(
            KEY, filename="evidência.jpg", media_type="image/jpeg", as_attachment=False
        )

        async with httpx.AsyncClient() as http:
            served = await http.get(url)
            tampered = await http.get(url.replace("inline", "attachment"))

        assert served.status_code == 200
        assert served.headers["content-type"] == "image/jpeg"
        assert served.headers["content-disposition"].startswith("inline;")
        assert tampered.status_code == 403
