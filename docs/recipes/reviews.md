# Comentários + avaliações

Comentários e avaliações de **0 a 5 estrelas** sobre qualquer coisa —
produto, post, evento — sem uma tabela por tipo. O módulo
`tempest_fastapi_sdk.reviews` é polimórfico: cada comentário/nota aponta
para um alvo via o par `(target_type, target_id)`.

Três peças, sobre os primitivos do SDK:

- **Tabelas abstratas** — `BaseCommentModel` (encadeável via `parent_id`)
  e `BaseRatingModel` (nota `0..5`, um voto por usuário) + fábricas
  `make_*`.
- **`ReviewService`** — comentar, avaliar (upsert), buscar a nota do
  usuário e **agregar** (média + contagem + distribuição por estrela).
- **`make_reviews_router`** — os endpoints HTTP.

!!! info "Sem extra"
    Só o núcleo do SDK. A nota usa o novo campo validado `RatingField`
    (`Annotated[int, 0..5]`).

## As tabelas

O SDK entrega a linha abstrata; o projeto entrega a concreta com a FK do
autor/usuário:

```python
from tempest_fastapi_sdk.reviews import BaseCommentModel, BaseRatingModel
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID


class CommentModel(BaseCommentModel):
    __tablename__ = "comments"

    author_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class RatingModel(BaseRatingModel):
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint(
            "target_type", "target_id", "user_id", name="uq_rating_target_user"
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

!!! tip "Atalho para testes"
    ```python
    from tempest_fastapi_sdk.reviews import make_comment_model, make_rating_model

    Comment = make_comment_model()
    Rating = make_rating_model()
    ```

## O serviço

`ReviewService` recebe dois repositórios (comentários e notas):

```python
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.reviews import ReviewService
from sqlalchemy.ext.asyncio import AsyncSession


def build_review_service(session: AsyncSession) -> ReviewService:
    return ReviewService(
        comments=BaseRepository(session, model=CommentModel),
        ratings=BaseRepository(session, model=RatingModel),
    )


async def demo(session: AsyncSession, product_id: UUID, user_id: UUID) -> None:
    service = build_review_service(session)

    await service.add_comment("product", product_id, user_id, "Excelente!")

    # Um voto por usuário — reavaliar atualiza a mesma linha.
    await service.rate("product", product_id, user_id, 5)
    await service.rate("product", product_id, user_id, 4)  # continua 1 nota

    aggregate = await service.aggregate("product", product_id)
    print(aggregate.average)       # 4.0
    print(aggregate.count)         # 1
    print(aggregate.distribution)  # {0: 0, 1: 0, ..., 4: 1, 5: 0}
```

`aggregate` devolve um `RatingAggregateSchema` com `average` (0.0 quando
não há notas), `count` e `distribution` (contagem por estrela, chaveada
`0..5`) — exatamente os números que um widget "4,3 ★ (128 avaliações)"
renderiza.

## O router

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.reviews import make_reviews_router


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session


def current_user_id() -> UUID:
    ...  # sua dependência de auth resolvendo o UUID do usuário


app = FastAPI()
app.include_router(
    make_reviews_router(
        service_factory=build_review_service,
        session_factory=get_session,
        current_user_id=current_user_id,
    )
)
```

Endpoints montados:

| Método | Rota | Faz |
| --- | --- | --- |
| `POST` | `/api/reviews/{target_type}/{target_id}/comments` | Comenta (autor = usuário logado) |
| `GET` | `/api/reviews/{target_type}/{target_id}/comments` | Pagina os comentários |
| `POST` | `/api/reviews/{target_type}/{target_id}/rating` | Define a nota 0–5 (upsert) |
| `GET` | `/api/reviews/{target_type}/{target_id}` | Agregado de estrelas |

Comentar e avaliar exigem autenticação; listar e agregar são públicos.
Uma nota fora de `0..5` é rejeitada com `422` pela validação de
`RatingField`.

## Recapitulando

- Alvo polimórfico `(target_type, target_id)` — uma dupla de tabelas
  serve todo tipo reavaliável.
- `rate` faz upsert: um voto por usuário por alvo.
- `aggregate` entrega média + contagem + distribuição prontas para a UI.
- `make_reviews_router` monta comentários + notas com validação `0..5`.
