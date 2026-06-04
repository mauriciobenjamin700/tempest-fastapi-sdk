# Mapa de endpoints

Toda a API REST do marketplace numa tabela só, pronta pra colar no contrato do frontend.

!!! note "Convenções"
    - **Auth**: `público` (sem token), `user` (JWT bearer), `org:<role>` (membro da org no role mínimo).
    - **Idem**: ✅ = aceita `Idempotency-Key` (cacheado pelo `IdempotencyMiddleware`).
    - **Paginação**: cursor por padrão; offset onde explícito.
    - **Erros**: todo erro vem no envelope SDK `{detail, code, details}`.

## Auth

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| POST | `/auth/signup` | público | ✅ | 201 | Cadastro de usuário. |
| POST | `/auth/login` | público | — | 200 | Login com email + senha. |
| POST | `/auth/refresh` | público | — | 200 | Troca refresh por par novo. |
| POST | `/auth/logout` | user | — | 204 | Revoga tokens (lista negra de `jti`). |
| GET  | `/users/me` | user | — | 200 | Perfil do usuário corrente. |
| PATCH | `/users/me` | user | — | 200 | Atualiza nome / foto. |
| DELETE | `/users/me` | user | — | 204 | Soft-delete (regra U-08). |

## Organizações

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| GET  | `/organizations` | user | — | 200 | Lista orgs onde o user é membro. |
| POST | `/organizations` | user | ✅ | 201 | Cria nova organização (regra O-02 ≤ 2). |
| GET  | `/organizations/{id}` | org:MEMBER | — | 200 | Detalhe da org. |
| PATCH | `/organizations/{id}` | org:OWNER | — | 200 | Atualiza nome / slug. |
| DELETE | `/organizations/{id}` | org:OWNER | — | 202 | Soft-delete + cleanup async. |
| POST | `/organizations/{id}/transfer-ownership` | org:OWNER | ✅ | 200 | Passa o OWNER pra outro membro. |

## Membros + convites

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| GET  | `/organizations/{id}/members` | org:MEMBER | — | 200 | Lista membros + role. |
| PATCH | `/organizations/{id}/members/{user_id}` | org:OWNER | — | 200 | Muda role (regra M-06). |
| DELETE | `/organizations/{id}/members/{user_id}` | org:OWNER | — | 204 | Remove membro. |
| DELETE | `/organizations/{id}/members/me` | org:MEMBER | — | 204 | Saída voluntária (regra M-08). |
| POST | `/organizations/{id}/invitations` | org:ADMIN | ✅ | 201 | Cria convite (regra I-02). |
| GET  | `/organizations/{id}/invitations` | org:ADMIN | — | 200 | Lista convites da org. |
| DELETE | `/organizations/{id}/invitations/{inv_id}` | org:ADMIN | — | 204 | Revoga convite. |
| POST | `/invitations/{token}/accept` | user | ✅ | 200 | Aceita convite (regra I-04). |
| POST | `/invitations/{token}/reject` | user | — | 204 | Rejeita convite. |

## Catálogo público

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| GET  | `/catalog` | público | — | 200 | Lista produtos ativos com estoque > 0. Cursor pagination. |
| GET  | `/catalog/{product_id}` | público | — | 200 | Detalhe completo do produto + variantes + preços. |
| GET  | `/catalog/{product_id}/reviews` | público | — | 200 | Reviews do produto, paginado. |

## Produtos (vendedor)

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| GET  | `/organizations/{id}/products` | org:MEMBER | — | 200 | Catálogo da própria org (inclui inativos). |
| POST | `/organizations/{id}/products` | org:ADMIN | ✅ | 201 | Cria produto + variantes + preço inicial (atômico). |
| GET  | `/products/{id}` | org:MEMBER | — | 200 | Detalhe completo. |
| PATCH | `/products/{id}` | org:ADMIN | — | 200 | Atualiza metadata. |
| DELETE | `/products/{id}` | org:ADMIN | — | 204 | Soft-delete. |

