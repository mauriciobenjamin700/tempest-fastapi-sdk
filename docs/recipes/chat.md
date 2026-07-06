# Chat (conversas + mensagens)

Um módulo de chat encadeado pronto sobre os primitivos do SDK
(`BaseModel` / `BaseRepository` / paginação / SSE). Você herda as tabelas
concretas, monta o router e ganha conversas, mensagens e entrega em tempo
real — sem escrever a camada de dados na mão.

O módulo `tempest_fastapi_sdk.chat` traz três peças:

- **Tabelas abstratas** — `BaseConversationModel`,
  `BaseConversationParticipantModel`, `BaseMessageModel` (+ fábricas
  `make_*` para testes/scripts).
- **`ChatService`** — a lógica de negócio: iniciar conversa, postar
  mensagem, listar histórico, listar as conversas de um usuário.
- **`make_chat_router`** — os endpoints HTTP, no mesmo formato de
  `make_auth_router` / `make_web_push_router`.

!!! info "Sem extra"
    O módulo usa só o núcleo do SDK. Nada de instalar extras — importe e
    use.

## As tabelas

Como as outras tabelas reutilizáveis do SDK, o SDK entrega a linha
**abstrata** e o seu projeto entrega a **concreta** (para a FK e o
`__tablename__` viverem na metadata da aplicação). Escreva-as à mão em
produção:

```python
from tempest_fastapi_sdk.chat import (
    BaseConversationModel,
    BaseConversationParticipantModel,
    BaseMessageModel,
)
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID


class ConversationModel(BaseConversationModel):
    __tablename__ = "conversations"


class ConversationParticipantModel(BaseConversationParticipantModel):
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_participant"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class MessageModel(BaseMessageModel):
    __tablename__ = "messages"

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

!!! tip "Atalho para testes"
    Em testes e scripts, as fábricas montam a classe concreta em runtime:

    ```python
    from tempest_fastapi_sdk.chat import (
        make_conversation_model,
        make_conversation_participant_model,
        make_message_model,
    )

    Conversation = make_conversation_model()
    Participant = make_conversation_participant_model()
    Message = make_message_model()
    ```

## O serviço

`ChatService` recebe três repositórios (e, opcionalmente, um `SSEBroker`
para tempo real):

```python
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService
from sqlalchemy.ext.asyncio import AsyncSession


def build_chat_service(session: AsyncSession) -> ChatService:
    return ChatService(
        conversations=BaseRepository(session, model=ConversationModel),
        participants=BaseRepository(session, model=ConversationParticipantModel),
        messages=BaseRepository(session, model=MessageModel),
    )


async def demo(session: AsyncSession, alice: UUID, bob: UUID) -> None:
    service = build_chat_service(session)

    # O criador entra como participante automaticamente.
    conversation = await service.start_conversation(alice, [bob], title="Projeto X")

    await service.post_message(conversation.id, alice, "Bora começar?")
    await service.post_message(conversation.id, bob, "Bora!")

    page = await service.list_messages(conversation.id, page=1, page_size=20)
    for message in page["items"]:
        print(message.sender_id, message.body)

    minhas = await service.list_conversations(alice)  # [] quando não há nenhuma
```

`list_messages` devolve o dicionário de paginação offset padrão do SDK
(`items` já mapeados para `MessageResponseSchema`, `total`, `page`,
`size`, `pages`), ordenado do mais antigo para o mais novo.

## O router

`make_chat_router` recebe **como** resolver a sessão, o serviço e o
usuário autenticado — igual aos outros routers do SDK:

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.chat import make_chat_router


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session


def current_user_id() -> UUID:
    ...  # sua dependência de auth (JWT/sessão) resolvendo o UUID do usuário


app = FastAPI()
app.include_router(
    make_chat_router(
        service_factory=build_chat_service,
        session_factory=get_session,
        current_user_id=current_user_id,
    )
)
```

Endpoints montados (todos exigem autenticação):

| Método | Rota | Faz |
| --- | --- | --- |
| `POST` | `/api/chat/conversations` | Inicia conversa (criador vira participante) |
| `GET` | `/api/chat/conversations` | Lista as conversas do usuário |
| `POST` | `/api/chat/conversations/{id}/messages` | Posta mensagem (só participante) |
| `GET` | `/api/chat/conversations/{id}/messages` | Pagina o histórico (só participante) |
| `GET` | `/api/chat/conversations/{id}/stream` | SSE de novas mensagens (só participante) |

!!! warning "Guarda de participante"
    Postar, ler e assinar exigem que o usuário autenticado seja
    participante da conversa; caso contrário o router responde `403`.

## Tempo real via SSE

Injete um `SSEBroker` no serviço e cada mensagem postada também é
publicada no canal da conversa (`str(conversation_id)`), reaproveitando o
fan-out SSE que o SDK já tem. Sem broker, o endpoint `/stream` responde
`404`.

```python
from tempest_fastapi_sdk.sse import SSEBroker

broker = SSEBroker()  # single-process; passe redis=<client> para multi-worker


def build_chat_service(session: AsyncSession) -> ChatService:
    return ChatService(
        conversations=BaseRepository(session, model=ConversationModel),
        participants=BaseRepository(session, model=ConversationParticipantModel),
        messages=BaseRepository(session, model=MessageModel),
        broker=broker,
    )
```

O cliente assina com um `EventSource` apontando para
`/api/chat/conversations/{id}/stream` e recebe eventos `message` cujo
`data` é o JSON de `MessageResponseSchema`. Veja a receita de
**[Server-Sent Events »](sse.md)** para o lado do cliente e a ponte
Redis multi-worker.

## Recapitulando

- Herde `BaseConversationModel` / `BaseConversationParticipantModel` /
  `BaseMessageModel` e aponte as FKs para o seu `UserModel`.
- `ChatService` cobre iniciar/postar/listar; devolve schemas, não ORM.
- `make_chat_router` monta os endpoints com guarda de participante.
- Passe um `SSEBroker` para ganhar entrega em tempo real de graça.
