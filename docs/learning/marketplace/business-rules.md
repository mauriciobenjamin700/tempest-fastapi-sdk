# Regras de negócio — Marketplace

Esta página é o **contrato do domínio**. Toda decisão de modelagem, endpoint ou teste do projeto deve poder ser justificada por uma regra aqui. Quando o código discordar da regra, o código está errado (ou a regra precisa de uma RFC antes de mudar).

!!! warning "Convenção de severidade"
    **MUST** = invariante de domínio, violação é bug.
    **MAY** = abertura intencional, decidir caso a caso.

## 1. Usuários

| ID | Regra |
|----|-------|
| **U-01** | Qualquer pessoa **MUST** poder se cadastrar via `POST /auth/signup` sem autenticação. |
| **U-02** | Email **MUST** ser único (case-insensitive, normalizado em lowercase). |
| **U-03** | Senha **MUST** ter ≥ 12 caracteres e ser hasheada com bcrypt (`PasswordUtils.hash`). |
| **U-04** | Cadastro **MUST** retornar `201` com `{user_id, access_token, refresh_token}`. |
| **U-05** | Login (`POST /auth/login`) **MUST** aceitar email+senha e devolver os tokens. |
| **U-06** | Refresh (`POST /auth/refresh`) **MUST** validar `refresh_token` e emitir par novo. |
| **U-07** | Usuário **MAY** atualizar nome / foto via `PATCH /users/me`. Email é imutável. |
| **U-08** | Soft-delete (`DELETE /users/me`) **MUST** marcar `is_active=False` e revogar tokens emitidos via lista negra de `jti`. |

**Justificativa:** signup público é o gatilho do funil. Email único impede duplicatas; bcrypt impede vazamento de senha em caso de leak. Soft-delete preserva integridade referencial (pedidos antigos continuam apontando pro user inativo).

## 2. Organizações

| ID | Regra |
|----|-------|
| **O-01** | Qualquer user **MUST** poder criar organização via `POST /organizations`. |
| **O-02** | Um user **MUST NOT** criar mais que **2 organizações ativas** simultâneas (count de `Organization` com `owner_id = user.id AND is_active = true`). |
| **O-03** | Quem cria vira **OWNER** automaticamente — `Membership(role=OWNER)` na transação do create. |
| **O-04** | Cada organização **MUST** ter exatamente 1 OWNER. Transferência via `POST /organizations/{id}/transfer-ownership` (target deve já ser membro). |
| **O-05** | OWNER **MAY** deletar a organização (soft-delete). Deleta a org não libera o slot até o cleanup async (TaskIQ) zerar membros e converter pedidos abertos em cancelados. |
| **O-06** | Slug da organização **MUST** ser único globalmente (`acme-supplies`). |

**Justificativa:** limite de 2 orgs prende abuso de criação em massa pra reset de quotas. OWNER único elimina conflito de governança.

## 3. Membros e papéis

| ID | Regra |
|----|-------|
| **M-01** | Cada organização **MUST** ter no máximo **10 membros ativos** (incluindo o OWNER). |
| **M-02** | Papéis: `OWNER`, `ADMIN`, `MEMBER`. Hierarquia: `OWNER > ADMIN > MEMBER`. |
| **M-03** | `OWNER` **MAY** tudo dentro da org. |
| **M-04** | `ADMIN` **MAY** convidar/remover membros (exceto o OWNER), CRUD de produto/preço/estoque, gerenciar pedidos. |
| **M-05** | `MEMBER` **MAY** apenas leitura + alteração de produtos/estoque (não pode mexer em membership nem em billing). |
| **M-06** | Mudança de papel **MUST** vir do OWNER. ADMIN **MUST NOT** promover outro a ADMIN. |
| **M-07** | Um user **MAY** ser membro de até 5 organizações no total. |
| **M-08** | Saída voluntária (`DELETE /organizations/{id}/members/me`) **MUST** ser bloqueada pro OWNER — ele transfere primeiro. |

**Justificativa:** limites previnem sprawl. RBAC mínimo (3 papéis) cobre 90% dos casos sem virar Active Directory.

## 4. Convites

| ID | Regra |
|----|-------|
| **I-01** | OWNER/ADMIN **MAY** criar convite via `POST /organizations/{id}/invitations` com `{email, role}`. |
| **I-02** | Convite gera token opaco (`generate_opaque_token(48)`), armazena hash (`hash_opaque_token`) e envia link por email (Jinja2 via `EmailUtils.render_template`). |
| **I-03** | Convite **MUST** expirar em **7 dias** (`expires_at`). |
| **I-04** | `POST /invitations/{token}/accept` **MUST** validar: convite válido + não expirado + usuário logado tem o mesmo email. |
| **I-05** | Aceite cria `Membership(role=convite.role)` na transação que marca o convite `ACCEPTED`. |
| **I-06** | Convite duplicado para o mesmo email **MUST** invalidar o anterior antes de criar o novo (`status=SUPERSEDED`). |
| **I-07** | OWNER/ADMIN **MAY** revogar via `DELETE /invitations/{id}` antes do aceite (`status=REVOKED`). |
| **I-08** | Aceite **MUST** falhar com `409` quando a organização já estourou os 10 membros. |

