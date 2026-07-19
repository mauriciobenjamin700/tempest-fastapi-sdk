# 🛒 Marketplace de produtos

Plataforma multi-tenant de vendas estilo **Mercado Livre / Shopee**, sem integrações externas. O foco é exercitar o `tempest-fastapi-sdk` num cenário realista — auth, multi-tenant, RBAC, idempotência, estoque auditável, pedidos com máquina de estados, mensageria em tempo real (SSE + Web Push) pra status e notificações, uploads via MinIO, email transacional.

## Páginas do projeto

| Página | O que tem |
|--------|-----------|
| **[Regras de negócio](business-rules.md)** | O domínio escrito em prosa — quem pode fazer o quê, sob quais condições. Leia antes de qualquer coisa. |
| **[Modelo de domínio](domain.md)** | Diagramas UML de classe + ER, com cada entidade explicada (atributos, invariantes, relacionamentos). |
| **[Fluxos críticos](flows.md)** | Diagramas de sequência cobrindo signup, convite de membro, criação de produto, checkout, expedição. State machines de Order e Invitation. |
| **[Mapa de endpoints](api.md)** | Tabela de toda a API REST com método + path + auth + payload + status. |

## Setup do projeto (5 min)

```bash
# 1. Scaffold via SDK
tempest new marketplace --extras auth,admin,upload,cache,email,minio,queue,tasks,metrics,prometheus,http,webpush

# 2. Sobe infra
cd marketplace
docker compose up -d

# 3. Configura env
cp .env.example .env
uv sync

# 4. Migrations (cria o schema, incluindo a tabela users)
uv run tempest db revision -m "init"
uv run tempest db upgrade

# 5. Cria o primeiro admin pra logar no /admin
uv run tempest user create --email admin@local --password admin-pass-12 --admin

# 6. Roda
uv run python main.py
```

Quando o serviço subir você terá:

- **API REST** em `http://localhost:8000`
- **Docs Swagger** em `http://localhost:8000/docs`
- **Painel admin (SDK)** em `http://localhost:8000/admin`
- **MinIO console** em `http://localhost:9001` (`minioadmin/minioadmin`)
- **RabbitMQ UI** em `http://localhost:15672` (`guest/guest`)
- **MailHog UI** em `http://localhost:8025`

!!! warning "Credenciais só de desenvolvimento local"
    Os logins e senhas acima (`minioadmin/minioadmin`, `guest/guest`, o admin `admin-pass-12`) são padrões de desenvolvimento local. Nunca faça deploy com eles: rotacione ou substitua todos por segredos fortes antes de qualquer ambiente compartilhado ou de produção.

## Stack do SDK exercitada

| Necessidade | Primitivo do SDK |
|-------------|------------------|
| Cadastro público + login + reset de senha | `UserAuthService` + `make_auth_router` (signup / activate / login / password-reset prontos desde v0.31.0) + `BaseUserModel` + `BaseUserTokenModel` |
| Refresh de access token via JWT pair | `JWTUtils` + `make_jwt_user_dependency` |
| Membership multi-tenant | `BaseRepository[T]`, `make_role_dependency`, `make_permission_dependency` |
| Convite de membro | `EmailUtils.render_template` + `generate_opaque_token`/`hash_opaque_token` |
| Variantes + preços versionados | `BaseModel` + `AuditMixin` + relacionamentos SQLAlchemy 2.0 async |
| Estoque append-only com seed em massa | `BaseModel` (movimento) + `BaseRepository.bulk_create_values` / `bulk_upsert` (v0.28+) |
| Checkout sem duplicar | `IdempotencyMiddleware` + `RedisIdempotencyStore` |
| Limite de tamanho do body do upload | `BodySizeLimitMiddleware` (v0.28+) |
| Imagens de produto | `UploadUtils` + `MinIOUploadStorage` + presigned URLs |
| Status do pedido em tempo real (app aberto) | `SSEBroker` — canal por usuário (`str(user.id)`); `broker.response(channel)` monta a `StreamingResponse` |
| Notificação com app fechado (background) | `WebPushDispatcher` + `WebPushSubscriptionService` + `make_web_push_router` (extra `[webpush]`) |
| Um evento de domínio em dois canais (foreground + background) | `NotificationService` custom fazendo fan-out SSE + Web Push com o mesmo payload |
| Notificações async | `AsyncTaskBrokerManager` (TaskIQ) + `AsyncBrokerManager` (FastStream) |
| Cache catálogo público | `AsyncRedisManager`, `@cached` |
| Métricas oncall | `PrometheusMiddleware` + `make_prometheus_router` (v0.28+) e `MetricsUtils` |
| OAuth social (Google / GitHub) | `GoogleOAuthClient` / `GitHubOAuthClient` / `OIDCProvider` (v0.29+) |
| Forms server-rendered seguros | `CSRFMiddleware` (v0.29+) |
| Chamadas a serviços externos | `HTTPClient` (v0.28+) com retry + circuit-breaker |
| Erros padronizados | `AppException` hierárquica + `register_exception_handlers` |
| Logs por nível + `/logs` | `configure_logging(log_dir=…)` + `make_logs_router` |

