"""The Redis stores' clock against real servers, not ``fakeredis``.

``test_quota.py`` pins the ``fakeredis`` clock and shifts the client's,
which proves the scripts read ``TIME`` — as ``fakeredis`` implements
it. Two things it cannot answer need a real server: that ``TIME``
followed by a write is accepted by every Redis the stores claim to run
on (Redis 4 refuses it without switching to effects replication first),
and that a client whose wall clock is off by an hour really cannot
refill a bucket or prune a window on a server that keeps its own time.

Each image runs in a container on ``make test-docker``. ``redis-py``
8.1.0 opens with ``HELLO 3``, which Redis 4 and 5 reject as an unknown
command, so the clients here speak RESP2.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from tempest_fastapi_sdk import RateLimitRule, RedisQuotaStore, RedisRateLimitStore

IMAGES: list[tuple[str, int]] = [("redis:4-alpine", 56394), ("redis:7-alpine", 56397)]
"""``(image, host port)``: the server that needs the guard, and a current one."""

SKEWS: list[tuple[float, float]] = [(0.0, 3600.0), (-3600.0, 0.0)]
"""``(drain_offset, probe_offset)`` in seconds of client wall clock."""

_WALL_CLOCK: Callable[[], float] = time.time
"""The unpatched ``time.time``, captured before any test shifts it."""


def _skew_client_clock(monkeypatch: pytest.MonkeyPatch, offset: float) -> None:
    """Shift this process's ``time.time()`` by ``offset`` seconds.

    The server keeps its own clock, so this moves only what a store
    would read if it took the time from the caller.

    Args:
        monkeypatch (pytest.MonkeyPatch): Restores ``time.time``.
        offset (float): Seconds to add to the real wall clock.
    """
    monkeypatch.setattr(time, "time", lambda: _WALL_CLOCK() + offset)


@pytest.fixture(scope="module", params=IMAGES, ids=[image for image, _ in IMAGES])
def redis_port(request: pytest.FixtureRequest) -> Iterator[int]:
    """Start a Redis container and yield its host port.

    Args:
        request (pytest.FixtureRequest): Carries ``(image, port)``.

    Yields:
        int: The port the container listens on, on ``127.0.0.1``.
    """
    image, port = request.param
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    name = f"tempest-quota-clock-{port}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", f"{port}:6379", image],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {image}: {started.stderr.strip()}")
    try:
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", name, "redis-cli", "ping"],
                capture_output=True,
                text=True,
            )
            if ready.stdout.strip() == "PONG":
                break
            time.sleep(1)
        else:
            pytest.skip(f"{image} never became ready")
        yield port
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest_asyncio.fixture
async def clients(redis_port: int) -> AsyncIterator[tuple[Redis, Redis]]:
    """Yield two independent clients on an empty database.

    Args:
        redis_port (int): Port from the container fixture.

    Yields:
        tuple[Redis, Redis]: Two connections to the same server.
    """
    first = Redis(host="127.0.0.1", port=redis_port, protocol=2)
    second = Redis(host="127.0.0.1", port=redis_port, protocol=2)
    await first.flushdb()
    yield first, second
    await first.aclose()
    await second.aclose()


@pytest.mark.docker
@pytest.mark.asyncio
@pytest.mark.parametrize(("drain", "probe"), SKEWS, ids=["ahead", "behind"])
async def test_quota_bucket_ignores_client_clock_skew(
    clients: tuple[Redis, Redis],
    monkeypatch: pytest.MonkeyPatch,
    drain: float,
    probe: float,
) -> None:
    """A client an hour off gets no refill from a real server.

    ``fail_open=False``, so a server that refuses the script fails the
    case instead of reading as an allowed request.
    """
    draining = RedisQuotaStore(clients[0], fail_open=False)
    probing = RedisQuotaStore(clients[1], fail_open=False)
    rules = [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=3)]
    _skew_client_clock(monkeypatch, drain)
    drained = [await draining.consume("k", rules) for _ in range(3)]
    assert all(result.allowed for result in drained)
    _skew_client_clock(monkeypatch, probe)
    denied = await probing.consume("k", rules)
    assert not denied.allowed
    assert denied.retry_after >= 3599


@pytest.mark.docker
@pytest.mark.asyncio
@pytest.mark.parametrize(("drain", "probe"), SKEWS, ids=["ahead", "behind"])
async def test_sliding_window_ignores_client_clock_skew(
    clients: tuple[Redis, Redis],
    monkeypatch: pytest.MonkeyPatch,
    drain: float,
    probe: float,
) -> None:
    """A client an hour off prunes nothing from a real server's window."""
    draining = RedisRateLimitStore(clients[0], fail_open=False)
    probing = RedisRateLimitStore(clients[1], fail_open=False)
    _skew_client_clock(monkeypatch, drain)
    hits = [await draining.hit("k", 3, 3600.0) for _ in range(3)]
    assert all(hit.allowed for hit in hits)
    _skew_client_clock(monkeypatch, probe)
    denied = await probing.hit("k", 3, 3600.0)
    assert not denied.allowed
    assert denied.retry_after >= 3599
