"""``tempest serve`` — run the project's app with the SDK's defaults.

The scaffolded ``main.py`` already boots uvicorn programmatically, so
this adds one thing: it works before (or without) that file, from any
project whose app sits somewhere conventional, reading the same
``SERVER_HOST`` / ``SERVER_PORT`` / ``SERVER_RELOAD`` the service reads.

uvicorn is given the import *string*, never the imported object: the
reloader re-imports it in the worker process, and handing it an instance
is what silently turns ``--reload`` into a no-op.
"""

from __future__ import annotations

import typer

from tempest_fastapi_sdk.cli.project import load_project_settings, resolve_app_spec


def serve_command(
    app_spec: str = typer.Option(
        "",
        "--app",
        "-a",
        help=(
            "Import spec of the FastAPI app ('module:attr'). Omitted, the "
            "scaffolded locations are probed."
        ),
    ),
    host: str = typer.Option(
        "",
        "--host",
        "-h",
        help="Bind interface. Defaults to the project's SERVER_HOST.",
    ),
    port: int = typer.Option(
        0,
        "--port",
        "-p",
        min=0,
        max=65535,
        help="TCP port. Defaults to the project's SERVER_PORT.",
    ),
    reload: bool | None = typer.Option(
        None,
        "--reload/--no-reload",
        help=(
            "Restart on file changes. Passing neither leaves the decision to "
            "the project's SERVER_RELOAD, so --no-reload can turn it off."
        ),
    ),
    workers: int = typer.Option(
        1,
        "--workers",
        "-w",
        min=1,
        help="Worker processes. Refused together with --reload.",
    ),
) -> None:
    """Start the HTTP server for the project in the working directory.

    Raises:
        typer.Exit: Exit code 2 when ``--workers`` is combined with
            ``--reload``, which uvicorn cannot honour at once.
    """
    if reload is True and workers > 1:
        typer.echo(
            "error: --reload runs a single process; drop --workers or drop --reload.",
            err=True,
        )
        raise typer.Exit(2)

    spec, is_factory = resolve_app_spec(app_spec or None)
    settings = load_project_settings()

    from tempest_fastapi_sdk import run_server

    extra: dict[str, object] = {}
    if is_factory:
        extra["factory"] = True
    if workers > 1:
        extra["workers"] = workers

    typer.echo(f"Serving {spec}")
    run_server(
        spec,
        settings=settings,
        host=host or None,
        port=port or None,
        reload=reload,
        **extra,
    )


__all__: list[str] = [
    "serve_command",
]
