"""``X-Accel-Redirect`` delivery through a real nginx in front of a real MinIO.

Opt-in (``make test-docker``). Three processes, each in its own place:

* MinIO in a container, published on ``127.0.0.1:MINIO_PORT``;
* the FastAPI app under uvicorn in this process, on ``127.0.0.1:APP_PORT``;
* nginx in a container on the host network, on ``127.0.0.1:NGINX_PORT``,
  proxying ``/`` to the app and following ``X-Accel-Redirect`` into
  ``internal`` locations that proxy to MinIO.

Every request below goes client → nginx → app → nginx → MinIO, so the
claims the storage recipe makes about nginx (the ``404`` on a direct hit to
the internal location, the ``Range`` it forwards, the ``403
SignatureDoesNotMatch`` when ``Host`` is not the signed one, the ``400`` when
``proxy_pass`` keeps the prefix) are measured, not deduced. The host network
is what lets the nginx container reach the app and MinIO at the very host the
URL was signed for; where it is unavailable (Docker Desktop) the module skips.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import Response

from tempest_fastapi_sdk import AsyncMinIOClient, MinIOSettings

MINIO_IMAGE: str = "minio/minio:RELEASE.2025-04-22T22-12-26Z"
NGINX_IMAGE: str = os.environ.get("TEMPEST_NGINX_IMAGE", "nginx:1.27-alpine")
"""Overridable so the same suite can be run against other nginx releases."""
MINIO_CONTAINER: str = "tempest-accel-live-minio"
NGINX_CONTAINER: str = "tempest-accel-live-nginx"
MINIO_PORT: int = 59151
NGINX_PORT: int = 59152
APP_PORT: int = 59153
INTERNAL: str = f"127.0.0.1:{MINIO_PORT}"
PAYLOAD: bytes = bytes(range(256)) * 4096
ODD_KEY: str = "pasta/relatório final (v2)+x.mp4"

NGINX_CONF: str = f"""
events {{}}
http {{
    server {{
        listen 127.0.0.1:{NGINX_PORT};
        proxy_set_header Host $host;

        location / {{
            proxy_pass http://127.0.0.1:{APP_PORT};
        }}

        location /_bucket/ {{
            internal;
            proxy_pass http://{INTERNAL}/;
            proxy_set_header Host {INTERNAL};
        }}

        location /_inherits_host/ {{
            internal;
            proxy_pass http://{INTERNAL}/;
        }}

        location /_keeps_prefix/ {{
            internal;
            proxy_pass http://{INTERNAL};
            proxy_set_header Host {INTERNAL};
        }}
    }}
}}
"""
"""The recipe's block plus two broken variants, one per pitfall."""

