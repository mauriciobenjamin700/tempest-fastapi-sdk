# Business rules — Marketplace

This page is the **domain contract**. Every modeling, endpoint, or test decision in the project must be justifiable by a rule listed here. When the code disagrees with the rule, the code is wrong (or the rule needs an RFC before it changes).

!!! warning "Severity convention"
    **MUST** = domain invariant, violation is a bug.
    **MAY** = intentional opening, decided case by case.

## 1. Users

| ID | Rule |
|----|------|
| **U-01** | Anyone **MUST** be able to sign up via `POST /auth/signup` without authentication. |
| **U-02** | Email **MUST** be unique (case-insensitive, stored lowercase). |
| **U-03** | Password **MUST** be ≥ 12 characters and hashed with bcrypt (`PasswordUtils.hash`). |
| **U-04** | Signup **MUST** return `201` with `{user_id, access_token, refresh_token}`. |
| **U-05** | Login (`POST /auth/login`) **MUST** accept email+password and return both tokens. |
| **U-06** | Refresh (`POST /auth/refresh`) **MUST** validate `refresh_token` and emit a new pair. |
| **U-07** | User **MAY** update name / photo via `PATCH /users/me`. Email is immutable. |
| **U-08** | Soft-delete (`DELETE /users/me`) **MUST** flip `is_active=False` and revoke issued tokens via `jti` blacklist. |

**Rationale:** public signup is the funnel trigger. Unique email prevents duplicates; bcrypt prevents password leak on breach. Soft-delete preserves referential integrity (old orders keep pointing at the now-inactive user).

## 2. Organizations

| ID | Rule |
|----|------|
| **O-01** | Any user **MUST** be able to create an organization via `POST /organizations`. |
| **O-02** | A user **MUST NOT** create more than **2 active organizations** simultaneously (count `Organization` rows with `owner_id = user.id AND is_active = true`). |
| **O-03** | Creator becomes the **OWNER** automatically — `Membership(role=OWNER)` in the same create transaction. |
| **O-04** | Each org **MUST** have exactly 1 OWNER. Transfer via `POST /organizations/{id}/transfer-ownership` (target must already be a member). |
| **O-05** | OWNER **MAY** delete the organization (soft-delete). Deletion does not free the slot until the async cleanup (TaskIQ) zeroes out members and converts open orders to cancelled. |
| **O-06** | Org slug **MUST** be globally unique (`acme-supplies`). |

**Rationale:** the 2-org cap blocks mass-creation abuse for quota resets. Single OWNER eliminates governance conflicts.

## 3. Members and roles

| ID | Rule |
|----|------|
| **M-01** | Each org **MUST** have at most **10 active members** (including the OWNER). |
| **M-02** | Roles: `OWNER`, `ADMIN`, `MEMBER`. Hierarchy: `OWNER > ADMIN > MEMBER`. |
| **M-03** | `OWNER` **MAY** do anything inside the org. |
| **M-04** | `ADMIN` **MAY** invite/remove members (except the OWNER), CRUD products/prices/stock, manage orders. |
| **M-05** | `MEMBER` **MAY** only read + edit products/stock (no membership / billing changes). |
| **M-06** | Role changes **MUST** come from the OWNER. An ADMIN **MUST NOT** promote another to ADMIN. |
| **M-07** | A user **MAY** be a member of up to 5 organizations total. |
| **M-08** | Voluntary exit (`DELETE /organizations/{id}/members/me`) **MUST** be blocked for the OWNER — transfer first. |

**Rationale:** caps prevent sprawl. Minimal RBAC (3 roles) covers 90% of cases without turning into Active Directory.

## 4. Invitations

| ID | Rule |
|----|------|
| **I-01** | OWNER/ADMIN **MAY** create an invitation via `POST /organizations/{id}/invitations` with `{email, role}`. |
| **I-02** | Invitation generates an opaque token (`generate_opaque_token(48)`), stores the hash (`hash_opaque_token`), and sends the link via email (Jinja2 via `EmailUtils.render_template`). |
| **I-03** | Invitation **MUST** expire in **7 days** (`expires_at`). |
| **I-04** | `POST /invitations/{token}/accept` **MUST** validate: invitation valid + not expired + logged-in user has the same email. |
| **I-05** | Acceptance creates `Membership(role=invite.role)` in the same transaction that marks the invitation `ACCEPTED`. |
| **I-06** | Duplicate invite for the same email **MUST** invalidate the previous one before creating the new one (`status=SUPERSEDED`). |
| **I-07** | OWNER/ADMIN **MAY** revoke via `DELETE /invitations/{id}` before acceptance (`status=REVOKED`). |
| **I-08** | Acceptance **MUST** fail with `409` when the org already maxed out at 10 members. |

