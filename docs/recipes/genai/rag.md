# Contexto RAG

Use para injetar contexto na LLM: busca web (SearXNG), extração do corpo das páginas, leitura de PDFs e RAG sobre um corpus próprio.

Parte da receita [IA generativa self-hosted](../genai.md).

Uma LLM local só sabe o que treinou. Pra respostas atuais e fundamentadas,
injete contexto: `tempest_fastapi_sdk.genai.rag` busca na web (SearXNG
self-hosted), extrai o corpo das páginas, lê PDFs e monta um bloco pronto
pro prompt — tudo sem enviar dados pra fora. Requer o extra `[genai-rag]`
(httpx + trafilatura + pymupdf); as peças importam preguiçosamente.

## Busca web (SearXNG)

```python
import asyncio

import httpx
from tempest_fastapi_sdk.genai.rag import SearxngBackend, WebSearch, build_context
from tempest_fastapi_sdk.utils.http_client import HTTPClient

client = HTTPClient()
search = WebSearch(SearxngBackend("http://localhost:8080", http_client=client))


async def main() -> None:
    """Run this example."""
    results = await search.search("o que é PIX?", max_results=5)   # list[SearchResult]
    context = build_context("o que é PIX?", results, long_text=False, max_chars=2000)
    # -> string pronta pra injetar no prompt do seu TextGenerator


asyncio.run(main())
```

O backend é um `Protocol` (`WebSearchBackend`) — troque o SearXNG por
outro provedor sem mexer no call site. O `HTTPClient` é injetado
(reaproveita o pool e dá retry/backoff + circuit-breaker de graça; ligue
no lifespan do FastAPI).

!!! tip "Da pergunta ao contexto em uma chamada"
    `WebSearch.retrieve` faz busca → (opcional) extração dos corpos em
    paralelo → `build_context`, tudo de uma vez:

    ```python
    from tempest_fastapi_sdk.genai.rag import ContentExtractor

    extractor = ContentExtractor(http_client=httpx.AsyncClient())
    context = await search.retrieve("o que é PIX?", extractor=extractor, max_results=5)
    resposta = await gen.generate(context)
    ```

    Sem `extractor`, usa só os snippets. `ContentExtractor.extract_many`
    busca N páginas concorrentes (limitado por `concurrency`).

## Extrair o corpo das páginas

O snippet do buscador é raso. Pra dar "verdade" pra LLM, busque cada
página e extraia o texto limpo (via `trafilatura`):

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
        result.content = outcome.text          # "" quando falha; outcome.failed marca
    context = build_context("o que é PIX?", results)   # agora com corpo completo


asyncio.run(main())
```

Falhas (timeout, 4xx/5xx, página sem corpo) **nunca** levantam — voltam
como `ExtractionResult(text="", failed=True)`, então nenhuma fonte some
silenciosamente.

!!! info "A URL é tratada como entrada não confiável"
    Quem escolhe a URL é o buscador ou o usuário, então o `extract` protege
    o seu servidor de virar proxy para a rede interna (SSRF):

    - só `http` e `https` — `file://`, `ftp://` e afins voltam
      `failed=True` sem request;
    - o host (IP literal, ou **todo** endereço que o hostname resolve)
      precisa ser público: loopback, rede privada (10/8, 172.16/12,
      192.168/16, `fd00::/8`), link-local — inclusive o
      `169.254.169.254` de metadata de nuvem —, CGNAT, multicast e
      reservado são recusados, em IPv4 e IPv6;
    - redirect é seguido à mão, com a mesma checagem em **cada** salto, até
      `max_redirects=` (default 5);
    - o corpo é lido em stream e a busca desiste passando de
      `max_response_bytes=` (default 5 MiB);
    - o `trafilatura` roda em thread (`asyncio.to_thread`), então página
      grande não trava o event loop.

    Para ler páginas da intranet de propósito, `allow_private_networks=True`
    desliga só a checagem de endereço (esquema, limite de redirect e de
    corpo continuam). Ligue apenas quando as URLs não vêm de quem ataca.

    A conexão vai para o endereço que passou na checagem, não para uma
    segunda resolução do nome: a URL de cada salto leva o IP validado, e o
    header `Host` e o nome TLS (SNI, pela extensão `sni_hostname` do httpx)
    continuam com o hostname. Um DNS que troca a resposta entre a checagem e
    a conexão (DNS rebinding) não alcança a conexão, e o HTTPS continua
    validando o certificado contra o hostname — um certificado emitido para
    outro nome é recusado.

    O pool do httpx indexa conexão por IP, então dois hostnames no mesmo IP
    (comum atrás de CDN) poderiam dividir uma sessão TLS negociada para um
    deles. Quando uma resposta HTTPS chega numa conexão aberta para outro
    hostname, o corpo não é lido, a conexão é fechada e o salto é refeito
    numa conexão nova, até 3 vezes. A linha de request e os headers já
    tinham ido para aquela conexão; o que se descarta é a resposta.

    Atrás de proxy HTTP o túnel é aberto para o IP e o httpcore 1.0.9 não
    manda o SNI, então busca HTTPS volta `failed=True`
    (`certificate verify failed: IP address mismatch`). Nesse cenário quem
    resolve o DNS é o proxy, e é nele que a regra de egress deve morar:
    `allow_private_networks=True` desliga a fixação junto com a checagem.

