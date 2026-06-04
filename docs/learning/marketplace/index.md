# 🛒 Marketplace de produtos

Plataforma multi-tenant de vendas estilo **Mercado Livre / Shopee**, sem integrações externas. O foco é exercitar o `tempest-fastapi-sdk` num cenário realista — auth, multi-tenant, RBAC, idempotência, estoque auditável, pedidos com máquina de estados, SSE pra status em tempo real, uploads via MinIO, email transacional.

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
tempest new marketplace --extras auth,upload,cache,email,minio,queue,tasks,metrics

# 2. Sobe infra
cd marketplace
docker compose up -d

# 3. Configura env
cp .env.example .env

# 4. Roda
uv sync
uv run python main.py
```

Quando o serviço subir você terá:

- **API REST** em `http://localhost:8000`
- **Docs Swagger** em `http://localhost:8000/docs`
- **Painel admin (SDK)** em `http://localhost:8000/admin`
- **MinIO console** em `http://localhost:9001` (`minioadmin/minioadmin`)
- **RabbitMQ UI** em `http://localhost:15672` (`guest/guest`)
- **MailHog UI** em `http://localhost:8025`

## Stack do SDK exercitada

| Necessidade | Primitivo do SDK |
|-------------|------------------|
| Cadastro público + login | `BaseUserModel`, `PasswordUtils`, `JWTUtils`, `make_jwt_user_dependency` |
| Membership multi-tenant | `BaseRepository[T]`, `make_role_dependency`, `make_permission_dependency` |
| Convite de membro | `EmailUtils.render_template` + `generate_opaque_token`/`hash_opaque_token` |
| Variantes + preços versionados | `BaseModel` + `AuditMixin` + relacionamentos SQLAlchemy 2.0 async |
| Estoque append-only | `BaseModel` (movimento) + `BaseRepository.bulk_create` (esperado v0.27+) |
| Checkout sem duplicar | `IdempotencyMiddleware` + `RedisIdempotencyStore` |
| Imagens de produto | `UploadUtils` + `MinIOUploadStorage` + presigned URLs |
| Status do pedido em tempo real | `EventStream`, `sse_response` |
| Notificações async | `AsyncTaskBrokerManager` (TaskIQ) + `AsyncBrokerManager` (FastStream) |
| Cache catálogo público | `AsyncRedisManager`, `@cached` |
| Métricas oncall | `MetricsUtils` (Prometheus endpoint via v0.26+) |
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
    │   │   └── reviews.py         # avaliação pós-entrega
    │   └── dependencies/
    │       ├── auth.py            # current_user, require_org_role
    │       └── controllers.py
    ├── controllers/               # orquestração entre services
    ├── services/                  # lógica de domínio
    ├── schemas/                   # DTOs Pydantic
    ├── core/                      # settings + exceptions + constants
    ├── db/
    │   ├── models/                # ORM
    │   └── repositories/          # queries
    ├── queue/                     # consumers FastStream
    ├── tasks/                     # tarefas TaskIQ
    └── utils/
```

## Ordem recomendada de implementação

1. **Auth** — `User` + signup + login + dependência `current_user`. (Cobre: model, repository, service, controller, router, JWT.)
2. **Organizações + membros** — `Organization` + `Membership` com regra de no máximo 2 orgs por user e 10 membros por org. (Cobre: invariantes de negócio, controle por role.)
3. **Convites** — `Invitation` com token opaco, email Jinja2, expiração 7 dias. (Cobre: emails transacionais.)
4. **Catálogo + produtos** — `Product` + `ProductVariant` + `PriceHistory`. (Cobre: relacionamentos 1-N, soft-delete.)
5. **Estoque** — `StockMovement` (append-only) + saldo derivado via view ou query agregada. (Cobre: auditoria, transações.)
6. **Carrinho + checkout** — `Cart` + `Order` + `OrderItem` com idempotência. (Cobre: state machine, idempotência.)
7. **Status em tempo real via SSE** — stream de eventos de mudança de status pro comprador. (Cobre: SSE.)
8. **Imagens de produto** — upload via `UploadUtils` + `MinIOUploadStorage` + listagem com presigned URL. (Cobre: storage.)
9. **Notificações async** — TaskIQ enviando emails + RabbitMQ publicando eventos pra projeções/relatórios. (Cobre: filas, tarefas.)
10. **Métricas + admin** — endpoint Prometheus + `/admin` listando entidades. (Cobre: observabilidade, admin SDK.)

Cada passo mostra **uma fatia vertical completa** — sobe uma feature ponta-a-ponta antes de adicionar a próxima.
