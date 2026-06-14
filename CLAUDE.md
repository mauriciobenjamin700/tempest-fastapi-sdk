# CLAUDE.md — tempest-fastapi-sdk

Project-specific guidance for Claude Code working in this repository.
The global instructions at `~/.claude/CLAUDE.md` apply too — this file
only documents what is *different* or *load-bearing* for this SDK.

## What this is

`tempest-fastapi-sdk` is a **PyPI-distributed library**, not a
deployable service. It ships the shared FastAPI/SQLAlchemy/Pydantic
building blocks every Tempest service imports.

Two structural consequences:

- **Flat layout.** The package directory `tempest_fastapi_sdk/` lives
  at the repo root, next to `pyproject.toml`. **No `src/` wrapper.**
  Tests live in `tests/` at the root. This contradicts the
  service-layout rule in the global `CLAUDE.md` on purpose — detecting
  a `src/tempest_fastapi_sdk/` directory is a defect, flag it before
  adding features.
- **Every public surface change ships docs in the same commit.**
  README install snippets, `CHANGELOG.md`, the MkDocs site under
  `docs/` (bilingual PT-BR + EN-US), and the API reference must all
  reflect the new shape **before** the `vX.Y.Z` tag is pushed. See
  the "Documentation must follow the code" section in the global
  `CLAUDE.md`.

## Release flow

```bash
# 1. bump version
sed -i 's/version = "X.Y.Z"/version = "X.Y.Z+1"/' pyproject.toml
sed -i 's/__version__: str = "X.Y.Z"/__version__: str = "X.Y.Z+1"/' tempest_fastapi_sdk/__init__.py

# 2. CHANGELOG entry under ## [X.Y.Z+1] — YYYY-MM-DD (Keep a Changelog format)

# 3. update relevant docs/recipes/*.md (and the .en.md mirror)

# 4. gate
UV_PYTHON=3.11 make check                 # ruff + mypy + 661+ tests
UV_PYTHON=3.11 uv run --group docs mkdocs build --strict
UV_PYTHON=3.11 make smoke                 # import-test the wheel

# 5. commit + tag + push
git add -A && git commit -m "feat: vX.Y.Z+1 — <subject>"
git tag vX.Y.Z+1
git push origin main && git push origin vX.Y.Z+1
```

CI on tag push runs `release-pypi.yml` (trusted-publishing — no
token), then `docs.yml` redeploys GitHub Pages. Don't push a tag
without the docs being green.

## Roadmap — features we still owe

The SDK currently covers (Sep 2025+, post-v0.31.x):

- **Auth** — JWT/bearer/role/permission/X-Token deps, full
  bundled flow (`UserAuthService` + `make_auth_router` covering
  signup/activate/login/password-reset), `BaseUserModel` +
  `BaseUserTokenModel`, OAuth2/OIDC providers
  (`GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`),
  CSRF middleware + `make_csrf_token_dependency`.
- **DB** — `AsyncDatabaseManager`, `BaseRepository[T]` with
  bulk ops (`bulk_create_values`, `bulk_upsert`, `bulk_update`,
  `add_all`, etc.), `AlembicHelper`, `BaseModel`, audit /
  soft-delete mixins, `reorder_base_columns_first` Alembic
  hook so generated migrations ship `id`/`is_active`/
  `created_at`/`updated_at` first. `alembic.ini` ships with
  `sqlalchemy.url` empty — URL resolves at runtime from env /
  settings / constructor.
- **Standardized exceptions** (`AppException` + subclasses) +
  `register_exception_handlers`.
- **Observability** — structured logging + per-level files +
  `/logs` endpoint, metrics (CPU/RAM/GPU/Disk), Prometheus
  `/metrics` endpoint + `PrometheusMiddleware`, request-id
  middleware with contextvar propagation, typed `HTTPClient`
  (httpx wrapper with retry/backoff/circuit-breaker /
  `X-Request-ID` propagation).
- **HTTP layer** — `RequestIDMiddleware`, `RateLimitMiddleware`,
  `IdempotencyMiddleware` (memory + Redis stores),
  `BodySizeLimitMiddleware`, hardened static files, CORS,
  health + tool-spec routers.
- **Pagination** — offset + cursor.
- **Settings mixins** — every `*Settings` carries
  `title`/`description`/`examples` on every field.
- **SSE** — `EventStream`, `sse_response`.
- **Throttle** — `AttemptThrottle` (memory + Redis).
- **Upload** — `UploadUtils` with pluggable backends
  (`LocalUploadStorage`, `MinIOUploadStorage`), download helpers,
  presigned URLs.
- **MinIO / S3** — `AsyncMinIOClient` via `[minio]` extra
  (bucket lifecycle, object I/O, streaming download, presigned
  URLs).
- **Email** — SMTP via `EmailUtils` + Jinja2 template rendering
  with bundled defaults (`activation.html`, `password_reset.html`)
  shadowable by the project's `template_dir`.
- **WebPush** + webhook signatures.
- **Cache** — Redis manager + `@cached`.
- **Queue / tasks** — FastStream + TaskIQ wrappers.
- **BR validators** — CPF/CNPJ/CEP/phone.
- **BR localities** — `UF` (StrEnum, 27 siglas) + `Region`
  (5 macro-regiões IBGE), `StateBR`/`CityBR` schemas, offline
  dataset of 27 states + 5606 municipalities (IBGE-derived,
  DF as 36 administrative regions), `list_states`/`get_state`/
  `cities_by_uf`/`states_by_region`, `is_valid_uf`/`normalize_uf`,
  `is_valid_city`/`normalize_city` (accent/case-insensitive),
  `UFField`/`CityNameField`.
