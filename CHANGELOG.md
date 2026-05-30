# Changelog

All notable changes to **tempest-fastapi-sdk** are listed below.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  - See the [Migration guide 0.7 → 0.8](README.md#migration-guide-07--08)
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