## 5. Catálogo e produtos

| ID | Regra |
|----|-------|
| **P-01** | Produto **MUST** pertencer a uma organização (`Product.organization_id NOT NULL`). |
| **P-02** | Cada produto **MUST** ter ≥ 1 variante. Catálogo nunca mostra produto sem variante. |
| **P-03** | Variante **MUST** ter SKU único dentro da organização. |
| **P-04** | Variante carrega atributos livres `dict[str, str]` (cor/tamanho/voltagem). |
| **P-05** | Preço **MUST** ser versionado — alterar gera nova linha em `PriceHistory(variant_id, amount_cents, currency, valid_from)`. Histórico nunca é apagado. |
| **P-06** | Preço de exibição **MUST** ser o registro mais recente com `valid_from <= now()`. |
| **P-07** | Catálogo público (`GET /catalog`) **MUST** filtrar `is_active=True AND organization.is_active=True AND variant.stock > 0`. |
| **P-08** | Cada produto **MAY** ter até **8 imagens** (`UploadUtils + MinIOUploadStorage`). |
| **P-09** | Imagens **MUST** ser servidas via URL presigned com TTL de 1h. |

## 6. Estoque

| ID | Regra |
|----|-------|
| **S-01** | Estoque **MUST** ser um livro-razão append-only — `StockMovement(variant_id, kind, qty, reason, ref_type, ref_id, created_at)`. |
| **S-02** | `kind ∈ {IN, OUT, ADJUST, RESERVATION, RELEASE}`. Quantidade positiva sempre; o sinal é dado pelo `kind`. |
| **S-03** | Saldo atual = SUM(IN + RELEASE - OUT - RESERVATION + ADJUST*sinal). |
| **S-04** | `OUT` ou `RESERVATION` **MUST** falhar se o saldo resultante ficar negativo. |
| **S-05** | Toda mudança de estoque **MUST** ter `reason` (string) e `ref_type/ref_id` apontando pra entidade origem (ex: `order_id`). |
| **S-06** | Ajuste manual (`ADJUST`) **MUST** exigir papel ADMIN+ e gravar `audit_user_id`. |

## 7. Carrinho

| ID | Regra |
|----|-------|
| **C-01** | Cada user **MUST** ter no máximo **1 carrinho aberto por organização**. Itens de orgs diferentes vivem em carrinhos separados (ML/Shopee style). |
| **C-02** | Adicionar item (`POST /cart/items`) **MUST** validar estoque disponível antes de aceitar. |
| **C-03** | Item do carrinho carrega snapshot de preço + qty. |
| **C-04** | Carrinho **MUST** expirar após **24h** sem atualização — job TaskIQ varre e marca `EXPIRED`. |

## 8. Pedidos

| ID | Regra |
|----|-------|
| **D-01** | Checkout (`POST /orders`) **MUST** ser idempotente via header `Idempotency-Key`. |
| **D-02** | Checkout converte 1 carrinho em 1 pedido. Carrinho fica `CONVERTED`. |
| **D-03** | Pedido segue a máquina: `PENDING → PAID → SHIPPED → DELIVERED` (ver [Fluxos](flows.md)). |
| **D-04** | `CANCELLED` é alcançável de `PENDING` e `PAID`; depois de `SHIPPED` só `RETURNED`. |
| **D-05** | Criação de pedido **MUST** gerar `RESERVATION` no estoque (atômico com o `INSERT` do pedido). |
| **D-06** | Pagamento confirmado **MUST** converter `RESERVATION` em `OUT`. |
| **D-07** | Cancelamento **MUST** gerar `RELEASE` (devolve estoque). |
| **D-08** | Mudança de estado **MUST** publicar evento SSE pro stream `orders/{id}/events` do comprador. |
| **D-09** | Pagamento neste projeto é **mock** — endpoint admin marca como pago. Sem integração externa. |

## 9. Reviews

| ID | Regra |
|----|-------|
| **R-01** | Review (`POST /products/{id}/reviews`) **MUST** exigir que o user tenha um pedido `DELIVERED` desse produto. |
| **R-02** | Um user **MAY** revisar cada variante uma única vez (constraint `UNIQUE(user_id, variant_id)`). |
| **R-03** | Score **MUST** estar entre 1 e 5. |

## 10. Limites e quotas globais

| ID | Regra |
|----|-------|
| **G-01** | Rate-limit padrão `100 req/min/user` via `RateLimitMiddleware`. |
| **G-02** | Body limit `5 MiB` (uploads de imagem usam presigned PUT, então API não recebe os bytes). |
| **G-03** | Logs estruturados sempre — `configure_logging(log_dir="logs")`. Não há "log de produção desativado". |
| **G-04** | Toda exceção não tratada gera 500 no `error.log` E `500.log` (via `register_exception_handlers`). |

## Próximos passos

Com as regras na cabeça, vá para o **[Modelo de domínio](domain.md)** ver as entidades + diagramas, e depois **[Fluxos críticos](flows.md)** ver como as regras se realizam.
