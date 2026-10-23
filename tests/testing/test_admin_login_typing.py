"""The admin login helpers accept every test client under a strict checker (#610).

Starlette's ``TestClient`` subclasses ``httpx2.Client`` when ``httpx2`` is
installed, so a helper typed ``httpx.Client`` worked at runtime and was
refused by ``mypy --strict`` in the consumer's suite. This runs mypy over
the calls a consumer writes, the way the consumer's checker does.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from mypy import api as mypy_api

CONSUMER: str = textwrap.dedent(
    """
    import httpx
    import httpx2
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tempest_fastapi_sdk.testing import admin_login, admin_login_sync

    app = FastAPI()


    def with_test_client() -> int:
        with TestClient(app) as client:
            return admin_login_sync(client, "a@x.io", "pw").status_code


    def with_httpx_client() -> int:
        with httpx.Client(base_url="http://t") as client:
            response: httpx.Response = admin_login_sync(client, "a@x.io", "pw")
            return response.status_code


    async def with_httpx2_async_client() -> int:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport) as client:
            response: httpx2.Response = await admin_login(client, "a@x.io", "pw")
            return response.status_code


    async def with_httpx_async_client() -> int:
        async with httpx.AsyncClient(base_url="http://t") as client:
            response: httpx.Response = await admin_login(client, "a@x.io", "pw")
            return response.status_code
    """
)
"""What a consumer writes with each client the helpers document."""


def _strict_errors(source: str, tmp_path: Path) -> list[str]:
    """Run ``mypy --strict`` over ``source``, as a consumer's checker would.

    Args:
        source (str): The module to check.
        tmp_path (Path): Where to write it.

    Returns:
        list[str]: The ``error:`` lines.
    """
    module = tmp_path / "consumer.py"
    module.write_text(source, encoding="utf-8")
    stdout, _stderr, _status = mypy_api.run(
        [
            "--strict",
            "--no-error-summary",
            "--no-color-output",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(module),
        ]
    )
    return [line for line in stdout.splitlines() if ": error:" in line]


def test_every_documented_client_type_checks(tmp_path: Path) -> None:
    assert _strict_errors(CONSUMER, tmp_path) == []


def test_a_non_client_is_still_refused(tmp_path: Path) -> None:
    """The protocol is not ``Any`` in disguise."""
    source = CONSUMER + textwrap.dedent(
        """

        def with_anything() -> None:
            admin_login_sync(object(), "a@x.io", "pw")
        """
    )
    errors = _strict_errors(source, tmp_path)
    assert len(errors) == 1
    assert "admin_login_sync" in errors[0]