### Imagens

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| POST | `/products/{id}/images/presign` | org:ADMIN | — | 200 | Devolve URL presigned PUT (15min). |
| PATCH | `/products/{id}/images` | org:ADMIN | — | 200 | Anexa lista de `image_keys` ao produto (max 8). |
| DELETE | `/products/{id}/images/{key}` | org:ADMIN | — | 204 | Remove imagem do MinIO + da lista. |

### Variantes

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| POST | `/products/{id}/variants` | org:ADMIN | ✅ | 201 | Adiciona nova variante. |
| PATCH | `/variants/{id}` | org:ADMIN | — | 200 | Atualiza atributos. |
| POST | `/variants/{id}/prices` | org:ADMIN | ✅ | 201 | Cria nova versão de preço (regra P-05). |
| GET  | `/variants/{id}/prices` | org:MEMBER | — | 200 | Histórico de preços. |

## Estoque

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| POST | `/variants/{id}/stock/movements` | org:MEMBER | ✅ | 201 | Cria movimento (IN/OUT/ADJUST/RESERVATION/RELEASE). |
| GET  | `/variants/{id}/stock/movements` | org:MEMBER | — | 200 | Livro-razão, cursor pagination. |
| GET  | `/variants/{id}/stock/balance` | org:MEMBER | — | 200 | Saldo derivado. |

## Carrinho + Pedidos (comprador)

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| GET  | `/cart` | user | — | 200 | Lista carrinhos abertos do user (1 por org). |
| POST | `/cart/items` | user | ✅ | 201 | Adiciona variante ao carrinho da org dela. |
| PATCH | `/cart/items/{id}` | user | — | 200 | Atualiza qty. |
| DELETE | `/cart/items/{id}` | user | — | 204 | Remove item. |
| POST | `/orders` | user | ✅ | 201 | Checkout — converte carrinho em pedido (regra D-01). |
| GET  | `/orders` | user | — | 200 | Lista pedidos do user, cursor pagination. |
| GET  | `/orders/{id}` | user | — | 200 | Detalhe + items + status atual. |
| GET  | `/orders/{id}/events` | user | — | 200 | **SSE** com transições de status em tempo real. |
| POST | `/orders/{id}/cancel` | user (buyer) | ✅ | 200 | Cancela enquanto `PENDING` ou `PAID`. |
| POST | `/orders/{id}/confirm-delivery` | user (buyer) | ✅ | 200 | Marca como `DELIVERED`. |

## Pedidos (vendedor)

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| GET  | `/organizations/{id}/orders` | org:MEMBER | — | 200 | Lista pedidos da org, filtros por status. |
| POST | `/orders/{id}/mark-paid` | org:ADMIN | ✅ | 200 | Mock de pagamento (regra D-09). |
| POST | `/orders/{id}/ship` | org:ADMIN | ✅ | 200 | Marca como expedido + tracking. |

## Reviews

| Método | Path | Auth | Idem | Status | Descrição |
|--------|------|------|------|--------|-----------|
| POST | `/variants/{id}/reviews` | user (com order DELIVERED) | ✅ | 201 | Avalia variante (regra R-01). |
| GET  | `/variants/{id}/reviews` | público | — | 200 | Reviews públicos. |
| DELETE | `/reviews/{id}` | user (autor) | — | 204 | Apaga avaliação própria. |

## Endpoints técnicos (SDK)

| Método | Path | Auth | Descrição |
|--------|------|------|-----------|
| GET  | `/health/liveness` | público | `make_health_router` — viva? |
| GET  | `/health/readiness` | público | viva + DB ok + Redis ok? |
| GET  | `/logs` | X-Token | `make_logs_router` — lê `logs/*.log`. |
| GET  | `/metrics` | público (interno) | Prometheus scrape (v0.26+). |
| GET  | `/admin/*` | session admin | `AdminSite` Jinja+HTMX. |
| GET  | `/docs` | público | Swagger UI. |
| GET  | `/redoc` | público | Redoc. |

## Exemplo: payload de checkout

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

Resposta:

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

Retentando com a mesma `Idempotency-Key` → mesma resposta (mesmo `order.id`, mesmo `total_cents`, sem nova `RESERVATION` no estoque).

## Próximo passo

Volta pro [índice do projeto](index.md) e comece a implementar — a ordem recomendada está lá.
