# IA generativa self-hosted

Rodar modelos do HuggingFace no **seu próprio hardware** — sem API
externa, sem enviar dados pra fora. O módulo `tempest_fastapi_sdk.genai`
está sendo entregue em fatias; esta página cobre a **primeira**: saber,
*antes* de baixar gigabytes de pesos, se a máquina aguenta o modelo.

!!! info "Roadmap do módulo"
    - **v0.96:** `genai.hardware` — sondagem + `can_run` / `recommend`.
    - **v0.97:** `genai.rag` — contexto RAG (busca web SearXNG + leitura de
      PDF) pra injetar nas LLMs (esta página, seção [Contexto RAG](#contexto-rag)).
    - **v0.98:** `TextGenerator` — LLM local + quantização int4/int8
      (seção [Gerar texto](#gerar-texto-com-llm-local)).
    - **v0.99:** `Embedder`, `BatchScheduler`, `ModelRegistry` — embeddings
      + escala (seção [Embeddings e escala](#embeddings-e-escala)).
    - **v0.102:** `SpeechToText` / `TextToSpeech` — áudio (seção
      [Áudio (voz)](#audio-voz)).

O extra `[genai]` (transformers + torch + accelerate) só é necessário pra
**rodar** modelos. As funções de capacidade **importam sem o extra** — o
`torch` só é usado (quando presente) pra ver a VRAM real da GPU.

## "A máquina aguenta?"

Carregar um modelo grande demais termina num OOM minutos depois do
download começar. `can_run` responde antes:

```python
from tempest_fastapi_sdk.genai import can_run, ModelDtype

report = can_run(model_id="Qwen/Qwen2.5-7B-Instruct", dtype=ModelDtype.BFLOAT16)

if report.fits:
    print(f"OK em {report.device} — {report.headroom_pct:.0f}% de folga")
else:
    print(report.reason)
    print("Sugestão:", report.suggestion)   # ex.: "Quantize to int4 ..."
```

O `CapacityReport` traz: `fits`, `device` (`cuda`/`mps`/`cpu`),
`estimated_bytes` vs `available_bytes`, `headroom_pct`, `reason` e uma
`suggestion` concreta quando não cabe (quantizar, offload pra CPU, ou
trocar de modelo).

!!! tip "Deixe o SDK escolher a precisão"
    `recommend(...)` tenta `bfloat16` → `int8` → `int4` no melhor device
    disponível e devolve a **primeira** config que cabe:

    ```python
    from tempest_fastapi_sdk.genai import recommend

    best = recommend(model_id="meta-llama/Llama-3.1-8B")
    print(best.device, best.dtype, best.fits)   # ex.: cuda int8 True
    ```

## Sondando o hardware

```python
from tempest_fastapi_sdk.genai import probe_hardware

hw = probe_hardware()
print(hw.cpu_cores, hw.ram_available_bytes)
print(hw.has_cuda, [g.name for g in hw.gpus])   # VRAM por GPU quando há CUDA
```

`HardwareInfo` reporta CPU, RAM total/disponível, GPUs CUDA (nome +
VRAM total/livre), MPS (Apple) e espaço livre em disco. Sem `psutil` ou
`torch` instalados, os campos correspondentes caem pra defaults seguros
(`0` / `False` / lista vazia) — nada quebra.

## Estimativa sem baixar pesos

A conta é `nº de parâmetros × bytes por parâmetro × overhead`. Os bytes
por parâmetro vêm da precisão (`float32`=4, `float16`/`bfloat16`=2,
`int8`=1, `int4`≈0.6); o overhead (×1.25) cobre ativações, KV-cache e
contexto de runtime.

```python
from tempest_fastapi_sdk.genai import estimate_model_bytes, ModelDtype

gb = estimate_model_bytes(7_000_000_000, ModelDtype.INT4) / 1e9
print(f"~{gb:.1f} GB")   # 7B em int4
```

O número de parâmetros pode vir explícito (`num_params=`) ou ser lido do
Hub por `model_id` (via `huggingface_hub`, sem baixar os pesos —
metadados safetensors).

## Gerar texto com LLM local

`TextGenerator` carrega um LLM causal do HuggingFace **uma vez** e gera no
seu hardware. Resolve device e precisão sozinho, suporta quantização
int4/int8, carrega os pesos preguiçosamente (na 1ª chamada) e libera VRAM
quando ocioso. Requer `[genai]` (e `[genai-quant]` pra quantizar).

```python
from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator(
    "Qwen/Qwen2.5-7B-Instruct",
    quantization="int4",            # cabe em GPU modesta; None = precisão cheia
    idle_unload_seconds=300,        # libera VRAM após 5 min ocioso
)

texto = await gen.generate("Explique PIX em uma frase.", max_new_tokens=128)

# chat com template de papéis:
resposta = await gen.chat([
    {"role": "system", "content": "Você responde em PT-BR."},
    {"role": "user", "content": "O que é PIX?"},
])

# streaming token a token:
async for pedaco in gen.stream("Escreva um haiku sobre chuva."):
    print(pedaco, end="", flush=True)

gen.unload()                        # libera a memória na hora
```

A geração bloqueante roda em `asyncio.to_thread` — não trava o event loop.
`device="auto"` escolhe CUDA → MPS → CPU; `dtype="auto"` usa bf16 em GPU e
fp32 em CPU.

!!! tip "Confira antes de carregar"
    Combine com o [capacity check](#a-maquina-aguenta): rode `can_run` /
    `recommend` pra escolher `quantization`/`device` que **cabem** antes
    de instanciar o `TextGenerator`.

!!! tip "Libere VRAM entre picos"
    Com `idle_unload_seconds` setado, chame `gen.unload_if_idle()`
    periodicamente (ex.: num `@tq.interval(60)` do [TaskQueue](queue-tasks.md))
    — ele descarrega o modelo só quando passou do tempo ocioso, sem mágica
    de background thread. `unload()` libera na hora.

## Embeddings e escala

### Gerar embeddings

`Embedder` transforma texto em vetores no seu hardware (busca semântica,
RAG, clustering). Carrega o modelo uma vez, faz batch e (opcional) cacheia
vetor por texto — cache hit nem toca no modelo.

```python
from tempest_fastapi_sdk.genai import Embedder, InMemoryEmbeddingCache

emb = Embedder(
    "sentence-transformers/all-MiniLM-L6-v2",
    cache=InMemoryEmbeddingCache(),     # ou um wrapper Redis (get/set)
)

vetores = await emb.embed(["o que é pix?", "como estornar?"])   # list[list[float]]
```

O `cache` é qualquer objeto com `get(key)->list|None` e `set(key, val)` —
passe um wrapper sobre o `AsyncRedisManager` pra compartilhar entre
workers. `device`/`dtype`/`unload`/`unload_if_idle` funcionam como no
`TextGenerator`.

Pra busca semântica, use `normalize=True` (vetores unitários) + a função
`cosine_similarity`:

```python
from tempest_fastapi_sdk.genai import cosine_similarity

emb = Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True)
q, *docs = await emb.embed(["pergunta", "doc a", "doc b"])
ranked = sorted(docs, key=lambda d: cosine_similarity(q, d), reverse=True)
```

### Batch de inferência concorrente

Numa GPU, rodar um item por vez desperdiça o device. `BatchScheduler`
coalesce chamadas concorrentes num lote só — cada chamador ainda dá
`await` no seu próprio resultado:

```python
from tempest_fastapi_sdk.genai import BatchScheduler

sched = BatchScheduler(emb._embed_many, max_batch=32, max_wait_ms=10)

# N requests concorrentes viram 1 forward pass:
vetor = await sched.submit("texto")
await sched.aclose()
```

Forma um lote quando junta `max_batch` itens **ou** passa `max_wait_ms`
desde o primeiro — o que vier antes. Erro do handler propaga pra todos os
chamadores do lote.

### Compartilhar modelos carregados

`ModelRegistry` mantém modelos carregados por id (LRU) — dois call sites
pedindo o mesmo modelo reusam a instância, e o menos usado é descarregado
(`unload()`) quando passa de `max_models`:

```python
from tempest_fastapi_sdk.genai import Embedder, ModelRegistry

registry = ModelRegistry(max_models=2)

def get_embedder(model_id: str) -> Embedder:
    return registry.get(model_id, lambda: Embedder(model_id))
```

## Áudio (voz)

Interpretar e gerar voz no seu hardware — sem API externa. Requer o extra
`[genai-audio]` (faster-whisper + Coqui TTS); as engines importam
preguiçosamente.

### Interpretar áudio (STT)

`SpeechToText` transcreve com **faster-whisper** (Whisper via CTranslate2,
rápido em CPU/GPU). Carrega uma vez, roda em worker thread, serializa
chamadas por um semáforo.

```python
from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText("base", device="auto")     # tiny…large-v3
result = await stt.transcribe("reuniao.wav")
print(result.text, result.language, result.duration)
for seg in result.segments:                    # timestamps por trecho
    print(seg.start, seg.end, seg.text)
```

Aceita caminho ou `bytes`. `device`/`compute_type` resolvem sozinhos
(`float16` na GPU, `int8` na CPU).

### Gerar voz (TTS)

`TextToSpeech` sintetiza com **Coqui TTS** (WAV). Mesma disciplina
(lazy + thread + semáforo).

```python
from tempest_fastapi_sdk.genai.audio import TextToSpeech

tts = TextToSpeech("tts_models/multilingual/multi-dataset/xtts_v2")
wav = await tts.synthesize("Olá, mundo.", language="pt")   # -> bytes WAV
# clonagem de voz (XTTS): passe um clipe de referência
wav = await tts.synthesize("Oi!", language="pt", speaker_wav="ref.wav")
```

`synthesize` devolve os `bytes` do WAV; passe `out_path=` pra também
gravar em disco.

### Idioma (PT-BR / EN-US)

Não precisa saber o código do Whisper nem escolher modelo TTS: use o enum
`Language`. Ele resolve o código (`pt`/`en`) pro STT e um modelo TTS bom
por idioma:

```python
from tempest_fastapi_sdk.genai.audio import Language, SpeechToText, TextToSpeech

# STT: força o idioma sem decorar o código
await SpeechToText("base").transcribe("audio.wav", language=Language.PT_BR)

# TTS: pega o modelo padrão do idioma automaticamente
tts = TextToSpeech.for_language(Language.PT_BR)     # modelo pt-BR
wav = await tts.synthesize("Olá, mundo.")
```

`preset_for(Language.PT_BR)` expõe o preset (`whisper_language`,
`tts_model`, `tts_language`) se quiser inspecionar/override. `language=`
no `transcribe`/`synthesize` também aceita o código cru (`"pt"`) ou `None`
(auto-detect no STT).

!!! tip "Loop de voz completo"
    Encadeie com o LLM: **STT** transcreve a fala → `TextGenerator`/RAG
    responde → **TTS** fala a resposta. Tudo local, nada sai da máquina.

## Contexto RAG

Uma LLM local só sabe o que treinou. Pra respostas atuais e fundamentadas,
injete contexto: `tempest_fastapi_sdk.genai.rag` busca na web (SearXNG
self-hosted), extrai o corpo das páginas, lê PDFs e monta um bloco pronto
pro prompt — tudo sem enviar dados pra fora. Requer o extra `[genai-rag]`
(httpx + trafilatura + pymupdf); as peças importam preguiçosamente.

### Busca web (SearXNG)

```python
import httpx
from tempest_fastapi_sdk.genai.rag import SearxngBackend, WebSearch, build_context

client = httpx.AsyncClient()
search = WebSearch(SearxngBackend("http://localhost:8080", http_client=client))

results = await search.search("o que é PIX?", max_results=5)   # list[SearchResult]
context = build_context("o que é PIX?", results, long_text=False, max_chars=2000)
# -> string pronta pra injetar no prompt do seu TextGenerator
```

O backend é um `Protocol` (`WebSearchBackend`) — troque o SearXNG por
outro provedor sem mexer no call site. O `httpx.AsyncClient` é injetado
(reaproveita o pool; ligue no lifespan do FastAPI).

!!! tip "Da pergunta ao contexto em uma chamada"
    `WebSearch.retrieve` faz busca → (opcional) extração dos corpos em
    paralelo → `build_context`, tudo de uma vez:

    ```python
    from tempest_fastapi_sdk.genai.rag import ContentExtractor

    extractor = ContentExtractor(http_client=client)
    context = await search.retrieve("o que é PIX?", extractor=extractor, max_results=5)
    resposta = await gen.generate(context)
    ```

    Sem `extractor`, usa só os snippets. `ContentExtractor.extract_many`
    busca N páginas concorrentes (limitado por `concurrency`).

### Extrair o corpo das páginas

O snippet do buscador é raso. Pra dar "verdade" pra LLM, busque cada
página e extraia o texto limpo (via `trafilatura`):

```python
from tempest_fastapi_sdk.genai.rag import ContentExtractor

extractor = ContentExtractor(http_client=client)
for result in results:
    outcome = await extractor.extract(result.url)
    result.content = outcome.text          # "" quando falha; outcome.failed marca
context = build_context("o que é PIX?", results)   # agora com corpo completo
```

Falhas (timeout, 4xx/5xx, página sem corpo) **nunca** levantam — voltam
como `ExtractionResult(text="", failed=True)`, então nenhuma fonte some
silenciosamente.

### Ler PDFs (base de conhecimento)

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

### RAG sobre corpus próprio (vector store)

Busca web é uma fonte; a outra é o **seu conhecimento** (PDFs, docs). Em
vez de reembeddar tudo a cada request, indexe uma vez num **vector store**
e recupere por similaridade. `Retriever` amarra `Embedder` → store →
`build_context`:

```python
from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

rag = Retriever(Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
                InMemoryVectorStore())

await rag.index(PdfReader().chunks("/kb/manual.pdf"))     # uma vez
context = await rag.retrieve("como estornar?", top_k=5)   # depois, barato
answer = await gen.generate(context)
```

- **`VectorStore`** é um `Protocol` — `InMemoryVectorStore` (dev/testes,
  scan por cosseno) ou `PgVectorStore` (produção).
- **`PgVectorStore`** usa **pgvector** no Postgres que o serviço já tem
  (sem infra nova): cria a tabela sob demanda, busca com o operador de
  distância cosseno `<=>`. Requer `[genai-rag]` + `CREATE EXTENSION vector`.

```python
from tempest_fastapi_sdk.genai.rag import PgVectorStore

store = PgVectorStore(db, dim=384)          # db = AsyncDatabaseManager
rag = Retriever(embedder, store)
```

`rag.search(query, top_k=)` devolve os `Chunk` com `score` (similaridade);
`rag.retrieve(...)` já monta o contexto. Precisa de Qdrant/Weaviate depois?
Implemente o `VectorStore` (2 métodos) e injete — o `Retriever` não muda.

## Recap

- **`can_run` / `recommend`** — respondem se o host roda o modelo e o que
  fazer se não rodar, **antes** do download.
- **RAG sobre corpus** — `Retriever` + `VectorStore` (`InMemoryVectorStore`
  / `PgVectorStore` pgvector): indexe chunks uma vez, recupere top-k por
  similaridade.
- **Áudio** — `SpeechToText` (faster-whisper) transcreve; `TextToSpeech`
  (Coqui TTS) sintetiza — voz local ponta a ponta.
- **RAG** — `WebSearch`/`SearxngBackend` (busca), `ContentExtractor`
  (corpo das páginas), `PdfReader` (PDF → texto/chunks) e `build_context`
  (bloco pro prompt), todos sob o extra `[genai-rag]`.
- **`probe_hardware`** — snapshot de CPU/RAM/GPU/disco; degrada sem os
  extras.
- **`estimate_model_bytes` / `bytes_per_param`** — a matemática da
  estimativa, testável e reutilizável.
- Tudo importa sem o extra `[genai]`; instale-o pra rodar modelos de fato
  (fatias seguintes do módulo).
