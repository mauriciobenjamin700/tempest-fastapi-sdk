# SHIPPED.md — o que o SDK já cobre

Inventário do que está entregue, para não re-planejar trabalho pronto.
Vive fora do `CLAUDE.md` de propósito: ele carrega as **regras**, e uma
regra que chega depois de 800 linhas de histórico não dispara. A cada
release, o que shippou é escrito aqui; as regras ficam lá, curtas e no
começo.

**Esta é a fonte da verdade sobre escopo entregue.** Se uma linha daqui
descreve como backlog algo que já existe (ou vice-versa), corrija na
mesma PR — nenhum teste lê prosa.

O histórico completo por versão, com Added/Changed/Fixed, está em
[`CHANGELOG.md`](CHANGELOG.md); este arquivo é o mapa por assunto.

## Roadmap — features we still owe

The SDK currently covers (Sep 2025+, post-v0.31.x):

- **Auth** — JWT/bearer/role/permission/X-Token deps (JWT deps
  read the token from header → cookie → query string via
  `query_param=`, for cookieless `EventSource`/SSE clients), full
  bundled flow (`UserAuthService` + `make_auth_router` covering
  signup/activate/login/password-reset; signup is closable since
  v0.272.0 via `AUTH_SIGNUP_ENABLED=false` or `allow_signup=False`,
  which keeps the route out of the app **and** out of the OpenAPI
  schema while leaving activation mounted), `BaseUserModel` +
  `BaseUserTokenModel` (nullable `payload` column carrying flow
  context), email change/re-verify/recovery
  (`request_email_change`/`confirm_email_change`,
  `request_email_verification`/`confirm_email_verification`,
  `request_email_recovery` — password + MFA-if-enrolled, opt-in
  `AUTH_EMAIL_RECOVERY_ENABLED`; old-email security notice via
  `AUTH_EMAIL_CHANGE_NOTIFY_OLD`; `EMAIL_CHANGE` token purpose;
  JSON + backend HTML pages + bilingual templates), OAuth2/OIDC
  providers (`GoogleOAuthClient`, `GitHubOAuthClient`,
  `OIDCProvider`) **plus the four bundled social-login routes since
  v0.273.0** (see the dedicated bullet below), CSRF middleware +
  `make_csrf_token_dependency`,
  opt-in DB-backed opaque refresh tokens
  (`BaseUserRefreshTokenModel`, `make_user_refresh_token_model`,
  `refresh_token_model=` on `UserAuthService`) with rotation,
  family-wide reuse detection (durable since v0.252.0: the detection path
  reads `family_id`, rolls the request's staged writes back itself, and only
  then issues the `UPDATE` and a `commit` that outlives the unwind — before,
  the revocation only reached `flush()` and went out with the session
  teardown, so detection without consequence looked like the theft had been
  handled) and `POST /auth/logout` (`LogoutSchema`). **WebAuthn / passkeys (v0.217.0, extra `[webauthn]` =
  `fido2`):** `WebAuthnService` (both ceremonies + list/delete),
  `BaseWebAuthnCredentialModel`/`make_web_authn_credential_model`,
  `Memory`/`RedisWebAuthnChallengeStore` (`GETDEL`, so single-use holds under
  concurrency), six routes via `make_auth_router(webauthn=)` gated by
  `AUTH_WEBAUTHN_ENABLED` (missing service raises at wiring, like
  `recovery_code_model`). Beyond what `fido2` verifies the SDK checks the
  **signature counter** (a stalled one is the spec's cloned-authenticator
  signal; authenticators reporting `0` are exempt — for them it carries no
  information), pops the challenge on use, keeps credential IDs unique per
  *table*, and answers `authenticate_begin` identically for an unknown email
  (otherwise it is an enumeration oracle). `AUTH_WEBAUTHN_RP_ID` is the
  security boundary — changing it invalidates every credential;
  `AUTH_WEBAUTHN_ALLOWED_ORIGINS` **replaces** the `fido2` default rule
  rather than extending it. Passkey login deliberately skips the MFA
  challenge. Tests drive real crypto through a software authenticator
  (`tests/auth/webauthn_authenticator.py`) — a mocked verifier would assert
  away exactly the properties worth testing. Recipe:
  `docs/recipes/webauthn.md`. **Token-type checks for foreign tokens
  (v0.258.0):** `token_type_allowed(..., strict=True, legacy_claims=("type",))`.
  The default — a payload without `typ` passes — is right for tokens this SDK
  minted and wrong for everyone else's: a service that already separated
  access from refresh under a claim of its own has no `typ` and none of the
  SDK's fallback markers, so every legacy token reached the accept path and a
  refresh token authorized any call site for the length of its TTL.
  `legacy_claims` names where else to read the type from (in order, only when
  `typ` is absent); `strict` refuses what stays unclassified, and does **not**
  disable the SDK's own `refresh: True` / `purpose: "mfa_pending"` markers.
- **Firebase ID token verification (v0.230.0, extra `[firebase]` =
  `firebase-admin`)** — for services whose clients sign in with Firebase and
  arrive with an ID token. `FirebaseAuth` owns the idempotent
  `get_app()`/`except ValueError` init (two instances with the same
  `app_name` share one app; distinct names talk to distinct projects),
  verifies off the event loop via `asyncio.to_thread`, and takes the
  credential from inline JSON, a service-account file, or the environment's
  default credential, in that order. `FirebaseIdentity` is a frozen dataclass
  (`uid`, `email`, `email_verified`, `phone_number`, `provider`, full
  `claims`); `FirebaseUserResolver[UserT]` is the seam from a uid to the
  project's user, since the SDK does not own that rule. Dependencies:
  `get_identity` / `get_uid` (strict) and `get_optional_identity` (soft,
  `None`). Every failure carries its own `code` —
  `FIREBASE_TOKEN_MISSING` / `_INVALID` / `_EXPIRED` / `_REVOKED`,
  `FIREBASE_UNAVAILABLE`, and `FIREBASE_USER_DISABLED` at **403** (the caller
  proved who they are), which is also the one failure the soft variant still
  raises. The `except` ordering is load-bearing: on `firebase-admin` 7.5.0
  `ExpiredIdTokenError` and `RevokedIdTokenError` are subclasses of
  `InvalidIdTokenError` (measured), so catching the parent first would
  collapse three codes into one — a parametrized test pins it. The extra is
  heavy (33 packages, 52 MB measured) and deliberately **out of `[all]`**;
  the import is lazy, so only construction needs it. Tests run offline: a
  locally generated RSA service account plus a patch on the real
  `verify_id_token`, and one test feeds a non-JWT to the genuine verifier,
  which rejects it structurally before any network call. Config:
  `FirebaseSettings` (`FIREBASE_PROJECT_ID`, `FIREBASE_CREDENTIALS_PATH`,
  `FIREBASE_CREDENTIALS_JSON`). Recipe: `docs/recipes/firebase-auth.md`.
- **Unified push, web + mobile (v0.231.0)** — `tempest_fastapi_sdk.push`.
  `PushDispatcher` is a one-method `Protocol` (the `UploadStorage` shape);
  `WebPushTransport` adapts the existing VAPID dispatcher and `FCMTransport`
  delivers to iOS/Android through `firebase_admin.messaging`, reusing the
  `[firebase]` extra and the service account `FirebaseAuth` loaded
  (`FCMTransport(auth=...)` via the new `FirebaseAuth.app`).
  `BaseDeviceTokenModel` holds browsers and phones in one table;
  `DeviceService` registers idempotently by token (a handset that changes
  hands moves to the new user), fans out concurrently, and prunes on one rule
  with two vocabularies — 404/410 on the web, `UnregisteredError` /
  `SenderIdMismatchError` on FCM. `PushFanoutResult` separates `delivered` /
  `pruned` / `failed` / `skipped`, and `skipped` never deletes: a web-only
  service keeps its iOS rows. `make_push_router` exposes register/unregister
  plus the VAPID key; `PushSettings` resolves the `enabled` collision between
  `WebPushSettings` and `FirebaseSettings`. Tokens are masked everywhere
  (`mask_push_token`). **`webpush` is untouched** — the VAPID code stays
  there (moving it would create an import cycle between the two packages) and
  `tests/webpush/` passes with zero edits. Two measured details:
  `Message.token` is deprecated in favour of `fid` on firebase-admin 7.5.0
  but they are different wire fields, so registration tokens stay in `token`;
  and FCM's `InvalidArgumentError` deliberately does **not** prune, because
  it covers malformed payloads too and pruning on it would wipe a fleet.
  Recipe: `docs/recipes/push.md`.
- **Stripe (v0.232.0, no extra)** —
  `tempest_fastapi_sdk.integrations.payment.stripe`. `StripeClient` over
  `HTTPClient` with a generic `StripeResource` (create/retrieve/update/delete/
  list + `auto_paginate`) across nine resources; `Idempotency-Key` on every
  write; `stripe_http_client` pinning `Stripe-Version`; zero- and
  three-decimal-aware money helpers; webhook verification over `t.body` with
  a replay window, rotation support and a `sign_payload` for tests;
  `StripeEvent` (265 types generated from the spec, drift-tested);
  `StripeError` carrying type/code/decline_code/param/request_id; thin
  response models with `extra="allow"`. Ships alongside two pieces of general
  value: **`form_encode`** (bracket-notation flattening, public) and the
  **generator reading the request body's media type** so form-only APIs emit
  `data=form_encode(payload)`. Also fixes a parser bug where note sinks were
  removed by equality, aborting any re-entrant spec.
  **The client is hand-written on purpose**: generating Stripe's spec yields
  3.3 MB of schemas costing 5.8 s / 492 MB RSS to import, and `/v1/prices`
  alone reaches 864 of 1440 schemas, so no small subset exists. Measurements
  and the fetch/regen steps live in `scripts/regen_stripe.py`. Recipe:
  `docs/recipes/stripe.md`.
- **Permission guards (v0.167.0)** — `@requires(*guards, user_param=None)`
  (`tempest_fastapi_sdk.authz`, re-exported at the root) runs plain
  `(user) -> user | None` guards before a function body, at any layer, sync or
  async; guards deny by raising an `AppException`, and a non-`None` return
  replaces the user the body sees (how `require_active` narrows `UserT | None`).
  The user param resolves from the annotations (`BaseModel`/`BaseUserModel`
  subclass), `user_param=` disambiguates. Misuse caught in three places:
  `TempestPermissionError` at import (no guard, wrong arity, async guard on a
  sync fn, unresolvable user param), `GuardContractWarning` at call time
  (foreign exception, `return False`), and
  `tempest permissions [--check|--strict|--path]` statically (`ast`) — errors
  `no-guards`/`user-param-missing`/`user-param-ambiguous`/`guard-arity`/
  `guard-async-in-sync`/`guard-returns-bool`/`guard-foreign-exception`,
  warnings `guard-never-denies`/`guard-missing-annotation`/`guard-return-type`/
  `guard-unresolved` (ambiguous or out-of-scope guard reported, never guessed).
  `openapi-errors` follows the guards, so their exceptions land in
  `error_responses(...)`. `declared_guards`/`guarded_user_param` introspect a
  route. **Metadata (v0.168.0):** a guard may declare a 2nd param
  `meta: dict[str, Any]` — `meta={...}` literals fixed at decoration plus
  `include_args=True` merging the call's args (user excluded, omitted params
  contribute defaults, `Depends(...)` markers dropped, `meta=` wins a name
  clash); one-param guards untouched; `TempestPermissionError` when `meta=`
  isn't a mapping or nothing consumes it; `guard_metadata(fn)` reads the
  literals; new static codes `meta-unused` (error), `guard-meta-missing`/
  `guard-meta-annotation`/`meta-key-collision` (warnings). Recipe:
  `docs/recipes/permission-guards.md`.
