# Modelo de domínio

Esta página traz o **modelo conceitual** (UML de classe), o **modelo físico** (ER) e o glossário dos atributos de cada entidade. Os diagramas usam Mermaid — renderiza automaticamente no MkDocs Material.

## Diagrama de classes (UML)

```mermaid
classDiagram
    class User {
        +UUID id
        +str email
        +str password_hash
        +str name
        +str|None photo_url
        +bool is_active
        +datetime created_at
        +signup() User
        +login(password) tokens
    }

    class Organization {
        +UUID id
        +UUID owner_id
        +str name
        +str slug
        +bool is_active
        +datetime created_at
        +invite(email, role) Invitation
        +add_member(user, role) Membership
        +transfer_ownership(target) void
    }

    class Membership {
        +UUID id
        +UUID organization_id
        +UUID user_id
        +Role role
        +datetime joined_at
        +can(permission) bool
    }

    class Invitation {
        +UUID id
        +UUID organization_id
        +str email
        +Role role
        +str token_hash
        +InvitationStatus status
        +datetime expires_at
        +accept(user) Membership
        +revoke() void
    }

    class Product {
        +UUID id
        +UUID organization_id
        +str title
        +str description
        +bool is_active
        +list~str~ image_keys
        +datetime created_at
    }

    class ProductVariant {
        +UUID id
        +UUID product_id
        +str sku
        +dict attributes
        +int stock_balance() (derived)
        +int current_price_cents() (derived)
    }

    class PriceHistory {
        +UUID id
        +UUID variant_id
        +int amount_cents
        +str currency
        +datetime valid_from
    }

    class StockMovement {
        +UUID id
        +UUID variant_id
        +StockKind kind
        +int qty
        +str reason
        +str ref_type
        +UUID ref_id
        +datetime created_at
    }

    class Cart {
        +UUID id
        +UUID user_id
        +UUID organization_id
        +CartStatus status
        +datetime expires_at
    }

    class CartItem {
        +UUID id
        +UUID cart_id
        +UUID variant_id
        +int qty
        +int price_snapshot_cents
    }

    class Order {
        +UUID id
        +UUID buyer_id
        +UUID organization_id
        +OrderStatus status
        +int total_cents
        +str shipping_address
        +str idempotency_key
        +datetime created_at
        +transition_to(status) void
    }

    class OrderItem {
        +UUID id
        +UUID order_id
        +UUID variant_id
        +int qty
        +int unit_price_cents
    }

    class Review {
        +UUID id
        +UUID user_id
        +UUID variant_id
        +int score
        +str comment
        +datetime created_at
    }

    class PushSubscription {
        +UUID id
        +UUID user_id
        +str endpoint
        +str p256dh
        +str auth
        +datetime created_at
    }

    User "1" --o "0..2" Organization : owns
    User "1" --o "0..5" Membership : has
    Organization "1" --o "1..10" Membership : has
    Organization "1" --o "*" Invitation : sends
    Organization "1" --o "*" Product : sells
    Product "1" --* "1..*" ProductVariant : composed of
    ProductVariant "1" --o "*" PriceHistory : priced over time
    ProductVariant "1" --o "*" StockMovement : ledgered by
    User "1" --o "*" Cart : owns
    Cart "1" --o "*" CartItem : holds
    User "1" --o "*" Order : places
    Order "1" --* "1..*" OrderItem : itemized as
    User "1" --o "*" Review : writes
    ProductVariant "1" --o "*" Review : reviewed by
    User "1" --o "*" PushSubscription : registers
```

`PushSubscription` guarda um dispositivo/navegador inscrito no Web Push do usuário (`endpoint` + chaves `p256dh`/`auth` que o browser gera). Um usuário tem N inscrições (desktop, celular, cada aba/perfil). É o alvo do canal **background** da mensageria — o canal **foreground** (SSE) não precisa de estado persistido, é só uma conexão viva por `user_id`.

## Diagrama ER (modelo físico)

