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

import os
import sys
from pathlib import Path

import typer


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
