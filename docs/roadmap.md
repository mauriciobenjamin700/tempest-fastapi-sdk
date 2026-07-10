# Roadmap

Esta página lista o que o SDK **ainda não oferece** + o que já foi entregue. Ordenado por impacto, não por ordem de implementação — a release atual é puxada pela pressão de negócio, não pela posição na lista.

!!! tip "O que o SDK já cobre"
    Auth completa (JWT/bearer/role/permission/X-Token + bundled signup/activate/login/reset via `UserAuthService` + `make_auth_router`), OAuth2/OIDC (Google/GitHub + genérico), CSRF middleware, DB (`AsyncDatabaseManager` + `BaseRepository` + bulk ops + `AlembicHelper` + `BaseModel` + `BaseUserModel` + `BaseUserTokenModel` + mixins de auditoria/soft-delete + Alembic hook que reordena colunas base), exceções padronizadas, logging estruturado + arquivos por nível + endpoint `/logs`, métricas (CPU/RAM/GPU/Disco + Prometheus `/metrics` + `PrometheusMiddleware`), rate limiting, idempotência (`IdempotencyMiddleware` + memory/Redis stores), body-size limit, paginação (offset + cursor), settings por mixin com `title`/`description`/`examples`, SSE, throttle, upload/download local + storage pluggável (`LocalUploadStorage` + `MinIOUploadStorage`), MinIO/S3 (`AsyncMinIOClient`), WebPush, assinatura de webhook, validadores BR (CPF/CNPJ/CEP/telefone), painel admin (Jinja + HTMX, paridade com Django admin — list view com busca/filtros/colunas ordenáveis, CRUD completo, ações em massa, export CSV/JSON, widgets FK-select, dashboard com contagens + métricas, MFA TOTP no login, trilha de auditoria `created_by`/`updated_by`), email (SMTP + Jinja2 templates), cache Redis, fila FastStream, tarefas TaskIQ, hardened static files, runner de servidor, health, tool-spec router, request-id middleware, CORS, HTTP client typed (`HTTPClient` httpx wrapper com retry/backoff/circuit-breaker), IA generativa self-hosted (`tempest_fastapi_sdk.genai` — hardware-check, LLM local `TextGenerator` + quantização, `Embedder`, RAG web+PDF+vector store, áudio STT/TTS PT-BR/EN-US), CLI completo (`tempest new`, `tempest generate --docker` — credenciais do compose resolvidas do `.env` via `${VAR:-default}`, não hardcoded —, `tempest db <subcommand>`, `tempest user <subcommand>`, quality gates).

## Tier S — toda API séria precisa

| Feature | Status | Onde |
|---------|--------|------|
| `IdempotencyMiddleware` + tabela `idempotency_keys` | ✅ v0.24.0 | `tempest_fastapi_sdk.api.middlewares.idempotency` |
| `UploadUtils` com backends pluggáveis (`LocalUploadStorage`, `MinIOUploadStorage`) | ✅ v0.24.0 | `tempest_fastapi_sdk.utils.storage_backends` |
| `HTTPClient` (wrapper typed do httpx) com retry/backoff/circuit-breaker | ✅ v0.28.0 | `tempest_fastapi_sdk.utils.http_client` |
| **OpenTelemetry tracing** — `setup_tracing(app, otlp_endpoint=…)` | ✅ v0.43.0 | `tempest_fastapi_sdk.api.tracing` |
| **Outbox pattern** — `BaseRepository.save_with_outbox(model, event)` | ✅ v0.44.0 | `BaseRepository.save_with_outbox` + `tempest_fastapi_sdk.db.outbox` |

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
| **Escopo multi-tenant** — `TenantScopedRepository(tenant_id)` auto-injetando `WHERE tenant_id = …` em toda query do repository | ✅ v0.45.0 | `tempest_fastapi_sdk.db.tenant` |

## Tier B — quando o serviço crescer

