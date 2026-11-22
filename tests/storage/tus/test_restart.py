"""An interrupted upload resumes after the server process is killed.

The server runs in a subprocess (``_tus_server.py``, real uvicorn over TCP).
The first process accepts half the file and is killed with ``SIGKILL`` — no
shutdown hook runs. A second process, built from the same directory and
nothing else, answers the ``HEAD`` with the offset the first one stored and
takes the rest.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

SERVER: Path = Path(__file__).with_name("_tus_server.py")
PAYLOAD: bytes = os.urandom(300 * 1024)
CHUNK: int = 64 * 1024
TUS: dict[str, str] = {"Tus-Resumable": "1.0.0", "X-User": "ana"}


def free_port() -> int:
    """Ask the kernel for a free TCP port.

    Returns:
        int: The port.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ServerProcess:
    """Start and kill ``_tus_server.py`` by PID."""

    def __init__(self, spec: str) -> None:
        """Remember the store spec.

        Args:
            spec (str): The store argument for the server.
        """
        self.spec: str = spec
        self.process: subprocess.Popen[bytes] | None = None
        self.base_url: str = ""

    def start(self) -> str:
        """Start a fresh process and wait for it to answer.

        Returns:
            str: The base URL.
        """
        port = free_port()
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER), str(port), self.spec],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else b""
                raise RuntimeError(f"server exited: {stderr.decode()}")
            try:
                httpx.head(f"{self.base_url}/api/uploads/{'x' * 32}", timeout=1)
                return self.base_url
            except httpx.TransportError:
                time.sleep(0.1)
        raise RuntimeError("server never answered")

    def kill(self) -> None:
        """Send ``SIGKILL`` to the process and reap it."""
        if self.process is not None and self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)


@pytest.fixture
def server(tmp_path: Path) -> Iterator[ServerProcess]:
    """Yield a server handle on a local store under ``tmp_path``.

    Args:
        tmp_path (Path): Store directory.

    Yields:
        ServerProcess: The handle; killed on teardown.
    """
    handle = ServerProcess(f"local:{tmp_path / 'tus'}")
    try:
        yield handle
    finally:
        handle.kill()


def upload_across_a_kill(server: ServerProcess) -> tuple[str, int]:
    """Send half the payload, kill the server, restart it, send the rest.

    Args:
        server (ServerProcess): The server handle.

    Returns:
        tuple[str, int]: The upload URL and the offset the second process
        reported before resuming.
    """
    base = server.start()
    created = httpx.post(
        f"{base}/api/uploads",
        headers={**TUS, "Upload-Length": str(len(PAYLOAD))},
    )
    assert created.status_code == 201, created.text
    path = created.headers["location"]
    offset = 0
    while offset < len(PAYLOAD) // 2:
        answer = httpx.patch(
            f"{base}{path}",
            content=PAYLOAD[offset : offset + CHUNK],
            headers={
                **TUS,
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": str(offset),
            },
        )
        assert answer.status_code == 204, answer.text
        offset = int(answer.headers["upload-offset"])
    server.kill()

    base = server.start()
    head = httpx.head(f"{base}{path}", headers=TUS)
    assert head.status_code == 200
    resumed_from = int(head.headers["upload-offset"])
    offset = resumed_from
    while offset < len(PAYLOAD):
        answer = httpx.patch(
            f"{base}{path}",
            content=PAYLOAD[offset : offset + CHUNK],
            headers={
                **TUS,
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": str(offset),
            },
        )
        assert answer.status_code == 204, answer.text
        offset = int(answer.headers["upload-offset"])
    return path, resumed_from


def test_local_upload_survives_a_restart_of_the_server_process(
    server: ServerProcess,
    tmp_path: Path,
) -> None:
    """``SIGKILL`` mid-upload; a new process resumes at the stored offset."""
    path, resumed_from = upload_across_a_kill(server)
    assert isinstance(server.process, subprocess.Popen)
    assert server.process.pid != os.getpid()
    assert resumed_from == 3 * CHUNK
    upload_id = path.rsplit("/", 1)[-1]
    assert (tmp_path / "tus" / f"{upload_id}.bin").read_bytes() == PAYLOAD
    other = httpx.head(f"{server.base_url}{path}", headers={**TUS, "X-User": "bruno"})
    assert other.status_code == 404
