# Changelog

All notable changes to **tempest-fastapi-sdk** are listed below.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.138.2] — 2026-07-23

### Changed

- **`BaseAppSettings` must now be the last base of a composed `Settings`.**
  This documents a constraint introduced by 0.138.1: because every mixin
  now inherits `BaseAppSettings`, Python's C3 linearization forbids
  listing `BaseAppSettings` **before** any mixin — a base cannot precede
  its own subclass. A `Settings` that listed `BaseAppSettings` in the
  middle of its bases (valid before 0.138.1, when mixins inherited raw
  `BaseSettings`) now raises `TypeError: Cannot create a consistent
  method resolution order (MRO)` at import. The fix is a one-line
  reorder — move `BaseAppSettings` to the end of the bases, which was
  already the documented convention. The value of `.env` loading is
  still order-independent; only the base *ordering* is now enforced by
  the interpreter.

### Docs

- The settings-composition recipe (`recipes/http`, PT-BR + EN) and the
  `settings/mixins` module docstring now state that `BaseAppSettings`
  **must** be the last base (not merely "by convention"), and explain
  the `TypeError` raised otherwise. Corrects the 0.138.1 note that
  implied any base ordering was safe.

## [0.138.1] — 2026-07-23

### Fixed

- **Settings mixins now always honor `.env`.** Every settings mixin
  (`ServerSettings`, `DatabaseSettings`, `RedisSettings`, … — all 16)
  now inherits `BaseAppSettings` instead of raw
  `pydantic_settings.BaseSettings`. Pydantic materializes a *complete*
  `model_config` onto every settings class, so a mixin that inherited
  raw `BaseSettings` carried `env_file=None`; when it was listed before
  `BaseAppSettings` in the bases (the documented order), that full
  config overwrote the canonical one and **`.env` was silently
  ignored** — every field fell back to its default (most visibly
  `DATABASE_URL` → the SQLite default) unless the variable was already
  exported into the process environment. Because the app reads real env
  vars in containers, the bug only surfaced locally (CLI, `.env`-driven
  runs). Inheriting `BaseAppSettings` keeps `env_file=".env"`,
  `extra="ignore"` and `case_sensitive=True` on the composed `Settings`
  regardless of base ordering. No consumer code change is required.

## [0.138.0] — 2026-07-20

### Added

- **`LocaleField`** (`tempest_fastapi_sdk.LocaleField`) — the schema-ready
  counterpart of the `Locale` enum, mirroring how `UFField` pairs with `UF`.
  It is `Annotated[Locale, BeforeValidator(normalize_locale_tag)]`, so a
  request field normalizes loose input (`"pt_BR"`, `"PT-BR"`, the bare
  primary subtag `"pt"`) into a `Locale` member and rejects unsupported tags
  with a `422`. Use the `Locale` enum for canonical values you already hold,
  and `LocaleField` on request schemas where the input is client-supplied.
- **`normalize_locale_tag`** (`tempest_fastapi_sdk.normalize_locale_tag`) — the
  loose-string → `Locale` normalizer behind `LocaleField` (also usable
  standalone), analogous to `normalize_uf`.

### Docs

- The fields recipe (PT-BR + EN) documents `LocaleField` with an Enum-vs-Field
  note cross-linked to `LocaleColumnMixin`.

## [0.137.0] — 2026-07-20

### Added

- **`Locale` enum** (`tempest_fastapi_sdk.Locale`) — a curated, dependency-free
  `BaseStrEnum` of common BCP-47 locale tags (`Locale.PT_BR == "pt-BR"`,
  `Locale.EN_US`, plus ~30 more). Each member is the tag itself, so it compares
  to and binds to a `String` column as that value.
- **`LocaleColumnMixin`** (`tempest_fastapi_sdk.LocaleColumnMixin`) — an opt-in
  SQLAlchemy mixin that adds a nullable `locale` column (BCP-47, `VARCHAR(35)`)
  to a model, so a user row can carry the language its notifications and
  localized text should render in without every project re-declaring the
  column. `NULL` means "no preference" — resolve to the app default (e.g. via
  `MessageCatalog`). Pairs with the `Locale` enum and the Web Push recipe.

### Docs

- The **database recipe** (PT-BR + EN) gains a "Locale" section documenting
  `LocaleColumnMixin` + `Locale` block-by-block, cross-linked from the Web Push
  recipe; the API reference lists the new mixin.

## [0.136.1] — 2026-07-20

### Docs

- **Rewrote the Web Push recipe (PT-BR + EN) to the layered
  `router → controller → service → repository` pattern.** Each code block
  now carries its file path and a block-by-block explanation, and the wiring
  matches the conventions used across Tempest services:
  - `WebPushDispatcher` is an infrastructure singleton built lazily in
    `resources.py` (never fails at import without VAPID keys).
  - Concrete `WebPushSubscriptionRepository` subclass instead of a bare
    `BaseRepository`; the SDK `WebPushSubscriptionService` is used as-is (no
    pass-through wrapper); a thin `WebPushController` holds the auth gate.
  - Per-layer DI providers with `session` resolved via `Depends(get_session)`
    (the previous `get_push_service(session)` example could not be used as a
    `Depends()` target).
  - Router uses a bare `/webpush` prefix mounted under `/api`, avoiding the
    `/api/api/...` double-prefix; `make_web_push_router` is framed as an
    opt-in shortcut that bypasses the controller layer.
- No public API changes — documentation only.

## [0.136.0] — 2026-07-20

### Added

- **New `<column>__<op>` filter operators**, available everywhere the
  convention filters are (dict filters, `Q`, and therefore every
  `BasePaginationFilterSchema` subclass via `get_conditions`):
  - `between` → `col BETWEEN lo AND hi`; value is an ordered two-item
    `(lo, hi)` list/tuple (a malformed value is skipped, not raised).
  - `iexact` → case-insensitive equality (`lower(col) == lower(value)`).
  - `like` / `ilike` → raw `LIKE` / `ILIKE` with the caller's own `%` / `_`
    wildcards, **not** escaped (use `contains` / `startswith` / `endswith`
    for escaped user input). `ilike` is always case-insensitive; plain `like`
    case-sensitivity is backend-defined — prefer `ilike` / `iexact` for
    portable case handling.
  - `not_in` → readability alias for the existing `notin`.

### Changed

- **Convention filters accept any non-string iterable for membership, not
  just `list`.** `build_filter_condition` — shared by `BaseRepository` dict
  filters and `Q` — now treats a `set`, `tuple`, `frozenset`, `range`, `dict`
  view or one-shot generator the same as a `list`, emitting `col.in_(values)`.
  A bare filter (`{"id": some_set}`) and the `__in` / `__notin` suffixes both
  benefit, so callers no longer wrap a `set` in `list(...)` just to hand it to
  a filter. The iterable is materialized once (so generators survive the
  count/page double-use), and `str` / `bytes` / `Mapping` stay scalars — a
  plain string value is still equality, never a character-wise `IN`.

## [0.135.0] — 2026-07-19

### Added

- **`query_param` on `UserAuthService.current_user_dependency`.** The service
  wrapper now forwards a `query_param` to `make_jwt_user_dependency`, so a
  cookieless client (e.g. the browser `EventSource`/SSE, which can't send an
  `Authorization` header) can authenticate off `?access_token=<jwt>` without
  dropping to the low-level factory. Unlike `cookie_name` it is never
  auto-derived — it's an opt-in escape hatch; enable it only over TLS with
  short-lived access tokens.

## [0.134.0] — 2026-07-19

### Added

- **`SSEData` type alias** (exported from the top level and `tempest_fastapi_sdk.sse`):
  `str | bytes | Mapping[str, Any] | Sequence[Any] | int | float | bool | None` —
  the payload contract for SSE publishers.

### Changed

- **Type the SSE `data` parameter with `SSEData`** instead of `Any` on
  `ServerSentEvent.data`, `EventStream.publish` and `SSEBroker.publish`. Runtime
  behavior is unchanged (`str`/`bytes` sent as-is, everything else JSON-encoded
  via `json.dumps(..., default=str)`); this only tightens the static type.
  Passing a bare object that relies solely on `default=str` serialization (e.g.
  a top-level `UUID`) now needs an explicit `str(...)`/dict wrap to satisfy the
  type checker.

## [0.133.1] — 2026-07-18

### Changed

- **Bump the `tempestweb` floor to `>=0.60.0`** in the `[ssr]` and `[all]` extras
  (previously `>=0.9.0`). The `tempest_fastapi_sdk.ssr` bridge
  (`Page`, `html_response`, `make_htmx_router`, `make_web_app_router`,
  `build_web_app`, `detect_build_mode`) is developed against the current
  `tempestweb` line; pinning the floor to the latest release keeps a fresh
  install from resolving a years-stale backend that predates those APIs.

## [0.133.0] — 2026-07-18

### Added

- **Batch object storage on `AsyncMinIOClient`** — three concurrent, order-aware
  helpers for the common "resolve one key per row on a list endpoint" pattern,
  replacing serial `await` loops over the single-key methods:
  - `presigned_get_urls(keys)` → `dict[str, str]` — signs many download URLs at
    once, deduplicating keys.
  - `put_objects(items)` → `dict[str, str]` — uploads many objects, each described
    by the new **`PutObjectItem`** dataclass (mirrors `put_object`'s per-object
    arguments: `content_type`, `metadata`, `length`, `part_size`).
  - `get_objects_bytes(keys)` → `dict[str, bytes]` — downloads many small objects,
    deduplicating keys.

  All three are **fail-fast** (the first failure aborts the batch and propagates)
  and bound their in-flight work with a semaphore via `max_concurrency` (default
  16; `0`/negative raises `ValueError`), so a large page cannot saturate the
  thread executor.
- **`StoredFileServiceMixin.file_urls(keys)`** — the batch counterpart of
  `file_url`. Drops `None`/empty keys, collapses duplicates and returns a
  `dict[str, str]` keyed by object key, so a page of rows resolves its presigned
  URLs in one bounded fan-out (`urls.get(row.key)` yields `None` for an empty
  key). `PutObjectItem` is now exported from the package root.

## [0.132.0] — 2026-07-15

### Added

- **`WebPushSettings.enabled`** — a property returning `bool(VAPID_PRIVATE_KEY)`,
  so services can gate dispatch on "is Web Push configured" without hand-rolling
  the check next to `webpush_kwargs()`.
- **`make_web_push_router(vapid_public_key=...)`** — optional; when set (a string
  or a zero-arg callable), mounts a public `GET {prefix}/vapid-public-key`
  returning `{"public_key": ...}` so the browser can subscribe without baking the
  key into the frontend build. Omitted by default.

## [0.131.0] — 2026-07-15

### Added

- **Versioned artifact registry** (`tempest_fastapi_sdk.artifacts`) — the generic
  core of a "DB-backed, activatable binary artifact" registry, for serving any
  versioned blob (ML models, rule bundles, config packs) from object storage:
  - `ArtifactVersionMixin` — SQLAlchemy 2.0 declarative mixin (`name`,
    `version`, `file_key`, `is_current`) to mix into a concrete `BaseModel`.
  - `ArtifactRegistry[TModel]` — `current(name)`, `list_current()` and
    `activate(version_id)` (sets `is_current` on one row and clears the
    same-`name` siblings in a single transaction).
  - `build_manifest_entries(registry, digest_source=...)` → `ArtifactManifestEntry`
    (`name`/`version`/`file_key`/`sha256`/`size`), serialization-agnostic so the
    app owns the wire shape and URL scheme.
  - `file_digest(path)` / `object_digest(client, bucket, key)` — streamed
    (1 MiB chunks) sha256 + size, memoized by immutable identity.
  - `make_activate_artifact_action(label=...)` — an admin-action factory
    (register via `actions=[action.handler]`) reusing the SDK admin context.

  Object serving/`object_digest` need the `[minio]` extra; the mixin, registry,
  `file_digest` and the admin action run on core deps. See the new
  *Artifact registry* recipe.

## [0.130.0] — 2026-07-15

### Added

- **Introspection-based bearer auth** (`tempest_fastapi_sdk.auth.IntrospectionAuth`)
  — a reusable dependency for services that do **not** issue their own tokens
  but validate an opaque bearer against an upstream userinfo/introspection
  endpoint (OAuth2 resource-server pattern). Validates a token by calling
  `userinfo_url` (a string or a lazily-resolved callable), caches successful
  lookups in-process for `cache_ttl_seconds` (evicting on 401/403), optionally
  gates access on an app-membership claim (`required_app` / `app_claim`), and
  extracts the user id from `subject_claim`. Expose `Depends(auth.get_claims)`
  and `Depends(auth.get_user_id)` on any route. See the new
  *Introspection auth* recipe.

### Changed

- **`WebPushSubscriptionService.notify_user(..., exclude_endpoints=...)`** — skip
  specific devices when fanning a payload out, the common case being a
  multi-device sync notification where the device that made the change must not
  notify itself. Excluded devices are never contacted and never pruned.
- **`CORSSettings.CORS_ORIGIN_REGEX` + `apply_cors(origin_regex=...)`** — allow a
  regex matched against the request `Origin` for session-varying origins (ngrok
  / Cloudflare dev tunnels, preview deploys). Empty disables it; unlike
  `["*"]`, it is compatible with `CORS_ALLOW_CREDENTIALS=True`.

## [0.129.0] — 2026-07-11

### Added

- **Typed SSR attribute builders** (`tempest_fastapi_sdk.ssr`) — `htmx()`,
  `aria()` and `data()` assemble a widget's open `attrs: dict[str, str]` from
  typed keyword arguments, moving `hx-*` / `aria-*` / `data-*` call sites from
  stringly-typed dicts to autocompleted, statically-checked code. Each returns
  exactly the plain `dict[str, str]` you'd write by hand (mergeable via
  `{**htmx(...), **aria(...), "id": "x"}`), so nothing is hidden. `htmx()`
  renders booleans as `"true"`/`"false"`, JSON-encodes a mapping passed to
  `vals`/`headers`, and turns `on={":after-request": "…"}` into
  `hx-on::after-request`; `data()` maps `user_id` → `data-user-id`. Pure
  functions, no extra dependency — the base `attrs` type stays `dict[str, str]`
  because the HTML attribute space is open.

## [0.128.0] — 2026-07-11

### Added

- **Serve a compiled `tempestweb` build from FastAPI** (`tempest_fastapi_sdk.ssr`)
  — host a `tempestweb build` artifact directly from an SDK service. Two entry
  points, each with the shape that fits the artifact:
  - `make_web_app_router(directory)` → an `APIRouter` that serves a **static**
    (wasm) SPA build with a single-page history fallback (unmatched paths →
    `index.html`), correct MIME for `.wasm`/`.mjs`/`.webmanifest`, `no-cache` on
    the shell + service worker (`Service-Worker-Allowed: /`), `asset_cache_control`
    for other assets, and path-traversal protection. No CSP is imposed (first-party
    code + Pyodide `wasm-unsafe-eval`); pass `security_headers=` to add one.
    Include it **last** so API routes win over the catch-all.
  - `build_web_app(directory)` → a `FastAPI` sub-app hosting a **server** build
    (WebSocket/SSE via `tempestweb.server.create_app`, `/static` client + shell at
    `/`) — the same wiring the artifact's generated `server.py` does, in-process.
  - `detect_build_mode(directory)` returns `"wasm"` or `"server"`.

  The SDK only *serves* an already-built `dist/` — building stays in the tempestweb
  CLI/CI flow. `tempestweb` is imported lazily (only the server path needs it).

## [0.127.0] — 2026-07-11

### Added

- **Admin in-place inline editing** — `Inline(editable=True, can_delete=True)`
  turns a read-only child table on the parent's detail view into an editable
  formset: one input row per existing child plus a blank row to add another,
  posting to `POST /admin/m/<parent>/<id>/inlines/<child>`. Existing rows are
  updated, a blank row with any value becomes a new child, and a checked delete
  box removes a row — all in one transaction. The parent foreign key is implied
  (forced to the parent, never rendered as an input), every child row is scoped
  to the parent (a mismatched foreign key is ignored, never cross-edited),
  upload/autocomplete columns stay on the child's own form, and validation
  errors re-render the formset in place with per-field messages. Requires the
  child model's own registered `AdminModel` and its `can_edit` (and `can_delete`
  for deletion). `build_form_fields`/`parse_submission` gained an `only=` filter
  and a new `inline_editable_names` helper backs the field selection.

## [0.126.0] — 2026-07-11

### Added

- **Model factories for tests** (`tempest_fastapi_sdk.testing`) — `ModelFactory`
  binds a `BaseModel` + default column values to a session so tests build rows
  tersely: `build()` (unsaved), `create(**overrides)` (add + flush + refresh) and
  `create_many(count)`. A default (or override) that is **callable** receives the
  row's incrementing index — how unique fields are generated — and `seq("u{n}@x")`
  is a helper for the common case. No magic: the factory never guesses required
  values, you declare the defaults; `flush` (not `commit`) keeps rows in the
  test's transaction. Framework-agnostic (no `pytest` import). `ModelFactory` and
  `seq` are exported from `tempest_fastapi_sdk.testing`.

## [0.125.0] — 2026-07-11

### Added

- **Outbound webhooks — `WebhookSender`** (`tempest_fastapi_sdk.api`) — the
  counterpart to `WebhookSignatureVerifier`: POSTs a JSON event to a subscriber
  URL, signs the exact body with the **same** verifier instance (so the receiver
  validates with that verifier), and retries transient failures — connection
  errors, `5xx`, `429` — with exponential backoff; other `4xx` are not retried.
  Sends `X-Webhook-Event` / `X-Webhook-Id` (unique uuid) / `X-Webhook-Timestamp`
  headers plus the signature. `send()` returns a `WebhookDelivery`
  (`delivered` / `status_code` / `attempts` / `error` / `delivery_id`);
  `send_many()` fans the same event out concurrently. The `httpx.AsyncClient` is
  injected (caller owns the lifecycle). `WebhookSender` and `WebhookDelivery` are
  re-exported at the package root and from `tempest_fastapi_sdk.api`. Pairs with
  the outbox (`OutboxRelay`) for at-least-once signed delivery.

## [0.124.0] — 2026-07-11

### Added

- **`BusinessMetrics`** (`tempest_fastapi_sdk.api`, `[prometheus]` extra) — a
  typed factory for application metrics bound to the shared Prometheus
  registry, so a service declares its own `counter` / `gauge` / `histogram`
  (orders placed, queue depth, job duration) without repeating the `registry=`
  wiring or touching the global default registry. Optional `namespace=` prefix;
  creation is de-duplicated by name (calling a factory twice with the same name
  returns the same metric instead of raising `Duplicated timeseries`). The
  returned objects are the real `prometheus_client` metrics — no wrapping, no
  magic — and land on the same `/metrics` endpoint as the built-in HTTP
  collectors. Re-exported at the package root and from `tempest_fastapi_sdk.api`.

## [0.123.0] — 2026-07-11

### Added

- **More `field__op` filter operators** (shared by `Q` and the `BaseRepository`
  dict filters): `in` / `notin` (`IN` / `NOT IN`), `isnull` (`IS NULL` when
  truthy, `IS NOT NULL` when falsy — note `isnull` is exempt from the
  "`None` value skips" rule since its value is a bool), and case-insensitive
  `contains` / `icontains` / `startswith` / `endswith` (`ILIKE`). These join the
  existing `gt` / `gte` / `lt` / `lte` / `ne`. Kept explicit (one branch per
  operator, no operator-name magic) so the supported set is greppable and typed.

## [0.122.0] — 2026-07-11

### Fixed

- **Admin CSS**: the components added this cycle (audit history timeline, inline
  tables, dashboard cards, lens tabs, autocomplete dropdown, CSV-import report)
  referenced an undefined `--tempest-border` variable — borders fell back to the
  text color. Defined `--tempest-border`, `--tempest-surface` and
  `--tempest-accent-soft` in `:root`, and repointed the dashboard cards and the
  autocomplete dropdown off the dark **sidebar** background vars
  (`--tempest-bg-soft` / `--tempest-bg`) onto the light `--tempest-surface`, so
  they no longer render as dark boxes on the light content area.

### Changed

- **Admin detail layout**: related-record inlines now render immediately after
  the record's own fields (before the Audit / History metadata), and `JSON`
  columns are pretty-printed in a monospaced block — matching the JSON edit
  widget — instead of showing a raw `dict` repr.

## [0.121.0] — 2026-07-11

### Added

- **Admin JSON + time field widgets** — the create/edit form now renders `JSON`
  columns as a monospaced JSON editor (pretty-printed on load, parsed +
  validated on submit — invalid JSON is a field error, not a stored string) and
  `Time` columns as an `<input type="time">`. Previously a JSON column fell
  through to a plain text input (storing the raw string) and a time column had
  no dedicated widget. Part of the admin refinement pass.

## [0.120.0] — 2026-07-11

### Added

- **Admin lenses (saved list-view presets)** — `AdminModel(lenses=[Lens(...)])`
  renders named presets as tabs above the list view. A `Lens(name, filters=...,
  order_by=..., label=...)` bundles filter conditions (same dict conventions as
  the repository) and an optional ordering; selecting it (`?lens=<slug>`) ANDs
  its filters under the user's own search/filters and applies its ordering
  unless the user clicked a column sort. An "All" tab clears the lens; the
  active lens is preserved across pagination, sort and export links. `Lens` is
  exported at the package root and from `tempest_fastapi_sdk.admin`. This closes
  the Tier 3 "lenses" item — the admin-panel evolution is now essentially
  complete (only in-place inline editing remains).

## [0.119.0] — 2026-07-11

### Added

- **Admin granular RBAC** — `make_admin_router(access_policy=...)` takes an
  optional `(principal, admin, AdminPermission)` → bool hook (sync or async)
  consulted for every model action. A denied `VIEW` / `CREATE` / `EDIT` /
  `DELETE` yields `403`; denied `VIEW` also hides the model from the dashboard
  and the sidebar nav, and denied `CREATE` / `EDIT` / `DELETE` hide the
  corresponding buttons/links. Enforced across list, detail, create, edit,
  delete, bulk (delete → `DELETE`, others → `EDIT`), export, import and FK
  autocomplete. The policy composes with — does not replace — the
  `AdminModel.can_create` / `can_edit` / `can_delete` flags (both must allow).
  With no policy the behavior is unchanged. `AdminPermission` (enum) and
  `AdminAccessPolicy` (type) are exported at the package root and from
  `tempest_fastapi_sdk.admin`. This closes the Tier 3 "granular RBAC" item of
  the admin-panel evolution.

## [0.118.0] — 2026-07-11

### Added

- **Admin CSV import** — `AdminModel(can_import=True)` exposes a CSV import page
  (`GET`/`POST {prefix}/m/{slug}/import`) that bulk-creates rows from an uploaded
  file. Each CSV row is validated and coerced with the same rules as the create
  form; valid rows are inserted best-effort (one bad row never aborts the rest)
  and the page reports the created count plus a per-row error table. A
  "Import CSV" link appears on the list view when enabled. Opt-in (default
  `False`) and also gated on `can_create`. This is the counterpart to the
  existing CSV/JSON export (Tier 3 of the admin-panel evolution).

## [0.117.0] — 2026-07-11

### Added

- **Admin dashboard business-metric cards** — `AdminSite(dashboard_cards=[...])`
  renders value / trend / partition cards at the top of the dashboard, each
  computed from the application's own data (distinct from the system CPU/RAM/disk
  panel). A `MetricCard(label, compute, help_text=...)` pairs a heading with an
  async `compute(session)` returning `MetricValue` (a number + optional unit),
  `MetricTrend` (current vs previous, exposing `delta` / `pct` / `direction`) or
  `MetricPartition` (labeled segments with a `total` + rendered bars). A card
  whose compute raises is skipped so one broken metric never blanks the page.
  `MetricCard` / `MetricValue` / `MetricTrend` / `MetricPartition` are exported
  at the package root and from `tempest_fastapi_sdk.admin`. This closes the
  Tier 2 "dashboard metrics" item of the admin-panel evolution.

## [0.116.0] — 2026-07-10

### Added

- **Admin inlines / nested relations** — `AdminModel(inlines=[Inline(Child,
  Child.parent_id)])` lists a model's 1-N children on its detail view as a
  compact table (Django `TabularInline` analog). Each row links to the child's
  own admin and an "Add" button pre-fills the parent foreign key via a create
  query param; the inline reuses the child admin's `list_display` and CRUD
  routes (falling back to read-only rows when the child is unregistered). The
  new `Inline` config (model + `fk_field` + optional `list_display`/`label`) is
  exported at the package root and from `tempest_fastapi_sdk.admin`. Rows are
  capped at 50 per inline. This closes the Tier 2 "inline / related editing"
  item of the admin-panel evolution (in-place editing on the parent form
  remains a follow-up).
- The admin create form now pre-fills editable fields from query parameters,
  which is what an inline "Add" link uses to seed the parent foreign key.

## [0.115.0] — 2026-07-10

### Added

- **Admin autocomplete FK fields** — `AdminModel(autocomplete_fields=[...])`
  renders the listed foreign-key columns as a typed HTMX search box instead of a
  `<select>` of every related row, removing the 1000-row cap and the plain-UUID
  fallback for large target tables. A new `GET /m/{slug}/autocomplete/{field}`
  endpoint (session-guarded) searches the referenced admin's `search_fields`
  (ILIKE, ORed, capped at 20 results) and returns an `<li>` option fragment; the
  edit form pre-fills the current row's label. The target table must have its
  own registered `AdminModel`. This closes the Tier 2 "autocomplete FK fields"
  item of the admin-panel evolution.

## [0.114.0] — 2026-07-10

### Added

- **Admin audit-history viewer** — `AdminModel(audit_model=...)` renders a
  per-row change timeline in the admin detail view, read from a
  `BaseAuditLogModel` table (matched on `entity` = the model name and
  `entity_id` = the row id, newest first, capped at 50 entries). Each entry
  shows the action (create/update/delete, color-coded), the actor and
  timestamp, and a field-by-field before/after diff. Pair it with
  `BaseRepository(audit_model=...)` + `add_audited` / `update_audited` /
  `delete_audited` so the trail is written. Without `audit_model` the detail
  view is unchanged (only the `created_by` / `updated_by` stamps). This closes
  the Tier 1 "audit history viewer" item of the admin-panel evolution.

## [0.113.0] — 2026-07-10

### Added

- **Management commands — project-registered `tempest <cmd>`** — a service can
  now plug its own commands into the `tempest` CLI, like Django's
  `manage.py <command>`. Expose a `typer.Typer` named `commands` (or `app`) in a
  discovered module — `src/commands.py`, `app/commands.py` or `commands.py`,
  auto-detected — and its commands appear as first-class `tempest <cmd>` entries
  sharing the SDK's help rendering. Override the location with
  `[tool.tempest] commands = "src.management"` (string or list) in
  `pyproject.toml`. A project command whose name collides with a built-in is
  skipped (with a stderr warning) so the SDK's commands always win; discovery is
  best-effort and never blocks the built-in commands. Nested Typer groups work
  (`tempest ops resync`). `mount_project_commands` in
  `tempest_fastapi_sdk.cli.commands` implements the discovery.

### Changed

- The `tempest` console script now points at `tempest_fastapi_sdk.cli.main:main`
  (a thin wrapper that mounts project commands before running) instead of the
  `app` object directly. The `app` Typer instance is unchanged for programmatic
  use.

## [0.112.0] — 2026-07-10

### Added

- **System checks (`tempest_fastapi_sdk.checks`) + `tempest check-config`** — a
  Django-style framework to validate configuration before serving traffic. A
  check is a `(context) -> Iterable[CheckMessage]` function registered with
  `@check(*tags)` / `register_check`; `CheckMessage` carries a `CheckLevel`
  (DEBUG/INFO/WARNING/ERROR/CRITICAL), message, hint and id, built via the
  `debug` / `info` / `warning` / `error` / `critical` helpers. `run_checks`
  collects messages; `run_system_checks` raises `SystemCheckError` when any
  reaches `fail_level` (default ERROR) — call it from a FastAPI lifespan to fail
  fast on a misconfigured deploy. `CheckRegistry` backs isolated sets;
  `default_registry` is the process-wide one.
  - **Built-in checks** flag common misconfigurations off any `*Settings` shape
    (best-effort via `getattr`): empty/weak signing secret (`JWT_SECRET` /
    `SECRET_KEY` / `TOKEN_SECRET`, < 32 chars), CORS `*` with credentials,
    SQLite `DATABASE_URL` while `DEBUG` is off, `DEBUG` enabled, `0.0.0.0` bind.
  - **`tempest check-config`** runs the checks against the project's settings
    (auto-detected from conventional locations, or `--settings module:attr`),
    with `--tag` filtering, `--import` for extra check modules, and
    `--fail-level`; exits non-zero when a message reaches the threshold.
  - The framework API (`CheckLevel`, `CheckMessage`, `CheckRegistry`,
    `SystemCheckError`, `check`, `register_check`, `run_checks`,
    `run_system_checks`) is re-exported at the package root.

## [0.111.0] — 2026-07-10

### Added

