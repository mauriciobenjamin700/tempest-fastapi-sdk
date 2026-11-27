---
description: "Use to run the quality gates (ruff, mypy, pytest) from the CLI and know which SDK steps `tempest check` runs, including `lint-schemas`."
---

# Quality gates and `tempest check`

Use to run the quality gates and know which SDK steps `tempest check` runs.

Part of the [CLI](../cli.md) recipe.

## Quality gates

!!! info "These commands come from `tempest-cli` (v0.226.0)"
    The quality gate has nothing to do with FastAPI — it is `ruff`,
    `mypy` and `pytest`. Since v0.226.0 it lives in its own package,
    [`tempest-cli`](https://pypi.org/project/tempest-cli/), which the SDK's
    `[cli]` extra declares and the SDK mounts on its CLI.

    **Nothing changes for you**: `tempest check` is the same command,
    with the same flags and the same `[tool.tempest] typing_strictness`.
    What changes is that people **not** using FastAPI can now install
    the gate alone:

    ```bash
    uv add --dev tempest-cli
    tempest-cli check
    ```

    Why split it: reaching those four commands meant installing FastAPI,
    SQLAlchemy, Alembic and Pydantic — 38.7 MB of dependencies and about
    0.5 s of import time per invocation, measured, for commands that
    touch none of it.

    The two never diverge because there is one implementation: the SDK
    calls `register_commands(app)` from `tempest_cli`. Importing
    `tempest_fastapi_sdk.cli.lint` or `.pr_prompt` keeps working and
    returns the same functions.

The lint commands shell out to the project's tooling, looking for the executable in this order:

1. the activated `$VIRTUAL_ENV`;
2. the nearest `.venv`, walking up from the working directory — the project's environment, even when it was never activated;
3. the directory of the interpreter running `tempest` itself;
4. `PATH`, skipping a version-manager shim (pyenv/asdf) that dispatches nowhere;
5. `uv run --with <tool> <tool>`, when `uv` is on `PATH`.

The project comes before the CLI's own environment on purpose: under `uv tool install` the CLI carries its own `ruff`, and a project that pins another version still runs the one it pinned.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix-only . + ruff format . + ruff check .   (writes)
tempest fix --unsafe                            # also apply ruff's --unsafe-fixes
tempest format                                  # ruff format .          (writes)
tempest fmt-check                               # ruff format --check .   (read-only)
tempest type                                    # mypy .
tempest test                                    # pytest -n auto -p no:cacheprovider  (parallel, the default)
tempest test tests/api/                         # with a path filter
tempest test -w 4                               # four workers
tempest test --serial                           # serially (--lf / --ff, a debugger)
tempest check                                   # lint + fmt-check + type + lint-layers + openapi-errors + lint-schemas + test, stops at first failure
tempest check --rule IMP002 --strict            # the same gate, lint-layers on that rule only and warnings failing
```

`test` and `check` run the suite in parallel by default since `tempest-cli`
0.6.0 (the `[cli]` extra declares `tempest-cli>=0.7.0`); without pytest-xdist
in pytest's environment, they print a notice and run serially. When to use
it, one database per worker and what to do with a test that fails only in
parallel are in the
[testing recipe](../testing.md#the-suite-in-parallel-the-tempest-test-default).

`tempest fix` is the one-shot "organize the project" pass — sorts and dedupes imports, drops unused imports, normalizes string quotes, removes trailing whitespace, then runs `ruff format` to align indentation, line length, blank lines and trailing newlines. Run it before pushing when CI keeps catching style nits.

!!! info "The report comes from the formatted file (`tempest-cli` 0.5.1)"
    `tempest fix` runs three passes: `ruff check --fix-only`, `ruff format`
    and finally `ruff check`, which is where the report and the exit code
    come from. A violation the formatter resolved no longer shows up or
    fails the command. Whatever is left after formatting still exits
    non-zero, so CI keeps failing on the real leftovers.

!!! tip "Long comments and strings are shortened, with an AST proof (`tempest-cli` 0.5.1)"
    `ruff format` never breaks a comment or a string literal — same as
    Black. After it, `tempest fix` wraps each `E501` comment at the limit
    (a trailing comment moves above its line) and splits each plain string
    literal at its spaces into implicitly concatenated parts inside
    parentheses. Every edit is kept only when the file's AST is identical
    before and after, so the program cannot change. With
    `line-length = 88`, this:

    ```python
    """Long lines."""

    MESSAGE: str = "this is a deliberately long plain string literal that goes well beyond the configured line length limit"


    def f() -> int:
        """Return one."""
        return 1  # this trailing comment is deliberately long so that it overflows the configured line length
    ```

    becomes this:

    ```python
    """Long lines."""

    MESSAGE: str = (
        "this is a deliberately long plain string literal that goes well beyond the "
        "configured line length limit"
    )


    def f() -> int:
        """Return one."""
        # this trailing comment is deliberately long so that it overflows the configured
        # line length
        return 1
    ```

    Tool pragmas (`noqa`, `type: ignore`, `fmt:`, …), f-strings,
    triple-quoted strings and words longer than a line are left alone. For
    those lines — and for what the formatter has nowhere to break, such as
    a dotted name — `tempest fix` names the overflowing token, its kind and
    the action that resolves it, and splits the leftovers into those with
    no autofix and those `tempest fix --unsafe` would fix. A long f-string,
    for instance, comes out like this (exit 1):

    ```text
    1 line(s) still too long after ruff format — the line-length setting was applied; the formatter has nowhere to break these:
      src/longs.py:4 [string] ` this is a deliberately long f-string literal that goes well beyond the limit`
        ruff format does not split string literals, and fix could not split this one safely (an f-string, a triple-quoted string, or a word longer than a line). Break it into implicitly concatenated parts inside parentheses.
    1 residual violation(s): 1 with no autofix (edit by hand).
    ```

Every command returns the underlying tool's exit code, so `tempest check` is safe to wire into CI (`tempest check || exit 1`) or pre-commit hooks. When none of the five steps finds the tool, the wrapper exits with `127` instead of failing silently, naming what to install:

```text
error: 'ruff' was not found in this project's environment, on PATH, and 'uv' is unavailable to run it. Install it with 'uv add --dev ruff' — or the whole set with 'uv add --dev "tempest-cli[tools]"' — and retry.
```

## The SDK's steps in `tempest check`

`tempest check` comes from `tempest-cli`, which knows nothing about FastAPI.
The SDK hands it three steps of its own, which run **after `mypy` and before
`pytest`**, in this order, with the same contract as the others: the first
non-zero exit code stops the gate.

| Step | What it runs | How it finds the project |
| --- | --- | --- |
| `lint-layers` | the [layer rules](lint-layers.md) | reads `src/` or `app/` with `ast` |
| `openapi-errors` | the same as `tempest openapi-errors --check` ([recipe](../openapi-errors.md#step-4-tempest-openapi-errors-check)) | reads `src/` or `app/` with `ast`, importing nothing |
| `lint-schemas` | the same as `tempest lint-schemas` | imports `src.schemas` (or `app.schemas`) in a **child process** |

The project root is the `check` target (`.` by default), or its parent when
the target is `src`/`app` itself (`tempest check src`). `lint-schemas` imports
the package in a separate process, run from the project root, so the project's
modules are not left loaded in the process that runs `pytest` next. Importing
`src.schemas` goes through `src/__init__.py`, which builds the settings, and a
settings class with `EnvironmentSettings` does not start without `ENV`: when
the shell does not set `ENV`, the child gets `ENV=test` — the same value the
`tempest new` `conftest.py` pins. An `ENV` exported in the shell wins.

On a project fresh from `tempest new`, all three pass (excerpt, exit code
`0`):

```text
$ lint-layers
Layer rules: no findings.
$ openapi-errors
Every route's declared errors match its flow (src).
$ lint-schemas
Schema metadata: src.schemas is fully documented.
$ pytest -n auto -p no:cacheprovider
```

With a router whose dependencies raise an undeclared 401 and 404, the gate
stops at `openapi-errors` and `pytest` never runs (exit code `1`):

```text
$ lint-layers
Layer rules: no findings.
$ openapi-errors
src/api/routers/items.py:19  GET /
  undocumented: ItemNotFoundException, UnauthorizedException
1 route(s) with drift, 2 undocumented exception(s).
```

A project with neither `src/` nor `app/` does not fail: the step says it
skipped and passes. Same for `lint-schemas` without a `schemas` package. A
`src.schemas` that does not import, though, **fails** (exit `2`, with the
traceback): the package exists and is broken.

To migrate an existing project one gate at a time, switch the step off under
`[tool.tempest]`:

```toml
[tool.tempest]
check_openapi_errors = false
check_lint_schemas = false
```

With the key set to `false`, the step prints `skipped: [tool.tempest]
check_openapi_errors = false.` and passes. A non-boolean value exits `2`. The
standalone commands (`tempest openapi-errors`, `tempest lint-schemas`) do not
read these keys.


### A required setting in `lint-schemas`

`lint-schemas` imports `src.schemas` in a child process with `ENV=test` when
the shell sets no `ENV` — and under `ENV=test`, `EnvironmentSettings` discards
the `.env`. A `Settings` with a required field and no default (a secret such as
`ADMIN_SECRET_KEY`, which must not carry a placeholder) does not import, and the
step exits `2`. The reason follows the traceback:

```text
hint: src.schemas was imported in a child process with ENV=test (ENV=test discards the .env, so a required setting without a default is missing). Give the child the settings it needs under [tool.tempest] lint_schemas_env = { NAME = "value" }, or drop the step with check_lint_schemas = false.
```

Give the child what it needs, for this step only:

```toml
[tool.tempest]
lint_schemas_env = { ADMIN_SECRET_KEY = "lint-only" }
```

Precedence is: the `ENV=test` default, then the table, then the shell — a value
the shell exports is never replaced by the placeholder. A non-string value
exits `2`.