## Estrutura sugerida

```text
marketplace/
├── main.py
├── docker-compose.yaml
├── pyproject.toml
├── alembic/
└── src/
    ├── api/
    │   ├── app.py
    │   ├── routers/
    │   │   ├── auth.py            # signup + login + refresh
    │   │   ├── organizations.py   # CRUD org + membros
    │   │   ├── invitations.py     # convidar / aceitar / revogar
    │   │   ├── catalog.py         # navegação pública
    │   │   ├── products.py        # CRUD produto + variante (org-only)
    │   │   ├── stock.py           # entrada / saída / ajuste
    │   │   ├── cart.py            # carrinho do comprador
    │   │   ├── orders.py          # checkout + status
    │   │   ├── reviews.py         # avaliação pós-entrega
    │   │   ├── notifications.py   # GET /notifications/stream (SSE)
    │   │   └── push.py            # make_web_push_router (subscriptions)
    │   └── dependencies/
    │       ├── auth.py            # current_user, require_org_role
    │       └── controllers.py
    ├── controllers/               # orquestração entre services
    ├── services/                  # lógica de domínio
    │   └── notification.py        # NotificationService (fan-out SSE + Web Push)
    ├── schemas/                   # DTOs Pydantic
    ├── core/                      # settings + exceptions + constants
    ├── db/
    │   ├── models/                # ORM (inclui push_subscription.py)
    │   └── repositories/          # queries
    ├── queue/                     # consumers FastStream
    ├── tasks/                     # tarefas TaskIQ
    └── utils/
```

## Ordem recomendada de implementação

1. **Auth** — `UserModel(BaseUserModel)` + `UserTokenModel(BaseUserTokenModel)` + montar `UserAuthService` + `make_auth_router`. Five endpoints prontos: `/auth/signup`, `/auth/activate/{token}`, `/auth/login`, `/auth/password-reset/request`, `/auth/password-reset/confirm`. (Cobre: model abstrato concretizado, fluxo bundled, JWT pair, templates Jinja2.) Veja **[Receita Auth flow »](../../recipes/auth-flow.md)**.
2. **Organizações + membros** — `Organization` + `Membership` com regra de no máximo 2 orgs por user e 10 membros por org. (Cobre: invariantes de negócio, controle por role.)
3. **Convites** — `Invitation` com token opaco, email Jinja2, expiração 7 dias. (Cobre: emails transacionais.)
4. **Catálogo + produtos** — `Product` + `ProductVariant` + `PriceHistory`. (Cobre: relacionamentos 1-N, soft-delete.)
5. **Estoque** — `StockMovement` (append-only) + saldo derivado via view ou query agregada. (Cobre: auditoria, transações.)
6. **Carrinho + checkout** — `Cart` + `Order` + `OrderItem` com idempotência. (Cobre: state machine, idempotência.)
7. **Mensageria em tempo real (SSE + Web Push)** — um `NotificationService` faz fan-out de cada evento de domínio (pedido pago/expedido, convite, novo review) em dois canais com o mesmo payload: `SSEBroker` pro comprador com o app aberto e Web Push (VAPID) pros dispositivos com o app fechado. (Cobre: SSE, Web Push.) SSE é core; Web Push precisa de `uv add "tempest-fastapi-sdk[webpush]"`; SSE multi-worker precisa de `[cache]`. Veja **[Receita SSE »](../../recipes/sse.md)** e **[Receita Web Push »](../../recipes/webpush.md)**.
8. **Imagens de produto** — upload via `UploadUtils` + `MinIOUploadStorage` + listagem com presigned URL. (Cobre: storage.)
9. **Notificações async** — TaskIQ enviando emails + RabbitMQ publicando eventos pra projeções/relatórios. (Cobre: filas, tarefas.)
10. **Métricas + admin** — endpoint Prometheus + `/admin` listando entidades. (Cobre: observabilidade, admin SDK.)

Cada passo mostra **uma fatia vertical completa** — sobe uma feature ponta-a-ponta antes de adicionar a próxima.
