# Fluxos críticos

Diagramas de sequência para os fluxos que **mais erram na primeira implementação** — incluindo a mensageria em tempo real (SSE + Web Push) — junto com as máquinas de estado de `Order` e `Invitation`. Cada fluxo aponta os primitivos do SDK envolvidos.

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

- `generate_opaque_token(48)` retorna par `(plain, hash)`.
- `EmailUtils.render_template("invitation.html", ctx)` (v0.24+).
- O envio é assíncrono (TaskIQ) — endpoint retorna `201` sem esperar SMTP.
- Toda a aceitação é **uma única transação** — membership + status do convite são atomic.

!!! warning "O banco guarda só o hash"
    `generate_opaque_token(48)` devolve `(plain, hash)`: o valor `plain` só existe no email enviado ao convidado, e o banco persiste apenas `hash`. Na aceitação, o service faz `hash_opaque_token(plain)` e busca pelo hash — um vazamento da tabela `invitations` não expõe tokens utilizáveis.

## 3. Criar produto com variante + imagens

```mermaid
sequenceDiagram
    autonumber
    actor M as Membro (ADMIN+)
    participant R as products.router
    participant CT as ProductController
    participant PS as ProductService
    participant VS as VariantService
    participant ST as AsyncMinIOClient
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
- Imagens **não trafegam pela API** — cliente faz `PUT` direto no MinIO via URL presigned gerada por `AsyncMinIOClient.presigned_put_url` (o `MinIOUploadStorage.presigned_url` é GET/leitura, não serve pra upload).
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

- `IdempotencyMiddleware` cobre o endpoint sem o handler precisar saber.
- Reserva de estoque é **dentro da mesma transação** do `INSERT` do pedido. Falha em qualquer item aborta tudo.
- A `SSE` notifica o stream (cliente do comprador escutando em `/orders/{id}/events`).
- O notify_seller vai pra fila — não bloqueia a resposta do checkout.

!!! note "Idempotência evita decremento duplo de estoque"
    Se o comprador retentar com a mesma `Idempotency-Key` (reload, timeout de rede, double-tap), o middleware devolve a resposta original — o handler **não roda 2x**, então o estoque **não é decrementado 2x** e nenhum pedido duplicado é criado.

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

- `SSEBroker` mantém um canal por usuário — cada cliente conectado do comprador recebe o frame (ver fluxo 6 pro fan-out completo).
- Transição **MUST** validar o estado origem (state machine no service).
- Estoque vira `OUT` definitivo só na entrega — se cancelar antes, o `RESERVATION` vira `RELEASE`.

## 6. Notificações: SSE + Web Push (um evento, dois canais)

Todo evento de domínio relevante pro usuário — pedido pago, pedido expedido, convite recebido, novo review — é entregue em **dois canais que carregam o mesmo payload**: **SSE** (`SSEBroker`, canal = id do usuário) pros clientes com o app **aberto** (foreground, ao vivo) e **Web Push** (VAPID) pros dispositivos com o app/aba **fechado** (background). É "notificação como mensageria": um único `NotificationService.notify(...)` faz o fan-out pros dois.

```mermaid
sequenceDiagram
    autonumber
    actor A as Admin (vendedor)
    participant OS as OrderService
    participant N as NotificationService
    participant SB as SSEBroker
    participant WP as WebPushSubscriptionService
    actor FG as App aberto (foreground)
    actor BG as Dispositivo fechado (background)

    Note over A: evento de domínio: pedido expedido
    A->>OS: POST /orders/{id}/ship {tracking}
    OS->>OS: transition(order_id, SHIPPED)
    OS->>N: notify(buyer_id, "order_shipped", title, body, data)
    par canal foreground
        N->>SB: publish(str(user_id), data, event="order_shipped")
        SB-->>FG: frame SSE (event: order_shipped)
    and canal background
        N->>WP: notify_user(user_id, WebPushPayloadSchema(...))
        WP-->>BG: Web Push (VAPID) → service worker exibe a notificação
    end
    Note over WP: dispositivos mortos (404/410) são podados automaticamente
```

O produtor (um controller/service) dispara o evento logo após a transição de domínio:

```python
await self.notifications.notify(
    order.buyer_id,
    event="order_shipped",
    title="Pedido a caminho",
    body=f"Pedido {order.code} saiu para entrega.",
    data={"order_id": str(order.id), "status": order.status},
)
```

O `NotificationService` é o único ponto que conhece os dois canais:

```python
# src/services/notification.py
from uuid import UUID

from tempest_fastapi_sdk import SSEBroker, WebPushPayloadSchema, WebPushSubscriptionService


class NotificationService:
    """Fan one domain event out to SSE (foreground) and Web Push (background)."""

    def __init__(self, broker: SSEBroker, push: WebPushSubscriptionService) -> None:
        self.broker = broker
        self.push = push

    async def notify(
        self, user_id: UUID, event: str, title: str, body: str, data: dict
    ) -> None:
        """Deliver one event on both channels with the same payload."""
        await self.broker.publish(str(user_id), data, event=event)
        await self.push.notify_user(
            user_id, WebPushPayloadSchema(title=title, body=body, tag=event, data=data)
        )
```

O cliente com o app aberto assina `GET /notifications/stream`, que devolve `broker.response(str(user.id))`. Um frame SSE que chega nele:

```text
event: order_shipped
id: 01J8Z9F2K7Q3M5R8T0W1X2Y3Z4
data: {"order_id": "9f8e7d6c-5b4a-3210-fedc-ba9876543210", "status": "SHIPPED"}

```

**Pontos do SDK:**

- **SSE é core** (sem extra): `SSEBroker()`, `await broker.publish(channel, data, event=..., id=..., retry=...)`, `broker.response(channel)` já monta a `StreamingResponse` que assina o canal e desregistra ao desconectar. SSE **multi-worker** precisa de `SSEBroker(redis=...)` + `broker.run()` no lifespan → extra `[cache]`.
- **Web Push precisa do extra `[webpush]`** (`uv add "tempest-fastapi-sdk[webpush]"`): monte `WebPushDispatcher(**settings.webpush_kwargs())`, passe-o pro `WebPushSubscriptionService(repository, dispatcher)`; `await service.notify_user(user_id, payload, *, ttl_seconds=None, exclude_endpoints=None)` envia pra todos os dispositivos do usuário e poda os mortos (404/410).
- O mesmo `data` viaja nos dois canais — o frontend trata SSE e Web Push com o mesmo handler.
- Detalhes dos primitivos: **[Receita SSE »](../../recipes/sse.md)** e **[Receita Web Push »](../../recipes/webpush.md)**.

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

!!! warning "Transições inválidas devem falhar com ConflictException"
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