- **DB** — `AsyncDatabaseManager` (+ `is_memory_sqlite_url` /
  `shared_memory_url`, v0.252.0: a `:memory:` URL is rewritten to a
  shared-cache database with a normal pool, so overlapping sessions stop
  dying on `cannot start a transaction within a transaction` without giving
  up savepoint atomicity; `poolclass=` from the caller is never
  overridden), `BaseRepository[T]` with
  bulk ops (`bulk_create_values`, `bulk_upsert`, `bulk_update`,
  `add_all`, etc.), `AlembicHelper`, `BaseModel`, audit /
  soft-delete mixins, `reorder_base_columns_first` Alembic
  hook so generated migrations ship `id`/`is_active`/
  `created_at`/`updated_at` first. `alembic.ini` ships with
  `sqlalchemy.url` empty — URL resolves at runtime from env /
  settings / constructor. **First-boot bootstrap (v0.275.0):**
  `AlembicHelper.sync_schema()` tells an empty database, one that
  predates Alembic and one already migrated apart, reporting which path
  it took via `SchemaSyncOutcome`; `adopt()` stamps the **base** (never
  `head`), `base_revision()` and `has_existing_schema()` are the two
  questions behind it. Exists because `create_tables()` + `stamp("head")`
  is a plausible bootstrap that leaves an old schema with Alembic
  declaring itself up to date — recipe `docs/recipes/migrations.md`. **Transactions (v0.200.0):**
  `transaction(session)` / `savepoint(session)` (+ `repo.transaction()` /
  `.savepoint()`), depth counter in `session.info` so **every repository on
  that session joins the same block**; `commit()`/`flush()`/`rollback()` on
  the repository (`commit()` degrades to flush inside a block, `rollback()`
  inside one raises), `autocommit=False`. `enable_sqlite_savepoints` is
  applied to every SQLite engine `AsyncDatabaseManager` builds — without it
  `RELEASE SAVEPOINT` **commits** on pysqlite, so test and production
  disagreed about atomicity. **SQLite concurrency (v0.227.0):** every
  SQLite engine also opens in **WAL** with a 30 s busy timeout
  (`sqlite_wal=` / `sqlite_busy_timeout=`, `DATABASE_SQLITE_WAL` /
  `DATABASE_SQLITE_BUSY_TIMEOUT`, public `enable_sqlite_wal`), which is
  what lets a web process and a `taskiq worker` share one file — measured
  across two processes, the rollback journal fails the writer with
  `database is locked` where WAL commits at once. **Search (v0.200.0):**
  `search()` portable
  (escaped ILIKE, AND across words, OR across columns) +
  `full_text_search()` (`websearch_to_tsquery` + `ts_rank` + `setweight` on
  PG, falls back elsewhere; `supports_full_text` reports which);
  `search_condition()`/`full_text_condition()` return the clause and
  `where=` now takes `WhereClause = Q | ColumnElement[bool]`, so a search
  paginates through `paginate()`. Regconfig is inlined, not bound — asyncpg
  cannot infer `regconfig` for a placeholder. **Enum columns (v0.200.0):**
  `TempestEnum` via `BaseModel.type_annotation_map`, so `Mapped[MyEnum]`
  stores the **value**, gets a `CHECK` on SQLite and an `_enum`-suffixed
  native type on PG; `enum_column()` for `default`/`index`. **BREAKING** —
  the previous default stored `.name`. **Enum migrations (v0.200.0):**
  `op.replace_enum` (rename+recreate+cast inside the transaction, defaults
  read from `information_schema` and restored, `value_map=` for renames,
  reversible; SQLite rebuilds via `copy_from` because batch mode otherwise
  carries the stale CHECK), `sync_enum_types` autogenerate hook (compares
  `pg_enum` / the CHECK — autogenerate compares neither), and
  `render_enum_types` (without it **every migration touching an enum column
  failed on import**). Both wired into `env.py.template`. **Query plans
  (v0.200.0):** `explain_queries(session)` / `repo.explain()` → typed
  `ExplainReport`/`QueryPlan`; `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on
  PG, `EXPLAIN QUERY PLAN` on SQLite reported as `PLAN_ONLY` with `None`
  metrics; **writes are never re-analyzed**; plans fetched at driver level
  because `session.execute` applies the wrapped statement's result mapping.
  Recipes: `transactions.md`, `text-search.md`, `enum-columns.md`,
  `query-plans.md`. **Backup/restore:** `DatabaseBackup(url)` dumps and
  restores Postgres (custom + plain) and SQLite over one URL. **Database in a
  container (v0.258.0):** `docker_container=` runs `pg_dump`/`pg_restore`/
  `psql` **inside** that container, so the application image does not carry
  `postgresql-client` pinned to a compatible server version just for a
  nightly job. The dump crosses back over stdout and the restore streams in
  over stdin (nothing is `docker cp`-ed in, so no half-written temp file is
  left behind); `-h`/`-p` are dropped because the URL's host describes the
  route from *outside*; `PGPASSWORD` crosses by name (`-e PGPASSWORD`), never
  by value, so it stays off the container's command line; a non-zero exit
  removes the destination file so a truncated dump cannot pass for a good
  one. Measured across a real `postgres:16-alpine` under the `docker` mark
  (`make test-docker`), not asserted from argv. Recipe:
  `deploy-safety.md`.
- **Standardized exceptions** (`AppException` + subclasses) +
  `register_exception_handlers`. **Factories (v0.227.0):**
  `not_found_exception(code, subject=, field=, template=, ...)` and
  `conflict_exception(...)` build the per-domain 404/409 that every
  project hand-copies — the generated class accepts both
  `(identifier)` and `(message=...)`, which is what keeps a
  `BaseRepository` miss a 404 instead of a `TypeError`-driven 500.
  **OpenAPI error docs (v0.160.0):**
  `ErrorResponseSchema` (the `{detail, code, details}` envelope as a
  schema), `error_responses(*exc_classes)` (class-introspected
  `responses=`; groups by status, codes in an `examples` selector since
  OpenAPI allows one response object per status; `catalog=`/`locale=`/
  `descriptions=`), `@raises(...)` + `TempestAPIRouter` (drop-in
  `APIRouter` expanding the tag before route construction, so the model
  lands in `components.schemas`; explicit `responses=` wins per status) +
  `declared_raises`/`RaisesSpec`, `AppException.details_example`,
  `InheritedErrorCodeWarning` (subclass inheriting a **generic** SDK
  `code` — domain codes and `message_key` never warn), and
  `tempest openapi-errors [--check|--allow-unreachable|--path|--fix|--dry-run]`
  (static `ast` walk of router->controller->service->repository reading
  `raise` + `Raises:` sections; reports undocumented/unreachable, CI gate).
  **`--fix` (v0.166.0)** writes the missing declarations back: injects
  `responses=error_responses(...)` into the decorator + the needed imports, or
  appends to an existing `error_responses`/`@raises` call preserving order.
  AST-anchored edits, `ruff` normalized, requires a clean git tree (`--dry-run`
  is read-only and prints the diff). Adds only — `unreachable` is never
  removed, and an ambiguous exception import is reported as `unresolved`
  instead of guessed. Declaring
  `code` in the **class body** is now the documented pattern — the raise-site
  `code=` form is not introspectable.
- **Observability** — structured logging + per-level files +
  `/logs` endpoint, metrics (CPU/RAM/GPU/Disk), Prometheus
  `/metrics` endpoint + `PrometheusMiddleware`, request-id
  middleware with contextvar propagation, typed `HTTPClient`
  (httpx wrapper with retry/backoff/circuit-breaker /
  `X-Request-ID` propagation). **Rotation is on by default
  (v0.258.0):** the per-level handlers are `RotatingFileHandler` with
  `configure_logging(max_bytes=10_000_000, backup_count=5)` — ~60 MB per
  level, hard cap; `max_bytes=0` restores the plain `FileHandler` for a host
  where `logrotate` owns retention. This is the writer side of the ceiling
  the `/logs` router already had (`DEFAULT_MAX_RECORDS_PER_FILE = 20_000`);
  `/logs` reads the exact filenames, so rotated files are outside the
  window. **`LogUtils` takes `%`-style positionals and `stacklevel`
  (v0.258.0)** — a service that already logs in `%`-style adopts the facade
  without rewriting call sites, interpolation stays lazy, and `stacklevel=2`
  by default points `funcName`/`lineno` at the caller instead of the facade.
  **`MetricsUtils.disk_async()` + `strict=` (v0.258.0)** —
  `disks_async([path])` logs and skips a bad path (right for a plural list,
  silent absence for a single one, which a dashboard cannot tell from a disk
  nobody asked about); `disk_async` propagates, and `strict=True` carries
  that to the plural variants.
- **HTTP layer** — `RequestIDMiddleware`, `RateLimitMiddleware`,
  `IdempotencyMiddleware` (memory + Redis stores),
  `ResponseCacheMiddleware` (v0.159.0 — ETag/conditional-GET always on +
  opt-in server-side cache via `ResponseCacheStore`/`Memory`/`Redis`; respects
  `no-store`/`private`/`Set-Cookie`, `vary=` key),
  `BodySizeLimitMiddleware`, `AccessLogMiddleware` (v0.275.0 — one
  structured line per request, pure ASGI so the exception path stays
  intact; `redact` for secrets in the URL, prefix `exempt_paths` for SSE,
  `5xx` at `ERROR`; register it **before** `RequestIDMiddleware` or the
  lines carry no `request_id`), `HoneypotBanMiddleware` (v0.275.0 — 39
  curated scanner signatures in `DEFAULT_HONEYPOT_PATTERNS` matched
  against path **and** query, `BanStore`/`MemoryBanStore`/`RedisBanStore`,
  fail-open by default, client IP via `trusted_ip_header`),
  hardened static files, CORS,
  health + tool-spec routers. **Quotas (v0.216.0):** `RateLimitRule`
  (sliding window, or **token bucket** when `burst` is set),
  `StaticRateLimitPolicy`/`PlanRateLimitPolicy` (+ `plan_by_jwt_claim`/
  `plan_by_header`/`key_by_plan_principal`) and `MemoryQuotaStore`/
  `RedisQuotaStore` on `RateLimitMiddleware(policy=, quota_store=)`. The
  store decides **every** rule before writing any — a request barred by the
  daily ceiling must not burn a per-minute token, and only one Lua script
  makes that atomic across replicas. `PlanRateLimitPolicy` validates its
  mapping at construction (each defect surfaces as *unlimited* traffic) but
  an unknown plan *name* falls back to the default. Bucket key TTL is
  `capacity / rate`, not the window: window-derived, a 10/min bucket with
  `burst=1000` expired an hour before it filled and came back **full**.
  `RateLimit-*` headers describe the tightest rule; `RateLimit-Reset` is
  emitted only where it is known. `lupa` is a dev dep so `fakeredis` runs
  the real Lua.
- **Pagination** — offset + cursor.
- **Settings mixins** — every `*Settings` carries
  `title`/`description`/`examples` on every field.
- **System checks read the names the mixins declare (v0.272.0)** —
  `check_debug` / `check_database` resolve the debug flag as
  `SERVER_DEBUG` then `DEBUG`, so a service composing `ServerSettings`
  is actually covered; before, both asked for a bare `DEBUG` that no
  mixin defines, which made `check_debug` inert and inverted
  `check_database`. `check_secrets` gained `security.W004`, comparing
  each secret against `model_fields[name].default` — the SDK's own
  32-character `JWT_SECRET` placeholder cleared both the empty and the
  length branch, so the one value guaranteed to be wrong was the one the
  check approved.
- **Social login is inside `make_auth_router` (v0.273.0)** — issue #250.
  `AUTH_OAUTH_ENABLED=true` + `oauth_clients={"google": ...}` mounts
  `/auth/oauth/{provider}/login|callback` and
  `/auth/oauth/accounts[/unlink]`; measured, the full router goes from 25
  routes with no OAuth path to 29 with four. The callback issues through
  `issue_token_pair`, which is the point: the hand-rolled
  `encode({"sub": ...})` the old recipe taught carries
  `['exp', 'iat', 'sub']` and is accepted only by `token_type_allowed`'s
  legacy branch (`lax=True strict=False`), while the bundled token carries
  `['email', 'exp', 'iat', 'sub', 'typ']` and passes `strict=True` — so a
  service with no legacy tokens that correctly sets `strict=True` used to
  break every Google login, invisibly, because no SDK code ran on that path.
  Ships `OAuthSettings` (credentials + `google_kwargs`/`github_kwargs` +
  `oauth_redirect_uri`, which **derives** the callback URL instead of asking
  for it twice), `BaseUserOAuthAccountModel`/`make_user_oauth_account_model`
  (separate table, `UNIQUE (provider, subject)` and `UNIQUE (user_id,
  provider)` declared on the abstract base), `NameMixin`, `OAuthClient` as a
  public `Protocol`, and `login_with_oauth`/`list_oauth_accounts`/
  `unlink_oauth_account`. The three security rules that used to live only in
  the recipe's `!!! danger` blocks now have a test class each: the `state`
  compared with `hmac.compare_digest` against an `HttpOnly` cookie that binds
  the provider key, linking by email gated on
  `AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL` **and** `email_verified is True`
  (`None` is not a yes — GitHub's `GET /user` never says), and the identity
  keyed on `(provider, subject)`. Account creation defaults to inheriting
  `AUTH_SIGNUP_ENABLED`, so the v0.272.0 kill-switch closes this door too.
- **`generate_password` guarantees the policy by construction (v0.273.0)** —
  the OAuth callback creates accounts and `hashed_password` stays `NOT NULL`,
  so a password has to be minted. Drawing from a flat alphabet does not work:
  against `_enforce_password_policy` with complexity on and the default
  12-character floor, 200 000 samples each, `token_urlsafe(16)` was rejected
  53.01% of the time, `token_urlsafe(32)` 26.54% and `token_hex(32)` 100%.
  One character per required class, filled and shuffled with
  `secrets.SystemRandom`, is 0/2000 across every policy combination.
- **The front doors expose what the SDK already could do (v0.274.0)** —
  issues #252, #253, #254, #255, all found by one real migration onto
  v0.273.0 and all the same shape: the capability existed, the door
  everyone enters through did not offer it, so the consumer reimplemented
  the piece to reach it.
  **(#252)** `make_bearer_token_dependency` / `make_jwt_user_dependency` /
  `current_user_dependency` / `make_auth_router` now forward `strict` and
  `legacy_claims` to `token_type_allowed`. Measured with a legacy refresh
  token declaring `{"type": "refresh"}`: `HTTP 200` under the default,
  `401` with `strict=True` — so before this, every refresh token such a
  service ever issued authorized any route for its whole TTL, and the only
  way to fix it was to stop using the factories.
  **(#253)** `_issue_token` now spends the user's other unused tokens of the
  same purpose, so only the newest activation / reset / email-change link
  works (`AUTH_SINGLE_ACTIVE_TOKEN`, default `True`). It is what makes the
  victim's own correct reaction effective: measured,
  `AUTH_SINGLE_ACTIVE_TOKEN=False` leaves the attacker's link working
  **after** the victim reset the password themselves. Scoped to the same
  purpose and the same user; the row is marked `used_at`, not deleted.
  **(#254)** `require_active` / `require_admin` / `require_authenticated`
  take `exception=` and `unauthenticated=` (free functions and the
  `UserAuthService` mirrors), plus `make_flag_guard(attr, exception=...)`
  for the flags the SDK does not model. The generic `code="FORBIDDEN"` was
  what made them unadoptable by the service that followed the SDK's own
  advice and declared `USER_IS_NOT_ADMIN`.
  **(#255)** Ten typed OAuth refusals, each with its own `code`. The pair
  that mattered: `OAUTH_EMAIL_TAKEN` has a next step for the person,
  `OAUTH_EMAIL_UNVERIFIED` has none by design — and both arrived as the same
  `409 CONFLICT`. Every class subclasses what that site already raised, so
  `except ConflictException` keeps working.
- **SSE** — `EventStream` (bounded queue + `overflow` backpressure —
  `drop_oldest`/`drop_newest`/`block`, `dropped_events` counter,
  `max_queue=0` to disable), `ServerSentEvent`, `sse_response`
  (`on_disconnect=` cleanup), `EventStream.response`, and `SSEBroker`
  (per-channel fan-out; `SSEBroker.response(channel)` bundles
  register + response + unregister-on-disconnect; in-memory
  single-process, or multi-worker via an injected Redis pub/sub bridge
  — same call site).
- **Throttle** — `AttemptThrottle` (any `ThrottleBackend`, e.g.
  `redis.asyncio.Redis`; no in-memory backend bundled).
- **Base CRUD layers** — `BaseService[Repo, Resp, UpdateT]` and
  `BaseController[Service, Resp, UpdateT]` with
  `get_by_id`/`get_or_none`/`list`/`paginate`/`count`/`exists`/`update`/
  `delete`; `update` is partial-aware (PUT/PATCH) and `UpdateT` is an
  optional 3rd generic (defaults to `BaseSchema`, PEP 696).
- **Base enums** — `BaseStrEnum` / `BaseIntEnum` with
  `values`/`keys`/`choices`/`to_dict`/`from_value`/`has_value`/`has_key`.
- **Validated field types** — `tempest_fastapi_sdk.utils` Annotated
  Pydantic types: `PositiveIntField`/`NonNegativeIntField`/`CentsField`/
  `PortField`/`PositiveFloatField`/`NonNegativeFloatField`/`PercentField`/
  `RatioField`/`LatitudeField`/`LongitudeField`/`PriceField`/
  `NonEmptyStrField`/`SlugField`/`HexColorField`.
- **Runtime typing** — `strict_types` / `typed` / `require_annotations`
  decorators (over `pydantic.validate_call`); ruff `ANN` enabled in the
  SDK and `tempest new` templates (ANN401 off — `Any` is valid); a
  `[tool.tempest] typing_strictness` knob (`lenient`/`standard`/`strict`,
  `--strictness` override) layered onto `tempest lint`/`fix`/`type`/`check`.
  **`[tool.pydantic-mypy] init_typed = true` (v0.241.0)** in the SDK's own
  `pyproject.toml` and in the `tempest new` template: the plugin alone types
  every model constructor argument `Any`, so no schema construction was
  checked at all. Guard: `tests/test_pydantic_mypy_guard.py`. A service
  scaffolded before v0.241.0 needs the block pasted in by hand — mypy reads
  plugin config only from the config file, so `tempest check` cannot layer it
  the way it layers `--strict`.
- **Vision** (`[vision]` extra) — `tempest_fastapi_sdk.vision` wrapping
  `ort-vision-sdk`: lazy `Detector`/`Classifier`/`Segmenter` + prediction
  schemas + `to_detection_schemas`/`to_classification_schema`/
  `to_segmentation_schemas` mappers.
- **Modelops (v0.173.0)** — `tempest_fastapi_sdk.modelops`, two extras
  (`[modelops]` psutil+nvidia-ml-py, `[modelops-onnx]` onnx+onnxruntime); the
  module imports with neither. **Bench:** `benchmark` (any callable) + `benchmark_onnx`/
  `benchmark_torch`/`benchmark_models` — warm-up + N reps, median/IQR first,
  p95/p99, throughput, RSS peak/delta, GPU memory; symbolic dims resolved via
  `dynamic_dims=`/`input_shapes=` or raise (never guessed). **Energy:**
  `PowerSampler` protocol + `NvmlPowerSampler` (prefers the NVML total-energy
  counter, falls back to integrating power), `NvidiaSmiPowerSampler`,
  `RaplEnergySampler` (CPU package via powercap, package domains only,
  wraparound handled), `NullPowerSampler`; `resolve_power_sampler`/
  `resolve_cpu_energy_sampler`; every reading carries an `EnergySource` and a
  CPU run resolves no GPU sampler. **Ranking:** `composite_scores`
  (`DEFAULT_COST_WEIGHTS`, weights renormalized over measured dims twice),
  `pareto_points` (unmeasured axis skipped, cost-only frontier without
  `quality`), `rank` → `BenchmarkReport`. **Export:** `export_torch_to_onnx`,
  `export_onnx_to_ort` (file or dir, FIXED/RUNTIME, `target_platform`, type
  reduction, `.required_operators.config`), `optimize_onnx_graph`.
  **Quantization:** `quantize_onnx_dynamic`/`quantize_onnx_static` plus the
  transformers-export path `optimize_hf_onnx` (`model_type=` override,
  `file_name=` for multi-graph exports) / `quantize_hf_onnx` (arm64/avx2/
  avx512/avx512_vnni; `reduce_range` refused where the ISA cannot saturate),
  and `quantize_hf_bnb`. **No `optimum` dependency** — it capped
  `transformers<4.58` while only wrapping `onnxruntime`, so the tables it
  carried (`_OPTIMIZATION_SPECS`, `_ISA_QUANTIZATION_SPECS`,
  `_ORT_FUSION_MODEL_TYPES`) are ported constants pinned by tests, and
  producing the export is a documented `uvx optimum-cli` step. See the
  "Dependency policy" section in the global `CLAUDE.md`. **Static:**
  `analyze_onnx`/`analyze_ort`/`analyze_torch`/`analyze_model`. CLI
  `tempest model analyze|bench|optimize|quantize|export-ort|hardware`.
  **sklearn to the edge (v0.188.0, `[modelops-sklearn]` = skl2onnx):**
  `export_sklearn_to_onnx` (float32 + ZipMap off), `verify_sklearn_onnx`,
  `edge_bundle` (returns the *smallest* artifact — optimize/`.ort` grow tiny
  graphs), `uses_ml_domain` (int8 quantization does not apply to
  `ai.onnx.ml`). `hummingbird-ml` rejected: caps `onnx<=1.16.1`. **Binary-tree defect relocated (v0.201.0):** it was recorded as a `skl2onnx` conversion bug; holding `skl2onnx` 1.20.0 / `sklearn` 1.9.0 / `onnx` 1.22.0 fixed and moving only the runtime showed it is **`onnxruntime`** — error 1.0 vs `predict_proba` on 1.27.0, 9.5e-08 on 1.28.0. Floor moved to `onnxruntime>=1.28`; `BINARY_TREE_FIXED_IN_ONNXRUNTIME` still gates the export warning for a force-assembled environment.
  **Serving (v0.189.0):** `OnnxPredictor` (resolves input/output names,
  `DEFAULT_INTRA_OP_THREADS = 1` for constrained devices, reports the
  providers *actually* in use, `reload` builds the new session before
  dropping the old so a bad rollout degrades to the previous version),
  `make_prediction_router`, `RegistryModelSource` (fleet update over the
  existing `ArtifactRegistry`, one cached file per version).
  **Monitoring (v0.190.0):** `PredictionMonitor` + `baseline_from_samples`
  + `population_stability_index` — latency/volume, input drift (PSI vs a
  training-time baseline of bin edges only) and prediction distribution;
  constant memory (counters, never rows), per-window, `insufficient_data`
  below `MIN_ROWS_FOR_DRIFT`; PSI thresholds documented as a **convention,
  not a statistical test**. `PredictionMetrics` publishes it to Prometheus;
  `make_prediction_router(monitor=, metrics=)` mounts `GET /monitor`.
  **Edge package (v0.191.0):** `edge_pipeline` (export → verify → drift
  baseline → `manifest.json` + gzip, in one shippable directory),
  `load_edge_package` (predictor + monitor wired, SHA-256 checked),
  `read_manifest`; `EdgeManifest` is a **cross-language contract** with a
  pinned `schema_version` — `tempest-react-sdk/tabular` reads the same file
  in the browser. Measured and documented: graph optimisation is a no-op on
  `ai.onnx.ml`, `.ort` more than doubles the file, int8 does not apply, gzip
  reaches 10-13%; and `DEFAULT_INTRA_OP_THREADS = 1` was **re-justified** —
  the old "coordination costs more than parallelism" claim was not
  measurable; the real reason is oversubscription across concurrent
  requests. Recipe: `docs/recipes/modelops.md`.
- **GenAI self-hosted** (`[genai]` extra: transformers+torch+accelerate;
  `[genai-quant]` = bitsandbytes) — `tempest_fastapi_sdk.genai`, delivered
  in slices. **Shipped (v0.96):** hardware capacity check — `probe_hardware`
  → `HardwareInfo` (CPU/RAM/CUDA-VRAM/MPS/disk, degrades without
  psutil/torch), `can_run`/`recommend` → `CapacityReport` (fits? device,
  estimate vs available, suggestion to quantize/offload), `estimate_model_bytes`/
  `bytes_per_param`/`fetch_num_params` (Hub metadata, no weight download),
  `ModelDtype`. Capacity fns import WITHOUT the extra. **Shipped (v0.98) —
  `TextGenerator`**: local causal LM (`generate`/`chat`/`stream` async via
  to_thread), auto device/dtype, int8/int4 quant (BitsAndBytesConfig), lazy
  `load` + `unload`/`unload_if_idle` (+ `idle_unload_seconds`),
  `resolve_device`/`auto_dtype_name`; torch/transformers lazy. **Shipped
  (v0.99) — embeddings + scale:** `Embedder` (local text→vectors, mean
  pooling, batched, optional `EmbeddingCache`/`InMemoryEmbeddingCache`),
  `BatchScheduler` (coalesce concurrent calls into one batch — pure
  asyncio, no extra; items already queued are taken without a timer since
  v0.252.0, so a busy loop no longer splits a full batch), `ModelRegistry` (LRU model sharing with unload).
  Classes-only (no bundled router). Submodule import like
  queue/tasks/vision. **Refinements (v0.100):** `WebSearch.retrieve`
  (one-shot search→extract→context), `ContentExtractor.extract_many`
  (bounded concurrent), generic `chunk_text` (any string), `Embedder(
  normalize=True)` + `cosine_similarity` (semantic search). **Corpus RAG
  (v0.101):** `VectorStore` Protocol + `InMemoryVectorStore` +
  `PgVectorStore` (pgvector, reuses the service Postgres) + `Retriever`
  (`index`/`search`/`retrieve` tying `Embedder` → store → `build_context`);
  `Chunk.score`. **Chroma + chat memory (v0.108, `[genai-chroma]` =
  chromadb):** `ChromaVectorStore` (ephemeral / persistent / injected client)
  and `ChatMemory` — recency-aware per-user long-term memory over a Chroma
  collection, `index()` embeds + upserts and evicts the oldest past a soft
  per-user quota, `recall()` returns scored `MemoryHit`s over any
  `SupportsEmbed`. Uses `PersistentClient` (embedded, no HTTP server), so the
  `chromadb` server advisory PYSEC-2026-311 is not reachable through the SDK.
  **Audio (v0.102, `[genai-audio]` = faster-whisper + coqui-tts + the Coqui
  runtime — torch, torchaudio, torchcodec, `transformers<5`, declared since
  v0.252.0 because coqui-tts hides all four behind its own extras; XTTS v2 is
  licence-gated and needs `COQUI_TOS_AGREED=1` on a server):** `tempest_fastapi_sdk.genai.audio` — `SpeechToText`
  (faster-whisper transcribe → `Transcription`) + `TextToSpeech` (Coqui TTS
  synthesize → WAV bytes, XTTS voice cloning via `speaker_wav`), lazy +
  to_thread + semaphore, auto device/compute. **Language presets (v0.103):**
  `Language` enum (PT_BR/EN_US) + `preset_for`/`TextToSpeech.for_language`
  — resolves Whisper code + default TTS model per language;
  `transcribe`/`synthesize` accept the enum, a raw code, or None. **Shipped (v0.97) — RAG context**
  (`tempest_fastapi_sdk.genai.rag`, `[genai-rag]` extra = httpx +
  trafilatura + pymupdf): `WebSearchBackend` Protocol + `SearxngBackend`
  (SearXNG JSON API, leviathan pattern) + `WebSearch`; `ContentExtractor`
  (trafilatura, failures never raise); `PdfReader` (PyMuPDF detailed
  extraction → `Document`/`Chunk`, `read`/`chunks` with overlap);
  `build_context(question, sources)` → prompt block mixing web +
  PDF. All import lazily.
- **GenAI self-hosted roadmap (v0.139–0.154).** Shipped on top of the
  above: seed/stop honored on the transformers path (v0.139); retry/
  backoff/circuit-breaker on Ollama + SearXNG via `HTTPClient` (+ its
  `stream()`/`transport=`) (v0.140); **tool calling** on `TextGenerator`
  (`chat_with_tools`, chat-template `tools=`) closing the pipeline gap
  (v0.141); **structured output** `generate_structured` + `parse_structured`
  (Ollama `format=`, transformers `lm-format-enforcer` via
  `[genai-structured]`) (v0.142) + **`chat_structured(messages, schema)`** on `OllamaGenerator`
  (v0.225.0), which keeps the instruction in a `system` turn separate from
  the document in `user` — measured to matter for schema adherence — and
  posts `format` at the top level of `/api/chat`, where the daemon reads
  it (a schema passed as a keyword to `chat()` lands in `options` and is
  ignored silently, so an explicit `format=` now raises);
  **backend parity (v0.242.0):** the same `chat_structured(messages, schema)`
  on `TextGenerator`, plus the runtime-checkable `StructuredTextBackend`
  protocol both satisfy, so a document-reading service swaps daemon for
  local weights without touching the call site; local generation takes a
  `stop_event` (a transformers stopping criterion, asked every token)
  because cancelling the coroutine that awaits a thread stops nothing —
  measured both ways, and `run_cancellable(stop_event=...)` sets it;
  **`VisionTextGenerator`** local VLM
  (`[genai-vlm]`) (v0.143); RAG **`Reranker`** cross-encoder (v0.144) +
  **`HybridRetriever`** BM25+dense RRF (`reciprocal_rank_fusion`, `rank-bm25`
  in `[genai-rag]`) + `SupportsRetrieve` (v0.145/0.154); **`OnnxEmbedder`**
  torch-free embeddings (`[genai-onnx]`) (v0.146); prompt→completion
  **generation cache** (`InMemory`/`RedisGenerationCache`, deterministic-only)
  (v0.147); **token/context** (`count_tokens`/`truncate_messages`) (v0.148);
  **`make_vision_router`** (v0.149); **`GenAIMetrics`** Prometheus (v0.150);
  content **moderation** (`RuleModerator`/`ClassifierModerator`) (v0.151);
  and integration — `AIChatPipeline` moderation + context truncation (v0.152),
  metrics+cache on `TextGenerator`/`Embedder` (v0.153); **OTel spans**
  (`genai_span`) — ambient tracing on `generate`/`chat`/`embed`/RAG reusing the
  `setup_tracing` `TracerProvider` (GenAI semconv; no-op without `[otel]`)
  (v0.156). The OpenAI-compatible
  client was **deliberately skipped** (self-hosted-only). Test tiers
  (unit/`@model`/`@gpu`) + plans live under `planning/genai/`. **Fix:** `httpx`
  + `email-validator` are base deps so a minimal/`[genai]` install imports
  (v0.151.1).
- **Reconhecimento facial (v0.223.0, extra `[faces]`)** —
  `tempest_fastapi_sdk.faces`: `FaceRecognizer` (`detect` sem biometria /
  `recognize` / `embed_face` que recusa entrada ruim), `compare_faces`,
  `FaceDetector` (SCRFD decodificado à mão + NMS), `align_face` +
  `similarity_transform` (Umeyama; `ARCFACE_TEMPLATE` portado e pinado),
  packs `buffalo_s` 16 MB (padrão, medido) e `buffalo_l` 191 MB.
  **Sem opencv, sem torch, sem biblioteca de sistema.** Folga medida: mesma
  pessoa 0,877–0,962 contra máx 0,180 entre diferentes — limiar 0,45 no meio de
  ~0,7, ao contrário da diarização, onde é apertado. `insightface` **medido e
  rejeitado**: 558 MB em 24 pacotes e `opencv-python` ligando contra 5 libs GL.
  Corrigido: recorte apertado 112×112 dava 0 faces (moldura de 20% → 1), e face
  abaixo de 40 px volta com `embedding` vazio em vez de vetor da ampliação.
  Vetor de rosto é **dado biométrico sensível** — a camada de cadastro
  persistente fica para entrega separada. Receita: `docs/recipes/faces.md`.

> A pilha de voz foi construída em quatro incrementos numerados 0.219.0-0.222.0,
> mas **nenhum deles foi ao PyPI**: a corrida de contagem por requisição nasceu
> na primeira, então publicá-los em ordem entregaria o defeito quatro vezes
> antes da correção. Tudo saiu na **v0.223.0**, junto com o reconhecimento
> facial. Os números intermediários não são instaláveis.

- **Contagem automática de falantes (v0.223.0)** — `num_speakers="auto"` é o
  padrão do `SpeakerDiarizer`: `estimate_speaker_count` lê a contagem do maior
  salto espectral na matriz de afinidade dos turnos, e uma segunda passada
  re-agrupa para ela. **12/12 exato** num banco de 12 gravações com verdade por
  construção, contra 8/10 do melhor limiar fixo. `affinity_report` expõe
  autovalores, saltos e margem. **Veto de monólogo** (`SOLO_COHESION_P10`): a
  busca espectral sempre acha divisão, e um ditado real de 6 turnos voltava
  como 2 falantes — percentil 10 da similaridade foi 0,490–0,667 para 1 voz e
  −0,080–0,166 para várias, e o corte fica no meio. É escala do modelo
  embarcado, não constante universal. Descartei um banco anterior por ser
  circular (a verdade vinha do próprio diarizador).
- **Contagem de falantes é argumento por chamada (v0.223.0)** —
  `SpeakerDiarizer.diarize(audio, num_speakers=...)`. Antes o transcriber
  escrevia no diarizador compartilhado antes de usá-lo, e duas requisições
  simultâneas liam a última escrita: quem pediu 2 falantes recebia 5, sem erro.
  Reproduzido (`[5, 5]` onde o esperado era `[2, 5]`) e fixado em
  `tests/genai/audio/test_concurrency.py`. O `unload()` que a mesma linha
  chamava saiu — derrubava ~46 MB debaixo de quem estava em voo — e o par
  `set_config`/`process` do engine ficou sob lock, porque a contagem vive na
  config dele.
- **Superfície de voz (v0.223.0)** — `make_voice_router` (`POST
  /voice/transcribe` + `POST`/`GET`/`DELETE` `/voice/profiles`; listar e apagar
  são direito da pessoa, e a listagem **não** devolve o embedding) e
  `tempest voice models|diarize|transcribe` (`diarize` não carrega Whisper).
  `profiles=` sem `current_user_id=` levanta na montagem — id vindo do corpo
  deixaria qualquer um gravar biometria em conta alheia. Upload limitado
  (25 MiB) **durante** a leitura, não depois.
- **Identificação de voz (v0.223.0)** — `VoiceEmbedder`,
  `VoiceProfileService` (cadastrar/identificar/apagar), `BaseVoiceProfileModel`
  + `make_voice_profile_model`, e `ConversationTranscriber.transcribe(
  identify_with=, session=, user_ids=)` que nomeia a conversa inteira numa
  chamada. Identifica **uma vez por cluster**, no turno mais longo dele.
  Medido com voz real: cadastrando de um turno e identificando **outro** turno
  da mesma pessoa, 0,687 e 0,734; falante não cadastrado volta `None`.
  **Impressão vocal é dado biométrico** (LGPD Art. 5º, II): `consent_reference`
  é obrigatório e em branco levanta `ConsentRequired`, o consentimento fica na
  mesma linha do vetor, o áudio **nunca** é gravado, e `forget_user()` é método
  — não exemplo — porque apagar é direito incondicional (Art. 18, VI). Perfil
  de outro modelo nunca é comparado (`model_name` por linha, `stale_profiles()`
  acha quem recadastrar); cadastro abaixo de 3 s é recusado.
- **Diarização (v0.223.0, `[genai-diarization]` = sherpa-onnx)** —
  `tempest_fastapi_sdk.genai.audio`: `SpeakerDiarizer` (quem falou quando),
  `ConversationTranscriber` (junta com o `SpeechToText` existente por
  sobreposição de tempo), `DiarizedTranscription`/`SpeakerTurn` com
  `transcript()` e `by_speaker()`, `ensure_models()` (46 MB, fora do wheel,
  honra `TEMPEST_VOICE_MODEL_DIR`). **sherpa-onnx e não pyannote**: 1
  dependência contra 21 (torch/lightning/matplotlib/otel/SDK pago) e modelos
  abertos contra pipeline gated no HuggingFace; RTF 0,125 em CPU. Transcreve a
  gravação **uma vez** e atribui depois — trecho que atravessa troca de falante
  cai em quem tem a maior parte, e fala fora de todo turno volta com
  `speaker = -1` em vez de sumir. Limiar padrão **0,9** (não o 0,5 do
  sherpa-onnx) por varredura em 3 gravações onde nenhum valor acerta as três;
  passar `num_speakers` é a diferença entre certo e errado. Índices de falante
  renumerados densos — o agrupamento devolvia `0,1,2,4,7,8,9`.
  `sherpa-onnx-core` é declarado explicitamente: o sdist não declara a
  dependência que os wheels declaram, e o uv lockava do sdist, deixando o
  extra quebrado no primeiro uso.
- **GenAI weight lifecycle (v0.176.0)** — `tempest_fastapi_sdk.genai.hub`,
  extra `[genai-hub]` (`huggingface-hub` alone; contained in `[genai]`, and
  the module imports with neither). `ModelRef` carries the weight identity
  (id/revision/cache/token/`local_files_only`/`trust_remote_code`) and emits
  **only non-default** kwargs, so an unpinned call stays byte-identical and the
  same dict works with narrower loaders. `resolve_revision` (branch → commit
  sha, `None` when unreachable — never raises), `model_disk_bytes` (Hub
  metadata, no download), `download_model` (`allow`/`ignore` globs →
  `ModelSnapshot`; refuses with `OSError` when free space < estimate x1.1),
  `list_cached_models`/`cache_size_bytes`/`remove_cached_model` (by sha or ref
  name, `dry_run`, `0` for absent = no-op). All 5 transformers loaders
  (`TextGenerator`/`Embedder`/`VisionTextGenerator`/`ClassifierModerator`/
  `Reranker`) take `revision=`/`local_files_only=`/`trust_remote_code=`;
  `SpeechToText` maps onto faster-whisper's `download_root`/`use_auth_token`
  and has no `trust_remote_code` (CTranslate2 runs no repo Python);
  `OnnxEmbedder` pins its tokenizer only (`tokenizer_revision=`/`hf_token=`).
  CLI `tempest model pull|cache-list|cache-rm`. Recipe:
  `docs/recipes/model-weights.md`.
- **Image generation (v0.177.0)** — `tempest_fastapi_sdk.genai.image`, extra
  `[genai-image]` (`diffusers`+`pillow`; module imports without it). Closes
  the last missing generative modality. `ImageGenerator` mirrors
  `TextGenerator` (device/dtype resolution, lazy load, `unload_if_idle`, the
  Hub pinning keywords): `generate(prompt, config=)` → `list[GeneratedImage]`
  carrying the **seed** (drawn when unset, so every result is reproducible),
  `edit(prompt, image, strength=)` via `AutoPipelineForImage2Image.from_pipe`
  (reuses the loaded UNet/VAE/text encoders — no second copy of a ~7 GB
  pipeline), `.pipeline` escape hatch for scheduler/LoRA.
  `ImageGenerationConfig` renames to the diffusers spellings
  (`steps`→`num_inference_steps`, `num_images`→`num_images_per_prompt`) and
  forwards only what is set — turbo wants 4 steps/guidance 0.0, full SDXL
  wants 30/7.5. `max_concurrent=1` by default (one diffusion call saturates
  the GPU; two double peak VRAM). `make_genai_router(image_generator=)` →
  `POST /image` returning the encoded bytes + `X-Image-Seed`. **Dependency
  note:** `diffusers` declares `httpx<1.0.0` + `huggingface-hub<2.0` —
  inert today and confined to the optional extra, accepted because
  schedulers/pipelines/VAE are real engineering, not a preset table.
  **v0.178.0** added `pipeline_kwargs=` (extra `from_pretrained` keywords,
  applied last so they beat the computed ones) after real-model validation
  showed the load itself takes decisions `.pipeline` cannot express —
  `safety_checker=None` (SD 1.x/2.x bundle an extra CLIP), `variant="fp16"`,
  `use_safetensors=True`. Recipe: `docs/recipes/image-generation.md`.
- **Runtime model inventory (v0.179.0)** — `tempest_fastapi_sdk.genai.inventory`
  (pure Python, no extra). `describe_model(handle, key=)` → `LoadedModel`
  reading **attributes only** (never triggers a load; unknown fields are
  `None`, never guessed; a handle exposing only `is_loaded` still appears);
  `LoadedModel.idle_past_threshold` is `False` whenever any input is unknown.
  `runtime_report(models, probe=)` → `ModelRuntimeReport` sorted **loaded
  first, longest-idle first** next to `probe_hardware()`. `ModelRegistry`
  gained `.inventory()` / `.items()` / `.unload_idle()` — the last frees
  weights but **keeps the entry** (it reloads on next use), unlike
  `evict()`. `GET /models` via `make_genai_router(models=)`.
  `ClassifierModerator` / `SpeechToText` / `OnnxEmbedder` gained the uniform
  `seconds_idle` / `unload_if_idle` / `unload` they lacked. **Fix:** the
  router's guard tested truthiness, so an empty `ModelRegistry` (`__len__`
  == 0 — the startup state) read as "nothing injected"; it now tests
  `is None`. **v0.180.0** wired the inventory into the two surfaces someone
  already watches: `GenAIMetrics.observe_inventory(report)` publishes
  `genai_models_loaded{kind,device}` / `genai_models_known` /
  `genai_gpu_vram_free_bytes{index}` (labelled series **cleared per call** —
  a gauge is a snapshot, so an unloaded model must stop being reported), and
  `make_model_cards(models, include_vram=)` (`genai/admin.py`) returns
  `AdminSite(dashboard_cards=)` entries reading the handles at **render**
  time. Also closed the `docs/reference.md` hole: the top-level `genai`
  surface + `genai.rag` + `genai.audio` now render (269 symbols), where
  before only the three new submodules did.
- **Agents (v0.181.0)** — `tempest_fastapi_sdk.agents`, submodule import, **no
  extra**. Goal in, traced run out — the split from `AIChatPipeline` (which
  answers a chat *turn*). `Agent.run/stream` → `AgentRun` (output + `steps` +
  `artifacts` + `stop_reason`); `AgentBudget` bounds steps/wall-clock/tool
  calls and `StopReason` names which fired (`succeeded` is `COMPLETED` only —
  a truncated run still carries text). **Three deliberate properties:** a
  raising tool becomes an observation fed back to the model, never a crashed
  run; every ceiling is enforced *and reported* (`max_seconds` defaults to 120
  because steps alone do not bound a hung call); binary results never enter
  the prompt (`ToolResult` = text for the model + `AgentArtifact` bytes for
  the caller). Handlers take **two** positionals `(arguments, context)` — the
  `AgentContext` holds artifacts **by name**, which is what chains multimodal
  work (draw `bike.png`, then have the VLM describe `bike.png`, no disk, no
  base64); `require_artifact` lists what exists so the model can self-correct.
  Builtin tools over the local models: `generate_image_tool`,
  `describe_image_tool`, `transcribe_audio_tool`, `speak_tool`,
  `retrieve_tool`, `web_search_tool`, `save_artifact_tool`, `text_tool`;
  `AgentTool.from_tool` adapts a pipeline `Tool`. Persistence **opt-in**:
  none → `InMemoryAgentRunSink` (bounded — runs carry artifacts) →
  `BaseAgentRunModel`/`make_agent_run_model`/`DbAgentRunSink` (keeps the trace
  and artifact *names*, not bytes). `make_agent_router` (`POST /run`,
  `/run/stream` SSE + `done`, `GET /runs`, artifact download with a real media
  type). Run state lives in a local `_RunState`, never on `self` — two
  concurrent runs on one agent must not mix. Recipe:
  `docs/recipes/agents.md`.
- **Multi-agent + loops (v0.182.0)** — delegation with **no team object**: an
  agent already picks tools by name, so `agent_tool(agent)` /
  `team_tools({agent: description})` make a specialist *a tool*. Three guards
  a plain tool does not need: the **clock is inherited**
  (`AgentContext.deadline` is absolute; each run takes the **earlier** of its
  own budget and the inherited one, so a child never outlives the request its
  parent holds open), **depth is bounded** (`max_depth`, default 3, turns
  A->B->A into a readable refusal), and the **child's work returns**
  (artifacts namespaced `<agent>/<name>`; a truncated child comes back as
  `[stopped: …] …` instead of passing partial work as complete). New
  `StepKind.AGENT` + `AgentStep.children`/`.agent`/`.total_steps` — a
  delegation can cost as much as a whole run and must not read as a function
  call. **Loops:** `run_until(agent, goal, until=)` repeats until a predicate
  *you* wrote accepts (real Python — parse it, import it, call it — a far
  harder gate than asking the model); `refine(worker, critic, goal)` is
  generate-critique-revise, critic approves with the exact token `APPROVED`
  and never rewrites. `LoopResult`/`LoopIteration` keep every round;
  `accepted=False` means nothing passed. **Fixed here:** the effective
  deadline was written to the run state but not back to the `AgentContext`,
  so delegation handed the child `None` and it ran to its own budget.
- **Planilhas (v0.229.0, `[spreadsheet]` extra = openpyxl)** —
  `tempest_fastapi_sdk.spreadsheet`. `SheetWriter` segura o cursor de linha
  (`title_block`/`header_row`/`group_row`/`write_row`/`total_row`/
  `blank_rows`, todos devolvendo a próxima linha livre; `apply_widths`,
  `freeze_below`); `Column` declara título, largura, máscara e alinhamento
  **uma vez**, então o formato não diverge entre a primeira linha e a
  milésima. As máscaras `BR_*` embutem o código de idioma `416`
  (`[$R$-416]`), porque `#,##0.00` puro é resolvido com o locale de **quem
  abre** — a mesma planilha lê `1.234,56` aqui e `1,234.56` num en-US.
  `SheetStyle` é **dado puro** (hex + inteiros, zero objeto openpyxl), então
  o tema é definível e testável sem o extra. `new_workbook` remove a aba
  `Sheet` fantasma; `workbook_to_bytes` entrega bytes (sem arquivo temporário
  e sem corrida entre duas requisições). O gerador de um documento
  específico — que abas, que linhas, que regras — continua sendo do serviço;
  aqui está só a camada que todo gerador reescrevia.
