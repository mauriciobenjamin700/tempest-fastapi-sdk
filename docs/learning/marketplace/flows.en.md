# Critical flows

Sequence diagrams for the 5 flows that **fail most often on first implementation**, plus the state machines for `Order` and `Invitation`. Each flow names the SDK primitives involved.

## 1. Public signup + login

```mermaid
sequenceDiagram
    autonumber
    actor C as Client
    participant R as auth.router
    participant S as UserService
    participant U as PasswordUtils
    participant J as JWTUtils
    participant DB as Postgres

    C->>R: POST /auth/signup {email, password, name}
    R->>S: signup(payload)
    S->>U: hash(password)
    U-->>S: bcrypt hash
    S->>DB: INSERT users (email, hash, ...)
    DB-->>S: user row
    S->>J: encode({sub: user.id}, ttl=ACCESS_TTL)
    S->>J: encode({sub: user.id, refresh: true}, ttl=REFRESH_TTL)
    S-->>R: {user, access, refresh}
    R-->>C: 201 Created
```

**SDK touchpoints:**

- Public endpoint — `auth.router` doesn't use `Depends(get_current_user)`.
- `PasswordUtils.hash` (bcrypt) + `JWTUtils.encode` (HS256).
- Duplicate-email failure **MUST** become `ConflictException` → the SDK handler responds with `409` and the standard envelope.

## 2. Member invitation

```mermaid
sequenceDiagram
    autonumber
    actor A as Admin (OWNER/ADMIN)
    actor I as Invitee
    participant R as invitations.router
    participant S as InvitationService
    participant T as generate_opaque_token
    participant E as EmailUtils
    participant Q as TaskIQ (async email)
    participant DB as Postgres

    A->>R: POST /organizations/{id}/invitations {email, role}
    R->>S: invite(org_id, payload, current_user)
    S->>S: assert role ≠ OWNER
    S->>S: assert org_member_count < 10
    S->>T: generate_opaque_token(48)
    T-->>S: (plain, hash)
    S->>DB: INSERT invitations (token_hash, expires_at=now+7d, PENDING)
    S->>Q: enqueue send_invitation_email(invite.id, plain)
    Q-->>E: render_template("invitation.html", {...})
    E-->>I: email with ?token={plain}
    S-->>R: invitation
    R-->>A: 201

    Note over I: 1 day later
    I->>R: POST /invitations/{plain}/accept (with invitee JWT)
    R->>S: accept(plain, current_user)
    S->>S: hash_opaque_token(plain) -> lookup
    S->>S: assert invite.email == current_user.email
    S->>S: assert not expired & status=PENDING
    S->>S: assert org_member_count < 10
    S->>DB: BEGIN
    S->>DB: INSERT memberships (role=invite.role)
    S->>DB: UPDATE invitations SET status=ACCEPTED
    S->>DB: COMMIT
    S-->>R: membership
    R-->>I: 200
```

**SDK touchpoints:**

- `generate_opaque_token(48)` returns `(plain, hash)`. Database stores only the hash.
- `EmailUtils.render_template("invitation.html", ctx)` (v0.24+).
- Send is async (TaskIQ) — endpoint returns `201` without waiting on SMTP.
- The acceptance is **one single transaction** — membership + invitation status are atomic.

## 3. Create product + variant + images

```mermaid
sequenceDiagram
    autonumber
    actor M as Member (ADMIN+)
    participant R as products.router
    participant CT as ProductController
    participant PS as ProductService
    participant VS as VariantService
    participant ST as MinIOUploadStorage
    participant DB as Postgres

    M->>R: POST /products {title, description, variants:[{sku, attrs, price_cents}]}
    R->>CT: create_product(payload, org_id, user_id)
    CT->>PS: create(org_id, payload)
    PS->>DB: BEGIN
    PS->>DB: INSERT products (...)
    loop for each variant
        PS->>VS: create_variant(product_id, variant_payload)
        VS->>DB: INSERT product_variants (...)
        VS->>DB: INSERT price_history (valid_from=now())
    end
    PS->>DB: COMMIT
    PS-->>R: product

    Note over M,ST: Image upload (separate step)
    M->>R: POST /products/{id}/images/presign
    R->>ST: presigned_put_url("products/{id}/{uuid}.jpg", 15min)
    ST-->>R: {key, url}
    R-->>M: {key, url}
    M->>ST: PUT bytes directly to MinIO via presigned URL
    M->>R: PATCH /products/{id} {image_keys: [...keys]}
    R->>PS: attach_images(product_id, keys)
    PS->>DB: UPDATE products SET image_keys = ...
    PS-->>R: product
    R-->>M: 200
```

**SDK touchpoints:**

- Product creation is a single transaction — product + variants + first `PriceHistory` row.
- Images **never flow through the API** — the client `PUT`s directly to MinIO via a presigned URL (`MinIOUploadStorage.presigned_url` or `AsyncMinIOClient.presigned_put_url` directly).
- The public catalog reads `image_keys` and mints presigned read URLs (1h TTL).