- **Admin panel** — Jinja + HTMX (`AdminSite`, `AdminModel`,
  `make_admin_router`).
- **CLI** — `tempest new` (scaffolds layered service +
  docker-compose), `tempest generate --docker` (regen compose),
  `tempest db init/revision/upgrade/downgrade/current/history/seed`,
  `tempest user create [--admin] / list`, `tempest secrets rotate`,
  plus quality gates (`lint`, `fix`, `format`, `fmt-check`, `type`,
  `test`, `check`).

The whole Tier S / Tier A / Tier B backlog that used to live here is
**shipped** — idempotency, cloud uploads, OTel tracing, `HTTPClient`,
the outbox pattern, `EmailUtils.render_template`, OAuth2/OIDC, CSRF +
body-size middleware, bulk repo ops, the Prometheus endpoint, TOTP/MFA,
`TenantScopedRepository`, `SlowQueryLogger`, `AlembicHelper.safe_upgrade`,
graceful shutdown, `make_websocket_router`, and the `db seed` /
`secrets rotate` CLI commands all exist today. The covers list above is
the source of truth; don't re-plan finished work.

### Next-version plan

Ordered by the priority the user set. Each item is the next minor
bump (`feat: vX.Y+1.0`) when picked up. Keep this honest — move an
item up to the covers list the moment it ships, and only add a new
entry when business pressure actually selects it.

1. **Rate-limit per user / scope** — `RateLimitMiddleware` keys on
   client IP today. Add a pluggable key extractor (authenticated
   user id, API key, tenant, or a custom callable) so limits can be
   per-principal instead of per-IP, with the same memory + Redis
   backends. Shared NAT / proxy clients stop sharing one bucket.
2. **i18n / localized `AppException` messages** — error envelopes are
   English-only. Let each `AppException` carry a message key + params
   resolved against per-locale catalogs (PT-BR default + EN), picked
   from `Accept-Language` or an explicit override, so `register_
   exception_handlers` emits the localized `message` without callers
   hand-translating.
3. **`@cached` tag / namespace invalidation** — the cache decorator
   only expires by TTL. Add tag/namespace tagging on write plus an
   `invalidate(tag|namespace)` call so a mutation can drop every
   dependent entry at once instead of waiting out the TTL.
4. **Feature flags** — `FeatureFlag` with env + Redis backends
   (Redis for runtime toggles, env for static), a dependency/guard to
   gate routes and a helper to branch in services, so rollouts and
   kill-switches don't require a redeploy.
5. **Audit trail** — beyond `AuditMixin` (timestamps), a per-entity
   mutation log capturing actor, action, before/after diff on
   create/update/delete, written in the same transaction as the
   change (reuse the outbox machinery), with a `BaseRepository` hook
   so services opt in per model.

## Conventions specific to this repo

- **Typed examples in docs.** Every code block in `README.md`,
  `docs/`, `tempest_fastapi_sdk/cli/_templates/*.tmpl` MUST have full
  type annotations (params + return). User explicitly rejected
  "magic Django-style" untyped APIs.
- **No emojis in code or docs** unless the user explicitly asks.
- **Bilingual docs.** Every page lives twice: `docs/<page>.md`
  (PT-BR, default) and `docs/<page>.en.md` (EN-US). The MkDocs
  `mkdocs-static-i18n` plugin renders both. Forgetting the `.en.md`
  mirror is a structural defect, not a polish item.
- **Bind defaults: `127.0.0.1`** in CLI-generated templates;
  `0.0.0.0` only when a frontend on a different origin consumes
  the service.
- **Logging tests must pass `file_output=False`** to avoid stray
  `logs/` folders in cwd. The default behavior writes to disk
  (since v0.22.0).
- **Explicit re-exports in every `__init__.py`.** Every public
  symbol that an `__init__.py` re-exports MUST use **both**:

  1. The PEP 484 `from x import Y as Y` form (explicit re-export),
     and
  2. A `__all__: list[str]` listing the same symbol.

  Reason: third-party consumers run a mixed bag of type-checkers
  (mypy, pyright, pylance, basedpyright) on different strictness
  settings and without project-aware `pyrightconfig.json`. Either
  form ALONE is theoretically PEP 484 compliant, but in practice
  basedpyright + Pylance strict still flag `from foo import Bar`
  inside an `__init__.py` as "private import usage" unless the
  symbol is aliased with `as Bar`. Always pair the two so any
  IDE — with or without a project config — accepts
  `from tempest_fastapi_sdk.<module> import Symbol` without a
  diagnostic. Example:

  ```python
  # tempest_fastapi_sdk/foo/__init__.py
  from tempest_fastapi_sdk.foo.bar import Bar as Bar
  from tempest_fastapi_sdk.foo.baz import Baz as Baz

  __all__: list[str] = ["Bar", "Baz"]
  ```

  Plain `from tempest_fastapi_sdk.foo.bar import Bar` (without
  `as Bar`) inside an `__init__.py` is a structural defect — flag
  it before adding features. When adding a new public symbol,
  update **both** the import alias and `__all__` in the same
  patch.
