# Audit trail

`AuditMixin` guarda **quem** mexeu por último (`created_by` / `updated_by`) e o `BaseModel` guarda **quando** (`created_at` / `updated_at`). Nenhum dos dois guarda o **histórico** das mudanças. O audit trail adiciona um log append-only: uma linha por create / update / delete, com o ator, a ação e um diff antes/depois das colunas alteradas.

A linha de auditoria é gravada na **mesma transação** da mudança — `add_audited` / `update_audited` adicionam a linha de auditoria e a linha de negócio e commitam as duas juntas, o mesmo padrão que o outbox usa —, então uma entrada de auditoria nunca referencia uma mudança que foi revertida.

## A tabela de auditoria

Subclasse `BaseAuditLogModel` e escolha um `__tablename__` (`audit_log` por convenção), igual ao `BaseOutboxModel`:

```python
from tempest_fastapi_sdk import BaseAuditLogModel


class AuditLogModel(BaseAuditLogModel):
    """Log append-only de mutações por entidade."""

    __tablename__ = "audit_log"
```

Herda os quatro campos canônicos (`id`, `is_active`, `created_at`, `updated_at`) mais: `entity` (nome do model), `entity_id` (id da linha, como texto), `action` (`AuditAction`), `actor` (quem fez, ou `None`), `changes` (o diff em JSON) e `context` (metadados opcionais — request id, motivo). Evento de domínio, IP, user agent e autor com FK ganham colunas próprias em [Evento, origem da requisição e autor](#evento-origem-da-requisicao-e-autor).

## Ligando no repository

Passe `audit_model=` no repository e use as variantes auditadas. Elas gravam a linha de negócio **e** a de auditoria juntas:

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository

from src.db.models import AuditLogModel, ProductModel


class ProductRepository(BaseRepository[ProductModel]):
    """Repository de produtos com trilha de auditoria."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa o repository.

        Args:
            session (AsyncSession): A sessão async do banco.
        """
        super().__init__(session, model=ProductModel, audit_model=AuditLogModel)
```

### Create

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.db.models import ProductModel
from src.db.repositories import ProductRepository

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = ProductRepository(session)

# O ator é o id de quem já está autenticado (ex.: `current_user.id`).
actor_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


async def main() -> None:
    """Run this example."""
    product = await repo.add_audited(ProductModel(name="Widget"), actor=str(actor_id))
    # grava o produto + uma entrada CREATE com {"after": {...}}


asyncio.run(main())
```

### Update — tire um snapshot antes de mutar

`update_audited` precisa do estado **anterior** para calcular o diff. Tire o snapshot com `repo.snapshot(...)` antes de alterar a instância:

```python
from uuid import UUID

from src.db.repositories import ProductRepository


async def rename_product(
    repo: ProductRepository, product_id: UUID, name: str, actor: str
) -> None:
    """Renomeia um produto registrando o diff na auditoria.

    Args:
        repo (ProductRepository): O repository de produtos.
        product_id (UUID): O id do produto.
        name (str): O novo nome.
        actor (str): Quem realizou a alteração.

    Raises:
        NotFoundException: Se o produto não existe.
    """
    product = await repo.get_by_id(product_id)
    before = repo.snapshot(product)                  # ← antes de mutar
    product.name = name
    await repo.update_audited(product, before, actor=actor)
    # grava uma entrada UPDATE com {"name": {"before": "...", "after": "..."}}
```

### Delete

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.db.repositories import ProductRepository

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = ProductRepository(session)

product_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")
actor_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


async def main() -> None:
    """Run this example."""
    product = await repo.get_by_id(product_id)
    await repo.delete_audited(product, actor=str(actor_id))
    # apaga a linha + grava uma entrada DELETE com {"before": {...}}


asyncio.run(main())
```

!!! warning "Mesma transação"
    As três variantes gravam a linha de negócio e a de auditoria **juntas**. Chamadas soltas, elas commitam as duas no fim; dentro de um bloco `repo.transaction()` (ou num repository com `autocommit=False`) elas só fazem `flush`, e o commit é do bloco — se o bloco aborta, as duas linhas somem juntas. Nos dois casos, se a auditoria falhar a mudança é revertida — nunca fica meia gravada. Veja [Transações](transactions.md). Repositories sem `audit_model` levantam `RuntimeError` ao chamar os métodos auditados.

## Evento, origem da requisição e autor

Só com `actor` e `context`, "tudo o que o usuário X fez" vira varredura de JSON, e o IP e o user agent ficam em chaves que cada serviço nomeia de um jeito. Três peças tiram isso do `context`:

- **`AuditRequestMixin`** — colunas `event` (indexada), `ip` e `user_agent` na tabela de auditoria.
- **`AuditRequestContext.from_request(request, trusted_ip_header=...)`** — lê o IP do cliente (pelo `get_client_ip`) e o `User-Agent`.
- **`record_event(...)`** — grava um evento de domínio que não muda linha nenhuma (`action="event"`).

E um ponto de extensão: declare `actor_id` com FK na sua subclasse e passe `actor_id=` — sem sobrescrever `new_entry`.

```python
import asyncio
from collections.abc import AsyncGenerator
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Request
from sqlalchemy import ForeignKey, String, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuditRequestContext,
    AuditRequestMixin,
    BaseAuditLogModel,
    BaseModel,
    BaseRepository,
)