## 4. Idempotent checkout

```mermaid
sequenceDiagram
    autonumber
    actor B as Buyer
    participant MW as IdempotencyMiddleware
    participant R as orders.router
    participant OC as OrderController
    participant OS as OrderService
    participant SS as StockService
    participant SSE as orders/{id}/events stream
    participant DB as Postgres
    participant Q as TaskIQ

    B->>MW: POST /orders {cart_id, address}<br/>Idempotency-Key: chk_uuid
    MW->>MW: cache lookup (method+path+key)
    alt cache hit
        MW-->>B: cached response (200/201)
    else cache miss
        MW->>R: forward
        R->>OC: checkout(cart_id, address, user)
        OC->>OS: create_order(cart, address, user)
        OS->>DB: BEGIN
        OS->>DB: SELECT cart FOR UPDATE
        OS->>OS: assert cart.user == user & status=OPEN
        OS->>SS: reserve(items)
        loop for each item
            SS->>DB: assert balance(variant) >= qty
            SS->>DB: INSERT stock_movements (kind=RESERVATION)
        end
        OS->>DB: INSERT orders (status=PENDING, idem_key)
        OS->>DB: INSERT order_items (...)
        OS->>DB: UPDATE carts SET status=CONVERTED
        OS->>DB: COMMIT
        OS->>Q: enqueue notify_seller(order.id)
        OS->>SSE: publish {order_id, status: PENDING}
        OS-->>R: order
        R-->>MW: 201 (full body)
        MW->>MW: store response under key
        MW-->>B: 201 Created
    end
```

**SDK touchpoints:**

- `IdempotencyMiddleware` covers the endpoint without the handler having to care. If the buyer retries with the same `Idempotency-Key`, the middleware replays the original response — the handler does not run twice, stock is not decremented twice.
- Stock reservation lives **inside the same transaction** as the order `INSERT`. A failure on any item rolls everything back.
- The `SSE` notifies the stream (the buyer's client listening on `/orders/{id}/events`).
- `notify_seller` is queued — does not block the checkout response.

## 5. Shipping + real-time updates

```mermaid
sequenceDiagram
    autonumber
    actor A as Admin (seller)
    actor B as Buyer
    participant R as orders.router
    participant OS as OrderService
    participant SS as StockService
    participant SSE as orders/{id}/events
    participant DB as Postgres

    B->>SSE: GET /orders/{id}/events<br/>Accept: text/event-stream
    SSE-->>B: event: status (PAID)

    Note over A: seller ships
    A->>R: POST /orders/{id}/ship {tracking}
    R->>OS: transition(order_id, SHIPPED)
    OS->>OS: assert current == PAID
    OS->>DB: UPDATE orders SET status=SHIPPED
    OS->>SSE: publish {status: SHIPPED, tracking}
    SSE-->>B: event: status (SHIPPED)

    Note over A: buyer confirms delivery
    B->>R: POST /orders/{id}/confirm-delivery
    R->>OS: transition(order_id, DELIVERED)
    OS->>OS: assert current == SHIPPED
    OS->>SS: convert_reservation_to_out(items)
    SS->>DB: INSERT stock_movements (kind=OUT) per item
    OS->>DB: UPDATE orders SET status=DELIVERED
    OS->>SSE: publish {status: DELIVERED}
    SSE-->>B: event: status (DELIVERED)
```

**SDK touchpoints:**

- `EventStream` keeps a broadcaster per `order_id` — every connected buyer client receives the SSE.
- Transition **MUST** validate the source state (state machine inside the service).
- Stock becomes a definitive `OUT` only on delivery — cancelling earlier turns the `RESERVATION` into a `RELEASE`.

## State machine — Order

```mermaid
stateDiagram-v2
    [*] --> PENDING : checkout
    PENDING --> PAID : payment confirmed (admin mock)
    PENDING --> CANCELLED : buyer/admin cancels
    PAID --> SHIPPED : seller ships
    PAID --> CANCELLED : refund pre-ship
    SHIPPED --> DELIVERED : buyer confirms
    SHIPPED --> RETURNED : return flow
    DELIVERED --> [*]
    CANCELLED --> [*]
    RETURNED --> [*]
```

Forbidden transitions (any other arrow) **MUST** fail with `ConflictException("invalid state transition")`. Typical implementation is an enum + `dict[from, set[to]]` in the service.

## State machine — Invitation

```mermaid
stateDiagram-v2
    [*] --> PENDING : invited
    PENDING --> ACCEPTED : invitee accepts
    PENDING --> REVOKED : admin revokes
    PENDING --> EXPIRED : job 7d
    PENDING --> SUPERSEDED : new invite for same email
    ACCEPTED --> [*]
    REVOKED --> [*]
    EXPIRED --> [*]
    SUPERSEDED --> [*]
```

`EXPIRED` is set by a TaskIQ task running hourly that sweeps invitations with `expires_at < now()`.

## Next step

Jump to the **[Endpoint map](api.en.md)** to see the full REST API ready to wire up the frontend contract.
