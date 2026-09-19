"""``tempest routes`` — list the URLs the project's app actually serves.

Reading the routers by hand answers the wrong question: a path is the
prefix of its router plus the prefix of every include above it, and
FastAPI 0.141.1 keeps an included router as a single ``_IncludedRouter``
entry rather than flattening its routes onto the application. A
comprehension over ``app.routes`` therefore misses every mounted router
— which is also why ``assert "/x" not in {r.path for r in app.routes}``
passes vacuously.

The effective paths are read from ``fastapi.routing.iter_route_contexts``,
the same expansion the OpenAPI generator walks, so routes excluded from
the schema (HTML pages, internal endpoints) are listed too.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import typer

from tempest_fastapi_sdk.cli.project import load_project_app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

_FASTAPI_BUILTIN_PATHS: frozenset[str] = frozenset(
    {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
)
"""The documentation endpoints FastAPI mounts on its own."""


@dataclass(frozen=True)
class _RouteRow:
    """One line of the listing.

    Attributes:
        method (str): HTTP method, or ``WEBSOCKET`` for a socket route.
        path (str): The effective path, prefixes already applied.
        name (str): The endpoint function's name.
        include_in_schema (bool): Whether OpenAPI documents the route.
        guards (tuple[str, ...]): Names of the top-level dependencies
            the route runs, including the ones inherited from the
            ``include_router`` call.
    """

    method: str
    path: str
    name: str
    include_in_schema: bool
    guards: tuple[str, ...]


def _guard_names(context: Any) -> tuple[str, ...]:
    """Name the top-level dependencies a route context carries.

    Args:
        context (Any): A FastAPI route context.

    Returns:
        tuple[str, ...]: Dependency names, empty when the route has none
            or exposes no dependant (a plain Starlette route).
    """
    dependant = getattr(context, "dependant", None)
    dependencies = getattr(dependant, "dependencies", ())
    names: list[str] = []
    for dependency in dependencies:
        call = getattr(dependency, "call", None)
        if call is None:
            continue
        names.append(getattr(call, "__name__", type(call).__name__))
    return tuple(names)


def _iter_contexts(app: FastAPI) -> Iterator[Any]:
    """Yield one context per effective route, mounted routers expanded.

    Args:
        app (FastAPI): The application to walk.

    Yields:
        Any: A FastAPI route context.
    """
    from fastapi.routing import iter_route_contexts

    yield from iter_route_contexts(app.routes)


def collect_routes(app: FastAPI) -> list[_RouteRow]:
    """Flatten an application into one row per method/path pair.

    Args:
        app (FastAPI): The application to inspect.

    Returns:
        list[_RouteRow]: Rows sorted by path, then method.
    """
    rows: list[_RouteRow] = []
    for context in _iter_contexts(app):
        path = getattr(context, "path", None)
        if not isinstance(path, str):
            continue
        name = str(getattr(context, "name", "") or "")
        include_in_schema = bool(getattr(context, "include_in_schema", True))
        guards = _guard_names(context)
        methods = getattr(context, "methods", None)
        if not methods:
            rows.append(
                _RouteRow("WEBSOCKET", path, name, include_in_schema, guards),
            )
            continue
        rows.extend(
            _RouteRow(method, path, name, include_in_schema, guards)
            for method in sorted(methods)
            if method != "HEAD"
        )
    return sorted(rows, key=lambda row: (row.path, row.method))


def _render_table(rows: list[_RouteRow]) -> str:
    """Format rows as an aligned, colour-free table.

    Args:
        rows (list[_RouteRow]): The rows to render.

    Returns:
        str: The table, header included.
    """
    header = ("METHOD", "PATH", "NAME", "SCHEMA", "GUARDS")
    cells = [
        (
            row.method,
            row.path,
            row.name or "-",
            "yes" if row.include_in_schema else "no",
            ", ".join(row.guards) or "-",
        )
        for row in rows
    ]
    widths = [
        max(len(header[index]), *(len(cell[index]) for cell in cells))
        if cells
        else len(header[index])
        for index in range(len(header))
    ]
    lines = ["  ".join(header[i].ljust(widths[i]) for i in range(len(header))).rstrip()]
    lines.extend(
        "  ".join(cell[i].ljust(widths[i]) for i in range(len(header))).rstrip()
        for cell in cells
    )
    return "\n".join(lines)


def routes_command(
    app_spec: str = typer.Option(
        "",
        "--app",
        "-a",
        help=(
            "Import spec of the FastAPI app ('module:attr'), the same string "
            "uvicorn takes. Omitted, the scaffolded locations are probed."
        ),
    ),
    match: str = typer.Option(
        "",
        "--match",
        "-m",
        help="Only list paths containing this substring.",
    ),
    methods: str = typer.Option(
        "",
        "--method",
        help="Comma-separated HTTP methods to keep (e.g. 'post,delete').",
    ),
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Also list FastAPI's own /docs, /redoc and /openapi.json routes.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print the routes as a JSON array instead of a table.",
    ),
) -> None:
    """List every route the application serves, mounted routers included.

    Routes kept out of the OpenAPI schema (HTML pages, internal
    endpoints) are listed with ``SCHEMA=no`` — they serve traffic, so
    leaving them out of the listing would hide exactly the routes a
    schema-based answer already misses.

    Raises:
        typer.Exit: Exit code 1 when no route survives the filters, so a
            typo in ``--match`` fails a script instead of reading as an
            application with no routes.
    """
    app = load_project_app(app_spec or None)
    rows = collect_routes(app)
    if not show_all:
        rows = [row for row in rows if row.path not in _FASTAPI_BUILTIN_PATHS]
    if match:
        rows = [row for row in rows if match in row.path]
    if methods:
        wanted = {item.strip().upper() for item in methods.split(",") if item.strip()}
        rows = [row for row in rows if row.method in wanted]

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "method": row.method,
                        "path": row.path,
                        "name": row.name,
                        "include_in_schema": row.include_in_schema,
                        "guards": list(row.guards),
                    }
                    for row in rows
                ],
                indent=2,
            )
        )
    else:
        typer.echo(_render_table(rows))

    if not rows:
        typer.echo("error: no route matched.", err=True)
        raise typer.Exit(1)


__all__: list[str] = [
    "collect_routes",
    "routes_command",
]
