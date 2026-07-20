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
| F() / Q() expressions wrappers para SQLAlchemy | ✅ v0.111.0 | `tempest_fastapi_sdk.db` (`F` / `Q`) |
| eager-load helper (`BaseRepository.get_by_id(id, with_=...)`) | ✅ v0.109.0 | `with_=` em `get`/`get_or_none`/`get_by_id`/`first`/`list` |
| Signals (`pre_save`/`post_save`/`pre_delete`/`post_delete`) no `BaseRepository` | ✅ v0.109.0 | `tempest_fastapi_sdk.db.signals` (`connect`/`on_signal`) |
| Permissions framework granular com object-level (`user.has_perm("order.delete", obj=order)`) | ✅ v0.110.0 | `tempest_fastapi_sdk.authz` |
| System checks no startup (`tempest check-config`) | ✅ v0.112.0 | `tempest_fastapi_sdk.checks` |
| Management commands framework — projeto registra `tempest <cmd>` próprio | ✅ v0.113.0 | `[tool.tempest] commands` + `src/commands.py` |

## Painel admin — evolução

O painel admin já existe (`AdminSite` / `AdminModel` / `make_admin_router`, Jinja + HTMX, CSRF token). Os itens abaixo elevam ele de "CRUD funcional" para "admin de produção", reaproveitando primitivos que o SDK já tem (`AuditMixin`, `MetricsUtils`, `TOTPHelper`, `UploadUtils`).

| Feature | Por que importa | Reaproveita |
|---------|-----------------|-------------|
| **Filtros / busca / ordenação por coluna** na listagem | Listas grandes ficam inutilizáveis sem isso; é o primeiro pedido de todo operador. | `BaseRepository` (filtros + paginação) |
| **Bulk actions** (deletar / ativar em massa) | Ações linha-a-linha não escalam; selecionar N linhas + uma ação é o fluxo padrão de admin. | `BaseRepository.bulk_update` / soft-delete |
| **Widgets de campo** (FK select ✅, date picker, file upload) + **FK autocomplete** ✅ v0.115.0 | FK como `<select>`, data com picker, upload via `UploadUtils`; FKs grandes viram caixa de busca HTMX (`autocomplete_fields`). | `UploadUtils` + storage backends |
| **Inline / related editing** ✅ v0.116.0 (leitura + navegar) | Filhos (1-N) listados no detail do pai, com link pro admin do filho e "Add" pré-preenchendo o FK (`inlines=[Inline(...)]`). Edição in-place na mesma tela fica como evolução. | `BaseRepository` + relationships |
| **Export CSV / JSON** | Operador exporta o resultado filtrado sem abrir o banco. | listagem + filtros |
| **Audit log visível no admin** ✅ v0.114.0 | Quem mudou o quê e quando, direto na UI — timeline por registro no detail. | `BaseAuditLogModel` + `diff_snapshots` (`AdminModel(audit_model=...)`) |
| **Dashboard com métricas** (sistema ✅) + **cards de negócio** ✅ v0.117.0 | CPU/RAM/contadores + cards value/trend/partition computados dos seus dados (`AdminSite(dashboard_cards=[...])`). | `MetricsUtils` + `MetricCard` |
| **MFA no login do admin** | Segundo fator no acesso mais sensível do sistema; encaixe natural agora que o TOTP existe. | `TOTPHelper` + `MFAMixin` + recovery codes |

## Tudo que já entregamos

