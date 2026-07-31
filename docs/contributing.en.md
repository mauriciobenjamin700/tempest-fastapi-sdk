# Contributing

!!! tip "The most useful contribution is an issue — not a PR"
    This SDK is a **versioned public surface**: every release publishes to PyPI, and every new symbol carries a docstring, bilingual docs and a reference entry. So the flow here is **issue first**: you describe the problem (or the idea), we agree on the scope and the shape, and only then does anyone write code. A PR that arrives before that agreement almost always has to be redone.

    **[Open an issue »](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new/choose)**

## Open an issue

| Need | Where |
| --- | --- |
| Bug (something does not behave as documented) | [Issue: bug report](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new?template=bug_report.yml) |
| Feature / API idea | [Issue: feature request](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new?template=feature_request.yml) |
| Confusing or incomplete docs, an example that does not run, a typo | [Issue: docs](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new?template=docs.yml) |
| Usage question ("how do I do X with the SDK?") | [Issue: question](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new?template=question.yml) |
| Security disclosure | mauricio.benjamin@reloverelations.com (private, **never** a public issue) |

What makes an issue get resolved fast:

- the **SDK version** (`python -c "import tempest_fastapi_sdk as t; print(t.__version__)"`) and the Python version;
- a **minimal snippet** that reproduces it — complete, with imports, no `...`;
- **what you expected** and **what happened** (the whole traceback, not its last line);
- for a feature: the **use case**, not a solution already designed. The problem often has a better answer built from pieces the SDK already ships.

!!! note "A docs typo is an issue too"
    It used to say "send a PR straight away". Not anymore: every page exists **twice** (`docs/<page>.md` in PT-BR and `docs/<page>.en.md` in EN-US), and a PR fixing only one side leaves the site inconsistent. Open the issue pointing at the page and the passage — the fix lands in both at once.

## Why an issue comes before code

Three constraints of this repository that never show up in a diff:

1. **Versioning and compatibility.** Every public symbol lands in `__all__`, in the rendered reference and in the SemVer contract. Renaming it or changing its signature later is a breaking change with a migration guide.
2. **Docs in the same commit.** A public-surface change without README, `CHANGELOG.md`, the bilingual recipe and the reference stub updated does not pass — that is a project rule, not a reviewer's preference.
3. **One release per feature.** Each slice ships as its own version (bump in `pyproject.toml`, `__version__` and `uv.lock`, full gates, tag). The maintainer drives that cycle.

None of this blocks your contribution — it just means **agreeing on the scope in the issue is faster** than discovering it during PR review.

## I want to implement it

Great — say so **in the issue** and wait for the go-ahead. Then:

- work on a `feat/<slug>` / `fix/<slug>` branch off `main`;
- run `make check` (lint + format + mypy + tests) — CI runs the same target on 3.11 / 3.12 / 3.13;
- update the PT **and** EN docs along with the code, plus `CHANGELOG.md`;
- do **not** bump the version or create a tag — that belongs to the release;
- one PR per topic, with a body that states the problem before the solution.

A PR without an accepted issue is usually closed with a request to open one — that is not a rejection of the work, it is the ordering that keeps the docs and the release coherent.

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

The same gates are available through the bundled CLI: `tempest lint` / `tempest fix` / `tempest check` work in any consumer project too.

## Tests

```bash
make test                    # full suite + coverage
uv run pytest tests/admin    # the admin module only
uv run pytest -k cursor      # tests matching "cursor"
uv run pytest -x             # stop at the first failure
```

The suite uses in-memory SQLite via `tempest_fastapi_sdk.testing.test_session`. Repository tests share the `session` fixture from `tests/conftest.py`.

## Docs

```bash
make docs-serve              # mkdocs serve — live reload at http://127.0.0.1:8000
make docs-build              # build the static site into ./site/
```

Every page lives in two languages (`docs/<page>.md` + `docs/<page>.en.md`) and the build runs with `--strict` — a warning is an error.

!!! info "The docs stay organized by rule, not by review"
    A new page needs: **both** files (PT + `.en.md`), an entry in **both** navs (the top-level `nav:` and the `en` locale's `nav:` — the i18n plugin translates labels but cannot reorder a shared nav), at its **alphabetical position** in each language, plus a row in the `docs/recipes/index.md`/`.en.md` table when it is a recipe, and a stub in `docs/reference.md` when it exposes a new symbol. `uv run pytest tests/test_docs_organization.py` fails when any of that is missing or out of order — and it runs inside `make check`, hence in CI. Top-level tabs, `learning/` pages, the `getting-started/` track and the landing's tour follow a reading order on purpose. Edits reach the Pages site on push to `main` via [`.github/workflows/docs.yml`](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/blob/main/.github/workflows/docs.yml).

## Commit message style

Conventional Commits prefixes:

- `feat:` — new user-facing capability
- `fix:` — bug fix
- `refactor:` — internal restructuring with no behavior change
- `docs:` — documentation only
- `style:` — formatting / whitespace
- `tests:` — test-only changes
- `chore:` — tooling, deps, release plumbing

Add `!` after the prefix for breaking changes (`feat!: drop class-attr config`). Note in the message which version delivers the change.

## Release (maintainer)

`make release VERSION=X.Y.Z` bumps the version locations, runs every gate, creates the commit + tag and tells you what to push:

```bash
make release VERSION=0.20.0
git push origin main
git push origin v0.20.0
```

The PyPI publish workflow fires on the `vX.Y.Z` tag push (trusted publishing — no API token in the repository).
