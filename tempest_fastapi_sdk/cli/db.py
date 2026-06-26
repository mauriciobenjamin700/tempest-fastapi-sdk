"""``tempest db`` — Alembic migration helpers driven from the CLI.

Wraps :class:`tempest_fastapi_sdk.AlembicHelper` so the project's
``alembic.ini`` + ``alembic/env.py`` stay the single source of
truth. All commands are thin shells over the underlying helper —
the heavy lifting lives in the SDK so the same flow works
programmatically (e.g. inside an app's lifespan).

Resolution order for the database URL:

1. ``--database-url`` flag when given.
2. The ``DATABASE_URL`` env var.
3. The project's ``src.core.settings.settings.DATABASE_URL``
   (when the scaffolded layout is detected).
4. The ``sqlalchemy.url`` written in ``alembic.ini``.

The async driver suffix (``+asyncpg`` / ``+aiosqlite``) is
stripped automatically before Alembic runs.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import typer

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession


def _resolve_database_url(explicit: str | None) -> str | None:
    """Pick the DB URL using the documented priority order.

    Args:
        explicit (str | None): Value passed via ``--database-url``.

    Returns:
        str | None: The chosen URL, or ``None`` to let
        :class:`AlembicHelper` fall back to ``alembic.ini``.
    """
    if explicit:
        return explicit
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    cwd = Path.cwd()
    if (cwd / "src" / "core" / "settings.py").is_file():
        sys.path.insert(0, str(cwd))
        try:
            from src.core.settings import settings  # type: ignore[import-not-found]

            url = getattr(settings, "DATABASE_URL", None)
            if isinstance(url, str) and url:
                return url
        except Exception:
            return None
    return None


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
    """
    from tempest_fastapi_sdk import AlembicHelper

    ini = Path(alembic_ini).resolve()
    if not ini.is_file():
        typer.echo(
            f"error: {ini} not found. Run `tempest db init` first or pass --ini.",
            err=True,
        )
        raise typer.Exit(2)
    # Alembic's env.py imports the project's models from the cwd
    # (``src.db.models``). Ensure the cwd is on sys.path before the
    # helper runs, otherwise ``alembic upgrade`` fails with
    # ``ModuleNotFoundError: src``.
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    return AlembicHelper(str(ini), db_url=database_url)


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
    """
    helper = _helper(ini, _resolve_database_url(database_url))
    helper.stamp(revision)  # type: ignore[attr-defined]
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
        typer.echo(
            "error: no database URL. Pass --database-url, set DATABASE_URL, "
            "or run inside a project with src/core/settings.py.",
            err=True,
        )
        raise typer.Exit(2)
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
        typer.echo(
            "error: no database URL. Pass --database-url, set DATABASE_URL, "
            "or run inside a project with src/core/settings.py.",
            err=True,
        )
        raise typer.Exit(2)
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
        typer.echo(
            "error: no database URL. Pass --database-url, set DATABASE_URL, "
            "or run inside a project with src/core/settings.py.",
            err=True,
        )
        raise typer.Exit(2)
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
    typer.echo(helper.history(verbose=verbose))  # type: ignore[attr-defined]


__all__: list[str] = [
    "db_app",
]