pytestmark = pytest.mark.docker


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a docker CLI command, capturing its output.

    Args:
        *args (str): Arguments after ``docker``.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.
    """
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _wait_for(url: str, *, attempts: int = 60) -> bool:
    """Poll ``url`` until it answers any HTTP status.

    Args:
        url (str): The URL to poll.
        attempts (int): How many one-second attempts to make.

    Returns:
        bool: ``True`` once the URL answered.
    """
    for _ in range(attempts):
        try:
            httpx.get(url, timeout=1)
            return True
        except httpx.HTTPError:
            time.sleep(1)
    return False


def _build_app() -> FastAPI:
    """Build the app: one route per delivery mode, plus the pitfall routes.

    Both ``serve_object`` routes are the same code; only the settings the
    client is built from differ, which is the switch the recipe documents.

    Returns:
        FastAPI: The application under test.
    """
    base = {
        "MINIO_ENDPOINT": INTERNAL,
        "MINIO_DEFAULT_BUCKET": "media",
        "MINIO_PUBLIC_ENDPOINT": "https://storage.example.com",
    }
    redirecting = AsyncMinIOClient(
        **MinIOSettings(**base, STORAGE_ACCEL_REDIRECT=True).minio_kwargs()
    )
    proxying = AsyncMinIOClient(**MinIOSettings(**base).minio_kwargs())
    app = FastAPI()

    @app.get("/files/{key:path}")
    async def files(key: str, request: Request) -> Response:
        """Serve through nginx (``STORAGE_ACCEL_REDIRECT=true``)."""
        return await redirecting.serve_object(key, request=request, as_attachment=False)

    @app.get("/proxied/{key:path}")
    async def proxied(key: str, request: Request) -> Response:
        """Serve through the app (``STORAGE_ACCEL_REDIRECT=false``)."""
        return await proxying.serve_object(key, request=request, as_attachment=False)

    @app.get("/report/{key:path}")
    async def report(key: str) -> Response:
        """Force a download with overridden type and cache policy."""
        return await redirecting.accel_redirect_response(
            key,
            filename="relatório.pdf",
            media_type="application/pdf",
            as_attachment=True,
            cache_control="private, max-age=60",
        )

    @app.get("/pitfall/{prefix}/{key:path}")
    async def pitfall(prefix: str, key: str) -> Response:
        """Redirect into one of the broken locations."""
        return await redirecting.accel_redirect_response(
            key, internal_prefix=f"/{prefix}/"
        )

    return app


@pytest.fixture(scope="module")
def nginx_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start MinIO, the app and nginx; yield nginx's base URL.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Where ``nginx.conf`` goes.

    Yields:
        str: ``http://127.0.0.1:<NGINX_PORT>``.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if _docker("info").returncode != 0:
        pytest.skip("docker daemon not reachable")
    _docker("rm", "-f", MINIO_CONTAINER, NGINX_CONTAINER)
    conf: Path = tmp_path_factory.mktemp("nginx") / "nginx.conf"
    conf.write_text(NGINX_CONF)
    conf.parent.chmod(0o755)
    conf.chmod(0o644)
    started = _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        MINIO_CONTAINER,
        "-p",
        f"{INTERNAL}:9000",
        MINIO_IMAGE,
        "server",
        "/data",
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {MINIO_IMAGE}: {started.stderr.strip()}")
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    try:
        if not _wait_for(f"http://{INTERNAL}/minio/health/live"):
            pytest.skip("minio never became ready")
        seed = AsyncMinIOClient(
            endpoint=INTERNAL,
            access_key="minioadmin",
            secret_key="minioadmin",
            default_bucket="media",
        )
        seed.client.make_bucket("media")
        for key in ("clip.mp4", ODD_KEY):
            seed.client.put_object(
                "media",
                key,
                io.BytesIO(PAYLOAD),
                len(PAYLOAD),
                content_type="video/mp4",
            )
        server = uvicorn.Server(
            uvicorn.Config(
                _build_app(), host="127.0.0.1", port=APP_PORT, log_level="warning"
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        if not _wait_for(f"http://127.0.0.1:{APP_PORT}/docs"):
            pytest.skip("app never started")
        started = _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            NGINX_CONTAINER,
            "--network",
            "host",
            "-v",
            f"{conf}:/etc/nginx/nginx.conf:ro",
            NGINX_IMAGE,
        )
        if started.returncode != 0:
            pytest.skip(f"could not start {NGINX_IMAGE}: {started.stderr.strip()}")
        base = f"http://127.0.0.1:{NGINX_PORT}"
        if not _wait_for(f"{base}/docs", attempts=20):
            pytest.skip("nginx on the host network is unreachable (Docker Desktop?)")
        if httpx.get(f"{base}/docs", timeout=5).status_code != 200:
            pytest.skip("nginx cannot reach the app over the host network")
        yield base
    finally:
        _docker("rm", "-f", NGINX_CONTAINER, MINIO_CONTAINER)
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)


def test_route_serves_the_object_through_nginx(nginx_url: str) -> None:
    response = httpx.get(f"{nginx_url}/files/clip.mp4", timeout=10)
    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers.get_list("content-disposition") == [
        "inline; filename=\"clip.mp4\"; filename*=UTF-8''clip.mp4"
    ]
    assert response.headers["accept-ranges"] == "bytes"
    assert "x-accel-redirect" not in response.headers


def test_range_is_forwarded_to_the_bucket(nginx_url: str) -> None:
    response = httpx.get(
        f"{nginx_url}/files/clip.mp4", headers={"Range": "bytes=100-199"}, timeout=10
    )
    assert response.status_code == 206
    assert response.content == PAYLOAD[100:200]
    assert response.headers["content-range"] == f"bytes 100-199/{len(PAYLOAD)}"


def test_revalidation_is_answered_by_the_bucket(nginx_url: str) -> None:
    etag = httpx.get(f"{nginx_url}/files/clip.mp4", timeout=10).headers["etag"]
    response = httpx.get(
        f"{nginx_url}/files/clip.mp4", headers={"If-None-Match": etag}, timeout=10
    )
    assert response.status_code == 304


def test_key_with_spaces_accents_and_plus(nginx_url: str) -> None:
    response = httpx.get(f"{nginx_url}/files/{ODD_KEY}", timeout=10)
    assert response.status_code == 200
    assert response.content == PAYLOAD


def test_signed_overrides_reach_the_client(nginx_url: str) -> None:
    response = httpx.get(f"{nginx_url}/report/clip.mp4", timeout=10)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, max-age=60"
    assert response.headers.get_list("content-disposition") == [
        "attachment; filename=\"relatrio.pdf\"; filename*=UTF-8''relat%C3%B3rio.pdf"
    ]


def test_internal_location_is_404_from_the_client(nginx_url: str) -> None:
    response = httpx.get(f"{nginx_url}/_bucket/media/clip.mp4", timeout=10)
    assert response.status_code == 404


def test_proxy_mode_on_the_same_code_path(nginx_url: str) -> None:
    response = httpx.get(
        f"{nginx_url}/proxied/clip.mp4", headers={"Range": "bytes=-10"}, timeout=10
    )
    assert response.status_code == 206
    assert response.content == PAYLOAD[-10:]


def test_inherited_host_breaks_the_signature(nginx_url: str) -> None:
    response = httpx.get(f"{nginx_url}/pitfall/_inherits_host/clip.mp4", timeout=10)
    assert response.status_code == 403
    assert "<Code>SignatureDoesNotMatch</Code>" in response.text


def test_proxy_pass_without_trailing_slash_keeps_the_prefix(nginx_url: str) -> None:
    response = httpx.get(f"{nginx_url}/pitfall/_keeps_prefix/clip.mp4", timeout=10)
    assert response.status_code == 400
    assert "<Code>InvalidBucketName</Code>" in response.text
