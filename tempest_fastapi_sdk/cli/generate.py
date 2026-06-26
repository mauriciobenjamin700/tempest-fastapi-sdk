"""``tempest generate`` — regenerate scaffolded artifacts in place.

Today the only target is ``--docker`` (regenerates
``docker-compose.yaml`` + the matching ``.env.example`` block from
the project's installed extras). New targets land here as the SDK
grows.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from tempest_fastapi_sdk.cli.docker_compose import (
    _parse_extras,
    env_block_for,
    generate,
)
from tempest_fastapi_sdk.cli.src_layers import add_src_layers, layers_for_extras

_SDK_NAME_RE = re.compile(
    r'^\s*"tempest-fastapi-sdk(?:\[([^\]]+)\])?[><=!~].*?",?\s*$',
    re.MULTILINE,
)
"""Captures the extras inside the SDK requirement line.

Matches::

    "tempest-fastapi-sdk[auth,upload]>=0.25.1",
    "tempest-fastapi-sdk>=0.25.1",
"""

_PROJECT_NAME_RE = re.compile(
    r'^\s*name\s*=\s*"([^"]+)"\s*$',
    re.MULTILINE,
)
"""Captures the project's ``[project] name = "…"`` value."""

_SERVER_PORT_RE = re.compile(
    r"^\s*SERVER_PORT\s*=\s*(\d+)\s*$",
    re.MULTILINE,
)
"""Captures the ``SERVER_PORT=…`` value in a ``.env`` / ``.env.example``."""


def _read_pyproject(target: Path) -> str:
    """Return the project's ``pyproject.toml`` contents.

    Args:
        target (Path): Project root directory.

    Returns:
        str: File contents.

    Raises:
        typer.Exit: When ``pyproject.toml`` does not exist (exit 2)
            or cannot be read.
    """
    pyproject = target / "pyproject.toml"
    if not pyproject.is_file():
        typer.echo(
            f"error: {pyproject} not found. Run `tempest generate` from "
            f"a project's root directory.",
            err=True,
        )
        raise typer.Exit(2)
    return pyproject.read_text(encoding="utf-8")


def _discover_project_name(pyproject_text: str, fallback: str) -> str:
    """Parse the project name from ``pyproject.toml``.

    Args:
        pyproject_text (str): The file contents.
        fallback (str): Value to return when the name is missing
            (typically the directory basename).

    Returns:
        str: The detected project name, or ``fallback``.
    """
    match = _PROJECT_NAME_RE.search(pyproject_text)
    return match.group(1) if match else fallback


def _discover_extras(pyproject_text: str) -> str:
    """Extract the SDK extras pinned in ``pyproject.toml``.

    Args:
        pyproject_text (str): The file contents.

    Returns:
        str: Comma-separated extras (e.g. ``"auth,upload,minio"``).
        Empty string when the SDK is installed without extras, or
        when the requirement line cannot be located.
    """
    match = _SDK_NAME_RE.search(pyproject_text)
    if match is None:
        return ""
    captured = match.group(1) or ""
    return ",".join(part.strip() for part in captured.split(",") if part.strip())


