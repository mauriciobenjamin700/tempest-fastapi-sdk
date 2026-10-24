"""``enqueue_by_name`` across processes, over a real Redis.

The claim the method makes is about two processes: the API sends a name
it never imported, a ``taskiq worker`` elsewhere runs the task with the
labels it was registered with. So the sender here is a subprocess that
imports only the SDK, the worker is the TaskIQ CLI in another, and the
broker is Redis in a container. Running the same worker with the
labels middleware popped shows the run a by-name call got before it.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

CONTAINER: str = "tempest-enqueue-by-name-redis"
"""Name of the Redis container this module starts and removes."""

WORKER_MODULE: str = """
import os
from pathlib import Path

from tempest_fastapi_sdk.tasks import RetryPolicy, TaskQueue

tq = TaskQueue.redis(os.environ["PROBE_REDIS_URL"])
tq.enable_retries(default_max_retries=0)
if os.environ.get("PROBE_DROP_LABELS") == "1":
    tq.broker.middlewares.pop(0)

LOG = Path(os.environ["PROBE_LOG"])


@tq.task(name="probe:flaky", retry=RetryPolicy(max_retries=2))
async def flaky() -> None:
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write("attempt\\n")
    if LOG.read_text(encoding="utf-8").count("attempt") < 2:
        raise RuntimeError("first attempt fails")


broker = tq.broker
"""

SENDER: str = """
import asyncio
import os
import sys

from tempest_fastapi_sdk.tasks import TaskQueue


async def main() -> None:
    tq = TaskQueue.redis(os.environ["PROBE_REDIS_URL"])
    await tq.connect()
    await tq.enqueue_by_name(os.environ["PROBE_TASK_NAME"])
    await tq.disconnect()
    print(sorted(name for name in sys.modules if name.startswith("probe_")))


asyncio.run(main())
"""


def free_port() -> int:
    """Return a TCP port nothing listens on right now.

    Returns:
        int: The port number.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    """Start Redis in a container and yield its URL.

    Yields:
        str: ``redis://127.0.0.1:<port>/0``.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    port = free_port()
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-p",
            f"{port}:6379",
            "redis:7-alpine",
        ],
        check=True,
        capture_output=True,
    )
    try:
        for _ in range(60):
            ping = subprocess.run(
                ["docker", "exec", CONTAINER, "redis-cli", "ping"],
                capture_output=True,
                text=True,
            )
            if ping.stdout.strip() == "PONG":
                break
            time.sleep(0.5)
        else:
            pytest.fail("redis never answered PONG")
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


def run_once(
    tmp_path: Path,
    redis_url: str,
    *,
    drop_labels: bool,
    task_name: str = "probe:flaky",
) -> tuple[int, str, str]:
    """Start a worker, send one call by name from another process, count runs.

    Args:
        tmp_path (Path): Scratch directory for the worker module and log.
        redis_url (str): The broker URL.
        drop_labels (bool): Whether the worker pops the labels middleware.
        task_name (str): The name the sender enqueues.

    Returns:
        tuple[int, str, str]: Attempts the worker made, what the sender
        printed (the ``probe_`` modules it had imported) and the worker's
        log output.
    """
    worker_dir = tmp_path / "worker"
    sender_dir = tmp_path / "sender"
    worker_dir.mkdir()
    sender_dir.mkdir()
    (worker_dir / "probe_tasks.py").write_text(textwrap.dedent(WORKER_MODULE))
    (sender_dir / "send.py").write_text(textwrap.dedent(SENDER))
    log = tmp_path / "attempts.log"
    env = {
        **os.environ,
        "PROBE_REDIS_URL": redis_url,
        "PROBE_LOG": str(log),
        "PROBE_DROP_LABELS": "1" if drop_labels else "0",
        "PROBE_TASK_NAME": task_name,
        "PYTHONPATH": str(worker_dir),
    }
    taskiq = Path(sys.executable).with_name("taskiq")
    worker_output = tmp_path / "worker.log"
    with worker_output.open("w", encoding="utf-8") as sink:
        worker = subprocess.Popen(
            [str(taskiq), "worker", "probe_tasks:broker", "--workers", "1"],
            cwd=worker_dir,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    try:
        time.sleep(4)
        sender_env = {key: value for key, value in env.items() if key != "PYTHONPATH"}
        sent = subprocess.run(
            [sys.executable, "send.py"],
            cwd=sender_dir,
            env=sender_env,
            capture_output=True,
            text=True,
            check=True,
        )
        deadline = time.monotonic() + 20
        expected = 1 if drop_labels else 2
        while time.monotonic() < deadline:
            if log.exists() and log.read_text().count("attempt") >= expected:
                break
            time.sleep(0.2)
        time.sleep(2)
    finally:
        worker.terminate()
        worker.wait(timeout=20)
    attempts = log.read_text().count("attempt") if log.exists() else 0
    return attempts, sent.stdout.strip(), worker_output.read_text()


class TestAcrossProcesses:
    def test_the_worker_retries_a_call_sent_by_name(
        self, tmp_path: Path, redis_url: str
    ) -> None:
        attempts, imported, _ = run_once(tmp_path, redis_url, drop_labels=False)
        assert imported == "[]"
        assert attempts == 2

    def test_without_the_merge_the_retry_is_lost(
        self, tmp_path: Path, redis_url: str
    ) -> None:
        attempts, _, _ = run_once(tmp_path, redis_url, drop_labels=True)
        assert attempts == 1

    def test_an_unknown_name_is_logged_and_dropped_by_the_worker(
        self, tmp_path: Path, redis_url: str
    ) -> None:
        attempts, _, output = run_once(
            tmp_path, redis_url, drop_labels=False, task_name="probe:missing"
        )
        assert attempts == 0
        assert 'task "probe:missing" is not found' in output