```mermaid
erDiagram
    USERS ||--o{ ORGANIZATIONS : "owns"
    USERS ||--o{ MEMBERSHIPS : "joins"
    ORGANIZATIONS ||--o{ MEMBERSHIPS : "has"
    ORGANIZATIONS ||--o{ INVITATIONS : "sends"
    ORGANIZATIONS ||--o{ PRODUCTS : "sells"
    PRODUCTS ||--|{ PRODUCT_VARIANTS : "has"
    PRODUCT_VARIANTS ||--o{ PRICE_HISTORY : "priced"
    PRODUCT_VARIANTS ||--o{ STOCK_MOVEMENTS : "tracked"
    USERS ||--o{ CARTS : "owns"
    CARTS ||--o{ CART_ITEMS : "holds"
    CART_ITEMS }o--|| PRODUCT_VARIANTS : "refs"
    USERS ||--o{ ORDERS : "places"
    ORGANIZATIONS ||--o{ ORDERS : "fulfills"
    ORDERS ||--|{ ORDER_ITEMS : "items"
    ORDER_ITEMS }o--|| PRODUCT_VARIANTS : "refs"
    USERS ||--o{ REVIEWS : "writes"
    PRODUCT_VARIANTS ||--o{ REVIEWS : "rated"
    USERS ||--o{ PUSH_SUBSCRIPTIONS : "registers"

    USERS {
        UUID id PK
        string email UK
        string password_hash
        string name
        string photo_url
        bool is_active
        datetime created_at
        datetime updated_at
    }
    ORGANIZATIONS {
        UUID id PK
        UUID owner_id FK
        string name
        string slug UK
        bool is_active
        datetime created_at
        datetime updated_at
    }
    MEMBERSHIPS {
        UUID id PK
        UUID organization_id FK
        UUID user_id FK
        string role
        datetime joined_at
    }
    INVITATIONS {
        UUID id PK
        UUID organization_id FK
        string email
        string role
        string token_hash UK
        string status
        datetime expires_at
        datetime created_at
    }
    PRODUCTS {
        UUID id PK
        UUID organization_id FK
        string title
        string description
        bool is_active
        json image_keys
        datetime created_at
        datetime updated_at
    }
    PRODUCT_VARIANTS {
        UUID id PK
        UUID product_id FK
        string sku UK
        json attributes
        datetime created_at
    }
    PRICE_HISTORY {
        UUID id PK
        UUID variant_id FK
        int amount_cents
        string currency
        datetime valid_from
    }
    STOCK_MOVEMENTS {
        UUID id PK
        UUID variant_id FK
        string kind
        int qty
        string reason
        string ref_type
        UUID ref_id
        UUID audit_user_id
        datetime created_at
    }
    CARTS {
        UUID id PK
        UUID user_id FK
        UUID organization_id FK
        string status
        datetime expires_at
        datetime created_at
    }
    CART_ITEMS {
        UUID id PK
        UUID cart_id FK
        UUID variant_id FK
        int qty
        int price_snapshot_cents
    }
    ORDERS {
        UUID id PK
        UUID buyer_id FK
        UUID organization_id FK
        string status
        int total_cents
        string shipping_address
        string idempotency_key UK
        datetime created_at
        datetime updated_at
    }
    ORDER_ITEMS {
        UUID id PK
        UUID order_id FK
        UUID variant_id FK
        int qty
        int unit_price_cents
    }
    REVIEWS {
        UUID id PK
        UUID user_id FK
        UUID variant_id FK
        int score
        string comment
        datetime created_at
    }
    PUSH_SUBSCRIPTIONS {
        UUID id PK
        UUID user_id FK
        string endpoint UK
        string p256dh
        string auth
        datetime created_at
    }
```

## Enums

