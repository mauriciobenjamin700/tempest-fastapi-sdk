# Learning projects

This section collects didactic projects built **entirely on top of `tempest-fastapi-sdk`** so you can learn the SDK in a practical setting. Each project ships:

- **Business rules** — the "why" before the code.
- **UML diagrams** — domain model (class), ER, sequence diagrams for critical flows.
- **REST endpoint map** with request/response shapes.
- **State machines** for entities with a lifecycle (orders, invitations…).
- **Testing strategy** showing how to exercise each layer.

!!! tip "Why learning projects instead of just recipes?"
    Recipes (in [Recipes](../recipes/index.md)) teach **one piece** of the SDK in isolation. Learning projects show how the pieces **fit together** in a realistic scenario — payments, multi-tenant, RBAC, auditing, stock — without external integrations getting in the way of learning.

## Available projects

### 🛒 [Product marketplace](marketplace/index.en.md)

A Mercado Livre / Shopee–style platform where:

- Users sign up via a public endpoint (`POST /signup`).
- Each user can create **up to 2 organizations** (sellers).
- Each organization can invite **up to 10 members** (owners + admins + collaborators).
- Members register products with variants, versioned prices, and stock control.
- Any logged-in user browses the public catalog and places orders.
- Orders run a state machine (`PENDING → PAID → SHIPPED → DELIVERED` + cancellation).
- Stock is an append-only ledger (auditable).

Covers JWT auth, multi-tenant via memberships, cursor pagination, checkout idempotency, uploads (product images via MinIO), transactional emails (invitations / confirmations), SSE (real-time order status), Prometheus metrics.

## How to use this section

1. Read the **business rules** first — understand the domain before coding.
2. Study the **diagrams** — model + sequence give you the "map".
3. Each rules file points at the SDK primitives that apply (e.g. `BaseUserModel`, `make_jwt_user_dependency`, `IdempotencyMiddleware`).
4. Implement one layer at a time — model → repository → service → controller → router.
5. Compare with the reference repo `tempest-marketplace-example` (link at the end of each project).

## Planned next projects

- **📚 Digital library** — loans, reservations, returns with SLA.
- **📅 Scheduling system** — multi-user calendars, slots, conflicts.
- **💸 Recurring billing** — subscriptions, cycles, dunning, payment webhooks.

Suggestions? Open an issue at <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> with the real use case that would motivate the project.
