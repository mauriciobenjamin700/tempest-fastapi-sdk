# Chat (conversas + mensagens)

Um módulo de chat encadeado pronto sobre os primitivos do SDK
(`BaseModel` / `BaseRepository` / paginação / SSE). Você herda as tabelas
concretas, monta o router e ganha conversas, mensagens e entrega em tempo
real — sem escrever a camada de dados na mão.

O módulo `tempest_fastapi_sdk.chat` traz três peças:

- **Tabelas abstratas** — `BaseConversationModel`,
  `BaseConversationParticipantModel`, `BaseMessageModel`,
  `BaseMessageAttachmentModel`, `BaseMessageReactionModel` (+ fábricas
  `make_*` para testes/scripts).
- **`ChatService`** — a lógica de negócio: iniciar conversa, postar,
  responder, editar, apagar para todos, reagir, encaminhar, marcar como
  lida e administrar o grupo.
- **`make_chat_router`** — os endpoints HTTP, no mesmo formato de
  `make_auth_router` / `make_web_push_router`.

!!! info "O que o módulo decide por você"
    A superfície existe para não deixar cada serviço tomar sozinho as
    decisões que erram igual em todo lugar:

    - **recibo de leitura é marca d'água no participante**, não uma linha
      por mensagem por leitor — num grupo de 200 pessoas o caminho óbvio
      custa 200 linhas *por mensagem*, e marcar a thread como lida vira
      um bulk insert;
    - **uma reação por pessoa**, substituída ao reagir de novo — é a
      `UniqueConstraint(message_id, user_id)`, e alargá-la para incluir o
      emoji é o que transforma double-tap em duas reações;
    - **apagar para todos limpa o `body` de verdade** e mantém a linha,
      para a thread preservar a forma e as respostas continuarem tendo o
      que citar;
    - **conversa direta é idempotente por par** — duas threads entre as
      mesmas duas pessoas é um estado que o usuário não conserta pela UI;
    - **`client_id` gerado pelo cliente** faz o retry devolver a mensagem
      que já foi gravada, em vez de postar a segunda.

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

As colunas que essas tabelas trazem — `kind`, `client_id`,
`reply_to_id`, `forwarded_from_id`, `forward_score`, `edited_at`,
`revoked_at`, `payload` na mensagem; `role`, `joined_at`, `left_at`,
`history_from`, `muted_until`, `is_pinned`, `is_archived`,
`last_read_at`, `last_read_message_id`, `last_delivered_at` no
participante — são o conjunto mínimo de uma thread entre pessoas. Duas
delas carregam constraint que **muda o comportamento**, então a classe
escrita à mão precisa declará-las:

```python
from uuid import UUID

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.chat import BaseMessageModel, BaseMessageReactionModel


class MessageModel(BaseMessageModel):
    """Uma mensagem postada numa conversa."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("sender_id", "client_id", name="uq_messages_sender_client"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class MessageReactionModel(BaseMessageReactionModel):
    """A reação de uma pessoa a uma mensagem."""

    __tablename__ = "message_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_reaction_per_user"),
    )

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

A primeira constraint é o que faz o retry devolver a linha existente em
vez de postar a segunda; a segunda é o que faz reagir de novo
**substituir** em vez de empilhar.

!!! danger "`message_id` do anexo é nullable, e isso é o desenho"
    Upload e post são **duas chamadas**: um vídeo de 40 MB que falha no
    último byte não pode levar a legenda junto, e um post repetido não
    pode reenviar o arquivo. A linha do anexo nasce quando os bytes
    chegam, e a mensagem que a reivindica pode não existir por mais um
    minuto — ou nunca, se o remetente desistir. Declarar a coluna
    `NOT NULL` faz **todo** upload falhar com erro de integridade.

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
    Attachment = make_message_attachment_model()
    Reaction = make_message_reaction_model()
    ```

    As fábricas já declaram as constraints acima — é a forma mais rápida
    de ganhar o comportamento certo num teste.

## O serviço

`ChatService` recebe três repositórios (e, opcionalmente, um `SSEBroker`
para tempo real):

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService

from src.db.models import ConversationModel, ConversationParticipantModel, MessageModel


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

Rodando o `demo`, os dois `print` emitem cada mensagem em ordem (mais
antiga primeiro), com o UUID do remetente e o corpo:

```text
2b1e9a4c-1f0d-4c3a-9c21-8e7f0a1b2c3d Bora começar?
7d3c5f8a-6b2e-4a19-b0d4-1c2e3f4a5b6c Bora!
```

`list_messages` devolve o dicionário de paginação offset padrão do SDK
(`items` já mapeados para `MessageResponseSchema`, `total`, `page`,
`page_size`, `pages`), ordenado do mais antigo para o mais novo.

### Responder, e o stub que a citação carrega

Responder é a operação mais usada de um chat em grupo. A parte cara não
é a FK: é o **stub**. A citação tem que renderizar sem um segundo
round-trip, e reenviar a mensagem-pai inteira (com anexos e reações)
sob cada resposta multiplica o payload da página.

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService, MessageCreateSchema


async def responder(
    service: ChatService,
    conversation_id: UUID,
    alice: UUID,
    bob: UUID,
) -> None:
    """Posta uma pergunta e responde citando-a."""
    parent = await service.post_message(
        conversation_id,
        alice,
        MessageCreateSchema(body="alguém revisa o PR?"),
    )
    reply = await service.post_message(
        conversation_id,
        bob,
        MessageCreateSchema(body="eu reviso", reply_to_id=parent.id),
    )

    assert reply.reply_to is not None
    print(reply.reply_to.sender_id, reply.reply_to.excerpt, reply.reply_to.revoked)
```

```text
2b1e9a4c-1f0d-4c3a-9c21-8e7f0a1b2c3d alguém revisa o PR? False
```

!!! danger "Apagar a mensagem citada apaga a citação"
    O campo `revoked` é o que nenhuma implementação ingênua tem. Sem
    ele, o texto apagado **vaza pelo quote de quem respondeu** — a
    mensagem some da thread e continua legível dentro de cada resposta.
    `revoke_message` limpa o `body` e o stub passa a responder
    `revoked=True` com `excerpt` vazio.

### Idempotência de envio

Um `POST` de mensagem cujo `201` se perde deixa o cliente sem saber se
ela foi. Reenviar às cegas manda duas; não reenviar perde. A saída é uma
`client_id` gerada **pelo cliente antes** da requisição:

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService, MessageCreateSchema


async def reenviar(
    service: ChatService,
    conversation_id: UUID,
    alice: UUID,
) -> None:
    """Envia duas vezes com a mesma `client_id` e grava uma só."""
    payload = MessageCreateSchema(body="oi", client_id="7f3a-1")

    first = await service.post_message(conversation_id, alice, payload)
    second = await service.post_message(conversation_id, alice, payload)

    assert first.id == second.id  # a mesma linha, não uma cópia
```

É o mesmo problema que o `IdempotencyMiddleware` resolve para HTTP
genérico — mas aqui a chave é de domínio: ela sobrevive à mensagem, não
à requisição, e é única por `(sender_id, client_id)`, então dois
clientes podem escolher ids independentes.

!!! warning "A unicidade é por remetente, não por conversa"
    Reaproveitar uma `client_id` numa **outra** conversa é recusado com
    `422` nomeando o campo. Devolver a mensagem encontrada seria pior do
    que parece: o cliente receberia `201` com uma linha de outra thread,
    enquanto a mensagem que ele realmente enviou nunca foi escrita.
    Gere uma id nova por mensagem (um UUID serve).

### Recibos: marca d'água, não linha por leitor

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService


async def marcar_lida(
    service: ChatService,
    conversation_id: UUID,
    bob: UUID,
    last_seen_id: UUID,
) -> None:
    """Move a marca d'água e lê as contagens derivadas dela."""
    await service.mark_read(conversation_id, bob)  # tudo, agora
    await service.mark_read(conversation_id, bob, message_id=last_seen_id)

    page = await service.list_messages(conversation_id, with_receipts=True)
    print(page["items"][0].receipts)
