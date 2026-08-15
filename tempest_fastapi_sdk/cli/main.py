"""Typer entry point wiring every ``tempest`` sub-command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, cast

import click
import typer
from tempest_cli.main import register_commands
from typer.core import TyperGroup

from tempest_fastapi_sdk.cli import generate as generate_module
from tempest_fastapi_sdk.cli import new as new_module
from tempest_fastapi_sdk.cli.commands import mount_project_commands
from tempest_fastapi_sdk.cli.config import load_project_commands
from tempest_fastapi_sdk.cli.db import db_app
from tempest_fastapi_sdk.cli.model import model_app
from tempest_fastapi_sdk.cli.pdf import pdf_app
from tempest_fastapi_sdk.cli.secrets import secrets_app
from tempest_fastapi_sdk.cli.user import user_app
from tempest_fastapi_sdk.cli.voice import voice_app

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
app.add_typer(model_app, name="model")
app.add_typer(pdf_app, name="pdf")
app.add_typer(voice_app, name="voice")

# The quality gate lives in `tempest-cli`, a framework-agnostic package.
# Registering it here is what keeps `tempest check` and `tempest-cli check`
# the same commands rather than two copies that drift.
register_commands(app)


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
    dockerfile: Annotated[
        bool,
        typer.Option(
            "--dockerfile",
            help=(
                "Regenerate the Dockerfile + .dockerignore. The EXPOSE "
                "port is read from the project's .env / .env.example "
                "(SERVER_PORT), falling back to 8000."
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
    spa_dir: Annotated[
        str | None,
        typer.Option(
            "--spa-dir",
            help=(
                "Frontend directory to compile in a Node stage of the "
                "Dockerfile (e.g. 'web'). Omitted, a co-located SPA is "
                "auto-detected by its package.json."
            ),
        ),
    ] = None,
    no_spa: Annotated[
        bool,
        typer.Option(
            "--no-spa",
            help=(
                "Emit a backend-only Dockerfile even when the project "
                "has a frontend directory."
            ),
        ),
    ] = False,
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
    .env.example block), ``--dockerfile`` (Dockerfile + .dockerignore),
    and/or ``--src`` (optional source layers from the pinned extras).
    New generators land here as the SDK grows.

    The Dockerfile is fullstack-aware: when the project holds a frontend
    (a ``package.json`` under ``web/``, ``frontend/``, ``client/`` or
    ``ui/``), it gains a Node stage that compiles the SPA and copies only
    the emitted ``dist/`` into the runtime image. Point at a different
    directory with ``--spa-dir``, or force a backend-only image with
    ``--no-spa``.
    """
    if not docker and not dockerfile and not src:
        typer.echo(
            "error: pass --docker, --dockerfile and/or --src to select "
            "what to regenerate.",
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
    if dockerfile:
        generate_module.regenerate_dockerfile(
            resolved_target,
            project_name=project_name,
            force=force,
            spa_dir=spa_dir,
            detect_spa=not no_spa,
        )
    if src:
        generate_module.regenerate_src(
            resolved_target,
            extras=extras,
            force=force,
        )


_SETTINGS_CANDIDATES: tuple[str, ...] = (
    "src.core.settings:settings",
    "app.core.settings:settings",
    "src.settings:settings",
    "app.settings:settings",
    "core.settings:settings",
)


def _load_object(path: str) -> Any:
    """Import and return the object named by a ``module:attr`` path.

    Args:
        path (str): An import path such as ``"src.core.settings:settings"``.

    Returns:
        Any: The resolved attribute.

    Raises:
        typer.BadParameter: When the path is malformed.
        ImportError | AttributeError: When the module or attribute is
            missing (propagated for the caller to handle).
    """
    module_name, _, attr = path.partition(":")
    if not module_name or not attr:
        raise typer.BadParameter(
            f"Expected an import path like 'module:attr', got {path!r}."
        )
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _autodetect_settings() -> Any | None:
    """Return the first importable settings object from the candidates.

    Returns:
        Any | None: The resolved settings object, or ``None`` when none
        of the conventional locations import cleanly.
    """
    for candidate in _SETTINGS_CANDIDATES:
        try:
            return _load_object(candidate)
        except (ImportError, AttributeError):
            continue
    return None


@app.command("check-config")
def check_config_cmd(
    settings: Annotated[
        str | None,
        typer.Option(
            "--settings",
            "-s",
            help="Import path to the settings object ('module:attr'). "
            "Auto-detected from conventional locations when omitted.",
        ),
    ] = None,
    imports: Annotated[
        list[str] | None,
        typer.Option(
            "--import",
            "-i",
            help="Extra module(s) to import so their @check registrations load.",
        ),
    ] = None,
    tags: Annotated[
        list[str] | None,
        typer.Option("--tag", "-t", help="Only run checks carrying one of these tags."),
    ] = None,
    fail_level: Annotated[
        str,
        typer.Option(
            "--fail-level",
            help="Level that makes the command exit non-zero "
            "(debug/info/warning/error/critical).",
        ),
    ] = "error",
) -> None:
    """Run system checks against the project's settings.

    Exits non-zero when any message reaches ``--fail-level`` (default
    ``error``), so it doubles as a CI gate and a pre-deploy sanity check.

    The current working directory is put on ``sys.path`` so the project is
    importable when the command is invoked from its root.
    """
    from tempest_fastapi_sdk.checks import CheckLevel, run_checks

    if "" not in sys.path and str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))

    try:
        threshold = CheckLevel[fail_level.upper()]
    except KeyError as exc:
        raise typer.BadParameter(
            f"Unknown --fail-level {fail_level!r}; expected one of "
            "debug/info/warning/error/critical."
        ) from exc

    for module_name in imports or []:
        import importlib

        importlib.import_module(module_name)

    context: Any | None
    if settings is not None:
        context = _load_object(settings)
    else:
        context = _autodetect_settings()
        if context is None:
            typer.secho(
                "Could not auto-detect a settings object. Pass --settings module:attr.",
                fg="yellow",
                err=True,
            )

    messages = run_checks(context, tags=tags)

    colors = {
        CheckLevel.DEBUG: "bright_black",
        CheckLevel.INFO: "blue",
        CheckLevel.WARNING: "yellow",
        CheckLevel.ERROR: "red",
        CheckLevel.CRITICAL: "bright_red",
    }
    for message in messages:
        typer.secho(str(message), fg=colors.get(message.level))

    serious = [m for m in messages if m.is_serious(threshold)]
    if not messages:
        typer.secho("System check identified no issues.", fg="green")
    else:
        summary = (
            f"{len(messages)} message(s), {len(serious)} at/above {threshold.name}."
        )
        typer.secho(summary, fg="red" if serious else "green")

    raise typer.Exit(1 if serious else 0)


