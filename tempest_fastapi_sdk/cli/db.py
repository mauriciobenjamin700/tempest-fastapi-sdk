"""``tempest db`` — Alembic migration helpers driven from the CLI.

Wraps :class:`tempest_fastapi_sdk.AlembicHelper` so the project's
``alembic.ini`` + ``alembic/env.py`` stay the single source of
truth. All commands are thin shells over the underlying helper —
the heavy lifting lives in the SDK so the same flow works
programmatically (e.g. inside an app's lifespan).

Resolution order for the database URL:

1. ``--database-url`` flag when given.
2. The ``DATABASE_URL`` env var.
3. A settings instance on the project's ``core.settings`` module,
   under either code root (``src`` or ``app``). Any
   ``pydantic_settings.BaseSettings`` instance qualifies — the
   names ``settings`` and ``config`` are tried first, then the
   module is scanned, because the instance's *type* is the real
   test and its *name* is a convention no service signed up for.
4. ``DATABASE_URL`` in the project's ``.env``, which is where the
   value actually lives in development.
5. The ``sqlalchemy.url`` written in ``alembic.ini``.

When every source comes up empty, each attempt reports why on
stderr. The previous ``except Exception: return None`` erased the
one clue available — an ``ImportError`` naming the attribute it
could not find — and left an error message pointing at two
conditions the project already satisfied.

The async driver suffix (``+asyncpg`` / ``+aiosqlite``) is
stripped automatically before Alembic runs.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

import typer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.ext.asyncio import AsyncSession


_CODE_ROOTS: tuple[str, ...] = ("src", "app")
"""The two directory names a service may use as its code root."""

_SETTINGS_ATTRS: tuple[str, ...] = ("settings", "config")
"""Instance names tried before scanning the module by type."""

_NO_DATABASE_URL_HELP: str = (
    "error: no database URL. Pass --database-url, set DATABASE_URL, put it in "
    ".env, or expose a settings instance with a DATABASE_URL on "
    "src/core/settings.py (or app/core/settings.py)."
)
"""Guidance printed when every resolution source came up empty."""


def _dotenv_database_url(env_file: Path) -> str | None:
    """Read ``DATABASE_URL`` out of a ``.env`` file.

    ``pydantic-settings`` treats ``.env`` as a first-class source and
    every service scaffolded by this SDK points at one, so a project
    with the value only in the file is the normal development case —
    checking ``os.environ`` alone reported "no database URL" to someone
    looking at the URL in their editor.

    Only the assignment form is honored: ``KEY=value``, optionally
    ``export``-prefixed, optionally quoted. Interpolation and multi-line
    values are not — a value needing either belongs in the environment.

    Args:
        env_file (Path): Path to the candidate ``.env``.

    Returns:
        str | None: The value, or ``None`` when the file is absent or
        carries no usable assignment.
    """
    try:
        lines: list[str] = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in lines:
        line: str = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        if key.strip() != "DATABASE_URL":
            continue
        candidate: str = value.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] in "\"'":
            candidate = candidate[1:-1]
        if candidate:
            return candidate
    return None


def _settings_instances(module: object) -> list[object]:
    """List the settings instances a project module exposes.

    The preferred names come first so a module holding more than one
    instance resolves predictably; the remainder is whatever else on the
    module is a ``BaseSettings`` instance, in definition order.

    Args:
        module (object): The imported ``core.settings`` module.

    Returns:
        list[object]: Candidate instances, best first. Empty when the
        module exposes none.
    """
    from pydantic_settings import BaseSettings

    members: dict[str, Any] = vars(module)
    found: list[object] = []
    seen: set[int] = set()
    for name in _SETTINGS_ATTRS:
        value = members.get(name)
        if isinstance(value, BaseSettings):
            found.append(value)
            seen.add(id(value))
    for name, value in members.items():
        if name.startswith("__") or id(value) in seen:
            continue
        if isinstance(value, BaseSettings):
            found.append(value)
            seen.add(id(value))
    return found


def _project_settings_database_url(cwd: Path, notes: list[str]) -> str | None:
    """Import the project's settings module and read the URL off it.

    Args:
        cwd (Path): The project root, which is put on ``sys.path`` so
            ``src``/``app`` import as top-level packages.
        notes (list[str]): Collector for diagnostics, printed by the
            caller only when the whole resolution fails.

    Returns:
        str | None: The URL, or ``None`` when no code root has a
        settings module exposing a non-empty one.
    """
    for root in _CODE_ROOTS:
        if not (cwd / root / "core" / "settings.py").is_file():
            continue
        if str(cwd) not in sys.path:
            sys.path.insert(0, str(cwd))
        dotted: str = f"{root}.core.settings"
        try:
            module = importlib.import_module(dotted)
        except Exception as error:
            notes.append(f"note: importing {dotted} failed: {error!r}")
            continue
        candidates: list[object] = _settings_instances(module)
        if not candidates:
            notes.append(
                f"note: {dotted} imported, but exposes no "
                f"pydantic_settings.BaseSettings instance."
            )
            continue
        for candidate in candidates:
            url = getattr(candidate, "DATABASE_URL", None)
            if isinstance(url, str) and url:
                return url
        notes.append(
            f"note: {dotted} exposes a settings instance, but its "
            f"DATABASE_URL is empty or absent."
        )
    return None


def _resolve_database_url(explicit: str | None) -> str | None:
    """Pick the DB URL using the documented priority order.

    Args:
        explicit (str | None): Value passed via ``--database-url``.

    Returns:
        str | None: The chosen URL, or ``None`` to let
        :class:`AlembicHelper` fall back to ``alembic.ini``. Every
        attempt that failed is reported on stderr before ``None`` is
        returned, so the caller's guidance arrives with the cause.
    """
    if explicit:
        return explicit
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    cwd = Path.cwd()
    notes: list[str] = []
    from_settings: str | None = _project_settings_database_url(cwd, notes)
    if from_settings:
        return from_settings
    from_dotenv: str | None = _dotenv_database_url(cwd / ".env")
    if from_dotenv:
        return from_dotenv
    for note in notes:
        typer.echo(note, err=True)
    return None


def _fail_no_database_url() -> NoReturn:
    """Print the resolution guidance and abort with exit code 2.

    Raises:
        typer.Exit: Always, with code ``2``.
    """
    typer.echo(_NO_DATABASE_URL_HELP, err=True)
    raise typer.Exit(2)


def _helper(
    alembic_ini: str,
    database_url: str | None,
) -> object:
    """Build the :class:`AlembicHelper` for the active project.

    Imported lazily so ``tempest --help`` doesn't pay the SQLAlchemy
    import cost.

    Args:
        alembic_ini (str): Path to ``alembic.ini``.
        database_url (str | None): Override for the URL written
            in the ini.

    Returns:
        object: The instantiated helper.

    Raises:
        typer.Exit: When ``alembic.ini`` is missing.

    Notes:
        Alembic's ``env.py`` imports the project's models from the working
        directory (``src.db.models``), so the cwd is put on ``sys.path``
        before the helper runs. Without it ``alembic upgrade`` fails with
        ``ModuleNotFoundError: src``.
    """
    from tempest_fastapi_sdk import AlembicHelper

    ini = Path(alembic_ini).resolve()
    if not ini.is_file():
        typer.echo(
            f"error: {ini} not found. Run `tempest db init` first or pass --ini.",
            err=True,
        )
        raise typer.Exit(2)
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    return AlembicHelper(str(ini), db_url=database_url)


_ALEMBIC_ADVICE: tuple[tuple[str, str], ...] = (
    (
        "Target database is not up to date.",
        "the database is behind head — a migration is pending. "
        "Run `tempest db upgrade`, then try again.",
    ),
    (
        "Multiple head revisions are present",
        "the history has more than one head. Run `tempest db history` and "
        "resolve it with a merge or a stamp.",
    ),
    (
        "Can't locate revision identified by",
        "that revision is not in `alembic/versions/`. "
        "Run `tempest db history` to list the ones that exist.",
    ),
    (
        "does not refer to ancestor/descendant revisions along the same branch",
        "those two revisions are not on the same branch. "
        "Run `tempest db history` to see the lineage.",
    ),
)
"""Substring of an Alembic ``CommandError`` mapped to what to do about it.

