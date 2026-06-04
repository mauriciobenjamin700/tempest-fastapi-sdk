# Roadmap

Esta página lista o que o SDK **ainda não oferece** mas tem demanda real em serviços de produção. Está ordenado por impacto, não por ordem de implementação — a release atual é puxada pela pressão de negócio, não pela posição na lista.

!!! tip "O que o SDK já cobre"
    Auth (JWT/bearer/role/permission/X-Token), DB (`AsyncDatabaseManager` + `BaseRepository` + `AlembicHelper` + `BaseModel` + mixins de auditoria/soft-delete), exceções padronizadas, logging estruturado + arquivos por nível + endpoint `/logs`, métricas (CPU/RAM/GPU/Disco), rate limiting, paginação (offset + cursor), settings por mixin, SSE, throttle, upload/download local, **storage MinIO/S3 (`AsyncMinIOClient` via extra `[minio]`)**, WebPush, assinatura de webhook, validadores BR (CPF/CNPJ/CEP/telefone), painel admin (Jinja + HTMX), email (SMTP), cache Redis, fila FastStream, tarefas TaskIQ, static files endurecidos, runner de servidor, health, tool-spec router, request-id middleware, CORS, CLI scaffolder.

## Tier S — toda API séria precisa

| Feature | Por que importa |
|---------|-----------------|
| **`IdempotencyMiddleware`** + tabela `idempotency_keys` | Header `Idempotency-Key` obrigatório em POST de pagamento/webhook/retry. Sem ele, cliente retentando duplica linha no banco. Padrão Stripe/AWS. |
| **`UploadUtils` com backends pluggáveis** — `LocalBackend`, `S3Backend(bucket, region)`, `GCSBackend` | Hoje `UploadUtils` só grava em disco local. ⚠️ **Cliente MinIO/S3 standalone já entregue na v0.23.0** via `AsyncMinIOClient` (`[minio]` extra) — falta plugar como backend do `UploadUtils`. |
| **OpenTelemetry tracing** — `setup_tracing(app, otlp_endpoint=…)` | `RequestIDMiddleware` correlaciona log mas não dá span cross-service. Precisa de auto-instrumentação FastAPI/SQLAlchemy/httpx. |
| **`HTTPClient` (wrapper typed do httpx)** | Retry + backoff, propagação de `X-Request-ID`, circuit-breaker, timeout default. Hoje cada serviço roda httpx solto. |
| **Outbox pattern** — `BaseRepository.save_with_outbox(model, event)` | Persiste evento na mesma transação do `INSERT`; `AsyncBrokerManager` drena. Sem isso, evento se perde quando o broker falha após o commit. |

## Tier A — comuns em backend SaaS

| Feature | Por que importa |
|---------|-----------------|
| **`EmailUtils.render_template(path, ctx)`** com Jinja2 | Emails de boas-vindas/reset/verify — hoje SMTP só aceita string crua. |
| **OAuth2 / OIDC providers** — `GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider(discovery_url)` | `JWTUtils` só assina nossos próprios tokens; não temos cola pra login social. |
| **`CSRFMiddleware` + `BodySizeLimitMiddleware`** | Admin hoje sem token CSRF; sem body limit = DoS via upload gigante antes do `UploadUtils.max_size_bytes` ser checado. |
| **`BaseRepository.bulk_create / bulk_update / bulk_upsert`** | Insert linha-a-linha é o gargalo #1 de N+1. SQLAlchemy 2.0 tem `insert().values([...])` + `on_conflict_do_update`. |
| **Endpoint Prometheus `/metrics`** | `MetricsUtils` já coleta os dados — falta exportar no formato Prometheus pro oncall fazer scrape. |
| **CSRF do admin + `make_csrf_token_dependency`** | Admin aceita POST sem token hoje. |

## Tier B — quando o serviço crescer

- **2FA / TOTP** (`pyotp` wrapper + `AdminModel.totp_secret` opcional)
- **Escopo multi-tenant** — `TenantScopedRepository(tenant_id)` auto-injetando `WHERE tenant_id = …` em todo query
- **`SlowQueryLogger`** — evento SQLAlchemy logando query > N ms com `EXPLAIN`
- **`AlembicHelper.safe_upgrade()`** — bloqueia migrations destrutivas (DROP COLUMN/TABLE) sem flag `--force`
- **Graceful shutdown** — drenar requisições in-flight no `SIGTERM` antes do uvicorn morrer
- **`make_websocket_router`** — auth bearer, heartbeat, broadcast (hoje só temos SSE)
- **CLI:** `tempest db seed`, `tempest user create-admin`, `tempest secrets rotate`

## Plano de release

### ✅ v0.23.0 — Storage MinIO/S3 (entregue)

- `AsyncMinIOClient` (extra `[minio]`) — bucket, object I/O, streaming, presigned URLs

### ✅ v0.24.0 — Uploads pluggáveis + idempotência + email templates (entregue)

- `UploadStorage` protocol + `LocalUploadStorage` + `MinIOUploadStorage`
- `IdempotencyMiddleware` + `MemoryIdempotencyStore` + `RedisIdempotencyStore`
- `EmailUtils.render_template(template, ctx)` com Jinja2 + autoescape

### v0.25.0 — CLI docker-compose generator

`tempest new` emite `docker-compose.yaml` baseado nos extras instalados — só sobe Postgres se tem `[admin]`/`[db]`, só sobe Redis se tem `[cache]`, etc.

### v0.26.0+ — observabilidade + retries

- `setup_tracing(app, otlp_endpoint=…)` com auto-instrumentação OTel
- `HTTPClient` (wrapper typed do httpx) — retry, backoff, propagação de `X-Request-ID`
- Endpoint Prometheus `/metrics` (com base no `MetricsUtils`)

!!! note "O roadmap é honesto, não aspiracional"
    Itens além de v0.24.0 só vão pro changelog quando a pressão de
    negócio puxar o próximo. Esta página é atualizada a cada
    release — se algo deveria estar aqui e não está, abra uma
    issue.

## Como pedir uma feature

Abra issue em <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> descrevendo:

1. O caso de uso real (não a solução).
2. O que você faz hoje como workaround.
3. Por que o workaround dói (perf, segurança, ergonomia, manutenção).

Issues com caso de uso concreto sobem na fila — abstrações sem
demanda não entram, mesmo quando "fariam sentido".
