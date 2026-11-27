# Long-term memory

Use when the assistant must remember facts across conversations (`ChatMemory`), in memory or in Postgres with pgvector.

Part of the [Self-hosted generative AI](../genai.md) recipe.

A chat that forgets everything between sessions isn't an assistant — it's a
form. `ChatMemory` gives the conversation **long-term memory**: every turn
becomes an indexed embedding, and before answering you recall the most
relevant snippets from **that same user** — even from old chats. Recall is
recency-aware: what is semantically close *and* recent floats to the top.

Needs the `[genai-chroma]` extra (ChromaDB) — if the service already runs
Postgres, see [Memory in Postgres with pgvector](#memory-in-postgres-with-pgvector),
which does without it. The embedder is any `SupportsEmbed` — here an
`OllamaEmbedder`, no torch:

```python
import asyncio
from datetime import datetime, timezone

from tempest_fastapi_sdk.genai import OllamaEmbedder
from tempest_fastapi_sdk.genai.rag import ChatMemory

memory = ChatMemory(
    OllamaEmbedder("nomic-embed-text"),
    persist_directory="./chat_memory",   # None = in-memory only
    top_k=5,
    min_similarity=0.55,
)


async def main() -> None:
    now = datetime.now(timezone.utc)

    # index two turns of an old conversation:
    await memory.index(
        user_id="u1", chat_id="c1", message_id="m1",
        role="user", content="I prefer short, direct answers.",
        created_at=now,
    )
    await memory.index(
        user_id="u1", chat_id="c1", message_id="m2",
        role="user", content="I work with FastAPI and Postgres.",
        created_at=now,
    )

    # in a NEW chat, recall what matters for that user:
    hits = await memory.search(
        user_id="u1",
        query="Which framework and database does he work with?",
        exclude_chat_id="c2",     # ignore the current chat
    )
    for hit in hits:
        print(f"{hit.score:.2f}  {hit.content}")


asyncio.run(main())
```

```text
0.58  I work with FastAPI and Postgres.
```

Only one memory comes back. Against this query, "I prefer short, direct
answers." scores 0.388 similarity, below the 0.55 floor, and is dropped
before the re-rank. The floor decides what reaches the model: a vague query
such as "what stack does he use?" scores 0.429 against the right text and
recalls nothing (measured with `nomic-embed-text`). Calibrate
`min_similarity` with your product's real queries, not the example's.

`search` filters by `user_id`, applies the similarity floor
(`min_similarity`), then blends in the recency decay — each `MemoryHit`
carries `content`, `role`, `chat_id`, `created_at`, `similarity` (raw
cosine) and `score` (the final value, recency included).

Every user shares one collection, so every mutation names its owner.
`delete_for_chat(user_id=..., chat_id=...)` deletes that user's rows for
the chat when they remove it — the `chat_id` comes from the request body
and can repeat across users, and the other users' rows stay.
`delete_for_user(user_id=...)` erases a user's whole memory (account
deletion). And `index` refuses, with `ValueError`, a `message_id` that
already belongs to another user instead of overwriting that user's row.

Chroma is asked for the `top_k * candidate_multiplier` nearest neighbours
(default `4`), and the recency re-rank picks `top_k` of them — so a recent
message ranked just below the raw top-K can still rise. With
`candidate_multiplier=1` only the raw top-K is reordered. The per-user
quota (`max_entries_per_user`) evicts the oldest **UTC instant** first:
`created_at` is stored converted to UTC, alongside a `created_at_ts` in
epoch seconds, and older rows without that field are ordered by parsing
the ISO string. Sorting the ISO strings put `10:00+05:00` after
`08:00+00:00`, although it is three hours earlier.

!!! info "The `[genai-chroma]` extra and the recency decay"
    Install with `uv add "tempest-fastapi-sdk[genai-chroma]"`. The final
    `score` combines similarity and recency via
    `(1 - recency_weight) * sim + recency_weight * sim * decay`, with
    `decay = 0.5 ** (age_in_days / recency_halflife_days)`. With the
    defaults (14 days, `recency_weight=0.5`), a 14-day-old snippet has
    `decay = 0.5` and scores 0.75 of its similarity, against 1.0 for a
    freshly written one of equal similarity. `recency_weight=0` = similarity
    only.