O histórico completo de releases — cada versão com o que entrou em **Added** / **Changed** / **Fixed** — vive no [changelog](changelog.md), no formato [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Ele é a fonte da verdade; esta página só destaca o que ainda falta.

## Próximos passos

A fila "Próximos passos" que restava do backlog Tier S/A/B está
**zerada** — o último item (management commands) entrou na v0.113.0. As
próximas releases voltam a ser puxadas por pressão de negócio.

A evolução do painel admin está **essencialmente completa**: Tiers 1 e 2
inteiros, e o Tier 3 com import CSV (v0.118), RBAC granular (v0.119) e
lenses (v0.120). Seguiu um refino: widgets JSON+time (v0.121), polish de
UX (v0.122) e um [exemplo integrado](admin-showcase.md) que fia tudo num
admin de loja. Com a **edição inline in-place** (v0.127) a evolução do
painel admin está concluída; o que vier é puxado por pressão de negócio.

!!! note "O roadmap é honesto, não aspiracional"
    Itens fora dos próximos cuts só vão pro changelog quando a pressão de negócio puxar. Esta página é atualizada a cada release — se algo deveria estar aqui e não está, abra uma issue.

## Entregue na v0.129.0

SSR — builders tipados de atributos:

| Feature | Status | Onde |
|---------|--------|------|
| **`htmx()` / `aria()` / `data()`** | ✅ v0.129 | Montam o `attrs: dict[str, str]` aberto do widget a partir de args tipados — `hx-*`/`aria-*`/`data-*` deixam de ser dict stringly-typed e viram call-site com autocomplete + checagem estática. Retornam exatamente o dict que você escreveria (mescláveis). Sem mágica, sem dep nova. [Referência »](ssr.md#atributos-tipados-htmx-aria-data) |

## Entregue na v0.128.0

SSR — servir um build compilado do tempestweb:

| Feature | Status | Onde |
|---------|--------|------|
| **`make_web_app_router` + `build_web_app` + `detect_build_mode`** | ✅ v0.128 | Hospeda um artefato `tempestweb build` direto no FastAPI: `make_web_app_router(dir)` serve o build **wasm** (SPA estática) como `APIRouter` com history fallback, MIME certo, cache do shell/SW, sem CSP imposto (Pyodide); `build_web_app(dir)` hospeda o build **server** (WebSocket/SSE) como sub-app. Só serve o `dist/` pronto — build fica no CLI do tempestweb. `[ssr]`. [Receita »](ssr.md) |

## Entregue na v0.127.0

Admin — edição inline in-place:

| Feature | Status | Onde |
|---------|--------|------|
| **`Inline(editable=True, can_delete=True)`** | ✅ v0.127 | O detail do pai renderiza os filhos 1-N como um formset editável (uma linha de inputs por filho + uma linha em branco pra adicionar) que dá POST em `/inlines/<filho>` — editar, adicionar e excluir sem sair da tela. O FK do pai é implícito (forçado ao pai, nunca um input), as linhas são escopadas ao pai, colunas de upload/autocomplete ficam no form próprio do filho, e erros de validação re-renderizam in-place. Requer o admin registrado do filho + `can_edit`/`can_delete`. [Receita »](recipes/admin.md) |

## Entregue na v0.126.0

Utilitários de teste — factories de modelo:

| Feature | Status | Onde |
|---------|--------|------|
| **`ModelFactory` + `seq`** | ✅ v0.126 | Amarra modelo + defaults à sessão: `build` (solta), `create`/`create_many` (add+flush+refresh). Default/override **callable** recebe o índice da linha → campos únicos; `seq("u{n}@x")` é o atalho. Sem mágica: você declara os defaults. `from tempest_fastapi_sdk.testing import ModelFactory, seq`. [Receita »](recipes/testing.md) |

## Entregue na v0.125.0

Webhooks de saída — assinar + entregar com retry:

| Feature | Status | Onde |
|---------|--------|------|
| **`WebhookSender`** | ✅ v0.125 | POST do evento JSON assinado com o mesmo `WebhookSignatureVerifier`; re-tenta transitórios (5xx/429/conexão) com backoff, 4xx não. `send`/`send_many` → `WebhookDelivery`. httpx injetado; casa com o outbox. [Receita »](recipes/http.md) |

## Entregue na v0.124.0

Observabilidade — métricas de negócio custom no `/metrics`:

| Feature | Status | Onde |
|---------|--------|------|
| **`BusinessMetrics`** | ✅ v0.124 | Fábrica tipada de `counter`/`gauge`/`histogram` no registry compartilhado (namespace opcional, dedup por nome); saem no mesmo `GET /metrics`. Objetos são os do `prometheus_client` — sem mágica. [Receita »](recipes/metrics.md) |

## Entregue na v0.123.0

Mais operadores de filtro `campo__op` (no `Q` e no dict do repository):

| Feature | Status | Onde |
|---------|--------|------|
| **Operadores `in`/`notin`/`isnull`/`contains`/`startswith`/`endswith`** | ✅ v0.123 | Somam-se a `gt`/`gte`/`lt`/`lte`/`ne`; `build_filter_condition` (base do `Q` + dict). [Receita »](recipes/database.md) |

## Entregue na v0.122.0

Refino do admin — polish de consistência/UX:

| Feature | Status | Onde |
|---------|--------|------|
| **Polish do admin** | ✅ v0.122 | Corrigido `--tempest-border` (não definido → bordas na cor do texto) + cards/autocomplete que usavam o bg escuro da sidebar; detail reordenado (inlines logo após os campos, audit/history por último) e colunas `JSON` pretty-print no detail. |

## Entregue na v0.121.0

Refino do admin — novos widgets de campo:

| Feature | Status | Onde |
|---------|--------|------|
| **Widgets JSON + time** | ✅ v0.121 | Colunas `JSON` viram um editor JSON monoespaçado (pretty-print ao carregar, parse+validação no submit); colunas `Time` viram `<input type=time>`. [Receita »](recipes/admin.md) |

## Entregue na v0.120.0

Painel admin — lenses / visões salvas (Tier 3), fechando a evolução do admin:

| Feature | Status | Onde |
|---------|--------|------|
| **Lenses** | ✅ v0.120 | `AdminModel(lenses=[Lens("Abertos", filters={"status": "open"}, order_by="-created_at")])` → abas acima da lista; clicar aplica os filtros (ANDeados com busca/filtros do usuário) + ordenação via `?lens=<slug>`. Aba "All" volta ao padrão. [Receita »](recipes/admin.md) |

## Entregue na v0.119.0

Painel admin — RBAC granular (Tier 3):

| Feature | Status | Onde |
|---------|--------|------|
| **RBAC granular** | ✅ v0.119 | `make_admin_router(access_policy=...)` — hook `(principal, admin, AdminPermission)` → bool consultado em toda ação (VIEW/CREATE/EDIT/DELETE). Nega → `403`, e some do dashboard/nav no VIEW. Compõe com os flags `can_*` (ambos precisam liberar). Restringe um admin não-super a subconjuntos de modelo/ação. [Receita »](recipes/admin.md) |

## Entregue na v0.118.0

Painel admin — import CSV (Tier 3), contraparte do export:

| Feature | Status | Onde |
|---------|--------|------|
| **CSV import** | ✅ v0.118 | `AdminModel(can_import=True)` expõe `GET/POST /m/{slug}/import`: sobe um CSV, cada linha é validada/coagida como no create e vira um registro. Relatório com total criado + erros por linha (best-effort: uma linha ruim não aborta as outras). Link "Import CSV" na list view. [Receita »](recipes/admin.md) |

## Entregue na v0.117.0

Painel admin — cards de métricas de negócio no dashboard (fecha o Tier 2
da evolução do admin):

| Feature | Status | Onde |
|---------|--------|------|
| **Dashboard business metrics** | ✅ v0.117 | `AdminSite(dashboard_cards=[MetricCard(label, compute)])` renderiza cards no topo do dashboard, computados dos seus dados: `MetricValue` (número), `MetricTrend` (vs período anterior, com delta/%/direção) e `MetricPartition` (breakdown com barras). Distinto do painel CPU/RAM. Card que falha é pulado (não quebra a página). [Receita »](recipes/admin.md) |

## Entregue na v0.116.0

Painel admin — inlines / relações aninhadas (Tier 2 da evolução do admin):

| Feature | Status | Onde |
|---------|--------|------|
| **Inlines (leitura + navegar)** | ✅ v0.116 | `AdminModel(inlines=[Inline(Child, Child.parent_id)])` lista os filhos 1-N no detail do pai como tabela, com link pro admin do filho e "Add" pré-preenchendo o FK (via query param no create). Reaproveita o `list_display`/CRUD do admin filho. Edição in-place na mesma tela: `editable=True` (v0.127). [Receita »](recipes/admin.md) |

## Entregue na v0.115.0

Painel admin — campos FK com autocomplete (Tier 2 da evolução do admin):

| Feature | Status | Onde |
|---------|--------|------|
| **Autocomplete FK** | ✅ v0.115 | `AdminModel(autocomplete_fields=[...])` troca o `<select>` de todas as linhas por uma caixa de busca HTMX — sem o cap de 1000 linhas nem o fallback de UUID cru. O endpoint `/m/{slug}/autocomplete/{field}` busca nos `search_fields` do admin alvo (ILIKE, OR), limita a 20; o edit pré-preenche o rótulo atual. [Receita »](recipes/admin.md) |

## Entregue na v0.114.0

Painel admin — visualizador de histórico de auditoria por registro
(primeiro item do Tier 1 da evolução do admin):

| Feature | Status | Onde |
|---------|--------|------|
| **Audit history viewer** | ✅ v0.114 | `AdminModel(audit_model=...)` renderiza no detail uma timeline das mudanças do registro, lida do `BaseAuditLogModel` (match `entity` + `entity_id`), com diff campo-a-campo (antes/depois) e ator/data por entrada. Pareie com `BaseRepository(audit_model=...)` + `add_audited`/`update_audited`/`delete_audited`. [Receita »](recipes/admin.md) |

## Entregue na v0.113.0

Framework de management commands — o serviço pluga comandos próprios na
CLI `tempest`:

| Feature | Status | Onde |
|---------|--------|------|
| **Management commands** | ✅ v0.113 | Exponha um `typer.Typer` chamado `commands` em `src/commands.py` (auto-detectado; ou `[tool.tempest] commands = "..."`) → vira `tempest <cmd>`, ao lado dos embutidos. Colisão com embutido → embutido vence (aviso). Typer puro (args/options/tipos/grupos). [Receita »](recipes/management-commands.md) |

## Entregue na v0.112.0

Framework de system checks estilo Django + a CLI `tempest check-config`:

| Feature | Status | Onde |
|---------|--------|------|
| **System checks** | ✅ v0.112 | `tempest_fastapi_sdk.checks`: `@check` registra um validador `(settings) -> [CheckMessage]`; embutidos p/ segredo vazio/fraco, CORS `*`+credenciais, SQLite-em-prod, DEBUG, bind `0.0.0.0`. `tempest check-config` roda tudo (auto-detecta as settings, `--tag`/`--fail-level`, sai ≠ 0 em ERROR); `run_system_checks(settings)` aborta boot mal-configurado no lifespan. [Receita »](recipes/system-checks.md) |

## Entregue na v0.111.0

Wrappers `F` / `Q` estilo Django sobre o SQLAlchemy, plugados no
`BaseRepository`:

| Feature | Status | Onde |
|---------|--------|------|
| **`F` (expressão de coluna)** | ✅ v0.111 | `F("stock") - 1` computa no banco numa instrução — update atômico sem race. Aritmética dos dois lados e entre colunas; resolvido em `bulk_update`. [Receita »](recipes/database.md) |
| **`Q` (condições compostas)** | ✅ v0.111 | `Q(status="open") \| Q(...)`, `&`, `~` para `OR`/`NOT` que o dict de filtros não expressa; mesmas convenções (`campo__gte`, `name` ILIKE, iterável → `IN`). `where=` em `list`/`first`/`get`/`get_or_none`/`count`/`exists`/`paginate`/`delete_many`. [Receita »](recipes/database.md) |

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

## Como pedir uma feature

Abra issue em <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> descrevendo:

1. O caso de uso real (não a solução).
2. O que você faz hoje como workaround.
3. Por que o workaround dói (perf, segurança, ergonomia, manutenção).

Issues com caso de uso concreto sobem na fila — abstrações sem demanda não entram, mesmo quando "fariam sentido".