Every entry is an **operational** condition, not a programming error: the
database is behind, the history forked, the revision was deleted. Matched by
substring and in order, because Alembic interpolates the offending revision
into three of the four messages. Read from alembic 1.18.4
(``autogenerate/api.py:601`` and ``script/base.py:214,222,236``); a message
that matches nothing is printed verbatim, so a new one degrades to Alembic's
own words rather than to a wrong hint.
"""


@contextmanager
def _alembic_errors() -> Iterator[None]:
    """Report an expected Alembic failure as one actionable line.

    ``CommandError`` is how Alembic says "the database is not in the state
    this command needs" — a condition the operator fixes, not a bug. Letting
    it reach Typer's ``pretty_exceptions`` prints ~20 frames of alembic,
    asyncio, greenlet and the project's own ``env.py`` before the single
    useful line, with this package's path at the top, so the failure reads
    as an SDK defect.

    ``TEMPEST_DEBUG=1`` re-raises untouched, for the case where the
    traceback is the point.

    Yields:
        None: For the duration of the Alembic call.

    Raises:
        typer.Exit: Code 1, after printing the advice.
    """
    from alembic.util.exc import CommandError

    try:
        yield
    except CommandError as exc:
        if os.environ.get("TEMPEST_DEBUG"):
            raise
        message = str(exc)
        advice = next(
            (text for needle, text in _ALEMBIC_ADVICE if needle in message),
            message,
        )
        typer.echo(f"error: {advice}", err=True)
        typer.echo("       (TEMPEST_DEBUG=1 for the full traceback)", err=True)
        raise typer.Exit(1) from exc


def _load_seed_callable(spec: str) -> Callable[[AsyncSession], Any]:
    """Import a ``module.path:callable`` seed entry point.

    Args:
        spec (str): Dotted spec ``"module.path:callable"``.

    Returns:
        Callable[[AsyncSession], Any]: The seed callable (sync or async).

    Raises:
        typer.Exit: When the spec is malformed, the import fails, or the
            attribute is not callable.
    """
    module_path, _, attr = spec.partition(":")
    if not module_path or not attr:
        typer.echo(
            f"error: --seed must be 'module.path:callable', got {spec!r}",
            err=True,
        )
        raise typer.Exit(2)
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        typer.echo(f"error: cannot import {module_path!r}: {exc}", err=True)
        raise typer.Exit(2) from exc
    seed: object = getattr(module, attr, None)
    if not callable(seed):
        typer.echo(
            f"error: {spec} is not callable (got {type(seed).__name__}).",
            err=True,
        )
        raise typer.Exit(2)
    return cast("Callable[[AsyncSession], Any]", seed)


async def _run_seed(database_url: str, seed: Callable[[AsyncSession], Any]) -> Any:
    """Open one managed session and run the seed callable in it.

    Args:
        database_url (str): The resolved database URL.
        seed (Callable[[AsyncSession], Any]): The seed callable; awaited
            when it returns a coroutine.

    Returns:
        Any: Whatever the seed callable returns (e.g. a row count), or
        ``None``.
    """
    from tempest_fastapi_sdk import AsyncDatabaseManager

    manager = AsyncDatabaseManager(database_url)
    await manager.connect()
    try:
        async with manager.get_session_context() as session:
            result = seed(session)
            if inspect.isawaitable(result):
                result = await result
            return result
    finally:
        await manager.disconnect()


db_app: typer.Typer = typer.Typer(
    name="db",
    help="Alembic migration helpers (create / apply / inspect).",
    no_args_is_help=True,
)


@db_app.command("init")
def db_init(
    script_location: str = typer.Option(
        "alembic",
        "--script-location",
        help="Directory the alembic env will live in.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL stamped in the generated alembic.ini.",
    ),
    metadata_import: str = typer.Option(
        "src.db.models",
        "--metadata-import",
        help=(
            "Dotted module exposing ``BaseModel.metadata`` for Alembic's "
            "autogenerate. Defaults to the scaffolded layout."
        ),
    ),
) -> None:
    """Scaffold a fresh ``alembic.ini`` + ``alembic/env.py``.

    Safe to call multiple times — refuses to overwrite an existing
    ``alembic.ini`` (Alembic itself errors out, surfaced verbatim).
    """
    from tempest_fastapi_sdk import AlembicHelper

    resolved_url = _resolve_database_url(database_url) or (
        "sqlite+aiosqlite:///./app.db"
    )
    helper = AlembicHelper("alembic.ini", db_url=resolved_url)
    helper.init(
        directory=script_location,
        metadata_module=metadata_import,
        db_url=resolved_url,
    )
    typer.echo(f"Initialized Alembic at {Path(script_location).resolve()}")


@db_app.command("revision")
def db_revision(
    message: str = typer.Option(
        ...,
        "-m",
        "--message",
        help="Short description used to name the migration file.",
    ),
    autogenerate: bool = typer.Option(
        True,
        "--autogenerate/--manual",
        help="Diff ORM metadata against the DB and emit the migration.",
    ),
    ini: str = typer.Option(
        "alembic.ini",
        "--ini",
        help="Path to alembic.ini.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Create a new migration file under ``alembic/versions/``.

    Autogenerate diffs ``BaseModel.metadata`` against the live
    database — the user table will only appear when
    ``src/db/models/__init__.py`` imports it.
    """
    helper = _helper(ini, _resolve_database_url(database_url))
    with _alembic_errors():
        helper.revision(message=message, autogenerate=autogenerate)  # type: ignore[attr-defined]
    typer.echo(f"Created revision: {message}")