!!! tip "Generic RAG with `ChromaVectorStore`"
    Just need a persistent vector store (without the per-user memory
    logic)? `ChromaVectorStore` is a `VectorStore` like the others —
    `add(chunks, vectors)` / `search(vector, top_k=)` — backed by ChromaDB,
    with the same re-index rule as the other stores (`add` replaces the
    batch's `source`s). Drop it into `Retriever` in place of
    `InMemoryVectorStore` / `PgVectorStore` to get a disk-persisted corpus:

    ```python
    from tempest_fastapi_sdk.genai import OllamaEmbedder
    from tempest_fastapi_sdk.genai.rag import ChromaVectorStore, Retriever

    rag = Retriever(
        OllamaEmbedder("nomic-embed-text"),
        ChromaVectorStore(collection_name="kb", persist_directory="./kb"),
    )
    ```

## Memory in Postgres with pgvector

Does your service already run Postgres? Then ChromaDB is a **second
database**: outside the application's backups and outside its transactions —
deleting a conversation and its memory stops being atomic. `PgChatMemory` is
the same memory in a pgvector table of the database you already have: same
methods (`index`, `search`, `delete_for_chat`, `delete_for_user`), same
parameters and the same ranking (similarity floor, recency re-rank,
over-fetch, per-user quota).

It does **not** need the `[genai-chroma]` extra nor the `pgvector` package:
the vector travels as text and Postgres casts it. All it takes is the async
driver (`[postgres]`, which brings `asyncpg`) and the `vector` extension on
the server.

Switching backends means switching the class — the rest of the code stays
the same:

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
    dim=768,                 # the embedder's vector size
    table="chat_memory",
)


async def main() -> None:
    await db.connect()
    await memory.ensure_schema()   # CREATE EXTENSION + table + indexes

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

With `nomic-embed-text` (which produces 768-dimension vectors) on a local
Ollama and `pgvector/pgvector:pg17`, this prints:

```text
0.68  Trabalho com FastAPI e Postgres.
```

Every search is `WHERE user_id = :user_id` (plus
`chat_id <> :exclude_chat_id` when you pass `exclude_chat_id`) **before**
ordering by the cosine distance `<=>` — one user's memory never shows up for
another. `message_id` is the primary key: indexing the same message again
replaces the row, and a `message_id` that already belongs to another user is
refused with `ValueError`.

### Delete the conversation and its memory in one transaction

`delete_for_chat` and `delete_for_user` accept **your** session. When you
pass it, the `DELETE` joins your transaction: if the rest fails, the memory
comes back with it.

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

Without `session`, each delete opens and commits a session of its own.

### The `vector` extension on the server

`ensure_schema()` runs `CREATE EXTENSION IF NOT EXISTS vector`, so Postgres
must have the extension installed **and** the application role must be
allowed to create it (otherwise run `CREATE EXTENSION vector` once as a
superuser before starting the service).

!!! warning "The official `postgres:*-alpine` image does not ship pgvector"
    On `postgres:17-alpine`, `CREATE EXTENSION vector` fails with:

    ```text
    ERROR:  extension "vector" is not available
    DETAIL:  Could not open extension control file "/usr/local/share/postgresql/extension/vector.control": No such file or directory.
    HINT:  The extension must first be installed on the system where PostgreSQL is running.
    ```

    Use an image that already ships the extension, such as
    `pgvector/pgvector:pg17`:

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

### HNSW index (optional)

Without an approximate index the search is exact: the `(user_id,
created_at)` B-tree finds the user's rows and Postgres sorts only those. At a
typical user's volume, that is enough. For large tables,
`ensure_schema(ann_index="hnsw")` builds the HNSW index (`vector_cosine_ops`),
the same one `PgVectorStore` builds.

!!! info "HNSW with a per-user filter needs pgvector 0.8.0+"
    The HNSW index returns at most `hnsw.ef_search` rows (of any user) and the
    `user_id` filter runs afterwards. Measured on pgvector 0.8.6, with 50 000
    rows and a user owning 500 of them, the plan used HNSW and that user's
    search came back **empty**. Since 0.8.0 pgvector has *iterative index
    scans*: `search` turns on `hnsw.iterative_scan` in its own transaction,
    and the same search returns its 5 hits. That is why
    `ensure_schema(ann_index=...)` refuses an older pgvector with
    `RuntimeError` — without an approximate index, the exact search works on
    any version.