- **Leitura de PDF (v0.229.0, `[pdf-read]` extra = pypdf)** —
  `extract_pdf_text` / `extract_pdf_pages` em `tempest_fastapi_sdk.pdf`, o
  inverso do renderer e o primeiro passo de todo pipeline "entrega o
  documento pro modelo". **Camada de texto apenas, sem OCR**: um PDF
  escaneado devolve `""` em vez de documento em branco, porque prompt vazio
  é como um modelo inventa resposta confiante sobre página que ninguém leu.
  Fronteira de página sobrevive como marcador (parametrizável). `max_chars` é
  **teto duro** desde a v0.252.0 — o aviso entra no orçamento, não por cima
  dele — e o corte prefere a última página completa; quando nem a primeira
  cabe, corta dentro dela e anuncia `TRUNCATED MID-PAGE`, porque devolver só
  o aviso era entregar prompt vazio ao modelo. Aviso que não deixa **um
  terço** do orçamento para texto é omitido: anotação não substitui payload.
  Extra separado do `[pdf]` de propósito: renderizar puxa WeasyPrint mais
  Pango e fontconfig do sistema, e quem só lê não deve carregar nada disso.
- **PDF (v0.218.0, `[pdf]` extra = weasyprint + jinja2)** —
  `tempest_fastapi_sdk.pdf`, submodule import. `PdfRenderer` (HTML string /
  template / typed document; `asyncio.to_thread` + semaphore since layout is
  CPU-bound), five bundled documents each with a Pydantic schema
  (`Receipt`/`Quote`/`Report`/`Contract`/`VoucherDocument` + `Party`/
  `Branding`/`LineItem`/`Clause`/`Signatory`/`ReportColumn`), BR filters
  (`brl`/`extenso`/`data`/`data_extenso`/`doc`/`qtd` over `format_cents`/
  `valor_por_extenso`/…), `make_pdf_router`, `tempest pdf list|schema|render`
  (`--html` skips layout for browser preview). Project `template_dir` shadows
  the bundled templates file by file, like `EmailUtils`.
  **WeasyPrint** for CSS Paged Media (repeating header, `página X de Y`);
  browser engines cost 150 MB in the image and `xhtml2pdf` would pin
  `reportlab<5` on every consumer. **Totals are computed from the items**,
  never accepted. **Reproducible only with `SOURCE_DATE_EPOCH`** — the PDF carries
  no clock, but the embedded font subset stamps its `head` table, so runs
  seconds apart differ (three hashes across three container runs). The first
  version claimed determinism and "proved" it with two renders in **one
  process**, which match trivially; the test now crosses a process boundary.
  Even pinned, bytes depend on font + WeasyPrint versions, so a hash does not
  travel between images. **`AssetPolicy` denies every fetch by default** (`data:` excepted —
  it fetches nothing); allowed dirs are checked on the *resolved* path so `../`
  and symlinks do not escape, and `_fail_on_errors` aborts the render at the
  first refusal rather than shipping an invoice with a hole where the logo was.
  `logo_data_uri` accepts only `data:`; `accent_color`/`page_size`/`margin` are
  shape-constrained because they land **inside the stylesheet**.
  **Fixed while building:** the report's grand total was a `<tfoot>`
  (`table-footer-group` → repeats), printing at the foot of page 2 above rows
  that summed to something else; and a `ValidationError` raised inside the
  router body escaped as **500** (FastAPI converts only the models it declared).
  Needs Pango + fontconfig + a font at runtime — `tempest generate
  --dockerfile` emits the apt line when the project pins the extra. Recipe:
  `docs/recipes/pdf.md`.
