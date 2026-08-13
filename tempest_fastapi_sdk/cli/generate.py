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


_SYSTEM_DEPS_PDF: str = """# WeasyPrint renders text through Pango and resolves fonts
# through fontconfig, and a slim image ships neither -- the render raises an
# OSError from cffi at the first render, not at build time. DejaVu is
# here because a container with no font at all lays the document out
# correctly and prints every glyph as a box.
RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        libpango-1.0-0 \\
        libpangoft2-1.0-0 \\
        libharfbuzz0b \\
        fontconfig \\
        fonts-dejavu-core \\
    && rm -rf /var/lib/apt/lists/*

"""
"""System packages the ``[pdf]`` extra needs at runtime.

Emitted into the final stage only when the project pins that extra, so a
service that generates no documents keeps the smaller image.
"""


def _system_deps(extras: str) -> str:
    """Build the Dockerfile stanza installing system packages.

    Args:
        extras (str): Comma-separated SDK extras pinned by the project.

    Returns:
        str: The stanza, or an empty string when no extra needs one — so
        the rendered Dockerfile is byte-identical to what it was before
        this existed.
    """
    pinned = {part.strip() for part in extras.split(",") if part.strip()}
    if "pdf" in pinned or "all" in pinned:
        return _SYSTEM_DEPS_PDF
    return ""


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

    Notes:
        Any previously generated block is stripped before appending, keyed
        off a marker line, so re-running the command stays idempotent
        instead of stacking duplicates.
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


SPA_CANDIDATE_DIRS: tuple[str, ...] = ("web", "frontend", "client", "ui")
"""Directory names checked when auto-detecting a co-located SPA."""

_SPA_HEADER = """#
# A single-page app was detected in {spa_dir}/, so this build is
# fullstack: a Node stage compiles it and only the emitted dist/ is
# copied into the final image — node_modules and the Node toolchain
# never reach the runtime. Serve it from FastAPI with
# `make_spa_router("{spa_dir}/dist")`, included after every API router.
#
"""

_SPA_STAGE = (
    "# ---- spa ---------------------------------------------"
    "-----------------------\n"
    """FROM node:22-alpine AS spa

WORKDIR /spa

# Install with the lockfile first so this layer caches on dependency
# changes only. `npm ci` needs a lockfile; the glob keeps the COPY
# working before one is committed, and the fallback covers that case.
COPY {spa_dir}/package.json {spa_dir}/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY {spa_dir}/ ./
RUN npm run build

"""
)

_SPA_IGNORE = """
# Frontend — the Node stage installs and builds these itself. Copying a
# local dist/ in would ship your machine's build instead of a reproducible
# one, and node_modules would bloat the context by hundreds of megabytes.
{spa_dir}/node_modules/
{spa_dir}/dist/
"""

_SPA_COPY = """# The compiled SPA, from the Node stage. Nothing else crosses over, so
# the runtime image carries no Node runtime and no node_modules.
COPY --from=spa --chown=app:app /spa/dist /app/{spa_dir}/dist

"""


def detect_spa_dir(target: Path) -> str | None:
    """Return the co-located SPA directory name, when there is one.

    Args:
        target (Path): Project root directory.

    Returns:
        str | None: The first of :data:`SPA_CANDIDATE_DIRS` that holds a
        ``package.json``, or ``None`` when the project ships no frontend.
        Detection is by ``package.json`` rather than by the directory
        existing, so an empty ``web/`` placeholder does not produce a
        Dockerfile stage that fails at ``npm ci``.
    """
    for name in SPA_CANDIDATE_DIRS:
        if (target / name / "package.json").is_file():
            return name
    return None


def _spa_context(spa_dir: str | None) -> dict[str, str]:
    """Build the Dockerfile placeholders for the SPA stage.

    Args:
        spa_dir (str | None): The SPA directory, or ``None`` for a
            backend-only build.

    Returns:
        dict[str, str]: Empty strings when there is no SPA, so the rendered
        Dockerfile is byte-identical to the backend-only one.
    """
    if spa_dir is None:
        return {
            "SPA_HEADER": "",
            "SPA_STAGE": "",
            "SPA_COPY": "",
            "SPA_IGNORE": "",
        }
    return {
        "SPA_HEADER": _SPA_HEADER.format(spa_dir=spa_dir),
        "SPA_STAGE": _SPA_STAGE.format(spa_dir=spa_dir),
        "SPA_COPY": _SPA_COPY.format(spa_dir=spa_dir),
        "SPA_IGNORE": _SPA_IGNORE.format(spa_dir=spa_dir),
    }


def regenerate_dockerfile(
    target: Path,
    *,
    project_name: str | None,
    force: bool,
    spa_dir: str | None = None,
    detect_spa: bool = True,
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
        spa_dir (str | None): Explicit frontend directory to compile in a
            Node stage. ``None`` auto-detects (see ``detect_spa``).
        detect_spa (bool): Whether to auto-detect a co-located SPA when
            ``spa_dir`` is ``None``. Pass ``False`` for a backend-only
            image even in a project that has a frontend.

    Raises:
        typer.Exit: When ``pyproject.toml`` is missing (exit 2), a target
            file exists without ``--force`` (exit 1), or an explicit
            ``spa_dir`` holds no ``package.json`` (exit 1) — a silent
            backend-only image would be worse than saying so.
    """
    from tempest_fastapi_sdk.cli.new import _render, _templates_root

    pyproject_text = _read_pyproject(target)
    resolved_name = project_name or _discover_project_name(
        pyproject_text,
        fallback=target.resolve().name,
    )
    if spa_dir is not None:
        if not (target / spa_dir / "package.json").is_file():
            typer.echo(
                f"error: {target / spa_dir / 'package.json'} not found — "
                f"--spa-dir must point at a frontend package.",
                err=True,
            )
            raise typer.Exit(1)
        resolved_spa: str | None = spa_dir
    else:
        resolved_spa = detect_spa_dir(target) if detect_spa else None

    context: dict[str, str] = {
        "PROJECT_NAME": resolved_name,
        "PORT": str(_discover_port(target)),
        "SYSTEM_DEPS": _system_deps(_discover_extras(pyproject_text)),
        **_spa_context(resolved_spa),
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
    if resolved_spa is not None:
        typer.echo(
            f"  SPA stage: builds {resolved_spa}/ and copies "
            f"{resolved_spa}/dist into the image."
        )


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
