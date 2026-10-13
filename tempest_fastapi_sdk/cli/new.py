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

IMPLIED_EXTRAS: dict[str, frozenset[str]] = {"admin": frozenset({"auth"})}
"""SDK extras another extra cannot run without, added when it is pinned.

The admin panel the scaffold mounts with ``[admin]`` logs in through
``UserModelAuthBackend``, which hashes the password with
``PasswordUtils`` and raises ``ImportError`` on the first login without
``[auth]``. So ``--extras admin`` pins ``[admin,auth]``; ``--extras auth``
alone pins just ``[auth]`` (the ``UserModel`` and the JWT settings, no
panel).
"""

CONDITIONAL_TEMPLATES: dict[str, str] = {"src/db/models/user.py.tmpl": "auth"}
"""Templates written only when a flag is on, keyed by their relative path.

A service without ``[auth]`` has no users, so it gets no ``UserModel`` —
the file is skipped rather than rendered empty.
"""


def _resolve_extras(extras: str) -> str:
    """Normalize the requested extras and add the ones they imply.

    Up to v0.312.x ``admin`` and ``auth`` were always pinned, so a service
    without users started by dismantling the template (#522). Now they are
    opt-in, and only :data:`IMPLIED_EXTRAS` is added on top.

    Args:
        extras (str): Comma-separated extras passed to ``--extras``.
            Blank entries are dropped and names are lower-cased, matching
            how the compose and ``src`` layer generators read them.

    Returns:
        str: The deduplicated set plus its implied extras, sorted so the
        pinned requirement is stable regardless of input order
        (``"cache,admin,cache"`` becomes ``"admin,auth,cache"``).
    """
    requested = {part.strip().lower() for part in extras.split(",") if part.strip()}
    implied = {
        extra for name in requested for extra in IMPLIED_EXTRAS.get(name, frozenset())
    }
    return ",".join(sorted(requested | implied))


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


_BLOCK_OPEN = re.compile(r"^@@if (?P<negate>!?)(?P<flag>[a-z0-9_-]+)@@$")
"""Opens a conditional block: ``@@if <flag>@@`` or ``@@if !<flag>@@``."""

_BLOCK_ELSE = "@@else@@"
"""Switches a conditional block to its other branch."""

_BLOCK_CLOSE = "@@endif@@"
"""Closes a conditional block."""


def template_flags(extras: str) -> frozenset[str]:
    """Return the flags conditional template blocks are evaluated against.

    Every pinned extra is a flag under its own name. ``taskiq`` is added
    when either TaskIQ transport (``tasks`` or ``tasks-redis``) is pinned,
    because the settings mixin and the ``tasks`` layer are the same for
    both — only the broker URL differs.

    Args:
        extras (str): Comma-separated extras, as pinned in the project.

    Returns:
        frozenset[str]: The flags.
    """
    flags = {part.strip().lower() for part in extras.split(",") if part.strip()}
    if flags & {"tasks", "tasks-redis"}:
        flags.add("taskiq")
    return frozenset(flags)


def _render_blocks(content: str, flags: frozenset[str]) -> str:
    """Keep or drop the conditional blocks of a template.

    A block is the lines between ``@@if <flag>@@`` and ``@@endif@@``, each
    marker alone on its line; ``@@if !<flag>@@`` inverts the test and an
    optional ``@@else@@`` carries the other branch. The marker lines
    themselves never reach the output. Blocks do not nest — the templates
    stay readable as the file they render to.

    Args:
        content (str): The raw template content.
        flags (frozenset[str]): Flags that are on (see
            :func:`template_flags`).

    Returns:
        str: The content with every block resolved.

    Raises:
        ValueError: On a nested block, an ``@@else@@`` or ``@@endif@@``
            outside a block, or a block left open at the end of the file.
    """
    kept: list[str] = []
    keeping: bool | None = None
    for line in content.splitlines(keepends=True):
        marker = line.strip()
        opened = _BLOCK_OPEN.fullmatch(marker)
        if opened:
            if keeping is not None:
                raise ValueError("nested @@if@@ blocks are not supported")
            keeping = (opened["flag"] in flags) != bool(opened["negate"])
            continue
        if marker == _BLOCK_ELSE:
            if keeping is None:
                raise ValueError("@@else@@ outside an @@if@@ block")
            keeping = not keeping
            continue
        if marker == _BLOCK_CLOSE:
            if keeping is None:
                raise ValueError("@@endif@@ outside an @@if@@ block")
            keeping = None
            continue
        if keeping is None or keeping:
            kept.append(line)
    if keeping is not None:
        raise ValueError("@@if@@ block left open")
    return "".join(kept)


