"""Typer entry point wiring every ``tempest`` sub-command."""

from __future__ import annotations

from typing import Annotated

import typer

from tempest_fastapi_sdk.cli import lint as lint_module
from tempest_fastapi_sdk.cli import new as new_module

app: typer.Typer = typer.Typer(
    name="tempest",
    help=(
        "Tempest FastAPI SDK CLI — scaffold projects and run the SDK's "
        "preferred quality gates (ruff, mypy, pytest)."
    ),
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


def _print_version(value: bool) -> None:
    """Print the SDK version and exit.

    Args:
        value (bool): True when ``--version`` is passed.

    Raises:
        typer.Exit: Always when ``value`` is True.
    """
    if value:
        from tempest_fastapi_sdk import __version__

        typer.echo(f"tempest-fastapi-sdk {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the SDK version and exit.",
            callback=_print_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Root callback wiring global flags such as ``--version``."""


@app.command("version")
def version_cmd() -> None:
    """Show the SDK version (alias of ``--version``)."""
    from tempest_fastapi_sdk import __version__

    typer.echo(f"tempest-fastapi-sdk {__version__}")


@app.command("new")
def new_cmd(
    name: Annotated[
        str,
        typer.Argument(
            help=(
                "Project / package name (must be a valid Python identifier), "
                "or '.' to scaffold flatly in the current working directory. "
                "Omitting the argument is equivalent to '.'."
            ),
        ),
    ] = ".",
    path: Annotated[
        str | None,
        typer.Option(
            "--path",
            "-p",
            help="Parent directory where the project folder is created. "
            "Defaults to the current working directory.",
        ),
    ] = None,
    bind_host: Annotated[
        str,
        typer.Option(
            "--bind-host",
            help=(
                "Default HOST value injected into the scaffolded settings. "
                "Use 127.0.0.1 for internal services and 0.0.0.0 only when "
                "another origin (e.g. a frontend dev server) needs to reach "
                "the service."
            ),
        ),
    ] = "127.0.0.1",
    bind_port: Annotated[
        int,
        typer.Option(
            "--bind-port",
            min=1,
            max=65535,
            help="Default PORT value injected into the scaffolded settings.",
        ),
    ] = 8000,
    extras: Annotated[
        str,
        typer.Option(
            "--extras",
            help=(
                "Comma-separated SDK extras to pin in the generated "
                "pyproject.toml (e.g. 'auth,upload'). Pass an empty string "
                "to install the core package without extras."
            ),
        ),
    ] = "auth",
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite the target directory if it already exists.",
        ),
    ] = False,
) -> None:
    """Scaffold a new layered FastAPI service using the SDK conventions."""
    new_module.scaffold(
        name=name,
        path=path,
        bind_host=bind_host,
        bind_port=bind_port,
        extras=extras,
        force=force,
    )


@app.command("lint")
def lint_cmd(
    target: Annotated[
        str,
        typer.Argument(help="Path to lint. Defaults to the current directory."),
    ] = ".",
) -> None:
    """Run ``ruff check`` on the target."""
    raise typer.Exit(lint_module.run_ruff_check(target))


@app.command("fix")
def fix_cmd(
    target: Annotated[
        str,
        typer.Argument(help="Path to fix. Defaults to the current directory."),
    ] = ".",
    unsafe: Annotated[
        bool,
        typer.Option(
            "--unsafe",
            help=(
                "Also apply ruff's unsafe autofixes (rules with possible "
                "behavior changes). Off by default — review the diff after "
                "enabling."
            ),
        ),
    ] = False,
) -> None:
    """Apply every ruff autofix + format the target in one pass.

    Equivalent to running ``ruff check --fix`` followed by ``ruff format``:
    sorts and dedupes imports, drops unused imports, normalizes string
    quotes, removes trailing whitespace, normalizes indentation, line
    length and blank lines.
    """
    raise typer.Exit(lint_module.run_ruff_fix(target, unsafe=unsafe))


@app.command("format")
def format_cmd(
    target: Annotated[
        str,
        typer.Argument(
            help="Path to format. Defaults to the current directory.",
        ),
    ] = ".",
) -> None:
    """Run ``ruff format`` on the target (writes files)."""
    raise typer.Exit(lint_module.run_ruff_format(target, check=False))


@app.command("fmt-check")
def fmt_check_cmd(
    target: Annotated[
        str,
        typer.Argument(help="Path to inspect. Defaults to the current directory."),
    ] = ".",
) -> None:
    """Run ``ruff format --check`` on the target (read-only)."""
    raise typer.Exit(lint_module.run_ruff_format(target, check=True))


@app.command("type")
def type_cmd(
    target: Annotated[
        str,
        typer.Argument(help="Package/path to type-check."),
    ] = ".",
) -> None:
    """Run ``mypy`` against the target."""
    raise typer.Exit(lint_module.run_mypy(target))


@app.command("test")
def test_cmd(
    target: Annotated[
        str | None,
        typer.Argument(help="Optional pytest path filter."),
    ] = None,
) -> None:
    """Run ``pytest`` (forwarding the optional path argument)."""
    raise typer.Exit(lint_module.run_pytest(target))


@app.command("check")
def check_cmd(
    target: Annotated[
        str,
        typer.Argument(
            help="Path to inspect. Defaults to the current directory.",
        ),
    ] = ".",
) -> None:
    """Run the full quality gate (lint + fmt-check + type + test)."""
    raise typer.Exit(lint_module.run_full_check(target))


if __name__ == "__main__":  # pragma: no cover - manual invocation only
    app()
