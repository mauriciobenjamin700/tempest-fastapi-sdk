"""Database backup / restore helpers driven from the CLI.

Wraps the canonical per-dialect tooling behind a single
:class:`DatabaseBackup` so ``tempest db backup`` / ``tempest db
restore`` work the same against the two databases the SDK supports:

* **PostgreSQL** (production) — shells out to ``pg_dump`` / ``pg_restore``
  (custom ``-Fc`` format) or ``psql`` (plain ``.sql``). The format is
  picked from the file extension: ``.dump`` → custom, ``.sql`` → plain.
* **SQLite** (development) — copies the database file. A restore
  overwrites the target file outright, which is inherently "clean".

The async driver suffix (``+asyncpg`` / ``+aiosqlite``) is stripped
before any tool runs. The Postgres password is passed via the
``PGPASSWORD`` environment variable rather than on the command line so
it never shows up in ``ps`` output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from sqlalchemy.engine import make_url

from tempest_fastapi_sdk.db.migrations import _strip_async_driver
from tempest_fastapi_sdk.utils.datetime import utcnow


class BackupToolMissingError(RuntimeError):
    """Raised when a required CLI tool (pg_dump/pg_restore/psql) is absent."""


class UnsupportedBackupBackendError(RuntimeError):
    """Raised when the database dialect has no backup strategy."""


def _require_tool(name: str) -> str:
    """Return the absolute path to ``name`` or raise if it is not on PATH.

    Args:
        name (str): The executable to locate (e.g. ``"pg_dump"``).

    Returns:
        str: The resolved absolute path.

    Raises:
        BackupToolMissingError: When the tool is not installed.
    """
    path = shutil.which(name)
    if path is None:
        raise BackupToolMissingError(
            f"{name!r} not found on PATH. Install the PostgreSQL client tools "
            "to back up / restore a Postgres database."
        )
    return path


def _run(args: list[str], *, env: dict[str, str] | None = None) -> None:
    """Run a subprocess, raising with captured stderr on failure.

    Args:
        args (list[str]): The command and its arguments (no shell).
        env (dict[str, str] | None): Extra environment overrides merged
            over ``os.environ``.

    Raises:
        RuntimeError: When the process exits non-zero. The message
            carries the tool's stderr so the caller sees the real cause.
    """
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(
        args,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        tool = Path(args[0]).name
        raise RuntimeError(
            f"{tool} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


class DatabaseBackup:
    """Per-dialect database backup / restore over a single URL.

    All methods are synchronous because the underlying tools are sync
    processes / file copies. Call them from a CLI command or, if needed
    from async code, via ``asyncio.to_thread``.

    Attributes:
        url (str): The sync-flavored database URL (async driver stripped).
        backend (str): The detected backend (``"postgresql"`` /
            ``"sqlite"``).
    """

    def __init__(self, database_url: str) -> None:
        """Initialize from a (possibly async) database URL.

        Args:
            database_url (str): The application's ``DATABASE_URL``. The
                async driver suffix is stripped automatically.
        """
        self.url: str = _strip_async_driver(database_url)
        self.backend: str = make_url(self.url).get_backend_name()

    def _pg_conn(self) -> tuple[list[str], dict[str, str]]:
        """Build ``pg_*`` connection flags + a password env from the URL.

        Returns:
            tuple[list[str], dict[str, str]]: ``(args, env)`` where
            ``args`` carries ``-h/-p/-U/-d`` and ``env`` carries
            ``PGPASSWORD`` (empty when the URL has no password).
        """
        parsed = make_url(self.url)
        args: list[str] = []
        if parsed.host:
            args += ["-h", parsed.host]
        if parsed.port:
            args += ["-p", str(parsed.port)]
        if parsed.username:
            args += ["-U", parsed.username]
        if parsed.database:
            args += ["-d", parsed.database]
        env: dict[str, str] = {}
        if parsed.password:
            env["PGPASSWORD"] = parsed.password
        return args, env

    def _default_output(self, plain: bool) -> Path:
        """Compute the default ``backups/<db>_<timestamp>.<ext>`` path.

        Args:
            plain (bool): Whether a plain ``.sql`` dump is being written
                (Postgres only; ignored for SQLite).

        Returns:
            Path: The default output path. The parent directory is the
            caller's responsibility to create.
        """
        parsed = make_url(self.url)
        if self.backend == "sqlite":
            stem = Path(parsed.database or "database").stem
            ext = "sqlite"
        else:
            stem = parsed.database or "database"
            ext = "sql" if plain else "dump"
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        return Path("backups") / f"{stem}_{stamp}.{ext}"

    def backup(self, output: Path | None = None, *, plain: bool | None = None) -> Path:
        """Dump the database to ``output`` and return the written path.

        Args:
            output (Path | None): Destination file. When ``None``, a
                timestamped path under ``backups/`` is used. The parent
                directory is created if missing.
            plain (bool | None): Postgres format override. ``True`` forces
                a plain ``.sql`` dump, ``False`` forces custom ``-Fc``.
                When ``None``, the format is inferred from ``output``'s
                extension (``.sql`` → plain, else custom). Ignored for
                SQLite.

        Returns:
            Path: The path the backup was written to.

        Raises:
            UnsupportedBackupBackendError: For an unsupported dialect.
            BackupToolMissingError: When ``pg_dump`` is not installed.
            RuntimeError: When the dump process fails or the SQLite file
                is missing / in-memory.
        """
        if plain is None:
            plain = output is not None and output.suffix == ".sql"
        dest = output or self._default_output(plain)

        # Validate the dialect (and required tooling / source) BEFORE
        # creating the destination directory, so a failed backup never
        # leaves an empty ``backups/`` behind.
        if self.backend == "postgresql":
            tool = _require_tool("pg_dump")
            conn, env = self._pg_conn()
            fmt = [] if plain else ["-Fc"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            _run([tool, *fmt, "-f", str(dest), *conn], env=env)
        elif self.backend == "sqlite":
            source = self._sqlite_path()
            self._sqlite_copy(source, dest)
        else:
            raise UnsupportedBackupBackendError(
                f"backup is not supported for backend {self.backend!r}."
            )
        return dest

    def restore(self, source: Path, *, clean: bool = True) -> None:
        """Restore the database from ``source``.

        Args:
            source (Path): The backup file. For Postgres the format is
                inferred from the extension (``.sql`` → ``psql``, else
                ``pg_restore``).
            clean (bool): When ``True`` (default), drop existing objects
                before recreating so the restore is a faithful copy. For
                Postgres custom this uses ``pg_restore --clean
                --if-exists``; for plain it drops/recreates the ``public``
                schema first; for SQLite the target file is overwritten.

        Raises:
            FileNotFoundError: When ``source`` does not exist.
            UnsupportedBackupBackendError: For an unsupported dialect.
            BackupToolMissingError: When the required tool is absent.
            RuntimeError: When the restore process fails.
        """
        if not source.is_file():
            raise FileNotFoundError(f"backup file not found: {source}")

        if self.backend == "postgresql":
            self._pg_restore(source, clean=clean)
        elif self.backend == "sqlite":
            # Overwriting the file is inherently clean; ``clean`` has no
            # separate meaning for a file copy.
            self._sqlite_copy(source, self._sqlite_path())
        else:
            raise UnsupportedBackupBackendError(
                f"restore is not supported for backend {self.backend!r}."
            )

    def _pg_restore(self, source: Path, *, clean: bool) -> None:
        """Restore a Postgres dump, dispatching on the file format.

        Args:
            source (Path): The dump file (``.sql`` → plain, else custom).
            clean (bool): Drop existing objects first when ``True``.
        """
        conn, env = self._pg_conn()
        if source.suffix == ".sql":
            psql = _require_tool("psql")
            if clean:
                _run(
                    [
                        psql,
                        *conn,
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-c",
                        "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;",
                    ],
                    env=env,
                )
            _run([psql, *conn, "-v", "ON_ERROR_STOP=1", "-f", str(source)], env=env)
        else:
            pg_restore = _require_tool("pg_restore")
            clean_flags = ["--clean", "--if-exists"] if clean else []
            _run(
                [pg_restore, "--no-owner", *clean_flags, *conn, str(source)],
                env=env,
            )

    def _sqlite_path(self) -> Path:
        """Return the on-disk path of the SQLite database.

        Returns:
            Path: The database file path.

        Raises:
            RuntimeError: When the URL points at an in-memory database.
        """
        database = make_url(self.url).database
        if not database or database == ":memory:":
            raise RuntimeError("cannot back up an in-memory SQLite database.")
        return Path(database)

    @staticmethod
    def _sqlite_copy(src: Path, dest: Path) -> None:
        """Copy a SQLite database file, preserving metadata.

        Args:
            src (Path): Source file.
            dest (Path): Destination file.

        Raises:
            RuntimeError: When the source file is missing.
        """
        if not src.is_file():
            raise RuntimeError(f"SQLite database file not found: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


__all__: list[str] = [
    "BackupToolMissingError",
    "DatabaseBackup",
    "UnsupportedBackupBackendError",
]
