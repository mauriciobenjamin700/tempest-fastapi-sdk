# Contributing

## Development environment

```bash
# Clone + sync every extra and the dev/docs groups
git clone https://github.com/mauriciobenjamin700/tempest-fastapi-sdk.git
cd tempest-fastapi-sdk
uv sync --all-extras --group dev --group docs
```

!!! tip "Quick verification"
    `make check` runs the full quality gate (lint + format check + mypy + pytest). CI runs the same target on every push, so a green `make check` locally means a green PR.

## Quality gates

| Command | What it does |
| --- | --- |
| `make lint` | `ruff check .` (no auto-fix) |
| `make fix` | `ruff check --fix .` + `ruff format .` (writes) |
| `make fmt` | `ruff format .` (writes) |
| `make fmt-check` | `ruff format --check .` (read-only) |
| `make type` | `mypy tempest_fastapi_sdk` (strict) |
| `make test` | `pytest` with coverage |
| `make check` | `lint + fmt-check + type + test` (stops at first failure) |
| `make ci` | `check + build + smoke` (full CI mirror) |

The same gates are available via the bundled CLI: `tempest lint` / `tempest fix` / `tempest check` work in any consuming project too.

## Tests

```bash
make test                    # full suite + coverage
uv run pytest tests/admin    # just the admin module
uv run pytest -k cursor      # tests matching "cursor"
uv run pytest -x             # stop at first failure
```

The suite uses in-memory SQLite via `tempest_fastapi_sdk.testing.test_session`. Repository tests share the `session` fixture from `tests/conftest.py`.

## Docs

```bash
make docs-serve              # mkdocs serve — live reload at http://127.0.0.1:8000
make docs-build              # build the static site into ./site/
```

Edits land on the deployed Pages site on push to `main` via [`.github/workflows/docs.yml`](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/blob/main/.github/workflows/docs.yml).

## Commit message style

Conventional Commits prefixes:

- `feat:` — new user-facing capability
- `fix:` — bug fix
- `refactor:` — internal restructuring with no behavior change
- `docs:` — documentation only
- `style:` — formatting / whitespace
- `tests:` — test-only changes
- `chore:` — tooling, deps, release plumbing

Add `!` after the prefix for breaking changes (`feat!: drop class-attr config`). Tag the version that ships the change in the message.

## Release

`make release VERSION=X.Y.Z` bumps both version locations, runs every gate, creates the commit + tag, and tells you what to push:

```bash
make release VERSION=0.20.0
git push origin main
git push origin v0.20.0
```

The PyPI publish workflow fires on the `vX.Y.Z` tag push (trusted publishing — no API token in the repo).

## Where to file things

| Need | Channel |
| --- | --- |
| Bug report / feature request | [GitHub Issues](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues) |
| Security disclosure | mauricio.benjamin@reloverelations.com (private) |
| Docs typo | PR straight against `docs/<page>.md` |
