# Roadmap

Esta página lista o que o SDK **ainda não oferece** + o que já foi entregue. Ordenado por impacto, não por ordem de implementação — a release atual é puxada pela pressão de negócio, não pela posição na lista.

!!! tip "O que o SDK já cobre"
    Auth completa (JWT/bearer/role/permission/X-Token + bundled signup/activate/login/reset via `UserAuthService` + `make_auth_router`), OAuth2/OIDC (Google/GitHub + genérico), CSRF middleware, DB (`AsyncDatabaseManager` + `BaseRepository` + bulk ops + `AlembicHelper` + `BaseModel` + `BaseUserModel` + `BaseUserTokenModel` + mixins de auditoria/soft-delete + Alembic hook que reordena colunas base), exceções padronizadas, logging estruturado + arquivos por nível + endpoint `/logs`, métricas (CPU/RAM/GPU/Disco + Prometheus `/metrics` + `PrometheusMiddleware`), rate limiting, idempotência (`IdempotencyMiddleware` + memory/Redis stores), body-size limit, paginação (offset + cursor), settings por mixin com `title`/`description`/`examples`, SSE, throttle, upload/download local + storage pluggável (`LocalUploadStorage` + `MinIOUploadStorage`), MinIO/S3 (`AsyncMinIOClient`), WebPush, assinatura de webhook, validadores BR (CPF/CNPJ/CEP/telefone), painel admin (Jinja + HTMX), email (SMTP + Jinja2 templates), cache Redis, fila FastStream, tarefas TaskIQ, hardened static files, runner de servidor, health, tool-spec router, request-id middleware, CORS, HTTP client typed (`HTTPClient` httpx wrapper com retry/backoff/circuit-breaker), CLI completo (`tempest new`, `tempest generate --docker`, `tempest db <subcommand>`, `tempest user <subcommand>`, quality gates).

## Tier S — toda API séria precisa

| Feature | Status | Onde |
|---------|--------|------|
| `IdempotencyMiddleware` + tabela `idempotency_keys` | ✅ v0.24.0 | `tempest_fastapi_sdk.api.middlewares.idempotency` |
| `UploadUtils` com backends pluggáveis (`LocalUploadStorage`, `MinIOUploadStorage`) | ✅ v0.24.0 | `tempest_fastapi_sdk.utils.storage_backends` |
| `HTTPClient` (wrapper typed do httpx) com retry/backoff/circuit-breaker | ✅ v0.28.0 | `tempest_fastapi_sdk.utils.http_client` |
| **OpenTelemetry tracing** — `setup_tracing(app, otlp_endpoint=…)` | ❌ pendente | — |
| **Outbox pattern** — `BaseRepository.save_with_outbox(model, event)` | ❌ pendente | — |

## Tier A — comuns em backend SaaS

| Feature | Status | Onde |
|---------|--------|------|
| `EmailUtils.render_template(path, ctx)` com Jinja2 | ✅ v0.24.0 | `EmailUtils.render_template` + templates bundled |
| OAuth2 / OIDC providers (`GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`) | ✅ v0.29.0 | `tempest_fastapi_sdk.api.oauth` |
| `CSRFMiddleware` + `make_csrf_token_dependency` | ✅ v0.29.0 | `tempest_fastapi_sdk.api.middlewares.csrf` |
| `BodySizeLimitMiddleware` | ✅ v0.28.0 | `tempest_fastapi_sdk.api.middlewares.body_size` |
| `BaseRepository.bulk_create_values / bulk_upsert` | ✅ v0.28.0 | `BaseRepository` |
| Endpoint Prometheus `/metrics` | ✅ v0.28.0 | `tempest_fastapi_sdk.api.routers.metrics` |
| Bundled signup / activate / login / password-reset | ✅ v0.31.0 | `tempest_fastapi_sdk.auth` |
| Modo backend-only (signup / activate / reset renderizado pelo backend) | ✅ v0.32.0 | `tempest_fastapi_sdk.auth` + HTML templates |
| `make_websocket_router` — bearer auth, heartbeat, broadcast | ✅ v0.33.0 | `tempest_fastapi_sdk.websockets` |
| Sessões server-side (alternativa ao JWT) | ✅ v0.34.0 | `tempest_fastapi_sdk.sessions` |
| 2FA / TOTP (`pyotp` wrapper + recovery codes) | ✅ v0.35.0 | `TOTPHelper` + `UserAuthService.mfa_*` + `BaseUserRecoveryCodeModel` |
| `tempest db` + `tempest user` CLI | ✅ v0.30.0 | `tempest_fastapi_sdk.cli.db` / `cli.user` |
| `BaseRepository.bulk_update` (filters + values) | ✅ pré-existente | `BaseRepository.bulk_update` |
| **Escopo multi-tenant** — `TenantScopedRepository(tenant_id)` auto-injetando `WHERE tenant_id = …` em toda query do repository | ❌ planejado v0.36.0 | — |