```mermaid
classDiagram
    class Role {
        <<enum>>
        OWNER
        ADMIN
        MEMBER
    }

    class InvitationStatus {
        <<enum>>
        PENDING
        ACCEPTED
        REVOKED
        EXPIRED
        SUPERSEDED
    }

    class StockKind {
        <<enum>>
        IN
        OUT
        ADJUST
        RESERVATION
        RELEASE
    }

    class CartStatus {
        <<enum>>
        OPEN
        CONVERTED
        EXPIRED
        ABANDONED
    }

    class OrderStatus {
        <<enum>>
        PENDING
        PAID
        SHIPPED
        DELIVERED
        CANCELLED
        RETURNED
    }
```

## Invariantes (resumo executável)

| Entidade | Invariante | Onde checa |
|----------|-----------|------------|
| `User` | email único, ativo, bcrypt-hashed | constraint DB + service signup |
| `Organization` | owner é membro OWNER, slug único, 1 OWNER por org | constraint + trigger ou service |
| `Membership` | `(org_id, user_id)` único, ≤10 por org, ≤5 por user, exatamente 1 OWNER por org | unique constraint + service guard |
| `Invitation` | token_hash único, expira em 7d, status terminal não muta | constraint + state machine no service |
| `Product` | ≥1 variant ativa pra aparecer no catálogo | query do catálogo |
| `ProductVariant` | SKU único dentro de `organization_id` (via JOIN product) | constraint composta + service |
| `PriceHistory` | append-only — sem UPDATE/DELETE | revoke permissões DB + service |
| `StockMovement` | append-only, saldo nunca negativo | service guard + check constraint |
| `Order` | `idempotency_key` único, transição obedece máquina | constraint + state machine |

## Mapeamento entidade → primitivo SDK

| Entidade | Herda de | Repository | Service base |
|----------|----------|-----------|--------------|
| `User` | `BaseUserModel` | `BaseRepository[UserModel]` | `BaseService` |
| `Organization` | `BaseModel + AuditMixin + SoftDeleteMixin` | `BaseRepository[OrganizationModel]` | `BaseService` |
| `Membership` | `BaseModel + AuditMixin` | `BaseRepository[MembershipModel]` | `BaseService` |
| `Invitation` | `BaseModel + AuditMixin` | `BaseRepository[InvitationModel]` | `BaseService` |
| `Product` | `BaseModel + AuditMixin + SoftDeleteMixin` | custom (com JOINs em variant+price) | `BaseService` |
| `ProductVariant` | `BaseModel + AuditMixin` | custom | `BaseService` |
| `PriceHistory` | `BaseModel` (sem updated_at) | append-only | `BaseService` |
| `StockMovement` | `BaseModel` (sem updated_at) | append-only via `bulk_create_values` quando lote | `BaseService` |
| `Cart`/`CartItem` | `BaseModel + AuditMixin` | `BaseRepository[CartModel]` | `BaseService` |
| `Order`/`OrderItem` | `BaseModel + AuditMixin` | custom | `BaseService` |
| `Review` | `BaseModel + AuditMixin` | `BaseRepository[ReviewModel]` | `BaseService` |
| `PushSubscription` | modelo base de subscription do SDK (Web Push) | `BaseRepository[PushSubscriptionModel]` | `WebPushSubscriptionService` |

O `PushSubscriptionModel` **não é escrito à mão** — concretiza o modelo base de subscription que o SDK expõe (só declara `__tablename__` e a FK pro `user_id`), exatamente como na **[Receita Web Push »](../../recipes/webpush.md)**. O `WebPushSubscriptionService` recebe esse `BaseRepository[PushSubscriptionModel]` + um `WebPushDispatcher` e cuida do CRUD e do envio (podando dispositivos mortos 404/410). Não há entidade `Notification` persistida: cada notificação é efêmera — vive só como frame SSE (foreground) e/ou payload Web Push (background), ambos derivados do mesmo evento de domínio.

`PriceHistory` e `StockMovement` são append-only, então o repositório deles **não expõe `update` nem `delete`** — só `add`/`list`/`get`. Isso impede ALTER acidental no histórico.

## Próximo passo

Pula pra **[Fluxos críticos](flows.md)** ver os diagramas de sequência cobrindo signup, convite, criação de produto, checkout e expedição.
