# Fluxos críticos

Diagramas de sequência para os 5 fluxos que **mais erram na primeira implementação**, junto com as máquinas de estado de `Order` e `Invitation`. Cada fluxo aponta os primitivos do SDK envolvidos.

## 1. Signup público + login

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant R as auth.router
    participant S as UserService
    participant U as UserUtils (PasswordUtils)
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

**Pontos do SDK:**

- Endpoint público — `auth.router` não usa `Depends(get_current_user)`.
- `PasswordUtils.hash` (bcrypt) + `JWTUtils.encode` (HS256).
- Falha de email duplicado **MUST** virar `ConflictException` → handler do SDK responde `409` com envelope padrão.

## 2. Convite de membro

```mermaid
sequenceDiagram
    autonumber
    actor A as Admin (OWNER/ADMIN)
    actor I as Convidado
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
    E-->>I: email com link ?token={plain}
    S-->>R: invitation
    R-->>A: 201

    Note over I: 1 dia depois
    I->>R: POST /invitations/{plain}/accept (com JWT do convidado)
    R->>S: accept(plain, current_user)
    S->>S: hash_opaque_token(plain) -> lookup
    S->>S: assert convite.email == current_user.email
    S->>S: assert not expired & status=PENDING
    S->>S: assert org_member_count < 10
    S->>DB: BEGIN
    S->>DB: INSERT memberships (role=convite.role)
    S->>DB: UPDATE invitations SET status=ACCEPTED
    S->>DB: COMMIT
    S-->>R: membership
    R-->>I: 200
```

**Pontos do SDK:**

- `generate_opaque_token(48)` retorna par `(plain, hash)`. Banco guarda só o hash.
- `EmailUtils.render_template("invitation.html", ctx)` (v0.24+).
- O envio é assíncrono (TaskIQ) — endpoint retorna `201` sem esperar SMTP.
- Toda a aceitação é **uma única transação** — membership + status do convite são atomic.

## 3. Criar produto com variante + imagens

```mermaid
sequenceDiagram
    autonumber
    actor M as Membro (ADMIN+)
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
    loop pra cada variant
        PS->>VS: create_variant(product_id, variant_payload)
        VS->>DB: INSERT product_variants (...)
        VS->>DB: INSERT price_history (valid_from=now())
    end
    PS->>DB: COMMIT
    PS-->>R: product

    Note over M,ST: Upload de imagem (separado)
    M->>R: POST /products/{id}/images/presign
    R->>ST: presigned_put_url("products/{id}/{uuid}.jpg", 15min)
    ST-->>R: {key, url}
    R-->>M: {key, url}
    M->>ST: PUT bytes direto no MinIO via URL presigned
    M->>R: PATCH /products/{id} {image_keys: [...keys]}
    R->>PS: attach_images(product_id, keys)
    PS->>DB: UPDATE products SET image_keys = ...
    PS-->>R: product
    R-->>M: 200
```

**Pontos do SDK:**

- Criação de produto é transação única — produto + variantes + primeira linha de `PriceHistory`.
- Imagens **não trafegam pela API** — cliente faz `PUT` direto no MinIO via URL presigned (`MinIOUploadStorage.presigned_url` ou direto `AsyncMinIOClient.presigned_put_url`).
- Catálogo público lê `image_keys` e gera URLs presigned de leitura (TTL 1h).

## 4. Checkout idempotente

```mermaid
sequenceDiagram
    autonumber
    actor B as Comprador
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
        MW-->>B: response cacheada (200/201)
    else cache miss
        MW->>R: forward
        R->>OC: checkout(cart_id, address, user)
        OC->>OS: create_order(cart, address, user)
        OS->>DB: BEGIN
        OS->>DB: SELECT cart FOR UPDATE
        OS->>OS: assert cart.user == user & status=OPEN
        OS->>SS: reserve(items)
        loop pra cada item
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
        R-->>MW: 201 (body completo)
        MW->>MW: store response under key
        MW-->>B: 201 Created
    end
```

**Pontos do SDK:**

- `IdempotencyMiddleware` cobre o endpoint sem o handler precisar saber. Se o comprador retentar com a mesma `Idempotency-Key`, o middleware devolve a resposta original — handler não roda 2x, estoque não é decrementado 2x.
- Reserva de estoque é **dentro da mesma transação** do `INSERT` do pedido. Falha em qualquer item aborta tudo.
- A `SSE` notifica o stream (cliente do comprador escutando em `/orders/{id}/events`).
- O notify_seller vai pra fila — não bloqueia a resposta do checkout.

## 5. Expedição + atualização em tempo real

```mermaid
sequenceDiagram
    autonumber
    actor A as Admin (vendedor)
    actor B as Comprador
    participant R as orders.router
    participant OS as OrderService
    participant SS as StockService
    participant SSE as orders/{id}/events
    participant DB as Postgres

    B->>SSE: GET /orders/{id}/events<br/>Accept: text/event-stream
    SSE-->>B: event: status (PAID)

    Note over A: vendedor expede
    A->>R: POST /orders/{id}/ship {tracking}
    R->>OS: transition(order_id, SHIPPED)
    OS->>OS: assert current == PAID
    OS->>DB: UPDATE orders SET status=SHIPPED
    OS->>SSE: publish {status: SHIPPED, tracking}
    SSE-->>B: event: status (SHIPPED)

    Note over A: cliente confirma recebimento
    B->>R: POST /orders/{id}/confirm-delivery
    R->>OS: transition(order_id, DELIVERED)
    OS->>OS: assert current == SHIPPED
    OS->>SS: convert_reservation_to_out(items)
    SS->>DB: INSERT stock_movements (kind=OUT) por item
    OS->>DB: UPDATE orders SET status=DELIVERED
    OS->>SSE: publish {status: DELIVERED}
    SSE-->>B: event: status (DELIVERED)
```

**Pontos do SDK:**

- `EventStream` mantém um broadcaster por `order_id` — cada cliente do comprador conectado recebe via SSE.
- Transição **MUST** validar o estado origem (state machine no service).
- Estoque vira `OUT` definitivo só na entrega — se cancelar antes, o `RESERVATION` vira `RELEASE`.

## Máquina de estados — Order

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

Transições proibidas (qualquer outra setinha) **MUST** falhar com `ConflictException("invalid state transition")`. Implementação típica num enum + `dict[from, set[to]]` no service.

## Máquina de estados — Invitation

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

`EXPIRED` é set por tarefa TaskIQ que roda de hora em hora varrendo convites com `expires_at < now()`.

## Próximo passo

Pula pro **[Mapa de endpoints](api.md)** ver a API REST completa pronta pra cabear contratos no frontend.
