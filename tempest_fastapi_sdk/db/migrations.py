"""Alembic command wrappers and environment-init helpers."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from importlib import resources
from io import StringIO
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import NoSuchModuleError

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

    All methods are synchronous because Alembic itself is sync; run
    them from CLI scripts or from FastAPI's startup hook via
    ``asyncio.to_thread`` if you must call them from async code.

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
        resolution chain above fills it at runtime.

        Returns:
            Config: The configured Alembic config.
        """
        config = Config(self.config_path)
        if self._db_url_override is not None:
            config.set_main_option("sqlalchemy.url", self._db_url_override)
            return config

        # Fall back to env/settings when the ini left ``sqlalchemy.url``
        # empty (the SDK default since v0.30.2).
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
        # Alembic's ``command.init`` writes the ini at
        # ``config.config_file_name``; pre-seed it with our target
        # path so the file lands where the helper expects.
        ini_path = Path(self.config_path)
        ini_path.parent.mkdir(parents=True, exist_ok=True)
        config = Config(str(ini_path))
        config.set_main_option("script_location", directory)
        config.set_main_option("sqlalchemy.url", db_url)
        command.init(config, directory, template="async")

        # Patch the generated env.py with the SDK template so
        # autogenerate gets the project's metadata + sensible
        # comparison flags out of the box.
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

        # Overwrite the ini Alembic just wrote with the SDK's
        # opinionated layout (logger sections, file_template, UTC).
        # ``sqlalchemy.url`` is intentionally left empty so the
        # credentials never land in version control. The companion
        # ``env.py`` template resolves the URL at runtime from
        # ``DATABASE_URL`` env var (or ``src.core.settings``) and
        # injects it back into the Alembic config before the engine
        # is built. Pass ``db_url=...`` on the constructor to override
        # for one-off operations (CI smoke, scripted migrations).
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
            # Auto-format every freshly generated revision so the
            # files autogenerate emits (long ``sa.Column`` lines,
            # trailing whitespace in the docstring header when
            # ``down_revision`` is ``None``) are lint-clean out of
            # the box.
            #
            # Order matters: ``ruff format`` MUST run first — it
            # wraps over-length lines (E501) and strips trailing
            # whitespace (W291). Running ``ruff check --fix`` first
            # would emit noisy "found N errors / N fixed / M
            # remaining" output for things the formatter is about
            # to fix on the next hook. ``--quiet`` silences the
            # second pass when nothing actionable is left.
            "[post_write_hooks]",
            "hooks = ruff_format, ruff_fix",
            "ruff_format.type = exec",
            "ruff_format.executable = ruff",
            "ruff_format.options = format --quiet REVISION_SCRIPT_FILENAME",
            "ruff_fix.type = exec",
            "ruff_fix.executable = ruff",
            "ruff_fix.options = check --fix --quiet REVISION_SCRIPT_FILENAME",
            "",
            # No [loggers]/[handlers]/[formatters] sections on purpose.
            # ``env.py`` runs inside the host application process, and
            # the host already configured Python's logging tree via
            # ``configure_logging``. If we shipped the stock alembic.ini
            # ``[logger_root] level = WARN handlers = console`` block,
            # ``fileConfig(alembic.ini)`` from env.py would reset the
            # root logger to WARN + a stderr handler, silencing the SDK
            # 500 handler, the /logs writer and every JSON record the
            # app emits. The companion env.py template still calls
            # ``fileConfig`` (guarded on the section presence) so users
            # who manually re-add a ``[loggers]`` block keep working.
        ]
        ini_path.write_text("\n".join(ini_lines), encoding="utf-8")

    def upgrade(self, revision: str = "head") -> None:
        """Apply migrations up to ``revision`` (default: ``head``).

        Args:
            revision (str): Target revision identifier or relative
                spec (``"+1"``). ``"head"`` runs every pending
                migration.
        """
        command.upgrade(self.config, revision)

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
        """
        offences = self.pending_destructive_ops(revision)
        if offences and not force:
            raise DestructiveMigrationError(offences)
        self.upgrade(revision)

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
                revisions to squash, or when the migration graph has
                multiple heads (run a merge revision first).
        """
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

        # 1. Empty the database so autogenerate captures the full schema
        #    instead of an empty diff against the already-migrated DB.
        self.downgrade("base")

        # 2. Clear the versions directory (backup into a subdir or delete).
        #    Alembic is non-recursive by default, so a ``_squashed_*``
        #    subdirectory of ``versions/`` is invisible to the script graph.
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

        # 3. Autogenerate the single root revision and apply it.
        new_script = self.revision(message=message, autogenerate=True)
        self.upgrade("head")

        new_revision = getattr(new_script, "revision", None)
        if isinstance(new_revision, str):
            return new_revision
        return self.current() or ""

    def downgrade(self, revision: str = "-1") -> None:
        """Revert migrations down to ``revision`` (default: one step back).

        Args:
            revision (str): Target revision identifier or relative
                spec. ``"base"`` rolls everything back.
        """
        command.downgrade(self.config, revision)

    def current(self) -> str | None:
        """Return the revision the database is currently stamped at.

        Reads ``alembic_version`` via a temporary sync engine derived
        from the configured URL (async drivers get stripped).

        Returns:
            str | None: The revision identifier, or ``None`` when the
            ``alembic_version`` table is missing/empty.
        """
        config = self.config
        url = config.get_main_option("sqlalchemy.url")
        if url is None:
            raise RuntimeError("sqlalchemy.url is not configured in alembic.ini")
        try:
            engine = create_engine(_strip_async_driver(url))
        except (NoSuchModuleError, ModuleNotFoundError):
            # No sync DBAPI for this backend: either SQLAlchemy doesn't
            # know the driver (NoSuchModuleError) or it knows it but the
            # package isn't installed (ModuleNotFoundError — create_engine
            # eagerly imports the DBAPI in SQLAlchemy 2.0, e.g. an
            # asyncpg-only project has no psycopg2). Read via the async
            # driver instead.
            return self._current_via_async(url)
        try:
            with engine.connect() as connection:
                migration_context = MigrationContext.configure(connection)
                return migration_context.get_current_revision()
        except ModuleNotFoundError:
            # No sync DBAPI installed for this backend (e.g. an
            # asyncpg-only project has no psycopg2). Fall back to the
            # async driver to read ``alembic_version``.
            return self._current_via_async(url)
        finally:
            engine.dispose()

    def _current_via_async(self, url: str) -> str | None:
        """Read the current revision through the async driver.

        Fallback for projects that install only an async DBAPI (e.g.
        ``asyncpg``) and therefore have no sync driver for the stripped
        URL. Opens a short-lived async engine and reads
        ``alembic_version`` via ``run_sync``.

        Args:
            url (str): The (async-flavored) database URL from the config.

        Returns:
            str | None: The current revision, or ``None`` when the
            ``alembic_version`` table is missing/empty.
        """
        from sqlalchemy.ext.asyncio import create_async_engine

        async def _read() -> str | None:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as connection:
                    return await connection.run_sync(
                        lambda sync_conn: MigrationContext.configure(
                            sync_conn
                        ).get_current_revision()
                    )
            finally:
                await engine.dispose()

        return asyncio.run(_read())

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
        """
        return command.revision(
            self.config,
            message=message,
            autogenerate=autogenerate,
            sql=sql,
            head=head,
        )

    def stamp(self, revision: str = "head", *, purge: bool = False) -> None:
        """Stamp the database with ``revision`` without running migrations.

        Useful when importing an existing schema into Alembic for the
        first time, or after a manual squash where ``alembic_version``
        still points at a revision that no longer exists in the tree.

        Args:
            revision (str): The revision to stamp.
            purge (bool): When ``True``, delete the existing
                ``alembic_version`` rows before stamping. Required when
                the recorded revision is no longer in the script
                directory — a plain stamp would fail with
                ``Can't locate revision`` because Alembic cannot resolve
                the stale pointer. Defaults to ``False``.
        """
        command.stamp(self.config, revision, purge=purge)

    def check(self) -> bool:
        """Return ``True`` if no autogenerate diff would be produced.

        Wraps ``alembic check`` (added in Alembic 1.9). Suitable for
        CI to fail when models drift from the migration tree.

        Returns:
            bool: ``True`` if the schema matches the models.
        """
        try:
            command.check(self.config)
            return True
        except Exception:
            return False

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
    "DestructiveMigrationError",
]
