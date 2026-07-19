# Endpoint map

The full REST API of the marketplace in a single table, ready to paste into the frontend contract.

!!! note "Conventions"
    - **Auth**: `public` (no token), `user` (JWT bearer), `org:<role>` (org member at minimum role).
    - **Idem**: ✅ = accepts `Idempotency-Key` (cached by `IdempotencyMiddleware`).
    - **Pagination**: cursor by default; offset where explicitly noted.
    - **Errors**: every error follows the SDK envelope `{detail, code, details}`.
    - **`X-NN` rules**: the codes cited in the descriptions (e.g. `rule O-02`, `rule D-01`) refer to the [business rules](business-rules.en.md) page, where each rule is detailed.

## Auth

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| POST | `/auth/signup` | public | ✅ | 201 | User signup. |
| POST | `/auth/login` | public | — | 200 | Email + password login. |
| POST | `/auth/refresh` | public | — | 200 | Swap refresh for a new pair. |
| POST | `/auth/logout` | user | — | 204 | Revoke tokens (`jti` blacklist). |
| GET  | `/users/me` | user | — | 200 | Current user profile. |
| PATCH | `/users/me` | user | — | 200 | Update name / photo. |
| DELETE | `/users/me` | user | — | 204 | Soft-delete (rule U-08). |

## Organizations

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| GET  | `/organizations` | user | — | 200 | List orgs where the user is a member. |
| POST | `/organizations` | user | ✅ | 201 | Create new org (rule O-02 ≤ 2). |
| GET  | `/organizations/{id}` | org:MEMBER | — | 200 | Org detail. |
| PATCH | `/organizations/{id}` | org:OWNER | — | 200 | Update name / slug. |
| DELETE | `/organizations/{id}` | org:OWNER | — | 202 | Soft-delete + async cleanup. |
| POST | `/organizations/{id}/transfer-ownership` | org:OWNER | ✅ | 200 | Transfer OWNER role to another member. |

## Members + invitations

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| GET  | `/organizations/{id}/members` | org:MEMBER | — | 200 | List members + role. |
| PATCH | `/organizations/{id}/members/{user_id}` | org:OWNER | — | 200 | Change role (rule M-06). |
| DELETE | `/organizations/{id}/members/{user_id}` | org:OWNER | — | 204 | Remove member. |
| DELETE | `/organizations/{id}/members/me` | org:MEMBER | — | 204 | Voluntary leave (rule M-08). |
| POST | `/organizations/{id}/invitations` | org:ADMIN | ✅ | 201 | Create invite (rule I-02). |
| GET  | `/organizations/{id}/invitations` | org:ADMIN | — | 200 | List the org's invitations. |
| DELETE | `/organizations/{id}/invitations/{inv_id}` | org:ADMIN | — | 204 | Revoke an invitation. |
| POST | `/invitations/{token}/accept` | user | ✅ | 200 | Accept invitation (rule I-04). |
| POST | `/invitations/{token}/reject` | user | — | 204 | Reject invitation. |

## Public catalog

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| GET  | `/catalog` | public | — | 200 | List active products with stock > 0. Cursor pagination. |
| GET  | `/catalog/{product_id}` | public | — | 200 | Full detail + variants + prices. |
| GET  | `/catalog/{product_id}/reviews` | public | — | 200 | Paginated product reviews. |

## Products (seller)

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| GET  | `/organizations/{id}/products` | org:MEMBER | — | 200 | Own org's catalog (includes inactive). |
| POST | `/organizations/{id}/products` | org:ADMIN | ✅ | 201 | Create product + variants + initial price (atomic). |
| GET  | `/products/{id}` | org:MEMBER | — | 200 | Full detail. |
| PATCH | `/products/{id}` | org:ADMIN | — | 200 | Update metadata. |
| DELETE | `/products/{id}` | org:ADMIN | — | 204 | Soft-delete. |

### Images

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| POST | `/products/{id}/images/presign` | org:ADMIN | — | 200 | Return a presigned PUT URL (15min). |
| PATCH | `/products/{id}/images` | org:ADMIN | — | 200 | Attach a list of `image_keys` to the product (max 8). |
| DELETE | `/products/{id}/images/{key}` | org:ADMIN | — | 204 | Remove the image from MinIO + the list. |

### Variants

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| POST | `/products/{id}/variants` | org:ADMIN | ✅ | 201 | Add a new variant. |
| PATCH | `/variants/{id}` | org:ADMIN | — | 200 | Update attributes. |
| POST | `/variants/{id}/prices` | org:ADMIN | ✅ | 201 | Create a new price version (rule P-05). |
| GET  | `/variants/{id}/prices` | org:MEMBER | — | 200 | Price history. |

## Stock

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| POST | `/variants/{id}/stock/movements` | org:MEMBER | ✅ | 201 | Create a movement (IN/OUT/ADJUST/RESERVATION/RELEASE). |
| GET  | `/variants/{id}/stock/movements` | org:MEMBER | — | 200 | Ledger, cursor pagination. |
| GET  | `/variants/{id}/stock/balance` | org:MEMBER | — | 200 | Derived balance. |

