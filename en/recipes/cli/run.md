# Serve, console and database

Use to run the service (`tempest serve`), open the console (`tempest shell`) and operate the database (`tempest db`).

Part of the [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli.md) recipe.

## Run the service — `tempest serve`

```bash
tempest serve                              # host/port/reload from the settings
tempest serve --reload                     # turn reload on for this run
tempest serve --host 0.0.0.0 --port 9000   # override
tempest serve --workers 4                  # several processes (no reload)
tempest serve --app src.api.app:create_app # app outside the conventional spots
```

The scaffolded `main.py` already boots uvicorn programmatically; this command
is for the project that does not have that file yet, or for overriding host
and port without editing `.env`. Resolution is `run_server`'s: flag > the
project's settings > the SDK default (`127.0.0.1`, `8000`, no reload).

!!! note "uvicorn gets the *string*, never the object"
    The reloader re-imports the target in the child process. Handing it the
    already-imported instance works — and silently turns `--reload` into
    nothing. So the command imports the candidate once to validate it, and
    passes `"src.server:app"` along.

`--reload` together with `--workers` exits 2: uvicorn cannot honour both.
And `--no-reload` really does turn it off, even with `SERVER_RELOAD=true` in
`.env` — the flag has three states (on, off, "you decide").

## Interactive console — `tempest shell`

A REPL with the project already loaded, so "how many orders does this user
have?" does not cost a throwaway script.

```bash
tempest shell
```

```text
tempest shell — Python 3.11.12, top-level await enabled
available: WidgetModel, db, select, session, settings, text
session: open on sqlite+aiosqlite:///./app.db
>>> rows = await session.execute(select(WidgetModel))
>>> rows.scalars().all()
[<WidgetModel id=...>]
```

