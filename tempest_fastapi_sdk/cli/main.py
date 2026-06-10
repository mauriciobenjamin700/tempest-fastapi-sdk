"""Typer entry point wiring every ``tempest`` sub-command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, cast

import click
import typer
from typer.core import TyperGroup

from tempest_fastapi_sdk.cli import generate as generate_module
from tempest_fastapi_sdk.cli import lint as lint_module
from tempest_fastapi_sdk.cli import new as new_module
from tempest_fastapi_sdk.cli.db import db_app
from tempest_fastapi_sdk.cli.secrets import secrets_app
from tempest_fastapi_sdk.cli.user import user_app

# Typer >= 0.13 vendors its own copy of Click under ``typer._click``, so a
# raised usage error is a ``typer._click.exceptions.UsageError`` — NOT the
# public ``click.UsageError``. Catch both so the full-help behaviour works
# whether the installed Typer vendors Click or uses the public package
# (``typer>=0.12``). ``NoArgsIsHelpError`` is the special case Typer raises
# for ``no_args_is_help`` groups: its message already *is* the help text.
_NoArgsIsHelpError: type[Exception] | None
_USAGE_ERRORS: tuple[type[Exception], ...]
_ABORT_ERRORS: tuple[type[Exception], ...]

try:  # pragma: no cover - import shape depends on the installed Typer
    from typer._click.exceptions import Abort as _TyperAbort
    from typer._click.exceptions import NoArgsIsHelpError as _TyperNoArgs
    from typer._click.exceptions import UsageError as _TyperUsageError

    _NoArgsIsHelpError = _TyperNoArgs
    _USAGE_ERRORS = (_TyperUsageError, click.UsageError)
    _ABORT_ERRORS = (_TyperAbort, click.exceptions.Abort)
except ImportError:  # pragma: no cover - older Typer on public Click
    _NoArgsIsHelpError = None
    _USAGE_ERRORS = (click.UsageError,)
    _ABORT_ERRORS = (click.exceptions.Abort,)


class FullHelpTyperGroup(TyperGroup):
    """Typer group that prints full ``--help`` on any usage error.

    Click's default reaction to a bad command, an unknown option, or a
    missing required argument is a one-line ``Error:`` plus a terse
    ``Try '... --help'`` hint. That forces the user to re-run with
    ``--help`` to see what the command actually accepts. This group
    intercepts the :class:`click.UsageError`, renders the offending
    context's complete help text (every parameter, default and
    description), and only then prints the error — so the fix is on
    screen immediately.
    """

    def main(self, *args: Any, **kwargs: Any) -> Any:
        """Run the CLI, expanding usage errors into full help output.

        Parsing is delegated to Click with ``standalone_mode`` forced
        off so usage errors propagate here instead of being printed and
        swallowed inside Click. Success/exit codes are then re-applied
        to preserve the normal process-exit contract (e.g. the quality
        gates that ``raise typer.Exit(<code>)``).

        Args:
            *args (Any): Positional arguments forwarded to
                :meth:`click.Group.main`.
            **kwargs (Any): Keyword arguments forwarded to
                :meth:`click.Group.main`.

        Returns:
            Any: The command return value when ``standalone_mode`` is
            explicitly requested by the caller; otherwise the process
            exits.

        Raises:
            click.UsageError: Re-raised when ``standalone_mode`` is off.
            click.exceptions.Abort: Re-raised when ``standalone_mode``
                is off.
        """
        standalone_mode = kwargs.get("standalone_mode", True)
        kwargs["standalone_mode"] = False
        try:
            result = super().main(*args, **kwargs)
        except _USAGE_ERRORS as error:
            self._show_usage_error_with_help(cast("click.UsageError", error))
            if not standalone_mode:
                raise
            sys.exit(getattr(error, "exit_code", 2) or 2)
        except _ABORT_ERRORS:
            click.secho("Aborted!", err=True, fg="red")
            if not standalone_mode:
                raise
            sys.exit(1)
        if not standalone_mode:
            return result
        sys.exit(result if isinstance(result, int) else 0)

    @staticmethod
    def _show_usage_error_with_help(error: click.UsageError) -> None:
        """Print the offending context's full help, then the error.

        For a ``no_args_is_help`` group the raised error's message is
        already the help text, so it is printed verbatim with no extra
        ``Error:`` line. Every other usage error renders the offending
        context's complete help followed by the concrete error message.

        Args:
            error (click.UsageError): The raised usage error. Its
                ``ctx`` (when present) identifies which command's help
                to render.

        Returns:
            None: Output is written to stderr.
        """
        if _NoArgsIsHelpError is not None and isinstance(error, _NoArgsIsHelpError):
            error.show()
            return
        ctx = error.ctx
        if ctx is not None:
            click.echo(ctx.get_help(), err=True)
            click.echo(err=True)
        click.secho(f"Error: {error.format_message()}", err=True, fg="red")


app: typer.Typer = typer.Typer(
    name="tempest",
    cls=FullHelpTyperGroup,
    help=(
        "Tempest FastAPI SDK CLI — scaffold projects and run the SDK's "
        "preferred quality gates (ruff, mypy, pytest)."
    ),
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
app.add_typer(db_app, name="db")
app.add_typer(user_app, name="user")
app.add_typer(secrets_app, name="secrets")


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
                "pyproject.toml (e.g. 'auth,admin,upload'). Pass an empty "
                "string to install the core package without extras. "
                "Defaults to 'auth,admin' because the scaffolded app.py "
                "wires the admin panel and concrete UserModel out of the "
                "box."
            ),
        ),
    ] = "auth,admin",
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


@app.command("generate")
def generate_cmd(
    docker: Annotated[
        bool,
        typer.Option(
            "--docker",
            help=(
                "Regenerate docker-compose.yaml + .env.example service "
                "block from the project's currently pinned SDK extras."
            ),
        ),
    ] = False,
    src: Annotated[
        bool,
        typer.Option(
            "--src",
            help=(
                "Add the optional source layers triggered by the "
                "project's pinned SDK extras (e.g. [queue] -> src/queue/, "
                "[tasks] -> src/tasks/). Idempotent — existing files are "
                "skipped unless --force is passed."
            ),
        ),
    ] = False,
    target: Annotated[
        str,
        typer.Option(
            "--path",
            "-p",
            help=(
                "Project root to regenerate inside. Defaults to the "
                "current working directory."
            ),
        ),
    ] = ".",
    extras: Annotated[
        str | None,
        typer.Option(
            "--extras",
            help=(
                "Override the SDK extras used to decide which services "
                "land in docker-compose.yaml. When omitted, the extras "
                "are read from the project's pyproject.toml."
            ),
        ),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help=(
                "Override the project name used as the container-name "
                "prefix. Defaults to the ``[project] name`` value in "
                "pyproject.toml or the directory basename."
            ),
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite generated files if they already exist.",
        ),
    ] = False,
) -> None:
    """Regenerate scaffolded artifacts in an existing project.

    Pick what to regenerate with ``--docker`` (docker-compose.yaml +
    .env.example block) and/or ``--src`` (optional source layers from
    the pinned extras). New generators land here as the SDK grows.
    """
    if not docker and not src:
        typer.echo(
            "error: pass --docker and/or --src to select what to regenerate.",
            err=True,
        )
        raise typer.Exit(2)

    resolved_target = Path(target).expanduser().resolve()

    if docker:
        generate_module.regenerate_docker_compose(
            resolved_target,
            project_name=project_name,
            extras=extras,
            force=force,
        )
    if src:
        generate_module.regenerate_src(
            resolved_target,
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