- **`F` / `Q` expression wrappers (`tempest_fastapi_sdk.db`)** — Django-style
  ergonomics over SQLAlchemy, wired into `BaseRepository`:
  - **`F`** references a column by name and builds arithmetic against it
    (`F("stock") - 1`, `100 - F("stock")`, `F("price") * F("qty")`). Passed as a
    `bulk_update` value it computes the new value in the database — an atomic
    update with no read-modify-write race.
  - **`Q`** captures the repository's dict-filter conventions (`name` ILIKE,
    `field__gte` comparisons, list `IN`, …) as an object combined with `&` / `|`
    / `~` for real `OR` / `NOT` trees. Pass it as the new `where=` argument on
    `get` / `get_or_none` / `first` / `list` / `count` / `exists` / `paginate` /
    `delete_many`; it is ANDed with any dict `filters`. `TenantScopedRepository`
    threads `where=` through its scoped overrides.
  - Both are re-exported at the package root and from `tempest_fastapi_sdk.db`.

### Changed

- `BaseRepository._apply_filters` now shares its per-field logic with `Q` via
  the new `build_filter_condition` helper (single source of truth for the
  filter conventions); behavior is unchanged.

## [0.110.0] — 2026-07-10

### Added

- **Object-level permissions (`tempest_fastapi_sdk.authz`)** — authorization
  that takes the row into account ("may **this** user edit **this** order?"),
  complementing the token-only static guard. Register a `(user, obj) -> bool`
  rule (sync or async) with `@permission("order.delete")` /
  `PermissionRegistry.register`; ask with `has_perm(user, perm, obj=...)` or
  enforce with `check_permission(...)` (raises `ForbiddenException`). Resolution:
  `None` user denied → superuser bypass (`is_superuser`, default `user.is_admin`)
  → object rules (any truthy grants) → static permission-set fallback
  (`permission_resolver`, default `user.permissions`); rules match exact strings
  or `order.*` / `*` wildcards. `make_permission_checker(perm, get_user=...,
  get_object=...)` builds a FastAPI route guard (omit `get_object` for a
  model-level check). `PermissionMixin` adds `await user.has_perm(perm,
  obj=...)`. Superuser predicate and resolver are injectable per
  `PermissionRegistry`; `PermissionRegistry.clear()` aids test isolation. The
  main API (`PermissionRegistry`, `PermissionMixin`, `has_perm`,
  `check_permission`, `permission`, `make_permission_checker`, `default_registry`)
  is re-exported at the package root. Imports without any extra (FastAPI is a
  core dependency).

## [0.109.0] — 2026-07-10

### Added

- **`BaseRepository` eager-loading via `with_=`** — every read method
  (`get`, `get_or_none`, `get_by_id`, `first`, `list`) now accepts
  `with_=["author", "orders.items"]` to eager-load relationships in the same
  query. Dotted paths traverse nested relationships; each hop uses
  `selectinload`, so N related rows cost one extra query per level (not N) and
  both collection and scalar relationships work. Kills the `MissingGreenlet`
  error from touching a relationship after the async session closed. An unknown
  relationship name raises `ValueError` up front.
- **`BaseRepository` lifecycle signals (`tempest_fastapi_sdk.db.signals`)** — a
  process-global registry emitting `PRE_SAVE` / `POST_SAVE` / `PRE_DELETE` /
  `POST_DELETE` around the unit-of-work write path. Register sync or async
  handlers per model with `connect` / `on_signal` (decorator) / `disconnect`;
  `RepositorySignal` enum + `SignalHandler` type + `clear_signals` (test
  isolation) are exported. Handlers registered on a base model apply to
  subclasses (MRO-resolved). `add` / `add_all` / `update` / `update_many` /
  `soft_delete` / `restore` / `delete` fire signals; the set-based bulk methods
  (`bulk_update`, `bulk_create_values`, `bulk_upsert`, `delete_many`,
  `delete_batch`) bypass them by design. A `PRE_SAVE` handler that raises vetoes
  the write (rollback + re-raise). `PRE_DELETE`/`POST_DELETE` only load the row
  when a delete handler is registered — zero overhead otherwise — and the row is
  detached before commit so its columns stay readable in `POST_DELETE`.
  `RepositorySignal` and `on_signal` are also re-exported at the package root.

### Changed

- `BaseRepository._raise_not_found` is now typed `NoReturn`, so type-checkers
  narrow correctly after a not-found guard (removed a redundant `cast`).

## [0.108.0] — 2026-07-10

### Added

- **Self-hosted AI chat, end to end (`tempest_fastapi_sdk.genai`)** — enough to
  run an LLM chat app in-process, so a separate inference service becomes an
  organizational choice rather than a necessity:
  - **`AIChatPipeline`** — composable orchestrator: memory recall → optional
    web-search augment → build messages (system + memory + context + history +
    user turn, images on the user turn) → generate (with a bounded
    tool-calling loop when tools + a tool-capable backend are present, else
    plain chat) → optional TTS → best-effort index of both turns into memory.
    `respond()` returns an `AIChatResult` (reply, sources, memory_hits,
    tool_calls_made, audio_base64); `stream()` yields tokens.
  - **`Tool`** (name/description/parameters/handler + `to_spec()`) for function
    calling, and **`make_ai_chat_router`** exposing `POST /chat` +
    `POST /chat/stream` (SSE). The router is stateless (history comes from the
    request).
  - **`ChatMemory`** — recency-aware, per-user long-term chat memory over a
    Chroma collection: `index()` embeds + upserts and evicts oldest over a
    per-user quota; `search()` does a metadata-filtered query scoped to the
    user (optionally excluding the current chat), applies a similarity floor,
    then blends a recency decay (`0.5 ** (age/halflife)`) before returning
    top-k `MemoryHit`s. Takes any `SupportsEmbed`.
  - **`ChromaVectorStore`** — a `VectorStore` backed by ChromaDB (ephemeral,
    persistent, or injected client) under the new `[genai-chroma]` extra.
  - **`OllamaGenerator` vision + tools** — `generate(images=[...])` and
    per-message `images` on `chat()` for multimodal models; `chat_with_tools()`
    returns the full Ollama message (content + `tool_calls`).
  - **`SpeechToText` parity** — `beam_size` / `vad_filter` (constructor
    defaults + per-call overrides) and `language_probability` on
    `Transcription`.
- New `[genai-chroma]` extra (`chromadb`). Install with
  `pip install tempest-fastapi-sdk[genai-chroma]`.

## [0.107.0] — 2026-07-06

### Added