```

```text
delivered=1 read=1 total=1
```

Uma linha por mensagem por leitor é O(mensagens × participantes): num
grupo de 200 pessoas são 200 linhas por mensagem. A marca d'água são
**200 linhas no total**, e marcar como lida é um `UPDATE`. A contagem de
"quem leu" vira um agregado sobre `last_read_at >= message.created_at`.
O custo é que "lida" passa a ser por instante, não por mensagem — que é
exatamente a granularidade que a interface mostra.

`unread_count` sai de graça em `list_conversations`.

### Anexos

```python
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService, MessageCreateSchema, MessageKind


async def anexar(
    service: ChatService,
    attachments: BaseRepository[Any],
    conversation_id: UUID,
    alice: UUID,
    storage_key: str,
    mime_type: str,
    size_bytes: int,
) -> None:
    """Grava o arquivo primeiro e só depois posta a mensagem."""
    attachment = await attachments.add(
        attachments.model(
            storage_key=storage_key,  # chave, nunca URL
            filename="foto.jpg",
            mime_type=mime_type,
            size_bytes=size_bytes,
        ),
    )

    await service.post_message(
        conversation_id,
        alice,
        MessageCreateSchema(
            kind=MessageKind.IMAGE,
            body="olha isso",  # legenda vive no body
            attachment_ids=[attachment.id],
        ),
    )
```

`storage_key` é chave, **nunca URL**: URL é capability com TTL, e
gravá-la congela um acesso na linha. Um id já reivindicado é recusado
com `404` — o mesmo arquivo não entra em duas mensagens.

!!! tip "Validando o upload de texto"
    `UploadUtils(verify_magic_bytes=True)` recusa assinatura
    desconhecida, e `.txt` / `.csv` **não têm assinatura** — num chat
    isso derruba anexo de texto. Use
    `require_known_signature=False`: recusa só quando a assinatura
    **contradiz** o tipo declarado. E para o checksum do arquivo inteiro
    passe `hasher=hashlib.sha256()`, porque o `content_validator` vê só
    o primeiro chunk.

### Apagar para todos ≠ deletar a linha

```python
from uuid import UUID

from tempest_fastapi_sdk import UploadUtils
from tempest_fastapi_sdk.chat import ChatService


async def apagar_para_todos(
    service: ChatService,
    uploads: UploadUtils,
    message_id: UUID,
    alice: UUID,
) -> None:
    """Revoga a mensagem e apaga os arquivos que ficaram órfãos."""
    tombstone, orphaned_keys = await service.revoke_message(message_id, alice)

    for key in orphaned_keys:
        await uploads.delete(key)

    assert tombstone.body == ""
```

A linha sobrevive (a thread mantém a forma, as respostas mantêm o que
citar) mas o `body` é **limpo de verdade** — não escondido atrás de um
flag que a próxima query esquece de filtrar. As chaves de storage saem
no segundo item da tupla: o SDK não é dono do bucket, e chave esquecida
é arquivo que sobrevive à mensagem que o justificava.

### Reação: uma por pessoa

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService


async def reagir(service: ChatService, message_id: UUID, bob: UUID) -> None:
    """Reage duas vezes; a segunda substitui a primeira."""
    await service.react(message_id, bob, "👍")
    updated = await service.react(message_id, bob, "🎉")

    print([(r.emoji, r.count) for r in updated.reactions])
```

```text
[('🎉', 1)]
```

### Preferências são da sua caixa

Fixar, arquivar e silenciar moram no participante. Escritos na conversa,
silenciam para todo mundo:

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import ChatService


async def organizar_caixa(
    service: ChatService,
    conversation_id: UUID,
    alice: UUID,
) -> None:
    """Fixa e arquiva a conversa para uma pessoa só."""
    await service.set_preferences(conversation_id, alice, is_pinned=True)
    await service.set_preferences(conversation_id, alice, is_archived=True)
```

`list_conversations` ordena fixadas primeiro, depois por mensagem mais
recente, e esconde as arquivadas salvo `include_archived=True`.

### Grupo de verdade

```python
from uuid import UUID