| Feature | Status | Onde |
|---------|--------|------|
| `SlowQueryLogger` — evento SQLAlchemy logando query > N ms com `EXPLAIN` | ✅ v0.59.1 | `tempest_fastapi_sdk.db.slow_query` |
| `AlembicHelper.safe_upgrade()` — bloqueia migrations destrutivas sem `--force` | ✅ v0.46.0 | `AlembicHelper.safe_upgrade` (`tempest_fastapi_sdk.db.migrations`) |
| Graceful shutdown — drenar requisições in-flight no `SIGTERM` | ✅ v0.46.0 | `GracefulShutdownMiddleware` (`tempest_fastapi_sdk.api.middlewares.graceful`) |
| `tempest db seed` — carregar fixtures JSON/Python | ✅ v0.47.0 | `tempest_fastapi_sdk.cli.db` |
| CLI: `tempest secrets rotate` | ✅ v0.47.0 | `tempest_fastapi_sdk.cli.secrets` |
| F() / Q() expressions wrappers para SQLAlchemy | ❌ pendente | — |
| eager-load helper (`BaseRepository.get_by_id(id, with_=...)`) | ✅ v0.109.0 | `with_=` em `get`/`get_or_none`/`get_by_id`/`first`/`list` |
| Signals (`pre_save`/`post_save`/`pre_delete`/`post_delete`) no `BaseRepository` | ✅ v0.109.0 | `tempest_fastapi_sdk.db.signals` (`connect`/`on_signal`) |
| Permissions framework granular com object-level (`user.has_perm("order.delete", obj=order)`) | ✅ v0.110.0 | `tempest_fastapi_sdk.authz` |
| System checks no startup (`tempest check-config`) | ❌ pendente | — |
| Management commands framework — projeto registra `tempest <cmd>` próprio | ❌ pendente | — |

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

