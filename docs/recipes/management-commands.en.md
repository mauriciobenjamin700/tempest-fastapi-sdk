# Management commands (project `tempest <cmd>`)

Plug your own commands into the `tempest` CLI — the way Django lets you
register `manage.py <command>`. A backfill script, a custom seed, a
"reprocess the queue": each becomes `tempest backfill`, with the same
help and error handling as the built-in commands.

## The problem

Every service accumulates loose operational scripts
(`scripts/backfill.py`, `python -m app.tools.resync`). Each with its own
way to run, no `--help`, no convention. There was no canonical home: the
same CLI that already runs `tempest db upgrade` and `tempest check-config`.

## Convention: `src/commands.py`

Expose a `typer.Typer` named `commands` in a `src/commands.py` module (or
`app/commands.py`, or `commands.py` at the root):

```python
import typer

commands: typer.Typer = typer.Typer()


@commands.command("backfill")
def backfill(dry_run: bool = False) -> None:
    """Recompute the denormalized counters."""
    typer.echo(f"backfill (dry_run={dry_run})")
```

Run it from the project root:

```bash
tempest backfill --dry-run
```

It shows up in `tempest --help` alongside the built-ins. The full power
of Typer is available: arguments, options, types, help — all typed.

## Pointing at the location

Auto-detects `src.commands` / `app.commands` / `commands`. For another
location (or several), configure it in `pyproject.toml`:

```toml
[tool.tempest]
commands = "src.management"
# or several modules:
commands = ["src.billing.commands", "src.ops.commands"]
```

## Collision with a built-in command

If a project command has the same name as a built-in (`new`, `db`,
`check`, `check-config`, `version`, …), the built-in **wins** and the
project one is skipped with a warning on stderr. Pick another name.

## Nested groups

Since it is plain Typer, you can group. A sub-Typer becomes `tempest ops
<cmd>`:

```python
import typer

commands: typer.Typer = typer.Typer()
ops: typer.Typer = typer.Typer()
commands.add_typer(ops, name="ops")


@ops.command("resync")
def resync() -> None:
    """Reprocess the sync queue."""
    ...
```

```bash
tempest ops resync
```

!!! note "Discovery is best-effort, but errors surface"
    With no command module, the CLI runs as usual. If you **configured**
    `[tool.tempest] commands` and the module fails to import (or exposes
    no Typer), `tempest` warns on stderr — but never stops running the
    built-in commands because of it.

!!! tip "Run from the project root"
    Discovery adds the current directory to `sys.path` to import
    `src.commands`. Run `tempest` from the project root (where
    `pyproject.toml` lives), as you already do for `tempest db` /
    `check-config`.

## Recap

- Expose `commands: typer.Typer` in `src/commands.py`; it becomes
  `tempest <cmd>`.
- `[tool.tempest] commands` points at another module (string or list).
- Collision with a built-in → built-in wins, project one skipped with a
  warning.
- Plain Typer: args/options/types/help/nested groups for free.
