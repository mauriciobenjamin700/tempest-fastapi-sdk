# Receitas

Passo a passo curtos no estilo "quero conectar X". Cada página começa com **qual problema resolve**, **quando recorrer a ela** e um exemplo de código completo que você pode copiar literalmente.

!!! tip "Quando ler o quê"
    - Só precisa consultar uma assinatura? Pule para a **[Referência »](../reference.md)**.
    - Construindo um serviço novo do zero? Siga primeiro o **[Tutorial »](../tutorial.md)** linear.
    - Conectando uma peça específica do SDK? Você está no lugar certo — escolha a receita abaixo.

| Tema | Cobre |
| --- | --- |
| **[Banco de dados »](database.md)** | `BaseModel`, `AsyncDatabaseManager`, `BaseRepository` (CRUD + filtros + bulk), paginação offset/cursor, mixins, `AlembicHelper`, `SlowQueryLogger` |
| **[Multi-tenant »](multi-tenant.md)** | `TenantScopedRepository` — isolamento por `tenant_id` em toda query |
| **[Audit trail »](audit-trail.md)** | `BaseAuditLogModel`, `add_audited` / `update_audited` / `delete_audited`, `snapshot_model` / `diff_snapshots` |
| **[Sync offline-first (delta) »](offline-sync.md)** | `BaseRepository.changes_since`, `SyncFilterSchema`, `SyncPaginationSchema`, deltas por cursor + soft-delete |
| **[Camada HTTP »](http.md)** | `apply_cors`, `RequestIDMiddleware`, `RateLimitMiddleware`, `make_health_router`, dependências de JWT / role / permissão, verificador de assinatura de webhook, headers Link de paginação, router de tool-spec |
| **[HTTP client (saída) »](http-client.md)** | `HTTPClient` — httpx tipado com retry/backoff, circuit-breaker, X-Request-ID; `RetryPolicy`, `CircuitOpenError` |
| **[Idempotência »](idempotency.md)** | `IdempotencyMiddleware`, `MemoryIdempotencyStore` / `IdempotencyStore` (Redis) — replay seguro de POST/PUT/PATCH/DELETE |
| **[Cache »](cache.md)** | `AsyncRedisManager`, decorator `@cached`, `CacheInvalidator` (tag/namespace) |
| **[Feature flags »](feature-flags.md)** | `FeatureFlags`, backends env/Redis/composto, `make_flag_dependency` |
| **[Auth flow (signup/reset) »](auth-flow.md)** | `UserAuthService`, `make_auth_router` — signup / ativação / login / reset de senha, entrega de token (bearer/cookie/ambos), `BaseUserModel` |
| **[MFA (TOTP / 2FA) »](mfa.md)** | `MFAMixin`, `TOTPHelper`, endpoints enroll/confirm/verify/disable no `make_auth_router`, códigos de recuperação |
| **[Refresh tokens (rotação/revogação) »](refresh-tokens.md)** | `BaseUserRefreshTokenModel`, `make_user_refresh_token_model`, `issue_token_pair`, rotação + detecção de reuso por família |
| **[Sessões server-side »](sessions.md)** | `SessionMiddleware`, `SessionAuth`, `make_session_router`, `MemorySessionStore` / `RedisSessionStore` |
| **[Tempo real »](realtime.md)** | Visão geral — quando escolher SSE, WebSocket ou Web Push |
| **[Server-Sent Events (SSE) »](sse.md)** | `EventStream`, `sse_response`, `ServerSentEvent`, `SSEBroker` (fan-out por canal, ponte Redis) |
| **[WebSocket router »](websocket.md)** | `WebSocketHub`, `make_websocket_router`, `broadcast` / `send_to`, heartbeat, auth via bearer |
| **[Frontend tempestweb + SDK »](tempestweb-frontend.md)** | Frontend tempestweb chamando o backend do SDK: `tempestweb.native.http`, `Idempotency-Key` + `IdempotencyMiddleware`, retry, mesma origem vs CORS |
| **[Fila e Tarefas »](queue-tasks.md)** | FastStream (`AsyncBrokerManager`), TaskIQ (`AsyncTaskBrokerManager`), `AsyncTaskScheduler`, outbox transacional |
| **[Outbox transacional »](outbox.md)** | `BaseOutboxModel`, `OutboxRelay`, `save_with_outbox` — eventos confiáveis |
| **[Email transacional »](email.md)** | `EmailUtils` — SMTP, corpo texto/HTML, anexos, templates Jinja2 |
| **[Web Push »](webpush.md)** | `WebPushDispatcher`, schemas VAPID, broadcast com poda |
| **[Chat (conversas + mensagens) »](chat.md)** | `ChatService`, `make_chat_router`, tabelas base + fan-out em tempo real via `SSEBroker` |
| **[Comentários + avaliações »](reviews.md)** | `ReviewService`, `make_reviews_router`, notas 0–5 estrelas com agregação, comentários encadeados |
| **[Visão computacional (ONNX) »](vision.md)** | `Detector` / `Classifier` / `Segmenter` + schemas de predição |
| **[Logging »](logging.md)** | `LogUtils`, logging JSON estruturado, propagação de request-ID |
| **[Métricas »](metrics.md)** | `MetricsUtils` — snapshots de CPU / RAM / disco / GPU |
| **[Observabilidade (tracing) »](observability.md)** | `setup_tracing` (OpenTelemetry), `SlowQueryLogger` |
| **[Painel admin »](admin.md)** | `AdminSite`, `AdminModel`, `make_admin_router`, `BaseUserModel` |
| **[Downloads »](downloads.md)** | `DownloadUtils` — `file_response`, `stream`, `build_content_disposition`, anti path-traversal |
| **[Uploads (backends) »](uploads.md)** | `UploadUtils`, validação de extensão/MIME (`sniff_mime`), backends local / MinIO |
| **[Storage (MinIO/S3) »](storage.md)** | `AsyncMinIOClient`, `MinIOUploadStorage`, `presigned_get_url` / `presigned_put_url`, `list_objects` |
| **[Arquivo no serviço (mixin) »](stored-files.md)** | `StoredFileServiceMixin` — `set_file` / `replace` / `clear_file` sobre `UploadUtils` |
| **[Utilitários »](utilities.md)** | `utcnow`/`to_utc`, `modify_dict`, `get_client_ip`, tokens opacos (`generate_opaque_token`) |
| **[Tipagem (estático + runtime) »](typing.md)** | `strict_types` / `typed` / `require_annotations`, knob `[tool.tempest] typing_strictness`, ruff `ANN` |
| **[Campos validados (tipos prontos) »](fields.md)** | tipos Pydantic Annotated — `PositiveIntField` / `CentsField` / `PriceField` / `SlugField` / `HexColorField` / `CPFField` / `UFField` |
| **[Testes »](testing.md)** | `test_session`, `test_database`, SQLite em memória, fixtures pytest |
| **[Deploy seguro »](deploy-safety.md)** | `AlembicHelper.safe_upgrade` (barra DROPs), `GracefulShutdownMiddleware` |
| **[CLI »](cli.md)** | `tempest new` / `db` (+ `seed`) / `user` / `secrets rotate` / `lint` / `fix` / `format` / `type` / `test` / `check` |
| **[Segurança »](security.md)** | `AttemptThrottle`, helpers de token opaco, `HardenedStaticFiles`, headers de segurança |
| **[Helpers brasileiros »](br-helpers.md)** | validação + normalização de CPF / CNPJ / CEP / telefone |

## Anatomia de uma receita

Toda receita segue o mesmo formato de quatro seções para você bater o olho:

1. **O que resolve** — um parágrafo em linguagem simples.
2. **Quando usar** — lista de situações + quando *não* usar.
3. **O código** — completo, executável, com anotações `# 1. setup` / `# 2. wire` / `# 3. test`.
4. **Pegadinhas** — ressalvas de produção, defaults de segurança, notas de escala.

Se você encontrar uma receita que não segue esse formato, [abra uma issue](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new) — tratamos regressões de doc como regressões de código.
