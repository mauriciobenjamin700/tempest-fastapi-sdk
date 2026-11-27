# RAG context

Use to inject context into the LLM: web search (SearXNG), page-body extraction, PDF reading and RAG over your own corpus.

Part of the [Self-hosted generative AI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai.md) recipe.

A local LLM only knows what it trained on. For current, grounded answers,
inject context: `tempest_fastapi_sdk.genai.rag` searches the web
(self-hosted SearXNG), extracts page bodies, reads PDFs, and assembles a
prompt-ready block — without sending data outside. Needs the `[genai-rag]`
extra (httpx + trafilatura + pymupdf); the pieces import lazily.

## Web search (SearXNG)

```python
import asyncio

import httpx
from tempest_fastapi_sdk.genai.rag import SearxngBackend, WebSearch, build_context
from tempest_fastapi_sdk.utils.http_client import HTTPClient

client = HTTPClient()
search = WebSearch(SearxngBackend("http://localhost:8080", http_client=client))


async def main() -> None:
    """Run this example."""
    results = await search.search("what is PIX?", max_results=5)   # list[SearchResult]
    context = build_context("what is PIX?", results, long_text=False, max_chars=2000)
    # -> a string ready to inject into your TextGenerator prompt


asyncio.run(main())
```

The backend is a `Protocol` (`WebSearchBackend`) — swap SearXNG for another
provider without touching call sites. The `HTTPClient` is injected (pool
reuse, plus retry/backoff + a circuit-breaker for free; wire it in the
FastAPI lifespan).

!!! tip "From question to context in one call"
    `WebSearch.retrieve` does search → (optional) parallel body extraction
    → `build_context`, all at once:

    ```python
    from tempest_fastapi_sdk.genai.rag import ContentExtractor

    extractor = ContentExtractor(http_client=httpx.AsyncClient())
    context = await search.retrieve("what is PIX?", extractor=extractor, max_results=5)
    answer = await gen.generate(context)
    ```

    Without `extractor` it uses snippets only. `ContentExtractor.extract_many`
    fetches N pages concurrently (bounded by `concurrency`).

## Extract page bodies

Search snippets are thin. To give the LLM ground truth, fetch each page
and extract the clean text (via `trafilatura`):

```python
import asyncio

import httpx

from tempest_fastapi_sdk.genai.rag import ContentExtractor, build_context

results = []  # hits from a previous retriever.search(...)


extractor = ContentExtractor(http_client=httpx.AsyncClient())


async def main() -> None:
    """Run this example."""
    for result in results:
        outcome = await extractor.extract(result.url)
        result.content = outcome.text          # "" on failure; outcome.failed marks it
    context = build_context("what is PIX?", results)   # now with full bodies


asyncio.run(main())
```

Failures (timeout, 4xx/5xx, empty page) **never** raise — they come back
as `ExtractionResult(text="", failed=True)`, so no source is silently
dropped.

!!! info "The URL is treated as untrusted input"
    The URL is picked by the search engine or by the user, so `extract`
    keeps your server from becoming a proxy into the internal network
    (SSRF):

    - only `http` and `https` — `file://`, `ftp://` and friends come back
      `failed=True` without a request;
    - the host (an IP literal, or **every** address the hostname resolves
      to) must be public: loopback, private ranges (10/8, 172.16/12,
      192.168/16, `fd00::/8`), link-local — including the cloud metadata
      `169.254.169.254` —, CGNAT, multicast and reserved are refused, for
      IPv4 and IPv6;
    - redirects are followed by hand, with the same check on **every** hop,
      up to `max_redirects=` (default 5);
    - the body is streamed and the fetch gives up past
      `max_response_bytes=` (default 5 MiB);
    - `trafilatura` runs in a thread (`asyncio.to_thread`), so a large page
      does not stall the event loop.

    To read intranet pages on purpose, `allow_private_networks=True` turns
    off only the address check (scheme, redirect bound and body cap still
    apply). Turn it on only when the URLs do not come from an attacker.

    The connection goes to the address that passed the check, not to a
    second lookup of the name: each hop's URL carries the validated IP,
    while the `Host` header and the TLS name (SNI, through httpx's
    `sni_hostname` extension) keep the hostname. A DNS answer that changes
    between the check and the connection (DNS rebinding) never reaches the
    connection, and HTTPS still validates the certificate against the
    hostname — a certificate issued for another name is refused.

    The httpx pool keys connections by IP, so two hostnames on the same IP
    (common behind a CDN) could share a TLS session negotiated for one of
    them. When an HTTPS response arrives on a connection opened for another
    hostname, the body is not read, the connection is closed and the hop is
    retried on a fresh connection, up to 3 times. The request line and
    headers had already gone out on that connection; what is discarded is
    the response.

    Behind an HTTP proxy the tunnel is opened to the IP and httpcore 1.0.9
    does not send the SNI, so HTTPS fetches come back `failed=True`
    (`certificate verify failed: IP address mismatch`). In that setup the
    proxy resolves DNS, and the egress rule belongs there:
    `allow_private_networks=True` turns pinning off along with the check.

