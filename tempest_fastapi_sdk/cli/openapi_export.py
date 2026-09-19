"""``tempest openapi-export`` — write this service's own OpenAPI document.

``tempest openapi-client`` consumes somebody else's spec; this writes
ours. Two uses follow from that: feeding a generator (the client
command, a TypeScript one, a mock server) without booting the service,
and committing the document so ``--check`` fails CI the day a route,
status code or schema changes without anyone saying so.

``--check`` compares against the file on disk and names what moved,
because "the spec changed" is not a reviewable message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from tempest_fastapi_sdk.cli.project import load_project_app

if TYPE_CHECKING:
    from fastapi import FastAPI

_MAX_REPORTED_DIFFERENCES: int = 20
"""How many changed operations ``--check`` names before summarizing."""


def _document(app: FastAPI) -> dict[str, Any]:
    """Build the application's OpenAPI document.

    Args:
        app (FastAPI): The application to describe.

    Returns:
        dict[str, Any]: The generated document.
    """
    return app.openapi()


def _serialize(document: dict[str, Any], *, as_yaml: bool, indent: int) -> str:
    """Render the document as JSON or YAML text.

    Args:
        document (dict[str, Any]): The OpenAPI document.
        as_yaml (bool): Whether to emit YAML instead of JSON.
        indent (int): JSON indentation, ignored for YAML.

    Returns:
        str: The serialized document, newline-terminated.

    Raises:
        typer.Exit: Exit code 2 when YAML is requested without PyYAML.
    """
    if not as_yaml:
        return json.dumps(document, indent=indent, sort_keys=True) + "\n"
    try:
        import yaml
    except ImportError as exc:
        typer.echo(
            "error: --yaml needs PyYAML. Install the extra: "
            'uv add "tempest-fastapi-sdk[openapi]".',
            err=True,
        )
        raise typer.Exit(2) from exc
    dumped: str = yaml.safe_dump(document, sort_keys=True, allow_unicode=True)
    return dumped


def _load_stored(path: Path) -> dict[str, Any] | None:
    """Read a previously exported document.

    Args:
        path (Path): The file to read.

    Returns:
        dict[str, Any] | None: The parsed document, or ``None`` when the
            file does not exist.

    Raises:
        typer.Exit: Exit code 2 when the file exists but does not parse.
    """
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix in {".yaml", ".yml"}:
            import yaml

            parsed: Any = yaml.safe_load(text)
        else:
            parsed = json.loads(text)
    except Exception as exc:
        typer.echo(f"error: could not parse {path} ({exc}).", err=True)
        raise typer.Exit(2) from exc
    if not isinstance(parsed, dict):
        typer.echo(f"error: {path} does not hold an OpenAPI document.", err=True)
        raise typer.Exit(2)
    return parsed


def _operations(document: dict[str, Any]) -> set[str]:
    """Collect ``METHOD /path`` pairs out of a document.

    Args:
        document (dict[str, Any]): An OpenAPI document.

    Returns:
        set[str]: One entry per documented operation.
    """
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return set()
    operations: set[str] = set()
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        operations.update(f"{method.upper()} {path}" for method in item)
    return operations


def _describe_difference(current: dict[str, Any], stored: dict[str, Any]) -> list[str]:
    """Name what changed between two documents.

    Operations added and removed are listed first because they are the
    breaking kind. When the operation sets match, the difference is
    inside an operation or a schema, and the deepest key that differs is
    reported instead of a bare "documents differ".

    Args:
        current (dict[str, Any]): The freshly generated document.
        stored (dict[str, Any]): The document on disk.

    Returns:
        list[str]: Human-readable lines, empty when the two match.
    """
    if current == stored:
        return []
    lines: list[str] = []
    current_ops = _operations(current)
    stored_ops = _operations(stored)
    lines.extend(f"  + {item}" for item in sorted(current_ops - stored_ops))
    lines.extend(f"  - {item}" for item in sorted(stored_ops - current_ops))
    for key in sorted(set(current) | set(stored)):
        if current.get(key) != stored.get(key) and key != "paths":
            lines.append(f"  ~ {key} changed")
    for path in sorted(set(current.get("paths", {})) & set(stored.get("paths", {}))):
        if current["paths"][path] != stored["paths"][path]:
            lines.append(f"  ~ {path} changed")
    if len(lines) > _MAX_REPORTED_DIFFERENCES:
        remaining = len(lines) - _MAX_REPORTED_DIFFERENCES
        lines = [*lines[:_MAX_REPORTED_DIFFERENCES], f"  ... and {remaining} more"]
    return lines or ["  ~ documents differ outside paths and top-level keys"]


def openapi_export_command(
    out: str = typer.Option(
        "",
        "--out",
        "-o",
        help="File to write. Omitted, the document goes to stdout.",
    ),
    app_spec: str = typer.Option(
        "",
        "--app",
        "-a",
        help=(
            "Import spec of the FastAPI app ('module:attr'), the same string "
            "uvicorn takes. Omitted, the scaffolded locations are probed."
        ),
    ),
    as_yaml: bool = typer.Option(
        False,
        "--yaml",
        help="Emit YAML instead of JSON (needs the [openapi] extra).",
    ),
    indent: int = typer.Option(
        2,
        "--indent",
        min=0,
        help="JSON indentation. Ignored with --yaml.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help=(
            "Compare against --out instead of writing it, and exit 1 when "
            "they differ. The CI guard against an undeclared contract change."
        ),
    ),
) -> None:
    """Export the application's OpenAPI document (or check it is current).

    Raises:
        typer.Exit: Exit code 2 when ``--check`` is passed without
            ``--out`` or the stored file is missing; exit code 1 when
            the stored document differs from the generated one.
    """
    app = load_project_app(app_spec or None)
    document = _document(app)

    if not check:
        text = _serialize(document, as_yaml=as_yaml, indent=indent)
        if not out:
            typer.echo(text, nl=False)
            return
        path = Path(out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        operations = len(_operations(document))
        typer.echo(f"Wrote {path} ({operations} operation(s)).")
        return

    if not out:
        typer.echo("error: --check needs --out to compare against.", err=True)
        raise typer.Exit(2)
    path = Path(out).expanduser()
    stored = _load_stored(path)
    if stored is None:
        typer.echo(
            f"error: {path} does not exist yet. Run without --check to write it.",
            err=True,
        )
        raise typer.Exit(2)

    differences = _describe_difference(document, stored)
    if not differences:
        typer.echo(f"{path} is up to date.")
        return
    typer.echo(f"error: {path} is out of date:", err=True)
    for line in differences:
        typer.echo(line, err=True)
    typer.echo("Run 'tempest openapi-export --out <file>' to refresh it.", err=True)
    raise typer.Exit(1)


__all__: list[str] = [
    "openapi_export_command",
]
