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
    - **v0.107:** backend Ollama — `OllamaGenerator` / `OllamaEmbedder`,
      LLM local sem torch (seção [Backend Ollama](#backend-ollama)).
    - **v0.108:** memória de longo prazo, pipeline de chat com IA e
      visão/tools — `ChatMemory` / `AIChatPipeline` (seções
      [Memória de longo prazo](#memoria-de-longo-prazo) e
      [Pipeline de chat com IA](#pipeline-de-chat-com-ia)).

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

## Backend Ollama

O `TextGenerator` carrega os pesos do HuggingFace com `torch` no seu
hardware — ótimo quando você tem GPU/torch, mas exige baixar gigabytes de
pesos e gerenciar VRAM. Se você já roda um **daemon Ollama** local, o
`OllamaGenerator` usa a **mesma superfície** do `genai` (router,
`Retriever`, `GenerationConfig`) falando HTTP com o Ollama: nada de torch,
nada de pesos locais, nada de `load()`. O Ollama cuida do download e da
VRAM sozinho.

Requer o extra `[genai-ollama]` (só `httpx`) e o daemon rodando com o
modelo já baixado:

```bash
uv add "tempest-fastapi-sdk[genai-ollama]"
ollama pull llama3.2
ollama pull nomic-embed-text
```

### Gerar texto via Ollama

`OllamaGenerator` espelha o `TextGenerator` — `generate`, `chat` e
`stream`, mesma assinatura:

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaGenerator

gen = OllamaGenerator("llama3.2")   # base_url padrão = http://127.0.0.1:11434


async def main() -> None:
    # geração simples:
    texto = await gen.generate("Explique PIX em uma frase.")
    print(texto)

    # chat com template de papéis:
    resposta = await gen.chat([
        {"role": "system", "content": "Você responde em PT-BR."},
        {"role": "user", "content": "O que é PIX?"},
    ])
    print(resposta)

    # streaming token a token:
    async for pedaco in gen.stream("Escreva um haiku sobre chuva."):
        print(pedaco, end="", flush=True)


asyncio.run(main())
```

Sem `load()` nem `unload()`: o modelo vive no daemon Ollama, que baixa na
1ª chamada e libera a VRAM sozinho. `base_url` aponta pra outro host se o
Ollama não for local (o padrão é `DEFAULT_OLLAMA_URL`); `keep_alive`,
`timeout` e um `http_client` seu (pra reaproveitar o pool) são opcionais.

!!! info "`GenerationConfig` vira opções do Ollama"
    O mesmo `GenerationConfig` tipado funciona aqui — os campos são
    traduzidos pras opções do Ollama: `max_new_tokens`→`num_predict`,
    `repetition_penalty`→`repeat_penalty`, e `temperature`/`top_p`/`top_k`/
    `seed`/`stop` passam direto. `do_sample=False` vira `temperature=0`
    (geração greedy).

### Embeddings via Ollama + RAG

`OllamaEmbedder` satisfaz o mesmo protocolo `SupportsEmbed` do `Embedder`,
então entra no `Retriever` e no endpoint `/embed` sem mudar mais nada — as
embeddings saem do Ollama em vez do torch:

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaEmbedder, OllamaGenerator
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

gen = OllamaGenerator("llama3.2")
rag = Retriever(OllamaEmbedder("nomic-embed-text"), InMemoryVectorStore())


async def main() -> None:
    await rag.index(PdfReader().chunks("/kb/manual.pdf"))     # uma vez
    context = await rag.retrieve("como estornar?", top_k=5)   # depois, barato
    print(await gen.generate(context))


asyncio.run(main())
```

`embed(texts, *, batch_size=32)` devolve `list[list[float]]`, igual ao
`Embedder`.

### Busca híbrida (BM25 + denso)

A busca densa capta significado mas erra termos exatos — nomes próprios,
códigos, siglas que a query compartilha literalmente com o chunk. O BM25
(esparso) acerta esses e ignora semântica. O `HybridRetriever` roda os
dois sobre os mesmos chunks indexados e funde os rankings com **Reciprocal
Rank Fusion** — então "o que o BACEN faz?" acha o chunk que diz "BACEN"
mesmo com score denso morno. BM25 vem do `rank-bm25` (extra `[genai-rag]`).

```python
from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import HybridRetriever, InMemoryVectorStore

rag = HybridRetriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
)
await rag.index(chunks)                              # indexa denso + BM25
chunks = await rag.search("o que é CNPJ?", top_k=5)  # funde denso + esparso
```

`search(query, top_k, candidates)` pega `candidates` de cada lado e funde
pra `top_k`. `reciprocal_rank_fusion(rankings, k=60)` está exposto avulso
pra fundir rankings arbitrários. O índice BM25 é in-memory (reconstruído a
cada `index`) — bom até dezenas de milhares de chunks.
### Reranking (cross-encoder)

A busca densa (embed da query, embed dos chunks, cosseno) é rápida mas
grosseira: nunca vê query e chunk juntos. Um **cross-encoder** pontua cada
par `(query, chunk)` de uma vez — preciso demais pra rodar no corpus
inteiro, ideal como 2ª etapa sobre os top-N candidatos. Injete um
`Reranker` no `Retriever`: a busca super-busca candidatos no store e o
cross-encoder afina pra `top_k`.

```python
from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, Reranker, Retriever

rag = Retriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
    reranker=Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2"),
)
# search pega max(top_k, rerank_candidates) do store e reordena pra top_k:
chunks = await rag.search("como estornar?", top_k=5, rerank_candidates=20)
```

Sem `reranker`, o `Retriever` continua denso puro. O `Reranker` (extra
`[genai]`) tem lazy load + `unload`/`unload_if_idle` como o
`TextGenerator`.

### Mesmo router, torch OU Ollama

O `make_genai_router` type-hinta `TextBackend` / `SupportsEmbed`, então os
objetos Ollama entram no lugar dos de torch sem tocar no resto:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import (
    OllamaEmbedder,
    OllamaGenerator,
    make_genai_router,
)

app = FastAPI()
app.include_router(
    make_genai_router(
        text_generator=OllamaGenerator("llama3.2"),
        embedder=OllamaEmbedder("nomic-embed-text"),
    )
)
```

Trocar `TextGenerator` / `Embedder` (torch) por `OllamaGenerator` /
`OllamaEmbedder` é a única mudança — os endpoints `/generate`,
`/generate/stream`, `/chat` e `/embed` são idênticos.

!!! tip "`TextBackend` é a costura pra qualquer engine"
    `TextBackend` é um `Protocol` `runtime_checkable` (`generate` / `chat`
    / `stream`). Ollama é só uma implementação; pra plugar vLLM, TGI ou uma
    API hospedada, implemente o mesmo protocolo e injete no router /
    `Retriever` — o call site não muda.

## Memória de longo prazo

Um chat que esquece tudo entre sessões não é assistente — é um formulário.
`ChatMemory` dá **memória de longo prazo** à conversa: cada turno vira uma
embedding indexada, e antes de responder você recupera os trechos mais
relevantes do **próprio usuário** — inclusive de chats antigos. Recall com
recência: o que é semanticamente próximo *e* recente sobe primeiro.

Requer o extra `[genai-chroma]` (ChromaDB). O embedder é qualquer
`SupportsEmbed` — aqui um `OllamaEmbedder`, sem torch:

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
        query="qual stack ele usa?",
        exclude_chat_id="c2",     # ignora o chat atual
    )
    for hit in hits:
        print(f"{hit.score:.2f}  {hit.content}")


asyncio.run(main())
```

`search` filtra pelo `user_id`, aplica o piso de similaridade
(`min_similarity`) e então mistura o decaimento de recência — cada
`MemoryHit` traz `content`, `role`, `chat_id`, `created_at`, `similarity`
(cosseno cru) e `score` (o valor final, já com recência). `delete_for_chat`
apaga tudo de um chat quando ele é removido.

!!! info "Extra `[genai-chroma]` e o decaimento de recência"
    Instale com `uv add "tempest-fastapi-sdk[genai-chroma]"`. O `score`
    final combina similaridade e recência via
    `0.5 ** (idade_em_dias / recency_halflife_days)` — com o padrão de 14
    dias, um trecho de 14 dias atrás pesa metade de um recém-escrito.
    Ajuste a mistura com `recency_weight` (0 = só similaridade).

!!! tip "RAG genérico com o `ChromaVectorStore`"
    Precisa só de um vector store persistente (sem a lógica de memória por
    usuário)? `ChromaVectorStore` é um `VectorStore` como os outros —
    `add(chunks, vectors)` / `search(vector, top_k=)` — respaldado por
    ChromaDB. Injete no `Retriever` no lugar do `InMemoryVectorStore` /
    `PgVectorStore` pra ter um corpus persistido em disco:

    ```python
    from tempest_fastapi_sdk.genai import OllamaEmbedder
    from tempest_fastapi_sdk.genai.rag import ChromaVectorStore, Retriever

    rag = Retriever(
        OllamaEmbedder("nomic-embed-text"),
        ChromaVectorStore(collection_name="kb", persist_directory="./kb"),
    )
    ```

## Pipeline de chat com IA

Aqui as fatias anteriores se encaixam. Montar um chatbot "de verdade" —
memória, RAG por web, tool-calling, TTS opcional — normalmente significa
escrever (e manter) um microserviço de inferência inteiro. `AIChatPipeline`
faz isso **dentro do seu processo**: você injeta as peças que já viu
(`OllamaGenerator`, `ChatMemory`, `WebSearch`, `Tool`s) e chama `respond`.

```python
import asyncio

from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    OllamaEmbedder,
    OllamaGenerator,
    Tool,
)
from tempest_fastapi_sdk.genai.rag import ChatMemory, SearxngBackend, WebSearch


async def get_weather(args: dict) -> str:
    """Handler da tool: recebe os args validados, devolve texto pro modelo."""
    return f"Faz 24°C em {args['city']}."


weather_tool = Tool(
    name="get_weather",
    description="Consulta o clima de uma cidade.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    handler=get_weather,
)

pipeline = AIChatPipeline(
    OllamaGenerator("llama3.2"),
    memory=ChatMemory(OllamaEmbedder("nomic-embed-text")),
    web_search=WebSearch(SearxngBackend("http://localhost:8080")),
    tools=[weather_tool],
    base_system_prompt="Você é um assistente objetivo, responde em PT-BR.",
)


async def main() -> None:
    result = await pipeline.respond(
        user_id="u1",
        chat_id="c1",
        content="Como está o tempo em Recife?",
        use_web_search=False,      # True augmenta o prompt com busca web
        speak=False,               # True gera áudio (precisa de tts=)
    )
    print(result.reply)
    print("tools chamadas:", result.tool_calls_made)
    print("fontes:", result.sources)
    print("memórias usadas:", len(result.memory_hits))


asyncio.run(main())
```

`respond` faz o ciclo completo: recupera memória → (opcional) augmenta com
busca web → monta as mensagens (system + memória + contexto + histórico +
turno do usuário; `images` viajam no turno do usuário) → gera (com loop de
tool-calling limitado quando há `tools` + um backend que suporta —
`OllamaGenerator` **ou** o `TextGenerator` local (transformers); senão,
`chat` puro) → (opcional) TTS → indexa os dois turnos na memória
(best-effort). O `AIChatResult` traz `reply`, `sources`, `memory_hits`,
`tool_calls_made` e `audio_base64`.

!!! tip "Tools no backend local (transformers)"
    `TextGenerator.chat_with_tools` renderiza o chat template com
    `tools=` (transformers >= 4.44) e faz o parse dos tool-calls que o
    modelo emite (`<tool_call>{...}</tool_call>` do Qwen/Hermes ou JSON
    Llama), devolvendo a mesma forma que o `OllamaGenerator` — então o
    mesmo `AIChatPipeline` roda com pesos locais, sem depender do daemon.
    Use um modelo instruct com suporte a ferramentas (ex.
    `Qwen/Qwen2.5-7B-Instruct`).

### Endpoint pronto: `make_ai_chat_router`

Um router, um backend de chat inteiro no processo:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import make_ai_chat_router

app = FastAPI()
app.include_router(make_ai_chat_router(pipeline))   # prefixo /api/ai-chat
```

Ele monta `POST /api/ai-chat/chat` (devolve `AIChatResult`) e
`POST /api/ai-chat/chat/stream` (tokens via SSE).

!!! note "O router é stateless"
    O histórico vive no corpo do request, não no servidor — cada chamada
    manda o `history`. Isso mantém o backend sem sessão (escala horizontal
    de graça) e a memória de longo prazo cuida do "lembrar" via
    `ChatMemory`.

### Streaming

`stream` devolve os tokens conforme saem (modo prompt; resolve qualquer
tool-call **antes** de começar a emitir):

```python
import asyncio


async def stream_demo() -> None:
    async for token in pipeline.stream(
        user_id="u1", chat_id="c1", content="Explique RAG em uma frase.",
    ):
        print(token, end="", flush=True)


asyncio.run(stream_demo())
```

!!! tip "O microserviço de inferência vira uma escolha, não um requisito"
    Com o pipeline in-process, ter um serviço separado só pra LLM passa a
    ser uma decisão de organização (isolar a GPU, escalar à parte) — não
    uma obrigação arquitetural. O mesmo `TextBackend` deixa você trocar
    Ollama por vLLM/TGI depois sem mudar o call site.

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

### Embeddings ONNX (sem torch)

Se você não quer a stack pesada do `torch`/`transformers` só pra embeddar,
`OnnxEmbedder` roda um modelo de embedding exportado pra ONNX via ONNX
Runtime — dependências leves (`onnxruntime` + `tokenizers`, extra
`[genai-onnx]`), CPU-barato. Satisfaz o mesmo `SupportsEmbed`, então entra
no `Retriever` / `make_genai_router` sem mudar nada.

```python
from tempest_fastapi_sdk.genai import OnnxEmbedder

emb = OnnxEmbedder(
    "all-MiniLM-L6-v2.onnx",
    tokenizer="sentence-transformers/all-MiniLM-L6-v2",
    normalize=True,
)
vectors = await emb.embed(["pergunta", "doc a"])
```

O pooling é a **média ponderada pela attention mask** dos embeddings de
token (não uma média ingênua sobre padding), então os vetores batem com os
do `Embedder` torch (cosseno ≈ 1.0 pro mesmo modelo). Exporte o modelo com
`optimum` (`optimum-cli export onnx ...`) e aponte `model_path` pro `.onnx`.

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
from tempest_fastapi_sdk.utils.http_client import HTTPClient

client = HTTPClient()
search = WebSearch(SearxngBackend("http://localhost:8080", http_client=client))

results = await search.search("o que é PIX?", max_results=5)   # list[SearchResult]
context = build_context("o que é PIX?", results, long_text=False, max_chars=2000)
# -> string pronta pra injetar no prompt do seu TextGenerator
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

### Extrair o corpo das páginas

O snippet do buscador é raso. Pra dar "verdade" pra LLM, busque cada
página e extraia o texto limpo (via `trafilatura`):

```python
from tempest_fastapi_sdk.genai.rag import ContentExtractor

extractor = ContentExtractor(http_client=httpx.AsyncClient())
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

## Ergonomia: config tipada, router e cache Redis

### `GenerationConfig` tipado

Em vez de espalhar `**kwargs` (`max_new_tokens=...`, `temperature=...`)
por cada chamada, monte um `GenerationConfig` validado e reutilizável e
passe via `config=`:

```python
from tempest_fastapi_sdk.genai import GenerationConfig, TextGenerator

gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
config = GenerationConfig(max_new_tokens=512, temperature=0.2, top_p=0.9)

await gen.generate("Explique PIX em uma frase.", config=config)
await gen.chat([{"role": "user", "content": "Oi"}], config=config)
```

Só os campos definidos entram sobre os defaults; `**kwargs` explícitos
ainda vencem o config (`gen.generate(prompt, config=config,
temperature=0.9)` usa `0.9`).

!!! tip "`seed` e `stop` valem no path local"
    `seed` e `stop` são honrados tanto no `OllamaGenerator` quanto no
    `TextGenerator` (transformers): `seed` é reaplicado via
    `transformers.set_seed` antes de gerar (mesma seed + `do_sample=True`
    reproduz a saída) e `stop` vira o argumento `stop_strings` de
    `model.generate` (requer transformers >= 4.44). Ambos podem vir do
    `GenerationConfig` ou por chamada — o override por chamada vence.

### Saída estruturada (JSON validado)

Force o modelo a devolver um schema Pydantic e receba a instância já
validada — em vez de torcer pra saída ser um JSON parseável:

```python
from pydantic import BaseModel
from tempest_fastapi_sdk.genai import OllamaGenerator


class Pessoa(BaseModel):
    nome: str
    idade: int


gen = OllamaGenerator("llama3.2")
pessoa: Pessoa = await gen.generate_structured("Uma pessoa qualquer.", Pessoa)
# -> Pessoa(nome="...", idade=...)
```

O `OllamaGenerator` manda o schema no campo `format` do daemon (o Ollama
garante JSON schema-válido nativamente) e faz o parse na saída — **é a
rota estruturada recomendada, sem biblioteca extra**.

!!! info "No backend local (transformers)"
    `TextGenerator.generate_structured(prompt, schema, constrained=True)`
    restringe a decodificação com o `lm-format-enforcer`
    (extra `[genai-structured]`), então o modelo só emite tokens que
    mantêm o JSON válido. Se a versão do `lm-format-enforcer` não casar
    com a do `transformers` instalado, `constrained=True` levanta um erro
    claro — nesse caso use `constrained=False` (best-effort: gera e faz o
    parse) ou o backend Ollama.

!!! tip "Só o parse"
    `parse_structured(texto, schema)` extrai o JSON de uma saída crua
    (tolera cercas markdown e texto ao redor) e valida contra o schema —
    útil pra reaproveitar em qualquer saída de modelo.

### Contagem de tokens e janela de contexto

Pra caber um chat na janela do modelo, conte tokens com o **tokenizer do
próprio modelo** (nunca heurística — BPE e SentencePiece divergem) e dropе
os turnos mais antigos quando estoura:

```python
from tempest_fastapi_sdk.genai import count_tokens, truncate_messages

n = count_tokens("Explique PIX.", tokenizer)   # tokenizer do modelo

fit = truncate_messages(
    messages, max_tokens=3000, tokenizer=tokenizer,
)   # mantém system + último turno, dropa os mais antigos
```

`count_message_tokens(messages, tokenizer, per_message_overhead=4)` soma o
custo do chat; `truncate_messages` preserva os `system` (movidos pra frente)
e o turno mais recente, dropando os antigos até caber. Funcionam sobre
qualquer tokenizer com `encode(text) -> sequência` (o `AutoTokenizer` serve).
### Cache de geração (prompt → completion)

Gerações **determinísticas** (greedy, ou `temperature=0`) produzem sempre o
mesmo texto pro mesmo prompt+params — então dá pra cachear e pular o modelo
numa repetição. Passe um cache no gerador; só chamadas determinísticas são
cacheadas (sampling nunca, pra não devolver amostra velha):

```python
from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    InMemoryGenerationCache,
    OllamaGenerator,
)

gen = OllamaGenerator("llama3.2", generation_cache=InMemoryGenerationCache())
cfg = GenerationConfig(temperature=0)   # determinístico → cacheável
await gen.generate("Explique PIX.", config=cfg)   # roda o modelo
await gen.generate("Explique PIX.", config=cfg)   # servido do cache
```

`InMemoryGenerationCache` é local ao processo; `RedisGenerationCache`
(cache `[cache]`) compartilha entre workers — o gerador dá `await` no
sync-ou-async no mesmo call site. Funciona igual no `TextGenerator`
(`generation_cache=...`). Invalide removendo a chave (ou via TTL no Redis).
### Visão (VLM multimodal local)

O `VisionTextGenerator` é o irmão multimodal do `TextGenerator`: carrega
um `AutoModelForVision2Seq` + `AutoProcessor` e gera texto condicionado a
imagens, no seu hardware. Paridade com o `OllamaGenerator`, que já aceita
`images`. Requer `[genai]` + `[genai-vlm]` (Pillow).

```python
from tempest_fastapi_sdk.genai import VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")
descricao = await gen.generate(
    "USER: <image>\nDescreva a imagem.\nASSISTANT:",
    images=["foto.jpg"],
)
```

As imagens entram como caminho, `bytes`, `PIL.Image` ou `ndarray` NumPy
(mesma leniência do `ort-vision-sdk`). `generate`/`chat` são
image-opcionais — chamadas só-texto continuam funcionando (é um
`TextBackend`).

!!! warning "Convenções de processor variam por família"
    Esta classe mira a interface comum `processor(text=..., images=...)`
    de LLaVA e Qwen2-VL. Outras famílias podem exigir um adaptador fino
    (token de placeholder de imagem, formato do chat template). Valide o
    modelo alvo antes de produção.

### `make_genai_router` — endpoints prontos

Injete os objetos que você tem carregados e o router monta **só** os
endpoints correspondentes:

```python
from fastapi import FastAPI
from tempest_fastapi_sdk.genai import Embedder, TextGenerator, make_genai_router

app = FastAPI()
app.include_router(
    make_genai_router(
        text_generator=TextGenerator("Qwen/Qwen2.5-7B-Instruct"),
        embedder=Embedder("sentence-transformers/all-MiniLM-L6-v2"),
    )
)
```

| Objeto | Endpoints |
| --- | --- |
| `text_generator` | `POST /generate`, `POST /generate/stream` (SSE token a token), `POST /chat` |
| `embedder` | `POST /embed` |
| `retriever` | `POST /rag` (query → bloco de contexto) |
| `speech_to_text` | `POST /transcribe` (upload de áudio) |
| `text_to_speech` | `POST /tts` (devolve `audio/wav`) |

!!! tip "Streaming"
    `/generate/stream` devolve `text/event-stream`: cada token vira um
    evento SSE, encerrando com um evento `done`. Reaproveita o
    `sse_response` do SDK — cliente com `EventSource` recebe os tokens ao
    vivo.

### `RedisEmbeddingCache` — cache compartilhado entre workers

`Embedder` aceita cache síncrono (`InMemoryEmbeddingCache`) **ou**
assíncrono. Troque por `RedisEmbeddingCache` para compartilhar vetores
entre processos sem mudar o call site:

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.genai import Embedder, RedisEmbeddingCache

from src.core.settings import settings

redis = AsyncRedisManager(**settings.redis_kwargs())
# no lifespan: await redis.connect()  (antes de acessar .client)

cache = RedisEmbeddingCache(redis.client, ttl_seconds=86400)
embedder = Embedder("sentence-transformers/all-MiniLM-L6-v2", cache=cache)

await embedder.embed(["texto"])  # 1ª vez calcula; próximos workers reaproveitam
```

O `Embedder` aguarda `get`/`set` quando o cache é assíncrono e chama
direto quando é síncrono — o mesmo código serve aos dois.

!!! note "Client Redis: `AsyncRedisManager`"
    O `RedisEmbeddingCache` recebe um `redis.asyncio.Redis` cru. Como o embedder
    roda em contexto async (serviço/RAG, não middleware), use o
    `AsyncRedisManager` (client gerenciado do SDK) e passe o `.client` **depois**
    do `await redis.connect()` no lifespan — antes disso, `.client` levanta
    `RuntimeError`. Precisa do extra `[cache]` (o pacote `redis`) além do `[genai]`.

## Recap

- **`GenerationConfig`** — parâmetros de geração tipados e reutilizáveis
  no lugar de `**kwargs`.
- **`make_genai_router`** — monta só os endpoints dos objetos injetados;
  `/generate/stream` faz streaming de tokens via SSE.
- **`RedisEmbeddingCache`** — cache de vetores compartilhado; `Embedder`
  aceita cache sync ou async no mesmo call site.
- **Backend Ollama** — `OllamaGenerator` / `OllamaEmbedder` usam a mesma
  superfície (router, `Retriever`, `GenerationConfig`) via daemon Ollama
  local: sem torch, sem pesos, sem `load()`; `TextBackend` é a costura pra
  outras engines.
- **`ChatMemory` / `ChromaVectorStore`** — memória de longo prazo por
  usuário com recall por similaridade + recência (`[genai-chroma]`);
  `ChromaVectorStore` é um `VectorStore` persistente pra RAG genérico.
- **`AIChatPipeline` / `make_ai_chat_router`** — chatbot completo
  in-process (memória + RAG web + tool-calling + TTS opcional); um router
  stateless (`/chat` + `/chat/stream` SSE) mata o microserviço de inferência.
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
