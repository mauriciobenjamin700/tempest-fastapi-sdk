"""Alembic command wrappers and environment-init helpers."""

from __future__ import annotations

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
            # Auto-format every freshly generated revision so the files
            # autogenerate emits (long ``sa.Column`` lines, trailing
            # whitespace in the docstring header when ``down_revision``
            # is ``None``) are lint-clean out of the box. ``ruff check
            # --fix`` sorts imports / applies safe fixes; ``ruff format``
            # wraps over-length lines and strips docstring trailing
            # whitespace. Both are no-ops when ruff is not installed
            # only if the hook fails — keep ruff in the dev deps.
            "[post_write_hooks]",
            "hooks = ruff_fix, ruff_format",
            "ruff_fix.type = exec",
            "ruff_fix.executable = ruff",
            "ruff_fix.options = check --fix REVISION_SCRIPT_FILENAME",
            "ruff_format.type = exec",
            "ruff_format.executable = ruff",
            "ruff_format.options = format REVISION_SCRIPT_FILENAME",
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
        engine = create_engine(_strip_async_driver(url))
        try:
            with engine.connect() as connection:
                migration_context = MigrationContext.configure(connection)
                return migration_context.get_current_revision()
        finally:
            engine.dispose()

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

    def stamp(self, revision: str = "head") -> None:
        """Stamp the database with ``revision`` without running migrations.

        Useful when importing an existing schema into Alembic for the
        first time.

        Args:
            revision (str): The revision to stamp.
        """
        command.stamp(self.config, revision)

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
]
