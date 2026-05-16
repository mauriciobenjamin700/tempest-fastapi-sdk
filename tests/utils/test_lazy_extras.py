"""Regression tests for lazy optional-extra imports.

Verifies the bug reported on 2026-05-16: importing the top-level
package must NOT require every optional extra to be installed.
Only attempting to *instantiate* a helper whose extra is missing
should raise ``ImportError``.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest


def _drop_module(name: str) -> None:
    """Remove a module (and its submodules) from ``sys.modules``."""
    for key in [name, *[m for m in sys.modules if m.startswith(f"{name}.")]]:
        sys.modules.pop(key, None)


@pytest.fixture
def hide_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Simulate an uninstalled optional dependency.

    Yields a callable that hides a top-level package from ``import``.
    The original module (if any) is restored when the test finishes.
    """
    saved: dict[str, Any] = {}

    def _hide(name: str) -> None:
        saved.update(
            {
                k: v
                for k, v in sys.modules.items()
                if k == name or k.startswith(f"{name}.")
            },
        )
        _drop_module(name)
        monkeypatch.setattr(
            sys,
            "meta_path",
            [_BlockFinder(name), *sys.meta_path],
        )

    yield _hide

    for key, mod in saved.items():
        sys.modules[key] = mod


class _BlockFinder:
    """Meta-path finder that raises ImportError for one module name."""

    def __init__(self, blocked: str) -> None:
        self.blocked = blocked

    def find_spec(self, name: str, *_args: Any, **_kwargs: Any) -> None:
        if name == self.blocked or name.startswith(f"{self.blocked}."):
            raise ImportError(f"Pretending {name!r} is not installed.")
        return None


@pytest.mark.parametrize(
    "missing_module",
    ["aiosmtplib", "jwt", "bcrypt", "aiofiles", "psutil"],
)
def test_top_level_import_survives_missing_extra(
    missing_module: str,
    hide_module: Any,
) -> None:
    """Top-level package imports cleanly even when an extra is gone."""
    hide_module(missing_module)
    _drop_module("tempest_fastapi_sdk")
    pkg = importlib.import_module("tempest_fastapi_sdk")
    assert pkg.__version__


def test_email_utils_raises_clear_error_without_extra(hide_module: Any) -> None:
    """EmailUtils() raises ImportError with the [email] hint."""
    hide_module("aiosmtplib")
    _drop_module("tempest_fastapi_sdk")
    pkg = importlib.import_module("tempest_fastapi_sdk")
    with pytest.raises(ImportError, match=r"\[email\] extra"):
        pkg.EmailUtils(host="x", port=25, from_addr="x@y.z")


def test_jwt_utils_raises_clear_error_without_extra(hide_module: Any) -> None:
    """JWTUtils() raises ImportError with the [auth] hint."""
    hide_module("jwt")
    _drop_module("tempest_fastapi_sdk")
    pkg = importlib.import_module("tempest_fastapi_sdk")
    with pytest.raises(ImportError, match=r"\[auth\] extra"):
        pkg.JWTUtils(secret="x" * 32)


def test_password_utils_raises_clear_error_without_extra(hide_module: Any) -> None:
    """PasswordUtils() raises ImportError with the [auth] hint."""
    hide_module("bcrypt")
    _drop_module("tempest_fastapi_sdk")
    pkg = importlib.import_module("tempest_fastapi_sdk")
    with pytest.raises(ImportError, match=r"\[auth\] extra"):
        pkg.PasswordUtils()


def test_upload_utils_raises_clear_error_without_extra(
    hide_module: Any,
    tmp_path: Any,
) -> None:
    """UploadUtils() raises ImportError with the [upload] hint."""
    hide_module("aiofiles")
    _drop_module("tempest_fastapi_sdk")
    pkg = importlib.import_module("tempest_fastapi_sdk")
    with pytest.raises(ImportError, match=r"\[upload\] extra"):
        pkg.UploadUtils(upload_dir=tmp_path)


def test_metrics_utils_raises_clear_error_without_extra(hide_module: Any) -> None:
    """MetricsUtils methods raise ImportError with the [metrics] hint."""
    hide_module("psutil")
    _drop_module("tempest_fastapi_sdk")
    pkg = importlib.import_module("tempest_fastapi_sdk")
    with pytest.raises(ImportError, match=r"\[metrics\] extra"):
        pkg.MetricsUtils.cpu(interval=0)


def test_unrelated_helpers_still_work_without_email_extra(hide_module: Any) -> None:
    """PasswordUtils + JWTUtils still construct when only [email] is missing."""
    hide_module("aiosmtplib")
    _drop_module("tempest_fastapi_sdk")
    pkg = importlib.import_module("tempest_fastapi_sdk")
    pkg.PasswordUtils()
    pkg.JWTUtils(secret="x" * 32)
    assert pkg.is_valid_cpf("000.000.000-00") is False