## 5. Catalog and products

| ID | Rule |
|----|------|
| **P-01** | Product **MUST** belong to an organization (`Product.organization_id NOT NULL`). |
| **P-02** | Each product **MUST** have ≥ 1 variant. Catalog never shows a product without variants. |
| **P-03** | Variant **MUST** have a unique SKU within the organization. |
| **P-04** | Variant carries free-form attributes `dict[str, str]` (color/size/voltage). |
| **P-05** | Price **MUST** be versioned — changing emits a new row in `PriceHistory(variant_id, amount_cents, currency, valid_from)`. History is never deleted. |
| **P-06** | Display price **MUST** be the latest record with `valid_from <= now()`. |
| **P-07** | Public catalog (`GET /catalog`) **MUST** filter `is_active=True AND organization.is_active=True AND variant.stock > 0`. |
| **P-08** | Each product **MAY** carry up to **8 images** (`UploadUtils + MinIOUploadStorage`). |
| **P-09** | Images **MUST** be served via presigned URL with 1h TTL. |

## 6. Stock

| ID | Rule |
|----|------|
| **S-01** | Stock **MUST** be an append-only ledger — `StockMovement(variant_id, kind, qty, reason, ref_type, ref_id, created_at)`. |
| **S-02** | `kind ∈ {IN, OUT, ADJUST, RESERVATION, RELEASE}`. Quantity is always positive; the sign is implied by `kind`. |
| **S-03** | Current balance = SUM(IN + RELEASE - OUT - RESERVATION + ADJUST*sign). |
| **S-04** | `OUT` or `RESERVATION` **MUST** fail if the resulting balance would go negative. |
| **S-05** | Every stock change **MUST** carry `reason` (string) and `ref_type/ref_id` pointing to the origin entity (e.g. `order_id`). |
| **S-06** | Manual `ADJUST` **MUST** require ADMIN+ and record `audit_user_id`. |

## 7. Cart

| ID | Rule |
|----|------|
| **C-01** | Each user **MUST** have at most **1 open cart per organization**. Items from different orgs live in separate carts (ML/Shopee style). |
| **C-02** | Adding an item (`POST /cart/items`) **MUST** validate stock availability before accepting. |
| **C-03** | Cart items carry a price + qty snapshot. |
| **C-04** | Cart **MUST** expire after **24h** without updates — TaskIQ job sweeps and marks `EXPIRED`. |

## 8. Orders

| ID | Rule |
|----|------|
| **D-01** | Checkout (`POST /orders`) **MUST** be idempotent via the `Idempotency-Key` header. |
| **D-02** | Checkout converts 1 cart into 1 order. Cart becomes `CONVERTED`. |
| **D-03** | Order follows the machine: `PENDING → PAID → SHIPPED → DELIVERED` (see [Flows](flows.en.md)). |
| **D-04** | `CANCELLED` is reachable from `PENDING` and `PAID`; past `SHIPPED` only `RETURNED`. |
| **D-05** | Order creation **MUST** emit a stock `RESERVATION` (atomic with the order `INSERT`). |
| **D-06** | Confirmed payment **MUST** convert `RESERVATION` into `OUT`. |
| **D-07** | Cancellation **MUST** emit `RELEASE` (returns stock). |
| **D-08** | State change **MUST** publish an SSE event to the buyer's `orders/{id}/events` stream. |
| **D-09** | Payment in this project is **mock** — an admin endpoint marks the order as paid. No external integration. |

## 9. Reviews

| ID | Rule |
|----|------|
| **R-01** | Review (`POST /products/{id}/reviews`) **MUST** require the user to have a `DELIVERED` order containing the variant. |
| **R-02** | A user **MAY** review each variant once (`UNIQUE(user_id, variant_id)`). |
| **R-03** | Score **MUST** be between 1 and 5. |

## 10. Global limits and quotas

| ID | Rule |
|----|------|
| **G-01** | Default rate-limit `100 req/min/user` via `RateLimitMiddleware`. |
| **G-02** | Body limit `5 MiB` (image uploads use presigned PUT, so the API never receives the bytes). |
| **G-03** | Structured logs always on — `configure_logging(log_dir="logs")`. There is no "production logs off" mode. |
| **G-04** | Every unhandled exception ends up in `error.log` AND `500.log` (via `register_exception_handlers`). |

## Next step

With the rules in mind, head to the **[Domain model](domain.en.md)** to see the entities + diagrams, then **[Critical flows](flows.en.md)** to see how the rules play out at runtime.
