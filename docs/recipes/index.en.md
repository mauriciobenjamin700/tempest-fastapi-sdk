# Recipes

Bite-sized "I want to wire X" walkthroughs. Each page starts with **what problem it solves**, **when to reach for it**, and a complete code example you can copy verbatim.

!!! tip "When to read what"
    - Just need to look up a signature? Jump to **[Reference »](../reference.md)**.
    - Building a brand-new service? Follow the linear **[Tutorial »](../tutorial.md)** first.
    - Wiring a specific SDK piece? You're in the right place — pick the recipe below.

| Theme | Covers |
| --- | --- |
| **[Database »](database.md)** | `BaseRepository`, `BaseModel`, `AuditMixin` / `SoftDeleteMixin`, cursor pagination, Alembic helper |
| **[HTTP layer »](http.md)** | `apply_cors`, `RequestIDMiddleware`, `RateLimitMiddleware`, `make_health_router`, JWT / role / permission dependencies, webhook signature verifier, pagination Link headers, tool-spec router |
| **[HTTP client (outbound) »](http-client.md)** | `HTTPClient` — typed httpx with retry/backoff, circuit-breaker, X-Request-ID; `RetryPolicy`, `CircuitOpenError` |
| **[Cache »](cache.md)** | `AsyncRedisManager`, `@cached` decorator |
| **[Real-time »](realtime.md)** | Server-Sent Events (`EventStream`, `sse_response`) |
| **[Queue & Tasks »](queue-tasks.md)** | FastStream (`AsyncBrokerManager`), TaskIQ (`AsyncTaskBrokerManager`), `AsyncTaskScheduler`, transactional outbox |
| **[Transactional outbox »](outbox.md)** | `BaseOutboxModel`, `OutboxRelay`, `save_with_outbox` — reliable events |
| **[Transactional email »](email.md)** | `EmailUtils` — SMTP, text/HTML body, attachments, Jinja2 templates |
| **[Web Push »](webpush.md)** | `WebPushDispatcher`, VAPID schemas, broadcast with pruning |
| **[Logging »](logging.md)** | `LogUtils`, structured JSON logging, request-ID propagation |
| **[Metrics »](metrics.md)** | `MetricsUtils` — CPU / RAM / disk / GPU snapshots |
| **[Observability (tracing) »](observability.md)** | `setup_tracing` (OpenTelemetry), `SlowQueryLogger` |
| **[Admin site »](admin.md)** | `AdminSite`, `AdminModel`, `make_admin_router`, `BaseUserModel` |
| **[Downloads »](downloads.md)** | `DownloadUtils` — `file_response`, `stream`, `build_content_disposition`, path-traversal safe |
| **[Utilities »](utilities.md)** | `utcnow`/`to_utc`, `modify_dict`, `get_client_ip`, opaque tokens (`generate_opaque_token`) |
| **[Testing »](testing.md)** | `test_session`, `test_database`, in-memory SQLite, pytest fixtures |
| **[CLI »](cli.md)** | `tempest new` / `lint` / `fix` / `format` / `type` / `test` / `check` |
| **[Security »](security.md)** | `AttemptThrottle`, opaque-token helpers, `HardenedStaticFiles`, security headers |
| **[Brazilian helpers »](br-helpers.md)** | CPF / CNPJ / CEP / phone validation + normalization |

## Anatomy of a recipe

Every recipe follows the same four-section shape so you can skim:

1. **What it solves** — one paragraph in plain language.
2. **When to use it** — bullet list of situations + when *not* to.
3. **The code** — complete, runnable, with `# 1. setup` / `# 2. wire` / `# 3. test` annotations.
4. **Gotchas** — production caveats, security defaults, scaling notes.

If you spot a recipe that doesn't follow this shape, [open an issue](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new) — we treat docs regressions like code regressions.
