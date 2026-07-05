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
    - **Em breve:** `Embedder`, cache de modelo/resultado, `BatchScheduler`.

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

## Recap

- **`can_run` / `recommend`** — respondem se o host roda o modelo e o que
  fazer se não rodar, **antes** do download.
- **RAG** — `WebSearch`/`SearxngBackend` (busca), `ContentExtractor`
  (corpo das páginas), `PdfReader` (PDF → texto/chunks) e `build_context`
  (bloco pro prompt), todos sob o extra `[genai-rag]`.
- **`probe_hardware`** — snapshot de CPU/RAM/GPU/disco; degrada sem os
  extras.
- **`estimate_model_bytes` / `bytes_per_param`** — a matemática da
  estimativa, testável e reutilizável.
- Tudo importa sem o extra `[genai]`; instale-o pra rodar modelos de fato
  (fatias seguintes do módulo).