## Tier B — quando o serviço crescer

| Feature | Status |
|---------|--------|
| `SlowQueryLogger` — evento SQLAlchemy logando query > N ms com `EXPLAIN` | ❌ pendente |
| `AlembicHelper.safe_upgrade()` — bloqueia migrations destrutivas sem `--force` | ❌ pendente |
| Graceful shutdown — drenar requisições in-flight no `SIGTERM` | ❌ pendente |
| F() / Q() expressions wrappers para SQLAlchemy | ❌ pendente |
| eager-load helper (`BaseRepository.get_by_id(id, with_=...)`) | ❌ pendente |
| Signals (`pre_save`/`post_save`/`pre_delete`) via SQLAlchemy events no `BaseRepository` | ❌ pendente |
| Permissions framework granular com object-level (`user.has_perm("order.delete", obj=order)`) | ❌ pendente |
| System checks no startup (`tempest check-config`) | ❌ pendente |
| Management commands framework — projeto registra `tempest <cmd>` próprio | ❌ pendente |
| `tempest db seed` — carregar fixtures JSON/Python | ❌ pendente |
| CLI: `tempest secrets rotate` | ❌ pendente |

## Painel admin — evolução

O painel admin já existe (`AdminSite` / `AdminModel` / `make_admin_router`, Jinja + HTMX, CSRF token). Os itens abaixo elevam ele de "CRUD funcional" para "admin de produção", reaproveitando primitivos que o SDK já tem (`AuditMixin`, `MetricsUtils`, `TOTPHelper`, `UploadUtils`).

| Feature | Por que importa | Reaproveita |
|---------|-----------------|-------------|
| **Filtros / busca / ordenação por coluna** na listagem | Listas grandes ficam inutilizáveis sem isso; é o primeiro pedido de todo operador. | `BaseRepository` (filtros + paginação) |
| **Bulk actions** (deletar / ativar em massa) | Ações linha-a-linha não escalam; selecionar N linhas + uma ação é o fluxo padrão de admin. | `BaseRepository.bulk_update` / soft-delete |
| **Widgets de campo** (FK select, date picker, file upload) | Hoje o form é genérico; FK como `<select>`, data com picker e upload via `UploadUtils` removem digitação manual e erro. | `UploadUtils` + storage backends |
| **Inline / related editing** | Editar filhos (1-N) na mesma tela do pai — padrão Django admin que falta. | `BaseRepository` + relationships |
| **Export CSV / JSON** | Operador exporta o resultado filtrado sem abrir o banco. | listagem + filtros |
| **Audit log visível no admin** | Quem mudou o quê e quando, direto na UI. | `AuditMixin` (`created_by` / `updated_by`) |
| **Dashboard com métricas** | Tela inicial com CPU/RAM/contadores em vez de página vazia. | `MetricsUtils` |
| **MFA no login do admin** | Segundo fator no acesso mais sensível do sistema; encaixe natural agora que o TOTP existe. | `TOTPHelper` + `MFAMixin` + recovery codes |

## Tudo que já entregamos

### ✅ v0.23.0 — Storage MinIO/S3

`AsyncMinIOClient` via extra `[minio]` — bucket lifecycle, object I/O, streaming download, presigned URLs.

### ✅ v0.24.0 — Uploads pluggáveis + idempotência + email templates

- `UploadStorage` protocol + `LocalUploadStorage` + `MinIOUploadStorage`
- `IdempotencyMiddleware` + `MemoryIdempotencyStore` + `RedisIdempotencyStore`
- `EmailUtils.render_template(template, ctx)` com Jinja2 + autoescape

