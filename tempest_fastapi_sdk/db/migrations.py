"""Alembic command wrappers and environment-init helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import redirect_stdout
from importlib import resources
from io import StringIO
from pathlib import Path
from typing import Any, TypeVar

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import NoSuchModuleError

from tempest_fastapi_sdk.core.enums import BaseStrEnum

_T = TypeVar("_T")

# Alembic operation calls that destroy data. Matched against the source
# of each pending migration's ``upgrade()`` so :meth:`AlembicHelper.safe_upgrade`
# can refuse them without ``force=True``.
_DESTRUCTIVE_OPS: tuple[str, ...] = (
    "op.drop_table(",
    "op.drop_column(",
    "batch_op.drop_column(",
    "op.drop_constraint(",
)


class DestructiveMigrationError(RuntimeError):
    """Raised when a pending migration would drop a table/column/constraint.

    :meth:`AlembicHelper.safe_upgrade` raises this instead of running the
    migration, unless the caller passes ``force=True``. Carries the list
    of offending ``(revision, operation)`` pairs so the caller can log
    exactly what was blocked.

    Attributes:
        offences (list[tuple[str, str]]): ``(revision_id, operation)``
            pairs that tripped the guard.
    """

    def __init__(self, offences: list[tuple[str, str]]) -> None:
        """Initialize.

        Args:
            offences (list[tuple[str, str]]): The blocked operations as
                ``(revision_id, operation)`` pairs.
        """
        self.offences: list[tuple[str, str]] = offences
        detail = ", ".join(f"{rev}: {op}" for rev, op in offences)
        super().__init__(
            f"refusing to run destructive migration(s) without force=True: {detail}"
        )


class AmbiguousBaseRevisionError(RuntimeError):
    """Raised when a migration tree has no single root to adopt at.

    :meth:`AlembicHelper.base_revision` needs exactly one base. Zero means
    the project has no revisions yet, so there is nothing to stamp; more
    than one means the tree was merged from independent roots and only a
    human knows which one describes the schema already on disk.

    Attributes:
        bases (list[str]): The roots Alembic reported.
    """

    def __init__(self, bases: list[str]) -> None:
        """Initialize.

        Args:
            bases (list[str]): The roots Alembic reported.
        """
        self.bases: list[str] = bases
        super().__init__(f"expected exactly one base revision, found {bases or 'none'}")


class SchemaSyncOutcome(BaseStrEnum):
    """What :meth:`AlembicHelper.sync_schema` did.

    Attributes:
        NO_MIGRATIONS (str): The project has no revisions yet, so the
            database was left alone.
        ADOPTED (str): A schema that predates Alembic was stamped at the
            base revision and then upgraded.
        SYNCED (str): The ordinary path — the database was already under
            Alembic (or empty) and was upgraded to head.
    """

    NO_MIGRATIONS = "no_migrations"
    ADOPTED = "adopted"
    SYNCED = "synced"


def _upgrade_section(source: str) -> str:
    """Return the ``def upgrade()`` body slice of a migration's source.

    Slices from ``def upgrade`` up to the next ``def downgrade`` so a
    ``drop_*`` call in the (expected) downgrade path never counts as a
    destructive upgrade.

    Args:
        source (str): The full migration module source.

    Returns:
        str: The upgrade-function slice, or the whole source when the
        markers are not found.
    """
    start = source.find("def upgrade")
    if start == -1:
        return source
    end = source.find("def downgrade", start)
    return source[start:] if end == -1 else source[start:end]


def _resolve_runtime_database_url() -> str | None:
    """Read ``DATABASE_URL`` from env / scaffolded settings.

    Used by :attr:`AlembicHelper.config` when ``sqlalchemy.url`` is
    blank in ``alembic.ini`` (the SDK default since v0.30.2 — keeps
    credentials out of version control). Both ``current()`` /
    ``upgrade()`` etc. and the bundled ``env.py`` template share
    this resolver so CLI commands and in-process callers agree on
    the URL.

    Returns:
        str | None: The resolved URL, or ``None`` when neither
        source is set.
    """
    import os
    import sys
    from pathlib import Path

    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    cwd = Path.cwd()
    if (cwd / "src" / "core" / "settings.py").is_file():
        if str(cwd) not in sys.path:
            sys.path.insert(0, str(cwd))
        try:
            from src.core.settings import settings  # type: ignore[import-not-found]

            url = getattr(settings, "DATABASE_URL", None)
            if isinstance(url, str) and url:
                return url
        except Exception:
            return None
    return None


def _refuse_running_loop(method: str) -> None:
    """Raise when ``method`` is called from a thread running an event loop.

    Every command that executes ``alembic/env.py`` ends in the SDK
    template's ``asyncio.run(run_async_migrations())``, and ``asyncio.run``
    refuses to nest: called from a FastAPI lifespan it raises ``asyncio.run()
    cannot be called from a running event loop`` from deep inside Alembic
    and leaves ``run_async_migrations`` un-awaited, which Python reports as a
    second, unrelated-looking ``RuntimeWarning``. Checking first turns that
    into one error that names the method to call instead.

    Args:
        method (str): Name of the sync :class:`AlembicHelper` method being
            called; the message points at its ``_async`` counterpart.

    Raises:
        RuntimeError: When an event loop is running in the current thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        f"AlembicHelper.{method}() was called from a running event loop. It "
        "runs alembic/env.py, which drives migrations with asyncio.run() and "
        f"cannot nest inside the loop; use `await helper.{method}_async(...)` "
        "instead."
    )


