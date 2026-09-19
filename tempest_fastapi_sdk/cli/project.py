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
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from fastapi import FastAPI

CODE_ROOTS: tuple[str, ...] = ("src", "app")
"""The two directory names a service may use as its code root."""

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
    root = (project_root or Path.cwd()).resolve()
    sys.path.insert(0, str(root))

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


__all__: list[str] = [
    "CODE_ROOTS",
    "load_project_app",
]
