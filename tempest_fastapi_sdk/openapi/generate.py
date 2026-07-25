"""Orchestrate: load a specification, parse it, emit files, format them."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from tempest_fastapi_sdk.cli.src_layers import detect_source_root
from tempest_fastapi_sdk.openapi.emit_client import emit_client, rerun_hint
from tempest_fastapi_sdk.openapi.emit_schemas import emit_schemas
from tempest_fastapi_sdk.openapi.loader import SpecError, load_spec
from tempest_fastapi_sdk.openapi.naming import to_pascal, to_snake
from tempest_fastapi_sdk.openapi.parse import parse_spec

_PACKAGE_DOCSTRING = '''"""{title} integration — generated from its OpenAPI spec.

Do not edit by hand. Rerun to refresh:

{rerun}
"""

from .client import DEFAULT_BASE_URL as DEFAULT_BASE_URL
from .client import {client_class} as {client_class}

__all__: list[str] = [
    "DEFAULT_BASE_URL",
    "{client_class}",
]
'''
"""Body of the generated package ``__init__.py`` (client included)."""

_SCHEMAS_ONLY_DOCSTRING = '''"""{title} schemas — generated from its OpenAPI spec.

Do not edit by hand. Rerun to refresh:

{rerun}
"""
'''
"""Body of the generated package ``__init__.py`` under ``--schemas-only``."""


@dataclass(slots=True)
class GenerationResult:
    """What one generation run produced.

    Attributes:
        written (list[Path]): Files created or overwritten.
        skipped (list[Path]): Files left untouched because they existed
            and ``force`` was ``False``.
        schema_count (int): Number of generated classes.
        operation_count (int): Number of generated client methods.
        unsupported (tuple[str, ...]): Notes about constructs that could
            not be represented, for the caller to surface.
        formatted (bool): Whether ``ruff format`` ran over the output.
    """

    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    schema_count: int = 0
    operation_count: int = 0
    unsupported: tuple[str, ...] = ()
    formatted: bool = False


def default_output_dir(target: Path, name: str) -> Path:
    """Return the conventional destination for a generated integration.

    A third-party integration is an outbound adapter, not the service's own
    DTO layer, so it gets its own fully-generated package instead of being
    mixed into ``src/schemas/``. That keeps regeneration safe — nothing in
    the directory is ever hand-edited — and keeps 60 generated classes out
    of the hand-written schema package's ``__init__``.

    Args:
        target (Path): The project root.
        name (str): The integration name.

    Returns:
        Path: ``<src|app>/integrations/<name>/``, honoring whichever source
        root the project uses.
    """
    root = detect_source_root(target)
    return target / root / "integrations" / to_snake(name)


def _format_paths(paths: list[Path]) -> bool:
    """Run ``ruff format`` over the generated files.

    Args:
        paths (list[Path]): Files to format.

    Returns:
        bool: ``True`` when ruff ran. ``False`` when no runner could be
        found — the emitted code is already formatted to the project's
        style, so a missing ruff degrades the output's polish, never its
        correctness.
    """
    if not paths:
        return False
    from tempest_fastapi_sdk.cli.lint import _resolve

    runner = _resolve("ruff")
    if runner is None:
        return False
    arguments = [str(path) for path in paths]
    subprocess.run(
        [*runner, "format", *arguments],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        [*runner, "check", "--fix", "--quiet", *arguments],
        check=False,
        capture_output=True,
    )
    return True


def generate_integration(
    source: str,
    *,
    target: Path,
    name: str | None = None,
    out: Path | None = None,
    headers: Mapping[str, str] | None = None,
    schemas_only: bool = False,
    force: bool = False,
    run_format: bool = True,
) -> GenerationResult:
    """Generate an integration package from an OpenAPI specification.

    Args:
        source (str): URL or path of the specification.
        target (Path): Project root, used to resolve the default output
            directory and its ``src``/``app`` source root.
        name (str | None): Integration name. ``None`` derives it from the
            specification's ``info.title``.
        out (Path | None): Explicit output directory, overriding the
            convention.
        headers (Mapping[str, str] | None): Headers for fetching the
            specification.
        schemas_only (bool): Skip ``client.py`` and the package
            ``__init__.py`` re-exports.
        force (bool): Overwrite files that already exist. Matches the
            semantics of the other generators — without it an existing
            file is reported as skipped, never silently replaced.
        run_format (bool): Run ``ruff format`` + ``ruff check --fix`` over
            the result.

    Returns:
        GenerationResult: Files written and skipped, counts, and the
        unsupported-construct notes.

    Raises:
        SpecError: When the specification cannot be loaded or parsed, or
            when it declares no ``info.title`` and no ``--name`` was given
            (there would be no way to name the package).
    """
    document = load_spec(source, headers=headers)

    if name is None:
        info = document.get("info")
        title = info.get("title") if isinstance(info, dict) else None
        if not isinstance(title, str) or not title.strip():
            raise SpecError(
                "The specification has no `info.title`, so the integration "
                "cannot be named automatically. Pass --name."
            )
        name = title
    package_name = to_snake(name)

    spec = parse_spec(document, client_name=package_name)
    destination = out if out is not None else default_output_dir(target, package_name)

    files: dict[str, str] = {
        "schemas.py": emit_schemas(spec, title=spec.client.title),
    }
    if schemas_only:
        files["__init__.py"] = _SCHEMAS_ONLY_DOCSTRING.format(
            title=spec.client.title,
            rerun="\n".join(rerun_hint(source, package_name, schemas_only=True)),
        )
    else:
        files["client.py"] = emit_client(spec.client)
        files["__init__.py"] = _PACKAGE_DOCSTRING.format(
            title=spec.client.title,
            rerun="\n".join(rerun_hint(source, package_name)),
            client_class=spec.client.class_name,
        )

    result = GenerationResult(
        schema_count=len(spec.schemas),
        operation_count=len(spec.client.operations),
        unsupported=spec.unsupported,
    )
    destination.mkdir(parents=True, exist_ok=True)
    for filename in sorted(files):
        path = destination / filename
        if path.exists() and not force:
            result.skipped.append(path)
            continue
        path.write_text(files[filename], encoding="utf-8")
        result.written.append(path)

    if run_format:
        result.formatted = _format_paths(result.written)
    return result


def suggest_client_class(name: str) -> str:
    """Return the class name :func:`generate_integration` will emit.

    Useful for a caller that wants to print usage instructions before
    generating.

    Args:
        name (str): The integration name.

    Returns:
        str: ``<PascalName>Client``.
    """
    return f"{to_pascal(name)}Client"


__all__: list[str] = [
    "GenerationResult",
    "default_output_dir",
    "generate_integration",
    "suggest_client_class",
]
