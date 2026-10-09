"""The body of the ``env.py`` that ``tempest db init`` generates.

The generated ``alembic/env.py`` is three statements: import
:func:`run_alembic_env`, import the project's ``BaseModel``, and call
``run_alembic_env(BaseModel.metadata)``. Everything else lives here, for
two reasons measured on real services:

* **A copied environment script drifts.** A service that rewrote the
  generated file by hand kept its own copy of the SDK's logic, so later
  fixes to it never arrived — and the rewrite silently dropped the
  ``sys.path`` handling, which broke ``alembic downgrade`` outside the
  ``tempest`` CLI. A call into the SDK picks up every fix on upgrade.
* **A full script cannot be lint-clean under every ruff configuration.**
  The ``alembic/`` directory next to ``pyproject.toml`` makes ruff's
  default ``src = [".", "src"]`` classify ``alembic`` as first-party,
  while a ``src = ["src", "tests"]`` project (the ``tempest new``
  scaffold) classifies it as third-party; ``from alembic import context``
  sorts into a different import block under each, so one of the two
  always reports ``I001``. The generated file imports no ``alembic`` at
  all, and sorts the same under both.
"""

from __future__ import annotations

import asyncio
import configparser
import os
import sys
from collections.abc import Sequence
from logging.config import fileConfig
from pathlib import Path
from typing import Any, Final

from alembic import context
from alembic.config import Config
from sqlalchemy import MetaData, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from tempest_fastapi_sdk.db.alembic_hooks import (
    ProcessRevisionDirectives,
    backfill_non_nullable_defaults,
    compose_hooks,
    reorder_base_columns_first,
)
from tempest_fastapi_sdk.db.enum_migrations import (
    drop_enum_types_on_downgrade,
    render_enum_types,
    sync_enum_types,
)
from tempest_fastapi_sdk.db.migrations import require_sqlite_foreign_keys_off

DEFAULT_REVISION_HOOKS: Final[tuple[ProcessRevisionDirectives, ...]] = (
    reorder_base_columns_first,
    backfill_non_nullable_defaults,
    sync_enum_types,
    drop_enum_types_on_downgrade,
)
"""The ``process_revision_directives`` hooks every revision goes through.

In order:

- ``reorder_base_columns_first``: ``id``, ``is_active``, ``created_at``
  and ``updated_at`` come first in every ``create_table``.
- ``backfill_non_nullable_defaults``: a ``NOT NULL`` column added to a
  table with rows gets a ``server_default`` from its scalar Python
  default, so the migration backfills existing rows instead of raising
  ``NotNullViolationError``.
- ``sync_enum_types``: a changed enum member list becomes a
  ``replace_enum`` operation. Alembic compares neither ``pg_enum``
  (PostgreSQL) nor the ``CHECK`` constraint (SQLite), so without it,
  adding a member to a Python enum autogenerates an empty migration.
- ``drop_enum_types_on_downgrade``: the downgrade of a new table also
  drops the PostgreSQL ``ENUM`` types no surviving table uses, so a
  ``downgrade`` followed by an ``upgrade`` does not fail with
  ``type ... already exists``.

Extend it with ``compose_hooks(*DEFAULT_REVISION_HOOKS, my_hook)`` and
pass the result as ``process_revision_directives=``.
"""


def run_alembic_env(
    target_metadata: MetaData | Sequence[MetaData] | None,
    *,
    process_revision_directives: ProcessRevisionDirectives | None = None,
) -> None:
    """Run the current Alembic command the way the SDK configures it.

    Call it as the last statement of ``alembic/env.py``. It resolves the
    database URL, applies ``alembic.ini``'s logging sections only when
    they exist, and runs the migrations offline (``--sql``) or online
    with ``compare_type``, ``compare_server_default``, batch mode,
    :func:`~tempest_fastapi_sdk.db.enum_migrations.render_enum_types` as
    ``render_item`` and the revision hooks.

    The URL comes from, in order: the ``sqlalchemy.url`` already on the
    config (``--database-url``, or ``AlembicHelper(db_url=...)``); the
    ``DATABASE_URL`` environment variable; and
    ``src.core.settings.settings.DATABASE_URL`` when
    ``src/core/settings.py`` exists in the working directory, which is
    put on ``sys.path`` first. ``alembic.ini`` never has to hold a
    secret.

    Online, three entry points are checked in order:

    1. A caller that already holds a connection hands it over as
       ``config.attributes["connection"]`` (Alembic's connection-sharing
       recipe, typically from inside ``AsyncConnection.run_sync``), and
       the migrations run on it: no engine and no event loop of our own.
       A SQLite connection that enforces foreign keys is refused first
       (:func:`~tempest_fastapi_sdk.db.migrations.require_sqlite_foreign_keys_off`):
       batch mode recreates tables, and dropping a parent's old copy
       cascades to its children.
    2. No event loop is running in this thread (the CLI, the sync
       ``AlembicHelper`` methods, or its ``*_async`` methods, which run
       in a worker thread): build an async engine under ``asyncio.run``.
    3. An event loop is running and no connection was handed over: raise
       before creating the coroutine, because ``asyncio.run`` cannot nest
       and would leave the migration coroutine un-awaited.

    Args:
        target_metadata (MetaData | Sequence[MetaData] | None): The
            autogenerate target, usually ``BaseModel.metadata``. ``None``
            disables autogenerate comparisons.
        process_revision_directives (ProcessRevisionDirectives | None):
            The hook every autogenerated revision goes through. ``None``
            composes :data:`DEFAULT_REVISION_HOOKS`.

    Raises:
        RuntimeError: When no source provides a database URL; in case 3;
            and in case 1 when the connection is SQLite with
            ``PRAGMA foreign_keys=ON``.
    """
    config = context.config
    url = _resolve_database_url(config)
    if not url:
        raise RuntimeError(
            "DATABASE_URL is empty. Set it on the environment, in "
            "src/core/settings.py, or pass --database-url to the CLI."
        )
    config.set_main_option("sqlalchemy.url", url)
    _configure_logging(config)

    options: dict[str, Any] = {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "render_as_batch": True,
        "render_item": render_enum_types,
        "process_revision_directives": (
            process_revision_directives or compose_hooks(*DEFAULT_REVISION_HOOKS)
        ),
    }
    if context.is_offline_mode():
        _run_offline(url, options)
    else:
        _run_online(config, options)