## Read PDFs (knowledge base)

`PdfReader` (PyMuPDF — detailed, reading-order extraction) turns PDF paths
into text and prompt/embedding-ready chunks:

```python
from tempest_fastapi_sdk.genai.rag import PdfReader, build_context

reader = PdfReader()
doc = reader.read("/kb/manual.pdf")             # Document: text + pages + metadata
chunks = reader.chunks("/kb/manual.pdf", max_chars=2000, overlap=200)

context = build_context("how to refund?", chunks)   # cites "file (page N)"
```

`chunks(..., overlap=200)` shares characters between neighbors so a fact on
a boundary isn't cut in half; `per_page=True` (default) keeps each chunk on
a single page and records its page number.

!!! tip "Mix web + PDF in one context"
    `build_context` accepts `SearchResult` and `Chunk` in the same list —
    it delimits each source with `---` and labels the origin (URL or `file
    (page N)`) so the LLM can cite. Pass `long_text=False` to truncate each
    source to `max_chars`.

## RAG over your own corpus (vector store)

Web search is one source; the other is **your own knowledge** (PDFs, docs).
Instead of re-embedding everything each request, index once into a **vector
store** and retrieve by similarity. `Retriever` ties `Embedder` → store →
`build_context`:

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder, TextGenerator, TextModelEnum
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

gen = TextGenerator(TextModelEnum.QWEN2_5_7B_INSTRUCT)


rag = Retriever(Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
                InMemoryVectorStore())


async def main() -> None:
    """Run this example."""
    await rag.index(PdfReader().chunks("/kb/manual.pdf"))     # once
    context = await rag.retrieve("how to refund?", top_k=5)   # cheap, afterwards
    answer = await gen.generate(context)


asyncio.run(main())
```

- **`VectorStore`** is a `Protocol` — `InMemoryVectorStore` (dev/tests,
  cosine scan) or `PgVectorStore` (production).
- **`PgVectorStore`** uses **pgvector** in the Postgres the service already
  has (no new infra): creates the table and a B-tree index on `source` on
  demand, searches with the cosine distance operator `<=>`. Needs
  `[genai-rag]` + `CREATE EXTENSION vector`.
- **Every SDK store replaces per source**: `add` deletes what each batch
  chunk's `source` already held and writes the batch (in `PgVectorStore`,
  in one transaction: one `DELETE` + one batched `INSERT`). Re-indexing an
  edited document leaves no old tail; pass every chunk of a source in the
  same call.

```python
from tempest_fastapi_sdk.genai import Embedder, EmbeddingModelEnum
from tempest_fastapi_sdk.genai.rag import PgVectorStore, Retriever

from src.api.dependencies.resources import db

embedder = Embedder(EmbeddingModelEnum.ALL_MINILM_L6_V2)


store = PgVectorStore(db, dim=384)          # db = AsyncDatabaseManager
rag = Retriever(embedder, store)
```

The table name is interpolated into the SQL (an identifier cannot be a bound
parameter), so the constructor refuses with `ValueError` anything outside an
unquoted `name` or `schema.name` (`[A-Za-z_][A-Za-z0-9_]*`, up to 63
characters each), and a `dim` that is not a positive integer.

### Approximate index: HNSW or IVFFlat

Out of the box, search is **exact**: Postgres runs a sequential scan and
computes `<=>` against every row. With 100,000 chunks of 384 dimensions that
already costs ~50–60 ms per search (measurement below), and the cost follows
the row count.

An approximate index trades some **recall** (the fraction of the exact
neighbors that comes back) for latency. Ask for one in `ensure_schema`:

```python
import asyncio

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.genai.rag import Chunk, PgVectorStore

db = AsyncDatabaseManager("postgresql+asyncpg://app:app@127.0.0.1:5432/app")
store = PgVectorStore(db, dim=384)


async def main() -> None:
    """Build the HNSW index once, then search with a wider candidate list."""
    await store.ensure_schema(ann_index="hnsw", m=16, ef_construction=64)
    query: list[float] = [0.1] * 384
    hits: list[Chunk] = await store.search(query, top_k=10, ef_search=100)
    print([hit.text for hit in hits])