## Cart + Orders (buyer)

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| GET  | `/cart` | user | — | 200 | List user's open carts (1 per org). |
| POST | `/cart/items` | user | ✅ | 201 | Add a variant to the cart of its org. |
| PATCH | `/cart/items/{id}` | user | — | 200 | Update qty. |
| DELETE | `/cart/items/{id}` | user | — | 204 | Remove item. |
| POST | `/orders` | user | ✅ | 201 | Checkout — converts cart into order (rule D-01). |
| GET  | `/orders` | user | — | 200 | List user's orders, cursor pagination. |
| GET  | `/orders/{id}` | user | — | 200 | Detail + items + current status. |
| GET  | `/orders/{id}/events` | user | — | 200 | **SSE** stream of status transitions. |
| POST | `/orders/{id}/cancel` | user (buyer) | ✅ | 200 | Cancel while `PENDING` or `PAID`. |
| POST | `/orders/{id}/confirm-delivery` | user (buyer) | ✅ | 200 | Mark as `DELIVERED`. |

## Orders (seller)

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| GET  | `/organizations/{id}/orders` | org:MEMBER | — | 200 | List org orders, filterable by status. |
| POST | `/orders/{id}/mark-paid` | org:ADMIN | ✅ | 200 | Mock payment (rule D-09). |
| POST | `/orders/{id}/ship` | org:ADMIN | ✅ | 200 | Mark as shipped + tracking. |

## Reviews

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| POST | `/variants/{id}/reviews` | user (with DELIVERED order) | ✅ | 201 | Review variant (rule R-01). |
| GET  | `/variants/{id}/reviews` | public | — | 200 | Public reviews. |
| DELETE | `/reviews/{id}` | user (author) | — | 204 | Delete own review. |

## Notifications (real-time messaging)

A domain event (order paid/shipped, invite, new review) is delivered on two channels with the same payload — **SSE** for the app open (foreground) and **Web Push** for the app closed (background). Details in the "Notifications: SSE + Web Push" flow on [Critical flows](flows.en.md).

| Method | Path | Auth | Idem | Status | Description |
|--------|------|------|------|--------|-------------|
| GET  | `/notifications/stream` | user | — | 200 | **SSE** — per-user channel (`SSEBroker`, channel = `str(user.id)`); returns `broker.response(...)`. Receives all the user's events live. |
| POST | `/push/subscriptions` | user | ✅ | 201 | Register a device for Web Push (`endpoint` + `p256dh`/`auth` keys). `make_web_push_router`. |
| DELETE | `/push/subscriptions` | user | — | 204 | Remove the current device's subscription (by `endpoint`). `make_web_push_router`. |

!!! info "Required extras"
    SSE (`/notifications/stream`) is **core** — no extra needed. The `/push/subscriptions` routes come from `make_web_push_router` and need the `[webpush]` extra (`uv add "tempest-fastapi-sdk[webpush]"`). Multi-worker SSE (Redis fan-out) needs `[cache]`.

## Technical endpoints (SDK)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET  | `/health/liveness` | public | `make_health_router` — alive? |
| GET  | `/health/readiness` | public | alive + DB ok + Redis ok? |
| GET  | `/logs` | X-Token | `make_logs_router` — reads `logs/*.log`. |
| GET  | `/metrics` | public (internal) | Prometheus scrape (v0.26+). |
| GET  | `/admin/*` | admin session | `AdminSite` Jinja+HTMX. |
| GET  | `/docs` | public | Swagger UI. |
| GET  | `/redoc` | public | Redoc. |

## Example: checkout payload

```http
POST /orders HTTP/1.1
Authorization: Bearer <jwt>
Idempotency-Key: chk_3e8f1c2a-9d4b-4f0a-8e7c-1234567890ab
Content-Type: application/json

{
  "cart_id": "0b9bd1b8-7e0e-4c3a-9f2c-8a1234567890",
  "shipping_address": "Av. Paulista 1000, São Paulo SP, 01310-100"
}
```

Response:

```json
{
  "id": "9f8e7d6c-5b4a-3210-fedc-ba9876543210",
  "buyer_id": "...",
  "organization_id": "...",
  "status": "PENDING",
  "total_cents": 18990,
  "items": [
    {
      "variant_id": "...",
      "sku": "TSHIRT-M-BLACK",
      "qty": 2,
      "unit_price_cents": 9495
    }
  ],
  "idempotency_key": "chk_3e8f1c2a-9d4b-4f0a-8e7c-1234567890ab",
  "created_at": "2026-06-04T13:42:18.512Z"
}
```

Retrying with the same `Idempotency-Key` → same response (same `order.id`, same `total_cents`, no new `RESERVATION` in stock).

## Next step

Head back to the [project index](index.en.md) and start implementing — the recommended order is there.