O histórico completo de releases — cada versão com o que entrou em **Added** / **Changed** / **Fixed** — vive no [changelog](changelog.md), no formato [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Ele é a fonte da verdade; esta página só destaca o que ainda falta.

## Próximos passos

Trabalho genuinamente não lançado (posterior à v0.89.0). A ordem segue impacto, não a numeração — a release atual é puxada pela pressão de negócio.

| Release | Conteúdo |
|---------|----------|
| **próximo** | F() / Q() expressions wrappers para SQLAlchemy |
| **futuro** | system checks no startup (`tempest check-config`), management commands framework (`tempest <cmd>` registrado pelo projeto) |

!!! note "O roadmap é honesto, não aspiracional"
    Itens fora dos próximos cuts só vão pro changelog quando a pressão de negócio puxar. Esta página é atualizada a cada release — se algo deveria estar aqui e não está, abra uma issue.

## Entregue na v0.105.0

O plano de ergonomia GenAI + os dois módulos de aplicação abaixo já
**entraram** (antes eram "planejados" aqui):

| Feature | Status | Onde |
|---------|--------|------|
| **`GenerationConfig` tipado** | ✅ v0.105 | Params de geração validados no lugar de `**kwargs`. [Receita »](recipes/genai.md) |
| **`make_genai_router`** | ✅ v0.105 | Endpoints prontos (`/generate`+SSE, `/chat`, `/embed`, `/rag`, `/transcribe`, `/tts`), monta só o que você injeta. [Receita »](recipes/genai.md) |
| **`RedisEmbeddingCache`** | ✅ v0.105 | Cache de vetores async compartilhado entre workers; `Embedder` aceita cache sync ou async. [Receita »](recipes/genai.md) |
| **Chat (`tempest_fastapi_sdk.chat`)** | ✅ v0.105 | `ChatService` + tabelas base + `make_chat_router` + tempo real via `SSEBroker`. [Receita »](recipes/chat.md) |
| **Comentários + avaliações (`reviews`)** | ✅ v0.105 | `ReviewService` (comentar, avaliar 0–5, agregar) + `make_reviews_router`; `RatingField`. [Receita »](recipes/reviews.md) |

## Entregue na v0.110.0

Autorização object-level — a pergunta que o guard estático não responde:
"esse usuário pode editar **esse** objeto?".

| Feature | Status | Onde |
|---------|--------|------|
| **Permissions object-level** | ✅ v0.110 | `tempest_fastapi_sdk.authz`: registre uma regra `(user, obj) -> bool` com `@permission("order.delete")`, cheque com `has_perm`/`check_permission`, proteja a rota com `make_permission_checker`. Bypass de superusuário + fallback estático injetáveis via `PermissionRegistry`; wildcards (`order.*`/`*`); handlers sync ou async; `PermissionMixin` dá `await user.has_perm(...)`. [Receita »](recipes/authz.md) |

## Entregue na v0.109.0

Duas melhorias no `BaseRepository`, ambas puxadas do "Próximos passos"
acima:

| Feature | Status | Onde |
|---------|--------|------|
| **Eager-load (`with_=`)** | ✅ v0.109 | `get`/`get_or_none`/`get_by_id`/`first`/`list` aceitam `with_=["autor", "livros.reviews"]` (paths pontilhados p/ nested); usa `selectinload`, então N relacionados custam 1 query extra, não N. Elimina o `MissingGreenlet` ao acessar relacionamento fora do contexto async. [Receita »](recipes/database.md) |
| **Signals de ciclo de vida** | ✅ v0.109 | `tempest_fastapi_sdk.db.signals`: `connect`/`on_signal`/`disconnect` registram handlers (sync ou async) por modelo para `PRE_SAVE`/`POST_SAVE`/`PRE_DELETE`/`POST_DELETE`. Disparam no caminho unit-of-work (`add`/`update`/`delete`/…); os bulk set-based fazem bypass por design. Um handler de `PRE_SAVE` que levanta veta a escrita. [Receita »](recipes/database.md) |

## Entregue na v0.107.0 / v0.108.0

Paridade GenAI self-hosted ponta a ponta — chat com IA rodando in-process,
para que um microserviço de inferência vire escolha de organização, não
necessidade:

| Feature | Status | Onde |
|---------|--------|------|
| **Backend Ollama** (`OllamaGenerator` / `OllamaEmbedder`) | ✅ v0.107 | HTTP puro (sem torch), drop-in no `make_genai_router` / `Retriever`. Extra `[genai-ollama]`. [Receita »](recipes/genai.md) |
| **Visão + tool-calling no Ollama** | ✅ v0.108 | `generate(images=…)` + `images` por mensagem em `chat()` + `chat_with_tools()`. [Receita »](recipes/genai.md) |
| **STT paridade** | ✅ v0.108 | `beam_size` / `vad_filter` (default + override por chamada) + `language_probability` em `Transcription`. [Receita »](recipes/genai.md) |
| **`ChromaVectorStore`** | ✅ v0.108 | `VectorStore` sobre ChromaDB (efêmero / persistente / client injetado). Extra `[genai-chroma]`. [Receita »](recipes/genai.md) |
| **`ChatMemory`** | ✅ v0.108 | Memória long-term por usuário sobre Chroma: embed + upsert com eviction por cota, busca com filtro de similaridade + decay de recência. [Receita »](recipes/genai.md) |
| **`AIChatPipeline`** | ✅ v0.108 | Orquestrador: memória → web-search → gera (com loop de tool-calling) → TTS → index. `Tool` + `make_ai_chat_router` (`/chat` + `/chat/stream` SSE, stateless). [Receita »](recipes/genai.md) |

## Como pedir uma feature

Abra issue em <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> descrevendo:

1. O caso de uso real (não a solução).
2. O que você faz hoje como workaround.
3. Por que o workaround dói (perf, segurança, ergonomia, manutenção).

Issues com caso de uso concreto sobem na fila — abstrações sem demanda não entram, mesmo quando "fariam sentido".
