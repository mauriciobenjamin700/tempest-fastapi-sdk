# Recipes

Bite-sized "I want to wire X" walkthroughs. Each page starts with **what problem it solves**, **when to reach for it**, and a complete code example you can copy verbatim.

!!! tip "When to read what"
    - Just need to look up a signature? Jump to **[Reference »](../reference.md)**.
    - Building a brand-new service? Follow the linear **[Tutorial »](../tutorial.md)** first.
    - Wiring a specific SDK piece? You're in the right place — pick the recipe below.

| Theme | Covers |
| --- | --- |
| **[Database »](database.md)** | `BaseModel`, `AsyncDatabaseManager`, `BaseRepository` (CRUD + filters + bulk), offset/cursor pagination, mixins, `AlembicHelper`, `SlowQueryLogger` |
| **[Multi-tenant »](multi-tenant.md)** | `TenantScopedRepository` — `tenant_id` isolation on every query |
| **[Audit trail »](audit-trail.md)** | `BaseAuditLogModel`, `add_audited` / `update_audited` / `delete_audited`, `snapshot_model` / `diff_snapshots` |
| **[Offline-first sync (delta) »](offline-sync.md)** | `BaseRepository.changes_since`, `SyncFilterSchema`, `SyncPaginationSchema`, cursor deltas + soft-delete |
| **[HTTP layer »](http.md)** | `apply_cors`, `RequestIDMiddleware`, `RateLimitMiddleware`, `make_health_router`, JWT / role / permission dependencies, webhook signature verifier, pagination Link headers, tool-spec router |
| **[HTTP client (outbound) »](http-client.md)** | `HTTPClient` — typed httpx with retry/backoff, circuit-breaker, X-Request-ID; `RetryPolicy`, `CircuitOpenError` |
| **[Idempotency »](idempotency.md)** | `IdempotencyMiddleware`, `MemoryIdempotencyStore` / `IdempotencyStore` (Redis) — safe replay of POST/PUT/PATCH/DELETE |
| **[Cache »](cache.md)** | `AsyncRedisManager`, `@cached` decorator, `CacheInvalidator` (tag/namespace) |
| **[Feature flags »](feature-flags.md)** | `FeatureFlags`, env/Redis/composite backends, `make_flag_dependency` |
| **[Auth flow (signup/reset) »](auth-flow.md)** | `UserAuthService`, `make_auth_router` — signup / activation / login / password reset, token delivery (bearer/cookie/both), `BaseUserModel` |
| **[MFA (TOTP / 2FA) »](mfa.md)** | `MFAMixin`, `TOTPHelper`, enroll/confirm/verify/disable endpoints on `make_auth_router`, recovery codes |
| **[Refresh tokens (rotation/revocation) »](refresh-tokens.md)** | `BaseUserRefreshTokenModel`, `make_user_refresh_token_model`, `issue_token_pair`, rotation + family reuse detection |
| **[Server-side sessions »](sessions.md)** | `SessionMiddleware`, `SessionAuth`, `make_session_router`, `MemorySessionStore` / `RedisSessionStore` |
| **[Real-time »](realtime.md)** | Overview — when to choose SSE, WebSocket or Web Push |
| **[Server-Sent Events (SSE) »](sse.md)** | `EventStream`, `sse_response`, `ServerSentEvent`, `SSEBroker` (per-channel fan-out, Redis bridge) |
| **[WebSocket router »](websocket.md)** | `WebSocketHub`, `make_websocket_router`, `broadcast` / `send_to`, heartbeat, bearer auth |
| **[Queue & Tasks »](queue-tasks.md)** | FastStream (`AsyncBrokerManager`), TaskIQ (`AsyncTaskBrokerManager`), `AsyncTaskScheduler`, transactional outbox |
| **[Transactional outbox »](outbox.md)** | `BaseOutboxModel`, `OutboxRelay`, `save_with_outbox` — reliable events |
| **[Transactional email »](email.md)** | `EmailUtils` — SMTP, text/HTML body, attachments, Jinja2 templates |
| **[Web Push »](webpush.md)** | `WebPushDispatcher`, VAPID schemas, broadcast with pruning |
| **[Chat (conversations + messages) »](chat.md)** | `ChatService`, `make_chat_router`, base tables + real-time fan-out via `SSEBroker` |
| **[Comments + ratings »](reviews.md)** | `ReviewService`, `make_reviews_router`, 0–5 star scores with aggregation, threaded comments |
| **[Computer vision (ONNX) »](vision.md)** | `Detector` / `Classifier` / `Segmenter` + prediction schemas |
| **[Logging »](logging.md)** | `LogUtils`, structured JSON logging, request-ID propagation |
| **[Metrics »](metrics.md)** | `MetricsUtils` — CPU / RAM / disk / GPU snapshots |
| **[Observability (tracing) »](observability.md)** | `setup_tracing` (OpenTelemetry), `SlowQueryLogger` |
| **[Admin site »](admin.md)** | `AdminSite`, `AdminModel`, `make_admin_router`, `BaseUserModel` |
| **[Downloads »](downloads.md)** | `DownloadUtils` — `file_response`, `stream`, `build_content_disposition`, path-traversal safe |
| **[Uploads (backends) »](uploads.md)** | `UploadUtils`, extension/MIME validation (`sniff_mime`), local / MinIO backends |
| **[Storage (MinIO/S3) »](storage.md)** | `AsyncMinIOClient`, `MinIOUploadStorage`, `presigned_get_url` / `presigned_put_url`, `list_objects` |
| **[Stored file (service mixin) »](stored-files.md)** | `StoredFileServiceMixin` — `set_file` / `replace` / `clear_file` over `UploadUtils` |
| **[Utilities »](utilities.md)** | `utcnow`/`to_utc`, `modify_dict`, `get_client_ip`, opaque tokens (`generate_opaque_token`) |
| **[Typing (static + runtime) »](typing.md)** | `strict_types` / `typed` / `require_annotations`, `[tool.tempest] typing_strictness` knob, ruff `ANN` |
| **[Validated fields (ready-made types) »](fields.md)** | Annotated Pydantic types — `PositiveIntField` / `CentsField` / `PriceField` / `SlugField` / `HexColorField` / `CPFField` / `UFField` |
| **[Testing »](testing.md)** | `test_session`, `test_database`, in-memory SQLite, pytest fixtures |
| **[Safe deploys »](deploy-safety.md)** | `AlembicHelper.safe_upgrade` (blocks DROPs), `GracefulShutdownMiddleware` |
| **[CLI »](cli.md)** | `tempest new` / `db` (+ `seed`) / `user` / `secrets rotate` / `lint` / `fix` / `format` / `type` / `test` / `check` |
| **[Security »](security.md)** | `AttemptThrottle`, opaque-token helpers, `HardenedStaticFiles`, security headers |
| **[Brazilian helpers »](br-helpers.md)** | CPF / CNPJ / CEP / phone validation + normalization |

## Anatomy of a recipe

Every recipe follows the same four-section shape so you can skim:

1. **What it solves** — one paragraph in plain language.
2. **When to use it** — bullet list of situations + when *not* to.
3. **The code** — complete, runnable, with `# 1. setup` / `# 2. wire` / `# 3. test` annotations.
4. **Gotchas** — production caveats, security defaults, scaling notes.

If you spot a recipe that doesn't follow this shape, [open an issue](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new) — we treat docs regressions like code regressions.
