"""Locate the FastAPI application of the project the operator stands in.

Commands that inspect a live app (``tempest routes``,
``tempest openapi-export``) need the same thing ``uvicorn`` needs: an
import string. Asking for it every time would be noise in the scaffolded
layout, where the app always sits at ``<root>.server:app``, so the spec
is optional and a short candidate list is probed instead.

Every failed candidate is reported with its cause. Swallowing the
``ImportError`` would erase the one clue available — the attribute or
module name that could not be found — which is exactly how
``tempest db`` used to fail before v0.281.0.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from tempest_fastapi_sdk.utils._settings_discovery import (
    CODE_ROOTS as CODE_ROOTS,
)
from tempest_fastapi_sdk.utils._settings_discovery import (
    discover_settings,
)
from tempest_fastapi_sdk.utils._settings_discovery import (
    ensure_project_on_path as ensure_project_on_path,
)
from tempest_fastapi_sdk.utils._settings_discovery import (
    settings_instances as settings_instances,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

_CANDIDATE_SUFFIXES: tuple[str, ...] = (
    "server:app",
    "api.app:create_app",
    "main:app",
)
"""Import specs tried under each code root, most conventional first."""

_ROOTLESS_CANDIDATES: tuple[str, ...] = ("main:app",)
"""Import specs tried outside a code root (a single-module service)."""


def _import_object(spec: str) -> Any:
    """Import ``module:attr`` and return the attribute.

    Args:
        spec (str): The import spec, e.g. ``src.server:app``.

    Returns:
        Any: The imported attribute.

    Raises:
        ValueError: When the spec carries no ``:`` separator.
    """
    module_name, separator, attr = spec.partition(":")
    if not separator or not attr:
        message = f"expected 'module:attr', got {spec!r}"
        raise ValueError(message)
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _materialize(candidate: Any) -> FastAPI | None:
    """Return the FastAPI instance a resolved attribute stands for.

    A spec may point at the application itself or at the factory that
    builds it (``create_app``), which is the shape the scaffolded
    ``api/app.py`` exposes.

    Args:
        candidate (Any): The attribute resolved from an import spec.

    Returns:
        FastAPI | None: The application, or ``None`` when the attribute
        is neither an app nor a factory returning one.
    """
    from fastapi import FastAPI

    if isinstance(candidate, FastAPI):
        return candidate
    if callable(candidate):
        built = candidate()
        if isinstance(built, FastAPI):
            return built
    return None


def load_project_app(spec: str | None, project_root: Path | None = None) -> FastAPI:
    """Import the project's FastAPI application.

    The working directory (or ``project_root``) is prepended to
    ``sys.path`` so the import resolves against the project the operator
    is standing in rather than against anything installed.

    Args:
        spec (str | None): Explicit ``module:attr`` spec. When omitted,
            ``<root>.server:app``, ``<root>.api.app:create_app`` and
            ``main:app`` are tried under ``src`` and ``app``.
        project_root (Path | None): Project root to import from.
            Defaults to the current working directory.

    Returns:
        FastAPI: The imported application.

    Raises:
        typer.Exit: Exit code 2 when the spec fails, or when no
            candidate resolves. Every attempt's cause is printed on
            stderr first.
    """
    root = ensure_project_on_path(project_root)

    if spec:
        try:
            resolved = _import_object(spec)
        except (ImportError, AttributeError, ValueError) as exc:
            typer.echo(f"error: could not import {spec!r} ({exc}).", err=True)
            raise typer.Exit(2) from exc
        app = _materialize(resolved)
        if app is None:
            typer.echo(
                f"error: {spec!r} is neither a FastAPI app nor a factory "
                "returning one.",
                err=True,
            )
            raise typer.Exit(2)
        return app

    notes: list[str] = []
    for candidate in _candidates(root):
        try:
            resolved = _import_object(candidate)
        except (ImportError, AttributeError, ValueError) as exc:
            notes.append(f"  {candidate}: {exc}")
            continue
        app = _materialize(resolved)
        if app is None:
            notes.append(f"  {candidate}: not a FastAPI app")
            continue
        return app

    for note in notes:
        typer.echo(note, err=True)
    typer.echo(
        "error: no FastAPI app found. Pass --app 'module:attr' (the same "
        "string you give uvicorn), or run inside the project root.",
        err=True,
    )
    raise typer.Exit(2)


def _candidates(root: Path) -> list[str]:
    """Build the import specs to probe, skipping absent code roots.

    Args:
        root (Path): The project root being probed.

    Returns:
        list[str]: Import specs, most conventional first.
    """
    specs: list[str] = []
    for code_root in CODE_ROOTS:
        if not (root / code_root).is_dir():
            continue
        specs.extend(f"{code_root}.{suffix}" for suffix in _CANDIDATE_SUFFIXES)
    specs.extend(_ROOTLESS_CANDIDATES)
    return specs


def load_project_settings(project_root: Path | None = None) -> Any | None:
    """Import the project's settings, or ``None`` when absent.

    Accepts either shape the SDK documents: a ``BaseSettings`` instance on
    the module (``settings``, ``config``, or any other) or a zero-arg
    factory that builds one (``get_settings()``). The result is the first
    candidate found, best first.

    Args:
        project_root (Path | None): Project root to import from.
            Defaults to the current working directory.

    Returns:
        Any | None: The first settings object found under
        ``<root>/core/settings.py``, or ``None`` when no code root has a
        usable one. Diagnostics (an import that raised, a module with
        nothing usable) go to stderr, because every caller of this has a
        fallback.
    """
    discovery = discover_settings(project_root)
    for note in discovery.notes:
        typer.echo(note, err=True)
    if discovery.candidates:
        return discovery.candidates[0]
    return None


def resolve_app_spec(
    spec: str | None,
    project_root: Path | None = None,
) -> tuple[str, bool]:
    """Resolve the import string uvicorn should be given.

    ``serve`` needs the *string*, not the object: uvicorn re-imports it
    in the worker process, which is what makes ``--reload`` work at all.
    The candidate is still imported once here, so a typo fails before
    the server starts rather than inside the reloader.

    Args:
        spec (str | None): Explicit ``module:attr`` spec, or ``None`` to
            probe the scaffolded locations.
        project_root (Path | None): Project root to import from.
            Defaults to the current working directory.

    Returns:
        tuple[str, bool]: The import string and whether it names a
        factory (which uvicorn needs told).

    Raises:
        typer.Exit: Exit code 2 when nothing resolves, after reporting
            every attempt's cause.
    """
    from fastapi import FastAPI

    root = ensure_project_on_path(project_root)
    candidates = [spec] if spec else _candidates(root)
    notes: list[str] = []
    for candidate in candidates:
        try:
            resolved = _import_object(candidate)
        except (ImportError, AttributeError, ValueError) as exc:
            notes.append(f"  {candidate}: {exc}")
            continue
        if isinstance(resolved, FastAPI):
            return (candidate, False)
        if callable(resolved) and isinstance(resolved(), FastAPI):
            return (candidate, True)
        notes.append(f"  {candidate}: not a FastAPI app")

    for note in notes:
        typer.echo(note, err=True)
    typer.echo(
        "error: no FastAPI app found. Pass --app 'module:attr' (the same "
        "string you give uvicorn), or run inside the project root.",
        err=True,
    )
    raise typer.Exit(2)


def resolve_redis_url(explicit: str | None, project_root: Path | None = None) -> str:
    """Pick the Redis URL the way the service would.

    Order: the flag, ``REDIS_URL`` on the environment, then the
    project's settings. The settings default (``redis://localhost:6379``)
    counts, because a service that composes ``RedisSettings`` without
    setting the variable really does talk to that host.

    Args:
        explicit (str | None): Value passed on the command line.
        project_root (Path | None): Project root to import settings
            from. Defaults to the current working directory.

    Returns:
        str: The resolved URL.

    Raises:
        typer.Exit: Exit code 2 when no source carries one.
    """
    import os

    if explicit:
        return explicit
    env = os.environ.get("REDIS_URL")
    if env:
        return env
    settings = load_project_settings(project_root)
    candidate = getattr(settings, "REDIS_URL", None)
    if isinstance(candidate, str) and candidate:
        return candidate
    typer.echo(
        "error: no Redis URL. Pass --redis-url, set REDIS_URL, or compose "
        "RedisSettings into the project's settings.",
        err=True,
    )
    raise typer.Exit(2)


__all__: list[str] = [
    "CODE_ROOTS",
    "ensure_project_on_path",
    "load_project_app",
    "load_project_settings",
    "resolve_app_spec",
    "resolve_redis_url",
    "settings_instances",
]