def regenerate_docker_compose(
    target: Path,
    *,
    project_name: str | None,
    extras: str | None,
    force: bool,
) -> None:
    """Regenerate ``docker-compose.yaml`` (and ``.env.example`` block).

    Reads the project's ``pyproject.toml`` to discover the
    currently pinned SDK extras unless ``extras`` is given
    explicitly. Refuses to overwrite an existing
    ``docker-compose.yaml`` without ``--force`` so a hand-edited
    compose file is never lost silently.

    Args:
        target (Path): Project root directory.
        project_name (str | None): Override for container-name
            prefixes. Defaults to the ``[project] name`` value or
            the directory basename.
        extras (str | None): Override for the discovered extras.
            ``None`` reads them from ``pyproject.toml``.
        force (bool): Overwrite an existing compose file.

    Raises:
        typer.Exit: On invalid input or overwrite without
            ``--force``.
    """
    pyproject_text = _read_pyproject(target)
    resolved_name = project_name or _discover_project_name(
        pyproject_text,
        fallback=target.resolve().name,
    )
    resolved_extras = extras if extras is not None else _discover_extras(pyproject_text)

    compose_path = target / "docker-compose.yaml"
    if compose_path.exists() and not force:
        typer.echo(
            f"error: {compose_path} already exists. Pass --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)
    compose_path.write_text(
        generate(resolved_name, resolved_extras),
        encoding="utf-8",
    )

    env_example = target / ".env.example"
    if env_example.exists():
        addendum = env_block_for(resolved_extras)
        if addendum:
            existing = env_example.read_text(encoding="utf-8")
            # Strip any previous SDK-generated block so re-runs stay
            # idempotent. The block is appended after a marker line.
            marker = "\n# Postgres container credentials — read by docker compose.\n"
            if marker in existing:
                existing = existing.split(marker, 1)[0].rstrip() + "\n"
            env_example.write_text(existing + addendum, encoding="utf-8")

    typer.echo(
        f"Regenerated {compose_path}"
        + (
            f" (extras: {resolved_extras})"
            if resolved_extras
            else " (no extras pinned)"
        ),
        err=False,
    )


def _discover_port(target: Path, fallback: int = 8000) -> int:
    """Read ``SERVER_PORT`` from the project's ``.env`` / ``.env.example``.

    The port only feeds the Dockerfile's ``EXPOSE`` / ``SERVER_PORT`` —
    purely informational, so a missing value falls back silently.

    Args:
        target (Path): Project root directory.
        fallback (int): Value returned when no port can be read.

    Returns:
        int: The discovered port, or ``fallback``.
    """
    for filename in (".env", ".env.example"):
        candidate = target / filename
        if not candidate.is_file():
            continue
        match = _SERVER_PORT_RE.search(candidate.read_text(encoding="utf-8"))
        if match:
            return int(match.group(1))
    return fallback


def regenerate_dockerfile(
    target: Path,
    *,
    project_name: str | None,
    force: bool,
) -> None:
    """Regenerate the ``Dockerfile`` and ``.dockerignore``.

    Renders the bundled templates with the project's name and the
    ``SERVER_PORT`` discovered from its ``.env`` / ``.env.example`` (so
    the ``EXPOSE`` line matches the configured port). Refuses to
    overwrite either file without ``force`` so a hand-tuned Dockerfile
    is never lost silently.

    Args:
        target (Path): Project root directory.
        project_name (str | None): Override for the name baked into the
            generated comments. Defaults to the ``[project] name`` value
            or the directory basename.
        force (bool): Overwrite the files if they already exist.

    Raises:
        typer.Exit: When ``pyproject.toml`` is missing (exit 2) or a
            target file exists without ``--force`` (exit 1).
    """
    # Imported here to avoid a module-level dependency on the scaffolder.
    from tempest_fastapi_sdk.cli.new import _render, _templates_root

    pyproject_text = _read_pyproject(target)
    resolved_name = project_name or _discover_project_name(
        pyproject_text,
        fallback=target.resolve().name,
    )
    context: dict[str, str] = {
        "PROJECT_NAME": resolved_name,
        "PORT": str(_discover_port(target)),
    }

    root = _templates_root()
    renders: dict[Path, Path] = {
        target / "Dockerfile": root / "Dockerfile.tmpl",
        target / ".dockerignore": root / "dockerignore.tmpl",
    }

    for destination in renders:
        if destination.exists() and not force:
            typer.echo(
                f"error: {destination} already exists. Pass --force to overwrite.",
                err=True,
            )
            raise typer.Exit(1)

    for destination, template in renders.items():
        destination.write_text(
            _render(template.read_text(encoding="utf-8"), context),
            encoding="utf-8",
        )
        typer.echo(f"Regenerated {destination}", err=False)


def regenerate_src(
    target: Path,
    *,
    extras: str | None,
    force: bool,
) -> None:
    """Add the optional ``src`` layers triggered by the project's extras.

    Reads the SDK extras pinned in the project's ``pyproject.toml``
    (unless ``extras`` overrides them) and writes only the layers that
    match — ``[queue]`` -> ``<root>/queue/``, ``[tasks]`` ->
    ``<root>/tasks/``. Files that already exist are left untouched
    unless ``force`` is passed, so a hand-edited handler is never
    clobbered silently.

    Args:
        target (Path): Project root directory.
        extras (str | None): Override for the discovered extras.
            ``None`` reads them from ``pyproject.toml``.
        force (bool): Overwrite layer files that already exist.

    Raises:
        typer.Exit: When ``pyproject.toml`` is missing (exit 2).
    """
    pyproject_text = _read_pyproject(target)
    resolved_extras = extras if extras is not None else _discover_extras(pyproject_text)
    extras_set = _parse_extras(resolved_extras)

    triggered = layers_for_extras(extras_set)
    if not triggered:
        typer.echo(
            "No src layers to generate — none of the pinned extras "
            "(queue, tasks) contribute a source layer.",
        )
        return

    written, skipped = add_src_layers(target, extras_set, force=force)
    for path in written:
        typer.echo(f"  + {path}")
    for path in skipped:
        typer.echo(f"  = {path} (exists — pass --force to overwrite)")
    typer.echo(
        f"Generated {len(written)} file(s) for layers: {', '.join(triggered)}"
        + (f" ({len(skipped)} skipped)" if skipped else ""),
    )


__all__: list[str] = [
    "regenerate_docker_compose",
    "regenerate_dockerfile",
    "regenerate_src",
]