## Ler PDFs (base de conhecimento)

`PdfReader` (PyMuPDF — extração detalhada, ordem de leitura) transforma
caminhos de PDF em texto e em chunks prontos pra prompt ou índice de
embeddings:

```python
from tempest_fastapi_sdk.genai.rag import PdfReader, build_context

reader = PdfReader()
doc = reader.read("/base/manual.pdf")            # Document: text + pages + metadata
chunks = reader.chunks("/base/manual.pdf", max_chars=2000, overlap=200)

context = build_context("como estornar?", chunks)   # cita "arquivo (page N)"
```

`chunks(..., overlap=200)` compartilha caracteres entre pedaços vizinhos,
pra um fato na fronteira não ser cortado ao meio; `per_page=True` (padrão)
mantém cada chunk numa página só, carregando o número dela.

!!! tip "Misture web + PDF no mesmo contexto"
    `build_context` aceita `SearchResult` e `Chunk` na mesma lista —
    delimita cada fonte com `---` e rotula a origem (URL ou `arquivo
    (page N)`), pra LLM citar. Passe `long_text=False` pra truncar cada
    fonte a `max_chars`.

## RAG sobre corpus próprio (vector store)

Busca web é uma fonte; a outra é o **seu conhecimento** (PDFs, docs). Em
vez de reembeddar tudo a cada request, indexe uma vez num **vector store**
e recupere por similaridade. `Retriever` amarra `Embedder` → store →
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
    await rag.index(PdfReader().chunks("/kb/manual.pdf"))     # uma vez
    context = await rag.retrieve("como estornar?", top_k=5)   # depois, barato
    answer = await gen.generate(context)


asyncio.run(main())
```

- **`VectorStore`** é um `Protocol` — `InMemoryVectorStore` (dev/testes,
  scan por cosseno) ou `PgVectorStore` (produção).
- **`PgVectorStore`** usa **pgvector** no Postgres que o serviço já tem
  (sem infra nova): cria a tabela e um índice B-tree em `source` sob
  demanda, busca com o operador de distância cosseno `<=>`. Requer
  `[genai-rag]` + `CREATE EXTENSION vector`.
- **Todo store do SDK substitui por fonte**: `add` apaga o que a `source`
  de cada chunk do lote já tinha e grava o lote (no `PgVectorStore`, numa
  transação: um `DELETE` + um `INSERT` em lote). Reindexar um documento
  editado não deixa cauda antiga; passe todos os chunks de uma fonte na
  mesma chamada.

```python
from tempest_fastapi_sdk.genai import Embedder, EmbeddingModelEnum
from tempest_fastapi_sdk.genai.rag import PgVectorStore, Retriever

from src.api.dependencies.resources import db

embedder = Embedder(EmbeddingModelEnum.ALL_MINILM_L6_V2)