from tempest_fastapi_sdk.chat import (
    ChatService,
    ConversationKind,
    ParticipantRole,
)


async def administrar_grupo(
    service: ChatService,
    alice: UUID,
    bob: UUID,
    carol: UUID,
    dave: UUID,
) -> None:
    """Cria o grupo e mexe na composição dele."""
    group = await service.start_conversation(
        alice,
        [bob, carol],
        kind=ConversationKind.GROUP,
        title="Projeto X",
    )
    await service.add_participants(group.id, alice, [dave])  # history_from = agora
    await service.set_role(group.id, alice, bob, ParticipantRole.ADMIN)
    await service.remove_participant(group.id, alice, dave)
    await service.leave(group.id, bob)
```

Quem entra depois **não herda o backlog** por padrão (`history_from` é
carimbado na entrada); passe `share_history=True` para entregar o
histórico. Quem sai mantém a linha com `left_at` — senão mensagens
antigas perdem o nome do remetente. O dono não pode ser removido.

!!! tip "`kind` é inferido quando você não diz"
    Sem `kind`, até duas pessoas vira `DIRECT` e mais que isso vira
    `GROUP` — que é o que quem nunca ouviu falar da distinção quer
    dizer. Pedir `DIRECT` **explicitamente** com outra contagem é
    recusado com `422`.

### Mensagem de sistema

"Ana criou o grupo", "Bruno saiu". Cada mudança de composição posta uma
mensagem `kind=system` com payload estruturado:

```json
{"event": "participant_left", "actor_id": "…", "user_ids": ["…"]}
```

O cliente renderiza **do payload** — assim o texto é localizado e os
nomes viram link. O `body` que o servidor escreve é fallback para push e
busca, nunca a representação primária.

### Custo de uma página

`list_messages` resolve a página inteira com um número **fixo** de
queries — anexos, reações e as mensagens citadas saem em um `IN` cada,
não um por linha. Medido numa página de 20 mensagens: resolvendo linha a
linha eram 42 statements; em lote são 4, e continuam 4 quando a página
cresce. Encaminhar copia a linha do anexo (mesma `storage_key`), nunca
os bytes.

## O router

`make_chat_router` recebe **como** resolver a sessão, o serviço e o
usuário autenticado — igual aos outros routers do SDK:

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.chat import make_chat_router

from src.api.dependencies.resources import db
from src.api.dependencies.services import build_chat_service

sessionmaker = db.get_session_context


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
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.chat import ChatService
from tempest_fastapi_sdk.sse import SSEBroker

from src.db.models import ConversationModel, ConversationParticipantModel, MessageModel


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
`data` é o JSON de `MessageResponseSchema`:

```text
event: message
id: 7d3c5f8a-6b2e-4a19-b0d4-1c2e3f4a5b6c
data: {"id": "7d3c5f8a-...", "conversation_id": "1a2b3c4d-...", "sender_id": "7d3c5f8a-...", "body": "Bora!", "created_at": "2026-07-18T14:32:07Z"}
```

Veja a receita de
**[Server-Sent Events »](sse.md)** para o lado do cliente e a ponte
Redis multi-worker.

## Recapitulando

- Herde as cinco tabelas abstratas e aponte as FKs para o seu
  `UserModel` — declarando as duas constraints que mudam comportamento
  (`(sender_id, client_id)` e `(message_id, user_id)`).
- `ChatService` cobre iniciar, postar, responder, editar, apagar para
  todos, reagir, encaminhar, marcar como lida e administrar o grupo;
  devolve schemas, não ORM.
- Recibo é **marca d'água no participante**; reação é **uma por
  pessoa**; revogar **limpa o body** e devolve as chaves de storage para
  você apagar.
- `client_id` torna o reenvio idempotente; conversa direta é idempotente
  por par.
- `make_chat_router` monta os endpoints com guarda de participante —
  que considera quem **saiu** como não-participante.
- Passe um `SSEBroker` para ganhar entrega em tempo real de graça.
