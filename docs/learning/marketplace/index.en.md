# 🛒 Product marketplace

Multi-tenant sales platform **Mercado Livre / Shopee** style, no external integrations. The point is to exercise `tempest-fastapi-sdk` in a realistic scenario — auth, multi-tenant, RBAC, idempotency, auditable stock, orders with a state machine, real-time SSE status, MinIO uploads, transactional email.

## Project pages

| Page | What's there |
|------|--------------|
| **[Business rules](business-rules.en.md)** | The domain written as prose — who can do what, under which conditions. Read first. |
| **[Domain model](domain.en.md)** | UML class + ER diagrams, every entity explained (attributes, invariants, relationships). |
| **[Critical flows](flows.en.md)** | Sequence diagrams covering signup, member invite, product creation, checkout, shipment. Order and Invitation state machines. |
| **[Endpoint map](api.en.md)** | Table of the whole REST API with method + path + auth + payload + status. |

## Project setup (5 min)

```bash
# 1. Scaffold via SDK
tempest new marketplace --extras auth,upload,cache,email,minio,queue,tasks,metrics

# 2. Boot the infra
cd marketplace
docker compose up -d

# 3. Configure env
cp .env.example .env

# 4. Run
uv sync
uv run python main.py
```

When the service boots you have:

- **REST API** at `http://localhost:8000`
- **Swagger docs** at `http://localhost:8000/docs`
- **SDK admin panel** at `http://localhost:8000/admin`
- **MinIO console** at `http://localhost:9001` (`minioadmin/minioadmin`)
- **RabbitMQ UI** at `http://localhost:15672` (`guest/guest`)
- **MailHog UI** at `http://localhost:8025`

## SDK stack exercised

| Need | SDK primitive |
|------|---------------|
| Public signup + login | `BaseUserModel`, `PasswordUtils`, `JWTUtils`, `make_jwt_user_dependency` |
| Multi-tenant membership | `BaseRepository[T]`, `make_role_dependency`, `make_permission_dependency` |
| Member invitation | `EmailUtils.render_template` + `generate_opaque_token`/`hash_opaque_token` |
| Variants + versioned prices | `BaseModel` + `AuditMixin` + SQLAlchemy 2.0 async relationships |
| Append-only stock | `BaseModel` (movement) + `BaseRepository.bulk_create` (expected v0.27+) |
| Non-duplicating checkout | `IdempotencyMiddleware` + `RedisIdempotencyStore` |
| Product images | `UploadUtils` + `MinIOUploadStorage` + presigned URLs |
| Real-time order status | `EventStream`, `sse_response` |
| Async notifications | `AsyncTaskBrokerManager` (TaskIQ) + `AsyncBrokerManager` (FastStream) |
| Public catalog cache | `AsyncRedisManager`, `@cached` |
| Oncall metrics | `MetricsUtils` (Prometheus endpoint via v0.26+) |
| Standardized errors | `AppException` hierarchy + `register_exception_handlers` |
| Per-level logs + `/logs` | `configure_logging(log_dir=…)` + `make_logs_router` |

## Suggested structure

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
    │   │   ├── organizations.py   # CRUD org + members
    │   │   ├── invitations.py     # invite / accept / revoke
    │   │   ├── catalog.py         # public navigation
    │   │   ├── products.py        # product + variant CRUD (org-only)
    │   │   ├── stock.py           # in / out / adjust
    │   │   ├── cart.py            # buyer cart
    │   │   ├── orders.py          # checkout + status
    │   │   └── reviews.py         # post-delivery reviews
    │   └── dependencies/
    │       ├── auth.py            # current_user, require_org_role
    │       └── controllers.py
    ├── controllers/               # cross-service orchestration
    ├── services/                  # domain logic
    ├── schemas/                   # Pydantic DTOs
    ├── core/                      # settings + exceptions + constants
    ├── db/
    │   ├── models/                # ORM
    │   └── repositories/          # queries
    ├── queue/                     # FastStream consumers
    ├── tasks/                     # TaskIQ tasks
    └── utils/
```

## Recommended implementation order

1. **Auth** — `User` + signup + login + `current_user` dependency. (Covers: model, repository, service, controller, router, JWT.)
2. **Organizations + members** — `Organization` + `Membership` with the 2-orgs-per-user / 10-members-per-org guard. (Covers: domain invariants, role-based access.)
3. **Invitations** — `Invitation` with opaque token, Jinja2 email, 7-day expiry. (Covers: transactional email.)
4. **Catalog + products** — `Product` + `ProductVariant` + `PriceHistory`. (Covers: 1-N relationships, soft-delete.)
5. **Stock** — `StockMovement` (append-only) + balance derived via view or aggregate query. (Covers: auditing, transactions.)
6. **Cart + checkout** — `Cart` + `Order` + `OrderItem` with idempotency. (Covers: state machine, idempotency.)
7. **Real-time status via SSE** — event stream for status changes. (Covers: SSE.)
8. **Product images** — upload via `UploadUtils` + `MinIOUploadStorage` + listing with presigned URL. (Covers: storage.)
9. **Async notifications** — TaskIQ sending emails + RabbitMQ publishing events to projections / reports. (Covers: queues, tasks.)
10. **Metrics + admin** — Prometheus endpoint + `/admin` listing entities. (Covers: observability, SDK admin.)

Each step ships **one complete vertical slice** — one feature end-to-end before adding the next.