@db_app.command("upgrade")
def db_upgrade(
    target: str = typer.Argument(
        "head",
        help="Target revision. Default ``head`` applies every pending migration.",
    ),
    ini: str = typer.Option(
        "alembic.ini",
        "--ini",
        help="Path to alembic.ini.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Apply migrations up to ``target`` (``head`` by default)."""
    helper = _helper(ini, _resolve_database_url(database_url))
    with _alembic_errors():
        helper.upgrade(target)  # type: ignore[attr-defined]
    typer.echo(f"Upgraded to {target}.")


@db_app.command("downgrade")
def db_downgrade(
    target: str = typer.Argument(
        "-1",
        help="Target revision. Default ``-1`` rolls back one step.",
    ),
    ini: str = typer.Option(
        "alembic.ini",
        "--ini",
        help="Path to alembic.ini.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Roll back migrations toward ``target`` (default one step)."""
    helper = _helper(ini, _resolve_database_url(database_url))
    with _alembic_errors():
        helper.downgrade(target)  # type: ignore[attr-defined]
    typer.echo(f"Downgraded to {target}.")


@db_app.command("current")
def db_current(
    ini: str = typer.Option(
        "alembic.ini",
        "--ini",
        help="Path to alembic.ini.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Print the revision currently applied to the database."""
    helper = _helper(ini, _resolve_database_url(database_url))
    with _alembic_errors():
        current = helper.current()  # type: ignore[attr-defined]
    typer.echo(current or "(no revision applied)")


@db_app.command("squash")
def db_squash(
    message: str = typer.Option(
        "squash",
        "-m",
        "--message",
        help="Message/slug for the new root migration.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm: drops every table in the target DB before regenerating.",
    ),
    backup: bool = typer.Option(
        True,
        "--backup/--no-backup",
        help=(
            "Move old revisions to versions/_squashed_<oldhead>/ instead of "
            "deleting them."
        ),
    ),
    ini: str = typer.Option(
        "alembic.ini",
        "--ini",
        help="Path to alembic.ini.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Collapse the whole migration history into one fresh root revision.

    Migrations grow without bound as the project evolves. This drops the
    configured (development) database, regenerates a single migration
    from the current models, and re-applies it. Old revision files are
    moved to ``versions/_squashed_<oldhead>/`` unless ``--no-backup``.

    Existing production databases are untouched — after deploying the
    squashed tree, stamp them with ``tempest db stamp head``.
    """
    if not yes:
        typer.echo(
            "error: `db squash` drops every table in the target database to "
            "regenerate a single migration. Re-run with --yes once you have "
            "confirmed DATABASE_URL points at a development database.",
            err=True,
        )
        raise typer.Exit(2)
    helper = _helper(ini, _resolve_database_url(database_url))
    new_rev = helper.squash(message=message, force=True, backup=backup)  # type: ignore[attr-defined]
    typer.echo(f"Squashed history into new root revision {new_rev}.")
    typer.echo(
        "Production databases: deploy this tree, then run "
        "`tempest db stamp head` to mark them as migrated.",
    )


@db_app.command("stamp")
def db_stamp(
    revision: str = typer.Argument(
        "head",
        help="Revision to stamp. Default ``head``.",
    ),
    purge: bool = typer.Option(
        False,
        "--purge",
        help=(
            "Clear alembic_version before stamping. Needed when the recorded "
            "revision no longer exists in the tree (e.g. after a manual squash)."
        ),
    ),
    ini: str = typer.Option(
        "alembic.ini",
        "--ini",
        help="Path to alembic.ini.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Stamp the database at ``revision`` without running migrations.

    Use on an already-populated database (e.g. production after a
    squash) so Alembic records it as migrated without recreating tables.
    Pass ``--purge`` when the recorded revision is no longer in the
    script directory, so a plain stamp would fail to resolve it.
    """
    helper = _helper(ini, _resolve_database_url(database_url))
    with _alembic_errors():
        helper.stamp(revision, purge=purge)  # type: ignore[attr-defined]
    typer.echo(f"Stamped database at {revision}.")


@db_app.command("backup")
def db_backup(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Destination file. Defaults to backups/<db>_<timestamp>.<ext>. "
            "For Postgres the format is inferred from the extension: "
            ".dump → custom (pg_dump -Fc), .sql → plain."
        ),
    ),
    plain: bool | None = typer.Option(
        None,
        "--plain/--custom",
        help=(
            "Force the Postgres dump format instead of inferring from the "
            "extension. Ignored for SQLite."
        ),
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Dump the database to a file.

    PostgreSQL is dumped via ``pg_dump`` (custom ``-Fc`` by default, or
    plain ``.sql``); SQLite is copied. The written path is printed.
    """
    url = _resolve_database_url(database_url)
    if url is None:
        _fail_no_database_url()
    from tempest_fastapi_sdk.db.backup import DatabaseBackup

    written = DatabaseBackup(url).backup(output, plain=plain)
    typer.echo(f"Backed up to {written}.")


@db_app.command("restore")
def db_restore(
    source: Path = typer.Argument(
        ...,
        help="Backup file to restore from (.dump → pg_restore, .sql → psql).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm: drops existing objects in the target DB before restoring.",
    ),
    no_clean: bool = typer.Option(
        False,
        "--no-clean",
        help="Apply the dump without dropping existing objects first.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Restore the database from a backup file.

    By default this is a clean restore — existing objects are dropped
    and recreated so the result is a faithful copy of the backup. Pass
    ``--no-clean`` to apply the dump on top of the current schema.
    """
    if not yes:
        typer.echo(
            "error: `db restore` overwrites the target database. Re-run with "
            "--yes once you have confirmed DATABASE_URL points at the right "
            "database.",
            err=True,
        )
        raise typer.Exit(2)
    url = _resolve_database_url(database_url)
    if url is None:
        _fail_no_database_url()
    from tempest_fastapi_sdk.db.backup import DatabaseBackup

    DatabaseBackup(url).restore(source, clean=not no_clean)
    typer.echo(f"Restored from {source}.")


@db_app.command("seed")
def db_seed(
    seed_spec: str = typer.Option(
        "src.db.seeds:seed",
        "--seed",
        "-s",
        help=(
            "Dotted 'module.path:callable' to run. The callable receives "
            "one positional AsyncSession and may be sync or async. Defaults "
            "to the scaffolded 'src.db.seeds:seed'."
        ),
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Run a project seed callable inside one managed session.

    Builds an :class:`AsyncDatabaseManager` from the resolved URL, opens
    a session (committed on success, rolled back on error), and invokes
    the dotted callable with it. The callable owns what gets inserted —
    the SDK only wires the session lifecycle so seeding looks the same
    across every service.
    """
    url = _resolve_database_url(database_url)
    if url is None:
        _fail_no_database_url()
    seed_callable = _load_seed_callable(seed_spec)
    result = asyncio.run(_run_seed(url, seed_callable))
    suffix = f" ({result} rows)" if isinstance(result, int) else ""
    typer.echo(f"Seeded via {seed_spec}{suffix}.")


@db_app.command("history")
def db_history(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Include the full message body for each revision.",
    ),
    ini: str = typer.Option(
        "alembic.ini",
        "--ini",
        help="Path to alembic.ini.",
    ),
    database_url: str | None = typer.Option(
        None,
        "--database-url",
        help="Override DATABASE_URL for this run.",
    ),
) -> None:
    """Print the migration history (newest → oldest)."""
    helper = _helper(ini, _resolve_database_url(database_url))
    with _alembic_errors():
        typer.echo(helper.history(verbose=verbose))  # type: ignore[attr-defined]


__all__: list[str] = [
    "db_app",
]