def _strip_async_driver(url: str) -> str:
    """Return a sync flavor of an async database URL.

    Alembic operations (``current``, ``check``) need a sync engine
    because the migration context runs synchronously. This helper
    converts ``postgresql+asyncpg://...`` to ``postgresql://...``,
    ``sqlite+aiosqlite://...`` to ``sqlite://...`` and so on.

    Args:
        url (str): The (possibly async) database URL.

    Returns:
        str: A URL using the sync driver flavor of the same backend.
    """
    parsed = make_url(url)
    drivername = parsed.drivername.split("+", maxsplit=1)[0]
    return parsed.set(drivername=drivername).render_as_string(hide_password=False)


class AlembicHelper:
    """High-level wrapper around the Alembic command surface.

    Encapsulates a single ``alembic.ini`` configuration and exposes
    the operations that matter for day-to-day work — upgrade,
    downgrade, revision authoring, schema-vs-models check — without
    leaking Alembic internals into application code.

    Alembic itself is synchronous, and the SDK's ``env.py`` drives the
    async engine with ``asyncio.run``, so the plain methods are for CLI
    scripts and other code with no event loop running. Code that already
    runs on a loop — a FastAPI lifespan, an admin endpoint — awaits the
    ``*_async`` counterpart instead (:meth:`upgrade_async`,
    :meth:`sync_schema_async`, ...). Each one runs the sync method in a
    worker thread, which has no loop of its own, so ``asyncio.run`` inside
    ``env.py`` works there and the caller's loop keeps serving while the
    migration runs. A sync method that would execute ``env.py`` called from
    a running loop raises a ``RuntimeError`` naming its ``_async``
    counterpart, instead of failing inside Alembic.

    The worker thread is chosen over running the migration on the caller's
    loop through ``AsyncConnection.run_sync`` (Alembic's connection-sharing
    recipe) because it works with every ``env.py`` already generated: that
    recipe needs an ``env.py`` that reads ``config.attributes["connection"]``,
    and the template only learned to in the release that added these
    methods. The template now supports both, so code that must reuse its
    own connection still can — see the migrations recipe.

    Attributes:
        config_path (str): Path to the ``alembic.ini`` configuration.
    """

    def __init__(
        self,
        config_path: str = "alembic.ini",
        *,
        db_url: str | None = None,
    ) -> None:
        """Initialize the helper.

        Args:
            config_path (str): Path to ``alembic.ini``. Resolved
                relative to the current working directory.
            db_url (str | None): If provided, overrides
                ``sqlalchemy.url`` from the ``.ini`` file. Useful
                when the URL must come from settings/environment
                rather than the ini.
        """
        self.config_path: str = config_path
        self._db_url_override: str | None = db_url

    @property
    def config(self) -> Config:
        """Return a fresh :class:`alembic.config.Config` instance.

        A new instance is built on every access so the helper stays
        stateless and safe to share across threads — Alembic mutates
        the config object during command execution.

        ``sqlalchemy.url`` resolution order:

        1. ``db_url`` passed on the constructor (explicit override).
        2. The value already on the ini file.
        3. The ``DATABASE_URL`` environment variable (loaded from
           ``.env`` before invoking the helper).
        4. ``src.core.settings.settings.DATABASE_URL`` when the
           scaffolded layout is detected.

        The SDK-generated ini ships with ``sqlalchemy.url = `` empty
        on purpose so secrets never enter version control — the
        resolution chain above fills it at runtime. That empty default
        has been the SDK's shape since v0.30.2, which is why steps 3
        and 4 exist at all: on a project generated before then the ini
        still carries a URL and step 2 wins.

        Returns:
            Config: The configured Alembic config.
        """
        config = Config(self.config_path)
        if self._db_url_override is not None:
            config.set_main_option("sqlalchemy.url", self._db_url_override)
            return config

        if not config.get_main_option("sqlalchemy.url"):
            resolved = _resolve_runtime_database_url()
            if resolved:
                config.set_main_option("sqlalchemy.url", resolved)
        return config

    def init(
        self,
        directory: str = "alembic",
        *,
        metadata_module: str | None = None,
        metadata_attr: str = "BaseModel",
        db_url: str = "sqlite+aiosqlite:///./app.db",
    ) -> None:
        """Scaffold a new Alembic environment in ``directory``.

        Wraps ``alembic init -t async`` and then overwrites the
        generated ``env.py`` with the SDK's template, which already
        wires the metadata import, sets ``compare_type`` /
        ``compare_server_default`` and enables batch mode for SQLite.

        Three details of the scaffold are deliberate:

        * The ini is pre-seeded at :attr:`config_path` before calling
          ``command.init``, because Alembic writes it wherever
          ``config.config_file_name`` points — seeding is what makes
          the file land where this helper later expects to find it.
        * The ini Alembic wrote is then **replaced** by the SDK layout
          (logger sections, ``file_template``, UTC timestamps), with
          ``sqlalchemy.url`` left empty so credentials never enter
          version control. The companion ``env.py`` template resolves
          the URL at runtime from ``DATABASE_URL`` (or
          ``src.core.settings``) and injects it back into the config
          before the engine is built; pass ``db_url=`` on the
          constructor to override for a one-off (CI smoke, scripted
          migrations).
        * ``[post_write_hooks]`` runs ``ruff format`` **then**
          ``ruff check --fix`` over every generated revision, so the
          files autogenerate emits — long ``sa.Column`` lines, trailing
          whitespace in the header when ``down_revision`` is ``None``
          — are lint-clean out of the box. The order matters: the
          formatter wraps over-length lines (E501) and strips trailing
          whitespace (W291), so running the linter first would report
          errors the formatter is about to fix on the next hook.
          ``--quiet`` keeps the second pass silent when nothing
          actionable remains.

        The generated ini also carries **no**
        ``[loggers]``/``[handlers]``/``[formatters]`` sections, which is
        the one omission worth explaining. ``env.py`` runs inside the
        host application process, where ``configure_logging`` has
        already set up Python's logging tree. Shipping the stock
        ``[logger_root] level = WARN handlers = console`` block would
        make ``fileConfig(alembic.ini)`` reset the root logger to WARN
        plus a stderr handler, silencing the SDK's 500 handler, the
        ``/logs`` writer and every JSON record the app emits. The
        companion ``env.py`` still calls ``fileConfig`` — guarded on the
        section being present — so a project that deliberately re-adds a
        ``[loggers]`` block keeps working.

        Args:
            directory (str): Target directory for ``versions/`` and
                ``env.py``. Created if missing.
            metadata_module (str | None): Dotted module path that
                exposes the SQLAlchemy metadata (e.g. ``"app.db"``).
                When ``None``, the env.py is left with
                ``target_metadata = None`` so the user can wire it
                manually.
            metadata_attr (str): Name of the attribute inside
                ``metadata_module`` whose ``.metadata`` is used as
                the autogenerate target. Defaults to ``"BaseModel"``.
            db_url (str): Value to write under ``sqlalchemy.url`` in
                the generated ``alembic.ini``. Replace later via
                env-var injection or by passing ``db_url`` to the
                constructor.
        """
        ini_path = Path(self.config_path)
        ini_path.parent.mkdir(parents=True, exist_ok=True)
        config = Config(str(ini_path))
        config.set_main_option("script_location", directory)
        config.set_main_option("sqlalchemy.url", db_url)
        command.init(config, directory, template="async")

        env_py = Path(directory) / "env.py"
        template_text = (
            resources.files("tempest_fastapi_sdk.db._alembic_templates")
            .joinpath("env.py.template")
            .read_text(encoding="utf-8")
        )

        if metadata_module is None:
            metadata_import = "target_metadata = None"
        else:
            metadata_import = (
                f"from {metadata_module} import {metadata_attr}\n"
                f"target_metadata = {metadata_attr}.metadata"
            )
        env_py.write_text(
            template_text.replace("__METADATA_IMPORT__", metadata_import),
            encoding="utf-8",
        )

        ini_lines = [
            "[alembic]",
            f"script_location = {directory}",
            "sqlalchemy.url = ",
            (
                "file_template = "
                "%%(year)d_%%(month).2d_%%(day).2d_"
                "%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s"
            ),
            "timezone = UTC",
            "",
            "[post_write_hooks]",
            "hooks = ruff_format, ruff_fix",
            "ruff_format.type = exec",
            "ruff_format.executable = ruff",
            "ruff_format.options = format --quiet REVISION_SCRIPT_FILENAME",
            "ruff_fix.type = exec",
            "ruff_fix.executable = ruff",
            "ruff_fix.options = check --fix --quiet REVISION_SCRIPT_FILENAME",
            "",
        ]
        ini_path.write_text("\n".join(ini_lines), encoding="utf-8")

    def upgrade(self, revision: str = "head") -> None:
        """Apply migrations up to ``revision`` (default: ``head``).

        Args:
            revision (str): Target revision identifier or relative
                spec (``"+1"``). ``"head"`` runs every pending
                migration.

        Raises:
            RuntimeError: When called from a running event loop; await
                :meth:`upgrade_async` there instead.
        """
        _refuse_running_loop("upgrade")
        command.upgrade(self.config, revision)

    async def upgrade_async(self, revision: str = "head") -> None:
        """Run :meth:`upgrade` in a worker thread, for async callers.

        Args:
            revision (str): Target revision identifier or relative spec.
        """
        await asyncio.to_thread(self.upgrade, revision)

    def pending_destructive_ops(self, revision: str = "head") -> list[tuple[str, str]]:
        """Scan migrations pending up to ``revision`` for destructive ops.

        Walks the revisions between the database's current revision and
        ``revision`` and inspects each migration module's source for
        data-destroying Alembic calls (``op.drop_table`` /
        ``op.drop_column`` / ``op.drop_constraint`` and their
        ``batch_op`` variants). Source scanning is dialect-agnostic, so
        it never trips on SQLite's batch table-rebuild SQL the way
        offline-SQL scanning would.

        Args:
            revision (str): The target revision (default ``"head"``).

        Returns:
            list[tuple[str, str]]: ``(revision_id, operation)`` pairs for
            every destructive call found. Empty when the pending range is
            clean.
        """
        script = ScriptDirectory.from_config(self.config)
        current = self.current()
        offences: list[tuple[str, str]] = []
        for revobj in script.iterate_revisions(revision, current):
            try:
                source = Path(revobj.path).read_text(encoding="utf-8")
            except OSError:  # pragma: no cover - unreadable revision file
                continue
            upgrade_src = _upgrade_section(source)
            for op in _DESTRUCTIVE_OPS:
                if op in upgrade_src:
                    offences.append((revobj.revision, op.rstrip("(")))
        return offences

    async def pending_destructive_ops_async(
        self,
        revision: str = "head",
    ) -> list[tuple[str, str]]:
        """Run :meth:`pending_destructive_ops` in a worker thread.

        Args:
            revision (str): The target revision (default ``"head"``).

        Returns:
            list[tuple[str, str]]: ``(revision_id, operation)`` pairs.
        """
        return await asyncio.to_thread(self.pending_destructive_ops, revision)

    def safe_upgrade(self, revision: str = "head", *, force: bool = False) -> None:
        """Upgrade, but refuse destructive migrations unless forced.

        Runs :meth:`pending_destructive_ops` first. If any pending
        migration would drop a table, column or constraint, raises
        :class:`DestructiveMigrationError` and does **not** touch the
        database — unless ``force=True``, which logs the offences and
        proceeds. Use this in automated deploy pipelines so an
        accidental ``DROP COLUMN`` can't silently delete production data.

        Args:
            revision (str): The target revision (default ``"head"``).
            force (bool): When ``True``, run even if destructive ops are
                present (after logging them). Defaults to ``False``.

        Raises:
            DestructiveMigrationError: When destructive ops are pending
                and ``force`` is ``False``.
            RuntimeError: When called from a running event loop; await
                :meth:`safe_upgrade_async` there instead.
        """
        _refuse_running_loop("safe_upgrade")
        offences = self.pending_destructive_ops(revision)
        if offences and not force:
            raise DestructiveMigrationError(offences)
        self.upgrade(revision)

    async def safe_upgrade_async(
        self,
        revision: str = "head",
        *,
        force: bool = False,
    ) -> None:
        """Run :meth:`safe_upgrade` in a worker thread, for async callers.

        Args:
            revision (str): The target revision (default ``"head"``).
            force (bool): Run even if destructive ops are pending.

        Raises:
            DestructiveMigrationError: When destructive ops are pending
                and ``force`` is ``False``.
        """
        await asyncio.to_thread(self.safe_upgrade, revision, force=force)

    def squash(
        self,
        message: str = "squash",
        *,
        force: bool = False,
        backup: bool = True,
    ) -> str:
        """Collapse the whole migration history into one fresh root revision.

        Migration files accumulate without bound as a project evolves —
        every schema tweak adds another file under ``versions/`` that
        Alembic must walk on every ``upgrade``. This routine resets that
        history to a single root revision describing the *current*
        schema, so the tree stops growing while existing databases stay
        usable via :meth:`stamp`.

        The flow is **destructive to the configured database** and is
        meant to run against a development database:

        1. Capture the current head (used to name the backup directory).
        2. ``downgrade base`` — drop every table and clear
           ``alembic_version`` so autogenerate sees an empty schema.
        3. Move (``backup=True``) or delete the old revision files out
           of ``versions/``.
        4. Autogenerate one root revision from ``BaseModel.metadata`` —
           the full schema as a single ``upgrade()``.
        5. ``upgrade head`` to recreate the schema and stamp the new
           revision.

        Existing production databases are **not** touched. After
        deploying the squashed tree, mark them as migrated without
        recreating tables: ``tempest db stamp head``.

        Args:
            message (str): Slug/message for the new root revision.
            force (bool): Must be ``True`` to proceed — step 2 drops
                every table in the configured database, so this guards
                against running against the wrong (e.g. production) URL.
            backup (bool): When ``True`` (default), move the old revision
                files into ``versions/_squashed_<oldhead>/`` (a
                non-recursive subdirectory Alembic ignores) instead of
                deleting them outright.

        Returns:
            str: The revision id of the new root migration.

        Raises:
            RuntimeError: When ``force`` is ``False``, when there are no
                revisions to squash, when the migration graph has
                multiple heads (run a merge revision first), or when called
                from a running event loop (await :meth:`squash_async`).
        """
        _refuse_running_loop("squash")
        if not force:
            raise RuntimeError(
                "squash drops every table in the configured database; pass "
                "force=True to confirm the URL points at a development database."
            )
        script = ScriptDirectory.from_config(self.config)
        heads = list(script.get_heads())
        if not heads:
            raise RuntimeError("no revisions to squash.")
        if len(heads) > 1:
            raise RuntimeError(
                f"multiple heads {heads}; run a merge revision before squashing."
            )
        old_head = heads[0]

        self.downgrade("base")

        versions_dir = Path(script.versions)
        revision_files = [
            path for path in versions_dir.glob("*.py") if path.name != "__init__.py"
        ]
        if backup:
            backup_dir = versions_dir / f"_squashed_{old_head}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            for path in revision_files:
                path.rename(backup_dir / path.name)
        else:
            for path in revision_files:
                path.unlink()

        new_script = self.revision(message=message, autogenerate=True)
        self.upgrade("head")

        new_revision = getattr(new_script, "revision", None)
        if isinstance(new_revision, str):
            return new_revision
        return self.current() or ""

    async def squash_async(
        self,
        message: str = "squash",
        *,
        force: bool = False,
        backup: bool = True,
    ) -> str:
        """Run :meth:`squash` in a worker thread, for async callers.

        Args:
            message (str): Slug/message for the new root revision.
            force (bool): Must be ``True`` to proceed.
            backup (bool): Move the old revision files aside instead of
                deleting them.

        Returns:
            str: The revision id of the new root migration.

        Raises:
            RuntimeError: When ``force`` is ``False``, when there are no
                revisions to squash, or when the graph has multiple heads.
        """
        return await asyncio.to_thread(self.squash, message, force=force, backup=backup)

    def downgrade(self, revision: str = "-1") -> None:
        """Revert migrations down to ``revision`` (default: one step back).

        Args:
            revision (str): Target revision identifier or relative
                spec. ``"base"`` rolls everything back.

        Raises:
            RuntimeError: When called from a running event loop; await
                :meth:`downgrade_async` there instead.
        """
        _refuse_running_loop("downgrade")
        command.downgrade(self.config, revision)

    async def downgrade_async(self, revision: str = "-1") -> None:
        """Run :meth:`downgrade` in a worker thread, for async callers.

        Args:
            revision (str): Target revision identifier or relative spec.
        """
        await asyncio.to_thread(self.downgrade, revision)

    def _read(self, operation: Callable[[Connection], _T], *, method: str) -> _T:
        """Run ``operation`` against the database on a sync connection.

        Opens a short-lived engine from the configured URL with the async
        driver stripped, and falls back to :meth:`_read_via_async` when no
        sync DBAPI is available for the backend. Two different exceptions
        signal that, and both are caught: ``NoSuchModuleError`` when
        SQLAlchemy does not know the stripped driver at all, and
        ``ModuleNotFoundError`` when it knows the driver but the package is
        not installed — ``create_engine`` imports the DBAPI eagerly in
        SQLAlchemy 2.0, so an asyncpg-only project (no psycopg2) raises on
        construction rather than on connect.

        Args:
            operation (Callable[[Connection], _T]): Reader invoked with an
                open sync connection. Must not depend on the connection
                outliving the call.
            method (str): The public method reading, named in the error the
                async fallback raises from a running event loop.

        Returns:
            _T: Whatever ``operation`` returned.

        Raises:
            RuntimeError: When ``sqlalchemy.url`` is not configured, so
                there is no database to read from.
        """
        url = self.config.get_main_option("sqlalchemy.url")
        if url is None:
            raise RuntimeError("sqlalchemy.url is not configured in alembic.ini")
        try:
            engine = create_engine(_strip_async_driver(url))
        except (NoSuchModuleError, ModuleNotFoundError):
            return self._read_via_async(url, operation, method=method)
        try:
            with engine.connect() as connection:
                return operation(connection)
        except ModuleNotFoundError:
            return self._read_via_async(url, operation, method=method)
        finally:
            engine.dispose()

    def _read_via_async(
        self,
        url: str,
        operation: Callable[[Connection], _T],
        *,
        method: str,
    ) -> _T:
        """Run ``operation`` through the async driver instead.

        Fallback for projects that install only an async DBAPI (e.g.
        ``asyncpg``) and therefore have no sync driver for the stripped
        URL. Opens a short-lived async engine and hands ``operation`` the
        sync connection ``run_sync`` provides.

        This is the only read path that needs ``asyncio.run``, so it is the
        only one that refuses a running event loop: with a sync driver
        installed, :meth:`current` keeps working from async code.

        Args:
            url (str): The (async-flavored) database URL from the config.
            operation (Callable[[Connection], _T]): The reader to run.
            method (str): The public method reading, named in the error.

        Returns:
            _T: Whatever ``operation`` returned.

        Raises:
            RuntimeError: When called from a running event loop.
        """
        from sqlalchemy.ext.asyncio import create_async_engine

        _refuse_running_loop(method)

        async def _run() -> _T:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as connection:
                    return await connection.run_sync(operation)
            finally:
                await engine.dispose()

        return asyncio.run(_run())

    def current(self) -> str | None:
        """Return the revision the database is currently stamped at.

        Reads ``alembic_version`` via a temporary connection derived from
        the configured URL.

        Returns:
            str | None: The revision identifier, or ``None`` when the
            ``alembic_version`` table is missing/empty.

        Works from a running event loop whenever a sync driver for the
        backend is installed. On an async-only install (``asyncpg``
        without a sync PostgreSQL driver) the read goes through
        ``asyncio.run`` and therefore raises there; await
        :meth:`current_async` instead.

        Raises:
            RuntimeError: When ``sqlalchemy.url`` is not configured, so
                there is no database to read the revision from, or when
                the async-only fallback is needed inside a running loop.
        """
        return self._read(
            lambda connection: MigrationContext.configure(
                connection
            ).get_current_revision(),
            method="current",
        )

    async def current_async(self) -> str | None:
        """Run :meth:`current` in a worker thread, for async callers.

        Returns:
            str | None: The revision identifier, or ``None`` when the
            database is not stamped.

        Raises:
            RuntimeError: When ``sqlalchemy.url`` is not configured.
        """
        return await asyncio.to_thread(self.current)

    def has_existing_schema(self) -> bool:
        """Report whether the database holds tables Alembic did not create.

        This is the question that separates "empty database, run the
        migrations" from "database that predates Alembic, adopt it first",
        and it is the one a hand-rolled bootstrap usually forgets to ask.
        ``alembic_version`` is excluded because Alembic writes it itself,
        so its presence says nothing about the application schema.

        Returns:
            bool: ``True`` when at least one non-Alembic table exists.

        Raises:
            RuntimeError: When ``sqlalchemy.url`` is not configured, or when
                the async-only fallback is needed inside a running loop.
        """
        return self._read(
            lambda connection: bool(
                set(inspect(connection).get_table_names()) - {"alembic_version"}
            ),
            method="has_existing_schema",
        )

    async def has_existing_schema_async(self) -> bool:
        """Run :meth:`has_existing_schema` in a worker thread.

        Returns:
            bool: ``True`` when at least one non-Alembic table exists.

        Raises:
            RuntimeError: When ``sqlalchemy.url`` is not configured.
        """
        return await asyncio.to_thread(self.has_existing_schema)

    def base_revision(self) -> str:
        """Return the root revision of the migration tree.

        This is the revision to stamp when adopting a schema that already
        exists — never ``head``, which is :meth:`stamp`'s default and the
        wrong answer for adoption.

        Returns:
            str: The single root revision identifier.

        Raises:
            AmbiguousBaseRevisionError: When the tree has no revisions, or
                more than one root.
        """
        bases: list[str] = list(ScriptDirectory.from_config(self.config).get_bases())
        if len(bases) != 1:
            raise AmbiguousBaseRevisionError(bases)
        return bases[0]

    def adopt(self) -> bool:
        """Bring a pre-Alembic database under Alembic, without upgrading.

        Stamps the **base** revision when the database holds application
        tables but no ``alembic_version`` row — the state a project reaches
        by creating its schema with ``create_all`` before it had
        migrations. Stamping the base says "the baseline is already
        applied", which is true, and leaves every revision after it
        pending, which is also true.

        Does nothing when the database is already stamped (there is
        nothing to adopt) or when it is empty (the baseline will create the
        tables itself, so stamping would skip the work).

        Returns:
            bool: ``True`` when a stamp was written.

        Raises:
            AmbiguousBaseRevisionError: When adoption is needed but the
                tree has no single root to stamp.
            RuntimeError: When ``sqlalchemy.url`` is not configured, or when
                called from a running event loop (await
                :meth:`adopt_async`).
        """
        _refuse_running_loop("adopt")
        if self.current() is not None:
            return False
        if not self.has_existing_schema():
            return False
        self.stamp(self.base_revision())
        return True

    async def adopt_async(self) -> bool:
        """Run :meth:`adopt` in a worker thread, for async callers.

        Returns:
            bool: ``True`` when a stamp was written.

        Raises:
            AmbiguousBaseRevisionError: When adoption is needed but the
                tree has no single root to stamp.
            RuntimeError: When ``sqlalchemy.url`` is not configured.
        """
        return await asyncio.to_thread(self.adopt)

    def sync_schema(self, *, force: bool = False) -> SchemaSyncOutcome:
        """Bring the schema in line with the migration tree, from any state.

        This is the whole first-boot bootstrap, and it exists because the
        obvious hand-written version is wrong in a way that stays invisible
        for weeks. That version calls ``create_tables()`` and then
        ``stamp("head")``: ``create_all`` is ``CREATE TABLE IF NOT
        EXISTS``, so against a schema that already exists it adds no
        column, and the stamp then records every revision as applied.
        ``alembic current`` answers ``head``, ``alembic upgrade head`` has
        nothing to do, ``alembic history`` looks clean — and the first
        query touching a column a migration was supposed to add fails in
        production, far from the cause.

        The three states this distinguishes:

        * **Empty database** — no stamp; ``safe_upgrade`` runs the whole
          tree, and the schema the baseline builds is by construction the
          one later revisions expect to alter.
        * **Database that predates Alembic** — :meth:`adopt` stamps the
          base, then ``safe_upgrade`` runs everything after it.
        * **Database already under Alembic** — ``safe_upgrade`` alone.

        From a lifespan hook, await :meth:`sync_schema_async` — this
        method raises when an event loop is already running.

        Args:
            force (bool): Passed to :meth:`safe_upgrade`. Destructive
                migrations are refused without it.

        Returns:
            SchemaSyncOutcome: Which of the three paths ran.

        Raises:
            DestructiveMigrationError: When a pending migration drops a
                table, column or constraint and ``force`` is ``False``.
            AmbiguousBaseRevisionError: When adoption is needed but the
                tree has no single root to stamp.
            RuntimeError: When ``sqlalchemy.url`` is not configured, or when
                called from a running event loop (await
                :meth:`sync_schema_async`).
        """
        _refuse_running_loop("sync_schema")
        if not self.heads():
            return SchemaSyncOutcome.NO_MIGRATIONS
        adopted = self.adopt()
        self.safe_upgrade(force=force)
        return SchemaSyncOutcome.ADOPTED if adopted else SchemaSyncOutcome.SYNCED

    async def sync_schema_async(self, *, force: bool = False) -> SchemaSyncOutcome:
        """Run :meth:`sync_schema` in a worker thread, for async callers.

        The call to make from a FastAPI lifespan: the worker thread has no
        event loop, so the ``asyncio.run`` inside ``env.py`` works there.

        Args:
            force (bool): Passed to :meth:`safe_upgrade`.

        Returns:
            SchemaSyncOutcome: Which of the three paths ran.

        Raises:
            DestructiveMigrationError: When a pending migration drops a
                table, column or constraint and ``force`` is ``False``.
            AmbiguousBaseRevisionError: When adoption is needed but the
                tree has no single root to stamp.
            RuntimeError: When ``sqlalchemy.url`` is not configured.
        """
        return await asyncio.to_thread(self.sync_schema, force=force)

    def heads(self) -> list[str]:
        """Return every head revision known to the script directory.

        Multiple heads indicate divergent branches in the migration
        graph — usually a sign that a merge migration is needed.

        Returns:
            list[str]: The head revision identifiers.
        """
        script = ScriptDirectory.from_config(self.config)
        return list(script.get_heads())

    def history(self, *, verbose: bool = False) -> str:
        """Return the migration history as a printable string.

        Wraps ``alembic history`` and captures stdout so the result
        can be logged or returned from an admin endpoint.

        Args:
            verbose (bool): Forward ``--verbose`` to Alembic.

        Returns:
            str: The captured output.
        """
        buffer = StringIO()
        with redirect_stdout(buffer):
            command.history(self.config, verbose=verbose)
        return buffer.getvalue()

    def revision(
        self,
        message: str,
        *,
        autogenerate: bool = True,
        sql: bool = False,
        head: str = "head",
    ) -> Any:
        """Create a new revision file.

        Args:
            message (str): Description of the change (becomes the
                migration slug).
            autogenerate (bool): Run autogenerate against the live
                schema. When ``False``, an empty revision is created
                for the user to fill in.
            sql (bool): Emit SQL to stdout instead of executing.
            head (str): Parent revision; defaults to the current head.

        Returns:
            Any: The Alembic ``Script`` (or list of scripts) created
            by the command, as returned by ``alembic.command.revision``.

        Raises:
            RuntimeError: When called from a running event loop; await
                :meth:`revision_async` there instead. Refused even with
                ``autogenerate=False``, because ``revision_environment`` in
                the ini makes that path execute ``env.py`` too.
        """
        _refuse_running_loop("revision")
        return command.revision(
            self.config,
            message=message,
            autogenerate=autogenerate,
            sql=sql,
            head=head,
        )

    async def revision_async(
        self,
        message: str,
        *,
        autogenerate: bool = True,
        sql: bool = False,
        head: str = "head",
    ) -> Any:
        """Run :meth:`revision` in a worker thread, for async callers.

        Args:
            message (str): Description of the change.
            autogenerate (bool): Run autogenerate against the live schema.
            sql (bool): Emit SQL to stdout instead of executing.
            head (str): Parent revision; defaults to the current head.

        Returns:
            Any: What :meth:`revision` returned.
        """
        return await asyncio.to_thread(
            self.revision,
            message,
            autogenerate=autogenerate,
            sql=sql,
            head=head,
        )

    def stamp(self, revision: str = "head", *, purge: bool = False) -> None:
        """Stamp the database with ``revision`` without running migrations.

        Useful after a manual squash where ``alembic_version`` still points
        at a revision that no longer exists in the tree.

        .. danger::
            The default is ``"head"``, and ``"head"`` is the wrong answer
            when importing an existing schema into Alembic for the first
            time. Stamping ``head`` on a schema Alembic did not build
            records every revision as applied when none of them ran: the
            database keeps the old columns while ``alembic current``,
            ``upgrade`` and ``history`` all report it up to date, and the
            failure surfaces weeks later as ``no such column`` in
            production. The revision to stamp when adopting is the **base**
            — use :meth:`adopt`, or :meth:`sync_schema` for the whole
            first-boot path, rather than choosing by hand.

        Args:
            revision (str): The revision to stamp.
            purge (bool): When ``True``, delete the existing
                ``alembic_version`` rows before stamping. Required when
                the recorded revision is no longer in the script
                directory — a plain stamp would fail with
                ``Can't locate revision`` because Alembic cannot resolve
                the stale pointer. Defaults to ``False``.

        Raises:
            RuntimeError: When called from a running event loop; await
                :meth:`stamp_async` there instead.
        """
        _refuse_running_loop("stamp")
        command.stamp(self.config, revision, purge=purge)

    async def stamp_async(self, revision: str = "head", *, purge: bool = False) -> None:
        """Run :meth:`stamp` in a worker thread, for async callers.

        Args:
            revision (str): The revision to stamp.
            purge (bool): Delete the existing ``alembic_version`` rows
                before stamping.
        """
        await asyncio.to_thread(self.stamp, revision, purge=purge)

    def check(self) -> bool:
        """Return ``True`` if no autogenerate diff would be produced.

        Wraps ``alembic check`` (added in Alembic 1.9). Suitable for
        CI to fail when models drift from the migration tree.

        The running-loop refusal sits outside the ``except`` that maps
        every Alembic failure to ``False``: before it, a call from async
        code swallowed the ``asyncio.run`` error and reported drift that
        was never measured.

        Returns:
            bool: ``True`` if the schema matches the models.

        Raises:
            RuntimeError: When called from a running event loop; await
                :meth:`check_async` there instead.
        """
        _refuse_running_loop("check")
        try:
            command.check(self.config)
            return True
        except Exception:
            return False

    async def check_async(self) -> bool:
        """Run :meth:`check` in a worker thread, for async callers.

        Returns:
            bool: ``True`` if the schema matches the models.
        """
        return await asyncio.to_thread(self.check)

    def show(self, revision: str = "head") -> str:
        """Return the details of a single revision.

        Args:
            revision (str): The revision to inspect.

        Returns:
            str: Multi-line description (id, parent, doc, path).
        """
        script = ScriptDirectory.from_config(self.config)
        rev = script.get_revision(revision)
        if rev is None:
            return ""  # type: ignore[unreachable]
        lines = [
            f"Rev: {rev.revision}",
            f"Parent: {rev.down_revision}",
            f"Path: {rev.path}",
            f"Doc: {rev.doc}",
        ]
        return "\n".join(lines)


__all__: list[str] = [
    "AlembicHelper",
    "AmbiguousBaseRevisionError",
    "DestructiveMigrationError",
    "SchemaSyncOutcome",
]
