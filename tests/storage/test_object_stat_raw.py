"""``ObjectStat.raw`` is ``None`` off MinIO, and its type says so (#836).

The local disk backend and the in-memory fake have no ``minio`` ``Object``
to hand back, so they build ``ObjectStat(raw=None)``. Until #836 the field
was annotated ``_MinioObject`` and both constructors silenced the lie with
``type: ignore[arg-type]``: a consumer's checker accepted
``stat.raw.version_id`` and the call raised ``AttributeError`` at runtime
against either backend. These tests pin the runtime value and make mypy
prove the annotation now forces the ``None`` check.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import pytest

from tempest_fastapi_sdk.storage import LocalObjectStorageClient
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient

REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
"""Repository root, used to key the mypy cache per checkout."""

UNCHECKED_RAW_SNIPPET: str = '''\
"""A consumer reading ``ObjectStat.raw`` without a ``None`` check."""

from tempest_fastapi_sdk.storage import ObjectStat


def version_of(stat: ObjectStat) -> str | None:
    """Read the version id straight off the raw object."""
    return stat.raw.version_id
'''
"""Consumer code that is only safe on the MinIO backend."""

CHECKED_RAW_SNIPPET: str = '''\
"""A consumer reading ``ObjectStat.raw`` after narrowing it."""

from tempest_fastapi_sdk.storage import ObjectStat


def version_of(stat: ObjectStat) -> str | None:
    """Read the version id when the backend carries a raw object."""
    if stat.raw is None:
        return None
    return stat.raw.version_id
'''
"""Consumer code that is safe on every backend."""


def run_mypy(module: Path) -> list[str]:
    """Type-check ``module`` with ``mypy --strict`` and return the errors.

    Args:
        module (Path): The consumer module to check.

    Returns:
        list[str]: Every ``error:`` line mypy printed.
    """
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is a dev-group dependency")
    digest: str = hashlib.sha256(str(REPO_ROOT).encode("utf-8")).hexdigest()[:12]
    cache: Path = Path(tempfile.gettempdir()) / f"tempest-mypy-{digest}-object-stat"
    stdout, _stderr, _status = mypy_api.run(
        [
            str(module),
            "--strict",
            "--no-error-summary",
            "--hide-error-context",
            "--no-color-output",
            "--cache-dir",
            str(cache),
            "--python-executable",
            sys.executable,
        ]
    )
    return [line for line in stdout.splitlines() if ": error:" in line]


class TestRawIsNoneOffMinio:
    async def test_local_client_stat_has_no_raw(self, tmp_path: Path) -> None:
        client = LocalObjectStorageClient(
            tmp_path,
            public_url="https://api.example.com/",
            signing_secret="test-local-storage-secret",
            default_bucket="media",
        )
        await client.put_object("a.txt", b"hello")

        stat = await client.stat_object("a.txt")

        assert stat.raw is None

    async def test_fake_client_stat_has_no_raw(self) -> None:
        client = FakeObjectStorageClient()
        await client.put_object("a.txt", b"hello")

        stat = await client.stat_object("a.txt")

        assert stat.raw is None


class TestRawIsOptionalToTheChecker:
    def test_unchecked_raw_access_is_a_type_error(self, tmp_path: Path) -> None:
        """``stat.raw.version_id`` without a ``None`` check fails mypy.

        Before #836 mypy accepted this line, and it raised
        ``AttributeError`` against the local backend and the fake.

        Args:
            tmp_path (Path): pytest's per-test temporary directory.
        """
        module = tmp_path / "consumer_unchecked.py"
        module.write_text(UNCHECKED_RAW_SNIPPET, encoding="utf-8")

        errors = run_mypy(module)

        assert len(errors) == 1, "\n".join(errors)
        assert "[union-attr]" in errors[0]
        assert "Object | None" in errors[0]
        assert "has no attribute" in errors[0]

    def test_narrowed_raw_access_type_checks(self, tmp_path: Path) -> None:
        """Narrowing ``stat.raw`` against ``None`` is what the type asks for.

        Before #836 this repository's mypy config (``warn_unreachable``)
        reported the ``return None`` branch as unreachable: the safe code was
        the one the checker rejected.

        Args:
            tmp_path (Path): pytest's per-test temporary directory.
        """
        module = tmp_path / "consumer_checked.py"
        module.write_text(CHECKED_RAW_SNIPPET, encoding="utf-8")

        errors = run_mypy(module)

        assert not errors, "\n".join(errors)
