# Memória de longo prazo

Use quando o assistente precisa lembrar fatos entre conversas (`ChatMemory`), em memória ou no Postgres com pgvector.

Parte da receita [IA generativa self-hosted](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai.md).

Um chat que esquece tudo entre sessões não é assistente — é um formulário.
`ChatMemory` dá **memória de longo prazo** à conversa: cada turno vira uma
embedding indexada, e antes de responder você recupera os trechos mais
relevantes do **próprio usuário** — inclusive de chats antigos. Recall com
recência: o que é semanticamente próximo *e* recente sobe primeiro.

Requer o extra `[genai-chroma]` (ChromaDB) — se o serviço já roda Postgres,
veja [Memória no Postgres com pgvector](#memoria-no-postgres-com-pgvector),
que dispensa o extra. O embedder é qualquer `SupportsEmbed` — aqui um
`OllamaEmbedder`, sem torch:

```python
import asyncio
from datetime import datetime, timezone

from tempest_fastapi_sdk.genai import OllamaEmbedder
from tempest_fastapi_sdk.genai.rag import ChatMemory

memory = ChatMemory(
    OllamaEmbedder("nomic-embed-text"),
    persist_directory="./chat_memory",   # None = só em memória
    top_k=5,
    min_similarity=0.55,
)


async def main() -> None:
    now = datetime.now(timezone.utc)

    # indexa dois turnos de uma conversa antiga:
    await memory.index(
        user_id="u1", chat_id="c1", message_id="m1",
        role="user", content="Prefiro respostas curtas e diretas.",
        created_at=now,
    )
    await memory.index(
        user_id="u1", chat_id="c1", message_id="m2",
        role="user", content="Trabalho com FastAPI e Postgres.",
        created_at=now,
    )

    # num chat NOVO, recupera o que importa daquele usuário:
    hits = await memory.search(
        user_id="u1",
        query="Com que framework e banco ele trabalha?",
        exclude_chat_id="c2",     # ignora o chat atual
    )
    for hit in hits:
        print(f"{hit.score:.2f}  {hit.content}")


asyncio.run(main())
```

```text
0.68  Trabalho com FastAPI e Postgres.
```

Só uma lembrança volta. Contra essa consulta, "Prefiro respostas curtas e
diretas." tem similaridade 0,537, abaixo do piso de 0,55, e sai antes do
re-rank. O piso é o que decide o que chega ao modelo: uma consulta vaga como
"qual stack ele usa?" fica em 0,433 contra o texto certo e não recupera nada
(medido com `nomic-embed-text`). Calibre `min_similarity` com consultas
reais do seu produto, não com a do exemplo.

`search` filtra pelo `user_id`, aplica o piso de similaridade
(`min_similarity`) e então mistura o decaimento de recência — cada
`MemoryHit` traz `content`, `role`, `chat_id`, `created_at`, `similarity`
(cosseno cru) e `score` (o valor final, já com recência).

A coleção é compartilhada por todos os usuários, então toda mutação nomeia
o dono. `delete_for_chat(user_id=..., chat_id=...)` apaga as linhas daquele
usuário naquele chat quando ele o remove — o `chat_id` vem do corpo da
requisição e pode repetir entre usuários, e as linhas dos outros ficam.
`delete_for_user(user_id=...)` apaga a memória inteira de um usuário (exclusão
de conta). E `index` recusa com `ValueError` um `message_id` que já pertence a
outro usuário, em vez de sobrescrever a linha dele.

O Chroma é consultado pelos `top_k * candidate_multiplier` vizinhos mais
próximos (padrão `4`), e o re-rank por recência escolhe os `top_k` entre
eles — assim uma mensagem recente logo abaixo do top-K cru ainda pode subir.
Com `candidate_multiplier=1`, só o top-K cru é reordenado. A cota por
usuário (`max_entries_per_user`) despeja primeiro o **instante UTC** mais
antigo: o `created_at` é gravado convertido para UTC, junto de um
`created_at_ts` em segundos epoch, e linhas antigas sem esse campo são
ordenadas lendo o ISO. Ordenar as strings ISO punha `10:00+05:00` depois
de `08:00+00:00`, embora seja três horas antes.

!!! info "Extra `[genai-chroma]` e o decaimento de recência"
    Instale com `uv add "tempest-fastapi-sdk[genai-chroma]"`. O `score`
    final combina similaridade e recência via
    `(1 - recency_weight) * sim + recency_weight * sim * decay`, com
    `decay = 0.5 ** (idade_em_dias / recency_halflife_days)`. Com os padrões
    (14 dias, `recency_weight=0.5`), um trecho de 14 dias atrás tem
    `decay = 0.5` e fica com `score` 0,75 da similaridade, contra 1,0 de um
    recém-escrito de mesma similaridade. `recency_weight=0` = só
    similaridade.

!!! tip "RAG genérico com o `ChromaVectorStore`"
    Precisa só de um vector store persistente (sem a lógica de memória por
    usuário)? `ChromaVectorStore` é um `VectorStore` como os outros —
    `add(chunks, vectors)` / `search(vector, top_k=)` — respaldado por
    ChromaDB, com a mesma regra de reindexação dos outros stores (o `add`
    substitui as `source`s do lote). Injete no `Retriever` no lugar do
    `InMemoryVectorStore` / `PgVectorStore` pra ter um corpus persistido em
    disco:

    ```python
    from tempest_fastapi_sdk.genai import OllamaEmbedder
    from tempest_fastapi_sdk.genai.rag import ChromaVectorStore, Retriever

    rag = Retriever(
        OllamaEmbedder("nomic-embed-text"),
        ChromaVectorStore(collection_name="kb", persist_directory="./kb"),
    )
    ```

## Memória no Postgres com pgvector

Seu serviço já roda Postgres? Então o ChromaDB é um **segundo banco**: fora
do backup da aplicação e fora das transações dela — apagar uma conversa e a
memória dela deixa de ser atômico. `PgChatMemory` é a mesma memória numa
tabela pgvector do banco que você já tem: mesmos métodos (`index`, `search`,
`delete_for_chat`, `delete_for_user`), mesmos parâmetros e o mesmo ranking
(piso de similaridade, re-rank por recência, over-fetch, cota por usuário).

Ela **não** precisa do extra `[genai-chroma]` nem do pacote `pgvector`: o
vetor viaja como texto e o Postgres faz o cast. Basta o driver async
(`[postgres]`, que traz o `asyncpg`) e a extensão `vector` no servidor.

Trocar de backend é trocar a classe — o resto do código não muda:

```python
import asyncio
from datetime import UTC, datetime

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import OllamaEmbedder
from tempest_fastapi_sdk.genai.rag import PgChatMemory

db = AsyncDatabaseManager("postgresql+asyncpg://app:app@localhost:5432/app")
memory = PgChatMemory(
    OllamaEmbedder("nomic-embed-text"),
    db,
    dim=768,                 # tamanho do vetor do embedder
    table="chat_memory",
)


async def main() -> None:
    await db.connect()
    await memory.ensure_schema()   # CREATE EXTENSION + tabela + índices

    await memory.index(
        user_id="u1", chat_id="c1", message_id="m1",
        role="user", content="Trabalho com FastAPI e Postgres.",
        created_at=datetime.now(UTC),
    )
    hits = await memory.search(
        user_id="u1",
        query="Com que framework e banco ele trabalha?",
        exclude_chat_id="c2",
    )
    for hit in hits:
        print(f"{hit.score:.2f}  {hit.content}")

    await db.disconnect()


asyncio.run(main())
```

Com o `nomic-embed-text` (que gera vetores de 768 dimensões) num Ollama
local e o `pgvector/pgvector:pg17`, isso imprime:

```text
0.68  Trabalho com FastAPI e Postgres.
```

Toda busca é `WHERE user_id = :user_id` (mais `chat_id <> :exclude_chat_id`
quando você passa `exclude_chat_id`) **antes** de ordenar pela distância de
cosseno `<=>` — a memória de um usuário nunca aparece para outro. O
`message_id` é a chave primária: indexar de novo a mesma mensagem substitui a
linha, e um `message_id` que já pertence a outro usuário é recusado com
`ValueError`.

### Apagar a conversa e a memória numa transação só

`delete_for_chat` e `delete_for_user` aceitam a **sua** sessão. Passada, o
`DELETE` entra na sua transação: se o resto falhar, a memória volta junto.

```python
from sqlalchemy import text

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai.rag import PgChatMemory


async def delete_chat(
    db: AsyncDatabaseManager,
    memory: PgChatMemory,
    *,
    user_id: str,
    chat_id: str,
) -> None:
    async with db.transaction() as session:
        await session.execute(
            text("DELETE FROM chats WHERE id = :id AND owner_id = :owner"),
            {"id": chat_id, "owner": user_id},
        )
        await memory.delete_for_chat(
            user_id=user_id, chat_id=chat_id, session=session,
        )
```

Sem `session`, cada deleção abre e commita uma sessão própria.

### A extensão `vector` no servidor

`ensure_schema()` roda `CREATE EXTENSION IF NOT EXISTS vector`, então o
Postgres precisa ter a extensão instalada **e** o papel da aplicação precisa
poder criá-la (senão, rode o `CREATE EXTENSION vector` uma vez como
superusuário antes de subir o serviço).

!!! warning "A imagem `postgres:*-alpine` oficial não traz o pgvector"
    No `postgres:17-alpine`, o `CREATE EXTENSION vector` falha com:

    ```text
    ERROR:  extension "vector" is not available
    DETAIL:  Could not open extension control file "/usr/local/share/postgresql/extension/vector.control": No such file or directory.
    HINT:  The extension must first be installed on the system where PostgreSQL is running.
    ```

    Use uma imagem que já traz a extensão, como `pgvector/pgvector:pg17`:

    ```yaml
    services:
      db:
        image: pgvector/pgvector:pg17
        environment:
          POSTGRES_USER: app
          POSTGRES_PASSWORD: app
          POSTGRES_DB: app
        ports:
          - "127.0.0.1:5432:5432"
    ```

### Índice HNSW (opcional)

Sem índice aproximado, a busca é exata: o índice B-tree `(user_id,
created_at)` acha as linhas do usuário e o Postgres ordena só elas. Com o
volume típico de um usuário, isso basta. Para tabelas grandes,
`ensure_schema(ann_index="hnsw")` cria o índice HNSW (`vector_cosine_ops`),
igual ao do `PgVectorStore`.

!!! info "HNSW com filtro por usuário exige pgvector 0.8.0+"
    O índice HNSW devolve no máximo `hnsw.ef_search` linhas (de qualquer
    usuário) e o filtro `user_id` roda depois. Medido no pgvector 0.8.6, com
    50 000 linhas e um usuário dono de 500 delas, o plano usou o HNSW e a
    busca desse usuário voltou **vazia**. A partir do 0.8.0 o pgvector tem
    *iterative index scans*: a `search` liga `hnsw.iterative_scan` na própria
    transação, e a mesma busca volta com os 5 resultados. Por isso
    `ensure_schema(ann_index=...)` recusa com `RuntimeError` um pgvector mais
    antigo — sem índice aproximado, a busca exata funciona em qualquer versão.