### ✅ v0.25.0 — CLI docker-compose generator

`tempest new` emite `docker-compose.yaml` baseado nos extras escolhidos. Postgres sempre, `[cache]`→Redis, `[queue]`/`[tasks]`→RabbitMQ, `[minio]`→MinIO + bootstrap, `[email]`→MailHog. Tags fixadas. `.env.example` recebe addendum.

### ✅ v0.26.0 — `tempest generate --docker` + image bumps

Regera compose num projeto existente. Postgres 18 / Redis 8 / RabbitMQ 4. Pydantic schemas + settings com `title`/`description`/`examples`.

### ✅ v0.28.0 — Observabilidade + retries

- Endpoint Prometheus `/metrics` + `PrometheusMiddleware`
- `HTTPClient` (wrapper typed do httpx) com retry/backoff/circuit-breaker/`X-Request-ID` propagation
- `BodySizeLimitMiddleware`
- `BaseRepository.bulk_create_values` + `bulk_upsert`

### ✅ v0.29.0 — Security middlewares + OAuth providers

- `CSRFMiddleware` + `make_csrf_token_dependency`
- OAuth2/OIDC: `GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`
- Fix Postgres 18 mount path no docker-compose

### ✅ v0.29.1 — Scaffold com UserModel + admin wiring

`tempest new` agora gera `UserModel` concreto + monta admin out of the box. Default extras `auth,admin`.

### ✅ v0.30.0 — CLI db + user

- `tempest db init/revision/upgrade/downgrade/current/history`
- `tempest user create [--admin]` + `tempest user list [--admin]`
- Resolução `DATABASE_URL` via flag → env → settings → ini

### ✅ v0.30.1 — Alembic reorder hook

`reorder_base_columns_first` hook emite `id`, `is_active`, `created_at`, `updated_at` primeiro em todo `op.create_table` autogerado.

### ✅ v0.30.2 — `sqlalchemy.url` vazio no `alembic.ini`

Credenciais não entram mais no VCS. `env.py` resolve URL em runtime.

### ✅ v0.30.3 — Post-write hooks silenciosos

`ruff_format` antes de `ruff_fix` + `--quiet` em ambos — sem ruído no stdout durante `tempest db revision`.

### ✅ v0.31.0 — Bundled auth flow

- `UserAuthService` — signup / activate / login / request_password_reset / confirm_password_reset
- `make_auth_router` — 5 endpoints prontos pra mount
- `BaseUserTokenModel` + `UserTokenPurpose` (activation/password_reset/email_verification)
- `AuthSettings` mixin — `AUTH_AUTO_ACTIVATE`, `AUTH_RETURN_TOKEN_IN_RESPONSE`, TTLs, URL templates
- Templates Jinja2 bundled (override colocando arquivo de mesmo nome no `template_dir`)

### ✅ v0.31.1 — BaseSchema em tokens + docstrings completas

`ActivationToken` / `PasswordResetToken` rewritten como `BaseSchema` (não mais dataclass). Toda DTO de auth com docstring detalhada.

### ✅ v0.31.2 — `session: AsyncSession` em UserAuthService

`Any` removido — todas as 7 assinaturas do service tipam `AsyncSession`.

## Próximos passos

| Release | Conteúdo |
|---------|----------|
| **v0.32.0+** | OpenTelemetry tracing (`setup_tracing(app, otlp_endpoint=…)`) com auto-instrumentação FastAPI/SQLAlchemy/httpx |
| **v0.33.0+** | Outbox pattern (`BaseRepository.save_with_outbox(model, event)`) drenado por `AsyncBrokerManager` |

!!! note "O roadmap é honesto, não aspiracional"
    Itens fora dos próximos cuts só vão pro changelog quando a pressão de negócio puxar. Esta página é atualizada a cada release — se algo deveria estar aqui e não está, abra uma issue.

## Como pedir uma feature

Abra issue em <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> descrevendo:

1. O caso de uso real (não a solução).
2. O que você faz hoje como workaround.
3. Por que o workaround dói (perf, segurança, ergonomia, manutenção).

Issues com caso de uso concreto sobem na fila — abstrações sem demanda não entram, mesmo quando "fariam sentido".
