"""The suite's ``pytest-timeout`` must abort a hang whose first abort is swallowed.

``timeout_method = "signal"`` arms a one-shot ``SIGALRM`` and aborts the test
by raising ``Failed`` on the main thread. That is one ``BaseException``, and a
hang can eat it: raised inside a finalizer it becomes an "Exception ignored"
line, raised inside an ``asyncio`` callback ``Handle._run`` logs it, raised
inside a ``with`` whose ``__exit__`` waits on the same stuck resource the
cleanup hangs again. Nothing re-arms the alarm, so the test is unbounded
from there on — the gate of #336 sat for more than 40 minutes after the
handler had fired inside the ``scaffolded`` teardown (#337).

``timeout_method = "thread"`` dumps every thread's stack from a timer thread
and calls ``os._exit(1)``, which nothing inside the hung test can swallow.
These tests run the repository's own pytest configuration against a teardown
whose finalizers never return and assert both halves: the configured method
ends the process, and the signal method it replaced does not.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

HUNG_TEARDOWN = '''
import sys
import threading
import time
import types

import pytest

NEVER = threading.Event()


class WaitsOnClose:
    """A finalizer that waits for a worker that never answers."""

    def __del__(self) -> None:
        """Poll for an event nobody sets."""
        while not NEVER.is_set():
            time.sleep(0.05)


@pytest.fixture
def modules() -> object:
    """Hold two finalizers in fake modules, dropped as ``_forget_src_modules`` does."""
    for index in range(2):
        module = types.ModuleType(f"hung_fake_{index}")
        module.resource = WaitsOnClose()
        sys.modules[module.__name__] = module
    yield
    for name in [name for name in list(sys.modules) if name.startswith("hung_fake_")]:
        del sys.modules[name]


def test_body_passes(modules: object) -> None:
    """The body passes; the first finalizer eats the abort, the second hangs."""
'''
"""A test whose teardown swallows one abort and then hangs for good."""

SWALLOWED_MARKER = "Failed: Timeout"
"""What the default unraisable hook prints when a finalizer eats the abort."""


def _run_hung_teardown(
    tmp_path: Path,
    *extra: str,
) -> subprocess.Popen[str]:
    """Start pytest, with this repository's configuration, on the hung teardown.

    Args:
        tmp_path (Path): Where the hung test module is written.
        *extra (str): Extra command-line options for the child pytest.

    Returns:
        subprocess.Popen[str]: The running child, stdout and stderr merged.
    """
    module = tmp_path / "test_hung_teardown.py"
    module.write_text(HUNG_TEARDOWN, encoding="utf-8")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            "--rootdir",
            str(tmp_path),
            "--no-cov",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:unraisableexception",
            "-s",
            "-q",
            "-o",
            "timeout=1",
            *extra,
            str(module),
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_configured_method_ends_the_hung_process(tmp_path: Path) -> None:
    """The repository's ``timeout_method`` kills the process and names the frame."""
    child = _run_hung_teardown(tmp_path)
    try:
        output, _ = child.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        pytest.fail("the configured timeout method did not end a hung teardown")

    assert child.returncode == 1, output
    assert "Timeout" in output
    assert "in __del__" in output, output


def test_signal_method_is_swallowed_and_the_hang_continues(tmp_path: Path) -> None:
    """The method this repository replaced fires once, is eaten, and hangs on.

    A timer kills the child at the deadline, so a child that hangs without
    ever printing the marker ends ``readline`` instead of this test.
    """
    child = _run_hung_teardown(tmp_path, "-o", "timeout_method=signal")
    assert child.stdout is not None
    killer = threading.Timer(120, child.kill)
    killer.start()
    seen: list[str] = []
    try:
        for line in child.stdout:
            seen.append(line)
            if SWALLOWED_MARKER in line:
                break
        assert any(SWALLOWED_MARKER in line for line in seen), "".join(seen)
        time.sleep(2)
        assert child.poll() is None, "".join(seen)
    finally:
        killer.cancel()
        child.kill()
        child.communicate()
