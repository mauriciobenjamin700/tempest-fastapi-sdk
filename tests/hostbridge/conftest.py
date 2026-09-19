"""Fixtures shared by the host bridge suite."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig


@pytest.fixture
def config(tmp_path: Path) -> HostBridgeConfig:
    """A config allowing exactly one writable directory.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        HostBridgeConfig: Config whose only allowed base is ``tmp_path``.
    """
    return HostBridgeConfig(allowed_base_paths=(str(tmp_path),))


@pytest.fixture
def bridge(config: HostBridgeConfig) -> HostBridge:
    """A bridge over the temp-directory config.

    Args:
        config (HostBridgeConfig): The confined config.

    Returns:
        HostBridge: The bridge under test.
    """
    return HostBridge(config)


def fake_process(
    return_code: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    *,
    times_out: bool = False,
) -> MagicMock:
    """Build a stand-in for an ``asyncio`` subprocess.

    Args:
        return_code (int): Exit status the fake reports.
        stdout (bytes): Bytes ``communicate`` yields as stdout.
        stderr (bytes): Bytes ``communicate`` yields as stderr.
        times_out (bool): Make ``communicate`` raise ``TimeoutError``, which
            is what ``asyncio.wait_for`` raises on expiry.

    Returns:
        MagicMock: The fake process, with ``kill`` and ``wait`` recorded.
    """
    process = MagicMock()
    process.returncode = return_code
    process.communicate = (
        AsyncMock(side_effect=asyncio.TimeoutError)
        if times_out
        else AsyncMock(return_value=(stdout, stderr))
    )
    process.kill = MagicMock()
    process.wait = AsyncMock()
    return process