store = PgVectorStore(db, dim=384)          # db = AsyncDatabaseManager
rag = Retriever(embedder, store)
```

O nome da tabela é interpolado no SQL (identificador não vira parâmetro),
então o construtor recusa com `ValueError` qualquer coisa fora de
`nome` ou `schema.nome` sem aspas (`[A-Za-z_][A-Za-z0-9_]*`, até 63
caracteres cada), e um `dim` que não seja inteiro positivo.

### Índice aproximado: HNSW ou IVFFlat

Sem mais nada, a busca é **exata**: o Postgres faz varredura sequencial e
calcula `<=>` contra toda linha. Com 100 000 chunks de 384 dimensões isso já
custa ~50–60 ms por busca (medição abaixo), e o custo acompanha o número de
linhas.

Um índice aproximado troca um pouco de **recall** (a fração dos vizinhos
exatos que volta) por latência. Peça no `ensure_schema`:

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

- **`ann_index="hnsw"`** constrói um grafo navegável. Parâmetros de build:
  `m` (conexões por nó) e `ef_construction` (candidatos durante o build).
  Exige pgvector **>= 0.5.0**; abaixo disso o `ensure_schema` levanta
  `RuntimeError` com a versão encontrada.
- **`ann_index="ivfflat"`** divide os vetores em `lists` grupos e, na busca,
  varre só os mais próximos. Build muito mais rápido, mas os centróides saem
  das linhas que existem **na hora do build** — chame depois de carregar o
  corpus, não antes do primeiro `add`.
- O índice se chama `<tabela>_embedding_idx` (`store.ann_index_name`) e usa
  `vector_cosine_ops`, a operator class do `<=>` que o `search` ordena.
- Parâmetro deixado em `None` fica fora do `WITH`, e vale o default do
  pgvector. Parâmetro do outro método (`lists` com HNSW) ou sem
  `ann_index` levanta `ValueError` antes de qualquer SQL.
- Chamar de novo com os mesmos parâmetros não faz nada. Com **outro** método
  ou outros parâmetros levanta `ValueError` pedindo `DROP INDEX`: rebuild de
  índice grande não acontece escondido dentro de um `ensure_schema`.

Na busca, `ef_search` (HNSW) e `probes` (IVFFlat) regulam o trade-off por
chamada. O `search` aplica os dois com `set_config(..., true)`, que é o
`SET LOCAL` em forma de função: vale só na transação da busca, e a próxima
sessão no mesmo pool volta a ler o valor do servidor (`40` e `1`, conferido
no teste docker).

!!! warning "`ef_search` também limita quantos resultados voltam"
    Uma varredura HNSW devolve no máximo `ef_search` linhas: medido no
    pgvector 0.8.6, `search(top_k=20, ef_search=5)` devolve **5** chunks.
    Mantenha `ef_search >= top_k`.

#### Quanto rende

Medido com o `PgVectorStore.search` de ponta a ponta (Python + asyncpg +
Postgres em `localhost`), `top_k=10`, contra `pgvector/pgvector:pg16`
(pgvector 0.8.6, configuração default do container, `--shm-size=1g`) numa
máquina de 12 núcleos sob WSL2. **N = 100 000 vetores de 384 dimensões.**
Recall@10 é a fração dos 10 vizinhos da varredura exata que o índice
devolve; cada linha junta duas execuções com seeds diferentes (200 buscas
por seed nos dados gaussianos, 500 nos agrupados), e o intervalo mostra as
duas.

Dois corpora sintéticos, que cercam o caso real por cima e por baixo:

- **gaussiano**: cada componente é `N(0, 1)`, sem estrutura nenhuma — o
  vizinho mais próximo quase não é mais próximo que o resto;
- **agrupado**: 1 000 centros gaussianos, cada vetor é um centro mais ruído
  `N(0, 0,5)`; a consulta é gerada do mesmo jeito.

| Busca | p50 | recall@10 gaussiano | recall@10 agrupado |
| --- | --- | --- | --- |
| exata (varredura sequencial) | 50–60 ms | 1,00 | 1,00 |
| HNSW, `ef_search=40` (default) | 4–8 ms | 0,05–0,06 | 0,986–0,992 |
| HNSW, `ef_search=100` | 4–8 ms | 0,12–0,13 | 1,00 |
| HNSW, `ef_search=400` | 7–20 ms | 0,36–0,37 | 1,00 |
| IVFFlat `lists=100`, `probes=1` (default) | 3,4–4,4 ms | 0,03–0,04 | 0,992–0,996 |
| IVFFlat `lists=100`, `probes=10` | 6–7,5 ms | 0,23 | 0,9996–0,9998 |
| IVFFlat `lists=100`, `probes=40` | 17–20 ms | 0,62 | 1,00 |

Build do índice sobre as 100 000 linhas: HNSW com os defaults (`m=16`,
`ef_construction=64`) levou 72–81 s no gaussiano e 26 s no agrupado; IVFFlat
com `lists=100`, 0,8–1,5 s.

O que tirar disso:

- **Latência**: nesse N o índice leva a busca de ~50 ms para ~5 ms. Com
  5 000 linhas gaussianas (50 buscas, uma seed) a varredura exata mediu
  ~20 ms de p50 e o HNSW ~4 ms: o ganho existe, mas é de milissegundos.
- **Recall depende do corpus, não só do índice.** Os mesmos parâmetros
  devolvem quase tudo nos dados agrupados e quase nada nos gaussianos.
  Embedding de texto tem estrutura (documentos do mesmo assunto ficam
  perto), mas quanto — isso só medindo no **seu** corpus: compare o
  `search` com índice contra o resultado exato (antes de criar o índice)
  num punhado de consultas reais e suba `ef_search`/`probes` até o recall
  servir.
- **Poucos milhares de chunks?** Fique na busca exata: o custo é pequeno e
  o recall é 1.

`rag.search(query, top_k=)` devolve os `Chunk` com `score` (similaridade);
`rag.retrieve(...)` já monta o contexto. Precisa de Qdrant/Weaviate depois?
Implemente o `VectorStore` (2 métodos) e injete — o `Retriever` não muda.
