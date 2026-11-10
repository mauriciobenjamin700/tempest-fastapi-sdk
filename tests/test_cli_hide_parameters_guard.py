"""Every database manager the CLI opens hides bound parameters (#718).

``tempest user create`` shipped building ``AsyncDatabaseManager(url)``
directly. Its engine rendered bound parameters into every ``DBAPIError``
message, and the CLI echoes that message, so a failed insert printed the
bcrypt hash of the password just typed. The fix routes every CLI manager
through :func:`tempest_fastapi_sdk.cli._database.cli_database_manager`,
which passes ``hide_parameters=True``; this guard fails on a direct
construction anywhere else under ``tempest_fastapi_sdk/cli/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tempest_fastapi_sdk.cli._database import cli_database_manager

CLI_ROOT: Path = Path(__file__).resolve().parents[1] / "tempest_fastapi_sdk" / "cli"
"""The package directory the guard scans."""

FACTORY_MODULE: Path = CLI_ROOT / "_database.py"
"""The one module allowed to construct the manager."""

SHIPPED_FORM: str = """
async def _create_user(database_url: str) -> None:
    from tempest_fastapi_sdk import AsyncDatabaseManager

    db = AsyncDatabaseManager(database_url)
    await db.connect()
"""
"""The shape ``cli/user.py`` shipped with before #718."""


def direct_constructions(source: str) -> list[int]:
    """Return the lines of ``source`` that call ``AsyncDatabaseManager``.

    Both the bare name and an attribute access (``tfs.AsyncDatabaseManager``)
    count.

    Args:
        source (str): Python source to scan.

    Returns:
        list[int]: One line number per call.
    """
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name == "AsyncDatabaseManager":
            lines.append(node.lineno)
    return lines


def test_no_cli_module_builds_the_manager_directly() -> None:
    offenders: list[str] = []
    for path in sorted(CLI_ROOT.rglob("*.py")):
        if path == FACTORY_MODULE:
            continue
        for line in direct_constructions(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(CLI_ROOT)}:{line}")
    assert not offenders, (
        "build CLI database managers with cli_database_manager(), which "
        f"hides bound parameters: {offenders}"
    )


def test_guard_fires_on_the_shipped_form() -> None:
    assert direct_constructions(SHIPPED_FORM) == [5]


def test_factory_hides_parameters() -> None:
    manager = cli_database_manager("sqlite+aiosqlite:///:memory:")
    assert manager._engine_kwargs == {"hide_parameters": True}