def _resolve_database_url(config: Config) -> str | None:
    """Pick the database URL at runtime so secrets never sit in alembic.ini.

    Args:
        config (Config): The running command's Alembic config.

    Returns:
        str | None: The URL, or ``None`` when no source provides one.
    """
    existing = config.get_main_option("sqlalchemy.url")
    if existing:
        return existing
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    cwd = Path.cwd()
    if not (cwd / "src" / "core" / "settings.py").is_file():
        return None
    if str(cwd) not in sys.path:
        sys.path.insert(0, str(cwd))
    try:
        from src.core.settings import settings  # type: ignore[import-not-found]
    except Exception:
        return None
    url = getattr(settings, "DATABASE_URL", None)
    return url if isinstance(url, str) and url else None


def _configure_logging(config: Config) -> None:
    """Apply alembic.ini's logging sections, only when they exist.

    The SDK-generated ``alembic.ini`` omits ``[loggers]``/``[handlers]``/
    ``[formatters]`` so a migration runs without touching the host's
    logging tree; a project that re-adds a ``[loggers]`` block keeps it
    working through this guarded call. ``disable_existing_loggers=False``
    is what keeps the application's loggers alive: the default ``True``
    wipes every SDK logger configured via ``configure_logging`` (the 500
    handler, the ``/logs`` writer, the request-ID context).

    Args:
        config (Config): The running command's Alembic config.
    """
    if config.config_file_name is None:
        return
    ini = configparser.ConfigParser()
    try:
        ini.read(config.config_file_name, encoding="utf-8")
    except (OSError, configparser.Error):
        return
    if ini.has_section("loggers"):
        fileConfig(config.config_file_name, disable_existing_loggers=False)


def _run_offline(url: str, options: dict[str, Any]) -> None:
    """Run migrations in offline mode, emitting SQL to stdout.

    Args:
        url (str): The database URL, which picks the SQL dialect.
        options (dict[str, Any]): The ``context.configure`` options
            shared with online mode.
    """
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **options,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_on_connection(connection: Connection, options: dict[str, Any]) -> None:
    """Configure Alembic on an open connection and run the migrations.

    Args:
        connection (Connection): The connection the migrations run on.
        options (dict[str, Any]): The ``context.configure`` options.
    """
    context.configure(connection=connection, **options)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations(config: Config, options: dict[str, Any]) -> None:
    """Build the async migration engine and run the migrations on it.

    Args:
        config (Config): The running command's Alembic config.
        options (dict[str, Any]): The ``context.configure`` options.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_on_connection, options)
    await connectable.dispose()


def _run_online(config: Config, options: dict[str, Any]) -> None:
    """Run migrations online through one of the three entry points.

    Args:
        config (Config): The running command's Alembic config.
        options (dict[str, Any]): The ``context.configure`` options.

    Raises:
        RuntimeError: When an event loop is running and no connection
            was handed over, or the handed-over connection is SQLite
            with foreign keys on.
    """
    shared = config.attributes.get("connection")
    if isinstance(shared, Connection):
        require_sqlite_foreign_keys_off(shared)
        _run_on_connection(shared, options)
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_run_async_migrations(config, options))
        return
    raise RuntimeError(
        "alembic/env.py was executed from a running event loop and cannot "
        "call asyncio.run() there. Await the AlembicHelper *_async method "
        "(e.g. `await helper.upgrade_async()`), or pass an open connection "
        'as config.attributes["connection"] from AsyncConnection.run_sync.'
    )


__all__: list[str] = [
    "DEFAULT_REVISION_HOOKS",
    "run_alembic_env",
]