- **SSR** (`[ssr]` extra) — `tempest_fastapi_sdk.ssr`: typed Python
  pages rendered to HTML via `tempestweb`'s `render_to_html` /
  `render_document`. `Page` (typed `Component` base — `body()` +
  overridable `shell()` layout), `html_response` (widget tree →
  `HTMLResponse`, full document or bare HTMX fragment), and
  `make_htmx_router` (serves a wheel-bundled HTMX 2.x locally, no CDN).
  `tempestweb` imported lazily so `import tempest_fastapi_sdk` never
  needs the extra.
- **Assinatura de webhook do Mercado Pago portada do provedor (v0.251.0)** —
  `build_manifest`, `SignatureHeader`, `DEFAULT_SIGNATURE_VERSIONS`, mais
  `versions=` e `tolerance_seconds=` em `verify_signature`. O manifesto omite
  par ausente (era template fixo, e por isso **nenhuma** entrega sem `data.id`
  verificava), chave de header é case-insensitive, valor só-espaço é ausente,
  `ts` não-numérico é rejeitado. Portado de `mercadopago/sdk-nodejs` commit
  `99857f33`, com digests conferidos contra `openssl`. Falta uma entrega real.
  Receita: `docs/recipes/mercado-pago.md`.
- **Guard de tipo nos exemplos da doc (v0.257.0)** —
  `tests/test_docs_type_guard.py` escreve os 1999 blocos parseáveis dos 226
  arquivos markdown (as duas línguas mais o `README.md`) como módulo e roda mypy com a config deste repo, lendo quatro códigos:
  `arg-type`, `call-arg`, `name-defined`, `used-before-def`. Fecha o ponto cego
  que `test_docs_examples_compile` e `test_docs_method_guard` declaravam no
  próprio docstring — tipo de argumento. Primeira execução: 162 achados, dos
  quais 89 eram `NameError` na colagem (placeholder escrito **depois** da linha
  que o usa) e quatro eram anotação do SDK recusando o argumento que a própria
  receita mandava passar (`on_disconnect=task.cancel`, os três protocolos de
  Redis, `require_authenticated` de identidade não-`BaseUserModel`). Ignora
  `attr-defined` de propósito: página que excerta corpo de classe sem
  `__init__` é legítima. ~40s com cache frio, <1s quente.
