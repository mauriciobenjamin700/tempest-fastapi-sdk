"""``tempest new`` — render the bundled project skeleton on disk."""

from __future__ import annotations

import importlib.resources
import keyword
import re
from pathlib import Path
from typing import Any

import typer

_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
"""Slug whitelist accepted by ``tempest new <name>``.

Matches PEP 8 package/module naming: lowercase, underscores, no leading
digit, no hyphens. Rejected names raise :class:`typer.Exit` so the
user can rename before the scaffolder writes any files.
"""

_VALID_DIST_NAME = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
"""PEP 503 normalized distribution-name whitelist for ``tempest new .``.

The cwd basename only feeds ``__PROJECT_NAME__`` (pyproject ``name``,
README header, app title, health-check service field) — none of which
need to be a Python identifier. Hyphens are common on existing repos
(``todolist-api``) and are accepted here even though the strict
``<name>`` form still rejects them.
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


def _resolve_name_and_target(
    name: str,
    path: str | None,
) -> tuple[str, Path]:
    """Resolve the (project name, target directory) pair from CLI args.

    Two shapes are accepted:

    * ``tempest new <name>`` (with optional ``--path``) — name is the
      slug used as the package name AND the new subdirectory inside
      ``path`` (or cwd). Validated against the Python-identifier rules.
    * ``tempest new .`` — scaffold flatly in the current directory.
      The package name is derived from ``Path.cwd().name`` and must
      itself be a valid Python identifier; ``--path`` is rejected
      because the target is unambiguous.

    Args:
        name (str): The positional argument (``<name>`` or ``"."``).
        path (str | None): Optional parent directory.

    Returns:
        tuple[str, Path]: The resolved project name and absolute
        target directory.

    Raises:
        typer.Exit: On invalid input.
    """
    if name == ".":
        if path is not None:
            typer.echo(
                "error: --path is not compatible with the '.' shorthand; "
                "the target is the current working directory.",
                err=True,
            )
            raise typer.Exit(2)
        target = Path.cwd().resolve()
        derived = target.name.lower()
        if not _VALID_DIST_NAME.fullmatch(derived):
            typer.echo(
                f"error: cwd basename {target.name!r} is not a valid "
                f"PEP 503 distribution name (lowercase letters, digits, "
                f"and `.`/`_`/`-` separators between alphanumerics). "
                f"Rename the directory or pass an explicit project name.",
                err=True,
            )
            raise typer.Exit(2)
        return derived, target

    _validate_name(name)
    parent = Path(path).expanduser().resolve() if path else Path.cwd()
    return name, parent / name


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

    Kept as a thin shim around :func:`_resolve_name_and_target` so
    callers that already validated the name keep working.

    Args:
        name (str): The project name (must already be valid).
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

    The ``.tmpl`` suffix is stripped and the special ``gitignore`` /
    ``dockerignore`` / ``env.example`` filenames are renamed to their
    dotted form (templates can't be named ``.gitignore.tmpl`` without
    confusing IDEs).

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
    if parts[-1] == "dockerignore":
        parts[-1] = ".dockerignore"
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
    resolved_name, target = _resolve_name_and_target(name, path)

    is_cwd_scaffold = target == Path.cwd().resolve()
    if target.exists() and not is_cwd_scaffold and not force:
        typer.echo(
            f"error: target {target} already exists. Pass --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)
    target.mkdir(parents=True, exist_ok=True)

    context: dict[str, str] = {
        "PROJECT_NAME": resolved_name,
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

    from tempest_fastapi_sdk.cli.docker_compose import (
        env_block_for,
    )
    from tempest_fastapi_sdk.cli.docker_compose import (
        generate as generate_compose,
    )

    compose_path = target / "docker-compose.yaml"
    compose_path.write_text(generate_compose(resolved_name, extras), encoding="utf-8")
    written.append(compose_path)

    env_example = target / ".env.example"
    if env_example.exists():
        env_addendum = env_block_for(extras)
        if env_addendum:
            env_example.write_text(
                env_example.read_text(encoding="utf-8") + env_addendum,
                encoding="utf-8",
            )

    from tempest_fastapi_sdk.cli.docker_compose import _parse_extras
    from tempest_fastapi_sdk.cli.src_layers import add_src_layers

    # Scaffold the optional source layers (queue / tasks) triggered by the
    # chosen extras so a fresh `--extras queue` project ships src/queue/.
    layer_written, _ = add_src_layers(target, _parse_extras(extras), force=True)
    written.extend(layer_written)

    cwd = Path.cwd()
    display = target.relative_to(cwd) if target.is_relative_to(cwd) else target
    typer.echo(f"Scaffolded {len(written)} files in {target}", err=False)
    typer.echo("Next steps:", err=False)
    typer.echo(f"  cd {display}", err=False)
    typer.echo("  uv sync", err=False)
    typer.echo("  cp .env.example .env", err=False)
    typer.echo("  docker compose up -d", err=False)
    typer.echo("  uv run python main.py", err=False)
    typer.echo(
        f"Containerize the app:  docker build -t {resolved_name} . "
        f"(Dockerfile + .dockerignore included)",
        err=False,
    )


__all__: list[str] = [
    "scaffold",
]


def __getattr__(item: str) -> Any:  # pragma: no cover - typer test helper
    """Expose private helpers for the CLI test suite without enlarging ``__all__``."""
    if item in {
        "_build_sdk_dep",
        "_iter_templates",
        "_render",
        "_resolve_name_and_target",
        "_resolve_target",
        "_target_path",
        "_templates_root",
        "_validate_name",
    }:
        return globals()[item]
    raise AttributeError(item)