What the prompt holds: `settings` (the project's instance), every mapped
model **the project defines** (the SDK's `BaseModel` stays out — a query
against the base class is never what you meant), `select`, `text`, the
`AsyncDatabaseManager` as `db`, and an already-open `session`.

!!! info "Top-level `await`, on purpose"
    The console compiles with `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` and runs the
    coroutine **on the same loop** the session was opened on. Exposing a
    `run(coro)` helper would look equivalent and is not: a session driven
    from a second loop raises `MissingGreenlet` at the first lazy load.

`--no-db` starts without connecting — useful when the database is down and
you only want to inspect settings and models.

## Database — `tempest db`

Alembic wrapper backed by ``AlembicHelper`` — your project's ``alembic.ini`` + ``env.py`` stay the source of truth.

``DATABASE_URL`` resolution order:

1. ``--database-url`` flag.
2. ``DATABASE_URL`` env var.
3. Settings on the project's ``core/settings.py``, under either code root
   (``src`` or ``app``). **Any** ``pydantic_settings.BaseSettings`` instance
   qualifies — the names ``settings`` and ``config`` are tried first, then the
   module is scanned by type — **or** the result of a zero-arg factory that
   returns one (``get_settings()``, or any callable annotated to return
   ``BaseSettings``).
4. ``DATABASE_URL`` in the project's ``.env``.
5. ``sqlalchemy.url`` from ``alembic.ini``.

!!! tip "The instance name no longer matters, and the factory counts"
    Up to 0.280.0 only the name ``settings`` was accepted, and the failure was
    an ``ImportError`` swallowed by ``except Exception: return None``. A
    service naming its instance ``config`` got *"no database URL. Pass
    --database-url, set DATABASE_URL, or run inside a project with
    src/core/settings.py"* — pointing at two conditions the project already
    satisfied. Today the instance's **type** decides, the documented
    **factory** (``get_settings()``) is accepted, ``.env`` is read, and when
    nothing resolves each attempt reports why on stderr:

    ```text
    note: importing src.core.settings failed: ImportError("cannot import name ...")
    error: no database URL. Pass --database-url, set DATABASE_URL, put it in
           .env, or expose settings with a DATABASE_URL on
           src/core/settings.py (or app/core/settings.py).
    ```

    The same discovery is shared by ``tempest db``, ``tempest doctor``,
    ``tempest user`` and the generated ``env.py`` — a project whose settings
    exist only as ``get_settings()`` now migrates outside the CLI too.

```bash
tempest db init                                  # create alembic.ini + alembic/env.py
tempest db revision -m "init users table"        # autogenerate by default
tempest db revision -m "manual change" --manual  # empty file you'll edit
tempest db upgrade                               # alembic upgrade head
tempest db upgrade <rev>                         # upgrade to a specific revision
tempest db downgrade                             # roll back one step
tempest db downgrade <rev>                       # roll back to a specific revision
tempest db current                               # print the applied revision
tempest db history                               # revisions newest → oldest
tempest db history -v                            # with full message body
tempest db stamp head                            # mark the DB without running migrations
tempest db squash -m "init" --yes                # collapse history into 1 migration
tempest db backup                                # dump to backups/<db>_<ts>.<ext>
tempest db backup -o dump.sql                    # plain SQL (Postgres) by extension
tempest db restore dump.dump --yes               # restore (clean + recreate)
tempest db seed                                  # runs src.db.seeds:seed
tempest db seed --seed src.db.fixtures:demo      # custom callable
```

!!! danger "`stamp head` does not adopt an existing database"
    `stamp` writes the pointer without running anything. Against a schema
    Alembic did **not** build, stamping `head` records revisions that never ran
    as applied: the database keeps its old columns while `current`, `upgrade`
    and `history` all answer that it is up to date, and the failure surfaces
    weeks later in production. The right revision for adoption is the **base** —
    see [Migrations »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/migrations.md), which ships `AlembicHelper.adopt()`
    and `sync_schema()` so the choice is not made by hand.

### When Alembic refuses the command

An Alembic error describing the **state of the database** — not a bug — comes
out as one line, with what to do about it:

```console
$ tempest db revision -m "new field" --autogenerate
error: the database is behind head — a migration is pending. Run `tempest db upgrade`, then try again.
       (TEMPEST_DEBUG=1 for the full traceback)
$ echo $?
1
```

Four conditions get advice: the database is behind head, the history has more
than one head, the revision is not in `alembic/versions/`, and two revisions
are not on the same branch. Any other message is printed **exactly as Alembic
wrote it** — a wrong hint would be worse than the raw words.

!!! tip "Want the traceback back?"
    `TEMPEST_DEBUG=1 tempest db revision ...` re-raises untouched. Use it when
    the error is **not** one of the conditions above and you need to see where
    it came from.

!!! note "Only the CLI translates"
    Calling `AlembicHelper.revision(...)` directly, from a script or a
    lifespan, still raises `alembic.util.exc.CommandError`. Code wants the
    typed exception; a person at a terminal wants the sentence.

### Collapse the history — `tempest db squash`

Over time the `alembic/versions/` directory grows without bound — every schema tweak adds another file Alembic must walk on every `upgrade`. `squash` resets that history to a **single root migration** describing the **current** schema, while keeping existing databases usable.

!!! danger "Destructive — run it against a development database"
    `squash` runs `downgrade base` on the configured database (DROPs every table) so it can autogenerate the full schema into a single file. That's why it requires `--yes`. Make sure `DATABASE_URL` points at a dev database before running.

The flow is:

1. Capture the current `head` (used to name the backup directory).
2. `downgrade base` — empty the database so autogenerate sees an empty schema.
3. Move the old revisions into `alembic/versions/_squashed_<oldhead>/` (a subdirectory Alembic ignores). Pass `--no-backup` to delete them instead.
4. Autogenerate **one** root migration from `BaseModel.metadata`.
5. `upgrade head` recreates the schema and stamps the new revision.

```bash
tempest db squash -m "init" --yes               # recoverable backup (default)
tempest db squash -m "init" --yes --no-backup   # delete the old files
```

!!! warning "Production databases are not touched"
    `squash` only touches the configured database. After deploying the collapsed tree, mark production databases as migrated **without** recreating tables:

    ```bash
    tempest db stamp head
    ```

!!! tip "Manual squash (keep your data) → `stamp --purge`"
    If you unify migrations **by hand** (delete `versions/`, write one baseline migration, keep the database with its data), `alembic_version` still points at the old revision — which no longer exists in the tree. A plain `stamp` fails with `Can't locate revision`. Use `--purge` to clear the stale pointer and record the new baseline:

    ```bash
    tempest db stamp init_schema --purge
    ```

**Recap:** `squash` swaps an ever-growing history for one clean initial migration; `stamp` reconciles databases already at the final schema (use `--purge` when the recorded revision no longer exists). Recoverable backup by default — Git is your second net.

### Backup and restore — `tempest db backup` / `tempest db restore`

Snapshot the database to a file and back. The strategy differs per dialect, but the CLI is the same:

- **PostgreSQL** — `pg_dump` / `pg_restore`. The format comes from the file extension: `.dump` → custom (`pg_dump -Fc`, compressed, restored with `pg_restore`), `.sql` → plain (`psql`). Force it with `--plain` / `--custom`. Requires the PostgreSQL client tools on `PATH`.
- **SQLite** — copies the database file.

!!! warning "Prerequisite: PostgreSQL client tools"
    `backup` / `restore` against a **PostgreSQL** database depend on the `pg_dump`, `pg_restore` and `psql` binaries being on your `PATH`. They do **not** ship with the Python package — they are installed by your operating system. Without them the CLI fails with a clear message (`'pg_dump' not found on PATH`).

    **SQLite needs nothing** — the backup is a stdlib file copy.

    === "Debian / Ubuntu"
        ```bash
        sudo apt-get update && sudo apt-get install -y postgresql-client
        ```

    === "Fedora / RHEL"
        ```bash
        sudo dnf install -y postgresql
        ```

    === "Arch"
        ```bash
        sudo pacman -S postgresql
        ```

    === "macOS (Homebrew)"
        ```bash
        brew install libpq && brew link --force libpq
        ```

    === "Windows"
        ```powershell
        choco install postgresql   # Chocolatey
        scoop install postgresql   # or Scoop
        ```

    !!! tip "Verify the install"
        ```bash
        pg_dump --version && pg_restore --version && psql --version
        ```
        Match the client major version to the server (a newer `pg_dump` reads older servers, but not the other way around).

```bash
tempest db backup                       # backups/<db>_<YYYYMMDD-HHMMSS>.dump
tempest db backup -o snapshot.sql       # plain SQL (Postgres) by extension
tempest db backup -o snap.dump --custom # force the custom format
tempest db restore snapshot.sql --yes   # restore (psql)
tempest db restore snap.dump --yes      # restore (pg_restore --clean --if-exists)
```

!!! danger "Restore overwrites the target database"
    By default the restore is **clean + recreate**: existing objects are dropped before being recreated, so the result is a faithful copy of the backup (`pg_restore --clean --if-exists`; plain drops/recreates the `public` schema; SQLite overwrites the file). That's why it requires `--yes`. Pass `--no-clean` to apply the dump on top of the current schema.

!!! info "The Postgres password never leaks into `ps`"
    The URL is parsed into `-h/-p/-U/-d` and the password is passed via `PGPASSWORD` in the subprocess environment — never on the command line.

**Recap:** `backup` takes a snapshot (format by extension on Postgres, file copy on SQLite); `restore --yes` brings it back, cleaning the target by default.

### Catch model drift — `tempest db check`

`tempest db revision --autogenerate` records a diff when you ask for one.
Nothing notices when somebody edits a model and **forgets** to ask — until
the deploy that needed the column.

```bash
tempest db check
```

No drift:

```text
No drift: the models match the migration tree.
```

With drift, it names the operations a revision would contain and exits 1:

```text
error: New upgrade operations detected: [('add_column', ...'colour'...)]
       Run `tempest db revision -m "<message>"` to record them.
```

!!! tip "This is a CI step"
    Run it after `tempest db upgrade` in the pipeline. The comparison needs
    the database at `head` — when it is behind, Alembic says so and the
    message is passed through instead of being reported as drift.

### Seed the database — `tempest db seed`

Runs a project seed callable inside a managed session (commit on success, rollback on error). The callable takes a positional `AsyncSession` and may be sync or async; what it inserts is up to you — the SDK only wires the session lifecycle. Defaults to importing `src.db.seeds:seed`.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CategoryModel


async def seed(session: AsyncSession) -> int:
    """Seed initial categories. Return the count (optional)."""
    session.add_all([CategoryModel(name="Books"), CategoryModel(name="Games")])
    await session.flush()
    return 2
```

When the callable returns an `int`, the CLI prints the count: `Seeded via src.db.seeds:seed (2 rows).`