- **Guard de versão do `uv.lock` (v0.251.0)** —
  `tests/test_lock_version_guard.py` lê `git show HEAD:uv.lock`, porque
  `uv run` conserta o arquivo em disco antes de qualquer teste ler; o workflow
  de release compara a tag com as três versões. Fecha o drift que shippou em
  v0.236.0, v0.237.0, v0.238.0 e v0.247.0.
- **QR do Pix tipado (v0.250.0)** — `PixPayment`, `PixPointOfInteraction`,
  `PixTransactionData`, `create_pix_payment`, `get_pix_payment`,
  `parse_pix_payment`. Fecha o buraco que a v0.249.0 apenas documentou: a
  especificação não declara `point_of_interaction` no pagamento e o
  `BaseSchema` é `extra="ignore"`, então o `Payment` gerado descartava o QR na
  validação, em silêncio. Campos portados do SDK Node oficial (commit
  `c2d3c6ae`) e fixados por teste; `financial_institution` aceita `int` ou
  `str` por divergência medida entre as fontes; propriedades `qr_code` /
  `qr_code_base64` / `ticket_url` são None-safe. Não importa os schemas
  gerados, então ler um QR não paga os 0,76 s de construção deles. Receita:
  `docs/recipes/mercado-pago.md`.
- **Mercado Pago (v0.249.0)** — `integrations/payment/mercado_pago`: 324
  schemas e 143 operações geradas da OpenAPI oficial do provedor
  (`mercadopago/openapi`, Apache-2.0, commit `73bc0e49`), mais
  `DEFAULT_BASE_URL`, `MercadoPagoEvent`, os helpers de dinheiro e a
  verificação de webhook. Codegen escolhido por medição: 261 KB de spec e
  0,76 s / 107 MB de import, contra 847 KB e 0,67 s / 107 MB do OpenPix — o
  argumento que fechou o codegen no Stripe não se aplica. 142 das 143
  operações têm `operationId`, então os nomes de método são do provedor.
  `MercadoPagoSettings` traz token e webhook secret, sem campo de ambiente
  porque a spec declara um único host. Três armadilhas medidas: dinheiro em
  **reais** (o inverso do OpenPix), o QR do Pix só modelado em
  `OrderTransactionPayment` (Orders API), e a assinatura de webhook ainda
  **não** confirmada contra notificação real — o manifesto é parametrizável
  por isso. Receita: `docs/recipes/mercado-pago.md`.
- **App error reports (v0.248.0)** — `app_errors`: `make_app_error_model`,
  `AppErrorService`, `make_app_error_router` e os schemas, para o frontend
  reportar ao backend o erro que quebrou na mão do usuário. Truncar em vez de
  recusar (o remetente acabou de quebrar e não trata 422); `user_id` do token
  e nunca do corpo (dois schemas, e é isso que impede atribuir o erro a outra
  conta); `user_id` nullable com FK `SET NULL`, porque erro de login acontece
  antes do usuário e apagar a conta não pode apagar a evidência do bug;
  `created_at` indexado; listagem opt-in atrás de `admin_dependency`, ou pelo
  `AdminSite`. O teto do POST público fica no `RateLimitMiddleware`, com
  `FailOpenRateLimitStore` — medido: o middleware nu **propaga** a falha da
  store, o que perderia justamente os relatos do incidente em curso. Receita:
  `docs/recipes/app-errors.md`.
- **Overlay da spec da OpenPix (v0.259.0)** — `scripts/openpix_overlay.py`.
  `vendor/openpix-openapi.json` continua byte a byte o documento do provedor,
  então refresh upstream é diff só do que **eles** mudaram; tudo que a gente
  sabe que o documento erra vive no overlay, uma correção nomeada por vez com a
  evidência ao lado, aplicada antes de gerar. Três famílias: (1) **unidade
  inteira** — 154 campos `number` que são centavo ou contagem viram `integer`,
  com a descrição lida **antes** do nome porque `inputAmount` é centavo na
  lista de depósito e é "currency unit, not cents" na cotação de stablecoin;
  os genuinamente fracionários ficam `float`. (2) **campos que a resposta
  traz e o documento não declara** — `Charge.fee`, `Charge.discount`,
  `Charge.value_with_discount`, `ChargeRefund.refund_id`; sem declaração o
  `extra="ignore"` os descartava, e quem gravava `charge.fee` num ledger
  escreveria zero em toda linha. Uma terceira família — um `DELETE` de payment
  que o documento omitiria — foi **removida na v0.260.0**: o endpoint não
  existe. O overlay se aposenta sozinho: propriedade que o provedor passar a
  declarar não é sobrescrita. Testes: `tests/integrations/payment/
  openpix/test_overlay.py`. Recipe: `openpix.md`, seção "O que este pacote
  corrige na spec".
- **Operação não verificada passa a ser marcada (v0.262.0)** — issue #227. O
  documento do Mercado Pago não tem upstream nem origem registrada, e o SDK
  oficial cobre 65 das 147 operações; as outras 82 ficavam indistinguíveis dele
  no cliente gerado. Agora são três baldes — 65 cobertas pelo SDK, 35 sondadas
  vivas, 47 sem evidência —, e as 47 carregam `**Unverified.**` na docstring
  gerada. São todas não-`GET`, porque a sonda é por método **e** path e só fala
  pelo verbo que usa: `GET /v1/customers` responde `404` enquanto
  `POST /v1/customers` é onde o SDK cria cliente. `PROBED_OPERATIONS` guarda o
  que foi sondado, quando e com que código; `make mercadopago-diff` separa os
  baldes. Testes: `TestUnverifiedOperationsAreMarked`,
  `TestTheProbeOnlySpeaksForItsOwnVerb`.
- **Campo declarado com o tipo errado deixa de quebrar a leitura (v0.269.0)**
  — issue #238. A Woovi devolve `expiresIn` inteiro e o documento declara
  `string`, então toda resposta de cobrança falhava na validação Pydantic —
  o andar seguinte ao `splits` da v0.265.0. A camada de overlay ganhou
  `MISTYPED_PROPERTIES`, irmã da `CHARGE_RESPONSE_PROPERTIES` (campo
  ausente) para o caso de campo declarado errado; `_retype` pula o que já
  estiver certo, então o override se aposenta sozinho quando o provedor
  corrigir. Decisivo para a correção não ser palpite: o documento se
  contradiz três vezes sobre o mesmo campo (`Charge` `string`,
  `ChargePayload` `number`, `WebhookCharge` `integer`), e o `WebhookCharge`
  é o mesmo objeto de cobrança. Testes:
  `tests/integrations/payment/openpix/test_real_payload_shapes.py`, com o
  corpo **capturado da API** — os fixtures antigos saíam da mesma
  especificação errada que os modelos, então fantasia e implementação
  concordavam — mais o guard contra regeração repor o tipo errado.
- **Guard de digest do documento do Mercado Pago (v0.271.0)** — issue #228.
  `scripts/regen_mercado_pago.py` ganhou `SPEC_SHA256` + `spec_digest()` e o
  drift test compara os dois, espelhando o que a OpenPix tem desde a v0.260.0.
  Fecha a segunda das três opções que a issue lista. Antes, editar o YAML
  vendorizado à mão e regenerar passava verde (medido: 14 passed com um
  comentário acrescentado); agora falha nomeando o arquivo. **Não fecha a
  issue**: o digest fixa o arquivo, não estabelece de onde ele veio — o
  Mercado Pago não publica OpenAPI, e as 82 operações que o SDK oficial não
  toca seguem sem segunda fonte. Achar a origem é a opção 1 e continua aberta.