- **Ollama backend for GenAI (`tempest_fastapi_sdk.genai.ollama`)** — run text
  generation and embeddings against a local (or remote) [Ollama](https://ollama.com)
  daemon over HTTP instead of loading HuggingFace weights with `torch`:
  - `OllamaGenerator` mirrors `TextGenerator`'s `generate` / `chat` / `stream`
    surface (talking to `/api/generate` and `/api/chat`), so it drops straight
    into `make_genai_router` with no other changes. No `torch`, no local
    weights, no `load()` step — Ollama owns model download and VRAM.
  - `OllamaEmbedder` implements the `SupportsEmbed` protocol (`/api/embed`),
    so it plugs into `Retriever` and the `/embed` endpoint in place of the
    `torch`-backed `Embedder` (e.g. `nomic-embed-text`).
  - `GenerationConfig` fields are mapped to Ollama `options`
    (`max_new_tokens` → `num_predict`, `repetition_penalty` → `repeat_penalty`,
    plus `seed`/`stop`; `do_sample=False` → greedy `temperature=0`).
  - New `[genai-ollama]` extra (just `httpx`). Install with
    `pip install tempest-fastapi-sdk[genai-ollama]`.
- **`TextBackend` protocol (`tempest_fastapi_sdk.genai.text`)** — the
  `runtime_checkable` text-generation surface (`generate` / `chat` / `stream`)
  that both `TextGenerator` and `OllamaGenerator` satisfy. Implement it to plug
  in any other engine (vLLM, TGI, a hosted API).

### Changed

- `make_genai_router` now type-hints `text_generator` as `TextBackend | None`
  and `embedder` as `SupportsEmbed | None` (was `TextGenerator | None` /
  `Embedder | None`). Backward compatible — the concrete classes still satisfy
  the widened protocols; the router only ever duck-typed them.

## [0.106.0] — 2026-07-06

### Added

- **Geolocation expansion (`tempest_fastapi_sdk.geo`)** — a big round of
  spatial features, all still no-paid-API:
  - **Offline geometry** (zero deps): `bounding_box` (the coarse SQL
    pre-filter for a radius), `within_radius` / `nearest` (in-memory
    proximity filter + k-nearest, generic via a `key=` extractor),
    `initial_bearing` + `destination_point` (projection along a bearing),
    `point_in_polygon` + `polygon_area_km2` (polygon geofences), and
    `path_length_km`.
  - **Database radius search** — `GeoPointMixin` (indexed `latitude` /
    `longitude` columns) + `GeoRepositoryMixin.nearby` (portable
    bounding-box pre-filter + Haversine refine, any DB) +
    `PostGISRepositoryMixin.nearby` (pushes the query into PostGIS
    `ST_DWithin`, no `geoalchemy2` dep); `make_geo_point_model` factory.
  - **Geocoding** — `GeocodingBackend` Protocol + `NominatimBackend`
    (address <-> coordinate via OpenStreetMap Nominatim, injected
    `httpx.AsyncClient`); `GeocodeResult` schema.
  - **Routing** — `OSRMBackend.matrix` (many-to-many distance/duration via
    the OSRM `table` service → `DistanceMatrix`), `OSRMBackend.route(...,
    with_geometry=True)` decoding the route polyline into
    `TravelEstimate.geometry`, per-mode OSRM profiles
    (`DEFAULT_MODE_PROFILES` + `mode_profiles=`).
  - **Polyline codec** — `encode_polyline` / `decode_polyline` (Google/OSRM
    algorithm, precision 5 or 6), pure Python.
  - **Travel modes** — added `TravelMode.BICYCLE` and
    `TravelMode.PEDESTRIAN` with duration factors.
  - **Brazil** — `uf_centroid` (offline approximate centre of each of the
    27 federative units) + `UF_CENTROIDS`; `cep_to_coordinate` (resolve a
    CEP via an injected geocoder).
  - New schemas `BoundingBox`, `GeocodeResult`, `DistanceMatrix`;
    `TravelEstimate.geometry` field.

## [0.105.0] — 2026-07-05

### Added

- **GenAI ergonomics** in `tempest_fastapi_sdk.genai`:
  - **`GenerationConfig`** — a typed Pydantic schema for generation
    parameters (`max_new_tokens` / `temperature` / `top_p` / `top_k` /
    `repetition_penalty` / `do_sample` / `seed` / `stop`). Pass it to
    `TextGenerator.generate` / `chat` / `stream` via `config=` instead of
    loose `**kwargs`; only the set fields layer over the defaults, and
    explicit `**kwargs` still win over the config.
  - **`make_genai_router`** — an opt-in FastAPI router that mounts only the
    endpoints backed by the GenAI objects you inject: `POST /generate`
    (+ `/generate/stream`, token-by-token SSE) and `/chat` for a
    `TextGenerator`, `/embed` for an `Embedder`, `/rag` for a `Retriever`,
    `/transcribe` for a `SpeechToText`, and `/tts` (returns `audio/wav`) for
    a `TextToSpeech`. Raises when handed nothing.
  - **`RedisEmbeddingCache`** — an async, Redis-backed `EmbeddingCache`
    shared across workers (JSON vectors, optional TTL). `Embedder` now
    accepts sync **or** async caches (it awaits `get`/`set` when they return
    an awaitable), so swapping `InMemoryEmbeddingCache` for
    `RedisEmbeddingCache` needs no call-site change. New `AsyncEmbeddingCache`
    Protocol documents the async shape.
- **Chat module (`tempest_fastapi_sdk.chat`)** — a reusable threaded-chat
  layer over the SDK primitives. Abstract tables `BaseConversationModel` /
  `BaseConversationParticipantModel` / `BaseMessageModel` (+ `make_*`
  factories), a `ChatService` (`start_conversation` / `post_message` /
  `list_messages` / `list_conversations` / `is_participant`), and an opt-in
  `make_chat_router`. When an `SSEBroker` is injected, every posted message
  is also published to the conversation's channel for real-time delivery,
  reusing the existing SSE fan-out.
- **Reviews module (`tempest_fastapi_sdk.reviews`)** — comments and
  0-to-5-star ratings on any polymorphic target (`target_type` +
  `target_id`). Abstract tables `BaseCommentModel` (threaded via
  `parent_id`) / `BaseRatingModel` (one vote per user, unique
  `(target_type, target_id, user_id)`) + `make_*` factories, a
  `ReviewService` (`add_comment` / `list_comments` / `rate` upsert /
  `get_user_rating` / `aggregate` → average + count + per-star
  distribution), and an opt-in `make_reviews_router`.
- **`RatingField`** in `tempest_fastapi_sdk.utils` — `Annotated[int, 0..5]`
  for a star score; re-exported at the package root.

## [0.104.0] — 2026-07-05

### Added

- **Geolocation (`tempest_fastapi_sdk.geo`)** — distance and travel-time
  estimates between two coordinates without any paid API. Two layers over
  shared schemas:
  - **Offline heuristic** (zero deps, zero network): `haversine_km` for the
    great-circle distance and `estimate_travel(origin, destination, mode)`
    for road distance (Haversine x circuity factor) and per-mode travel time
    (car average speed x mode factor). Returns a `TravelEstimate` with
    `source="heuristic"`.
  - **Real routing** (`OSRMBackend`, `[geo]` extra = `httpx`): talks to any
    OSRM server (public demo or self-hosted) for true road geometry via an
    injected `httpx.AsyncClient`; satisfies the `RoutingBackend` Protocol and
    returns a `TravelEstimate` with `source="osrm"`.
  - `TravelMode` enum (`CAR` / `MOTORCYCLE` / `BUS`); motorcycle and bus
    derive from the car by scaling the duration via
    `DEFAULT_MODE_DURATION_FACTORS`, so both layers work against a car-only
    profile. `Coordinate` (validated lat/long) + tunable
    `DEFAULT_CIRCUITY_FACTOR` / `DEFAULT_CAR_SPEED_KMH`. Submodule import like
    `vision`/`genai`; the heuristic imports without the extra.

## [0.103.0] — 2026-07-05

### Added

- **Audio language presets (PT-BR / EN-US)** in
  `tempest_fastapi_sdk.genai.audio`. A `Language` enum (`PT_BR` / `EN_US`)
  hides engine-specific identifiers:
  - `SpeechToText.transcribe(..., language=Language.PT_BR)` resolves the
    Whisper code (`"pt"` / `"en"`); still accepts a raw code or `None`
    (auto-detect).
  - `TextToSpeech.for_language(Language.PT_BR)` builds a voice with a
    sensible default Coqui model for the language; `synthesize(...,
    language=...)` accepts the enum too.
  - `LanguagePreset` + `preset_for(language)` expose the
    `whisper_language` / `tts_model` / `tts_language` mapping for
    inspection or override. Dependency-free (no `[genai-audio]` needed to
    import).

## [0.102.0] — 2026-07-05

### Added

- **Self-hosted audio (`tempest_fastapi_sdk.genai.audio`)** — voice in and
  out, on your own hardware (the leviathan pattern):
  - `SpeechToText` — transcription via **faster-whisper** (Whisper /
    CTranslate2). Lazy load, worker-thread inference, concurrency
    semaphore; auto device (CUDA/CPU) + compute type (float16/int8).
    `transcribe(audio, *, language=None, with_segments=True)` →
    `Transcription` (text, language, duration, timestamped `segments`).
    Accepts a path or `bytes`.
  - `TextToSpeech` — synthesis via **Coqui TTS**. Same lazy/threaded
    discipline. `synthesize(text, *, out_path=None, speaker=None,
    language=None, speaker_wav=None)` → WAV `bytes` (voice cloning via
    `speaker_wav` on XTTS models).
  - New `[genai-audio]` extra (`faster-whisper` + `coqui-tts`); everything
    imports lazily without it. Exported helpers `resolve_audio_device` /
    `resolve_compute_type` and the `Transcription` / `TranscriptionSegment`
    schemas.

## [0.101.0] — 2026-07-05

### Added

- **RAG over your own corpus — vector store + retriever**
  (`tempest_fastapi_sdk.genai.rag`), closing the RAG loop (index once,
  retrieve by similarity, don't re-embed per request):
  - `VectorStore` Protocol — `add(chunks, vectors)` + `search(vector,
    top_k)`.
  - `InMemoryVectorStore` — dict-backed cosine scan for dev/tests/small
    corpora.
  - `PgVectorStore` — Postgres + `pgvector`, reusing the service's existing
    database (table created on demand, cosine `<=>` search). Added
    `pgvector` to the `[genai-rag]` extra.
  - `Retriever` — ties an embedder + store: `index(chunks)`,
    `search(query, top_k)` (returns `Chunk`s with a `score`), and
    `retrieve(query)` → prompt-ready context. Works with any store via the
    `SupportsEmbed` / `VectorStore` protocols.
  - `Chunk` gained an optional `score` field (set by vector search).

## [0.100.0] — 2026-07-05

### Added

- **GenAI refinements** for RAG + semantic search:
  - `WebSearch.retrieve(query, *, extractor=None, ...)` — one-shot RAG:
    search → optional parallel body extraction → `build_context`, in a
    single call.
  - `ContentExtractor.extract_many(urls, *, concurrency=5)` — bounded
    concurrent page extraction, order preserved, failures absorbed.
  - `chunk_text(text, *, source, max_chars, overlap, ...)` — a generic,
    dependency-free chunker (any string, not just PDFs); `PdfReader.chunks`
    now uses it. Exported from `tempest_fastapi_sdk.genai.rag`.
  - `Embedder(normalize=True)` L2-normalizes returned vectors, and
    `cosine_similarity(a, b)` ranks them — semantic-search essentials
    (exported from `tempest_fastapi_sdk.genai`).

## [0.99.0] — 2026-07-05

### Added

- **Self-hosted GenAI — embeddings + scale**, slice 4 (completes the
  planned module scope):
  - `Embedder` — local text → vectors over transformers (mean pooling),
    batched, with an optional per-text vector cache (`EmbeddingCache`
    Protocol + bundled `InMemoryEmbeddingCache`; a cache hit skips loading
    the model). Same device/precision resolution + `unload` /
    `unload_if_idle` as `TextGenerator`.
  - `BatchScheduler` — coalesce concurrent inference calls into one
    batched handler call (`max_batch` / `max_wait_ms`); each caller still
    awaits its own result. Pure asyncio, model-agnostic, imports without
    the `[genai]` extra.
  - `ModelRegistry` — share loaded models by id with LRU eviction
    (`unload()` on evict), so call sites don't load the same model twice.
  - All exported from `tempest_fastapi_sdk.genai`.

## [0.98.0] — 2026-07-05

### Added

- **Self-hosted GenAI — local LLM text generation
  (`tempest_fastapi_sdk.genai.TextGenerator`)**, slice 3. Loads a
  HuggingFace causal LM once and generates on your own hardware:
  - `generate(prompt, ...)`, `chat(messages, ...)` (tokenizer chat
    template) and `stream(prompt, ...)` (token-by-token) — all async,
    running the blocking model in `asyncio.to_thread`.
  - Automatic device (`auto` → CUDA → MPS → CPU) and precision (`auto` →
    bf16 on GPU, fp32 on CPU) resolution; int8/int4 `quantization` via
    BitsAndBytesConfig (`[genai-quant]`).
  - Lazy `load()` on first use; `unload()` frees VRAM; `unload_if_idle()`
    + `idle_unload_seconds` reclaim memory between bursts (call it from a
    `@tq.interval` task — no background thread).
  - Exported helpers `resolve_device` / `auto_dtype_name`. `torch` /
    `transformers` are imported lazily, so the module and its resolution
    helpers import without the `[genai]` extra.

## [0.97.0] — 2026-07-05

### Added

- **Self-hosted GenAI — RAG context (`tempest_fastapi_sdk.genai.rag`)**,
  slice 2: feed a local LLM with web + PDF knowledge, without shipping
  data to a third party.
  - **Web search** — `WebSearchBackend` Protocol + `SearxngBackend` (the
    leviathan pattern: SearXNG JSON API over an injected `httpx` client)
    + `WebSearch` facade. Returns `SearchResult`s.
  - **Content extraction** — `ContentExtractor` fetches a URL and pulls
    the clean article body via `trafilatura`; failures surface as
    `ExtractionResult(failed=True)`, never raised.
  - **PDF reading** — `PdfReader` (PyMuPDF, detailed reading-order
    extraction) → `Document` (text + per-page + metadata) and overlapping
    `Chunk`s (`read` / `chunks`).
  - **Context assembly** — `build_context(question, sources)` renders
    `SearchResult`s and/or `Chunk`s into one prompt-ready, source-labeled
    block (mix web + PDF, optional per-source truncation).
  - New `[genai-rag]` extra (httpx + trafilatura + pymupdf); everything
    imports lazily without it. Import from `tempest_fastapi_sdk.genai.rag`.

## [0.96.0] — 2026-07-05

### Added

- **Self-hosted GenAI — capacity check (`tempest_fastapi_sdk.genai`)**,
  the first slice of running HuggingFace models on your own hardware.
  Before downloading gigabytes of weights, check whether the host can run
  a model:
  - `probe_hardware()` → `HardwareInfo` (CPU, total/available RAM, CUDA
    GPUs with per-device VRAM, Apple MPS, free disk). Degrades gracefully
    without `psutil` / `torch`.
  - `can_run(...)` → `CapacityReport` (`fits`, chosen `device`, estimated
    vs available bytes, `headroom_pct`, and a concrete `suggestion` when
    it doesn't fit — quantize, offload, or pick a smaller model).
  - `recommend(...)` picks the first precision (`bfloat16` → `int8` →
    `int4`) that fits.
  - `estimate_model_bytes` / `bytes_per_param` (the estimation math) and
    `fetch_num_params` (reads a model's parameter count from the Hub via
    `huggingface_hub`, without downloading weights). `ModelDtype` enum.
  - New `[genai]` extra (transformers + torch + accelerate + safetensors +
    huggingface-hub) and `[genai-quant]` (bitsandbytes). The capacity
    functions import **without** the extra — `torch` is only used to probe
    real GPUs. Import from `tempest_fastapi_sdk.genai`.
  - Upcoming slices: `TextGenerator` (+ quantization), `Embedder`,
    model/result caching, `BatchScheduler`, and RAG context (web search +
    PDF reading).

## [0.95.0] — 2026-07-05

### Added

- **PIX key field + helpers** (`tempest_fastapi_sdk.utils`). `PixKeyField`
  is an `Annotated` schema type that validates any of the five BACEN PIX
  key types (CPF, CNPJ, e-mail, E.164 phone, random UUID) in one field
  and normalizes to a canonical form (CPF/CNPJ → digits, e-mail →
  lowercase, phone → `+55…`, random → lowercase UUID). Companions:
  `PixKeyType` (the enum), `detect_pix_key_type(value)` (returns the type
  or `None`), `is_valid_pix_key(value)` and `normalize_pix_key(value)`.
  Detection is by shape plus CPF/CNPJ check digits — all exported from the
  package root.

### Changed

- The **validated-fields recipe** was expanded (both locales): a full
  schema + route + 422 walkthrough, a "compose your own field" section,
  common gotchas (`CentsField` vs `PriceField`, `PercentField` vs
  `RatioField`), and the new PIX-key section.

## [0.94.0] — 2026-07-05

### Added

- **Human-friendly cron helpers** (`tempest_fastapi_sdk.tasks`) — schedule
  periodic tasks without writing cron syntax:
  - `Cron` — ready-made expressions (`Cron.EVERY_WEEKDAY_9AM`,
    `Cron.EVERY_5_MINUTES`, …).
  - `CronOffset` — timezone offsets by place (`CronOffset.BRASILIA` =
    `-03:00`, plus `FERNANDO_DE_NORONHA` / `MANAUS` / `ACRE` / `UTC`).
  - `Weekday` — day-of-week tokens.
  - Builder functions — `daily`, `weekdays`, `weekends`, `hourly`,
    `every_minute`, `every_n_minutes`, `weekly`, `monthly` — each
    returning a plain cron string with range validation.
  - `@tq.cron(...)` / `AsyncTaskScheduler.cron(...)` accept these
    directly, coercing enum members to their plain string value. The
    module has no third-party dependency (imports without `[tasks]`).
- **Class-based message consumers** (`tempest_fastapi_sdk.queue`) — an
  alternative to the `@mq.on` decorator, in two explicit styles:
  `Consumer` with a constructor `channel=` + Pydantic `schema=` (no
  annotation-sniffing) and overridden `handle`, or grouped `@subscribe`
  methods (one class, many channels). Wire with `MessageBroker.register`.
- **Class-based background tasks** (`tempest_fastapi_sdk.tasks`) —
  symmetric to consumers: `TaskDef` with an overridden `run` (name in the
  constructor) or grouped `@task_method` methods, wired with
  `TaskQueue.register` (returns a `Task` or a `dict[str, Task]`).

### Changed

- **`AsyncBrokerManager` renamed to `AsyncQueueManager`** — a clearer name
  for the thin lifecycle wrapper around an injected broker, matching the
  `Async*Manager` family. `AsyncBrokerManager` stays as a backward-compatible
  alias. `MessageBroker` remains the recommended batteries-included facade.

## [0.93.0] — 2026-07-05

### Added

- **Typed facades over FastStream and TaskIQ** — application code no
  longer imports `faststream` or `taskiq`.
  - **`MessageBroker`** (`tempest_fastapi_sdk.queue`) — transport-agnostic
    pub/sub over FastStream behind a single **channel** concept. Pick the
    transport with a constructor (`MessageBroker.rabbitmq(url)` / `.redis`
    / `.kafka` / `.nats`), declare consumers with `@mq.on("channel")`
    (the handler's Pydantic type hint validates the message), and publish
    channel-first with `await mq.publish("channel", model)`. `.broker`
    stays as the escape hatch.
  - **`TaskQueue`** (`tempest_fastapi_sdk.tasks`) — TaskIQ broker +
    scheduler folded into one object. `TaskQueue.rabbitmq(url)` / `.redis`
    / `.memory()`; `@tq.task` returns a typed **`Task`** with
    `await task.enqueue(...)` (to a worker) and `await task.run(...)`
    (inline, no broker); periodic tasks via `@tq.cron(...)` /
    `@tq.interval(...)`; `start_scheduler()` / `stop_scheduler()` for
    dev, with `tq.broker` / `tq.scheduler` exposed for the standalone
    `taskiq worker` / `taskiq scheduler` CLIs.
  - Both facades keep the SDK-standard lifecycle (`connect` / `disconnect`
    / `lifespan` / `health_check` / `is_connected`).
  - The `OutboxRelay` `publish` callable plugs straight into
    `MessageBroker.publish` (channel-first).

### Changed

- The **Queue & Tasks** recipe was rewritten in the tiangolo didactic
  style around the new facades, and its stale claim that the SDK ships no
  outbox primitive was corrected (it ships `BaseOutboxModel` /
  `OutboxRelay` / `save_with_outbox`).

### Deprecated

- `AsyncBrokerManager`, `AsyncTaskBrokerManager` and `AsyncTaskScheduler`
  remain fully functional but are superseded by `MessageBroker` /
  `TaskQueue`; new code should prefer the facades.

## [0.92.0] — 2026-07-05

### Added

- **Email change / re-verification / recovery flow** on `UserAuthService`
  + `make_auth_router`, mirroring the password reset/change surface:
  - **Change email (authenticated)** — `request_email_change` (verifies
    the current password, stages the new address, emails a confirmation
    link to the NEW address) + `confirm_email_change` (consumes the
    token, flips the email, and — when `AUTH_EMAIL_CHANGE_NOTIFY_OLD`
    is on — sends a security notice to the OLD address). Routes:
    `POST /auth/email-change/request` (202) and
    `POST /auth/email-change/confirm`.
  - **Re-verify current email** — `request_email_verification` /
    `confirm_email_verification` (resend a verification link to the
    current address; confirming marks the account active). Routes:
    `POST /auth/email-verify/request` (202) and
    `POST /auth/email-verify/confirm`.
  - **Recovery (lost mailbox access)** — `request_email_recovery`, an
    **unauthenticated** entry point that proves identity with the
    account password **plus a valid MFA code when TOTP is enrolled**,
    then emails the confirmation link to the new address. Always returns
    a generic `202` for soft failures (unknown email, wrong password,
    bad/missing MFA code) so it can't enumerate accounts. Route
    `POST /auth/email-recovery/request` is **opt-in** via
    `AUTH_EMAIL_RECOVERY_ENABLED` (off by default).
  - **Old-email security notice** on a confirmed change, toggled by
    `AUTH_EMAIL_CHANGE_NOTIFY_OLD` (default `True`).
  - **Backend HTML pages** (when `AUTH_BACKEND_LINKS=True`):
    `GET /auth/email-change/{token}` and `GET /auth/email-verify/{token}`
    render self-contained success/error pages — no frontend needed.
  - New schemas exported from the package root:
    `EmailChangeRequestSchema`, `EmailChangeConfirmSchema`,
    `EmailRecoveryRequestSchema`, `EmailChangeResponseSchema`,
    `EmailChangeToken`, `EmailVerificationToken`.
  - New `UserTokenPurpose.EMAIL_CHANGE`; the existing
    `EMAIL_VERIFICATION` purpose now backs the re-verify flow.
  - 14 bundled bilingual templates (PT-BR + EN-US): `email_change.html`,
    `email_verification.html`, `email_changed_notice.html`, plus
    `email_change_success/error.html` and
    `email_verification_success/error.html`.
  - Localized subjects/bodies for the three new emails.
  - New settings: `AUTH_EMAIL_CHANGE_TTL_SECONDS`,
    `AUTH_EMAIL_VERIFICATION_TTL_SECONDS`,
    `AUTH_EMAIL_CHANGE_URL_TEMPLATE`,
    `AUTH_EMAIL_VERIFICATION_URL_TEMPLATE`, `AUTH_EMAIL_CHANGE_TEMPLATE`,
    `AUTH_EMAIL_VERIFICATION_TEMPLATE`,
    `AUTH_EMAIL_CHANGED_NOTICE_TEMPLATE`, `AUTH_EMAIL_CHANGE_NOTIFY_OLD`,
    `AUTH_EMAIL_RECOVERY_ENABLED`, `AUTH_EMAIL_CHANGE_SUCCESS_TEMPLATE`,
    `AUTH_EMAIL_CHANGE_ERROR_TEMPLATE`,
    `AUTH_EMAIL_VERIFICATION_SUCCESS_TEMPLATE`,
    `AUTH_EMAIL_VERIFICATION_ERROR_TEMPLATE`.

### Changed

- **`BaseUserTokenModel` gains a nullable `payload` column**
  (`VARCHAR(320)`) carrying flow context — the pending new email for an
  `EMAIL_CHANGE` token. **Requires a migration** in consuming projects
  (additive nullable column, safe). See the migration guide.

## [0.91.0] — 2026-07-05

### Added

- **SSE backpressure — bounded queue + overflow policy.**
  `EventStream` (and `SSEBroker`-created streams) now cap the buffered
  events at `max_queue` (default `1000`) instead of growing without
  limit when a client stalls. When the buffer fills, `overflow` decides
  what gives:
  - `"drop_oldest"` (default) — evict the stalest event, keep the
    freshest data.
  - `"drop_newest"` — discard the incoming event, keep the backlog.
  - `"block"` — apply real backpressure (the producer waits for a slot).
  `EventStream.dropped_events` counts events lost to overflow for
  metrics / logs. `max_queue=0` restores the pre-0.91 unbounded
  behavior. The `close()` sentinel is never dropped or blocked, so a
  stream can always terminate. `SSEBroker(max_queue=..., overflow=...)`
  applies the same policy to every stream it opens.
- **SSE lifecycle helpers — no more hand-rolled `try/finally`.**
  - `sse_response(..., on_disconnect=...)` runs a cleanup callback
    (awaited if a coroutine) when the client disconnects or the stream
    ends — the one place guaranteed to fire — so a bound producer task
    is cancelled or a channel unregistered without boilerplate.
  - `EventStream.response(*, on_disconnect=..., status_code=...,
    headers=...)` wraps `stream()` in an SSE response in one call.
  - `SSEBroker.response(channel, ...)` bundles the whole per-connection
    lifecycle: `register` + `sse_response` + `unregister`-on-disconnect.
    This removes the leak-prone manual wrapper the recipe used to need.
- **JWT via query string for cookieless clients.**
  `make_bearer_token_dependency` and `make_jwt_user_dependency` gained a
  `query_param` argument (e.g. `query_param="access_token"`). Token
  lookup order becomes header → cookie → query string. This unblocks
  browser `EventSource` (SSE), whose constructor accepts neither headers
  nor a body. Documented with a security warning: use short-lived access
  tokens only, over TLS, and scrub the value from access logs. Prefer a
  session cookie (`withCredentials`) whenever the client shares the
  API's origin.
- **`OverflowPolicy`** is exported from `tempest_fastapi_sdk.sse` and the
  package root.

### Fixed

- `tempest_fastapi_sdk/sse/__init__.py` re-exports now use the explicit
  `X as X` alias form required by the repo's re-export convention (was a
  structural defect flagged by strict type-checkers).

## [0.90.0] — 2026-07-04

### Added

- **Unified file store (`FileStoreUtils`).** A single facade over the
  three pieces a service usually wires by hand — `UploadUtils`
  (validate + persist), `DownloadUtils` (serve bytes through the API)
  and the presigned-URL helpers of `AsyncMinIOClient` — behind one
  object with one configuration, targeting one storage backend.
  - The backend is picked once from `source`: a directory path for
    local disk (`[upload]` extra), or an `AsyncMinIOClient` for
    MinIO/S3 (`[minio]` extra).
  - Convenience surface: `save` / `replace` / `delete` / `exists`,
    `download` / `file_response` / `stream` / `resolve`, `validate`,
    and `presigned_get_url` / `presigned_put_url` (the latter return
    `None` on the local backend, keeping the call site uniform).
  - Escape hatches for the internal pieces: `uploader`, `downloader`,
    `backend` and `client`.
  - A single `UploadStorage` backend is built and shared with the
    upload half; on MinIO the same client instance is reused by the
    download half, so the connection pool is shared, not duplicated.
- **`UploadUtils` accepts an injected backend.** New keyword-only
  `backend: UploadStorage | None` bypasses the `source`-based backend
  selection, letting `FileStoreUtils` build one backend and share it.
  `source` is now optional when `backend` is given; passing neither
  raises `ValueError`. The `[upload]` extra is only required when the
  local backend is actually selected (MinIO-only use no longer needs
  `aiofiles`).

### Docs

- New bilingual recipe **File store (unificado) / Unified file store**
  (`docs/recipes/file-store.md` + `.en.md`) and an API-reference stub
  for `FileStoreUtils` (plus `DownloadUtils`).

## [0.89.0] — 2026-07-04

### Added

- **Typed server-side rendering (`tempest_fastapi_sdk.ssr`, extra
  `[ssr]`).** A first-class SSR surface: FastAPI routes return typed
  Python components rendered to HTML — full-stack, typed, no template
  language.
  - `Page` — a typed component base (`tempest_core` `Component`). Declare
    typed fields, implement `body() -> Widget`, and optionally override
    `shell(body)` to wrap every page in a shared header/nav/footer layout
    inherited through normal Python inheritance. `render()` composes
    `shell(body())` for you.
  - `html_response(widget, *, title=None, status_code=200, htmx=False,
    document=True, lang="pt-BR")` — renders a widget tree to a FastAPI
    `HTMLResponse`. `document=True` emits a full HTML5 document (requires
    `title`); `document=False` emits a bare fragment for HTMX partial
    swaps. `htmx=True` injects a locally-served HTMX `<script>` tag
    (never a CDN).
  - `make_htmx_router(prefix="/_ssr")` — serves a **bundled** HTMX 2.x
    (shipped inside the wheel) at `GET {prefix}/htmx.js` with an
    `application/javascript` media type — CSP- and offline-friendly, no
    external host contacted.
  - The renderer (`tempestweb`) is imported lazily, so
    `import tempest_fastapi_sdk` never hard-requires the extra; install
    with `pip install "tempest-fastapi-sdk[ssr]"`.

## [0.88.0] — 2026-07-03

### Added

- **Split-endpoint presigned URLs for MinIO/S3.** `AsyncMinIOClient`
  gained `public_endpoint` / `public_secure` args, and `MinIOSettings`
  the matching `MINIO_PUBLIC_ENDPOINT` / `MINIO_PUBLIC_SECURE`. When set,
  `presigned_get_url` / `presigned_put_url` are signed against the public
  host (so the browser can reach them) while every server-side operation
  keeps using the internal `MINIO_ENDPOINT` (fast private network). A
  second `minio.Minio` client signs the URLs — the host is part of the
  SigV4 signature, so it must be signed against the public endpoint
  rather than rewritten afterwards. Fully opt-in: without
  `MINIO_PUBLIC_ENDPOINT`, presigned URLs are signed with `MINIO_ENDPOINT`
  as before. A `https://` scheme (or trailing path) on the public
  endpoint is tolerated and stripped; `https://` implies HTTPS.

## [0.87.1] — 2026-07-02

### Fixed

- **`UserAuthService.current_user_dependency()` now honours cookie
  delivery.** When `AUTH_TOKEN_DELIVERY` is `"cookie"` or `"both"` it
  auto-derives the access-token cookie name from
  `AUTH_ACCESS_COOKIE_NAME`, so any business route guarded by the
  dependency authenticates off the cookie the bundled login set — the
  `Authorization` header still wins when present. Previously the
  dependency was bearer-only, so cookie-mode clients hit
  `401 Authorization token is missing or invalid` on protected routes
  even with the cookies in the browser. A new `cookie_name=` argument
  lets callers force a specific cookie (or `None` for header-only).

## [0.87.0] — 2026-07-02

### Added

- **Configurable token delivery for the auth router.** `make_auth_router`
  now supports three ways of handing back the JWT pair, selected by
  `AUTH_TOKEN_DELIVERY` (or the `token_delivery=` argument):
  - `"bearer"` (default) — tokens in the JSON body only. Unchanged,
    fully backward-compatible behaviour.
  - `"cookie"` — `access_token` / `refresh_token` set as `HttpOnly`
    cookies on `/auth/login`, `/auth/refresh`, `/auth/logout`; the body
    omits the token values (safer against XSS). `POST /auth/refresh`
    reads the refresh token from the cookie and rotates the pair;
    `POST /auth/logout` clears the cookies (and revokes the refresh
    family when a `refresh_token_model` is wired).
  - `"both"` — the bearer endpoints stay at `/auth/*` and a parallel
    cookie set is mounted at `/auth/cookie/*`, so one backend can serve
    web (cookie) and mobile/API (bearer) clients.
- New `AUTH_COOKIE_SECURE`, `AUTH_COOKIE_SAMESITE`, `AUTH_COOKIE_DOMAIN`,
  `AUTH_ACCESS_COOKIE_NAME` and `AUTH_REFRESH_COOKIE_NAME` settings tune
  the cookie security attributes.
- New public exports: `TokenDelivery`, `AuthCookieConfig`,
  `apply_auth_cookies`, `clear_auth_cookies`.
- `make_bearer_token_dependency` / `make_jwt_user_dependency` gained a
  `cookie_name=` argument: when set, the access token is read from that
  cookie if the `Authorization` header is absent (header still wins), so
  the same guarded routes work in cookie mode.

### Notes

- Activation, signup auto-login and the MFA-verify step still return the
  JWT pair in the body regardless of `AUTH_TOKEN_DELIVERY` — cookie
  delivery covers the login / refresh / logout session lifecycle.

## [0.86.0] — 2026-06-28

### Added

- **Admin rich list filters.** `list_filter` fields now auto-pick a
  widget by column type instead of rendering a useful dropdown only for
  booleans: **enum** columns become a member dropdown, **foreign keys**
  (whose target has a registered `AdminModel`) become a related-row
  dropdown, **date/datetime** columns become an inclusive date-range
  (two date inputs → `<field>__gte` / `<field>__lte`), and any other
  column becomes a text input (equality). Booleans keep the Yes/No
  dropdown. All filters preserve search / sort / pagination in the URL.

## [0.85.0] — 2026-06-28

### Added

- **Admin file / image upload fields.** `AdminModel` gained
  `upload_fields=[...]` + `upload_storage=...`: listed String columns
  render as file inputs in the create/edit form (which auto-switches to
  `multipart/form-data`), the posted file is streamed to the storage
  backend (`LocalUploadStorage` / `MinIOUploadStorage`), and the returned
  storage key is written to the column. On edit, omitting the file keeps
  the current value; on create, a missing file for a non-nullable column
  is a required-field error. Registering `upload_fields` without
  `upload_storage` raises `ValueError`.

## [0.84.0] — 2026-06-28

### Added

- **Admin custom actions (`@admin_action`).** Beyond the three hardcoded
  bulk operations (activate / deactivate / delete), the admin now takes
  user-defined actions: decorate an async function with `@admin_action`
  and register it via `AdminModel(actions=[...])`. Each renders in the
  list view's bulk dropdown (namespaced `custom:<name>` so it can't
  collide with the built-ins), runs on the checked rows, and flashes a
  banner from its `AdminActionResult`. The handler receives an
  `AdminActionContext` (selected ids, a request-scoped repository, the DB
  session, the request, the admin session, and the acting principal) and
  stays directly callable/testable — the decorator only attaches
  metadata. Exported from `tempest_fastapi_sdk` and
  `tempest_fastapi_sdk.admin` (`admin_action`, `AdminAction`,
  `AdminActionContext`, `AdminActionResult`).

## [0.83.0] — 2026-06-28

### Added

- **Computer-vision integration via the `[vision]` extra
  (`ort-vision-sdk`).** New `tempest_fastapi_sdk.vision` submodule wraps
  the ONNX Runtime inference library with the FastAPI layer it lacks:
  Pydantic response schemas (`DetectionSchema`, `ClassificationSchema`,
  `SegmentationSchema`, `BoundingBoxSchema`, `ClassProbabilitySchema`)
  and mappers (`to_detection_schemas`, `to_classification_schema`,
  `to_segmentation_schemas`) that convert a model result into them. The
  `Detector` / `Classifier` / `Segmenter` task classes are re-exported
  **lazily** — accessing one without the extra raises a clear
  `ImportError` pointing at `[vision]`; the schemas and mappers carry no
  such dependency. Like `cache` / `queue` / `tasks`, vision is
  submodule-only (`from tempest_fastapi_sdk.vision import Detector`).

## [0.82.1] — 2026-06-28

### Fixed

- **Docs: removed code that the API never offered.** A doc audit
  (cross-checking every `tempest_fastapi_sdk` import and example against
  the package) found and fixed drift in the README:
  - The brute-force throttling recipe invented `MemoryThrottleBackend` /
    `RedisThrottleBackend`, `throttle.check()` returning a `ThrottleStatus`
    enum, `record_failure()`, and `lock_seconds=` — none exist. Rewritten
    to the real API (`AttemptThrottle(backend, max_attempts=, window_seconds=)`
    with `raise_if_blocked` / `hit` / `reset` / `status`).
  - The client-IP recipe used `trusted_proxies=` / `accept_private=` — the
    real parameter is `trusted_header=`.
  - The cookie recipe treated `SameSite` as a `BaseStrEnum` (`SameSite.LAX`)
    with `same_site=` / `key=` kwargs and an auto-`Secure` claim; `SameSite`
    is a `Literal` alias and `set_cookie` takes positional `name`/`value`
    + `samesite=`.
  - `AsyncRedisManager` was imported from the top level in several recipes
    (cache / sessions / security) — it lives in `tempest_fastapi_sdk.cache`
    (submodule-only, like `queue` / `tasks`). No code changes.

## [0.82.0] — 2026-06-28

### Added

- **`SSEBroker` — multi-worker SSE fan-out.** The SSE recipe described a
  Redis Pub/Sub bridge for broadcasting across workers but shipped no
  primitive; `SSEBroker` is that primitive. It keeps a per-channel
  registry of local `EventStream`s and fans `publish(channel, ...)` out
  to them. Pass a `[cache]` Redis client and the same broker publishes
  via Redis `PUBLISH` while a background `run()` task `PSUBSCRIBE`-s the
  channel prefix and relays every message to each worker's local
  streams — so `publish` becomes cross-process with no call-site change
  (`register` / `unregister` / `publish` are identical in both modes).
  Exported from `tempest_fastapi_sdk` and `tempest_fastapi_sdk.sse`. The
  SSE recipe now shows the in-memory and Redis-lifespan setups.

## [0.81.2] — 2026-06-28

### Changed

- **Docs: API reference stubs for symbols the top-surface filter skips.**
  The auto-generated reference's top block excludes lowercase names, so
  the session's new free functions were missing — added explicit
  `mkdocstrings` entries for `strict_types` / `typed` /
  `require_annotations` (new "Core" section) and `uf_choices` /
  `region_choices` / `city_choices` (Utils). No code changes.

## [0.81.1] — 2026-06-28

### Changed

- **Docs: dedicated Server-Sent Events (SSE) recipe.** Promoted SSE from
  a section of the real-time recipe to its own bilingual page covering a
  single endpoint, the connection-lifecycle pattern, event anatomy
  (`event`/`id`/`retry`/heartbeat comments), a broadcast-to-many "hub"
  (with the multi-worker Pub/Sub caveat), and alignment with
  `tempest-react-sdk`'s `createEventStream` / `useEventStream`. The
  real-time recipe now links to it. No code changes.

## [0.81.0] — 2026-06-28

### Added

- **`make_web_push_router` — opt-in subscribe/unsubscribe router.**
  Mounts ``POST {prefix}/subscribe`` and ``POST {prefix}/unsubscribe``
  (default prefix ``/api/push``) wired straight to a
  `WebPushSubscriptionService`, mirroring `make_auth_router`. Both accept
  the raw ``PushSubscription.toJSON()`` body, so `tempest-react-sdk`'s
  `WebPushClient` `onSubscribe` / `onUnsubscribe` callbacks hit them
  directly. The caller injects `service_factory`, `session_factory` and a
  `current_user_id` dependency; the request ``User-Agent`` is stored as
  the device label by default. Exported from `tempest_fastapi_sdk` and
  `tempest_fastapi_sdk.webpush`.

## [0.80.0] — 2026-06-28

### Added

- **Web Push subscription storage + service.** The webpush module gained
  the missing persistence layer so apps no longer hand-roll it:
  `BaseWebPushSubscriptionModel` (abstract table, one row per user device,
  unique `endpoint`) + `make_web_push_subscription_model(user_table=...)`
  factory, mirroring the `BaseUserTokenModel` pattern; and
  `WebPushSubscriptionService` (generic over the concrete model) with
  `subscribe` (idempotent upsert keyed by endpoint), `unsubscribe`,
  `list_for_user`, `prune`, and `notify_user` — which fans a payload out
  to every device and **auto-prunes the ones the push service reports as
  gone (404/410)**. The wire shape matches `PushSubscription.toJSON()`,
  so it lines up 1:1 with `tempest-react-sdk`'s `WebPushClient`
  `onSubscribe` / `onUnsubscribe` callbacks. Exported from
  `tempest_fastapi_sdk`, `tempest_fastapi_sdk.webpush` and
  `tempest_fastapi_sdk.db`.

## [0.79.0] — 2026-06-27

### Changed

- **`BaseService` / `BaseController` `update` payload is now generically
  typed.** Both classes gained an optional third type parameter
  `UpdateT` (bound to `BaseSchema`, default `BaseSchema`) so `update`
  accepts the project's own update schema instead of the bare
  `BaseSchema`: `BaseService[Repo, Resp, MyUpdateSchema]` /
  `BaseController[Service, Resp, MyUpdateSchema]`. The default keeps every
  existing two-argument subclass working unchanged. Implemented with a
  PEP 695/696 `TypeVar` default via `typing_extensions` (already present
  through Pydantic).

## [0.78.0] — 2026-06-27

### Added

- **`BaseService.update` (and `BaseController.update`).** The service
  skeleton now ships a generic update method: fetch by primary key, copy
  the fields present in the payload (`data.to_dict()`, which drops unset
  and ``None`` values) onto the instance, persist via
  ``repository.update`` and return the mapped response. Because unset
  fields are skipped, the same method serves full (PUT) and partial
  (PATCH) updates. ``BaseController.update`` forwards to it, matching the
  existing pass-through layer. Override either when an update needs
  orchestration.

## [0.77.0] — 2026-06-27

### Added

- **Generic validated field types (`tempest_fastapi_sdk.utils.fields`).**
  A base set of `Annotated` Pydantic types that bake a validation rule
  into the type, following the `*Field` convention (so the schema reads as
  what it is instead of repeating `Field(gt=0, ...)`): integers
  `PositiveIntField`, `NonNegativeIntField`, `CentsField` (money in minor
  units), `PortField`; floats `PositiveFloatField`, `NonNegativeFloatField`,
  `PercentField` (0..100), `RatioField` (0..1), `LatitudeField`,
  `LongitudeField`; `PriceField` (non-negative `Decimal`, 2 places); and
  strings `NonEmptyStrField` (trim + non-empty), `SlugField`,
  `HexColorField`. Exported from `tempest_fastapi_sdk` and
  `tempest_fastapi_sdk.utils`.

## [0.76.0] — 2026-06-27

### Changed

- **BR field types now carry a `Field` suffix.** The Pydantic
  annotated-type aliases were renamed to make their role obvious at the
  import site, matching `UFField` / `CityNameField`: `CPF` -> `CPFField`,
  `CNPJ` -> `CNPJField`, `CPFOrCNPJ` -> `CPFOrCNPJField`,
  `PhoneBR` -> `PhoneBRField`, `CEP` -> `CEPField`. The old names remain
  exported as **deprecated aliases** (identical types), so existing
  imports keep working; prefer the `*Field` names. Slated for removal in
  a future major.

## [0.75.0] — 2026-06-27

### Added

- **Frontend `<select>` choices for BR localities (`ChoiceBR`,
  `uf_choices`, `region_choices`, `city_choices`).** The bundled
  states/cities dataset already backed the `UFField` / `CityNameField`
  validation fields; these helpers make the *other* role first-class —
  feeding dropdowns. Each returns `list[ChoiceBR]`, a typed Pydantic
  schema (`value`/`label`) that serializes as
  `{"value": ..., "label": ...}` and shows up typed in OpenAPI:
  `uf_choices()` pairs each acronym (the same value `UFField` validates)
  with the full state name, `region_choices()` lists the 5 IBGE
  macro-regions, and `city_choices(uf)` lists a state's municipalities.
  Exported from `tempest_fastapi_sdk` and `tempest_fastapi_sdk.utils`.

## [0.74.0] — 2026-06-27

### Added

- **Runtime type-enforcement decorators (`strict_types`, `typed`,
  `require_annotations`).** Type hints are erased at runtime; these close
  the gap. `strict_types` validates arguments and return against the
  annotations with no coercion (a `str` where `int` is annotated raises);
  `typed` does the same but coerces when Pydantic safely can
  (`"1"` -> `1`); both are built on `pydantic.validate_call` (already a
  dependency). `require_annotations` enforces at decoration time that a
  function *is* annotated, raising `TypeError` listing any unannotated
  parameter / return — `self`/`cls` and `*args`/`**kwargs` are exempt and
  `Any` counts as a valid annotation. Exported from `tempest_fastapi_sdk`
  and `tempest_fastapi_sdk.core`.
- **`[tool.tempest] typing_strictness` knob for the CLI gates.** A new
  config field (`lenient` / `standard` / `strict`, default `standard`)
  controls how strictly `tempest lint` / `fix` / `type` / `check` enforce
  typing: it layers ruff ANN rules and mypy flags on top of the project's
  own config without relaxing it. Override per run with
  `--strictness/-s`. `ANN401` (which flags `Any`) is never enabled at any
  level — the point is that things ARE annotated, not that they avoid
  `Any`. Read via `tempest_fastapi_sdk.cli.config` (`TempestConfig`,
  `load_tempest_config`).

### Changed

- **ruff `ANN` is now enabled in the SDK and in `tempest new` templates.**
  Generated projects ship `select = [..., "ANN"]` with
  `ignore = [..., "ANN401", "ANN002", "ANN003"]` plus the
  `[tool.tempest] typing_strictness = "standard"` knob, so a fresh
  service requires annotations out of the box while leaving `Any`
  allowed. Generated templates were also fixed to pass their own
  `tempest check` cleanly (import ordering, `known-first-party = ["src"]`,
  sorted `__all__`).

## [0.73.0] — 2026-06-27

### Added

- **`BaseStrEnum` / `BaseIntEnum` gained `choices`, `from_value`,
  `has_value`, and `has_key`.** Alongside the existing `values` /
  `keys` / `to_dict`, the shared `_EnumHelpers` mixin now exposes:
  `choices()` returning `(value, name)` pairs for HTML `<select>` /
  form widgets; `from_value(value, *, default=...)` a lenient
  constructor that resolves a member from a raw value or member name
  (exact, then case-insensitive), raising `ValueError` on no match or
  returning an explicit `default` when supplied; and the `has_value` /
  `has_key` membership predicates. Both bases inherit them, so every
  service enum gets the helpers for free.

### Changed

- **Email recipe documents production SMTP and credential handling.**
  The `email` recipe (PT-BR + EN) gained a "Production" section: read
  every `SMTP_*` field from the environment (never hardcode or commit
  the password), use a Gmail App Password with 2FA, a verified
  `SMTP_FROM_ADDR` domain (SPF/DKIM/DMARC), and a provider/port/TLS
  table (Gmail 587 STARTTLS vs 465 implicit TLS, AWS SES, SendGrid,
  MailHog). Mirrors the two real production setups in use.

## [0.72.0] — 2026-06-26

### Added

- **`AdminTheme` — typed theming for the admin panel.** A new
  `AdminTheme` dataclass carries appearance overrides (accent /
  accent_hover / danger colors, header & sidebar backgrounds, page
  background, border radius, font family, logo image + alt, favicon,
  footer text, dark mode, and a `custom_css_url` escape hatch) through
  typed, documented parameters. Pass it via `AdminSite(theme=...)`; the
  SDK injects a `<style>` block of `:root` overrides after `admin.css`
  (so it wins) plus the favicon / logo / footer chrome. `AdminTheme()`
  is a no-op that reproduces the stock look, so existing sites are
  unchanged. String fields reject `< > { } "` at construction to keep a
  value from breaking the injected markup. Exported from
  `tempest_fastapi_sdk` and `tempest_fastapi_sdk.admin`.

## [0.71.1] — 2026-06-26

### Fixed

- **File logging no longer crashes the app on a non-writable filesystem.**
  `configure_logging` now treats file logging as best-effort: if `log_dir`
  cannot be created or its files cannot be opened (read-only mount, missing
  write permission, hardened container, serverless, CI), the file handlers
  are skipped, a warning is emitted (to the logger when stdout is on, else
  straight to `stderr`), and the service keeps running with stdout logging
  instead of dying at import time with
  `PermissionError: [Errno 13] ... 'logs'`. `_build_file_handlers` also
  closes any handlers it opened before a mid-build failure so no file
  descriptors leak.
- **Scaffold `Dockerfile` fixed so the non-root `app` user can write
  `logs/`.** `WORKDIR /app` created `/app` as `root` before the
  `COPY --chown=app:app`, and `--chown` only sets ownership on the copied
  *contents* — not on the pre-existing `/app` directory node — so the `app`
  user could not create `logs/` (or the SQLite `app.db`) inside it and the
  container crash-looped at startup. The template now runs
  `RUN mkdir -p /app/logs && chown -R app:app /app` after the copy. Existing
  projects: regenerate with `tempest generate --dockerfile --force` or add
  that line by hand.

## [0.71.0] — 2026-06-26

### Added

- **`Dockerfile` + `.dockerignore` in the scaffold** — `tempest new`
  now ships a multi-stage, uv-based `Dockerfile` (builder stage installs
  deps into `/app/.venv`; final stage copies only the venv + source and
  runs as a non-root `app` user) plus a `.dockerignore` that keeps the
  build context lean and never bakes `.env` / `*.db` / `logs/` into the
  image. The final stage sets `SERVER_HOST=0.0.0.0` so the container is
  reachable without a `.env`.
- **`tempest generate --dockerfile`** — regenerate the `Dockerfile` +
  `.dockerignore` in an existing project. The `EXPOSE` / `SERVER_PORT`
  is read from the project's `.env` / `.env.example` (`SERVER_PORT`),
  falling back to `8000`. Refuses to overwrite without `--force`, like
  the other generators, and composes with `--docker` / `--src`.

### Notes

- The generated `docker-compose.yaml` stays infra-only (no `app`
  service); the `Dockerfile` is standalone. Add an `app:` service with
  `build: .` by hand if you want a one-command stack.

## [0.70.1] — 2026-06-26

### Fixed

- **`AlembicHelper.current()` async fallback now triggers** — the 0.70.0
  fix only caught the missing-DBAPI error around `engine.connect()`, but
  SQLAlchemy 2.0 imports the DBAPI eagerly inside `create_engine()`, so
  asyncpg-only projects still crashed with `ModuleNotFoundError: No
  module named 'psycopg2'`. The guard now also wraps `create_engine`, so
  `current()` / `tempest db current` correctly fall back to the async
  driver.

## [0.70.0] — 2026-06-26

### Fixed

- **`AlembicHelper.current()` on async-only projects** — `current()`
  built a sync engine from the stripped URL (`postgresql://…`), which
  defaults to the `psycopg2` driver. Projects that install only an async
  DBAPI (e.g. `asyncpg`) crashed with `ModuleNotFoundError: No module
  named 'psycopg2'` (and so did `tempest db current`). It now falls back
  to reading `alembic_version` through the async driver when no sync
  DBAPI is available.

### Added

- **`AlembicHelper.stamp(..., purge=True)` + `tempest db stamp --purge`**
  — clear `alembic_version` before stamping. Required after a manual
  squash where the recorded revision no longer exists in the script
  directory: a plain stamp fails with `Can't locate revision`, while
  `--purge` drops the stale pointer and stamps the new baseline cleanly.

## [0.69.0] — 2026-06-25

### Added

- **`tempest db squash` + `AlembicHelper.squash(...)`** — collapse the
  whole migration history into a single fresh root revision. Migration
  files accumulate without bound as a project evolves; `squash` runs
  `downgrade base` on the configured (development) database, moves the
  old revisions into `alembic/versions/_squashed_<oldhead>/` (a
  subdirectory Alembic ignores — pass `--no-backup` / `backup=False` to
  delete instead), autogenerates one root migration from
  `BaseModel.metadata`, and re-applies it. The CLI requires `--yes`
  because the flow drops every table in the target database. Production
  databases are untouched — reconcile them with the new
  `tempest db stamp head` after deploying the collapsed tree.
- **`tempest db stamp <revision>`** — CLI surface for the existing
  `AlembicHelper.stamp`. Marks an already-populated database (e.g.
  production after a squash) as migrated without recreating tables.
  Defaults to `head`.
- **`tempest db backup` / `tempest db restore` + `DatabaseBackup`** —
  snapshot a database to a file and back, dispatching per dialect.
  PostgreSQL uses `pg_dump` / `pg_restore` (custom `-Fc` by default, or
  plain `.sql` via `psql` — chosen from the file extension); SQLite
  copies the database file. Backups default to a timestamped path under
  `backups/`. `restore` is a clean restore by default (drops + recreates
  so it is a faithful copy) and requires `--yes`; pass `--no-clean` to
  apply on top of the current schema. The Postgres password is passed
  via `PGPASSWORD` so it never appears in `ps`. `DatabaseBackup`,
  `BackupToolMissingError` and `UnsupportedBackupBackendError` are
  re-exported at the top level.

## [0.68.0] — 2026-06-21

### Added

- **`AdminSite.automap(source, ...)` + `discover_models(source, ...)`** —
  register every concrete `BaseModel` under a package in one call
  instead of one `register` per table. Point it at a dotted module path
  (`"src.db.models"`) or a module; abstract bases (no `__tablename__`)
  are skipped automatically. Supports `exclude=` (class / class name /
  table name), `skip_registered=` (default `True`, so hand-tuned admins
  registered first are preserved), and `**admin_kwargs` applied
  uniformly. `discover_models` is re-exported at the top level.
- **`AdminSite(brand=...)`** — optional centered header brand text,
  exposed to templates via the new `AdminSite.brand_text` property
  (falls back to `title` when unset, so existing sites are unchanged).

### Changed

- **Admin panel layout** — the header brand is now **centered** on
  screen, and on desktop (≥769px) the sidebar is **fixed full-height and
  overlays the header and footer** (raised `z-index`); the mobile
  off-canvas behavior is unchanged. Bundled-CSS only, no config.

## [0.67.0] — 2026-06-21

### Added

- **`backfill_non_nullable_defaults` Alembic hook** — autogenerate now
  gives every **added** `NOT NULL` column a `server_default` derived from
  its scalar Python `default=`, so adding a non-nullable column to a table
  that already has rows backfills them instead of raising
  `NotNullViolationError: column "x" contains null values` on PostgreSQL.
  Covers `bool` / `int` / `float` / `str` / `Enum` (uses `.value`); leaves
  callable / SQL-expression defaults (`uuid4`, `func.now()`) and
  default-less columns untouched (those need a hand-written data
  migration). `CreateTableOp` columns are never touched. Re-exported at
  the top level and from `tempest_fastapi_sdk.db`.

### Changed

- **The scaffolded `env.py` now composes both revision hooks** —
  `compose_hooks(reorder_base_columns_first, backfill_non_nullable_defaults)`
  — so freshly generated migrations are both column-ordered and
  backfill-safe out of the box. **Existing projects:** update your
  `alembic/env.py` import + `process_revision_directives` wiring to pick
  up the new hook (see the "A new `NOT NULL` column no longer explodes"
  admonition in the Database recipe).

## [0.66.2] — 2026-06-21

### Changed

- **Docs: the auth-flow refresh section now points to the built-in
  DB-backed refresh tokens** instead of telling readers to roll their
  own table. The "both tokens rotate" warning now clarifies that
  stateless is just the default, and a new tip links to the
  `docs/recipes/refresh-tokens.md` recipe (opt-in `refresh_token_model`
  with rotation, reuse detection and `POST /auth/logout`). Docs-only.

## [0.66.1] — 2026-06-21

### Changed

- **Docs: the "Receitas" / "Recipes" nav is now sorted alphabetically**
  by label (the landing `recipes/index.md` stays first), so readers can
  scan and find a recipe predictably. Docs-only change — no public API
  delta.

## [0.66.0] — 2026-06-21

### Added

- **DB-backed (opaque) refresh tokens with rotation, reuse detection and
  revocation** — opt-in via a new `refresh_token_model=` argument on
  `UserAuthService`. When wired, the refresh token becomes an **opaque**
  value whose SHA-256 hash is persisted (the access token stays a
  stateless JWT). Every `POST /auth/refresh` marks the presented token
  single-use and mints a new one in the same rotation **family**;
  replaying an already-rotated token is treated as theft and **revokes
  the whole family** (`401`). Without the model the service keeps the
  legacy stateless JWT refresh behavior — **no breaking change**.
- **`BaseUserRefreshTokenModel` + `make_user_refresh_token_model`** — the
  abstract opaque-refresh-token row (`token_hash`, `family_id`,
  `expires_at`, `used_at`, `revoked_at`) and the one-call factory to bind
  a concrete table to the project's user table, mirroring
  `BaseUserTokenModel` / `BaseUserRecoveryCodeModel`. Re-exported at the
  top level.
- **`UserAuthService.issue_token_pair(session, user, *, family_id=None)`**
  — async issuance path used by the router at every login-equivalent
  step; opaque+persisted when a refresh-token model is wired, stateless
  JWT otherwise.
- **`UserAuthService.revoke_refresh_token(session, *, refresh_token,
  all_sessions=False)`** — logout: revoke the token's family (or every
  active token of the user). Idempotent.
- **`POST /auth/logout`** on the bundled router — revokes a DB-backed
  refresh token (family, or all sessions with `all_sessions=true`).
  Mounted **only** when a `refresh_token_model` is wired; absent in
  stateless mode. Request body: new `LogoutSchema` (re-exported at the
  top level).
- **Recipe — "Refresh tokens (rotação/revogação)"** (`docs/recipes/
  refresh-tokens.md` + `.en.md`), wired into the docs nav.

### Changed

- **`UserAuthService.refresh_tokens`** now branches on whether a
  refresh-token model is wired: DB-backed rotation + reuse detection when
  present, the previous stateless JWT decode path when absent. The
  `POST /auth/refresh` endpoint commits the rotation and its docs cover
  both modes.

## [0.65.0] — 2026-06-21

### Added

- **`POST /auth/refresh` on the bundled auth router** — exchange a valid
  refresh token for a brand-new `access_token` + `refresh_token` pair
  **without** re-entering email + password. The token must carry the
  `refresh` claim (a replayed *access* token is rejected with `401`), the
  subject must resolve to an **active** user (inactive → `403`), and an
  expired / malformed / wrongly-signed token returns `401`. Both tokens
  rotate on success. Response reuses `LoginResponseSchema`.
- **`UserAuthService.refresh_tokens(session, *, refresh_token)`** — the
  public service method behind the endpoint, returning
  `(user, access_token, refresh_token)` for callers that drive the flow
  without the router.
- **`RefreshSchema`** — request body for the new endpoint, re-exported at
  the top level (`from tempest_fastapi_sdk import RefreshSchema`).

## [0.64.1] — 2026-06-21

### Fixed

- **`StoredFileServiceMixin` now composes cleanly with `BaseService` under
  strict mypy.** The mixin declared `repository: BaseRepository[ModelType]`,
  which clashed with `BaseService`'s own generic `repository` attribute and
  made `class X(BaseService[...], StoredFileServiceMixin[...])` fail type
  checking (`Definition of "repository" ... is incompatible`). The mixin no
  longer re-types the host-provided `repository`; its public methods stay
  precisely typed via `ModelType`.

## [0.64.0] — 2026-06-21

### Added

- **`StoredFileServiceMixin[Model]`** — a service mixin that encodes the
  single-key stored-file flow once, parameterized by field name:
  - `set_file(ref, file, *, field, subdir=..., filename=..., keep_original_name=...)`
    resolves the entity (detach-safe), uploads the new file and deletes the
    old one via `UploadUtils.replace` (new written before old deleted),
    writes the key back and commits.
  - `clear_file(ref, *, field)` deletes the object and nulls the field
    (no-op, no commit, when the field is already empty).
  - `file_url(key, *, expires=...)` returns a presigned download URL, or
    `None` for an empty key.

  Removes the ~13-line boilerplate every service reimplements for avatars,
  banners, covers and attachments. Reads its `upload_utils` and `storage`
  collaborators off `self`, so the owning service keeps configuration (size
  limits, allowed types, bucket). Covers the common "one key field →
  presigned URL" case; resize/thumbnail pipelines, multi-variant assets and
  galleries are out of scope (compose `UploadUtils` directly). See the
  **Arquivo no serviço (mixin)** recipe.
- **`SupportsUpload`** and **`SupportsPresign`** — structural-typing
  protocols describing the collaborators `StoredFileServiceMixin` needs
  (satisfied by `UploadUtils` and `AsyncMinIOClient`), so importing the
  mixin never pulls the optional `[upload]` / `[minio]` extras.

## [0.63.0] — 2026-06-21

### Changed

- **`UserAuthService.current_user_dependency()` now loads the authenticated
  user on the request-scoped session** (`db.session_dependency` by default)
  instead of opening its own short-lived session through `load_user`.
  Previously the returned `UserModel` was **detached** — mutating it and
  committing/refreshing on the request's repository session raised
  `InvalidRequestError: Instance is not persistent within this Session`,
  and lazy-relationship access raised `DetachedInstanceError`. The user is
  now attached to the same session repositories use. **Breaking** only for
  apps whose repositories do not share the auth service's session callable;
  pass `session_dependency=` to point both at the same provider. See the
  **Migration guide** (`docs/migration.md`). The single-argument `user_loader` path
  of `make_jwt_user_dependency` is unchanged; the new behavior is opt-in via
  the new `session_dependency=` parameter (which `current_user_dependency`
  now passes by default).
- **`BaseRepository.resolve()` re-attaches detached instances** via
  `session.merge()` instead of returning them as-is. A detached model passed
  to a mutating service is brought back into the active session, so the
  subsequent `update()` commits instead of raising. `merge` issues a
  `SELECT` only when the row is not already in the session's identity map.

### Added

- **`make_jwt_user_dependency(..., session_dependency=...)`** — when given a
  request-scoped session provider, the dependency injects it and calls the
  two-argument loader `user_loader(subject, session)`, sharing the session
  with the request's repositories.
- **`UserAuthService.current_user_dependency(session_dependency=...)`** —
  override the session provider shared with repositories (defaults to
  `self.db.session_dependency`). Now raises `RuntimeError` eagerly when the
  service was built without `db=`.

## [0.62.0] — 2026-06-20

### Added

- **`BasePaginationFilterSchema.get_pagination_conditions()`** and
  **`CursorPaginationFilterSchema.get_pagination_conditions()`** — the
  counterpart to `get_conditions()`. Where `get_conditions()` strips the
  pagination keys to expose the domain filters, this returns **only** the
  pagination/sort keys (`page`/`page_size`/`order_by`/`ascending` for
  offset, `cursor`/`limit`/`order_by`/`ascending` for cursor). A service
  can now forward a filter schema to `paginate` / `cursor_paginate`
  without manually unpacking the model:

    ```python
    data = await repo.paginate(
        filters=f.get_conditions(),
        **f.get_pagination_conditions(),
    )
    ```

  This replaces the `**filter_schema` idiom, which leaked domain filters
  (e.g. `is_active`) into keyword arguments the repository does not accept.

## [0.61.0] — 2026-06-15

### Added

- **`POST /auth/password-change`** — a `make_auth_router` endpoint for an
  **authenticated** user to change their own password while logged in.
  Requires a valid bearer `access_token`; the user re-enters their
  `current_password` (mismatch → **401**) and the `new_password` is
  validated against the configured password policy (violations → **422**).
  Returns **204**; existing tokens stay valid (no session revocation).
  Distinct from the email-token reset flow.
    - **`UserAuthService.change_password(session, *, user,
      current_password, new_password)`** — the backing service method.
    - **`PasswordChangeSchema`** — request body
      (`current_password` + `new_password`), exported at the package root.

## [0.60.0] — 2026-06-15

### Added

- **`BaseRepository.resolve(id_or_instance)`** — accepts either a
  primary-key `UUID` or an already-loaded model instance and always
  returns the instance (`get_by_id` when a `UUID`, pass-through
  otherwise). Removes the `if isinstance(x, UUID): ...` boilerplate every
  service reimplements for methods that take `UUID | Model`.
- **`BaseRepository.exists_excluding(filters, *, exclude_id)`** — "is this
  value already used by *another* row?" The uniqueness check needed when
  updating a unique field (email / phone / username): plain `exists`
  would match the row itself; this excludes `exclude_id`. Pass
  `exclude_id=None` (the create case) to behave like `exists`.
- **`UploadUtils.replace(old_key, file, ...)`** — save a new object and
  delete the one it replaces, in one call. The new file is persisted
  **first** (so a validation/write failure leaves the old object intact),
  then `old_key` is deleted through the **same** configured backend —
  avoiding the save-through-one-backend, delete-through-another mistake.
  `old_key=None` skips the delete, so the same call serves first uploads
  and replacements.

## [0.59.1] — 2026-06-14

### Changed

- **Database recipe rewritten in the tutorial-first (tiangolo) pattern.**
  The `docs/recipes/database.*` page was shallow — it covered only the
  mixins, a hand-rolled cursor query, and Alembic, and skipped most of
  the DB layer. It is now a progressive, nine-section guide with
  complete runnable examples, admonitions and per-section recaps:
    - **`BaseModel`** — the four canonical columns, `NAMING_CONVENTION`,
      auto `__tablename__`, and the `to_dict` / `update_from_dict` /
      equality helpers.
    - **`AsyncDatabaseManager`** — engine/pool config, the per-request
      `session_dependency`, lifespan wiring, `health_check`,
      `db_url_safe`.
    - **`BaseRepository`** — direct vs. subclass usage, the mappers, and
      the full async CRUD surface (`get` / `get_or_none` / `first` /
      `list` / `exists` / `count` / `add` / `update` / `delete*` /
      `soft_delete` / `restore`).
    - **Convention-based filters** — `name` ILIKE, `bool`, `list`,
      `date`, `start_in` / `end_in`, and the `<col>__<op>` comparison
      suffixes.
    - **Bulk operations** — `add_all` / `update_many` vs.
      `bulk_create_values` / `bulk_update` / `bulk_upsert`.
    - **Pagination** — both the built-in `paginate` (offset) and
      `cursor_paginate` (cursor), replacing the old hand-rolled cursor
      example that reinvented logic the SDK already ships.
    - **`SlowQueryLogger`** — new section.
  No public API changed — documentation only.

## [0.59.0] — 2026-06-14

### Added

- **Bilingual auth emails and backend pages (`AUTH_DEFAULT_LOCALE`).**
  The bundled activation / password-reset **emails** and the
  backend-only **HTML pages** now ship in two languages out of the box —
  Brazilian Portuguese (`pt-BR`, the new default) and US English
  (`en-US`) — so a service gets fully localized account flows with zero
  custom templates.
    - **`AUTH_DEFAULT_LOCALE`** setting (default `"pt-BR"`) — language of
      the bundled emails and pages. Normalized case-insensitively, so
      `PT-BR`, `pt_br` and `ptbr` all resolve to `pt-BR`; `EN`, `en_US`
      and `enus` to `en-US`.
    - **Emails** always render in `AUTH_DEFAULT_LOCALE` (subject, plain
      body and HTML), since they have no request context.
    - **Backend HTML pages** (`AUTH_BACKEND_LINKS=True`) prefer the
      browser's `Accept-Language` header and fall back to
      `AUTH_DEFAULT_LOCALE`, so the same backend serves a Portuguese or
      English page per visitor.
    - New public helpers under `tempest_fastapi_sdk.auth`:
      `normalize_locale`, `negotiate_locale`, `format_expires_at`,
      `SUPPORTED_LOCALES` and `DEFAULT_AUTH_LOCALE`.
    - `EmailUtils.render_template(...)` and `render_auth_page(...)` gained
      an optional `locale=` argument selecting a per-locale template
      subdirectory.
- **Token-expiry timestamps in emails are now short and readable.** The
  activation / reset emails render the expiry as `21/06/2026 23:25 (UTC)`
  (pt-BR) or `2026-06-21 23:25 (UTC)` (en-US) — no seconds, no
  microseconds — instead of the raw
  `2026-06-21 23:25:49.742054+00:00`.

### Changed

- **`make_auth_router` endpoints now carry rich OpenAPI summaries and
  descriptions.** Every signup / activation / login / password-reset /
  MFA route (and the backend HTML pages) documents its request, response,
  status codes, side effects and related settings directly in `/docs`.
- **Bundled auth templates moved to per-locale subdirectories**
  (`auth/templates/<locale>/<name>.html`). Projects overriding templates
  via `template_dir` keep working unchanged (the flat layout is still
  searched); to override a single language, place the file under
  `template_dir/<locale>/`.

### Migration

- **No action required** for projects that keep the defaults — they now
  send Portuguese emails/pages instead of English. Set
  `AUTH_DEFAULT_LOCALE=en-US` to restore English as the default.
- **`EmailUtils.render_template("activation.html")` with no `template_dir`
  and no `locale`** now resolves the bundled template from the default
  locale (`pt-BR`) instead of English. Pass `locale="en-US"` to render
  the English bundled template.

## [0.58.1] — 2026-06-14

### Fixed

- **CLI no longer crashes with `ModuleNotFoundError: No module named
  'click'`.** `tempest_fastapi_sdk.cli.main` imports the public `click`
  package (for `click.echo` / `click.secho` / `click.UsageError` /
  `click.exceptions.Abort` in the full-help group), but `click` was only
  ever present transitively through Typer. Newer Typer releases vendor
  their own Click copy under `typer._click` and no longer pull the public
  `click` package, so installs that resolved such a Typer broke at CLI
  startup (`tempest new`, every `tempest …` command). `click>=8.0.0` is
  now a direct dependency, guaranteeing the public package is always
  importable regardless of how Typer ships Click.

## [0.58.0] — 2026-06-14

### Added

- **Per-entity audit trail** — an append-only log of who changed what,
  with a before/after diff, beyond the timestamp-only `AuditMixin`.
    - **`BaseAuditLogModel`** — abstract `audit_log` table (subclass and
      pick `__tablename__`, like `BaseOutboxModel`): `entity`,
      `entity_id`, `action`, `actor`, `changes` (JSON diff) and optional
      `context`. Ships `new_entry` / `for_create` / `for_update` /
      `for_delete` constructors and the `AuditAction` enum.
    - **`snapshot_model(instance)`** / **`diff_snapshots(before, after)`**
      — capture a model's columns as a JSON-able dict and diff two
      snapshots into `{field: {"before", "after"}}`.
    - **`BaseRepository` opt-in hook** — pass `audit_model=...` and use
      `add_audited(model, *, actor, context)`,
      `update_audited(model, before, *, ...)` (pair with `snapshot()`
      taken before mutating) and `delete_audited(model, *, ...)`. The
      business row and the audit row commit in the **same transaction**,
      so the trail can never reference a rolled-back change.
    - All symbols exported from `tempest_fastapi_sdk.db` and the package
      top level. Fully backward compatible: repositories without
      `audit_model` are unchanged.

## [0.57.0] — 2026-06-14

### Added

- **Feature flags** — toggle features without a redeploy (rollouts,
  kill-switches, beta gating). New `tempest_fastapi_sdk.flags` module,
  all exported at the package top level.
    - **`FeatureFlags(backend, default=False)`** service —
      `is_enabled(name, default=...)`, `enable` / `disable` / `set`,
      and `all()`.
    - **Pluggable backends** — `MemoryFeatureFlagBackend` (dev/tests),
      `EnvFeatureFlagBackend` (static, read-only, `FEATURE_<NAME>`),
      `RedisFeatureFlagBackend` (runtime toggles in a Redis hash,
      shared across replicas) and `CompositeFeatureFlagBackend`
      (layered — a Redis override beats an env default). The
      `FeatureFlagBackend` protocol + `coerce_flag` helper are public.
    - **`make_flag_dependency(flags, name, *, enabled=True,
      status_code=404, ...)`** — a FastAPI dependency that gates a
      route on a flag, raising the SDK envelope (404 by default, so a
      disabled feature looks absent; `enabled=False` inverts it into a
      kill-switch).

## [0.56.0] — 2026-06-14

### Added

- **Tag / namespace invalidation for `@cached`** — cache entries can be
  dropped on mutation instead of only expiring by TTL.
    - **`@cached(..., namespace=..., tags=...)`** — `namespace` is one
      coarse bucket per decorator; `tags` are fine-grained labels, given
      as a static sequence or a per-call builder
      `(args, kwargs) -> Sequence[str]` (e.g. `f"user:{id}"`). On each
      write the entry key is added to a Redis set per label, and those
      registry sets inherit the entry TTL so they self-prune.
    - **`CacheInvalidator(redis, key_prefix=...)`** — drops every entry
      under a label via `invalidate_namespace(ns)`,
      `invalidate_tag(tag)`, `invalidate_tags(*tags)` (deduped) and
      `invalidate_keys(*keys)` (raw keys); each returns the number of
      entries deleted. Bind it with the same `key_prefix` the matching
      decorators use.
    - **`namespace_registry_key`** / **`tag_registry_key`** helpers
      expose the registry set naming.
    - Fully backward compatible: without `namespace` / `tags` no
      registry sets are written and behavior is unchanged. New symbols
      are exported from `tempest_fastapi_sdk.cache`.

## [0.55.0] — 2026-06-14

### Added

- **Localized error envelopes (i18n) for `AppException`** — the error
  `detail` can now be resolved per request locale instead of being
  English-only, without callers hand-translating each `raise`.
    - **`MessageCatalog`** — maps `(locale, key) -> template`, with
      case-insensitive locale matching and primary-subtag fallback
      (a catalog holding `pt-BR` answers `pt`, and vice versa).
      `resolve()` interpolates `message_params` via `str.format`
      (a missing param returns the template instead of raising);
      `negotiate()` picks the best locale from an `Accept-Language`
      header; `merge()` overlays domain codes / new locales onto a
      base catalog without mutating it.
    - **`default_message_catalog()`** — PT-BR (default) + EN-US strings
      for every built-in exception code (`NOT_FOUND`, `CONFLICT`,
      `UNAUTHORIZED`, `FORBIDDEN`, `VALIDATION_ERROR`,
      `TOO_MANY_REQUESTS`, `INVALID_TOKEN`, `TOKEN_EXPIRED`,
      `FILE_TOO_LARGE`, `INVALID_FILE_TYPE`, `INTERNAL_SERVER_ERROR`).
    - **`parse_accept_language()`** + **`DEFAULT_LOCALE`** (`"pt-BR"`).
    - **`AppException` gains `message_key` / `message_params`** — the
      catalog key (defaults to the exception `code`) and template
      values. **`register_exception_handlers(app, catalog=...,
      default_locale=...)`** and `make_app_exception_handler` accept the
      catalog and localize `detail` from the negotiated locale.
    - Fully backward compatible: with no `catalog` the literal
      `message` is used exactly as before; a missing translation falls
      back to the exception's own `detail`. All new symbols are
      exported at the package top level.

## [0.54.0] — 2026-06-14

### Added

- **Per-principal & distributed rate limiting** — `RateLimitMiddleware`
  gains a pluggable store and ready-made key extractors, so limits can
  be per user / tenant / API key and shared across replicas.
    - **Pluggable store** — `RateLimitStore` protocol with
      `MemoryRateLimitStore` (default, in-process) and
      `RedisRateLimitStore` (distributed). The Redis store uses an
      atomic Lua sliding-window log over a sorted set — no race between
      count and add — and `fail_open=True` (default) allows the request
      on a transient Redis error. `RateLimitResult` carries the
      `allowed` / `remaining` / `retry_after` decision.
    - **Key extractors** — `key_by_ip`, `key_by_jwt_subject`
      (per-user via the `sub` claim), `key_by_jwt_claim` (per arbitrary
      claim, e.g. `tenant_id`) and `key_by_header` (e.g. an API key).
      Because the middleware runs before FastAPI dependencies, the
      `key_by_jwt_*` factories decode the bearer from the raw request
      (`decode_or_none`) and fall back to the client IP for anonymous
      traffic.
    - Fully backward compatible: the default behavior (in-process,
      per-IP) is unchanged; `store=` and the `key_by_*` factories are
      opt-in. All new symbols are exported at the package top level.

## [0.53.0] — 2026-06-13

### Added

- **Brazilian states & municipalities dataset** — an offline,
  dependency-free table of every federative unit and its cities, plus
  Pydantic building blocks. No network calls, no external API.
    - **`UF`** — a `StrEnum` with the 27 federative unit acronyms
      (`UF.SP`, `UF.RJ`, …).
    - **`Region`** — the five official IBGE macro-regions
      (`Norte`, `Nordeste`, `Centro-Oeste`, `Sudeste`, `Sul`).
      Every UF is statically mapped to its region.
    - **`StateBR`** / **`CityBR`** — schemas for a state (acronym +
      full name + region + alphabetically sorted municipalities) and a
      single city (name + UF).
    - **Query helpers** — `list_states()`, `get_state(uf)`,
      `cities_by_uf(uf)`, `states_by_region(region)`. The single-UF
      lookups accept any-case acronyms or a `UF` member.
    - **Validators/normalizers** — `is_valid_uf` / `normalize_uf`
      (case- and whitespace-insensitive), `is_valid_city` /
      `normalize_city` (also accent-insensitive, returning the
      canonical proper-case name).
    - **Annotated types** — `UFField` (coerces any-case acronyms to a
      `UF`) and `CityNameField` (trims a city name) ready to drop into
      schema fields.
    - Dataset bundled as `utils/data/br_locations.json` (27 states,
      5606 entries) and loaded lazily on first access. Municipality
      names come from the official IBGE list (current spellings,
      including post-2005 municipalities); the Distrito Federal is
      represented by its 36 administrative regions rather than a single
      Brasília row, for address-form use. All symbols are exported at
      the package top level.

## [0.52.0] — 2026-06-12

### Added

- **Delta-sync primitives on `BaseRepository`** — the backbone of
  offline-first / mobile / PWA backends, so projects stop copy-pasting
  cursor logic per service.
    - **Comparison filter operators.** Filter keys now accept a
      `<column>__<op>` suffix where `<op>` is `gt` / `gte` / `lt` /
      `lte` / `ne` (e.g. `{"updated_at__gt": watermark}` →
      `updated_at > watermark`). Timestamp-precise, unlike the
      whole-day `start_in` / `end_in`. A `None` value skips the
      condition, like every other filter. Works in every method that
      takes `filters` (`list`, `paginate`, `cursor_paginate`, `count`,
      `changes_since`, …).
    - **`BaseRepository.cursor_paginate(..., query=...)`.** New
      optional `query: Select | None` parameter mirroring `paginate`,
      so a hand-built `Select` (joins, `IS NULL` predicates the filter
      dict can't express) can still be cursor-paginated.
    - **`BaseRepository.changes_since(since, *, filters=None,
      cursor=None, limit=50, order_by="updated_at",
      include_deleted=True)`.** Returns rows changed strictly after a
      high-water mark, ascending by `updated_at` and tie-broken by
      `id`, cursor-paginated. Includes soft-deleted tombstones by
      default (so deletions propagate to the client) and returns a
      `server_time` the client persists as the next `since` —
      clock-skew-proof because it is captured server-side before the
      query runs.
- **`SyncFilterSchema` / `SyncPaginationSchema`** — request/response
  DTOs mirroring `changes_since` (the response carries `server_time`).
  Exported at the package top level.

## [0.51.0] — 2026-06-12

### Changed

- **Refreshed the dependency lock to latest compatible.** Notably
  `fastapi` 0.136.1 → 0.136.3, `starlette` 1.0.0 → 1.3.1,
  `sqlalchemy` 2.0.49 → 2.0.50, `typer` 0.26.4 → 0.26.7, `uvicorn`
  0.47 → 0.49, `cryptography` 48 → 49, `redis` 7.4 → 8.0,
  `faststream` 0.6.7 → 0.7.1. Project `>=` floors are unchanged except
  `faststream` (see below).
- **`faststream[rabbit]` floor raised to `>=0.7.1`** (was `>=0.5.30`).
  faststream 0.7 renamed `Broker.close()` to `Broker.stop()`;
  `AsyncBrokerManager.disconnect()` now calls `broker.stop()`, which
  does not exist on faststream < 0.7. Services pinning the `[queue]`
  extra must allow `faststream >= 0.7.1`.

### Fixed

- **redis 8.0 type stubs.** `@cached`'s `deserializer` parameter is now
  typed `Callable[[str | bytes], Any]` (redis returns `bytes` unless the
  client sets `decode_responses=True`; `json.loads` accepts both), and a
  stale `# type: ignore` on `RedisCacheManager.ping()` was removed.

## [0.50.0] — 2026-06-12

### Added

- **Imperative authorization guards — `require_authenticated`,
  `require_active`, `require_admin`.** Projects no longer hand-write
  `if user is None: raise ...` / `if not user.is_admin: raise ...`
  helpers. The new guards (in `tempest_fastapi_sdk.auth.guards`,
  re-exported at the top level) take the `UserT | None` a `soft=True`
  authenticated-user dependency yields, raise the canonical
  `UnauthorizedException` (401) / `ForbiddenException` (403) on failure,
  and **return the user narrowed to non-`None` and to its concrete
  subclass** so the caller drops the `| None` for the rest of the
  function: `user = require_admin(current)`. Generic over
  `BaseUserModel` via a bound `TypeVar`. Also mirrored as static methods
  on `UserAuthService` (`auth_service.require_admin(user)`) so a service
  already in scope guards without an extra import. The auth-flow recipe
  documents the path.

## [0.49.0] — 2026-06-12

### Added

- **`UserAuthService.current_user_dependency()` — built-in
  authenticated-user dependency.** Projects no longer hand-write a
  `load_user` callable plus a second `JWTUtils` to read the
  `current_user` from a bearer token. The service now exposes
  `get_user(subject, session)` (session-explicit), `load_user(subject)`
  (opens its own session from the `db=` handle), and
  `current_user_dependency(*, soft=False)` which wraps
  `make_jwt_user_dependency` with the service's **own** `JWTUtils` and
  `load_user` — so the token is verified with the same secret it was
  signed with, eliminating the divergent-secret footgun. Wiring
  collapses to `get_current_user = auth_service.current_user_dependency()`
  / `auth_service.current_user_dependency(soft=True)`. Requires the
  service to be built with `db=` (already an accepted constructor arg).
  The auth-flow and HTTP recipes now teach this path.

## [0.48.0] — 2026-06-11

### Changed

- **Scaffolded services read their API `title` / `version` /
  `description` from `.env`.** `tempest new` previously hardcoded
  `FastAPI(title="<project>", version="0.1.0")` and the health-router
  version in `src/api/app.py`. The scaffolded `Settings` now carries
  `TITLE`, `DESCRIPTION` and `VERSION` fields (each with `title` /
  `description` / `examples`), `app.py` reads them
  (`FastAPI(title=settings.TITLE, version=settings.VERSION, ...)`,
  `make_health_router(version=settings.VERSION)`,
  `AdminSite(title=f"{settings.TITLE} admin")`), and `.env.example`
  ships a documented `TITLE` / `VERSION` / `DESCRIPTION` block — so the
  OpenAPI docs and admin header are configurable without editing code.

## [0.47.0] — 2026-06-11

### Added

- **`tempest db seed`** — runs a project seed callable
  (default `src.db.seeds:seed`, dotted `module:callable`, sync or async,
  taking one `AsyncSession`) inside a managed session: commit on
  success, rollback on error. The SDK only wires the session lifecycle;
  the callable owns what gets inserted. Prints the row count when the
  callable returns an `int`.
- **`tempest secrets rotate`** — generates fresh URL-safe secrets for
  the keys a service signs/authenticates with (`JWT_SECRET` /
  `TOKEN_SECRET` by default; override with `--keys`) and rewrites the
  matching `.env` lines **in place** (existing keys replaced, missing
  keys appended) after a `.env.bak` backup. `--print` writes nothing and
  emits the values to stdout; `--length` sets the entropy; `--no-backup`
  skips the backup.

### Docs

- CLI recipe (bilingual) gains **`db seed`** and **`secrets rotate`**
  sections; README CLI section and recipes index updated.

## [0.46.0] — 2026-06-11

### Added

- **`AlembicHelper.safe_upgrade(revision="head", *, force=False)`** —
  runs the upgrade only after scanning each pending migration's
  `upgrade()` for data-destroying calls (`op.drop_table` /
  `op.drop_column` / `op.drop_constraint` and `batch_op` variants). When
  any are found it raises **`DestructiveMigrationError`** (carrying the
  offending `(revision, operation)` pairs) and leaves the database
  untouched; `force=True` logs and proceeds. Source-based scanning is
  dialect-agnostic (no false positives on SQLite batch rebuilds) and
  ignores drops in `downgrade()`. `pending_destructive_ops()` exposes the
  scan without running anything (CI-friendly).
- **`GracefulShutdownMiddleware`** — tracks in-flight requests and, once
  draining, replies `503` + `Retry-After` to new requests so a load
  balancer deregisters the instance. `begin_drain()` / `wait_drained()`
  (bounded by `drain_timeout`) are driven from the lifespan shutdown
  (uvicorn owns `SIGTERM`); an opt-in `install_signal_handlers()` chains
  the previous handler for servers that manage signals themselves. Wired
  via `app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)`.

### Docs

- New bilingual recipe **Deploy seguro / Safe deploys** covering
  `safe_upgrade` and `GracefulShutdownMiddleware`, added to the nav, the
  recipes index, and the API reference.

## [0.45.0] — 2026-06-11

### Added

- **`TenantScopedRepository[ModelType]`** — a `BaseRepository` locked
  to a single tenant for shared-schema multi-tenancy. Bind a
  `tenant_id` (and optional `tenant_field`, default `"tenant_id"`) at
  construction; it injects `WHERE tenant_id = ?` into **every** read
  (`get`/`get_or_none`/`get_by_id`/`exists`/`first`/`list`/`count`/
  `paginate`/`cursor_paginate`/`delete_many`) and stamps the tenant id
  onto **every** write (`add`/`add_all`). `delete` / `delete_batch` add
  the tenant predicate to the `DELETE` so a guessed id from another
  tenant matches nothing — cross-tenant access (even probing existence
  by id) is impossible through the repository. The constructor raises
  `AttributeError` at boot if the model lacks the tenant column.
  `tenant_column` property exposes the mapped column for custom queries.

### Docs

- New bilingual recipe **Multi-tenant**, added to the nav (under
  Database), the recipes index, and the API reference.

## [0.44.0] — 2026-06-11

### Added

- **Transactional outbox.** New `BaseOutboxModel` (abstract — the
  project subclasses it and picks `__tablename__`) carrying `topic`,
  `payload` (JSON), `status`, `attempts` / `max_attempts`,
  `available_at`, `sent_at` and `last_error`. `OutboxModel.new_event(
  topic, payload)` builds a pending row.
- **`BaseRepository.save_with_outbox(model, event)`** inserts the
  business row and the outbox event in the **same transaction**, so an
  event can never reference a rolled-back row (and a committed row
  always has its event durably queued) — the fix for the dual-write
  problem.
- **`OutboxRelay`** drains pending rows and publishes them through a
  caller-supplied async `publish` callable (no hard broker dependency
  — works with `AsyncBrokerManager`, a webhook, a test spy). Marks each
  row `sent`; on failure increments `attempts`, records `last_error`,
  reschedules with exponential backoff, and marks `failed` once the
  attempt budget is spent. Locks the batch with `FOR UPDATE SKIP
  LOCKED` on PostgreSQL/MySQL (multi-worker safe), falling back to a
  plain select on SQLite. `drain_once()` for one batch (tests/cron),
  `run(poll_interval=...)` for the loop.
- **`OutboxStatus`** enum (`pending` / `sent` / `failed`).

### Docs

- New bilingual recipe **Outbox transacional / Transactional outbox**,
  added to the nav, the recipes index, and the API reference.

## [0.43.0] — 2026-06-11

### Added

- **Distributed tracing with OpenTelemetry.** `setup_tracing(app,
  service_name=..., otlp_endpoint=...)` installs an OTLP/gRPC span
  exporter and auto-instruments FastAPI (incoming requests),
  SQLAlchemy (queries, via `sqlalchemy_engine=db.engine`) and httpx
  (outbound calls) so one trace follows a request across services.
  `otlp_endpoint=None` falls back to a console exporter for local
  debugging; `sample_ratio` controls head-based sampling;
  `resource_attributes` merges extra span attributes. Behind the new
  `[otel]` extra — importing the SDK without it costs nothing and
  never crashes. Complements `RequestIDMiddleware` (which correlates
  logs).
- **`SlowQueryLogger`** — attaches `before/after_cursor_execute`
  listeners to a SQLAlchemy engine (sync or async) and logs every
  statement slower than `threshold_ms`. Bind parameters are omitted
  by default (PII); `log_parameters=True` and `explain=True` (runs
  `EXPLAIN`) are opt-in for development. No extra required. Exposed
  at the top level and from `tempest_fastapi_sdk.db`.
- **`AsyncDatabaseManager.engine`** — public property returning the
  live `AsyncEngine`, so instrumentation (`SlowQueryLogger`, the OTel
  SQLAlchemy instrumentor) can attach to it directly.

### Docs

- New bilingual recipe **Observabilidade / Observability** covering
  `setup_tracing` and `SlowQueryLogger`, added to the nav, the
  recipes index, and the API reference.

## [0.42.0] — 2026-06-11

### Added

- **`tempest user promote` / `tempest user revoke`** — flip `is_admin`
  for an existing user, found by email (case-insensitive), without
  hand-written SQL. `promote` grants `/admin` access (`is_admin=True`),
  `revoke` removes it. Both exit `1` with `no user found` when no user
  matches the email.
- **`tempest generate --src`** — add the optional source layers
  triggered by the project's pinned SDK extras to an existing project:
  `[queue]` → `<root>/queue/` (FastStream broker + handlers stub),
  `[tasks]` → `<root>/tasks/` (TaskIQ broker + jobs stub). The source
  root (`src` or `app`) is auto-detected and generated imports point at
  it. Idempotent — existing files are kept unless `--force` is passed.
  `--docker` and `--src` can be combined in one invocation.
- **`tempest new` now scaffolds the chosen extras' source layers.**
  `tempest new svc --extras auth,queue` ships `src/queue/` out of the
  box (and `src/tasks/` with `[tasks]`); projects without those extras
  get no placeholder packages.

### Changed

- **Usage errors now print the offending command's full `--help`.**
  An unknown command, an invalid option, or a missing required argument
  renders the complete help (every parameter, default and description)
  before the error line, instead of Click's terse `Try '... --help'`
  hint. Quality-gate exit codes still propagate unchanged.
- **`tempest user create` prompts for the admin flag interactively.**
  When neither `--admin` nor `--no-admin` is passed in an interactive
  terminal, it asks `Should this user be an administrator? [y/N]`.
  Non-interactive runs (CI, pipes, scripts) skip the prompt and default
  to a regular user — pass `--admin` explicitly to create an admin
  without a TTY. The flag is now `--admin/--no-admin` (tri-state).

## [0.41.0] — 2026-06-07

### Changed (breaking)

- **`UploadUtils` and `DownloadUtils` now take the backend at construction**
  — a local folder **or** an `AsyncMinIOClient` — so callers stop passing it
  on every call and the same code works for disk or object storage:
  `UploadUtils("var/uploads")` / `UploadUtils(minio)`,
  `DownloadUtils("var/uploads")` / `DownloadUtils(minio)`.
    - `UploadUtils.save(...)` **dropped the per-call `storage=` argument**;
      it now returns the storage **key** (relative `Path`), not an absolute
      local path. `UploadUtils.delete(key)` is now **async**. The first
      constructor parameter was renamed `upload_dir` → `source`
      (`UploadSettings.upload_kwargs()` updated to match).
    - Migration: `UploadUtils(tmp) + save(file, storage=MinIOUploadStorage(c))`
      → `UploadUtils(c) + save(file)`; `path = save(...)` consumers that read
      the file back should store the key and use `DownloadUtils.download(key)`.

### Added

- **Download objects from MinIO/S3 through the app.**
  `AsyncMinIOClient.download_response(key, ...)` stats + streams an object
  into a ready `StreamingResponse` (Content-Disposition / type / length) —
  no disk, no full-memory load. `DownloadUtils(minio).download(key)` wraps
  it so local and MinIO downloads share one call (`download()`), while
  `file_response`/`resolve` stay local-only.

### Changed

- **Scaffold: infra singletons moved to `src/api/dependencies/resources.py`.**
  `tempest new` now builds the database manager once in `resources.py`
  (`db = AsyncDatabaseManager(**settings.database_kwargs())`) and exposes
  `get_db` / `get_session` providers; `app.py` imports `db` instead of
  constructing it inline, keeping the factory thin. Storage/mail follow the
  same shape (commented, opt-in with `[minio]`/`[email]`). The generated
  admin now enables the logs page (`show_logs=True`). Docs (architecture,
  tutorial, admin recipe) teach the same pattern.

### Fixed

- **Admin "+ New" button was white-on-white** (invisible): the
  `.tempest-admin-list__actions a` rule outweighed `.tempest-admin-list__new`,
  so the accent background was lost while the text stayed white. Scoped the
  button rule to win specificity.
- **Admin desktop sidebar didn't span the full height** when page content
  was short. The layout is now a sticky-footer flex column so the sidebar
  always reaches the footer.

### Added

- **Docs: three previously-missing util recipes** (bilingual) — `Downloads`
  (`DownloadUtils` + `build_content_disposition`), `HTTP client (outbound)`
  (`HTTPClient` + `RetryPolicy` + circuit-breaker, which had zero recipe
  coverage), and `Utilities` (`utcnow`/`to_utc`, `modify_dict`,
  `get_client_ip`, opaque tokens). Added to the nav and the recipes index.

## [0.39.0] — 2026-06-07

### Added

- **Admin: application logs page.** `make_admin_router(show_logs=True,
  log_dir=...)` mounts `GET {prefix}/logs`, reading the structured JSON
  files written by `configure_logging` and rendering them filtered (by
  source + message substring), paginated, with color-coded level badges.
  Opt-in (default `False`) since the payload exposes tracebacks; it adds a
  "Logs" entry to the sidebar and shows an empty state when no files
  exist.
- **Admin: sidebar navigation + mobile burger.** Every authenticated page
  now has a persistent left sidebar (Dashboard, one link per registered
  model, and Logs when enabled), with the current page highlighted. On
  desktop it is always visible; on mobile (≤768px) it becomes an
  off-canvas drawer toggled by a burger button and dismissed via a scrim —
  pure CSS, no JavaScript.
- **`*_kwargs()` helpers on settings mixins** that mirror an SDK
  constructor, so wiring is a one-liner instead of repeating field names:
  `DatabaseSettings.database_kwargs()` → `AsyncDatabaseManager`,
  `RedisSettings.redis_kwargs()` → `AsyncRedisManager`,
  `JWTSettings.jwt_kwargs()` → `JWTUtils`,
  `UploadSettings.upload_kwargs()` → `UploadUtils`,
  `WebPushSettings.webpush_kwargs()` → `WebPushDispatcher`,
  `MinIOSettings.minio_kwargs()` → `AsyncMinIOClient` (joining the
  existing `EmailSettings.email_kwargs()`). Each is splat-tested against
  the real constructor. Settings consumed by helpers that already accept a
  `settings=` object (`run_server`, `apply_cors`) keep that path.

## [0.38.1] — 2026-06-07

### Fixed

- **`EmailUtils` no longer hard-fails against a plain SMTP server.**
  `send()` forced `start_tls=True`, so any server that doesn't advertise
  STARTTLS — including the bundled MailHog dev server on `:1025` — crashed
  with `SMTPException: SMTP STARTTLS extension not supported by server.`
  (and the `/auth/password-reset/request` endpoint returned 500). STARTTLS
  is now **opportunistic** (`start_tls=None`): the connection upgrades only
  when the server advertises STARTTLS, and is left plain otherwise. This
  fixes existing services whose `.env` predates the 0.38.0 `.env.example`
  correction — no `SMTP_USE_TLS=false` needed for MailHog anymore. Setting
  `SMTP_USE_TLS=false` still forces plain with no upgrade attempt; implicit
  TLS (`SMTP_USE_SSL` / port 465) is unchanged.

## [0.38.0] — 2026-06-07

### Fixed

- **Email config from a generated `.env` silently did nothing, then
  crashed against MailHog.** The `[email]` block that `tempest new` /
  `tempest generate --docker` wrote to `.env.example` used `EMAIL_*`
  names (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
  `EMAIL_USE_STARTTLS`), but `EmailSettings` reads `SMTP_*`. The values
  were ignored, leaving `SMTP_USE_TLS` at its `True` default, so STARTTLS
  was forced and aiosmtplib raised `SMTPException: SMTP STARTTLS extension
  not supported by server.` against plain MailHog. The block now emits the
  correct `SMTP_HOST` / `SMTP_PORT` / `SMTP_FROM_ADDR` plus
  `SMTP_USE_TLS=false` + `SMTP_USE_SSL=false` for MailHog.

### Added

- **`EmailSettings.email_kwargs()`** — maps the `SMTP_*` settings onto the
  `EmailUtils` constructor (`SMTP_USE_TLS`→`use_starttls`,
  `SMTP_USE_SSL`→`use_tls`), so the long-documented
  `EmailUtils(**settings.email_kwargs())` recipe actually works. The
  method was referenced in the docstring but never existed.
- **Recipes for transactional email and Web Push** on the docs site
  (`recipes/email.md` + `recipes/webpush.md`, bilingual). Web Push moved
  out of the buried "Real-time" section into its own discoverable page.

### Changed

- **Every `*Settings` docstring now lists its fields.** All 16 settings
  classes in `settings/mixins.py` gained a Google-style `Attributes:`
  section enumerating each field — i.e. the exact environment-variable
  name, its type, purpose, and default — so users no longer have to read
  the source to find which env var to set.

## [0.37.0] — 2026-06-07

### Added

- **`[sqlite]` and `[postgres]` install extras** for the async database
  drivers. The SDK keeps `sqlalchemy[asyncio]` as a core dependency but
  ships **no DBAPI driver by default** — the driver is a deploy choice.
  Install `tempest-fastapi-sdk[sqlite]` (`aiosqlite`, dev default) or
  `[postgres]` (`asyncpg`, production); both are bundled into `[all]`.
  Without one, the engine raised `ModuleNotFoundError` on first
  connection.

### Changed

- **`tempest new` scaffold now ships a working DB driver.** The generated
  `pyproject.toml` pins `aiosqlite` as a **runtime** dependency (it was
  previously dev-only, so the default SQLite URL failed under a no-dev
  install) and carries a commented `asyncpg` line next to it, ready to
  uncomment when switching `DATABASE_URL` to PostgreSQL — matching the
  commented Postgres URL already in `.env.example`.

- **`tempest new` / `tempest generate --docker`: credentials now resolve
  from `.env`, not hardcoded in the compose.** Every `environment:` block
  in the generated `docker-compose.yaml` now uses the `${VAR:-default}`
  form so Docker Compose reads the value from the `.env` next to the
  compose file, keeping secrets out of a VCS-tracked compose. The
  `:-default` preserves zero-config dev boot before `.env.example` is
  copied to `.env`. Affected variables: `POSTGRES_USER` /
  `POSTGRES_PASSWORD` / `POSTGRES_DB`, `RABBITMQ_DEFAULT_USER` /
  `RABBITMQ_DEFAULT_PASS` / `RABBITMQ_DEFAULT_VHOST`, `MINIO_ROOT_USER` /
  `MINIO_ROOT_PASSWORD` (plus the MinIO bootstrap container's `mc alias`).
  `env_block_for` now emits these credential keys into `.env.example` so
  the generated `.env` carries them with their defaults.

## [0.36.0] — 2026-06-06

Admin panel brought to Django-admin parity: the list view, write CRUD,
bulk actions, dashboard, login, and audit trail all landed across the
phases below.

### Added

- **Admin list view — Phase 1 (read-only enhancements + responsive).**
    - Clickable **column sorting** on the list view
      (`?sort=<column>&dir=asc|desc`), validated against the displayed
      real columns; the admin's configured `ordering` remains the
      default.
    - **CSV / JSON export** endpoint
      (`GET /admin/m/{slug}/export.csv` / `.json`) honoring the active
      search / filters / sort. New `make_admin_router(export_max_rows=…)`
      caps export size (default 5000).
    - **Responsive admin UI** — bundled templates + CSS now adapt to
      mobile (≤600px): stacked header, full-width search/filters/actions,
      horizontal-scroll table wrappers, single-column detail grid.
      Verified at 390px (mobile) and 1280px (desktop).

- **Admin write CRUD — Phase 2a (create / edit / delete).**
    - `GET/POST /admin/m/{slug}/new` (create), `GET/POST
      /admin/m/{slug}/{identity}/edit` (edit), and `POST
      /admin/m/{slug}/{identity}/delete` (delete), each gated by new
      `AdminModel(can_create=…, can_edit=…, can_delete=…)` flags
      (default `True`; a disabled view returns `404`).
    - **CSRF-protected** mutations — every write form carries the
      session CSRF token, verified server-side (`403` on mismatch).
    - **Type-aware field widgets** — text / textarea (long strings) /
      number / checkbox / `datetime-local` / date / enum `select`,
      derived from the column types, with required-field + per-field
      validation errors re-rendered on the form, and integrity errors
      surfaced inline.
    - Detail view gains Edit / Delete controls; list view gains a
      "+ New" button. All responsive (verified at 390px / 1280px).

- **Admin bulk actions — Phase 2b.**
    - List view gains row checkboxes + a select-all toggle and a bulk
      action bar. `POST /admin/m/{slug}/bulk` applies **delete**
      (`can_delete`), **activate** / **deactivate** (`can_edit`, toggling
      the `is_active` flag) to the selected rows, CSRF-verified.
      Backed by `BaseRepository.delete_batch` / `bulk_update`.
      Responsive (verified at 390px / 1280px).

- **Admin foreign-key select — Phase 2c.**
    - A foreign-key column whose referenced table has its own
      `AdminModel` now renders as a **dropdown of the related rows**
      (Django's FK select) on the create/edit form, instead of a raw
      UUID input. Option labels come from the referenced admin's first
      `search_fields` entry (falling back to a `name`/`title`/`email`
      attribute, then the id). Capped at 1000 rows. FKs to unmanaged
      tables stay plain UUID inputs.

- **Admin dashboard — counts + metrics (Phase 3a).**
    - The dashboard now renders each registered model as a card with its
      **live row count** and Browse / + New links, plus a **system
      metrics panel** (CPU / RAM / disk) via `MetricsUtils`. The panel
      is on by default, silently omitted when the `[metrics]` extra is
      absent, and disabled with `make_admin_router(show_metrics=False)`.
      Responsive card grids (verified at 390px / 1280px).

- **Admin MFA login — Phase 3b.**
    - The admin login now supports a TOTP second factor. After the
      password step, a principal with MFA enabled gets a short
      `mfa_pending` session and is redirected to `GET/POST /admin/mfa`
      (a CSRF-protected TOTP challenge); only a valid code upgrades the
      session to full access. `AdminAuthBackend` gains `mfa_enabled` /
      `verify_mfa` (default off); `UserModelAuthBackend` implements them
      against `MFAMixin`'s `totp_secret` / `totp_enabled_at` via
      `TOTPHelper` (new `mfa_issuer` / `mfa_window` ctor args). Pending
      sessions are denied every admin page until the challenge passes.

- **Admin audit trail — Phase 3c.**
    - Create/edit through the admin now **stamps** `created_by` /
      `updated_by` (from `AuditMixin`) with the acting admin's id. The
      detail view gained an **Audit panel** showing created/updated
      timestamps and — when the model has the audit columns — the actor,
      with the stored UUID resolved to a display name via the auth
      backend. Models without `AuditMixin` show the timestamps only.

    File-upload widget and inline/related editing remain tracked as later
    admin phases on the roadmap.

## [0.35.0] — 2026-06-06

### Added

- **MFA / TOTP (RFC 6238)** — the bundled auth flow now supports
  two-factor authentication with Authenticator apps. New `[mfa]`
  extra (`pyotp>=2.9.0`). Public surface:
    - `TOTPHelper(issuer=...)` — stateless TOTP issuer/verifier
      (`generate_secret`, `provisioning_uri`, `verify` with a
      configurable clock-drift window). Lazy-imports `pyotp`, so
      `import tempest_fastapi_sdk` still works without the extra.
    - `BaseUserRecoveryCodeModel` + `make_user_recovery_code_model`
      — single-use recovery-code table (stores only the SHA-256
      hash of each code, mirroring `BaseUserTokenModel`).
    - `MFAMixin` — opt-in SQLAlchemy mixin adding `totp_secret` +
      `totp_enabled_at` columns (plus an `is_mfa_active` property).
      Mix it into the concrete user model
      (`class UserModel(MFAMixin, BaseUserModel)`) only when MFA is
      adopted, so projects that never enable it carry no dead
      columns. `totp_enabled_at IS NULL` means MFA is
      staged-but-not-active, so login stays single-step until the
      user confirms.
    - `UserAuthService` MFA methods: `is_mfa_enrolled`,
      `issue_mfa_token`, `mfa_enroll`, `mfa_confirm`, `mfa_verify`,
      `mfa_disable`.
    - `make_auth_router(..., recovery_code_model=...)` mounts four
      endpoints behind the `AUTH_MFA_ENABLED` kill-switch:
      `POST /auth/mfa/enroll`, `/auth/mfa/confirm`,
      `/auth/mfa/disable`, `/auth/mfa/verify`. `POST /auth/login`
      now returns `mfa_required=True` + a short-lived `mfa_token`
      (instead of the JWT pair) for enrolled users; step 2 swaps
      `mfa_token` + code for the real tokens.
    - New schemas: `MFAEnrollResponseSchema`, `MFAConfirmSchema`,
      `MFADisableSchema`, `MFAVerifySchema`. `LoginResponseSchema`
      gains `mfa_required` + `mfa_token` (and `access_token` /
      `refresh_token` are now nullable for the MFA-pending case).
    - New `AuthSettings`: `AUTH_MFA_ENABLED`, `AUTH_MFA_ISSUER`,
      `AUTH_MFA_RECOVERY_CODES_COUNT`, `AUTH_MFA_TOKEN_TTL_SECONDS`,
      `AUTH_MFA_VERIFY_WINDOW`.
- **Optional password complexity** — new
  `AUTH_PASSWORD_REQUIRE_COMPLEXITY` flag (default `False`). When off,
  any password meeting `AUTH_PASSWORD_MIN_LENGTH` is accepted; when on,
  signup + reset additionally require at least one lowercase letter,
  one uppercase letter, one digit, and one special (non-alphanumeric)
  character, and the effective length floor is raised to at least 8
  (a configured `AUTH_PASSWORD_MIN_LENGTH` below 8 is ignored while
  complexity is on). Enforced server-side on both `service.signup` and
  `service.confirm_password_reset`.

### Fixed

- **Password minimum length is now honored end-to-end.**
  `SignupSchema.password` and `PasswordResetConfirmSchema.new_password`
  hardcoded `min_length=12`, which overrode `AUTH_PASSWORD_MIN_LENGTH`
  on the router path — a project lowering the floor still got a 422
  from Pydantic, and raising it above 12 was enforced only by the
  service. The schemas now reject only empty strings;
  `AUTH_PASSWORD_MIN_LENGTH` is the single source of truth (now `ge=1`,
  fully configurable down to 4 or any value, default 12), applied
  server-side.

### Notes

- Enabling MFA requires a migration: mix `MFAMixin` into the concrete
  user model (`class UserModel(MFAMixin, BaseUserModel)`) to add the
  `totp_secret` / `totp_enabled_at` columns, and create the
  recovery-code table. `AUTH_MFA_ENABLED=True` without passing
  `recovery_code_model` to `make_auth_router` raises at
  router-build time.

## [0.34.0] — 2026-06-04

### Added

- **Server-side session module** — new `tempest_fastapi_sdk.sessions`
  package ships a full alternative to the JWT auth flow. Public
  surface:
    - `SessionStore` Protocol + `MemorySessionStore` (dev/tests) +
      `RedisSessionStore` (production). Stores keep sessions by the
      SHA-256 hash of the cookie id; the plaintext only lives in the
      Set-Cookie. Redis store gets TTL eviction for free; both
      stores expose `get` / `set` / `delete` / `delete_by_user` /
      `list_by_user`.
    - `Session` schema — `session_id` (hashed), `user_id`,
      `created_at`, `expires_at`, `last_seen_at`, `ip`,
      `user_agent`, `data` (free-form JSON bag).
    - `SessionAuth` service — authenticates credentials via
      `PasswordUtils` + `UserModel`, mints sessions, slides TTL on
      resolve, rotates on login (anti-fixation), revokes one or all
      sessions per user.
    - `SessionMiddleware` — reads the cookie, populates
      `request.state.session` so handlers never re-resolve.
    - `make_session_dependency(required=...)` — FastAPI dependency
      returning the resolved session or raising
      `UnauthorizedException` when required.
    - `make_session_router(service, session_factory=..., prefix=...)` —
      bundled 5-endpoint router: `POST /auth/session/login`,
      `POST /auth/session/logout`, `GET /auth/session/me`,
      `GET /auth/session/list`, `DELETE /auth/session/{id}`.
    - `SessionLoginSchema`, `SessionResponseSchema`,
      `SessionSummarySchema` — typed DTOs for the router.
- **`SessionSettings` mixin** — `SESSION_TTL_SECONDS`,
  `SESSION_SLIDING`, `SESSION_COOKIE_{NAME,DOMAIN,PATH,SECURE,HTTPONLY,SAMESITE}`,
  `SESSION_ROTATE_ON_LOGIN`. Every field carries
  `title`/`description`/`examples`.

### Documentation

- `docs/recipes/sessions.{md,en.md}` — new bilingual recipe with
  the JWT-vs-session decision table, setup wiring, endpoints,
  store comparison (Memory vs Redis vs custom), middleware
  semantics, security model (hash-at-rest, anti-fixation,
  SameSite, anti-enumeration, instant revocation) and when NOT to
  use sessions.
- `docs/reference.md` — new `tempest_fastapi_sdk.sessions` section
  with `mkdocstrings` entries for `SessionAuth`,
  `make_session_router`, `SessionMiddleware`,
  `make_session_dependency`, `SessionStore` /
  `MemorySessionStore` / `RedisSessionStore`, every schema.
- `mkdocs.yml` — recipe added to the navigation in both languages
  with the matching i18n translation entry.

### Tests

- 16 new cases under `tests/sessions/` covering the
  `MemorySessionStore` lifecycle (set/get/expire/delete/list +
  user-scoped wipe), `SessionAuth` (authenticate / login /
  resolve+slide / rotate / revoke_all + anti-enumeration), and
  router integration (login sets cookie, `me` returns session,
  `me` returns 401 without cookie, logout clears cookie, list
  marks current).

### Migration

- v0.34.0 is purely additive. No public-API breaking change.
  Existing JWT flows keep working untouched; sessions are a
  separate opt-in path.

## [0.33.0] — 2026-06-04

### Added

- **WebSocket router** (``tempest_fastapi_sdk.make_websocket_router`` +
  ``WebSocketHub``). New ``tempest_fastapi_sdk.websockets`` module
  ships the three concerns every WebSocket endpoint has to get right:
    - **Bearer auth at the handshake** via ``?token=<jwt>`` query
      string OR ``Sec-WebSocket-Protocol: bearer,<jwt>`` subprotocol
      (preferred — does not leak to proxy logs). The
      ``bearer_resolver`` callable maps the token to a user UUID;
      ``None`` closes the socket with code ``4401`` before the
      handler runs.
    - **Heartbeat ping/pong** with timeout. The router emits
      ``{"type": "ping"}`` every ``WS_HEARTBEAT_SECONDS`` (default
      ``30``) and closes with code ``4408`` when the matching
      ``{"type": "pong"}`` does not arrive within
      ``WS_HEARTBEAT_TIMEOUT_SECONDS`` (default ``60``).
    - **In-process registry** (``WebSocketHub``) tracking every
      live connection by user UUID + topic subscriptions. Exposes
      ``send_to(user_id, envelope)``, ``broadcast(envelope,
      topic=None)``, ``subscribe`` / ``unsubscribe``, ``online_users()``
      and ``connection_count()`` — usable from any HTTP handler in
      the same FastAPI app. Per-user cap ``WS_MAX_CONNECTIONS_PER_USER``
      (default ``5``) evicts the oldest connection with code ``4429``
      when exceeded. Dead peers are evicted transparently on
      ``send_json`` failure.
- **``WSEnvelope`` schema** — canonical ``{type, data, request_id}``
  envelope for SDK-managed frames (``ping``/``pong``) and the
  recommended shape for application messages.
- **``WebSocketConnection`` dataclass** — public handle returned by
  ``WebSocketHub.register`` so handlers can pass a stable
  ``connection_id`` to ``subscribe`` / ``unsubscribe``.
- **``WebSocketSettings`` mixin** — ``WS_HEARTBEAT_SECONDS``,
  ``WS_HEARTBEAT_TIMEOUT_SECONDS``, ``WS_MAX_CONNECTIONS_PER_USER``,
  ``WS_MAX_MESSAGE_BYTES`` with full ``title``/``description``/
  ``examples`` metadata.

### Documentation

- ``docs/recipes/websocket.{md,en.md}`` — new bilingual recipe
  covering setup, query-vs-subprotocol auth comparison, JavaScript
  client snippet with heartbeat + reconnect, broadcast / send_to /
  topic patterns, every close-code table, settings reference, and
  the single-process vs multi-replica trade-offs.
- ``docs/reference.{md,en.md}`` — new section
  ``tempest_fastapi_sdk.websockets`` with ``mkdocstrings`` entries
  for ``WebSocketHub``, ``WebSocketConnection``,
  ``make_websocket_router``, ``WSEnvelope``.
- ``mkdocs.yml`` — recipe added to the navigation in both languages
  with the matching i18n translation entry.

### Migration

- v0.33.0 is purely additive. No public-API breaking change. The
  new module imports lazily; existing services that don't mount
  the router pay no startup cost.

## [0.32.1] — 2026-06-04

### Changed

- **Top-level `__all__` now re-exports the bundled auth surface.** Adds
  ``UserAuthService``, ``make_auth_router``, ``BaseUserTokenModel``,
  ``UserTokenPurpose``, ``make_user_token_model``, ``AuthSettings``,
  every auth schema (``SignupSchema``/``SignupResponseSchema``/
  ``LoginSchema``/``LoginResponseSchema``/``ActivationToken``/
  ``ActivationResponseSchema``/``PasswordResetToken``/
  ``PasswordResetRequestSchema``/``PasswordResetResponseSchema``/
  ``PasswordResetConfirmSchema``) to the public re-export list. Runtime
  imports already worked; this satisfies strict re-export checkers
  (pyright/basedpyright/Pylance strict) without project-level
  ``pyrightconfig.json``.

### Documentation

- **Full audit + fix pass against the actual SDK code.** Every recipe,
  tutorial section, README block and learning-project example was
  cross-checked against the source; whatever didn't match was rewritten.
  Highlights of what changed:
    - ``docs/tutorial.{md,en.md}`` / ``README.md``: router section now
      calls ``controller.signup`` / ``controller.get_by_id`` / a real
      ``controller.paginate(...)`` invocation — the previous
      ``controller.create`` / ``controller.get`` / ``controller.list_paginated``
      names did not exist on ``BaseController`` and would have raised
      ``AttributeError`` on every endpoint.
    - ``page_size`` consistently replaces the bogus ``size`` query/JSON
      key throughout pagination snippets — the real
      ``BasePaginationFilterSchema`` field is ``page_size`` and the
      ``paginate(...)`` dict returns ``"page_size"``, not ``"size"``.
    - ``BaseUserModel`` examples no longer claim a ``password_hash``
      column — the real column is ``hashed_password`` and the docs now
      construct rows with it.
    - ``docs/recipes/testing.{md,en.md}`` rewritten: ``async with
      TestClient(app)`` (which doesn't work — ``TestClient`` is sync)
      replaced by ``httpx.AsyncClient(transport=ASGITransport(app=app))``;
      ``test_database`` / ``test_session`` / ``create_test_engine``
      signatures and return shapes corrected to match the helpers
      actually shipped under ``tempest_fastapi_sdk.testing``.
    - ``docs/recipes/security.{md,en.md}`` fully rewritten: every claim
      pointed at fictional API (``RedisThrottleBackend``,
      ``MemoryThrottleBackend``, ``ThrottleStatus.LOCKED``,
      ``throttle.check()``, ``throttle.record_failure()``,
      ``.attempts_left``, ``set_cookie(key=..., same_site=SameSite.LAX)``,
      ``get_client_ip(..., trusted_proxies={...})``,
      ``accept_private=False``, an HMAC-pepper claim on
      ``hash_opaque_token(..., secret=...)``, a ``Referrer-Policy`` in
      ``DEFAULT_STATIC_SECURITY_HEADERS``). New recipe documents the
      real ``AttemptThrottle`` / ``ThrottleStatus`` / ``set_cookie`` /
      ``clear_cookie`` / ``get_client_ip`` surface.
    - ``docs/recipes/http.{md,en.md}``: ``JWT_TTL_HOURS`` (does not
      exist) replaced by ``JWT_ACCESS_TTL_SECONDS``;
      ``RSAWebhookSignatureVerifier(encoding="base64",
      hash_algorithm="sha256")`` (also fictional) rewritten to the real
      ``algorithm="sha256"`` kwarg; ``request.client.host or "anon"``
      rewritten to handle ``request.client is None`` safely;
      ``controller.list_paginated`` replaced with a real
      ``controller.paginate(...)`` call.
    - ``docs/recipes/auth-flow.{md,en.md}``: ``SignupResponseSchema``
      example body now matches the real shape
      (``user_id``/``activation_required``/``activation_url``/
      ``access_token``/``refresh_token`` — no fictional ``email`` /
      ``is_active`` fields); ``tempest db init`` prereq is called out
      before ``tempest db revision``; UUID example replaces the bogus
      ULID-style placeholder.
    - ``docs/recipes/uploads.{md,en.md}``: phantom
      ``settings.UPLOAD_BACKEND`` field (it isn't on ``UploadSettings``)
      now has to be declared on the project's own ``Settings`` subclass
      in a copy-pasteable snippet; ``UploadFile.filename`` (typed
      ``str | None``) is now fallen back to ``"upload.bin"`` before
      being passed where ``str`` is required; the ``UploadUtils.__init__``
      mkdir side-effect is explicitly called out.
    - ``docs/recipes/metrics.{md,en.md}`` no longer stops at
      ``MetricsUtils`` — a full Prometheus exposition section was added
      covering ``PrometheusMiddleware`` + ``make_prometheus_registry`` +
      ``make_prometheus_router``, the ``[prometheus]`` extra, scrape
      config, and the rationale for not mounting the JSON snapshot on
      ``/metrics``.
    - ``docs/recipes/queue-tasks.{md,en.md}``: ``NameError`` in the
      outbox dispatcher fixed (``broker``/``queue_broker`` shadowing
      that would crash on import).
    - ``docs/recipes/realtime.{md,en.md}``: missing
      ``StreamingResponse`` import added; producer pattern rewritten to
      cancel on client disconnect (the previous fire-and-forget pattern
      leaked tasks).
    - ``docs/recipes/admin.{md,en.md}``: ``settings.ADMIN_SECRET_KEY``
      (doesn't exist) replaced with the scaffold's ``settings.JWT_SECRET``;
      ``__tablename__ = "user"`` replaced with the scaffold's actual
      ``"users"``.
    - ``docs/recipes/database.{md,en.md}``: ``filters={"deleted_at":
      None}`` (silently skipped — returns deleted rows) replaced with a
      raw ``select(...).where(col.is_(None))`` query; the tuple
      comparison ``(col_a, col_b) > (val_a, val_b)`` (invalid in
      SQLAlchemy) replaced with ``tuple_(col_a, col_b) > tuple_(...)``;
      the cursor example now decodes ``state["value"]`` back to
      ``datetime`` so Postgres tuple comparison doesn't fail on a
      str-vs-timestamp clash; missing ``select`` / ``AsyncSession`` /
      ``Any`` imports added.
    - ``docs/recipes/cli.{md,en.md}``: default
      ``--extras`` value corrected from ``auth`` to the real
      ``auth,admin``; ``--model myapp.models.user:User`` example
      renamed to ``UserModel`` with a comment explaining the
      ``BaseUserModel`` subclass requirement.
    - ``docs/recipes/cache.{md,en.md}``: the previously
      free-floating ``await cache.connect()`` is now shown inside a real
      ``@asynccontextmanager`` lifespan — without it ``cache.client``
      raises ``RuntimeError`` on first use.
    - ``docs/recipes/logging.{md,en.md}``: malformed
      ``"2026-05-16T20:14:33.412+00:00Z"`` timestamp (impossible — the
      formatter strips ``+00:00`` and appends ``Z``) corrected to
      ``"2026-05-16T20:14:33.412Z"``.
    - ``docs/recipes/br-helpers.{md,en.md}``: ``request.json()``
      (a coroutine in FastAPI/Starlette) now ``await``-ed inside an
      ``async def`` handler with the ``Request`` import included.
    - ``docs/recipes/storage.{md,en.md}``: stale "v0.24.0 will introduce
      `S3Backend`" promise replaced with a pointer to the uploads recipe
      where ``MinIOUploadStorage`` already lives.
    - ``docs/architecture.{md,en.md}``: ``paginate(...)`` row in the
      BaseService table now lists ``page_size`` instead of ``size``;
      a note next to ``UserController(UserService(UserRepository(session)))``
      explains the required ``BaseRepository`` subclass with
      ``model=UserModel``.
    - ``docs/installation.{md,en.md}``: version pins bumped from
      ``>=0.19.0`` to ``>=0.32.0``; ``tempest user create`` example now
      passes the required ``--email`` flag instead of relying on a
      non-existent prompt.
    - ``README.md``: SDK version pin in the pyproject snippet bumped
      from ``>=0.13.1`` to ``>=0.32.0``; all the same router /
      pagination / model-field fixes applied.

### Migration

- Zero breaking changes — this is a pure documentation + re-export
  audit on top of the v0.32.0 surface.

## [0.32.0] — 2026-06-04

### Added

- **Backend-only auth mode** (``AuthSettings.AUTH_BACKEND_LINKS=True``).
  When enabled, ``make_auth_router`` mounts three extra HTML endpoints
  on top of the JSON ones already exposed — a project can run the
  full signup → activate → reset cycle without any frontend route
  handling tokens. New endpoints:
    - ``GET /auth/activate/{token}`` — consumes the activation token
      and renders an HTML success page (or an error page on
      bad / expired / used tokens).
    - ``GET /auth/password-reset/{token}`` — peeks the token (does
      NOT consume it) and renders an HTML form with the new-password
      input + confirmation.
    - ``POST /auth/password-reset/{token}`` (``application/x-www-form-urlencoded``)
      — processes the form, validates the password floor, confirms
      the reset, and renders success or error HTML.
- **Five new bundled Jinja2 templates** under
  ``tempest_fastapi_sdk/auth/templates``:
  ``activation_success.html``, ``activation_error.html``,
  ``password_reset_form.html``, ``password_reset_success.html``,
  ``password_reset_error.html``. All responsive, inline-styled,
  mobile-friendly. Shadow them by dropping same-named files into the
  ``template_dir`` you pass to ``make_auth_router``.
- **``make_auth_router(template_dir=...)``** parameter — point the
  router at a project-owned directory whose templates override the
  bundled defaults. Only consulted when ``AUTH_BACKEND_LINKS=True``.
- **``UserAuthService.peek_token(session, token, purpose)``** — new
  service method that validates a token and returns the
  ``(token_record, user)`` pair **without** marking ``used_at``. Used
  by ``GET /auth/password-reset/{token}`` to render the form before
  the user actually submits.
- **``AuthSettings`` gains six fields** documenting the new flow:
    - ``AUTH_BACKEND_LINKS: bool`` (default ``False``)
    - ``AUTH_LOGIN_URL: str | None`` (default ``None``) — URL for the
      "Go to login" button rendered on success/error pages
    - ``AUTH_ACTIVATION_SUCCESS_TEMPLATE`` /
      ``AUTH_ACTIVATION_ERROR_TEMPLATE``
    - ``AUTH_PASSWORD_RESET_FORM_TEMPLATE`` /
      ``AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE`` /
      ``AUTH_PASSWORD_RESET_ERROR_TEMPLATE``
- **``tempest_fastapi_sdk.auth.page_renderer.render_auth_page``** —
  standalone Jinja2 renderer reused by the router; doesn't require
  ``EmailUtils`` to be wired (only the ``[email]`` extra for Jinja2
  itself).

### Changed

- ``make_auth_router`` signature now accepts the optional
  ``template_dir: str | None = None`` keyword. Existing call sites
  remain source-compatible.
- All JSON endpoints (``POST /auth/signup``,
  ``POST /auth/activate/{token}``, ``POST /auth/login``,
  ``POST /auth/password-reset/request``,
  ``POST /auth/password-reset/confirm``) stay mounted exactly as in
  v0.31.x — Backend-only Mode E is purely additive.

### Documentation

- ``docs/recipes/auth-flow.{md,en.md}`` gains the new **Mode E
  (backend-only)** section: ``.env`` block, Mermaid sequence
  diagram of the activation flow, bundled-template reference table,
  override walkthrough, trade-offs callout (zero frontend dep, no
  JWT auto-delivery, requires ``[email]`` extra). The "Four operating
  modes" section was renamed to "Five operating modes" and the TOC
  entry updated.

### Migration

- v0.32.0 has **no breaking changes**. To opt into backend-only
  mode, flip ``AUTH_BACKEND_LINKS=True`` in the ``.env`` and update
  ``AUTH_ACTIVATION_URL_TEMPLATE`` / ``AUTH_PASSWORD_RESET_URL_TEMPLATE``
  to point at your backend instead of your frontend. Everything else
  is wired automatically.

## [0.31.4] — 2026-06-04

### Changed

- **Explicit re-exports across `settings/`, `auth/`, and `db/` `__init__.py`.**
  Every symbol is now re-exported using the PEP 484
  ``from x import Y as Y`` form **in addition to** ``__all__``.
  Reason: third-party consumers run a mixed bag of type-checkers
  (mypy, pyright, pylance, basedpyright) at different strictness
  levels and without project-aware ``pyrightconfig.json``. Either
  form alone is theoretically PEP 484 compliant, but basedpyright
  and Pylance strict still flag bare ``from foo import Bar`` inside
  ``__init__.py`` as "private import usage" unless the symbol is
  aliased with ``as Bar``. Pairing the two patterns silences every
  IDE with no project-level config required. No behavior change —
  same runtime imports, same public surface, same wheel contents.

### Documentation

- **Auth-flow recipe rewritten end-to-end** (`docs/recipes/auth-flow.{md,en.md}`):
    - New table of contents at the top.
    - New "Email anatomy" section disambiguating the three concepts
      that confused readers (opaque token vs URL template vs Jinja2
      template) with a Mermaid sequence diagram of the full flow.
    - "Operating modes" expanded from three to **four** explicit
      modes (A. production / B. dev with local SMTP / C. dev without
      SMTP / D. CI), each with a copy-paste `.env` block.
    - New **"Mailhog vs smtp4dev"** comparison table + ready-to-use
      `docker-compose.yaml` snippets for both containers — the
      recipe now covers local SMTP interception out of the box.
    - "Customizing templates" rewritten with clearer prose, the full
      context-variable table, and a copy-paste minimal
      `emails/activation.html` example.
- ``CLAUDE.md`` gains a new "Explicit re-exports in every
  ``__init__.py``" rule documenting the dual ``as Y`` + ``__all__``
  pattern and flagging bare re-exports as a structural defect.

## [0.31.3] — 2026-06-04

### Documentation

- **Comprehensive documentation refresh** to reflect the v0.23.0 → v0.31.2
  surface. No code change.
- ``README.md``: extras table now lists ``[http]`` + ``[prometheus]``;
  module map covers ``BaseUserTokenModel``, ``UserTokenPurpose``,
  ``BASE_COLUMN_ORDER``, ``reorder_base_columns_first``, ``compose_hooks``,
  ``AuthSettings``, ``tempest_fastapi_sdk.auth``, ``utils.http_client``,
  ``utils.storage_backends``. Roadmap section rewritten with every shipped
  release v0.23.0 → v0.31.2.
- ``docs/index.{md,en.md}``: module map updated with the new exports
  (``auth``, ``storage``, ``IdempotencyMiddleware``,
  ``BodySizeLimitMiddleware``, ``CSRFMiddleware``, ``PrometheusMiddleware``,
  OAuth clients, ``HTTPClient``, bulk repo ops). Hero paragraph rewritten
  to mention the new layers.
- ``docs/installation.{md,en.md}``: extras table now lists ``[minio]``,
  ``[http]``, ``[prometheus]``; CLI section documents ``tempest generate``,
  ``tempest db <subcommand>``, ``tempest user <subcommand>``.
- ``docs/tutorial.{md,en.md}``: new "Auth flow already ships" admonition
  pointing readers at the bundled ``UserAuthService`` + ``make_auth_router``
  shortcut.
- ``docs/recipes/auth-flow.{md,en.md}`` (new): full PT-BR + EN-US recipe
  covering the bundled signup / activate / login / password-reset flow —
  ``UserTokenModel`` concretization, settings flags, the three operating
  modes (production, dev without SMTP, CI tests), template overrides,
  security guarantees.
- ``docs/roadmap.{md,en.md}``: full rewrite with Tier S / A / B status
  tables (Status + Where columns), every release detailed v0.23.0 →
  v0.31.2, "What's next" section for v0.32.0+ (OpenTelemetry) and
  v0.33.0+ (outbox).
- ``docs/reference.{md,en.md}``: ``mkdocstrings`` entries added for
  ``BodySizeLimitMiddleware``, ``CSRFMiddleware``, OAuth clients,
  ``PrometheusMiddleware`` + ``make_prometheus_router``, ``HTTPClient``
  + ``RetryPolicy`` + ``CircuitOpenError``, the full
  ``tempest_fastapi_sdk.auth`` module (service, router, schemas),
  ``reorder_base_columns_first``, ``compose_hooks``.
- ``docs/learning/marketplace/index.{md,en.md}``: stack table now points
  at ``UserAuthService`` + ``make_auth_router`` as the default auth path,
  ``BaseRepository.bulk_create_values`` / ``bulk_upsert`` for stock seed,
  ``PrometheusMiddleware``, ``BodySizeLimitMiddleware``, OAuth clients,
  ``CSRFMiddleware``, ``HTTPClient``. Implementation order's step 1
  rewritten to use the bundled auth recipe.
- ``mkdocs.yml``: ``Auth flow (signup/reset)`` entry added to nav + i18n
  translation map.
- ``CLAUDE.md``: "What the SDK currently covers" section rewritten as a
  structured category bullet list (Auth, DB, Observability, HTTP layer,
  Pagination, Settings, SSE, Throttle, Upload, MinIO/S3, Email, WebPush,
  Cache, Queue/tasks, BR validators, Admin panel, CLI).

## [0.31.2] — 2026-06-04

### Changed

- **``UserAuthService`` method signatures now type ``session``
  as ``sqlalchemy.ext.asyncio.AsyncSession``** instead of
  ``Any``. All seven public methods (``signup``, ``activate``,
  ``login``, ``request_password_reset``,
  ``confirm_password_reset``, ``_issue_token``, ``_consume_token``)
  declare the real type so mypy + IDE autocomplete can flag
  wrong shapes at the call site. No behavior change — only the
  annotations tightened. Aligned with the v0.25.1
  "avoid ``Any`` in SDK code" rule that the auth module had
  drifted away from when it landed in v0.31.0.

## [0.31.1] — 2026-06-04

### Changed

- **``ActivationToken`` and ``PasswordResetToken`` are now
  Pydantic ``BaseSchema`` subclasses** instead of ``dataclass``
  instances. Keeps the auth module aligned with the SDK's
  gold-standard DTO convention — every "thing returned to the
  caller" is a Pydantic schema with full
  ``title``/``description``/``examples`` metadata. The fields
  are the same; the constructor signature is the same. Callers
  that destructure via attribute access (``activation.token``,
  ``activation.url``) keep working unchanged.
- Both token schemas moved from ``tempest_fastapi_sdk.auth.service``
  to ``tempest_fastapi_sdk.auth.schemas`` — re-exports at the
  package root are unchanged, so existing imports
  (``from tempest_fastapi_sdk import ActivationToken``) keep
  resolving.
- **Every auth schema now carries a thorough class-level
  docstring** describing the flow that uses it, the meaning of
  each attribute, and the security / behavior contract (e.g.
  why ``PasswordResetResponseSchema.message`` is always
  identical, why ``ActivationToken.token`` is only handed back
  once).

## [0.31.0] — 2026-06-04

### Added

- **Bundled auth flow** — new ``tempest_fastapi_sdk.auth`` module
  ships service + router + schemas + templates so signup,
  activation, login, and password reset land end-to-end with a
  single ``include_router`` call:

  - ``UserAuthService`` — generic over the concrete ``UserModel``
    + ``UserTokenModel``. Methods: ``signup``, ``activate``,
    ``login``, ``request_password_reset``,
    ``confirm_password_reset``, ``issue_jwt_pair``. Every method
    accepts the active ``AsyncSession`` so the caller controls
    transaction boundaries.
  - ``make_auth_router(service, session_factory=…)`` mounts
    ``POST /auth/signup``, ``POST /auth/activate/{token}``,
    ``POST /auth/login``,
    ``POST /auth/password-reset/request``,
    ``POST /auth/password-reset/confirm``.
  - DTOs: ``SignupSchema`` / ``SignupResponseSchema``,
    ``LoginSchema`` / ``LoginResponseSchema``,
    ``ActivationResponseSchema``,
    ``PasswordResetRequestSchema`` /
    ``PasswordResetResponseSchema``,
    ``PasswordResetConfirmSchema``. Every field carries
    ``title`` / ``description`` / ``examples`` per the SDK
    convention.

- **``BaseUserTokenModel``** (abstract) for one-shot activation /
  reset tokens, plus ``make_user_token_model(user_table, …)`` for
  test fixtures. The plaintext token is returned exactly once;
  only the SHA-256 hash is persisted (via existing
  ``generate_opaque_token`` / ``hash_opaque_token`` helpers).
  Tokens carry a ``purpose`` (``UserTokenPurpose`` StrEnum:
  ``activation`` / ``password_reset`` / ``email_verification``)
  + ``expires_at`` + ``used_at``.

- **``AuthSettings`` mixin** exposing every knob the bundled flow
  needs:

  - ``AUTH_AUTO_ACTIVATE`` — skip activation email entirely
    (dev / CI mode); user is born active and the signup
    response carries JWTs immediately.
  - ``AUTH_RETURN_TOKEN_IN_RESPONSE`` — surface the activation /
    reset link in the JSON body instead of (or in addition to)
    sending the email. Toggles automatically when
    ``EmailUtils`` isn't wired.
  - ``AUTH_ACTIVATION_TTL_SECONDS`` (default 7d) /
    ``AUTH_PASSWORD_RESET_TTL_SECONDS`` (default 1h).
  - ``AUTH_ACTIVATION_URL_TEMPLATE`` /
    ``AUTH_PASSWORD_RESET_URL_TEMPLATE`` — front-end URL
    skeleton with ``{token}`` placeholder.
  - ``AUTH_ACTIVATION_TEMPLATE`` /
    ``AUTH_PASSWORD_RESET_TEMPLATE`` — Jinja2 template names.
  - ``AUTH_PASSWORD_MIN_LENGTH`` (default 12).

- **Default email templates** bundled under
  ``tempest_fastapi_sdk/auth/templates/activation.html`` and
  ``password_reset.html``. ``EmailUtils.render_template`` falls
  back to the SDK directory when the caller-supplied
  ``template_dir`` doesn't ship one with the same name, so the
  bundled flow renders out of the box. Override by placing a
  matching file in the project's template directory.

### Changed

- ``[email]`` extra now pulls ``email-validator`` so the
  Pydantic ``EmailStr`` fields used by ``SignupSchema`` / login
  / reset DTOs validate without a separate dependency.
- ``EmailUtils.render_template`` now accepts callers without an
  explicit ``template_dir`` — the SDK's bundled auth templates
  are reachable by default.

### Security

- Password-reset request endpoint always returns 202 and a
  generic message. Probing emails can no longer enumerate
  account existence.
- Activation + reset tokens are stored hashed (SHA-256, 48
  bytes of entropy on the plaintext). One-shot — ``used_at``
  prevents replay. TTL-bounded.

## [0.30.3] — 2026-06-04

### Fixed

- **Noisy lint output after ``tempest db revision``.** The
  post-write hooks ran in the wrong order — ``ruff_fix`` first,
  ``ruff_format`` second — so the linter loudly complained about
  ``W291`` (trailing whitespace in the docstring header) and
  ``E501`` (long ``sa.Column`` lines) that the formatter would
  fix on the very next hook. The final file was correct but
  stdout looked like the revision failed. Two adjustments:

  - Hooks reordered to ``ruff_format, ruff_fix`` so the formatter
    wraps lines + strips whitespace **before** the linter sees
    them.
  - Both hooks pass ``--quiet`` so the "found N errors / N fixed
    / M remaining" preamble is suppressed when nothing actionable
    is left.

  Existing projects: re-run ``tempest db init`` against an empty
  ``alembic.ini`` to regenerate, or hand-edit
  ``[post_write_hooks]`` to match the new layout.

## [0.30.2] — 2026-06-04

### Security

- **``alembic.ini`` no longer stamps the database URL.** The
  generated ini ships with ``sqlalchemy.url = `` empty so
  credentials never enter version control. Both the SDK
  ``env.py`` template and ``AlembicHelper.config`` resolve the
  URL at runtime:

  1. ``db_url`` passed to the constructor or via
     ``--database-url`` on the CLI.
  2. ``DATABASE_URL`` env var (loaded from ``.env``).
  3. ``src.core.settings.settings.DATABASE_URL`` in scaffolded
     projects.

  When none of the three is set the env.py raises
  ``RuntimeError("DATABASE_URL is empty. Set it on the
  environment, in src/core/settings.py, or pass --database-url
  to the CLI.")`` so missing config fails loudly instead of
  silently connecting to whatever was left on the ini.

### Migration for existing projects

Open ``alembic.ini`` and blank the ``sqlalchemy.url`` line:

```ini
sqlalchemy.url =
```

Then rotate the leaked credential at the database (assume the
secret is compromised the moment it landed in a Git commit).
Append ``alembic.ini`` to a code-search / CI hook so the line
stays empty across future PRs.

If your ``alembic/env.py`` was older than v0.21.x and does not
import ``tempest_fastapi_sdk.db.alembic_hooks``, rerun
``tempest db init`` against an empty file to regenerate it, or
copy the new template from
``tempest_fastapi_sdk/db/_alembic_templates/env.py.template``.

## [0.30.1] — 2026-06-04

### Added

- **``reorder_base_columns_first`` Alembic hook** in
  ``tempest_fastapi_sdk.db.alembic_hooks``. Wired into the
  scaffolded ``env.py`` so every autogenerated migration emits
  ``id`` → ``is_active`` → ``created_at`` → ``updated_at`` at the
  top of every ``op.create_table``, followed by the table's own
  constraints + subclass columns in their original relative
  order. The 4 ``BaseModel`` columns now ship in the documented
  order without manual editing.
- **``compose_hooks(*hooks)``** helper for chaining multiple
  ``process_revision_directives`` callables.
- **``BASE_COLUMN_ORDER``** tuple re-exported at the package
  root for tools that want to mirror the convention elsewhere.

### Docs

- Existing projects: copy the new ``env.py`` snippet from
  ``tempest_fastapi_sdk/db/_alembic_templates/env.py.template``
  (the ``process_revision_directives=reorder_base_columns_first``
  argument is added to both ``context.configure`` calls), or
  re-run ``tempest db init`` against an empty ``alembic.ini``
  to regenerate. Future ``tempest db revision --autogenerate``
  picks up the hook automatically.

## [0.30.0] — 2026-06-04

### Added

- **`tempest db` subcommand group** — Alembic wrapper backed by
  the existing ``AlembicHelper``. Commands:

  - ``tempest db init`` — scaffold ``alembic.ini`` + ``alembic/env.py``.
  - ``tempest db revision -m "<msg>" [--manual]`` — create a new
    migration (autogenerate by default).
  - ``tempest db upgrade [target]`` — apply pending migrations
    (``head`` by default).
  - ``tempest db downgrade [target]`` — roll back (default
    ``-1``, i.e. one step).
  - ``tempest db current`` — print the applied revision.
  - ``tempest db history [-v]`` — list revisions newest → oldest.

  ``DATABASE_URL`` resolves in this order: ``--database-url`` flag
  → ``DATABASE_URL`` env var →
  ``src.core.settings.settings.DATABASE_URL`` →
  ``sqlalchemy.url`` from ``alembic.ini``. The async driver
  suffix is stripped automatically before Alembic runs.

- **`tempest user` subcommand group** — seed and inspect users
  via the project's concrete ``UserModel`` (default
  ``src.db.models:UserModel``, overridable with ``--model``).
  Bootstraps the first admin row so ``/admin`` login works
  without manual SQL.

  - ``tempest user create --email X --password Y [--admin]``
    — insert one user. Omitting ``--password`` reads it
    interactively (no shell history leak). Password ≥ 8 chars
    enforced.
  - ``tempest user list [--admin]`` — print
    ``id  email  +admin/...  active/inactive`` per row.

### Docs

- ``docs/recipes/cli{,.en}.md`` adds full sections for
  ``tempest db`` and ``tempest user`` with flag reference + the
  ``DATABASE_URL`` resolution order.
- ``docs/learning/marketplace/index{,.en}.md`` setup block now
  runs ``tempest db revision`` + ``tempest db upgrade`` +
  ``tempest user create --admin`` between the docker compose up
  and the ``uv run python main.py`` so ``/admin`` login works on
  first run.
- ``README.md`` Command-line interface recipe grows the same
  two sections.

## [0.29.1] — 2026-06-04

### Fixed

- **Scaffold no longer ships an empty ``user`` table** — the
  scaffolded ``src/db/models/__init__.py`` was empty, so
  Alembic's ``--autogenerate`` found no models in
  ``BaseModel.metadata`` and never emitted the ``user`` table.
  The result: ``/admin`` login failed because the table didn't
  exist. The fix:

  - New ``src/db/models/user.py.tmpl`` ships a concrete
    ``UserModel(BaseUserModel)`` mapped to the ``users`` table.
  - ``src/db/models/__init__.py.tmpl`` re-exports ``BaseModel``
    + ``UserModel`` so Alembic sees the metadata.
  - ``src/api/app.py.tmpl`` now wires ``AdminSite`` +
    ``AdminModel(UserModel)`` + ``UserModelAuthBackend`` +
    ``make_admin_router`` out of the box.
  - ``tempest new`` default extras bumped from ``auth`` to
    ``auth,admin`` so the admin wiring boots without a manual
    extras tweak.

  Upgrade path for an already-scaffolded project: copy the new
  ``UserModel`` definition into ``src/db/models/user.py``,
  re-export from ``src/db/models/__init__.py``, then run
  ``uv run alembic revision --autogenerate -m "user table"``
  followed by ``uv run alembic upgrade head``.

## [0.29.0] — 2026-06-04

### Fixed

- **Postgres 18 mount path.** v0.26.0 bumped the pinned image to
  ``postgres:18-alpine`` but kept the historical
  ``postgres-data:/var/lib/postgresql/data`` mount. Postgres 18+
  reorganized the data layout — the image now refuses to start
  with ``Error: in 18+, these Docker images are configured to
  store database data in a format which is compatible with
  "pg_ctlcluster" (...) Counter to that, there appears to be
  PostgreSQL data in: /var/lib/postgresql/data (unused
  mount/volume)``. The generator now emits
  ``postgres-data:/var/lib/postgresql`` (no ``/data`` suffix);
  Postgres creates the version-specific subdirectory inside.

  Upgrade path for existing projects:

  ```bash
  docker compose down -v          # WIPES local data — back up first
  tempest generate --docker --force
  docker compose up -d
  ```

### Added

- **``CSRFMiddleware`` + ``make_csrf_token_dependency``** — full
  double-submit-cookie CSRF guard for cookie-authenticated
  endpoints. Unsafe verbs (``POST`` / ``PUT`` / ``PATCH`` /
  ``DELETE``) must carry both the ``csrf_token`` cookie and a
  matching ``X-CSRF-Token`` header; mismatch returns 403 with
  the SDK envelope ``{"code": "CSRF_VALIDATION_FAILED"}``.

  Safe methods always pass. ``exclude_paths`` lets bearer-auth
  ``/api/`` routes skip the check (JWT bearer is not subject to
  CSRF since the browser doesn't auto-attach it).

  ``generate_csrf_token(n_bytes=32)`` mints fresh tokens;
  ``make_csrf_token_dependency()`` returns a FastAPI dependency
  that the login/template endpoint can call to seed the cookie.

- **OAuth2 / OIDC providers** under ``tempest_fastapi_sdk.api.oauth``:

  - ``GoogleOAuthClient`` — Google identity, OIDC-compatible,
    default scopes ``openid email profile``.
  - ``GitHubOAuthClient`` — GitHub OAuth (not OIDC; user info
    via ``GET /user``), default scopes ``read:user user:email``.
  - ``OIDCProvider`` — generic discovery-driven OIDC client for
    Auth0 / Keycloak / Okta / Microsoft Entra / Cognito. Pass
    the authorize / token / userinfo URLs explicitly.

  All providers share the same surface — ``build_authorize_url(state, **extra)``,
  ``exchange_code(code) -> OAuthTokens``, ``fetch_user(tokens) -> OAuthUser``.
  Identity is normalized to ``OAuthUser(provider, subject, email,
  name, picture, raw)`` so the application sees one shape
  regardless of IdP. CSRF-grade state via ``generate_oauth_state()``.

  Built on the v0.28.0 ``HTTPClient`` for retries + circuit-breaker
  on the IdP — handy when Auth0 / Google occasionally hiccup.
  Requires the ``[http]`` extra.

## [0.28.0] — 2026-06-04

### Added

- **Prometheus ``/metrics`` endpoint + middleware.** New
  ``tempest_fastapi_sdk.api.routers.metrics`` module exposes:

  - ``PrometheusMiddleware`` — tracks
    ``http_requests_total{method, path, status}`` (Counter),
    ``http_request_duration_seconds{method, path}`` (Histogram),
    ``http_requests_in_progress{method}`` (Gauge). Uses the
    matched route template as the ``path`` label so cardinality
    stays bounded.
  - ``make_prometheus_registry()`` — fresh ``CollectorRegistry``
    decoupled from the default singleton.
  - ``make_prometheus_router(registry=…, path="/metrics",
    dependencies=…)`` — ``GET /metrics`` rendering the exposition
    format. Pair with ``Depends(require_x_token)`` in production.

  Requires the new ``[prometheus]`` extra (``prometheus-client``).

- **``HTTPClient`` — typed httpx wrapper** at
  ``tempest_fastapi_sdk.utils.http_client``:

  - Bounded retries with exponential backoff
    (``RetryPolicy(max_attempts, backoff_initial_seconds,
    backoff_max_seconds, retry_statuses)``); retries on network
    errors + configurable 5xx/429.
  - Per-host circuit breaker — trips after ``failure_threshold``
    consecutive failures, half-open after
    ``recovery_seconds``; raises ``CircuitOpenError`` while open.
  - ``X-Request-ID`` propagation from the
    ``request_id_ctx`` contextvar to outbound requests so
    correlation flows downstream.
  - Verb-level conveniences (``get``/``post``/``put``/``patch``/
    ``delete``) on top of the unified ``request()`` core.

  Requires the new ``[http]`` extra (``httpx``).

- **``BodySizeLimitMiddleware``** — short-circuits oversized
  requests at the ASGI layer:

  - Header check on ``Content-Length`` (fast path).
  - Streaming check for chunked / unknown-length bodies.
  - ``exclude_paths`` lets specific routes (e.g. media uploads)
    opt out and enforce their own per-endpoint limit.
  - Responds ``413`` with the SDK envelope
    ``{"code": "REQUEST_BODY_TOO_LARGE", "details": {"max_bytes": …}}``.

- **``BaseRepository.bulk_create_values(rows)``** — single
  ``INSERT … VALUES (…), (…)`` round-trip for batch persistence
  without unit-of-work overhead.

- **``BaseRepository.bulk_upsert(rows, conflict_columns,
  update_columns=None)``** — dialect-aware
  ``INSERT … ON CONFLICT DO UPDATE``. Picks Postgres or SQLite
  syntax automatically; raises ``NotImplementedError`` on other
  dialects so the caller can fall back to a
  ``SELECT FOR UPDATE`` loop.

### Changed

- ``[all]`` extra now includes ``httpx`` and
  ``prometheus-client``.

## [0.27.0] — 2026-06-04

### Added

- **New documentation section: Learning Projects** (PT-BR + EN-US).
  Didactic projects built end-to-end on the SDK so users can learn
  how the primitives compose in a realistic scenario.

- **First learning project: 🛒 Marketplace** — Mercado Livre /
  Shopee–style multi-tenant sales platform without external
  integrations. Covers the full SDK stack:

  - **Business rules** — every domain invariant numbered (U-01…
    G-04) with rationale. 41 rules across 10 sections.
  - **Domain model** — UML class diagram, ER diagram, enum
    diagrams (Mermaid), per-entity invariant table, and entity →
    SDK primitive mapping.
  - **Critical flows** — sequence diagrams for the 5 trickiest
    flows (signup, member invitation, product creation with
    images, idempotent checkout, shipping with SSE), plus state
    machines for ``Order`` and ``Invitation``.
  - **Endpoint map** — full REST surface as a table (method +
    path + auth role + idempotency + status + description).

  Exercises: ``BaseUserModel``, ``PasswordUtils``, ``JWTUtils``,
  ``make_jwt_user_dependency``, ``make_role_dependency``,
  ``BaseRepository[T]``, ``generate_opaque_token``,
  ``EmailUtils.render_template``, ``UploadUtils`` +
  ``MinIOUploadStorage``, ``IdempotencyMiddleware``,
  ``EventStream`` / ``sse_response``, ``AsyncTaskBrokerManager``,
  ``AsyncBrokerManager``, ``AsyncRedisManager`` + ``@cached``,
  ``MetricsUtils``, ``register_exception_handlers`` + the
  ``AppException`` hierarchy, ``configure_logging`` +
  ``make_logs_router``.

### Docs

- ``docs/learning/index{,.en}.md`` — section index with the
  catalog of learning projects (Marketplace shipped; library,
  scheduling, recurring billing planned).
- ``docs/learning/marketplace/{index,business-rules,domain,flows,api}{,.en}.md``
  — 10 new bilingual pages.
- ``mkdocs.yml`` adds the section to PT nav and the i18n
  ``nav_translations`` block (now 31 navigation elements,
  was 23).

## [0.26.0] — 2026-05-31

### Added

- **`tempest generate --docker`** — regenerate
  ``docker-compose.yaml`` (and refresh the ``.env.example`` service
  block) in an existing project. Reads the project's
  ``pyproject.toml`` to discover the currently pinned SDK extras
  unless ``--extras`` is given explicitly. Refuses to overwrite a
  hand-edited compose file without ``--force``. The ``.env.example``
  addendum is idempotent — re-running the command does not
  duplicate the service blocks.

  Flags:

  - ``--docker`` — selects the compose generator.
  - ``--path / -p`` — project root (default: cwd).
  - ``--extras`` — override discovered extras.
  - ``--name`` — override the container-name prefix.
  - ``--force / -f`` — overwrite existing compose file.

- **All Pydantic schemas and settings mixins now ship
  ``title`` + ``description`` + ``examples`` metadata** on every
  field. JSON-Schema consumers (FastAPI ``/docs``, ``/redoc``,
  IDE tooling, ``pydantic.model_json_schema()``) render rich
  metadata out of the box; OpenAPI examples populate the
  Swagger UI examples picker without further configuration.

  Surfaces covered:

  - ``settings.mixins`` — every ``*Settings`` mixin
    (``ServerSettings``, ``LogSettings``, ``DatabaseSettings``,
    ``RedisSettings``, ``RabbitMQSettings``, ``JWTSettings``,
    ``CORSSettings``, ``EmailSettings``, ``UploadSettings``,
    ``TokenSettings``, ``WebPushSettings``, ``TaskIQSettings``,
    ``MinIOSettings``).
  - ``schemas.pagination`` — ``BasePaginationFilterSchema``,
    ``BasePaginationSchema``, ``CursorPaginationFilterSchema``,
    ``CursorPaginationSchema``.
  - ``schemas.response`` — ``BaseResponseSchema``.
  - ``schemas.logs`` — ``LogEntrySchema``.
  - ``webpush.schemas`` — ``WebPushKeysSchema``,
    ``WebPushSubscriptionSchema``, ``WebPushPayloadSchema``.

### Changed

- **`docker-compose.yaml` image tags bumped** to the current
  major releases on Docker Hub:

  - ``postgres:16-alpine`` → ``postgres:18-alpine``. Postgres 14+
    has used ``scram-sha-256`` by default; no client-side change
    required.
  - ``redis:7-alpine`` → ``redis:8-alpine``. Note Redis 8.0+
    ships under a tri-license (RSALv2 / SSPLv1 / AGPLv3); the
    earlier ``<=7.2.4`` line was 3-Clause BSD. Internal use is
    unaffected; redistribution may need to pick a compatible
    license tier.
  - ``rabbitmq:3-management-alpine`` →
    ``rabbitmq:4-management-alpine``. ``RABBITMQ_DEFAULT_USER`` /
    ``RABBITMQ_DEFAULT_PASS`` remain functional;
    ``RABBITMQ_DEFAULT_VHOST=/`` made explicit in the rendered
    compose.

  Per-service tightening:

  - ``redis`` now boots with ``--appendonly yes`` so dev data
    survives container restarts; ``start_period: 5s`` lets the
    healthcheck wait for the AOF rewrite path.
  - ``rabbitmq`` healthcheck uses ``rabbitmq-diagnostics -q ping``
    (quiet) with ``start_period: 30s`` to absorb the broker's
    cold boot.
  - ``postgres`` healthcheck gains ``start_period: 10s``.

## [0.25.1] — 2026-05-31

### Changed

- **Tighter typing across the SDK's public surface — `Any` removed
  from most signatures.** The recent backend/protocol additions
  landed with `Any` in places where a concrete type or
  `Protocol` is just as ergonomic:

  - `UploadStorage.write_stream(..., validator=…)` and
    `UploadUtils.save(..., storage=…)` now accept the explicit
    `ContentValidator = Callable[[bytes], bool]` and
    `UploadStorage | None` types. Mypy and IDEs can now flag
    wrong shapes instead of waving them through.
  - `RedisIdempotencyStore(client=…)` takes a new `_RedisLike`
    `Protocol` (async `get(key)` / `set(key, value, ex)`) so the
    cache is decoupled from `redis-py` for type-checking while
    still accepting any compatible client.
  - `make_app_exception_handler` / `make_http_exception_handler`
    / `make_unhandled_exception_handler` return typed
    `Callable[[Request, ExcT], Awaitable[JSONResponse]]` aliases
    (`AppExceptionHandler`, `HTTPExceptionHandler`,
    `UnhandledExceptionHandler`).
  - `AsyncMinIOClient.__aexit__` and `ObjectStat.raw` annotate
    their real types (`TracebackType | None`,
    `minio.datatypes.Object`) via `TYPE_CHECKING` imports.
  - `EmailUtils._jinja_env`, `_aiofiles`, `_aiosmtplib` use
    `ModuleType | None` / `jinja2.Environment | None` instead of
    `Any`.

  The behavior is unchanged — only the types tightened, so
  callers see better autocomplete and downstream refactors stay
  honest.

### Removed

- `tempest_fastapi_sdk.utils.storage_backends._stream_upload_file`
  helper (was private, unused).

## [0.25.0] — 2026-05-31

### Added

- **`tempest new` now generates a `docker-compose.yaml`** wired
  with only the supporting infrastructure the picked extras
  require. The mapping:

  - `[cache]` → Redis 7 (alpine)
  - `[queue]` or `[tasks]` → RabbitMQ 3 with the management UI
    exposed at `http://localhost:15672`
  - `[minio]` → MinIO + a one-shot bootstrap container that
    creates the `uploads` bucket
  - `[email]` → MailHog (catches outbound SMTP, UI at
    `http://localhost:8025`)

  Postgres is always wired (the SDK's DB primitives are core), so
  every scaffolded project gets a one-command path to a real
  database via `docker compose up -d`. The scaffolded `.env` keeps
  SQLite as the default URL so the smoke run works without Docker.

- **`.env.example` gains a service-aware addendum** matching the
  same extras → environment variables. Picking `--extras cache,minio`
  writes `REDIS_URL`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY` (etc.)
  into `.env.example` so the developer can copy the file straight
  to `.env` and the service connects to the compose-spawned
  containers without further editing.

- **`tempest_fastapi_sdk.cli.docker_compose` module** exposes
  `generate(project_name, extras)` and `env_block_for(extras)` as
  public helpers. Both pin image tags to specific versions that
  are smoke-tested with the SDK; bumping the pins should go
  through the smoke suite before being released.

### Changed

- The post-scaffold "Next steps" hint printed by `tempest new`
  now reminds the developer to run `docker compose up -d` before
  `uv run python main.py`.

## [0.24.0] — 2026-05-31

### Added

- **`UploadUtils` pluggable storage backends.** New
  ``UploadStorage`` protocol under ``tempest_fastapi_sdk.utils``
  with two ready implementations:

  - ``LocalUploadStorage(base_dir)`` — disk-backed, matches the
    historical ``UploadUtils`` behavior.
  - ``MinIOUploadStorage(client, bucket=None)`` — wraps the
    ``AsyncMinIOClient`` shipped in v0.23.0, requires the
    ``[minio]`` extra.

  ``UploadUtils.save()`` now accepts an optional ``storage=``
  keyword. When provided, the validated upload is sent to the
  backend instead of the local filesystem — validation pipeline
  (extension / MIME / size / magic bytes / ``content_validator``)
  is identical for both targets. Calls without ``storage=``
  continue to write to ``upload_dir`` unchanged.

- **``IdempotencyMiddleware``** under
  ``tempest_fastapi_sdk.api.middlewares``. Caches the full response
  for ``POST`` / ``PUT`` / ``PATCH`` / ``DELETE`` requests keyed
  by ``(method, path, Idempotency-Key)`` so client retries don't
  re-execute the handler. Opt-in per request — endpoints without
  the header pass through.

  Two stores ship out of the box:

  - ``MemoryIdempotencyStore`` — async-lock-guarded dict with TTL
    eviction. Single-replica only.
  - ``RedisIdempotencyStore(client, prefix="idem:")`` — backed by
    an async Redis client. Required in multi-replica deployments.

  Custom backends can implement the ``IdempotencyStore`` protocol.

- **``EmailUtils.render_template(template_name, context)``.**
  Optional Jinja2 template rendering for transactional emails.
  Pass ``template_dir`` at construction time, then call
  ``render_template`` to produce the HTML / text body fed into
  ``send()``. HTML autoescaping is enabled for ``.html`` / ``.htm``
  / ``.xml`` templates so caller-supplied values can't break out
  into markup.

### Changed

- ``[email]`` extra now ships Jinja2 alongside ``aiosmtplib`` so
  ``render_template`` works without a separate ``[admin]``
  dependency. Existing installs should re-pull the extra:
  ``pip install -U "tempest-fastapi-sdk[email]"``.

### Fixed

- ``tests/utils/test_lazy_extras.py::hide_module`` fixture now
  fully restores ``sys.modules`` on teardown. The previous version
  only saved entries matching the ``_hide`` target, so tests that
  reimported the whole ``tempest_fastapi_sdk`` package leaked the
  freshly-built class objects into later tests — surfaced as
  ``pytest.raises(...)`` failing to catch exceptions that were
  raised under a different (re-imported) class identity.

## [0.23.0] — 2026-05-31

### Added

- **MinIO / S3 object storage module.** New
  `tempest_fastapi_sdk.storage` package exporting
  `AsyncMinIOClient` and `ObjectStat`. Async-friendly facade over
  the official `minio` package — every blocking call is wrapped in
  `asyncio.to_thread`, so the FastAPI event loop stays responsive
  while uploads/downloads run in the executor.

  Operations covered:

  - **Buckets** — `bucket_exists`, `ensure_bucket`, `list_buckets`,
    `remove_bucket`.
  - **Objects** — `put_object` (bytes or file-like), `fput_object`
    (from disk), `get_object_bytes`, `fget_object`, `stream_object`
    (chunked async iterator), `stat_object`, `list_objects`
    (prefix + recursion), `remove_object`, `copy_object`.
  - **Presigned URLs** — `presigned_get_url`, `presigned_put_url`
    with `timedelta` expiry.

  The full synchronous `minio.Minio` client stays accessible via
  the `.client` attribute when you need surface beyond the wrapper
  (SSE-KMS, lifecycle XML, bucket replication, etc.).

- **`MinIOSettings` mixin** under `tempest_fastapi_sdk.settings`,
  exposing `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`,
  `MINIO_SECURE`, `MINIO_REGION`, `MINIO_DEFAULT_BUCKET`. Re-exported
  at the package root.

- **New `[minio]` extra** — `pip install
  "tempest-fastapi-sdk[minio]"`. The `minio` package is lazy-loaded
  at `AsyncMinIOClient.__init__` so projects without storage don't
  pay the import cost.

### Docs

- New `docs/recipes/storage{,.en}.md` recipe with end-to-end
  examples: `UploadFile` upload, streaming download, presigned
  upload (direct from browser), presigned download, prefix
  listing, copy/move.
- `docs/reference.md` adds entries for `AsyncMinIOClient` and
  `ObjectStat`.

## [0.22.1] — 2026-05-31

### Fixed

- **File-descriptor leak in `configure_logging`.** Calling
  `configure_logging` twice (normal in tests, and in any service
  that supports hot-reload) removed the previous file handlers
  without closing them. After ~100 reconfigure cycles the kernel
  refused new file opens with ``OSError: [Errno 24] Too many open
  files``. The previous handlers are now `close()`-d before
  `removeHandler` so each call releases its FDs.

### Security

- **`UploadUtils.delete()` now refuses paths outside `upload_dir`.**
  Before, `utils.delete("/etc/passwd")` would happily `unlink()`
  whatever the caller passed. Any service that forwarded a
  user-supplied filename to `delete()` was effectively giving the
  caller an `rm` primitive bounded only by process permissions.
  The method now resolves the input against `upload_dir`, treats
  relative inputs as relative to `upload_dir`, and raises
  `InvalidFileTypeException` (with `reason="escapes upload_dir"`)
  for anything that escapes — including absolute paths to
  unrelated directories and `..`-style traversal.

  Callers that already passed paths returned by
  `UploadUtils.save()` keep working unchanged; only path-traversal
  attempts begin to raise.

### Changed

- `make_app_exception_handler` docstring now accurately states that
  `log_level` defaults to `logging.INFO` (the previous text claimed
  `logging.ERROR`, which is what the function was *originally*
  intended to do but never matched the signature).

## [0.22.0] — 2026-05-31

### Changed

- **`configure_logging` now writes to stdout *and* `logs/` by
  default.** Before, file logging only activated when the caller
  passed `log_dir=...`; the default scaffold often forgot it and the
  service ran with no on-disk audit trail. The new defaults:

  - `log_dir` default is now `"logs"` (was `None`).
  - New `stdout: bool = True` flag controls the terminal handler.
  - New `file_output: bool = True` flag controls the per-level +
    `500.log` file handlers.
  - Passing `stdout=False, file_output=False` raises
    `ValueError` — that combination silences every handler and is
    almost always a mistake.

  Backwards compatibility: passing `log_dir=None` (the old sentinel
  for "no files") still works and produces stdout-only behavior.
  Test suites that don't want logs/ files in cwd should now pass
  `file_output=False` explicitly.

- **`LogUtils(name=..., level=..., json_output=...)` and
  `LogUtils.configure(...)`** forward the same three flags
  (`log_dir`, `stdout`, `file_output`) so the imperative wrapper
  stays aligned with `configure_logging`.

### Migration

If your service was relying on `configure_logging(level=..., json_output=...)`
to be stdout-only, either:

```python
# Option A — opt out explicitly:
configure_logging(level="INFO", file_output=False)

# Option B — keep stdout-only via the old sentinel:
configure_logging(level="INFO", log_dir=None)
```

If your test suite spins up `configure_logging` or `LogUtils`
in-process, pass `file_output=False` to avoid stray ``logs/``
folders in the working directory.

## [0.21.3] — 2026-05-31

### Changed

- **`AppException` and 4xx `HTTPException` now emit an `INFO`-level
  log line.** Before, both paths were silent — the response went
  out with the SDK envelope but operators saw nothing in stdout
  or `info.log` for a `401`, `404`, `422` (etc.), making "API
  returned 4xx but I see no trace" debugging painful. The new
  behavior:

  - `AppException` with `status_code < 500` → `INFO` log, no
    traceback, no `500.log` marker.
  - `AppException` with `status_code >= 500` → `log_level`
    (default `ERROR`) + traceback + `HTTP_500_MARKER` so the
    record lands in `500.log`.
  - `HTTPException` 4xx → `INFO` log, no traceback. 5xx
    behavior unchanged (already logged at `log_level` + 500.log).
  - Unhandled `Exception` catch-all unchanged.

  The log line includes the request method, path, status code,
  exception code (for `AppException`) and request id — enough to
  grep for in `info.log` without paging the operator at 3am.

### Added

- **`make_app_exception_handler(*, log_level)`** factory exposed
  via `tempest_fastapi_sdk.api.handlers` and re-exported at the
  package root. The existing `app_exception_handler` callable is
  kept as a thin wrapper for backwards compatibility.

## [0.21.2] — 2026-05-31

### Fixed

- **Alembic wiped `configure_logging` during lifespan.** The stock
  `alembic/env.py` (generated by `alembic init`) ends with
  `fileConfig(config.config_file_name)`. That call defaults to
  `disable_existing_loggers=True` AND honors `[logger_root]` in
  `alembic.ini`, which the SDK was writing as
  `level = WARN, handlers = stderr`. When `AlembicHelper.upgrade()`
  ran during `lifespan`, every SDK logger configured via
  `configure_logging` got disabled and root was reset to WARN +
  stderr. The 500 catch-all handler kept building responses but
  nothing reached stdout, `error.log` or `500.log` — operators saw
  the 500 in the browser and zero log lines.

  Two-part fix shipped in the SDK templates:

  1. **`env.py.template`** — the call is now guarded on the
     presence of a `[loggers]` section AND uses
     `disable_existing_loggers=False`, so it never wipes the host's
     logging tree:

     ```python
     if config.config_file_name is not None:
         import configparser

         _ini = configparser.ConfigParser()
         _ini.read(config.config_file_name, encoding="utf-8")
         if _ini.has_section("loggers"):
             fileConfig(config.config_file_name, disable_existing_loggers=False)
     ```

  2. **`AlembicHelper.init()`** stops emitting the
     `[loggers]/[handlers]/[formatters]/[logger_*]/[handler_*]/[formatter_*]`
     sections into the generated `alembic.ini`. Alembic's own
     loggers inherit from root (which the host configures), and the
     guarded `fileConfig` above no-ops when no `[loggers]` section
     exists.

  Upgrade path for existing projects: re-run `tempest new --force`
  (or `AlembicHelper.init`) to regenerate the templates, OR
  manually patch `alembic/env.py` to wrap the `fileConfig` call as
  shown above and remove the `[loggers]`/`[handlers]`/`[formatters]`
  blocks from `alembic.ini`.

## [0.21.1] — 2026-05-31

### Fixed

- **`raise HTTPException(500, ...)` bypassed the SDK 500 logger.**
  Starlette intercepts every `HTTPException` inside its own
  `ExceptionMiddleware` and routes it to a default handler that
  emits a bare `JSONResponse({"detail": exc.detail})` with no log
  entry. The 0.21.0 catch-all `Exception` handler never saw those
  raises, so `tempest-fastapi-sdk[0.21.0]` users hitting a 5xx
  endpoint reported `Internal Server Error` in the browser with
  zero output in stdout / `error.log` / `500.log`.

  Added a third handler — `make_http_exception_handler` registered
  for `starlette.exceptions.HTTPException` — that:

  - logs every 5xx (`status_code >= 500`) at ERROR with
    `exc_info=exc` and `HTTP_500_MARKER` so the record lands in
    both `error.log` and the dedicated `500.log`;
  - returns the SDK envelope (`detail` / `code` / `details`),
    preserving the original status code and any custom headers,
    so frontends consuming the same envelope across `AppException`
    and raw `HTTPException` don't need to branch;
  - leaves 4xx HTTPExceptions untouched (Starlette's default body
    and no log) since those represent normal client outcomes.

  `make_http_exception_handler` and the existing `log_traceback`
  / `log_level` knobs on `register_exception_handlers` are wired
  end-to-end; opt out of the trace with
  `register_exception_handlers(app, log_traceback=False)` when an
  APM is already capturing the stack.

## [0.21.0] — 2026-05-31

### Added

- **File logging to a `logs/` directory + a `/logs` reader endpoint.**
  `configure_logging` gained a `log_dir` parameter. When set (the
  scaffold defaults it to `"logs"` via `LOG_DIR`), the stdout handler
  is kept **and** one JSON file per level is written — `debug.log`,
  `info.log`, `warning.log`, `error.log`, `critical.log` — each
  receiving only its own level (exact match, never `level >=`). A
  dedicated **`500.log`** captures only uncaught-500 records: the
  catch-all exception handler now flags them with
  `HTTP_500_MARKER`, so grave failures are isolated and never buried.
  A 500 therefore appears in both `error.log` and `500.log`. File
  handlers always emit JSON regardless of `json_output`.

- **`make_logs_router` — a paginated, filterable, authenticated log
  reader.** `GET /logs` reads the on-disk JSON files and returns a
  `BasePaginationSchema[LogEntrySchema]` (newest first). Query params:
  `source` (`all` | each level | `500`), `q` (message substring),
  `start` / `end` (ISO-8601 range), `page`, `page_size`. Gated by a
  shared-secret `X-Token` header via `make_token_dependency` — an
  empty `TOKEN_SECRET` disables the check (dev only). New exports:
  `make_logs_router`, `LogSource`, `LogEntrySchema`. The `create_app`
  scaffold wires the router and passes `log_dir`/`token_secret`
  automatically.

### Fixed

- **`tempest fix` now always formats, even when lint violations remain.**
  `run_ruff_fix` ran `ruff check --fix` and short-circuited on its exit
  code before reaching `ruff format`. But `ruff check --fix` exits
  non-zero whenever *any* residual violation it cannot autofix is left
  (an over-length string/comment, an undefined name, …) — so a single
  unfixable line silently skipped the formatter for the whole file,
  leaving long **code** lines un-wrapped and extra blank lines intact.
  The formatter now runs unconditionally; the lint exit code is still
  surfaced afterwards so CI keeps failing on the leftover issues.

  Note: `ruff format` (and therefore `tempest fix`) never wraps long
  **string literals or comments** — this matches Black. Those `E501`
  lines stay and must be shortened by hand or silenced with
  `# noqa: E501`.

- **Autogenerated migrations are now lint-clean out of the box.**
  `AlembicHelper.init()` writes a `[post_write_hooks]` block into the
  generated `alembic.ini` that runs `ruff check --fix` followed by
  `ruff format` on every freshly created revision file. Previously the
  files Alembic emits failed `tempest lint` (`ruff check`) with `W291`
  (trailing whitespace in the docstring header when `down_revision` is
  `None` — `Revises: `) and `E501` (over-length `sa.Column(...)`
  lines):

  ```text
  W291 Trailing whitespace
   --> alembic/versions/...add_todos_table.py:4:9
  E501 Line too long (120 > 88)
   --> alembic/versions/...add_todos_table.py:30:89
  ```

  The hooks resolve the project's own `ruff` configuration, so every
  selected rule that is autofixable (`I`, `UP`, `W`, `E501`, …) is
  cleared at generation time. Requires `ruff` on `PATH` — already a
  dev dependency in every `tempest new` scaffold.

## [0.20.0] — 2026-05-31

### Changed

- **BREAKING — pagination uses `page_size` everywhere instead of `size`.**
  The field on `BasePaginationFilterSchema` was named `size` (default
  `10`) while the controller / service / repository keyword argument
  was named `page_size` (default `20`), forcing every consumer to
  rename the attribute on the way through:

  ```python
  # before — required renaming + a default-value gotcha
  result = await controller.paginate(
      filters=f.get_conditions(),
      page=f.page,
      page_size=f.size,
      ...,
  )
  ```

  Aligned the request schema, the response envelope and the
  repository return dict on a single name + default:

  - `BasePaginationFilterSchema.size` → `BasePaginationFilterSchema.page_size`
  - Default `10` → `20`
  - `BasePaginationFilterSchema.get_conditions()` strips
    `["page", "page_size", "order_by", "ascending"]`
  - `BasePaginationSchema.size` → `BasePaginationSchema.page_size`
  - `BaseRepository.paginate` return dict key `"size"` →
    `"page_size"`. `BaseService.paginate` and
    `BaseController.paginate` propagate the new key.
  - `build_pagination_link_header(size=..., size_param="size")` →
    `build_pagination_link_header(page_size=..., size_param="page_size")`.
    URLs now look like `?page=2&page_size=20` by default. Pass
    `size_param="size"` to keep the old query-string spelling
    without renaming the function argument.

  Migration: rename `size` to `page_size` on every consumer; if a
  service relied on the previous default of `10` items per page,
  pass `page_size=10` explicitly. The admin router's `_Pagination`
  helper now reads `result["page_size"]` from the repository
  response.

## [0.19.2] — 2026-05-31

### Added

- **Explicit `log_traceback` flag on the 500 catch-all handler.**
  The default is `True` — every uncaught exception emits the full
  traceback via `logger.log(..., exc_info=exc)` so the operator
  always has it. Set `log_traceback=False` only when an APM agent /
  Sentry / equivalent is already capturing the failure and the
  duplicated stack noise is unwanted. The flag is forwarded by
  `register_exception_handlers` and `make_unhandled_exception_handler`.

## [0.19.1] — 2026-05-31

### Fixed

- **Unhandled exceptions returned a bare `Internal Server Error`
  string with no log entry.** `register_exception_handlers` only
  wired a handler for `AppException`, so every uncaught `Exception`
  (e.g. `RuntimeError`, `KeyError`, downstream library failures)
  fell through to Starlette's default — which writes nothing beyond
  the access line and returns a six-word body. Operators were left
  blind to real failures.
  - Added a catch-all `Exception` handler that logs the full
    traceback at ERROR via the `tempest_fastapi_sdk.api.handlers`
    logger (so the application's `LogUtils` / `configure_logging`
    setup picks it up), attaches the active `X-Request-ID` for
    correlation, and returns the canonical SDK envelope:

    ```json
    {
        "detail": "Internal server error",
        "code": "INTERNAL_SERVER_ERROR",
        "details": {"request_id": "<id>"}
    }
    ```

  - `register_exception_handlers(app, include_traceback=True)`
    embeds the formatted traceback under `details.traceback` so
    development environments can surface the failure in the
    response body too. Production callers leave it off so module
    paths / SQL fragments / object reprs don't leak.
  - `register_exception_handlers(app, log_level=logging.WARNING)`
    overrides the log level when needed.
  - Reads the request ID from the contextvar first, then falls
    back to the `X-Request-ID` header — `BaseHTTPMiddleware`
    spawns a child task so the contextvar set in
    `RequestIDMiddleware.dispatch` doesn't always reach the
    handler.
  - New `make_unhandled_exception_handler` factory exported from
    `tempest_fastapi_sdk.api`.

### Documentation

- Repository recipe in `docs/recipes/database.md` and the README
  Alembic walk-through still showed the deprecated
  `class UserRepository(BaseRepository[UserModel]): model = UserModel`
  Django-style class-attribute pattern dropped in 0.16.0. Replaced
  with the constructor signature
  `super().__init__(session, model=UserModel)`.

## [0.19.0] — 2026-05-30

### Added

- **MkDocs Material documentation site** auto-deployed to GitHub
  Pages at <https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/>.
  Sixteen pages total: landing, installation, architecture (with
  Mermaid layering + request-lifecycle diagrams), the eleven-step
  tutorial as one linear page, twelve thematic recipe pages
  (Database, HTTP, Cache, Real-time, Queue & Tasks, Logging, Metrics,
  Admin, Testing, CLI, Security, Brazilian helpers), an auto-generated
  API Reference via `mkdocstrings`, a migration guide, a contributing
  guide and the bundled CHANGELOG.
- New `[docs]` dependency group (`mkdocs`, `mkdocs-material`,
  `mkdocstrings[python]`, `pymdown-extensions`,
  `mkdocs-include-markdown-plugin`) installed via
  `uv sync --group docs`.
- **`make docs-serve` / `make docs-build` / `make docs`** Makefile
  targets for local docs work (live reload at
  `http://127.0.0.1:8000`).
- **`.github/workflows/docs.yml`** publishes the site to GitHub Pages
  on every push to `main` that touches docs, the package, the README
  or the CHANGELOG.
- README now opens with a docs-site banner linking to Home / Tutorial
  / Recipes / API reference so readers landing on PyPI or GitHub
  reach the prose-rich version in one click.

## [0.18.0] — 2026-05-30

### Added

- **`tempest fix`** — one-shot "organize the project" CLI command that
  runs `ruff check --fix <target>` followed by `ruff format <target>`.
  Sorts and dedupes imports, drops unused imports, normalizes string
  quotes to double, strips trailing whitespace, then normalizes
  indentation / line length / blank lines / trailing newlines. Pass
  `--unsafe` to also apply ruff's unsafe-fixes pass.
- **`py.typed` marker** shipped inside the wheel so downstream mypy
  reads the SDK's inline type hints instead of bailing out with
  `Skipping analyzing "tempest_fastapi_sdk": module is installed, but
  missing library stubs or py.typed marker`. PEP 561-compliant.

## [0.16.2] — 2026-05-30

### Fixed

- **`tempest new .` still rejected the `.` shorthand when the cwd
  basename contained a hyphen.** 0.16.1 special-cased `.` but then
  validated the derived name (`Path.cwd().name`) with the strict
  Python-identifier regex `^[a-z][a-z0-9_]*$`, so a real-world cwd
  like `todolist-api` died with `error: project name must match
  ^[a-z][a-z0-9_]*$`. The derived name is now matched against a
  PEP 503 normalized distribution-name regex
  (`^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$`) — the same shape pyproject
  accepts under `[project] name`. Explicit names (`tempest new
  myproject`) keep the stricter Python-identifier rule because the
  string is also used as the package directory name.

### Changed

- **`tempest new` (no positional argument) now defaults to `.`.**
  Previously typer rejected the bare invocation with
  `Missing argument 'NAME'`. The default matches the
  scaffold-in-current-directory shape: running `tempest new` inside
  an empty project directory writes `main.py` / `pyproject.toml` /
  `src/` / `tests/` directly under that directory. Pass an explicit
  name to keep the legacy "create a new subdir" behavior.

## [0.16.1] — 2026-05-30

### Fixed

- **CLI required `jinja2` even though `[admin]` was not installed.** Importing
  `tempest_fastapi_sdk` eagerly walked into `admin/router.py`, which had a
  top-level `from fastapi.templating import Jinja2Templates`. Starlette's
  `templating` module raises `ImportError("jinja2 must be installed to use
  Jinja2Templates")` at import time, so `tempest --version` (or any other
  CLI command) blew up on environments that legitimately skipped the admin
  extra. The import is now deferred inside `make_admin_router`, raising a
  clear `Install with pip install tempest-fastapi-sdk[admin]` only when the
  router is actually constructed.
- **`tempest new .` rejected the `.` shorthand for "scaffold here".** The
  positional `name` was always run through the Python-identifier regex
  before resolving the target, so `tempest new .` died with
  `error: project name must match ^[a-z][a-z0-9_]*$`. The CLI now accepts
  `.` and treats it as "scaffold flatly in the current working directory";
  the package name is derived from the cwd's basename (still validated).
  `--path` is rejected alongside `.` because the target is unambiguous.

## [0.16.0] — 2026-05-30

Repository and exception APIs join the admin in dropping Django-style class-attribute configuration. The constructor signature is now the contract; subclasses survive only when they add behavior (custom queries, `except DomainError`) — never to "fill in" a required class attribute.

### Changed

- **BREAKING — `BaseRepository.model` / `not_found_exception` are constructor kwargs, not class attributes.** Plain CRUD works without a subclass:

  ```python
  repository = BaseRepository(session, model=UserModel)
  ```

  Subclasses kept around for custom queries forward both via `super().__init__`:

  ```python
  class UserRepository(BaseRepository[UserModel]):
      def __init__(self, session: AsyncSession) -> None:
          super().__init__(
              session,
              model=UserModel,
              not_found_exception=UserNotFoundError,
              not_found_message="Usuário não encontrado",
          )
  ```

  Replaces the previous `class UserRepository(BaseRepository[UserModel]): model = UserModel; not_found_exception: ClassVar[...] = ...` form. The synthesized `_build_default_repository_class` helper in `admin/config.py` is gone — `AdminModel.build_repository` now calls `BaseRepository(session, model=self.model)` directly.

- **BREAKING — `AppException.code` is a plain `str` class attribute and is overridable at the raise site via `code=`.** Same for `status_code=`. The `code: ClassVar[str]` annotation is removed from every shipped subclass (`NotFoundException`, `ConflictException`, `ForbiddenException`, `UnauthorizedException`, `ValidationException`, `TooManyRequestsException`, `InvalidTokenException`, `ExpiredTokenException`, `FileTooLargeException`, `InvalidFileTypeException`). Subclasses still exist for `isinstance` / `except DomainError` matching; class-level defaults still work; constructor wins when both are present:

  ```python
  raise NotFoundException(
      "Pedido não encontrado",
      code="ORDER_NOT_FOUND",
      details={"order_id": str(order_id)},
  )
  ```

## [0.15.0] — 2026-05-30

Admin configuration is now a plain typed instance instead of a Django-style subclass. The class form (`class UserAdmin(AdminModel[UserModel])` with `ClassVar` attributes and the `@site.register` decorator) is gone — register a constructed instance instead. Field options accept real SQLAlchemy column attributes, so typos surface in the editor rather than at runtime.

### Changed

- **BREAKING — `AdminModel` is an instance, not a subclass.** Replace

  ```python
  @site.register
  class UserAdmin(AdminModel[UserModel]):
      model = UserModel
      list_display: ClassVar[list[str]] = ["email", "is_admin"]
      ordering = "-created_at"
  ```

  with

  ```python
  site.register(AdminModel(
      model=UserModel,
      list_display=[UserModel.email, UserModel.is_admin],
      ordering=desc(UserModel.created_at),
  ))
  ```

  `list_display`, `list_filter`, `search_fields`, `readonly_fields` and `identity_field` accept SQLAlchemy column attributes (`UserModel.email`) **or** plain strings. `ordering` accepts a column (ascending), `desc(column)` / `asc(column)`, or a `"-field"` string. `AdminSite.register` / `get` / `require` / `iter_models` now take and return instances. The `@site.register` decorator form is removed.

### Added

- **`FieldRef` / `OrderRef`** — public type aliases for the admin field- and ordering-reference unions, exported from the package root.

### Fixed

- **Admin list-view descending `ordering` raised `AttributeError`.** A configured `"-created_at"` was passed verbatim to `paginate(order_by=...)`, which did `getattr(model, "-created_at")`. Ordering is now normalized to a `(column, ascending)` pair, so descending orders and `desc()` / `asc()` wrappers work correctly.

## [0.13.1] — 2026-05-30

### Fixed

- **PyPI wheel duplicate-filename rejection.** `tool.hatch.build.targets.wheel.force-include` was double-listing the admin templates and static assets (already picked up by the default package scan), producing a wheel that PyPI rejected with
  `400 Invalid distribution file. ZIP archive not accepted: Duplicate filename in local headers`. Removed the redundant directives; `admin/templates/` and `admin/static/` continue to be bundled by hatchling's default sdist/wheel rules.

## [0.13.0] — 2026-05-30

Django-style admin site — Phase 1 (read-only). Mount under `/admin` so the database port can stay private; operators sign in with a user row owned by the application instead of a shared admin password.

### Added

- **`BaseUserModel`** — abstract `BaseModel` subclass with `email` (unique,
  lowercased), `hashed_password`, `is_admin`, `last_login_at`, plus
  `set_password()` / `check_password()` / `normalize_email()` helpers.
- **`AdminAuthBackend`** ABC + **`UserModelAuthBackend`** default. Enforces
  `is_admin=True` and `is_active=True`, stamps `last_login_at`, exposes
  `principal_id` / `load_principal` / `display_name` so custom backends
  (LDAP, OAuth, external IAM) plug into the same flow.
- **`AdminSite`** — slug registry with `register`/`unregister`/`require`
  and decorator-style usage (`@site.register`).
- **`AdminModel[ModelT]`** — Django-flavored declarative configuration:
  `list_display`, `list_filter`, `search_fields`, `readonly_fields`,
  `ordering`, `page_size`, `identity_field`, `verbose_name(_plural)`,
  `repository_class`. Auto-synthesizes a default repository when one
  is not supplied.
- **`make_admin_router`** — wires the HTML routes: login / logout /
  dashboard / list (paginated + search + filter) / detail (read-only)
  / static. Jinja2 templates + minimal admin.css ship with the wheel.
- **`SignedCookieSessionStore`** — itsdangerous `TimestampSigner`, signed
  HttpOnly + Secure + SameSite=Lax cookie scoped to the admin prefix,
  8-hour default lifetime, per-session CSRF token.
- New optional extra **`[admin]`** (`jinja2`, `itsdangerous`).

## [0.11.0] — 2026-05-30

### Added

- **`BaseStrEnum` / `BaseIntEnum`** — shared enum bases under
  `tempest_fastapi_sdk.core.enums` with `values()` / `keys()` /
  `to_dict()` helpers so str- and int-valued enums no longer need a
  per-project base class. Exported from the package root.

### Changed

- **`BaseService.map_to_response` is now async-aware.** The base awaits
  the repository's `map_to_response` only when it returns an
  awaitable (`inspect.isawaitable`), so concrete services with async
  mappers no longer need to override the read methods. Existing sync
  mappers keep working unchanged.

## [0.10.0] — 2026-05-30

Security hardening primitives, hoisted from a downstream service so every
project inherits the same defenses instead of re-rolling them.

### Fixed

- **`RateLimitMiddleware` keyed on the transport peer behind a proxy.** The
  default key was `request.client.host`, which is the *reverse-proxy* IP
  once the app is fronted by one — collapsing every client into a single
  bucket (one abuser exhausts everyone's quota; the limit is effectively
  global). Added `trusted_ip_header=` so the key is the client IP resolved
  from a single edge-set header (e.g. `"x-real-ip"`). Default behavior is
  unchanged (peer IP) for the no-proxy case.

### Added

- **`get_client_ip` / `get_client_ip_from_scope`** — spoof-resistant client
  IP resolution. Trusts only a single, explicitly named edge-set header
  (never the client-controlled `X-Forwarded-For`), falling back to the
  transport peer.
- **`AttemptThrottle`** + **`TooManyRequestsException` (429)** — a
  backend-agnostic fixed-window failure counter for login / OTP / code
  verification flows. Keyed by any string, counts only failures, raises a
  429 with `Retry-After` when the budget is exhausted, and fails open on a
  backend outage. Works with any async Redis-like client (`ThrottleBackend`
  protocol).
- **`generate_opaque_token` / `hash_opaque_token` / `verify_opaque_token`**
  — single-use opaque tokens hashed at rest (SHA-256) with constant-time
  verification, for password reset / email verification / magic links.
  Pure standard library.
- **`HardenedStaticFiles`** — a `StaticFiles` subclass that stamps
  `X-Content-Type-Options: nosniff`, a locked-down `Content-Security-Policy`
  and `Cross-Origin-Resource-Policy` on every response, so serving
  user-uploaded files can't become a stored-XSS vector. Headers
  configurable via `DEFAULT_STATIC_SECURITY_HEADERS`.
- **`set_cookie` / `clear_cookie`** — secure-by-default cookie helpers
  (`HttpOnly`, `Secure`, `SameSite`) with matching set/clear flags so
  logout actually drops the cookie.
- **`RSAWebhookSignatureVerifier`** — asymmetric (RSA-SHA256/384/512)
  webhook signature verification for providers that sign with a private
  key and publish a public key (OpenPix/Woovi-style), complementing the
  existing HMAC `WebhookSignatureVerifier`. Requires `cryptography`.

## [0.9.0] — 2026-05-30

### Added

- **`UploadUtils` magic-byte content verification.** New opt-in
  `verify_magic_bytes=True` constructor flag sniffs the first bytes of every
  upload and rejects content whose real type does not match its declared
  `Content-Type` / the `allowed_mimetypes` allow-list — closing the polyglot
  hole where an HTML+JS payload served as `image/jpeg` passed the
  extension/MIME check. Recognizes JPEG, PNG, GIF, BMP, WebP and PDF.
- **`sniff_mime(prefix)` helper** (exported at the top level) — magic-byte
  MIME detector usable on its own to build custom `content_validator`
  predicates.
- **`UploadUtils.save(..., content_validator=...)`** — optional predicate run
  on the first chunk; returning `False` aborts the save and removes the
  partial file before any further bytes are written.
- **`UploadUtils.save(..., filename=...)`** — explicit, deterministic final
  filename (e.g. `f"{user_id}.jpg"`), reduced to its basename and guarded
  against path traversal. Takes precedence over `keep_original_name`.

All four additions are backwards compatible — existing `UploadUtils` calls
behave exactly as in 0.8.0 unless the new options are passed.

## [0.8.0] — 2026-05-17

### Breaking changes

- **`ServerSettings` field rename.** `HOST`, `PORT` and `DEBUG` were renamed to
  `SERVER_HOST`, `SERVER_PORT` and `SERVER_DEBUG`, and a new `SERVER_RELOAD`
  field was added. `LOG_LEVEL` and `LOG_JSON` moved out to a new
  `LogSettings` mixin.
  - **Migration:** rename the matching env vars in every `.env` /
    deployment manifest and replace `settings.HOST` / `settings.PORT` /
    `settings.DEBUG` / `settings.LOG_LEVEL` / `settings.LOG_JSON`
    accordingly. Mix `LogSettings` into `Settings` if the service was
    relying on `ServerSettings` for the log fields.
  - See the [Migration guide 0.7 → 0.8](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/)
    in the README for the full checklist.

### Added — Settings mixins (Tier 1)

- `LogSettings` (`LOG_LEVEL`, `LOG_JSON`) — extracted from `ServerSettings`.
- `EmailSettings` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `SMTP_FROM_ADDR`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_TIMEOUT_SECONDS`).
- `UploadSettings` (`UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`,
  `UPLOAD_ALLOWED_EXTENSIONS`, `UPLOAD_ALLOWED_MIMETYPES`).
- `TokenSettings` (`TOKEN_SECRET`).
- `WebPushSettings` (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`,
  `VAPID_SUBJECT`, `WEBPUSH_DEFAULT_TTL_SECONDS`).
- `TaskIQSettings` (`TASKIQ_BROKER_URL`, `TASKIQ_RESULT_BACKEND_URL`).

### Added — API helpers (Tier 2)

- `tempest_fastapi_sdk.run_server(app, *, settings=None, host=None,
  port=None, reload=None, **uvicorn_kwargs)` — canonical
  `src/server.py` entry point.
- `make_bearer_token_dependency(tokens, soft=False, ...)` —
  `Authorization: Bearer <jwt>` decoder returning the claims dict.
- `make_jwt_user_dependency(tokens, user_loader, *, soft=False,
  subject_claim="sub", ...)` — bearer + user loader in one factory.
- `is_valid_cep`, `normalize_cep`, `CEP`, `CEP_PATTERN` — Brazilian
  zipcode validators in `tempest_fastapi_sdk.utils.regex`.

### Added — Opt-in primitives (Tier 3)

- `tempest_fastapi_sdk.cache.cached(redis, ttl=300, key_prefix="",
  serializer=..., deserializer=..., skip_cache=...)` — Redis-backed
  function cache decorator.
- `make_tool_spec_router(spec, *, path="/tool-spec", tag="meta")` —
  `/tool-spec` manifest router; accepts dict / sync / async providers.
- `make_role_dependency(tokens, roles, *, require_all=False, ...)` and
  `make_permission_dependency(tokens, permissions, *, require_all=True,
  ...)` — JWT-claim-based authorization.

### Added — Advanced primitives (Tier 4)

- `tempest_fastapi_sdk.WebhookSignatureVerifier(secret, *, algorithm,
  header_name, encoding, prefix)` — HMAC webhook signature
  verification with FastAPI dependency factory.
- `tempest_fastapi_sdk.RateLimitMiddleware(max_requests, window_seconds,
  key_func, exempt_paths)` — in-process sliding-window rate limiter.
- `build_pagination_link_header(base_url, *, page, size, pages,
  extra_params, page_param, size_param)` — RFC 8288 `Link` header
  builder for offset paginated responses.

### Docs

- README — full reorganization, every new primitive has a recipe with
  full code samples. New sections: Periodic tasks scheduler,
  Programmatic server entry point, JWT bearer / current-user / role
  dependencies, CEP, Cache decorator, Tool-spec router, Webhook
  signature verification, Pagination Link headers, Rate limit
  middleware, Utility helpers, Outbox dispatcher pattern, Migration
  guide 0.7 → 0.8.
- Tutorial sections 1–11 realigned to the canonical layout mandated by
  the SDK consumers' shared `CLAUDE.md` (single `main.py` one-liner,
  `src/server.py` exposing `run()`, `src/api/app.py` with `create_app()`,
  `src/db/repositories/` location, mandatory `src/controllers/`
  pass-through, `src/api/dependencies/` package).
- Reference section — method tables for `AsyncDatabaseManager`,
  `AsyncRedisManager`, `AsyncBrokerManager`, `AsyncTaskBrokerManager`
  and `AsyncTaskScheduler`.

### Dev

- Added `uvicorn>=0.30.0` to the dev dependency group so `run_server`
  tests can monkey-patch `uvicorn.run`.

## [0.7.3] — 2026-05-17

- Hardened request-ID middleware, SSE writer, web-push dispatcher and
  database manager lifecycle.

## [0.7.2] — 2026-05-16

- Release packaging fix only.

## [0.7.1] — 2026-05-16

### Changed

- Optional extras (`[auth]`, `[email]`, `[upload]`, `[cache]`,
  `[webpush]`, `[metrics]`, `[queue]`, `[tasks]`) are now lazy-loaded
  at first instantiation, so `import tempest_fastapi_sdk` works when
  only a subset of extras is installed.

## [0.7.0] — 2026-05-15

### Added

- `LogUtils`, `configure_logging`, `JSONFormatter`,
  `RequestIDMiddleware` and the `request_id_ctx` contextvar.
- `MetricsUtils` (CPU / memory / disk / GPU snapshots).
- `AsyncBrokerManager` (FastStream wrapper, `[queue]` extra) and
  `AsyncTaskBrokerManager` (TaskIQ wrapper, `[tasks]` extra).

## [0.6.0] — 2026-05-13

### Added

- SSE primitives (`EventStream`, `ServerSentEvent`, `sse_response`).
- Web Push dispatch (`WebPushDispatcher`, `WebPushSubscriptionSchema`,
  `WebPushPayloadSchema`, `WebPushGoneError`, `[webpush]` extra).

## [0.5.0] — 2026-05-10

### Added

- `AsyncRedisManager` (`[cache]` extra).
- CORS helpers (`apply_cors`, `CORSSettings`).
- Composable settings mixins (`ServerSettings`, `DatabaseSettings`,
  `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `CORSSettings`).

## [0.4.0] — 2026-05-07

### Added

- `make_health_router`, audit / soft-delete mixins, cursor pagination.

## [0.3.0] — 2026-05-04

### Added

- `BaseController` + `BaseService` generics, DI scaffolding, logging,
  `tempest_fastapi_sdk.testing` helpers.

## [0.2.0]

### Changed

- Drop Python 3.10 support; SDK now targets Python ≥ 3.11.

## [0.1.0]

- Initial public release.