@app.command("openapi-errors")
def openapi_errors_cmd(
    paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--path",
            "-p",
            help="Source directory (or file) to scan. Repeatable. "
            "Defaults to ./src or ./app, whichever exists.",
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check/--no-check",
            help="Exit non-zero when drift is found, for use as a CI step.",
        ),
    ] = False,
    allow_unreachable: Annotated[
        bool,
        typer.Option(
            "--allow-unreachable",
            help="With --check, only fail on undocumented exceptions. "
            "An over-declared route stays a warning.",
        ),
    ] = False,
    fix: Annotated[
        bool,
        typer.Option(
            "--fix",
            help="Write the missing declarations into the routes, adding "
            "the imports they need. Requires a clean git working tree.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="With --fix, print the unified diff instead of writing it.",
        ),
    ] = False,
) -> None:
    """Compare the exceptions each route raises against what it documents.

    Walks ``router -> controller -> service -> repository`` statically
    (via ``ast``, without importing the application) and reports two
    directions: exceptions reachable from a handler but missing from its
    ``error_responses(...)`` / ``@raises(...)`` declaration, and
    exceptions declared but never reachable.

    Reachability resolves calls by *name* and cannot see dynamic raises,
    so read the report as a guide rather than a proof. Both blind spots
    are covered by listing the exception in the function's Google-style
    ``Raises:`` section, which the analyzer reads too.

    ``--fix`` writes the missing declarations back into the routes and adds
    the imports they need. It only ever **adds**: names already declared
    keep their place, and the ``unreachable`` findings are never acted on,
    because name-based reachability could otherwise delete a correct
    declaration. A clean git working tree is required, so ``git diff`` is
    the review and ``git checkout`` the undo; ``--dry-run`` prints the diff
    instead of writing.
    """
    from tempest_fastapi_sdk.cli.openapi_errors import (
        analyze_paths,
        default_source_paths,
    )

    project_root = Path.cwd()
    targets = list(paths or []) or default_source_paths(project_root)
    if not targets:
        typer.secho(
            "No source directory found. Pass --path <dir> (expected ./src or ./app).",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    try:
        findings = analyze_paths(targets)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if not findings:
        typer.secho(
            f"Every route's declared errors match its flow "
            f"({', '.join(str(target) for target in targets)}).",
            fg="green",
        )
        raise typer.Exit(0)

    undocumented_total = 0
    for finding in findings:
        typer.secho(
            f"{finding.location}  {finding.route.method} {finding.route.path}",
            fg="cyan",
        )
        if finding.undocumented:
            undocumented_total += len(finding.undocumented)
            typer.secho(
                f"  undocumented: {', '.join(finding.undocumented)}",
                fg="red",
            )
        if finding.unreachable:
            typer.secho(
                f"  unreachable:  {', '.join(finding.unreachable)}",
                fg="yellow",
            )

    typer.secho(
        f"{len(findings)} route(s) with drift, "
        f"{undocumented_total} undocumented exception(s).",
        fg="red" if undocumented_total else "yellow",
    )

    if fix:
        _apply_error_fixes(findings, targets, project_root, dry_run=dry_run)
        raise typer.Exit(0)

    if not check:
        raise typer.Exit(0)
    raise typer.Exit(1 if undocumented_total or not allow_unreachable else 0)


@app.command("permissions")
def permissions_cmd(
    paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--path",
            "-p",
            help="Source directory (or file) to scan. Repeatable. "
            "Defaults to ./src or ./app, whichever exists.",
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check/--no-check",
            help="Exit non-zero when an error-level finding exists, for use "
            "as a CI step.",
        ),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="With --check, fail on warnings too.",
        ),
    ] = False,
) -> None:
    """Check every ``@requires`` guard against its contract.

    ``@requires`` validates what it can see when the module is imported —
    that each guard is callable, takes one parameter, and that the
    decorated function has a user to check. This command reads the rest of
    the contract off the source with ``ast``, without importing the
    application: a guard that raises outside the ``AppException``
    hierarchy (answered as HTTP 500 with no error code), a predicate-style
    guard whose ``return False`` is silently ignored, an ``async`` guard on
    a synchronous function, a guard that can never deny.

    Errors are contract violations that break the check at runtime;
    warnings are conventions and cases the checker could not resolve
    (a lambda guard, a guard defined outside the scanned paths, a name
    matching several definitions — reported rather than guessed). ``--check``
    fails on errors, ``--check --strict`` on warnings as well.
    """
    from tempest_fastapi_sdk.cli.openapi_errors import default_source_paths
    from tempest_fastapi_sdk.cli.permissions import analyze_permissions

    targets = list(paths or []) or default_source_paths(Path.cwd())
    if not targets:
        typer.secho(
            "No source directory found. Pass --path <dir> (expected ./src or ./app).",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    try:
        findings = analyze_permissions(targets)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if not findings:
        typer.secho(
            f"Every @requires guard honors its contract "
            f"({', '.join(str(target) for target in targets)}).",
            fg="green",
        )
        raise typer.Exit(0)

    errors = [finding for finding in findings if finding.severity == "error"]
    current: str | None = None
    for finding in findings:
        header = f"{finding.location}  {finding.function}"
        if header != current:
            typer.secho(header, fg="cyan")
            current = header
        typer.secho(
            f"  {finding.severity}: {finding.code}: {finding.message}",
            fg="red" if finding.severity == "error" else "yellow",
        )

    typer.secho(
        f"{len(findings)} finding(s), {len(errors)} error(s).",
        fg="red" if errors else "yellow",
    )

    if not check:
        raise typer.Exit(0)
    raise typer.Exit(1 if errors or strict else 0)


@app.command("openapi-client")
def openapi_client_cmd(
    spec: Annotated[
        str,
        typer.Argument(
            help="URL or path of the OpenAPI 3 specification to generate from.",
        ),
    ],
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            "-n",
            help="Integration name — becomes the package directory and the "
            "client class prefix. Defaults to the spec's info.title.",
        ),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help="Output directory. Defaults to <src|app>/integrations/<name>/.",
        ),
    ] = None,
    headers: Annotated[
        list[str] | None,
        typer.Option(
            "--header",
            "-H",
            help="Header sent when fetching the spec, as 'Name: value'. "
            "Repeatable — use it for a spec behind authentication.",
        ),
    ] = None,
    target: Annotated[
        str,
        typer.Option(
            "--path",
            "-p",
            help="Project root used to resolve the default output directory. "
            "Defaults to the current working directory.",
        ),
    ] = ".",
    schemas_only: Annotated[
        bool,
        typer.Option(
            "--schemas-only",
            help="Generate only schemas.py, skipping the HTTP client.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite generated files that already exist.",
        ),
    ] = False,
    no_format: Annotated[
        bool,
        typer.Option(
            "--no-format",
            help="Skip the `ruff format` + `ruff check --fix` pass over the "
            "generated files.",
        ),
    ] = False,
) -> None:
    """Generate Pydantic schemas + a typed HTTP client from an OpenAPI spec.

    Writes a self-contained package — ``schemas.py`` with one class per
    component and ``client.py`` with one async method per operation. Field
    names are Python-idiomatic with the wire name attached as a Pydantic
    ``alias``, and every field carries the ``title`` / ``description`` /
    ``examples`` the specification provided, so the generated module
    doubles as the integration's documentation.

    The whole directory is generated, never hand-edited: rerun with
    ``--force`` to refresh it when the third party ships a new version.
    """
    from tempest_fastapi_sdk.openapi.generate import generate_integration
    from tempest_fastapi_sdk.openapi.loader import SpecError, parse_header_options

    try:
        parsed_headers = parse_header_options(list(headers or []))
        result = generate_integration(
            spec,
            target=Path(target).expanduser().resolve(),
            name=name,
            out=out.expanduser().resolve() if out is not None else None,
            headers=parsed_headers,
            schemas_only=schemas_only,
            force=force,
            run_format=not no_format,
        )
    except SpecError as exc:
        typer.secho(f"error: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    for path in result.written:
        typer.secho(f"  + {path}", fg="green")
    for path in result.skipped:
        typer.secho(f"  = {path} (exists — pass --force to overwrite)", fg="yellow")

    typer.echo(
        f"{result.schema_count} schema(s), {result.operation_count} operation(s)."
    )
    if result.unsupported:
        typer.secho(
            f"{len(result.unsupported)} construct(s) could not be modelled as "
            f"written — each line says what was generated instead, and the "
            f"ones with something to mark carry an `# openapi: unsupported` "
            f"comment in the output:",
            fg="yellow",
        )
        for note in result.unsupported:
            typer.secho(f"  - {note}", fg="yellow")

    if result.skipped and not result.written:
        raise typer.Exit(1)


def _apply_error_fixes(
    findings: list[Any],
    targets: list[Path],
    root: Path,
    *,
    dry_run: bool,
) -> None:
    """Write (or preview) the missing error declarations.

    Args:
        findings (list[Any]): The routes the analyzer flagged.
        targets (list[Path]): The scanned source paths, used to locate the
            exception classes an import has to point at.
        root (Path): Project root, used to derive dotted import paths and
            to check the git working tree.
        dry_run (bool): Print the unified diff instead of writing.

    Raises:
        typer.Exit: ``1`` when the working tree is dirty, since a rewrite
            without a clean tree has no reviewable diff and no easy undo.
    """
    from tempest_fastapi_sdk.cli.openapi_errors import exception_locations
    from tempest_fastapi_sdk.cli.openapi_fix import (
        DirtyWorkingTreeError,
        ensure_clean_worktree,
        normalize,
        plan_file,
        render_file,
        ruff_runner,
        unified_diff,
    )

    actionable = [f for f in findings if f.undocumented]
    if not actionable:
        typer.secho("Nothing to fix — no undocumented exceptions.", fg="green")
        return

    if ruff_runner() is None:
        typer.secho(
            "note: no working ruff found, so the new import stays where it was "
            "spliced and a long decorator is not wrapped. Run `tempest fix` "
            "afterwards to sort and format.",
            fg="yellow",
        )

    if not dry_run:
        try:
            ensure_clean_worktree(root)
        except DirtyWorkingTreeError as exc:
            typer.secho(f"error: {exc}", fg="red", err=True)
            raise typer.Exit(1) from exc

    locations = exception_locations(targets)
    by_file: dict[Path, list[Any]] = {}
    for finding in actionable:
        by_file.setdefault(finding.function.file, []).append(finding)

    written = 0
    for path, group in sorted(by_file.items()):
        plan = plan_file(path, group, locations, root)
        if not plan.insertions:
            continue
        before = path.read_text(encoding="utf-8")
        after = normalize(render_file(plan), near=path.parent)
        if dry_run:
            typer.echo(unified_diff(path, before, after, root), nl=False)
        else:
            path.write_text(after, encoding="utf-8")
            typer.secho(f"  ~ {path} ({len(plan.routes)} route(s))", fg="green")
            written += 1
        if plan.unresolved:
            typer.secho(
                f"  ! {path}: could not import "
                f"{', '.join(sorted(set(plan.unresolved)))} — add it by hand.",
                fg="yellow",
            )

    if dry_run:
        typer.secho("Dry run — nothing written.", fg="cyan")
        return
    if written:
        typer.secho(
            f"Wrote {written} file(s). Review with `git diff`, undo with "
            f"`git checkout -- .`.",
            fg="cyan",
        )


def main() -> None:
    """Console-script entry point.

    Mounts the project's management commands (``[tool.tempest] commands``
    or the conventional ``src.commands`` / ``app.commands`` / ``commands``
    modules) onto the root app, then runs the CLI. Discovery failures are
    caught broadly and reported, never raised: a broken project command
    module must not brick the built-in commands that would let the user fix
    it.
    """
    try:
        mount_project_commands(app, modules=load_project_commands())
    except Exception as exc:
        typer.secho(
            f"tempest: could not load project commands: {exc}",
            err=True,
            fg="yellow",
        )
    app()


if __name__ == "__main__":  # pragma: no cover - manual invocation only
    main()