asyncio.run(main())
```

- **`ann_index="hnsw"`** builds a navigable graph. Build parameters: `m`
  (connections per node) and `ef_construction` (candidates while building).
  Needs pgvector **>= 0.5.0**; below that `ensure_schema` raises
  `RuntimeError` naming the version it found.
- **`ann_index="ivfflat"`** splits the vectors into `lists` groups and, at
  search time, scans only the nearest ones. Much faster to build, but the
  centroids come from the rows present **when it is built** — call it after
  loading the corpus, not before the first `add`.
- The index is named `<table>_embedding_idx` (`store.ann_index_name`) and
  uses `vector_cosine_ops`, the operator class of the `<=>` that `search`
  orders by.
- A parameter left as `None` stays out of the `WITH` clause, so pgvector's
  default applies. A parameter of the other method (`lists` with HNSW), or
  one given without `ann_index`, raises `ValueError` before any SQL runs.
- Calling it again with the same parameters does nothing. With **another**
  method or other parameters it raises `ValueError` asking for `DROP INDEX`:
  rebuilding a large index never happens hidden inside an `ensure_schema`.

At search time, `ef_search` (HNSW) and `probes` (IVFFlat) tune the trade-off
per call. `search` applies both with `set_config(..., true)`, the function
form of `SET LOCAL`: it lasts for the search's transaction only, and the next
session on the same pool reads the server value again (`40` and `1`,
checked in the docker test).

!!! warning "`ef_search` also caps how many results come back"
    An HNSW scan returns at most `ef_search` rows: measured on pgvector
    0.8.6, `search(top_k=20, ef_search=5)` returns **5** chunks. Keep
    `ef_search >= top_k`.

#### What it buys

Measured through `PgVectorStore.search` end to end (Python + asyncpg +
Postgres on `localhost`), `top_k=10`, against `pgvector/pgvector:pg16`
(pgvector 0.8.6, the container's default configuration, `--shm-size=1g`) on
a 12-core machine under WSL2. **N = 100,000 vectors of 384 dimensions.**
Recall@10 is the fraction of the exact scan's 10 neighbors that the index
returns; each row combines two runs with different seeds (200 searches per
seed on the Gaussian data, 500 on the clustered data), and the range shows
both.

Two synthetic corpora that bracket the real case from above and below:

- **Gaussian**: every component is `N(0, 1)`, no structure at all — the
  nearest neighbor is barely nearer than the rest;
- **clustered**: 1,000 Gaussian centers, each vector is a center plus
  `N(0, 0.5)` noise; queries are generated the same way.

| Search | p50 | recall@10 Gaussian | recall@10 clustered |
| --- | --- | --- | --- |
| exact (sequential scan) | 50–60 ms | 1.00 | 1.00 |
| HNSW, `ef_search=40` (default) | 4–8 ms | 0.05–0.06 | 0.986–0.992 |
| HNSW, `ef_search=100` | 4–8 ms | 0.12–0.13 | 1.00 |
| HNSW, `ef_search=400` | 7–20 ms | 0.36–0.37 | 1.00 |
| IVFFlat `lists=100`, `probes=1` (default) | 3.4–4.4 ms | 0.03–0.04 | 0.992–0.996 |
| IVFFlat `lists=100`, `probes=10` | 6–7.5 ms | 0.23 | 0.9996–0.9998 |
| IVFFlat `lists=100`, `probes=40` | 17–20 ms | 0.62 | 1.00 |

Index build over the 100,000 rows: HNSW with the defaults (`m=16`,
`ef_construction=64`) took 72–81 s on the Gaussian data and 26 s on the
clustered data; IVFFlat with `lists=100`, 0.8–1.5 s.

What to take from it:

- **Latency**: at this N the index takes a search from ~50 ms to ~5 ms. With
  5,000 Gaussian rows (50 searches, one seed) the exact scan measured ~20 ms
  at p50 and HNSW ~4 ms: the gain is there, but it is milliseconds.
- **Recall depends on the corpus, not only on the index.** The same
  parameters return nearly everything on the clustered data and nearly
  nothing on the Gaussian data. Text embeddings have structure (documents on
  the same subject sit close together), but how much only measuring **your**
  corpus tells: compare `search` with the index against the exact result
  (taken before creating the index) on a handful of real queries, and raise
  `ef_search`/`probes` until the recall is good enough.
- **A few thousand chunks?** Stay on exact search: the cost is small and the
  recall is 1.

`rag.search(query, top_k=)` returns the `Chunk`s with a `score` (similarity);
`rag.retrieve(...)` builds the context for you. Need Qdrant/Weaviate later?
Implement `VectorStore` (2 methods) and inject it — `Retriever` doesn't
change.