class UserModel(BaseModel):
    """Conta de usuário."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False)


class AuditLogModel(AuditRequestMixin, BaseAuditLogModel):
    """Auditoria com evento, origem e autor ligado ao usuário."""

    __tablename__ = "audit_log"

    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class ConsentModel(BaseModel):
    """Consentimento dado por um usuário."""

    __tablename__ = "consents"

    user_id: Mapped[UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)


class ConsentRepository(BaseRepository[ConsentModel]):
    """Repository de consentimentos com trilha de auditoria."""

    def __init__(self, session: AsyncSession) -> None:
        """Inicializa o repository.

        Args:
            session (AsyncSession): A sessão async do banco.
        """
        super().__init__(session, model=ConsentModel, audit_model=AuditLogModel)


db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
app = FastAPI()


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Entrega uma sessão por requisição.

    Yields:
        AsyncSession: A sessão aberta.
    """
    async with db.get_session_context() as session:
        yield session


@app.post("/users/{user_id}/consents")
async def grant_consent(
    user_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Registra o consentimento e o pedido de exportação do usuário.

    Args:
        user_id (UUID): O usuário que consente.
        request (Request): A requisição, de onde saem IP e user agent.
        session (AsyncSession): A sessão do banco.

    Returns:
        dict[str, str]: O id do consentimento criado.
    """
    origin = AuditRequestContext.from_request(request, trusted_ip_header="x-real-ip")
    repo = ConsentRepository(session)
    consent = await repo.add_audited(
        ConsentModel(user_id=user_id, purpose="marketing"),
        actor=str(user_id),
        actor_id=user_id,
        event="consent.granted",
        request_context=origin,
    )
    await repo.record_event(
        "export.requested",
        actor=str(user_id),
        actor_id=user_id,
        request_context=origin,
    )
    return {"id": str(consent.id)}


async def main() -> None:
    """Run this example."""
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="ana@example.com")
        session.add(user)
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        await client.post(
            f"/users/{user.id}/consents",
            headers={"X-Real-IP": "203.0.113.7", "User-Agent": "app/2.1"},
        )

    async with db.get_session_context() as session:
        await session.execute(delete(UserModel))
        await session.commit()
        rows = (await session.execute(select(AuditLogModel))).scalars().all()
        for row in sorted(rows, key=lambda r: r.action):
            print(row.action, row.event, row.entity_id != "", row.ip, row.user_agent, row.actor_id)

    await db.disconnect()


asyncio.run(main())
```

Saída:

```text
create consent.granted True 203.0.113.7 app/2.1 None
event export.requested False 203.0.113.7 app/2.1 None
```

Pedaço por pedaço:

- **`AuditRequestMixin`** entra **antes** de `BaseAuditLogModel` e acrescenta as três colunas. `event` é indexada: "todo consentimento concedido" é uma busca por índice, não um parse de JSON.
- **`actor_id`** é seu: a FK aponta para a **sua** tabela de usuários, e o SDK não precisa saber qual é. O `ON DELETE SET NULL` é o que deixa o `actor_id` em `None` na saída — a conta foi apagada, a trilha ficou, sem o vínculo com o titular.
- **`from_request`** exige `trusted_ip_header` sem default: confiar num header de proxy é um fato do seu deploy, e o SDK não adivinha. Passe o único header que a borda sobrescreve (`"x-real-ip"`), ou `None` para usar o peer da conexão. Valor que não é endereço IP vira `None`, e `User-Agent` acima de 512 caracteres é cortado — o header é do cliente, e um gigante não pode derrubar a escrita de negócio.
- **`record_event`** grava `action="event"`, com `entity` igual ao model do repository e `entity_id` vazio (ou o id do `subject=` que você passar), e `changes` igual a `{}` se você não mandar payload.
- `request_context=` e `ip=` / `user_agent=` são alternativas: passar os dois levanta `ValueError`.

!!! warning "Tabela que já existe"
    As colunas vêm de um mixin, não do `BaseAuditLogModel`, de propósito: quem já tem `audit_log` criado continua funcionando **sem migration**, chamando só com `actor` / `context`. Acrescentar `AuditRequestMixin` (ou `actor_id`) a uma tabela existente é mudança de schema — gere a migration do Alembic antes de subir.

!!! info "Valor para coluna que a tabela não tem é recusado"
    Passar `event=`, `ip=`, `user_agent=` ou `actor_id=` para uma tabela sem a coluna levanta `ValueError` antes do commit, e a transação inteira é revertida — inclusive a linha de negócio:

    ```text
    AuditLogModel has no column for: event, ip. Mix AuditRequestMixin in for event/ip/user_agent, or declare an actor_id column for actor_id.
    ```

    Perder um fato de auditoria em silêncio seria pior do que falhar a chamada. Pelo mesmo motivo, `record_event` exige o mixin: o nome do evento é o que a linha registra.

## Helpers avulsos

Fora do repository, `snapshot_model(instance)` e `diff_snapshots(before, after)` ficam disponíveis, e `BaseAuditLogModel.for_create / for_update / for_delete` constroem a entrada (sem adicionar à sessão) quando você quer controlar a gravação manualmente.

## Recapitulando

- `BaseAuditLogModel` (subclasse com `__tablename__`) + `AuditAction`.
- `repo = Repository(session, model=..., audit_model=AuditLogModel)`.
- `add_audited` / `update_audited(model, before)` / `delete_audited` — negócio + auditoria na mesma tx.
- `repo.snapshot(model)` antes de mutar; `snapshot_model` / `diff_snapshots` para uso manual.
- `AuditRequestMixin` (opt-in) + `event=` / `ip=` / `user_agent=` / `request_context=AuditRequestContext.from_request(...)`; `actor_id=` para a FK que você declarar; `record_event(...)` para evento sem mutação.
