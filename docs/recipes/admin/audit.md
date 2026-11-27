---
description: "Use para mostrar no admin quem mudou o quê via `audit_model`, a atividade recente no dashboard e restaurar uma versão pelo histórico."
---

# Histórico de auditoria

Use para mostrar quem mudou o quê no detail, a atividade recente no dashboard e desfazer pelo histórico.

Parte da receita [Painel admin](../admin.md).

O painel já carimba `created_by` / `updated_by`. Para ver **o quê** mudou
(não só quem/quando), passe um `audit_model` — a mesma tabela
`BaseAuditLogModel` que o `BaseRepository` já escreve — e o detail ganha
uma **timeline** por registro.

Primeiro, a tabela de auditoria e um repository que a alimenta:

```python
# src/db/models/audit.py
from tempest_fastapi_sdk import BaseAuditLogModel


class AuditLog(BaseAuditLogModel):
    __tablename__ = "audit_log"
```

```python
# grave a trilha nas escritas (create/update/delete)

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.db.audit import snapshot_model

from src.db.models import AuditLog, OrderModel, UserModel

current_user = UserModel(email="ana@example.com")
# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))


repo = BaseRepository(session, model=OrderModel, audit_model=AuditLog)


async def main() -> None:
    """Run this example."""
    order = await repo.add_audited(OrderModel(...), actor=str(current_user.id))

    before = snapshot_model(order)
    order.status = "shipped"
    await repo.update_audited(order, before, actor=str(current_user.id))


asyncio.run(main())
```

Então plugue o mesmo `audit_model` no admin:

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import AuditLog, OrderModel


site.register(AdminModel(model=OrderModel, audit_model=AuditLog))
```

No detail de cada pedido aparece um bloco **History**: uma entrada por
mudança (`create` / `update` / `delete`, com cor), ator e data, e um diff
campo-a-campo (antes → depois). As 50 entradas mais recentes, da mais
nova para a mais antiga.

!!! info "Como o match é feito"
    A viewer busca as linhas de auditoria onde `entity` == nome do modelo
    (ex.: `"OrderModel"`) e `entity_id` == id do registro — exatamente o
    que `add_audited` / `update_audited` / `delete_audited` gravam. Sem
    `audit_model`, o detail continua igual (só os carimbos
    `created_by`/`updated_by`).

## Atividade recente no dashboard (`recent_activity=`)

A linha do tempo acima é por registro. Para ver o que mudou no painel
inteiro sem abrir registro por registro, ligue o feed do dashboard no
`AdminSite`:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import AuditLog, OrderModel, TicketModel

site = AdminSite(title="Loja", recent_activity=20)
site.register(AdminModel(model=OrderModel, audit_model=AuditLog))
site.register(AdminModel(model=TicketModel, audit_model=AuditLog))
```

O dashboard ganha o bloco **Atividade recente**: as últimas 20 entradas de
todos os models que declaram `audit_model`, mescladas da mais nova para a
mais antiga, cada uma com a ação, o model, o rótulo do registro (link para
o detail), a data e o autor.

O feed passa pelas mesmas regras do resto do painel:

- model em que o principal não tem `VIEW` no `access_policy` não aparece;
- entrada de uma linha fora do `row_scope` do model não aparece — nem o
  rótulo. Num model com escopo, também some a entrada de uma linha que não
  existe mais (um `delete`), porque nada prova que ela era alcançável; sem
  escopo, ela aparece sem link;
- o autor segue a regra do painel de auditoria do detail: com `row_scope`
  no model de usuário, o nome só aparece para quem o principal alcança;
  senão, o id.

!!! info "Desligado por padrão — o custo"
    `recent_activity=None` é o padrão porque o feed custa consultas em
    **toda** carga do dashboard: uma por tabela de auditoria, uma por model
    auditado (para manter só as linhas do escopo e ler os rótulos) e uma
    busca de autor por autor distinto. Para compensar as entradas que o
    escopo esconde, o painel lê até `10 × recent_activity` entradas por
    tabela; se o escopo esconder mais que isso entre as mais novas, o feed
    mostra menos que o pedido.
## Desfazer pelo histórico (Restaurar)

Cada entrada `update` da timeline ganha um link **Restaurar**. Ele não
grava nada: abre o form de edição do registro com os valores **de antes**
daquela mudança já preenchidos — os outros campos ficam como estão hoje — e
um aviso dizendo quais campos vieram do histórico. O operador revisa e
salva pelo caminho de sempre, então valem a permissão `EDIT`, a validação
do form, o `validate_edit`, a conferência de FK, o `row_scope` e a trava
otimista (o form carrega a versão **atual** da linha: se outro save chegou
depois de abrir, a resposta é `409`). A entrada nova da timeline sai do
mesmo lugar que grava as outras edições — o painel não escreve auditoria
sozinho.

O link só aparece para quem tem `EDIT` no model, e só em entrada `update`
de que algum campo possa voltar. Ficam de fora, e continuam com o valor
atual:

- campos de upload (`upload_fields`) e de senha (`password_fields`);
- campos escondidos — `exclude_fields` e os segredos do model — e todo
  valor que a auditoria gravou como `[redacted]`;
- campos fora do form de edição (`readonly_fields`, `readonly_fields_for`).

Um valor antigo que hoje é inválido — uma opção que saiu do `choices`, por
exemplo — aparece preenchido (o `<select>` ganha a opção, para o browser
não trocar pela primeira em silêncio) e vira erro de campo no save, sem
gravar nada.
