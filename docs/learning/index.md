# Projetos de aprendizado

Esta seção reúne projetos didáticos construídos **inteiramente sobre o `tempest-fastapi-sdk`** para você aprender a usar o SDK na prática. Cada projeto traz:

- **Regras de negócio** completas (o "porquê" antes do código).
- **Diagramas UML** — modelo de domínio (classe), ER, sequência por fluxo crítico.
- **Mapa de endpoints** REST com a forma do request/response.
- **State machines** para entidades com ciclo de vida (pedidos, convites…).
- **Estratégia de testes** mostrando como exercitar cada camada.

!!! tip "Por que projetos de aprendizado e não só receitas?"
    Receitas (em [Recipes](../recipes/index.md)) ensinam **um pedaço** do SDK isolado. Projetos de aprendizado mostram como as peças **se encaixam** num cenário realista — pagamentos, multi-tenant, controle de acesso, auditoria, estoque — sem integrações externas que ofusquem o aprendizado.

## Projetos disponíveis

### 🛒 [Marketplace de produtos](marketplace/index.md)

Plataforma estilo Mercado Livre / Shopee onde:

- Usuários se cadastram via endpoint público (`POST /signup`).
- Cada usuário pode criar **até 2 organizações** (lojistas).
- Cada organização pode convidar **até 10 membros** (donos + admins + colaboradores).
- Membros cadastram produtos com variantes, preços versionados e controle de estoque.
- Qualquer usuário logado navega o catálogo público e faz pedidos.
- Pedidos têm máquina de estados (`PENDING → PAID → SHIPPED → DELIVERED` + cancelamento).
- Estoque é um livro-razão append-only (auditável).

Cobre auth JWT, multi-tenant via membership, paginação cursor, idempotência em checkout, upload (imagens de produtos via MinIO), email transacional (convites/confirmações), SSE (status de pedido em tempo real), métricas Prometheus.

## Como usar esta seção

1. Leia primeiro a **regra de negócio** — entenda o domínio antes de codar.
2. Estude os **diagramas** — modelo + sequência te dão o "mapa".
3. Cada arquivo de regra aponta para os primitivos do SDK que aplicam (ex: `BaseUserModel`, `make_jwt_user_dependency`, `IdempotencyMiddleware`).
4. Implemente uma camada de cada vez — model → repository → service → controller → router.
5. Compare com a referência no repo `tempest-marketplace-example` (link no final de cada projeto).

## Próximos projetos planejados

- **📚 Biblioteca digital** — empréstimos, reservas, devoluções com SLA.
- **📅 Sistema de agendamento** — calendários multi-usuário, slots, conflitos.
- **💸 Cobrança recorrente** — assinaturas, ciclos, dunning, webhooks de pagamento.

Sugestões? Abra issue em <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> com o caso de uso real que motivaria o projeto.
