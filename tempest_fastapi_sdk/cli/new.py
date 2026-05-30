"""``tempest new`` — render the bundled project skeleton on disk."""

from __future__ import annotations

import importlib.resources
import keyword
import re
from pathlib import Path
from typing import Any

import typer

_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
"""Slug whitelist accepted by ``tempest new``.

Matches PEP 8 package/module naming: lowercase, underscores, no leading
digit, no hyphens. Rejected names raise :class:`typer.Exit` so the
user can rename before the scaffolder writes any files.
"""

_TEMPLATE_SUFFIX = ".tmpl"
"""Suffix stripped from every bundled template file on render."""


def _validate_name(name: str) -> None:
    """Reject project names that are not safe Python identifiers.

    Args:
        name (str): The candidate slug.

    Raises:
        typer.Exit: When ``name`` is empty, not a valid identifier,
            or collides with a Python keyword.
    """
    if not _VALID_NAME.fullmatch(name):
        typer.echo(
            "error: project name must match ^[a-z][a-z0-9_]*$ "
            "(lowercase, underscores, no leading digit).",
            err=True,
        )
        raise typer.Exit(2)
    if keyword.iskeyword(name):
        typer.echo(f"error: '{name}' is a Python keyword.", err=True)
        raise typer.Exit(2)


def _templates_root() -> Path:
    """Return the directory holding the bundled templates.

    Uses :mod:`importlib.resources` so the lookup works from both an
    installed wheel and the editable source tree.

    Returns:
        Path: An absolute path to the templates root.
    """
    return Path(
        str(importlib.resources.files("tempest_fastapi_sdk.cli") / "_templates"),
    )


def _render(content: str, context: dict[str, str]) -> str:
    """Replace ``__KEY__`` placeholders with ``context[key]`` values.

    Args:
        content (str): The raw template content.
        context (dict[str, str]): Key/value pairs to substitute.

    Returns:
        str: The rendered content.
    """
    rendered = content
    for key, value in context.items():
        rendered = rendered.replace(f"__{key}__", value)
    return rendered


def _resolve_target(name: str, path: str | None) -> Path:
    """Return the absolute target directory for the scaffold.

    Args:
        name (str): The project name.
        path (str | None): Optional parent directory. When ``None`` the
            current working directory is used.

    Returns:
        Path: The absolute target directory.
    """
    parent = Path(path).expanduser().resolve() if path else Path.cwd()
    return parent / name


def _build_sdk_dep(extras: str) -> str:
    """Render the ``tempest-fastapi-sdk`` PEP 508 requirement.

    Args:
        extras (str): Comma-separated extras to attach. Empty string
            yields a bare ``tempest-fastapi-sdk>=<floor>`` requirement.

    Returns:
        str: A pinned requirement suitable for ``pyproject.toml``.
    """
    from tempest_fastapi_sdk import __version__

    cleaned = ",".join(part.strip() for part in extras.split(",") if part.strip())
    extras_fragment = f"[{cleaned}]" if cleaned else ""
    return f"tempest-fastapi-sdk{extras_fragment}>={__version__}"


def _iter_templates(root: Path) -> list[Path]:
    """Walk the templates tree and collect every ``*.tmpl`` file.

    Args:
        root (Path): The templates root.

    Returns:
        list[Path]: Sorted absolute paths to every bundled template.
    """
    return sorted(p for p in root.rglob(f"*{_TEMPLATE_SUFFIX}") if p.is_file())


def _target_path(template: Path, root: Path, target: Path) -> Path:
    """Translate a template path into its on-disk destination.

    The ``.tmpl`` suffix is stripped and the special ``gitignore``
    filename is renamed to ``.gitignore`` (templates can't be named
    ``.gitignore.tmpl`` without confusing IDEs).

    Args:
        template (Path): The source template path.
        root (Path): The templates root.
        target (Path): The destination project directory.

    Returns:
        Path: The absolute destination path.
    """
    relative = template.relative_to(root)
    parts = list(relative.parts)
    parts[-1] = parts[-1][: -len(_TEMPLATE_SUFFIX)]
    if parts[-1] == "gitignore":
        parts[-1] = ".gitignore"
    if parts[-1] == "env.example":
        parts[-1] = ".env.example"
    return target.joinpath(*parts)


def scaffold(
    *,
    name: str,
    path: str | None,
    bind_host: str,
    bind_port: int,
    extras: str,
    force: bool,
) -> None:
    """Render the project skeleton on disk.

    Args:
        name (str): The project / package name.
        path (str | None): Parent directory; defaults to the current
            working directory.
        bind_host (str): ``HOST`` value to inject into ``.env.example``.
        bind_port (int): ``PORT`` value to inject into ``.env.example``.
        extras (str): Comma-separated SDK extras to pin.
        force (bool): Overwrite the target if it already exists.

    Raises:
        typer.Exit: On invalid input or target collision without
            ``--force``.
    """
    _validate_name(name)

    target = _resolve_target(name, path)
    if target.exists() and not force:
        typer.echo(
            f"error: target {target} already exists. Pass --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)
    target.mkdir(parents=True, exist_ok=True)

    context: dict[str, str] = {
        "PROJECT_NAME": name,
        "HOST": bind_host,
        "PORT": str(bind_port),
        "SDK_DEP": _build_sdk_dep(extras),
        "SDK_EXTRAS": extras,
    }

    root = _templates_root()
    written: list[Path] = []
    for template in _iter_templates(root):
        destination = _target_path(template, root, target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            _render(template.read_text(encoding="utf-8"), context),
            encoding="utf-8",
        )
        written.append(destination)

    cwd = Path.cwd()
    display = target.relative_to(cwd) if target.is_relative_to(cwd) else target
    typer.echo(f"Scaffolded {len(written)} files in {target}", err=False)
    typer.echo("Next steps:", err=False)
    typer.echo(f"  cd {display}", err=False)
    typer.echo("  uv sync", err=False)
    typer.echo("  cp .env.example .env", err=False)
    typer.echo("  uv run python main.py", err=False)


__all__: list[str] = [
    "scaffold",
]


def __getattr__(item: str) -> Any:  # pragma: no cover - typer test helper
    """Expose private helpers for the CLI test suite without enlarging ``__all__``."""
    if item in {
        "_build_sdk_dep",
        "_iter_templates",
        "_render",
        "_resolve_target",
        "_target_path",
        "_templates_root",
        "_validate_name",
    }:
        return globals()[item]
    raise AttributeError(item)