def _render(
    content: str,
    context: dict[str, str],
    flags: frozenset[str] = frozenset(),
) -> str:
    """Resolve conditional blocks, then replace ``__KEY__`` placeholders.

    Args:
        content (str): The raw template content.
        context (dict[str, str]): Key/value pairs to substitute.
        flags (frozenset[str]): Flags the ``@@if@@`` blocks test.

    Returns:
        str: The rendered content.
    """
    rendered = _render_blocks(content, flags)
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
        extras (str): Comma-separated SDK extras to pin. Extras listed in
            :data:`IMPLIED_EXTRAS` bring their requirements along
            (``admin`` adds ``auth``); nothing else is added.
        force (bool): Overwrite the target if it already exists.

    Raises:
        typer.Exit: On invalid input or target collision without
            ``--force``.

    Notes:
        The optional source layers triggered by the chosen extras are
        scaffolded too, so a fresh ``--extras queue`` project ships
        ``src/queue/`` rather than requiring a follow-up ``tempest generate
        --src``.
    """
    resolved_name, target = _resolve_name_and_target(name, path)
    extras = _resolve_extras(extras)

    is_cwd_scaffold = target == Path.cwd().resolve()
    if target.exists() and not is_cwd_scaffold and not force:
        typer.echo(
            f"error: target {target} already exists. Pass --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)
    target.mkdir(parents=True, exist_ok=True)

    from tempest_fastapi_sdk.cli.generate import (
        _spa_context,
        _system_deps,
        detect_spa_dir,
    )

    context: dict[str, str] = {
        "PROJECT_NAME": resolved_name,
        "HOST": bind_host,
        "PORT": str(bind_port),
        "SDK_DEP": _build_sdk_dep(extras),
        "SDK_CLI_DEP": _build_sdk_dep("cli"),
        "SDK_EXTRAS": extras,
        # The Dockerfile and .dockerignore templates are shared with
        # `tempest generate dockerfile`, which fills these in. Scaffolding
        # left them out, so every new project shipped `__SPA_STAGE__` and
        # friends verbatim and `docker build` died on the first line that
        # carried one -- before any layer was built.
        "SYSTEM_DEPS": _system_deps(extras),
        **_spa_context(detect_spa_dir(target)),
    }

    flags = template_flags(extras)
    root = _templates_root()
    written: list[Path] = []
    for template in _iter_templates(root):
        flag = CONDITIONAL_TEMPLATES.get(template.relative_to(root).as_posix())
        if flag is not None and flag not in flags:
            continue
        destination = _target_path(template, root, target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            _render(template.read_text(encoding="utf-8"), context, flags),
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
    compose_path.write_text(
        generate_compose(resolved_name, extras, port=bind_port),
        encoding="utf-8",
    )
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

    layer_written, _ = add_src_layers(target, _parse_extras(extras), force=True)
    written.extend(layer_written)

    cwd = Path.cwd()
    display = target.relative_to(cwd) if target.is_relative_to(cwd) else target
    typer.echo(f"Scaffolded {len(written)} files in {target}", err=False)
    typer.echo("Next steps:", err=False)
    typer.echo(f"  cd {display}", err=False)
    typer.echo("  uv sync", err=False)
    typer.echo("  cp .env.example .env", err=False)
    typer.echo("  uv run tempest secrets init", err=False)
    typer.echo("  uv run tempest check-config", err=False)
    typer.echo("  docker compose up -d", err=False)
    typer.echo("  uv run python main.py", err=False)
    typer.echo(
        f"Containerize the app:  docker build -t {resolved_name} . "
        f"(Dockerfile + .dockerignore included)",
        err=False,
    )
    typer.echo(
        "Run app + infra in containers:  docker compose --profile prod up -d --build",
        err=False,
    )


__all__: list[str] = [
    "CONDITIONAL_TEMPLATES",
    "IMPLIED_EXTRAS",
    "scaffold",
    "template_flags",
]


def __getattr__(item: str) -> Any:  # pragma: no cover - typer test helper
    """Expose private helpers for the CLI test suite without enlarging ``__all__``."""
    if item in {
        "_build_sdk_dep",
        "_iter_templates",
        "_render",
        "_render_blocks",
        "_resolve_name_and_target",
        "_resolve_target",
        "_target_path",
        "_templates_root",
        "_validate_name",
    }:
        return globals()[item]
    raise AttributeError(item)