- **Adapter da OpenPix exercitado no fio, e as seis correções que isso
  achou (v0.270.0)** — issues #239 a #245.
  `tests/integrations/payment/adapters/test_openpix_adapter_wire.py` dirige
  `OpenPixPixProvider` sobre `httpx.MockTransport` com corpos capturados do
  sandbox — 43 testes, dois deles montando rota real (webhook assinado de
  ponta a ponta, e leitura atrás de `register_exception_handlers`, porque
  status code é fato sobre serviço). Era o buraco de fundo: os 12 testes de
  adapter usam um stub que devolve modelo construído em Python, e foi por
  isso que a #238 shippou passando por eles.
  O que a fronteira achou, cada uma com correção própria: `value` de webhook
  não-numérico virando `amount_cents=0` em silêncio (**#239** — o guard
  `isinstance` era mais estreito que `to_cents`, que aceita `str` de
  propósito); bloco `customer` só com `name` violando o `oneOf` da própria
  spec (**#240** — `name` é necessário nas três variantes e suficiente em
  nenhuma); status fora do enum virando 500 na API e `PENDING` silencioso no
  webhook (**#241** — os dois agora dão `PaymentStatus.UNKNOWN`, membro novo,
  e a leitura da API parou de recusar); `charge_id` sem escape no path, onde
  `order#42` virava um `DELETE` em `/charge/order` (**#242** — corrigido no
  **emissor**, `_path_param` com `quote(..., safe="")`, valendo para 58
  interpolações na OpenPix e 108 no Mercado Pago); `raw` mudando de grafia
  conforme o caminho (**#243** — `by_alias=True`, a grafia do fio nos dois,
  com nota de migração). E `MISTYPED_POINTERS` (**#244**), tabela irmã
  endereçada por JSON pointer, para o campo mistipado que não mora em
  `components.schemas` — `dispute.value` inline, que mantinha `get_dispute`
  quebrado, e `pix.value` nos três callbacks `receivedPix*`.
  A correção da #241 é um **lift**, não uma deleção: remover o `enum` no
  lugar apaga a classe `ChargeStatus` gerada, que é superfície pública, então
  os valores viraram componente próprio e a propriedade virou
  `ChargeStatus | str | None`.
- **Painel de tasks no admin (v0.268.0)** — issue #234.
  `TaskPanelService` + `tasks=` no `make_admin_router` montam
  `{prefix}/tasks` (execuções filtráveis do `JobStore` + agenda lida do
  registro do broker), o detalhe de uma execução e o cancelamento
  cooperativo, no mesmo formato opcional do console SQL. Qualquer uma das
  metades serve; seção sem fonte não renderiza. `make_job_admin_model` dá a
  listagem com paginação/filtro/export, simétrico ao de dead-letter. **Sem
  profundidade de fila** (a TaskIQ não expõe) e **sem coluna de próxima
  execução** (o `pycron` não tem `next()`) — as duas ausências ficam escritas
  na tela e na docstring. Validado em browser real, que achou três defeitos
  que os 15 testes não pegaram: chrome ausente, coluna de cron mentindo para
  task com intervalo, e trilho de progresso invisível na linha listrada.
- **Erro esperado do Alembic vira uma linha acionável (v0.267.0)** — issue
  #233. `CommandError` chegava no `pretty_exceptions` do Typer: ~20 frames com
  o caminho do SDK no topo, para terminar numa única linha útil. Os seis
  comandos de `tempest db` passam por um tradutor que casa quatro condições
  conhecidas (banco atrás da head, histórico com mais de uma head, revisão
  inexistente, revisões fora da mesma linhagem) e imprime o que fazer;
  mensagem desconhecida sai verbatim, e `TEMPEST_DEBUG=1` devolve o traceback.
  Só a CLI: o `AlembicHelper` continua propagando a exceção tipada. Guard:
  `tests/cli/test_db_alembic_errors.py`, que inclui o repro contra Alembic de
  verdade, sem double.
- **Página de auth sobrevive ao default do SQLAlchemy (v0.266.0)** — issue
  #237. `async_sessionmaker` usa `expire_on_commit=True` por default, e as
  páginas HTML renderizam depois do commit que consome o token: o primeiro
  `user.<coluna>` do template virava IO fora do greenlet e o usuário via 500
  num fluxo que deu certo. Recarga condicional por `inspect(user).expired`,
  que não toca no banco — quem usa o `AsyncDatabaseManager` não paga query
  extra. Quatro rotas (o GET do formulário de reset não commita; as páginas
  de erro não leem coluna). Guard: `tests/auth/test_expire_on_commit.py`,
  a primeira fixture da suíte que **não** desliga o `expire_on_commit`.
- **Corpo de request para de afirmar o que ninguém disse (v0.265.0)** — issue
  #236. `create_pix_charge` falhava com 400 em toda chamada: o codegen
  materializava array opcional como `default_factory=list` e o `_dump` gerado
  só descartava `None`, então `"splits": []` ia no corpo sempre e a Woovi
  recusa lista vazia de split. Duas correções: o `_dump` gerado ganha
  `exclude_unset=True` (colateral medido zero — os dois módulos gerados não
  têm nenhum campo com default literal), e array opcional em modelo que o
  fechamento alcança só como corpo vira `| None = None`, reusando a mesma
  maquinaria de direção que decide `extra="allow"` desde a v0.260.0. Modelo
  alcançado nas duas direções mantém a lista e é o `exclude_unset` que o
  segura. 21 campos (9 OpenPix, 12 Mercado Pago); resposta intocada. Testes:
  `tests/openapi/test_payload_arrays.py` (as quatro direções) e
  `tests/integrations/payment/openpix/test_request_body_shape.py`, que lê o
  corpo enviado de um `MockTransport` — nenhum teste olhava para o corpo.
- **Um fluxo, um idioma (v0.264.0)** — issue #235. O e-mail lia
  `AUTH_DEFAULT_LOCALE` e a página negociava `Accept-Language`, então um
  cadastro saía bilíngue e nenhuma configuração consertava (setar o default
  era exatamente o valor que o header atropelava). As duas pontas passam a
  chamar `resolve_locale`: `?lang=` carimbado no link (`stamp_locale`,
  `AUTH_STAMP_LOCALE_IN_LINK` para desligar) → `user.locale` (duck-typed,
  `LocaleColumnMixin` para quem quer a coluna tipada) → `Accept-Language` →
  `AUTH_DEFAULT_LOCALE`. O link vence a linha porque registra a língua
  **daquele** e-mail — trocar a preferência entre o envio e o clique não
  descasa a página da mensagem. Cobre os 5 `_maybe_send_*` e os 5 endpoints
  HTML. Testes: `TestFlowCoherence` roda e-mail e página do mesmo
  fluxo e falha no código da v0.263.0 com o sintoma do relato.
- **Protocolo de cliente de terceiro deixa de mentir (v0.263.0)** — issue
  #231. A receita de segurança prometia que `redis.asyncio.Redis` funciona
  out-of-the-box como `ThrottleBackend` e que o `fakeredis` serve nos testes;
  medido com basedpyright contra redis-py 8.1.0, **os dois eram recusados** —
  `expire(name, seconds)` exigia o nome `seconds` e o redis-py chama de
  `time`; `delete(name)` exigia keyword e o redis-py declara `delete(*names)`.
  O mypy aceita as duas formas, que é como shippou. Agora todo parâmetro
  obrigatório desses protocolos é posicional, e os oito membros
  `Awaitable[Any]` (`get`/`expire`/`delete`, `hget`/`hset`/`hgetall`,
  `eval` nos dois middlewares) declaram o contrato real — `Awaitable[object]`
  onde o resultado é descartado, nunca `Awaitable[None]`. Guard:
  `tests/test_protocol_shape_guard.py`, que acusa os 8 retornos erasados e os
  16 parâmetros nomeados quando alimentado com o código da v0.262.0.
- **`AdminAuthBackend` genérica no principal (v0.263.0)** — issue #232. O
  valor central do ponto de extensão de auth do admin atravessava seis
  métodos como `Any`, com o contrato só em prosa. Agora
  `AdminAuthBackend(ABC, Generic[PrincipalT])`, e `UserModelAuthBackend`
  declara `AdminAuthBackend[BaseUserModel]`. Runtime intocado; `mypy --strict`
  passa a exigir o parâmetro em subclasse de consumidor
  (`disallow_any_generics`), o que o guia de migração cobre com a linha exata.
- **`heartbeat` público, fora do router (v0.261.0)** — issue #225. A mecânica
  existia desde a v0.197.0 mas só dentro de `make_websocket_router`, que impõe
  bearer no handshake e registro num hub indexado por `user_id: UUID`.
  Signaling de WebRTC com salas anônimas, endereçado por `peer_id`, não encaixa
  em nenhum dos dois e reimplementava tudo. Agora é context manager async sobre
  socket já aceito: `async with heartbeat(ws, settings=settings) as live`.
  `live.interval_seconds` para anunciar a cadência, `live.touch()` para vida
  por evidência que o socket não vê, `max_message_bytes=` opcional porque cap
  de tamanho é outra política, e `ws.receive` restaurado na saída. **Qualquer**
  frame passa a contar como prova de vida, não só `pong` — peer no meio de
  negociação está presente. E o router manda um `hello` com
  `heartbeat_seconds` antes de tudo, porque o browser não distingue intervalo
  quieto de link morto e precisa calibrar o próprio watchdog. Testes:
  `tests/websockets/test_heartbeat.py`. Receita: `websocket.md`, seção
  "Heartbeat sem o router".
- **Nome do tamanho de página configurável no fio (v0.261.0)** — issue #209.
  `CompactPaginationSchema`/`CompactPaginationFilterSchema` publicam `size`
  mantendo `page_size` como nome Python, e as duas classes base ligam
  `populate_by_name` para o consumidor sobrescrever só o campo com outro alias.
  O nome do fio se escreve duas vezes (`validation_alias` +
  `serialization_alias`); `Field(alias=...)` passa no mypy e quebra em pyright.
  `BaseRepository.paginate` não muda — a renomeação vive no schema e em nenhum
  outro lugar. Receita: `database.md`, seção "O nome do tamanho de página no
  fio".
- **Refresh da spec da OpenPix + procedência vendorizada (v0.260.0)** — o
  documento estava duas versões atrás (`3.0.3` "OpenPix" contra `3.1.0`
  "Woovi"), e nada media isso: `test_specification_is_vendored` checava
  existência e tamanho. Agora `vendor/openpix-openapi.json` é guardado como o
  provedor serve (JSON, sem reformatar), o sha256 vive em `SPEC_SHA256` e um
  teste o compara — editar o vendorizado à mão passou a falhar.
  `make openpix-fetch` rebaixa e imprime o digest novo, `make openpix-diff`
  relata a distância contra o publicado, e `vendor/PROVENANCE.md` +
  `vendor/openpix-evidence.md` guardam de onde cada documento veio e o que foi
  medido. **Breaking:** o documento novo traz `operationId` em 125 de 125
  operações onde o antigo tinha zero, então os 125 métodos mudaram de nome
  (`post_api_v1_charge` → `create_charge`); superfície foi de 373/106 para
  **686 schemas / 125 operações**; `OpenPixEnvironment.PRODUCTION` virou
  `https://api.woovi.com`. Tabela de renomeação em `docs/migration.md`.
- **Dois métodos que não podiam funcionar, corrigidos na raiz (v0.260.0)** —
  `post_api_v1_dispute_id_evidence` montava `/api/v1/dispute/:id/evidence`, com
  dois-pontos literal na URL e sem argumento para nomear a disputa;
  `get_api_v1_account_register` dizia "by CorrelationID" e não recebia nada. Os
  dois vinham de defeitos que a Woovi publicou e já corrigiu. O gerador ganhou
  os guards que faltavam: path com sintaxe Express vira nota, e parâmetro
  `in: path` sem placeholder no template passa a chegar ao arquivo gerado — a
  nota existia, mas morria no sumário porque `_build_parameters` rodava fora do
  `capture()` da operação. Testes: `tests/openapi/test_parse.py::
  TestPathTemplateGaps`.
- **SDK oficial do Mercado Pago vira a autoridade (v0.260.0)** — o provedor
  não publica OpenAPI (`api.mercadopago.com/openapi{,.json}` → `404`, nenhum
  repo de spec na org), então o vendorizado não tem upstream. `mercadopago`
  no PyPI (3.5.0) passou a ser a autoridade **em conflito, não teto**: onde
  discordam o SDK vence, onde ele é silencioso o documento fica — ele é
  wrapper fino, e as 82 operações que só nós temos respondem `401`/`403`.
  `make mercadopago-diff` relata as duas direções; a que importa está em zero,
  fixada offline por `TestTheSdkIsTheAuthority` contra `OFFICIAL_SDK_CALLS`.
  Duas rotas corrigidas (`DELETE /v1/customers/{id}/delete` →
  `/v1/customers/{id}`, `GET /authorized_payments` → `/authorized_payments/
  search`), sete adicionadas com `dict[str, Any]` nos dois lados (path e verbo
  medidos, forma não), três `GET` removidas por responderem `404`. Duas
  armadilhas de leitura medidas: regex atribui verbo de chamada multi-linha à
  URL seguinte (3 fantasmas), e ler só literal esconde URL montada em variável
  (2 operações reais). Evidência: `vendor/mercadopago-evidence.md`.
- **`extra="allow"` para de vazar para payload (v0.260.0)** — classe alcançável
  pela resposta **e** pelo request body volta a `extra="ignore"`; o fecho do
  body é subtraído do fecho da resposta. São 4 classes na OpenPix — a âncora
  `PreRegistrationPayloadObject`, body e `200` da mesma operação, onde o typo
  do caller ia para o provedor no dump, mais as três que ela alcança — e 25 na
  Mercado Pago. Guard: `TestPayloadModelsNeverAllowExtra`.
- **Model de resposta gerado usa `extra="allow"` (v0.259.0)** — vale para toda
  integração gerada, e é transitivo, porque o objeto aninhado é onde o campo
  descartado se esconde. Model de payload continua `extra="ignore"`: ali chave
  inesperada é erro de digitação de quem chamou, e levá-la ao provedor é pior
  que descartar. Fecha na raiz o defeito que o `parse_pix_payment` contornava —
  o `point_of_interaction` do Mercado Pago (o QR do Pix) era descartado na
  validação sem exceção e sem aviso.
- **Bound numérico em schema de string vira bound de tamanho (v0.258.0)** —
  o gerador passava `maximum` ao pé da letra e emitia `Field(le=140)` num
  `str`. Pydantic não rejeita o valor: levanta `TypeError: Unable to apply
  constraint 'le' to supplied value` na construção, então
  `ChargeRefundPayload` e `RefundPayload` da OpenPix falhavam sempre que o
  refund carregava comentário. `maximum`/`minimum` sob `type: string` (e sob
  o `["string", "null"]` da 3.1) agora são relidos como
  `max_length`/`min_length`; bound exclusivo não tem equivalente de tamanho
  e é descartado; schema numérico não muda. O gerado da OpenPix regenera com
  as duas linhas corrigidas.
- **Header declarado vira argumento no cliente gerado (v0.247.0)** — o
  gerador OpenAPI emitia `'header' parameter 'X' skipped (pass it via
  HTTPClient default_headers)`; agora header declarado na operação é
  argumento keyword-only da chamada, com `None` significando "não mandar".
  A saída antiga era defeito para chave de idempotência: `default_headers`
  manda o mesmo valor em toda requisição, então a segunda cobrança seria
  deduplicada em cima da primeira. Medido na spec oficial do Mercado Pago,
  as notas de construto não modelado caem de 9 para 5, e a distribuição —
  127 operações sem header, 15 com um, 1 com dois — é o que justifica um
  argumento nomeado por header em vez de uma estrutura de agrupamento.
  `cookie` segue descartado, sozinho e com o motivo na nota. O gerado do
  OpenPix **não muda**: aquela spec declara zero headers, e
  `make openpix-regen` produz diff vazio.
- **Canonical Pix contract (v0.246.0)** — `integrations/payment/base.py`:
  `PixProvider` (a plain `Protocol`, like every other provider seam here),
  `PixChargeRequest` / `PixCharge` / `PixPayer` / `PixPaymentEvent`, and a
  `PaymentStatus` a service can branch on. The reason it exists is measured
  divergence: OpenPix states cents in a `float` with three charge states,
  Mercado Pago states reais in a `float` with nine payment states, five order
  states and four transaction states — so `charge.status == "COMPLETED"`
  couples a service to one endpoint of one provider. `PixCharge` keeps the
  provider's own string beside the canonical one (`provider_status`) and the
  whole payload in `raw`, because `BaseSchema` is `extra="ignore"` and would
  otherwise drop everything the contract does not model. It also turns
  `use_enum_values` **off**: with `BaseSchema`'s default, `status` would hold
  the string and `charge.status is PaymentStatus.PAID` would be `False` on
  every charge while `==` kept working. `adapters/openpix.py` ships the first
  translation; adapters resolve lazily, so importing
  `integrations.payment` still leaves `openpix.schemas` out of `sys.modules`
  (guarded by a subprocess test). Recipe: `docs/recipes/pix-protocol.md`.
- **OpenPix settings (v0.245.0)** — `OpenPixSettings` carries
  `OPENPIX_APP_ID` and `OPENPIX_ENVIRONMENT`, and `openpix_kwargs()` returns the
  `HTTPClient` keywords the client wraps: base URL resolved from the
  environment, plus the AppID in `Authorization` **raw** (no `Bearer`). Sandbox
  is the default, and the field is a `Literal`, so `prod` fails at load instead
  of charging the wrong environment. The field is a string rather than
  `OpenPixEnvironment` so `settings` never drags the lazy `integrations`
  namespace in — the enum is imported inside the method, keeping one source of
  truth for the hosts.
- **App palette (v0.243.0, floor `tempestweb>=0.67.0` from v0.244.0)** —
  `build_web_app(..., theme=...)` hands a `tempest_core.Theme` to every
  session's `App`. This is the half CSS cannot reach: a themed component
  resolves its colors at construction and bakes them into an inline
  `style`, so rebranding the custom properties alone leaves those at the
  Material baseline. The build installs the palette around the `view`
  call, so every component the view constructs resolves against it with
  no call-site change; a `theme=` passed straight to a widget still wins.
  Needs the floor above — `tempestweb` 0.66.0 accepts the argument and
  paints baseline, because the connection lives in `tempest-core` 0.12.0.
  Measured at both ends (core 0.12.0 and 0.14.0): a red-seeded palette
  resolves a filled button to the scheme's `primary`, not the baseline
  violet. Pair it with `tempestweb.html.theme_css(theme)` in the shell
  head for what the base stylesheet paints.
- **Custom app shell (v0.225.0)** — `build_web_app(..., shell=...)` and
  `make_web_app_router(..., shell=...)` replace the artifact's
  `index.html`, the only part of the HTML an application owns (document
  `lang`, description/Open Graph meta, favicon, CSP nonce). Accepts a
  `str` (the document), a `Path` (read per request) or a callable invoked
  per request — declaring a `Request` parameter or none. On the static
  router the override answers the SPA fallback too. A `str` without `<`
  is rejected as a path written where a document was expected.
- **UI layer (v0.224.0, `[ssr]` extra)** — `tempest_fastapi_sdk.ui`, the
  interface layer of a service, mirroring `src/ui/` one-to-one:
  `ui.pages` (`Page`, moved here; `ssr.Page` re-exports it),
  `ui.layout` (`Shell` landmarks, CSS `Grid`), `ui.components`
  (`Card`, `Alert`, `DataTable` — columns/headers from the row schema —,
  `Pagination` + `pagination_for(BasePaginationSchema)`, `EmptyState`,
  `NavBar`, `ComponentClasses`, `component_stylesheet`), `ui.forms` and
  `ui.css` (below), plus `app_stylesheet()` composing tokens + reset +
  form + component rules. Components render class names, never inline
  styles. `tempest new --extras "ssr"` (and `tempest generate --src`)
  scaffolds the whole layer plus `api/routers/web.py`; the generated
  project is import-tested and served in
  `tests/cli/test_scaffold_runtime.py`.
- **Scaffolded `CLAUDE.md` (v0.224.0)** — every project from `tempest
  new` carries the rules that keep Tempest services alike: layer
  dependency direction, the seven-step order for a new domain, raising
  SDK exceptions (with `code` declared) instead of building responses,
  the pagination envelope, the `ui` layer rules, a "do not reimplement"
  table mapping intent to the SDK symbol, code conventions, commands and
  a definition of done. Its examples are executed in CI
  (`tests/cli/test_scaffold_runtime.py`): the document's own domain is
  written into a scaffolded project and served, and every SDK symbol it
  imports is resolved against the package.
- **Forms from Pydantic schemas (v0.224.0)** — `tempest_fastapi_sdk.ui.forms`:
  `form_for` / `form_spec_for` / `fields_for` / `render_form` generate an
  accessible `<form>` from a schema (label bound by `for`, `aria-invalid`,
  `aria-describedby`, native `minlength`/`max`/`step`/`pattern` from the
  field metadata), and `parse_form` reads the submission back into the
  schema — unchecked checkbox to `False`, absent key left out so the
  default applies, empty optional to `None`, repeated keys and textarea
  lines to `list`. `FormResult` carries per-field errors plus the raw
  input, so re-rendering keeps what the reader typed. Overrides via
  `json_schema_extra={"ui": {...}}`; nested models and binary fields
  raise `UnsupportedFieldError` rather than rendering something that
  cannot round-trip. Emits the elements through `tag`/`attrs`: measured,
  `tempest_core`'s `Input` renders without a `name` and `Dropdown` /
  `TextArea` render as empty `<div>`s under the HTML renderer
  (`tests/ui/test_core_contract.py`).
- **Typed CSS (v0.224.0)** — `tempest_fastapi_sdk.ui.css`: `Rule`
  (typed `Style` and/or raw declarations, optional flex `layout=`),
  `Media` (`min_width` / `max_width` / `dark` / `reduced_motion`),
  `StyleSheet` (`to_css`, `merge`, `class_names`, `etag`, and a `cls()`
  that raises on a class the sheet does not define), `ThemeTokens`
  (adapts `tempest_core`'s `TokenSet` into CSS custom properties —
  39 colour roles in light and dark, spacing, shape, typography, motion;
  `breakpoint()` returns a number because a media query cannot read
  `var()`), and `make_css_router` / `css_response` / `stylesheet_links`
  serving the sheet rendered once with a strong `ETag` and `304`.
  `html_response` gained `stylesheets=` and `head=`. Note: `Style`
  validates colours as hex, so token references live in
  `Rule.declarations`, not in `Style`.
- **Geolocation (v0.104, `[geo]` extra = httpx)** — `tempest_fastapi_sdk.geo`,
  distance + travel-time between two points with no paid API. Two layers over
  shared schemas (`Coordinate`, `TravelEstimate`, `TravelMode` CAR/MOTORCYCLE/
  BUS): offline heuristic — `haversine_km` (great-circle) + `estimate_travel`
  (road = Haversine x circuity factor; time = car avg speed x mode factor,
  `source="heuristic"`, zero deps/network); real routing — `RoutingBackend`
  Protocol + `OSRMBackend` (free OSRM demo/self-host, injected
  `httpx.AsyncClient`, `source="osrm"`). Moto/bus derive from car via
  `DEFAULT_MODE_DURATION_FACTORS` so both layers work on a car-only profile.
  Submodule import like vision; heuristic imports without the extra.
  **Expanded (v0.106):** offline geometry (`bounding_box`, `within_radius`/
  `nearest` with `key=`, `initial_bearing`/`destination_point`,
  `point_in_polygon`/`polygon_area_km2`, `path_length_km`); DB radius search
  (`GeoPointMixin` + `GeoRepositoryMixin.nearby` bbox-prefilter+haversine,
  `PostGISRepositoryMixin.nearby` ST_DWithin, `make_geo_point_model`);
  geocoding (`GeocodingBackend`/`NominatimBackend` + `GeocodeResult`);
  `OSRMBackend.matrix` (table → `DistanceMatrix`) + `route(with_geometry=True)`
  (decoded into `TravelEstimate.geometry`) + per-mode `DEFAULT_MODE_PROFILES`;
  polyline codec (`encode_polyline`/`decode_polyline`); `TravelMode.BICYCLE`/
  `PEDESTRIAN`; BR `uf_centroid`/`UF_CENTROIDS` (offline) + `cep_to_coordinate`.
- **GenAI ergonomics (v0.105)** — `GenerationConfig` (typed generation
  params over `**kwargs`, `config=` on `generate`/`chat`/`stream`),
  `make_genai_router` (opt-in FastAPI router mounting only injected
  objects: `/generate`+SSE, `/chat`, `/embed`, `/rag`, `/transcribe`,
  `/tts`), `RedisEmbeddingCache` + `AsyncEmbeddingCache` (async Redis
  vector cache; `Embedder` awaits sync-or-async caches at one call site).
- **Chat (v0.105, `tempest_fastapi_sdk.chat`, no extra)** — threaded
  chat over the SDK primitives: abstract `BaseConversationModel`/
  `BaseConversationParticipantModel`/`BaseMessageModel` + `make_*`
  factories, `ChatService` (`start_conversation`/`post_message`/
  `list_messages`/`list_conversations`/`is_participant`), `make_chat_router`
  (participant guard) + real-time fan-out via an injected `SSEBroker`.
  Submodule import.
- **Reviews (v0.105, `tempest_fastapi_sdk.reviews`, no extra)** —
  comments + 0–5 star ratings on any polymorphic `(target_type,
  target_id)`: `BaseCommentModel` (thread via `parent_id`) / `BaseRatingModel`
  (unique per user) + `make_*`, `ReviewService` (`add_comment`/
  `list_comments`/`rate` upsert/`get_user_rating`/`aggregate` → avg +
  count + per-star distribution), `make_reviews_router`; `RatingField`
  (`Annotated[int, 0..5]`) in `utils.fields`. Submodule import.
- **Upload** — `UploadUtils` with pluggable backends
  (`LocalUploadStorage`, `MinIOUploadStorage`, opt-in injected via
  `backend=`), download helpers, presigned URLs, plus `FileStoreUtils`
  — a unified facade bundling upload + download + presign over one
  shared backend (`uploader`/`downloader`/`backend`/`client` escape
  hatches).
- **MinIO / S3** — `AsyncMinIOClient` via `[minio]` extra
  (bucket lifecycle, object I/O, streaming download, presigned
  URLs).
- **Email** — SMTP via `EmailUtils` + Jinja2 template rendering
  with bundled defaults (`activation.html`, `password_reset.html`)
  shadowable by the project's `template_dir`. **Bulk send (v0.258.0):**
  `send_many()` → `BulkEmailReport` (`delivered`, `permanent`, `transient`,
  each failure a `FailedRecipient` with the SMTP code and text). One
  connection per batch (`batch_size=500`) instead of one per message, at
  most `max_concurrency=32` open at once — the same two defaults the Web
  Push broadcast uses. A recipient's refusal is reported, never raised; only
  the operation failing (host, connection, auth) raises. `5xx` (prune) and
  `4xx` (requeue) land in separate lists, and a refusal with no code is
  filed as transient. Each recipient gets their own message.
- **WebPush** — `WebPushDispatcher` (`send`/`send_many`, 404/410
  pruning), subscription storage (`BaseWebPushSubscriptionModel` +
  `make_web_push_subscription_model`) + `WebPushSubscriptionService`
  (`subscribe`/`unsubscribe`/`list_for_user`/`notify_user` with
  auto-prune of gone endpoints) + `make_web_push_router` (opt-in
  `/subscribe` + `/unsubscribe`, aligned with `tempest-react-sdk`);
  webhook signatures.
- **Cache** — Redis manager + `@cached`.
- **Queue / tasks** — typed facades hiding FastStream + TaskIQ:
  `MessageBroker` (`.rabbitmq`/`.redis`/`.kafka`/`.nats`, `@mq.on(channel)`
  consumer, channel-first `publish(channel, message)`, `.broker` escape
  hatch) and `TaskQueue` (`.rabbitmq`/`.redis`/`.memory`, `@tq.task` →
  `Task.enqueue`/`.run`, folded `@tq.cron`/`@tq.interval` +
  `start_scheduler`, `tq.broker`/`tq.scheduler` for the CLIs).
  **Jobs (v0.228.0):** `BaseJobModel` + `JobStore[JobT]` — a row per unit
  of long work so the interface can say queued / running / done / failed,
  with `enqueue`, a conditional-`UPDATE` `claim` (loser gets `None`),
  `succeed`/`fail` that drop the payload, `list_recent`, `reclaim_stale`
  (bounded by `max_attempts`) and `watch()` polling without holding a
  session. The symmetric half of the outbox: message to publish vs work
  to execute. **Progress (v0.242.0):** `progress` + `stage` columns,
  `report_progress()` as a conditional `UPDATE` that cannot rewind the bar
  or repaint a cancelled job, `list_recent(statuses=...)` for the one
  "queued or running" query, `watch(emit_on=...)` so a bar moves between
  status changes, and `PhasePlan` + `ProgressTracker` — measured phase
  weights interpolated inside the running phase, never filling it, with
  the progress tick doubling as the cancellation check. **Worker lifespan (v0.227.0):** `@tq.on_startup` / `@tq.on_shutdown`
  (zero-argument hooks, sync or async, `scope="worker"|"client"|"both"`,
  worker by default) and `resources=[db, broker]` / `tq.use(...)` over
  the `LifecycleResource` protocol — the worker had no `lifespan`, so
  nothing opened or disposed the pool. **Both
  decorator and class-based styles**: `Consumer` + `@subscribe` +
  `MessageBroker.register` (constructor form takes explicit
  `channel`+`schema`, no magic); `TaskDef` + `@task_method` +
  `TaskQueue.register`. **Class-based publish (v0.208.0):**
  `Publisher[T]` + `MessageBroker.publisher_for` — the symmetric half
  `Consumer` never had. Declares `channel` (`str | QueueSpec`) + `schema`
  as class attributes; `publish` **takes the declared type**, enforces the
  schema on the way out (the consumer is a process away and can only
  reject what already left), and registers a spec's topology so a
  producer-only service still declares the DLX it names. Goes through
  `MessageBroker.publish`, not FastStream's publisher object, so it keeps
  the `message_id` dedup needs and the tracing headers — the raw object
  would look identical and lose both. `Consumer`/`@subscribe`/
  `Subscription` also had `channel: str` while `register` bound
  `str | QueueSpec`, so the class path could not declare topology without
  failing the type checker. **Class-path parity (v0.209.0):** `prefetch`
  is now a named keyword on `subscribe()` and `Consumer.__init__`, plus a
  class attribute covering every binding (a `@subscribe` naming its own
  wins) — `register` translates it into the FastStream `Channel` exactly
  as `on()` does. FastStream has **no** `prefetch` keyword, so the class
  path raised `TypeError` where the decorator worked. Found against a
  real broker and pinned there: `rabbitmqctl list_consumers queue_name
  prefetch_count` reports the caps. The constructor-form `Consumer` also
  forwards `**options` (`exchange=` etc.) — it registered `options={}`
  while `@subscribe` had taken them all along. Same release, task side:
  `@task_method(retry=RetryPolicy(...))` / `TaskDef(retry=)` were
  **silently ignored** — the policy object rode `**options` into a `retry`
  label TaskIQ never reads, so the task never retried and nothing raised
  (a real worker ran the decorator task twice and the class task once).
  `register` now renders it into labels like `task()` does, `retry` is a
  class attribute too, and `TaskDef(**options)` forwards extra labels.
  And the documented `taskiq worker src.tasks:tq.broker` **could not
  start**: the CLI resolves `module:attr` with a plain `getattr`, so every
  dotted form raised `AttributeError` — docs now bind `broker = tq.broker`
  first. **Cron without syntax**: `Cron`/`CronOffset`
  (`BRASILIA` etc.)/`Weekday` enums + `daily`/`weekdays`/`every_n_minutes`/
  `weekly`/`monthly`/… builders (dependency-free). `AsyncBrokerManager`
  renamed to **`AsyncQueueManager`** (v0.94.0; old alias kept); legacy
  `AsyncTaskBrokerManager`/`AsyncTaskScheduler` kept. Outbox
  (`BaseOutboxModel`/`OutboxRelay`/`save_with_outbox`) plugs its `publish`
  into `MessageBroker.publish`. **Task reliability + observability (v0.157.0):**
  `RetryPolicy` + `TaskQueue.enable_retries` (TaskIQ `SimpleRetryMiddleware`),
  `DeadLetter`/`DeadLetterSink` + `TaskQueue.dead_letter` (terminal-failure
  routing, backend-agnostic; `make_dead_letter_middleware`), `TaskMetrics` +
  `TaskQueue.enable_metrics` (`tasks_runs_total`/`tasks_duration_seconds` on the
  shared Prometheus registry). Opt-in middleware, imports without `[tasks]`.
  **Dead-letter panel (v0.158.0):** `BaseDeadLetterModel`/`make_dead_letter_model`
  + `DbDeadLetterSink` (persist terminal failures) + `make_dead_letter_admin_model`
  (read-mostly `AdminModel` + `make_requeue_action`) + `task_inventory`
  (`TaskInfo` per registered task). No live queue introspection (TaskIQ exposes
  none) — shows persisted failures + declared task set.
- **Envelope único de erro, inclusive no 429 (v0.256.0)** — o
  `RateLimitMiddleware` respondia `text/plain`, contradizendo o
  `ErrorResponseSchema` que o `error_responses()` publica para o mesmo status.
  Agora emite `{detail, code, details}` como todo handler, com `error_code`
  configurável (default lido de `TooManyRequestsException.code`) e
  `retry_after_seconds`/`limit` em `details`. `BodySizeLimitMiddleware` já
  emitia o envelope; `Idempotency`/`ResponseCache` não emitem corpo de erro
  próprio.
- **Redis handle para wiring no import (v0.256.0)** —
  `AsyncRedisManager.client_proxy`, handle estável que resolve o client vivo
  a cada comando. Existe porque os dois ciclos de vida não se encaixam:
  `add_middleware` roda no import e `connect()` roda no lifespan, então
  `manager.client` levanta na hora de construir qualquer store Redis do SDK
  (`RedisRateLimitStore`, `RedisQuotaStore`, `RedisSessionStore`,
  `RedisIdempotencyStore`, `RedisResponseCacheStore`,
  `RedisFeatureFlagBackend`, `RedisWebAuthnChallengeStore` — todos recebem
  client pronto, nenhum aceita factory). Adiantar a construção não bastava:
  `disconnect()` descarta o client e o `connect()` seguinte cria outro.
- **BR validators** — CPF/CNPJ/CEP/phone, with `*Field` Pydantic types
  (`CPFField`/`CNPJField`/`CPFOrCNPJField`/`PhoneBRField`/`CEPField`;
  pre-0.76 unsuffixed names kept as deprecated aliases). **PIX keys**
  (v0.95.0): `PixKeyField` validates+normalizes any of the 5 BACEN key
  types (CPF/CNPJ/email/E.164 phone/random UUID); `PixKeyType` +
  `detect_pix_key_type`/`is_valid_pix_key`/`normalize_pix_key`.
  **Celular (v0.256.0):** `is_valid_phone_br` aceita fixo de propósito, e
  quem usa o telefone como canal de entrega (WhatsApp/SMS/OTP) tem
  `is_valid_mobile_phone_br`, `normalize_mobile_phone_br` (sempre os 11
  dígitos nacionais, ao contrário de `normalize_phone_br`),
  `MobilePhoneBRField` e `parse_phone_br` -> `PhoneNumberBR`
  (DDD/número/`is_mobile`/E.164, `None` no que a ANATEL não atribui).
- **BR localities** — `UF` (StrEnum, 27 siglas) + `Region`
  (5 macro-regiões IBGE), `StateBR`/`CityBR` schemas, offline
  dataset of 27 states + 5606 municipalities (IBGE-derived,
  DF as 36 administrative regions), `list_states`/`get_state`/
  `cities_by_uf`/`states_by_region`, `is_valid_uf`/`normalize_uf`,
  `is_valid_city`/`normalize_city` (accent/case-insensitive),
  `UFField`/`CityNameField`, plus `ChoiceBR` + `uf_choices`/
  `region_choices`/`city_choices` (frontend `<select>` choices).
- **Rate limit** — `RateLimitMiddleware` (sliding window) with
  pluggable store (`MemoryRateLimitStore` / `RedisRateLimitStore`,
  atomic Lua) and per-principal key extractors (`key_by_ip`,
  `key_by_jwt_subject`, `key_by_jwt_claim`, `key_by_header`).
- **i18n error envelopes** — `MessageCatalog` +
  `default_message_catalog` (PT-BR + EN), `parse_accept_language`,
  `AppException.message_key` / `message_params`,
  `register_exception_handlers(..., catalog=..., default_locale=...)`.
- **Cache invalidation** — `@cached(namespace=..., tags=...)` +
  `CacheInvalidator` (`invalidate_namespace` / `invalidate_tag` /
  `invalidate_tags` / `invalidate_keys`).
- **Feature flags** — `tempest_fastapi_sdk.flags`: `FeatureFlags`
  over `Memory` / `Env` / `Redis` / `Composite` backends +
  `make_flag_dependency` route guard.
- **Audit trail** — `BaseAuditLogModel` + `AuditAction`,
  `snapshot_model` / `diff_snapshots`, `BaseRepository` opt-in
  (`audit_model=...` + `add_audited` / `update_audited` /
  `delete_audited`, same-tx).
- **Admin panel** — Jinja + HTMX (`AdminSite`, `AdminModel`,
  `make_admin_router`), typed theming via `AdminTheme` (colors /
  logo / favicon / font / radius / footer / dark mode /
  `custom_css_url`, injected as `:root` overrides), custom bulk actions
  (`@admin_action` + `AdminModel(actions=[...])`, `AdminActionContext` /
  `AdminActionResult`), file/image upload fields (`AdminModel(
  upload_fields=[...], upload_storage=...)`), rich list filters
  (bool/enum/FK select, date-range, text — auto by column type).
- **OpenAPI codegen (v0.161.0)** — `tempest_fastapi_sdk.openapi` +
  `tempest openapi-client <spec>`: generates Pydantic schemas **and** a
  typed HTTP client from a third party's OpenAPI 3 spec into
  `<src|app>/integrations/<name>/`. Every `Field` carries the spec's
  `title`/`description`/`examples` (the module doubles as the
  integration's docs); Python names + wire-name `alias` +
  `populate_by_name`; reserved words resolved; optional collections
  default to `[]`; enums → `BaseStrEnum`/`BaseIntEnum`; `allOf`
  flattened; recursion via `model_rebuild()`. Client takes an injected
  `HTTPClient` (so retry/breaker/creds stay with the caller, and
  `httpx.MockTransport` tests it offline). Emitted code passes
  `ruff check` + `ruff format --check` **before** the format pass
  (asserted against raw output), and an unchanged spec regenerates
  byte-identically. Unrepresentable constructs (`not`, external `$ref`,
  Swagger 2.0, non-JSON bodies, header params) → a line in the command
  summary stating what was done instead **plus** an
  `# openapi: unsupported` comment above the affected field/method/
  parameter (v0.212.0) — never a silent wrong schema. YAML needs
  `[openapi]` (pyyaml); JSON needs nothing.
  **Hostile text + wrong names (v0.211.0):** the spec's own prose used to
  break the generated package — text was interpolated **raw** into a
  double-quoted literal behind a guard (`"'" in repr(value)`) that matches
  `repr`'s delimiter rather than an apostrophe, so every quote-free string
  took that path (a YAML block scalar emitted an unterminated literal; `\b`
  changed value in silence). `openapi/source.py` now owns literal writing
  for both emitters and **mirrors `ruff format`**: quotes normalized by
  escape count (single when the text has more `"` than `'`), long text split
  into **two or more** adjacent literals (a lone parenthesized one is joined
  straight back), `r"""` for prose carrying a backslash, enum member names
  capped (the name derives from the value and `ruff format` breaks no
  assignment target; the value is never truncated). Names: `Transaction2` not
  `Transaction_2` (`N801`), `field_2fa` not `_2fa` (a leading underscore makes
  it a Pydantic **private** attribute, so the field vanishes). Paths are
  reconciled with the template — undeclared placeholder synthesized as a
  required `str` (the path is an f-string; skipping it leaves an undefined
  name), uninterpolated parameter dropped, order taken from the template.
  Pinned in `tests/openapi/test_hostile_spec.py`, generated with
  `run_format=False` **on purpose**: the command's own `ruff --fix` pass was
  hiding three of these.
  **Line budget vs. synthesized names (v0.213.0):** generating against the
  **real** OpenPix spec (847 KB, 358 schemas) gave 12 `E501`, 8 surviving
  `ruff format` — the hostile suite missed them because they need long
  *names*, and the ones that overrun are the names the generator builds
  itself. The `name: Annotation = Field(` head was never measured; when it
  overruns `ruff format` re-indents the arguments one level deeper, so every
  pre-split string came out at 92. The emitter now mirrors ruff's shape order
  (head / wrapped assignment at 12 / broken annotation at 8, and *inside the
  brackets* for a whole subscript). v0.211.0's forced `minimum=2` split was
  **wrong** — ruff joins a lone parenthesized literal back only when the
  collapsed line fits, which is a line never split, so forcing a second piece
  made ruff rejoin them; splits are now exactly as deep as the budget needs.
  Also: long `examples` lists explode (key prefix **measured**, not glued on),
  `return _validate(...)` is measured, and `MAX_CLASS_NAME = 55` caps
  synthesized names — bound by the docstring `Attributes:` entry, not the
  `class` statement (6 of 358 truncated on OpenPix, no new collision).
  **Stated limit:** a long field name next to a single-identifier annotation
  has no formatting that fits; only shorter names help.
  **Docs-audit fixes (v0.212.0):** the recipe had promised an
  `# openapi: unsupported` marker for several releases while the package
  emitted **zero** of them, and the CLI summary header hardcoded
  `(rendered as Any, marked in the output)`, false for every skipped /
  ignored / synthesized note. The marker now exists — `_Parser.capture()`
  attributes each note to the field, parameter or operation that raised it
  (`FieldIR`/`ParameterIR`/`OperationIR.unsupported`), and the emitters write
  it **above** the line, never trailing, so a long reason wraps instead of
  overrunning and `ruff format` has nothing to move. Sinks de-duplicate
  independently of the summary: the summary must not repeat itself, but two
  fields hitting the same gap both need marking. The header now states that
  each line says what was generated instead. Found by auditing the docs
  against the code — no test catches prose promising a feature.
- **Bundled integrations (v0.215.0)** — `tempest_fastapi_sdk.integrations.<kind>.<provider>`,
  grouped by **what the provider does**, not by vendor. **`integrations.payment.openpix`**
  ships the *whole* OpenPix API: 358 schemas + 105 operations generated from the
  spec pinned at `vendor/openpix-openapi.json` and **checked in**, so no service
  runs the generator. `scripts/regen_openpix.py` (`make openpix-regen`) is the
  only way to produce them and a **drift test fails on any hand edit**;
  `--name open_pix` is what yields `OpenPixClient` (not `Openpix…`), fed in at
  generation so byte-identity holds. **Lazy via PEP 562** — ~11 ms to import the
  subpackage, ~150 ms on the first generated name (Python 3.11, parent package
  already imported); a subprocess test asserts
  `…openpix.schemas` stays out of `sys.modules` on import. The vendored spec is
  build-time only, outside the wheel. Hand-written half: `OpenPixEnvironment`
  (production/sandbox are different domains), `to_cents`/`reais_to_cents`/
  `cents_to_reais` (spec says *"Value in cents"* then types it `number`;
  `to_cents` **refuses a fraction**, `reais_to_cents` rounds half-up unlike
  built-in `round`), `OpenPixEvent` (28 events, `OPENPIX:` prefix **not**
  uniform, pinned by test), and `make_openpix_webhook_dependency()`.
  `OpenPixWebhookEvent` is a frozen **dataclass** — `BaseSchema`'s
  `use_enum_values=True` would make `event.event is OpenPixEvent.X` **silently
  false on every delivery**. Unknown event and non-JSON-but-verified body stay
  200 on purpose. **Key is RSA-1024** (verified on load): proves origin, does
  **not** authorize moving money — re-read the charge, keep the handler
  idempotent. Verifying needs `cryptography` (extra `[webpush]` or direct);
  the module imports without it and fails at `verify()`, so the recipe says so.
  **BREAKING in v0.215.0:** was `tempest_fastapi_sdk.openpix` in v0.214.0, no
  shim (the old path lived hours). **v0.233.0** raised it to **373 schemas**
  and fixed two defects that had shipped since v0.214.0: a component whose top
  level is `oneOf` became a class with **no fields**, and `extra="ignore"` then
  dropped the caller's data (a charge went out with `"customer": {}`, no error
  anywhere), while `allOf: [<oneOf>, {flag}]` collapsed the payment body to the
  `autoApprove` flag alone. Untitled object variants now merge into one model
  (required = intersection); titled ones become a class each under a union
  alias. The package's `__all__` is now **generated** and lists every schema —
  measured with basedpyright, the `TYPE_CHECKING` wildcard made names visible
  but not re-exported, so consumers got *"X is not exported from module"* (mypy
  accepted it either way). Recipes: `docs/recipes/openpix.md` (architecture,
  charge, webhook + API read-back, reconciliation, refunds) and
  `docs/recipes/openpix-subscriptions.md` (plans live in your database,
  `RECURRENT` vs `PIX_RECURRING`, instalments, cancellation). **v0.234.0**
  fixed the third defect of the same family, found by type-checking the
  published wheel: every aliased field used `Field(alias=...)`, which renames
  the parameter in the synthesized `__init__`, so pyright rejected
  `ChargePayload(correlation_id=...)` and demanded the wire spelling. The wire
  name is now written as `validation_alias` + `serialization_alias`
  (`tests/test_alias_guard.py` keeps `alias=` from returning), which leaves
  runtime untouched and clears both basedpyright and mypy.
- **Fakes das costuras de terceiro (v0.253.0, `tempest_fastapi_sdk.testing.fakes`)** —
  `FakePixProvider`, `FakeTextBackend`, `FakeModerationBackend`,
  `FakePushDispatcher`, `FakeEmailUtils`, `FakeGeocodingBackend`,
  `FakeRoutingBackend`, `FakeWebSearchBackend`: um substituto por costura, sem
  credencial e sem rede, para rodar o serviço local e para asserção em teste.
  **Dirigíveis**, e é isso que os separa de mock: guardam estado e deixam o
  teste movê-lo (`advance(id, status)` chega a `PAID` e `CHARGED_BACK` sem
  ninguém escanear QR nem abrir disputa; `flag`, `add_place`, `add_route`,
  `add_results`, `queue`), e `fail_next(erro)` alcança o ramo que falha com a
  exceção que o cliente real levanta. O que aconteceu fica inspecionável
  (`calls`, `outbox`, `sent`, `prompts`, `charges`, `queries`). `FakeEmailUtils`
  é **subclasse** de `EmailUtils`, não implementação de protocolo, porque
  `UserAuthService` é tipado contra a classe concreta — só o `send` é
  substituído, `render_template` continua o real. `FakeRoutingBackend` delega a
  `estimate_travel` em vez de inventar aritmética. Resolução lazy (PEP 562):
  pedir o fake de Pix não importa genai, push nem geo. Guard em
  `tests/testing/test_fakes_contract.py` compara assinatura por
  `inspect.signature`, exige `async` onde a costura é `async`, e falha quando um
  fake novo entra sem cobertura.
- **CLI** — `tempest new` (scaffolds layered service +
  docker-compose + multi-stage uv `Dockerfile`/`.dockerignore`),
  `tempest generate --docker` (regen compose) / `--dockerfile`
  (regen Dockerfile + .dockerignore) / `--src` (extra source layers),
  `tempest db init/revision/upgrade/downgrade/current/history/seed`,
  `tempest user create [--admin] [--set col=value] / list / promote /
  revoke` (**`--set` (v0.240.0)** seeds the columns a concrete
  `UserModel` adds: validated against the mapped columns, converted to
  the column type, `email`/`hashed_password`/`is_admin` refused in
  favor of their own flags; a required column with no default is
  prompted for on a TTY and is an exit-2 error without one),
  `tempest secrets rotate`,
  `tempest model analyze/bench/optimize/quantize/export-ort/hardware/
  pull/cache-list/cache-rm`,
  `tempest pdf list/schema/render`,
  plus quality gates (`lint`, `fix`, `format`, `fmt-check`, `type`,
  `test`, `check`), `openapi-errors`, `openapi-client`, `permissions`.
  **`tempest pr-prompt` (v0.210.0)** — builds the prompt that makes an AI
  fill this branch's PR description: the repository's own PR template
  (`.github/pull_request_template.md` and the other conventional
  spellings; `--template` overrides, bundled PT-BR/EN-US fallback), the
  rules that stop the model from returning the placeholders, and the
  branch context. Prompt on stdout (`| claude -p`), diagnostics on
  stderr, `--out` writes a file. Diffs are `base...head` — the merge-base
  diff the forge shows, since two dots would attribute the base's own
  commits to the PR; excerpts go **most-changed first** (`--max-files`
  spent alphabetically never reaches the file the PR is about); and every
  bound is stated **inside** the prompt, cutting on line boundaries.
  Commits and the changed-file list are **never** bounded — only the
  patch excerpts are, and `--full` lifts both bounds (refusing to sit
  next to an explicit `--max-files` / `--max-chars` instead of
  overriding it in silence).
  Missing base falls back to `origin/<base>`; an empty comparison exits
  `1`. Recipe: `docs/recipes/cli.md`.

- **Quality gate lives in `tempest-cli` (v0.226.0)** — `lint` / `fix` /
  `format` / `fmt-check` / `type` / `test` / `check` / `pr-prompt` moved
  to a framework-agnostic package (only runtime dep: `typer`). The SDK
  depends on it and mounts the same commands via
  `tempest_cli.main.register_commands(app)`, so `tempest check` is
  unchanged and there is a single implementation. `[tool.tempest]` is now
  read per owner: `typing_strictness` by the gate, `commands` by the SDK
  (`load_project_commands()`); `TempestConfig` no longer carries
  `commands`. `tempest_fastapi_sdk.cli.lint` / `.pr_prompt` remain as
  re-exports. Reason, measured: reaching those commands here cost 38.7 MB
  of dependencies and ~0.5 s of import per invocation.

The whole Tier S / Tier A / Tier B backlog that used to live here is
**shipped**, and so is the five-item next-version plan that followed it
(rate-limit per principal, i18n error envelopes, `@cached`
tag/namespace invalidation, feature flags, audit trail — all landed in
v0.54.0–v0.58.0). The covers list above is the source of truth; don't
re-plan finished work.

### Next-version plan

**Theme: Admin panel — close the gap vs Django Admin / Laravel Nova /
SQLAdmin.** The current admin is a complete Phase-1 surface (list /
detail / CRUD, ILIKE search, boolean-only filters, sort, offset
pagination, CSV/JSON export, bulk activate/deactivate/delete, TOTP MFA,
audit stamps, 8 fixed widgets, FK `<select>` capped at 1000 rows).
Competitor admins go further; several of those gaps map to **engines
the SDK already ships but the admin does not surface** — so the work is
mostly wiring, not greenfield.

Build in tiers. Ship each item, document it (same-commit docs rule),
then move it up to the covers list.

**Shipped — `AdminTheme` (v0.72.0).** Typed appearance overrides
(colors / logo / favicon / font / radius / footer / dark mode /
`custom_css_url`) injected as `:root` CSS-variable overrides via
`AdminSite(theme=...)`. This is the "beautiful + typed customization"
foundation the user asked for first; the functional Tier 1 items below
inherit the look for free. Now in the covers list.

**Shipped — custom actions (v0.84.0).** `@admin_action` decorator +
`AdminModel(actions=[...])` + `AdminActionContext`/`AdminActionResult`;
custom entries render in the bulk dropdown (namespaced `custom:<name>`),
run on the checked rows, and flash a banner on the list view. Now in the
covers list.

**Shipped — file / image upload field (v0.85.0).** `AdminModel(
upload_fields=[...], upload_storage=...)` renders String columns as file
inputs, streams the upload to `LocalUploadStorage` / `MinIOUploadStorage`,
and stores the returned key. Now in the covers list.

**Shipped — rich filters (v0.86.0).** `list_filter` fields auto-pick a
widget by column type: bool / enum / FK → select, date/datetime →
inclusive date-range (two inputs → `__gte`/`__lte`), other → text.
Now in the covers list.

**Shipped — the whole Tier 1/2/3 backlog is done** (verified 2026-07-24):

1. **Audit history viewer** — per-row change timeline in the detail view
   wired to `AdminModel(audit_model=...)` + `_format_audit_changes`
   (`BaseAuditLogModel`); template renders the `history` timeline.
2. **Autocomplete FK fields** — HTMX-backed FK inputs (`FieldRef`), past
   the plain-`<select>` cap.
3. **Inlines / nested relations** — `Inline` (edit child rows in the
   parent's detail/edit view).
4. **Dashboard business metrics / charts** — `MetricCard` +
   `MetricValue` / `MetricTrend` / `MetricPartition`
   (`AdminSite(dashboard_cards=[...])`), distinct from the system panel.
5. **RBAC granular** — `AdminPermission` + `AdminAccessPolicy` (per-model /
   per-action), beyond `is_admin` + `can_create`/`can_edit`/`can_delete`.
6. **CSV import** — `AdminModel(can_import=True)` + the CSV import page /
   bulk-create endpoint (counterpart to the export).
7. **Lenses** — `Lens` (saved alternate views / queries per model).

All the above are in the covers list / admin recipe. Origin: competitor gap
analysis (Django Admin, Laravel Nova, SQLAdmin, Starlette-Admin) run
2026-06-26.

**Admin backlog: empty.** No queued admin work — pick the next theme from
business need, not from a stale list. Keep this honest, not aspirational.
