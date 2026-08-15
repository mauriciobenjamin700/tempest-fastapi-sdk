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
import asyncio

from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator(
    "Qwen/Qwen2.5-7B-Instruct",
    quantization="int4",            # cabe em GPU modesta; None = precisão cheia
    idle_unload_seconds=300,        # libera VRAM após 5 min ocioso
)


async def main() -> None:
    """Run this example."""
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


asyncio.run(main())
```

!!! info "O peso baixa uma vez — depois é cache em disco"
    A primeira chamada escreve os GB em `$HF_HOME/hub` (ou
    `~/.cache/huggingface/hub`); as execuções seguintes leem de lá, sem rede.
    Num **container sem volume** isso se perde a cada restart. Como apontar o
    cache, fixar a revisão, pré-baixar no deploy e rodar offline está em
    **[Pesos de modelos »](model-weights.md#onde-os-pesos-ficam-e-por-que-a-2a-execucao-e-instantanea)**.

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
import asyncio

from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import HybridRetriever, InMemoryVectorStore

rag = HybridRetriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
)


async def main() -> None:
    """Run this example."""
    await rag.index(chunks)                              # indexa denso + BM25
    chunks = await rag.search("o que é CNPJ?", top_k=5)  # funde denso + esparso


asyncio.run(main())
```

`search(query, top_k, candidates)` pega `candidates` de cada lado e funde
pra `top_k`. `reciprocal_rank_fusion(rankings, k=60)` está exposto avulso
pra fundir rankings arbitrários. O índice BM25 é in-memory (reconstruído a
cada `index`) — bom até dezenas de milhares de chunks. Tem também
`retrieve(query, top_k)` (busca híbrida → bloco de contexto), então o
`HybridRetriever` satisfaz `SupportsRetrieve` e entra no
`make_genai_router(retriever=...)` no lugar do `Retriever`.

### Reranking (cross-encoder)

A busca densa (embed da query, embed dos chunks, cosseno) é rápida mas
grosseira: nunca vê query e chunk juntos. Um **cross-encoder** pontua cada
par `(query, chunk)` de uma vez — preciso demais pra rodar no corpus
inteiro, ideal como 2ª etapa sobre os top-N candidatos. Injete um
`Reranker` no `Retriever`: a busca super-busca candidatos no store e o
cross-encoder afina pra `top_k`.

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, Reranker, Retriever

rag = Retriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
    reranker=Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2"),
)


async def main() -> None:
    """Run this example."""
    # search pega max(top_k, rerank_candidates) do store e reordena pra top_k:
    chunks = await rag.search("como estornar?", top_k=5, rerank_candidates=20)


asyncio.run(main())
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

!!! tip "Moderação + janela de contexto no pipeline"
    Opcionais no construtor: `moderator=` (um `ModerationBackend` —
    `RuleModerator`/`ClassifierModerator`) filtra o input antes de gerar e
    a resposta depois; turno flagueado responde `blocked_message` (input
    flagueado nem chama o modelo). `tokenizer=` + `max_context_tokens=`
    truncam os turnos mais antigos (via `truncate_messages`) pra caber na
    janela antes de gerar. Ambos opt-in.

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

from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    TextGenerator,
    TextModel,
    make_ai_chat_router,
)

pipeline = AIChatPipeline(generator=TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT))


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

from tempest_fastapi_sdk.genai import AIChatPipeline, TextGenerator, TextModel

pipeline = AIChatPipeline(generator=TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT))


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
import asyncio

from tempest_fastapi_sdk.genai import Embedder, InMemoryEmbeddingCache

emb = Embedder(
    "sentence-transformers/all-MiniLM-L6-v2",
    cache=InMemoryEmbeddingCache(),     # ou um wrapper Redis (get/set)
)


async def main() -> None:
    """Run this example."""
    vetores = await emb.embed(["o que é pix?", "como estornar?"])   # list[list[float]]


asyncio.run(main())
```

O `cache` é qualquer objeto com `get(key)->list|None` e `set(key, val)` —
passe um wrapper sobre o `AsyncRedisManager` pra compartilhar entre
workers. `device`/`dtype`/`unload`/`unload_if_idle` funcionam como no
`TextGenerator`.

Pra busca semântica, use `normalize=True` (vetores unitários) + a função
`cosine_similarity`:

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder, cosine_similarity


emb = Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True)


async def main() -> None:
    """Run this example."""
    q, *docs = await emb.embed(["pergunta", "doc a", "doc b"])
    ranked = sorted(docs, key=lambda d: cosine_similarity(q, d), reverse=True)


asyncio.run(main())
```

### Embeddings ONNX (sem torch)

Se você não quer a stack pesada do `torch`/`transformers` só pra embeddar,
`OnnxEmbedder` roda um modelo de embedding exportado pra ONNX via ONNX
Runtime — dependências leves (`onnxruntime` + `tokenizers`, extra
`[genai-onnx]`), CPU-barato. Satisfaz o mesmo `SupportsEmbed`, então entra
no `Retriever` / `make_genai_router` sem mudar nada.

```python
import asyncio

from tempest_fastapi_sdk.genai import OnnxEmbedder

emb = OnnxEmbedder(
    "all-MiniLM-L6-v2.onnx",
    tokenizer="sentence-transformers/all-MiniLM-L6-v2",
    normalize=True,
)


async def main() -> None:
    """Run this example."""
    vectors = await emb.embed(["pergunta", "doc a"])


asyncio.run(main())
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
import asyncio

from tempest_fastapi_sdk.genai import BatchScheduler, Embedder, EmbeddingModel

emb = Embedder(EmbeddingModel.ALL_MINILM_L6_V2)


sched = BatchScheduler(emb._embed_many, max_batch=32, max_wait_ms=10)


async def main() -> None:
    """Run this example."""
    # N requests concorrentes viram 1 forward pass:
    vetor = await sched.submit("texto")
    await sched.aclose()


asyncio.run(main())
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

### O que está carregado agora

Um serviço self-hosted pode segurar vários modelos ao mesmo tempo, cada um
ocupando gigabytes de VRAM enquanto ficar carregado. `runtime_report`
responde a pergunta operacional — *o que está residente agora?*:

```python
from tempest_fastapi_sdk.genai import Embedder, TextGenerator, runtime_report

report = runtime_report(
    {
        "chat": TextGenerator("Qwen/Qwen2.5-0.5B-Instruct"),
        "embed": Embedder("sentence-transformers/all-MiniLM-L6-v2"),
    },
)
for model in report.models:
    print(model.key, model.kind, model.loaded, model.seconds_idle)
print(report.loaded_count, "de", report.total_count)
```

```text
chat TextGenerator True 612.4
embed Embedder False None
1 de 2
```

A ordem não é acidental: **carregados primeiro, e entre eles o mais ocioso
primeiro** — é a ordem que alguém lê quando a placa encheu e precisa decidir
o que liberar. `model.idle_past_threshold` diz quais já passaram do próprio
limite.

Para um handle só, `describe_model`:

```python
from tempest_fastapi_sdk.genai import TextGenerator, describe_model

info = describe_model(TextGenerator("Qwen/Qwen2.5-0.5B-Instruct"), key="chat")
print(info.kind, info.model_id, info.device, info.loaded)
```

!!! check "Ler nunca carrega"
    `describe_model` só lê atributos — chamá-lo num gerador que nunca
    carregou devolve `loaded=False` e o deixa descarregado. É o que torna
    seguro chamar isso num serviço vivo, inclusive dentro de um health
    check.

!!! note "Campo ausente vira `None`, não chute"
    Cada loader expõe uma superfície um pouco diferente, e objetos de
    terceiros expõem menos ainda. Um handle que só implementa `is_loaded`
    ainda aparece no relatório, com o resto `None` — melhor que sumir de
    uma auditoria de memória.

O registry sabe se descrever sozinho, e sabe liberar o que envelheceu:

```python
from tempest_fastapi_sdk.genai import ModelRegistry

registry = ModelRegistry(max_models=3)
report = registry.inventory(probe=False)
freed = registry.unload_idle()
print(freed)
```

`unload_idle()` chama o `unload_if_idle()` de cada handle e devolve as
chaves que liberou. **As entradas continuam registradas** — um
`TextGenerator` que soltou os pesos ainda é o objeto certo pra entregar, e
ele recarrega no próximo uso. Para esquecer a entrada de vez, use `evict()`
/ `evict_all()`.

E por HTTP:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import ModelRegistry, make_genai_router

registry = ModelRegistry(max_models=3)
app = FastAPI()
app.include_router(make_genai_router(models=registry))
```

```bash
curl "http://127.0.0.1:8000/api/genai/models?probe=false"
```

O `probe=false` pula a leitura de NVML — a única parte do endpoint que custa
alguma coisa. Com o default, o relatório vem junto do retrato de memória do
host, então uma chamada responde "o que está carregado" e "quanto ainda
cabe" de uma vez.

### No Prometheus e no admin

O mesmo inventário alimenta as duas superfícies onde alguém já olha.

```python
from tempest_fastapi_sdk.genai import GenAIMetrics, ModelRegistry

registry = ModelRegistry(max_models=3)
metrics = GenAIMetrics()

def publish() -> None:
    metrics.observe_inventory(registry.inventory(probe=False))
```

Chame `publish()` numa tarefa periódica e o `/metrics` passa a expor
`genai_models_loaded{kind,device}`, `genai_models_known` e — quando o
relatório trouxe hardware — `genai_gpu_vram_free_bytes{index}`.

!!! note "Gauge é retrato, e por isso é limpo antes"
    Os contadores respondem "quanto trabalho passou"; estes respondem "o que
    está residente **agora**", que é a pergunta que explica um OOM. As séries
    com label são limpas a cada chamada: um modelo descarregado entre dois
    snapshots precisa **parar** de ser reportado, senão o valor velho fica
    parecendo residência.

No admin, os mesmos números viram cards:

```python
from tempest_fastapi_sdk.admin import AdminSite
from tempest_fastapi_sdk.genai import ModelRegistry, make_model_cards

registry = ModelRegistry(max_models=3)
site = AdminSite(dashboard_cards=make_model_cards(registry))
```

São três: **Models resident** (`2 of 5`), **Resident by device**
(`cuda: 2, cpu: 1`) e **VRAM free**. Só o último sonda o host, então passe
`include_vram=False` numa máquina sem GPU ou quando o dashboard não puder
ler NVML.

!!! tip "Os handles são lidos na hora de renderizar"
    Um registry vazio quando você monta o site continua reportando certo
    depois que os modelos carregam — os cards consultam o inventário a cada
    render, não na construção.

## Áudio (voz)

Interpretar e gerar voz no seu hardware — sem API externa. Requer o extra
`[genai-audio]` (faster-whisper + Coqui TTS); as engines importam
preguiçosamente.

### Interpretar áudio (STT)

`SpeechToText` transcreve com **faster-whisper** (Whisper via CTranslate2,
rápido em CPU/GPU). Carrega uma vez, roda em worker thread, serializa
chamadas por um semáforo.

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText("base", device="auto")     # tiny…large-v3


async def main() -> None:
    """Run this example."""
    result = await stt.transcribe("reuniao.wav")
    print(result.text, result.language, result.duration)
    for seg in result.segments:                    # timestamps por trecho
        print(seg.start, seg.end, seg.text)


asyncio.run(main())
```

Aceita caminho ou `bytes`. `device`/`compute_type` resolvem sozinhos
(`float16` na GPU, `int8` na CPU).

### Gerar voz (TTS)

`TextToSpeech` sintetiza com **Coqui TTS** (WAV). Mesma disciplina
(lazy + thread + semáforo).

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import TextToSpeech

tts = TextToSpeech("tts_models/multilingual/multi-dataset/xtts_v2")


async def main() -> None:
    """Run this example."""
    wav = await tts.synthesize("Olá, mundo.", language="pt")   # -> bytes WAV
    # clonagem de voz (XTTS): passe um clipe de referência
    wav = await tts.synthesize("Oi!", language="pt", speaker_wav="ref.wav")


asyncio.run(main())
```

`synthesize` devolve os `bytes` do WAV; passe `out_path=` pra também
gravar em disco.

### Idioma (PT-BR / EN-US)

Não precisa saber o código do Whisper nem escolher modelo TTS: use o enum
`Language`. Ele resolve o código (`pt`/`en`) pro STT e um modelo TTS bom
por idioma:

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import Language, SpeechToText, TextToSpeech


async def main() -> None:
    """Run this example."""
    # STT: força o idioma sem decorar o código
    await SpeechToText("base").transcribe("audio.wav", language=Language.PT_BR)

    # TTS: pega o modelo padrão do idioma automaticamente
    tts = TextToSpeech.for_language(Language.PT_BR)     # modelo pt-BR
    wav = await tts.synthesize("Olá, mundo.")


asyncio.run(main())
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

### Extrair o corpo das páginas

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
import asyncio

from tempest_fastapi_sdk.genai import Embedder, TextGenerator, TextModel
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

gen = TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)


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
  (sem infra nova): cria a tabela sob demanda, busca com o operador de
  distância cosseno `<=>`. Requer `[genai-rag]` + `CREATE EXTENSION vector`.

```python
from tempest_fastapi_sdk.genai import Embedder, EmbeddingModel
from tempest_fastapi_sdk.genai.rag import PgVectorStore, Retriever

from src.api.dependencies.resources import db

embedder = Embedder(EmbeddingModel.ALL_MINILM_L6_V2)


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
import asyncio

from tempest_fastapi_sdk.genai import GenerationConfig, TextGenerator

gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
config = GenerationConfig(max_new_tokens=512, temperature=0.2, top_p=0.9)


async def main() -> None:
    """Run this example."""
    await gen.generate("Explique PIX em uma frase.", config=config)
    await gen.chat([{"role": "user", "content": "Oi"}], config=config)


asyncio.run(main())
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
import asyncio

from pydantic import BaseModel
from tempest_fastapi_sdk.genai import OllamaGenerator


class Pessoa(BaseModel):
    nome: str
    idade: int


gen = OllamaGenerator("llama3.2")


async def main() -> None:
    """Run this example."""
    pessoa: Pessoa = await gen.generate_structured("Uma pessoa qualquer.", Pessoa)
    # -> Pessoa(nome="...", idade=...)


asyncio.run(main())
```

O `OllamaGenerator` manda o schema no campo `format` do daemon (o Ollama
garante JSON schema-válido nativamente) e faz o parse na saída — **é a
rota estruturada recomendada, sem biblioteca extra**.

!!! warning "Instrução longa vai em `system=`, não colada no documento"
    Passe a instrução em `generate_structured(documento, Schema,
    system="...")`. Instrução concatenada acima de um documento longo é
    ignorada: medido contra `gpt-oss:20b` lendo um edital de 24 mil
    caracteres, **0 itens** extraídos com a instrução no mesmo turno e
    **20 itens** com ela no turno `system`.

??? info "Por que a chamada vai em `/api/chat` e não em `/api/generate`"
    Num modelo de raciocínio (harmony, como o `gpt-oss`), o
    `/api/generate` **com** `format` responde `200 OK` com `eval_count`
    não-zero e `response` **vazio** — a resposta cai num canal de
    raciocínio que aquele endpoint não expõe. Sem `format` funciona; com
    `format`, não. O `/api/chat` devolve o JSON em `message.content`, e
    modelos sem raciocínio se comportam igual nos dois. Desde a v0.229.0
    a chamada usa `/api/chat`, e um conteúdo vazio levanta `ValueError`
    em vez de devolver nada.

!!! danger "Campo com `default` é campo que o modelo pode pular"
    O Pydantic deixa campo com default fora de `required` no JSON schema,
    e o decodificador constrangido do daemon então pode omiti-lo — e ele
    omite justamente os que têm default. Em schema de extração, **nenhum
    campo tem default**; a ausência se expressa no dado (`""`), nunca no
    schema.

!!! tip "Instrução separada do documento — `chat_structured`"
    Extraindo campos de um documento longo, a instrução precisa ir num
    turno `system` e o conteúdo num `user`. Concatenar tudo num prompt só
    degrada a aderência ao schema de forma **medida**: o modelo passa a
    "responder" trechos do documento.

    ```python
    import asyncio

    from pydantic import BaseModel
    from tempest_fastapi_sdk.genai import OllamaGenerator


    class NotaFiscal(BaseModel):
        numero: str
        total_centavos: int


    gen = OllamaGenerator("gpt-oss:20b")


    async def main() -> None:
        """Run this example."""
        nota: NotaFiscal = await gen.chat_structured(
            [
                {"role": "system", "content": "Extraia os campos da nota."},
                {"role": "user", "content": "NF-1 — total R$ 49,90"},
            ],
            NotaFiscal,
        )


    asyncio.run(main())
    ```

    O `format` vai no **top-level** do corpo, que é onde o daemon lê.
    Passar o schema como keyword do `chat()` cai em `options`, e o Ollama
    **ignora em silêncio**: volta `200 OK` com texto livre e o erro só
    aparece no `ValidationError` — ou num parse que por acaso funciona.
    Por isso `chat_structured` recusa um `format=` explícito.

!!! info "No backend local (transformers)"
    `TextGenerator.generate_structured(prompt, schema, constrained=True)`
    restringe a decodificação com o `lm-format-enforcer`
    (extra `[genai-structured]`), então o modelo só emite tokens que
    mantêm o JSON válido — o adapter é construído a partir do core estável
    da lib, então funciona no transformers 4.x **e 5.x** (validado no
    Qwen2.5-3B). O `constrained=False` continua disponível pra best-effort
    sem o extra.

!!! tip "Só o parse"
    `parse_structured(texto, schema)` extrai o JSON de uma saída crua
    (tolera cercas markdown e texto ao redor) e valida contra o schema —
    útil pra reaproveitar em qualquer saída de modelo.

### Moderação de conteúdo

Filtre prompts do usuário e saídas do modelo com uma camada de moderação
plugável. Dois backends satisfazem o `ModerationBackend` e devolvem um
`ModerationResult` (`flagged`, `categories`, `score`):

```python
import asyncio

from tempest_fastapi_sdk.genai import RuleModerator

user_input = "Explique PIX em uma frase."


mod = RuleModerator(["palavrão", "termo-proibido"], category="abuso")


async def main() -> None:
    """Run this example."""
    verdict = await mod.check(user_input)
    if verdict.flagged:
        ...   # bloqueie ou anote, conforme a política


asyncio.run(main())
```

`RuleModerator` é dep-free e previsível (block-list whole-word,
case-insensitive) — o piso determinístico. `ClassifierModerator` roda um
classificador local (ex. `unitary/toxic-bert`) via transformers (`[genai]`),
lazy, com `flagged_labels`/`threshold`. Qualidade PT-BR de modelos de
toxicidade varia — trate o classificador como best-effort e mantenha o
`RuleModerator` como base.
### Métricas de inferência (Prometheus)

`GenAIMetrics` empacota os contadores + histograma que todo serviço de
inferência acaba reimplementando — requests, latência e tokens in/out,
rotulados por modelo e operação. Reusa o `prometheus-client` (extra
`[prometheus]`) e aceita um `registry` explícito (compõe com o
`PrometheusMiddleware`/`/metrics` do SDK). É **opt-in**:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenAIMetrics, OllamaGenerator

metrics = GenAIMetrics()
gen = OllamaGenerator("llama3.2", metrics=metrics)


async def main() -> None:
    """Run this example."""
    await gen.generate("Explique PIX.")   # registra request + latência + tokens


asyncio.run(main())
```

`OllamaGenerator`, `TextGenerator` e `Embedder` aceitam `metrics=` e
registram request + latência (o Ollama também extrai
`prompt_eval_count`/`eval_count` da resposta pros contadores de token). Pra
qualquer outra chamada, envolva com o context manager e informe os tokens
que souber:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenAIMetrics

metrics = GenAIMetrics()


async def main() -> None:
    """Run this example."""
    async with metrics.track("meu-modelo", "generate") as span:
        span.tokens_out = 128
        ...  # roda o modelo


asyncio.run(main())
```

### Tracing distribuído (OpenTelemetry)

Além das métricas, as chamadas genai emitem **spans OpenTelemetry** — e sem
configuração por gerador. Chame `setup_tracing` uma vez no startup (extra
`[otel]`) e os spans passam a fluir junto com os do FastAPI/SQLAlchemy/httpx:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.api.tracing import setup_tracing

app: FastAPI = FastAPI()
setup_tracing(app, service_name="meu-servico", otlp_endpoint="localhost:4317")
```

A partir daí, `TextGenerator`/`OllamaGenerator` (`generate`/`chat`),
`Embedder.embed` e o `Retriever.search`/`.retrieve` do RAG abrem um span
automaticamente. Os spans seguem as **convenções semânticas GenAI** do
OpenTelemetry (`gen_ai.system`, `gen_ai.operation.name`, `gen_ai.request.model`
e, no caminho Ollama, `gen_ai.usage.input_tokens`/`gen_ai.usage.output_tokens`);
uma exceção marca o span como `ERROR` e a registra.

!!! info "Zero-config, zero-custo por padrão"
    É **ambiente**: reusa o `TracerProvider` global do `setup_tracing`. Sem o
    extra `[otel]` o helper vira no-op; com o extra mas sem provider
    configurado, o tracer global gera spans não-gravados. Nada é injetado nos
    geradores.

Pra instrumentar uma chamada própria, use o mesmo context manager:

```python
import asyncio

from tempest_fastapi_sdk.genai import genai_span


async def main() -> None:
    """Run this example."""
    async with genai_span("generate", "meu-modelo") as span:
        span.tokens_out = 128
        ...  # roda o modelo


asyncio.run(main())
```

### Contagem de tokens e janela de contexto

Pra caber um chat na janela do modelo, conte tokens com o **tokenizer do
próprio modelo** (nunca heurística — BPE e SentencePiece divergem) e dropе
os turnos mais antigos quando estoura:

```python
from tempest_fastapi_sdk.genai import (
    TextGenerator,
    TextModel,
    count_tokens,
    truncate_messages,
)

messages = [{"role": "user", "content": "oi"}]
tokenizer = gen.tokenizer
gen = TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)


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
import asyncio

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    InMemoryGenerationCache,
    OllamaGenerator,
)

gen = OllamaGenerator("llama3.2", generation_cache=InMemoryGenerationCache())
cfg = GenerationConfig(temperature=0)   # determinístico → cacheável


async def main() -> None:
    """Run this example."""
    await gen.generate("Explique PIX.", config=cfg)   # roda o modelo
    await gen.generate("Explique PIX.", config=cfg)   # servido do cache


asyncio.run(main())
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
import asyncio

from tempest_fastapi_sdk.genai import VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")


async def main() -> None:
    """Run this example."""
    descricao = await gen.generate(
        "USER: <image>\nDescreva a imagem.\nASSISTANT:",
        images=["foto.jpg"],
    )


asyncio.run(main())
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
import asyncio

from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.genai import Embedder, RedisEmbeddingCache

from src.core.settings import settings

redis = AsyncRedisManager(**settings.redis_kwargs())
# no lifespan: await redis.connect()  (antes de acessar .client)

cache = RedisEmbeddingCache(redis.client, ttl_seconds=86400)
embedder = Embedder("sentence-transformers/all-MiniLM-L6-v2", cache=cache)


async def main() -> None:
    """Run this example."""
    await embedder.embed(["texto"])  # 1ª vez calcula; próximos workers reaproveitam


asyncio.run(main())
```

O `Embedder` aguarda `get`/`set` quando o cache é assíncrono e chama
direto quando é síncrono — o mesmo código serve aos dois.

!!! note "Client Redis: `AsyncRedisManager`"
    O `RedisEmbeddingCache` recebe um `redis.asyncio.Redis` cru. Como o embedder
    roda em contexto async (serviço/RAG, não middleware), use o
    `AsyncRedisManager` (client gerenciado do SDK) e passe o `.client` **depois**
    do `await redis.connect()` no lifespan — antes disso, `.client` levanta
    `RuntimeError`. Precisa do extra `[cache]` (o pacote `redis`) além do `[genai]`.

## Quem falou o quê: diarização

Transcrever responde *o que foi dito*. Diarizar responde *quem disse* —
recorta a gravação em turnos e agrupa as vozes. Junto, dá a ata de uma
reunião ou o registro de uma ligação:

```python
# scripts/ata.py

import asyncio

from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeakerDiarizer,
    SpeechToText,
)


async def main() -> None:
    """Transcribe a two-party call, attributing each line."""
    transcriber = ConversationTranscriber(
        stt=SpeechToText(model_size="small"),
        diarizer=SpeakerDiarizer(num_speakers=2),
    )
    conversa = await transcriber.transcribe("ligacao.wav", language="pt")
    print(conversa.transcript())
    print(conversa.by_speaker())


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Falante 0: Bom dia, em que posso ajudar?
Falante 1: Queria saber sobre a segunda via do boleto.
```

!!! info "Extra necessário"
    ```bash
    uv add "tempest-fastapi-sdk[genai-audio,genai-diarization]"
    ```
    `[genai-audio]` traz o Whisper (transcrição), `[genai-diarization]`
    traz o `sherpa-onnx` (quem falou). Nenhum dos dois usa PyTorch.

### Por que sherpa-onnx e não pyannote

Medido, não suposto. O `pyannote.audio` 4.0.7 declara **21 dependências**
de runtime — `torch>=2.8`, `lightning`, `matplotlib`, três pacotes de
OpenTelemetry e um cliente da API paga do fornecedor — e o pipeline
pré-treinado dele é **gated** no HuggingFace: o build do container precisa
de token e de licença aceita na mão.

O `sherpa-onnx` declara **uma** dependência, roda em ONNX Runtime sem
PyTorch, e os modelos são abertos. Numa gravação de 57 s com 4 falantes
separou os quatro com **RTF 0,125** em CPU — oito vezes mais rápido que
tempo real.

### Os modelos não vêm no wheel

São 46 MB. Baixe uma vez, de preferência no build:

```python
from tempest_fastapi_sdk.genai.audio import ensure_models

ensure_models()  # honra TEMPEST_VOICE_MODEL_DIR
```

Deixar para a primeira requisição faz um usuário pagar o download dentro
do timeout dele.

### Quantos falantes? Ele descobre sozinho

Diarizar precisa responder duas perguntas, e só uma é fácil. *Onde cada turno
começa e termina* sai do modelo de segmentação. *Quantas vozes distintas
existem* não sai de modelo nenhum — precisa ser inferido de como os turnos se
agrupam, e é a parte que erra o transcrito de um jeito que ninguém percebe:
oito participantes numa ligação de duas pontas, ou quatro pessoas coladas numa
só.

O padrão é `num_speakers="auto"`. Medido num banco de doze gravações cuja
contagem está correta **por construção** — turnos recortados de gravações
distintas, logo pessoas distintas, em vez de saírem do próprio diarizador:

| método | exato | erro médio |
| --- | --- | --- |
| limiar 0,5 | 4/10 | 1,90 |
| limiar 0,7 | 8/10 | 0,40 |
| limiar 0,9 | 8/10 | 0,20 |
| **automático** | **12/12** | **0,00** |

O automático ganha por motivo estrutural, não por constante feliz: um limiar
pergunta *quão perto é perto o bastante*, resposta que muda com o microfone, o
idioma e a sala; o método espectral pergunta *onde essa matriz de afinidade se
divide naturalmente*, que é propriedade da própria gravação.

Custa uma segunda passada — um embedding por turno mais uma decomposição —,
desprezível perto da segmentação que produziu os turnos.

!!! tip "Sabe quantos são? Diga."
    ```python
    from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer

    diarizer = SpeakerDiarizer(num_speakers=2)
    ```
    Numa ligação de duas pontas você sabe. É exato e pula a segunda passada.
    `num_speakers=None` volta ao agrupamento só por limiar, que é a opção mais
    fraca e existe para quem quer o comportamento antigo.

    Quando a contagem varia por requisição, passe **por chamada** em vez de
    ajustar o objeto:

    ```python
    turns = await diarizer.diarize(audio, num_speakers=2)
    ```

    Um diarizador é construído uma vez e compartilhado por todas as
    requisições, então escrever nele afeta as que estão em voo: duas chamadas
    simultâneas pedindo 2 e 5 falantes viam ambas a última escrita, e quem
    pediu 2 recebia 5 sem erro nenhum. O argumento por chamada não tem esse
    problema; `ConversationTranscriber.transcribe(num_speakers=...)` repassa
    por esse caminho.

!!! warning "Monólogo era reportado como conversa"
    A busca pelo maior salto espectral **sempre** acha uma divisão, inclusive
    onde não há: um ditado real de seis turnos voltava como dois falantes.

    Uma voz só é *uniformemente* parecida consigo mesma — até o par de turnos
    mais distante dela está perto —, enquanto duas vozes produzem pares
    genuinamente longe. Medido nas doze gravações, o percentil 10 da
    similaridade ficou em 0,490–0,667 para um falante e −0,080–0,166 para mais
    de um; o veto fica no meio dessa folga.

    Esse número é propriedade da **escala de similaridade do modelo embarcado**,
    não constante universal: trocar de modelo exige remedi-lo.

### Como a atribuição funciona, e onde ela erra

A gravação é transcrita **uma vez**, não uma vez por turno: entregar
trechos de dois segundos ao Whisper joga fora o contexto que ele usa para
decidir pontuação e grafia, e custa uma inferência por turno. Depois cada
trecho transcrito vai para o turno com quem ele mais se sobrepõe no tempo.

O preço dessa escolha: um trecho do Whisper pode **atravessar** uma troca
de falante, e aí ele inteiro cai em quem detém a maior parte. Um turno que
o diarizador descartou por ser curto demais ainda transcreve — e esse
texto sai com `speaker = -1`, nunca some. Perder palavra caladamente é
pior do que admitir que não se sabe quem falou.

### Reconhecer quem é: perfis de voz

Diarização separa falante 0 de falante 1. Identificação diz que o falante 0
**é a Ana**, casando a voz com um perfil cadastrado.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import VoiceProfileService

from src.db.models import UserModel

VoiceProfileModel = make_voice_profile_model(user_table="users")
perfis = VoiceProfileService(profile_model=VoiceProfileModel)
```

Cadastro exige consentimento — não é opcional e não é configurável:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeechToText,
    VoiceProfileService,
)

from src.db.models import UserModel

perfis = VoiceProfileService(
    profile_model=make_voice_profile_model(user_table="users"),
)
transcriber = ConversationTranscriber(stt=SpeechToText())


async def cadastrar_voz(session: AsyncSession, usuario: UserModel) -> None:
    """Enrol a user's voice after they consented to it."""
    await perfis.enroll(
        session,
        user_id=usuario.id,
        audio="cadastro.wav",
        consent_reference="politica-biometria-v3",
        label="cadastro no onboarding",
    )
```

Depois, a transcrição já sai com nome:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeechToText,
    VoiceProfileService,
)

from src.db.models import UserModel

perfis = VoiceProfileService(
    profile_model=make_voice_profile_model(user_table="users"),
)
transcriber = ConversationTranscriber(stt=SpeechToText())


async def ata(session: AsyncSession, participantes: list[UserModel]) -> str:
    """Transcribe a meeting with each line attributed to a person."""
    conversa = await transcriber.transcribe(
        "reuniao.wav",
        identify_with=perfis,
        session=session,
        user_ids=[p.id for p in participantes],
    )
    return conversa.transcript()
```

!!! danger "Isso é dado biométrico"
    Impressão vocal identifica uma pessoa como um template de digital. Pela
    LGPD é **dado pessoal sensível** (Art. 5º, II), e o tratamento exige
    consentimento **específico e destacado** para essa finalidade (Art. 11, I)
    — termo de uso genérico não cobre.

    Por isso `consent_reference` é obrigatório e um valor em branco levanta
    `ConsentRequired`. O SDK guarda o vetor e o consentimento na mesma linha, e
    **nunca grava o áudio**: o vetor não se reproduz de volta em som, o que faz
    um vazamento dessa tabela custar muito menos que um vazamento das
    gravações.

    `forget_user()` existe como método, e não como exemplo na doc, porque
    "apague meus dados biométricos" é direito incondicional (Art. 18, VI) e não
    pode depender de cada projeto escrever o `WHERE` certo.

!!! tip "Restrinja aos participantes"
    `user_ids=[...]` transforma "quem no banco inteiro é essa voz" em "qual
    destas cinco pessoas é". Mais rápido, e muito menos sujeito a colocar o
    nome de um estranho numa linha.

Medido com vozes reais: cadastrando a partir de um turno e identificando
**outro** turno da mesma pessoa, a similaridade deu 0,687 e 0,734; um falante
não cadastrado voltou `None`. O limiar padrão é 0,5 — suba para qualquer coisa
que conceda acesso, porque aí o erro caro deixa de ser "não reconheceu" e passa
a ser "reconheceu errado".

Trocar o modelo de embedding invalida todo perfil já cadastrado: os vetores
deixam de ser comparáveis e as pessoas silenciosamente param de ser
reconhecidas. `stale_profiles()` encontra quem precisa recadastrar.

### Servindo por HTTP e pela linha de comando

```python
# src/api/app.py

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.genai.audio import make_voice_router

from src.api.dependencies.auth import current_user_id
from src.core.resources import db, perfis, transcriber


def create_app() -> FastAPI:
    """Mount the voice routes behind the service's own auth."""
    app = FastAPI()
    app.include_router(
        make_voice_router(
            session_factory=db.session_dependency,
            transcriber=transcriber,
            profiles=perfis,
            current_user_id=current_user_id,
            dependencies=[Depends(current_user_id)],
        ),
    )
    return app
```

Quatro rotas: `POST /voice/transcribe`, e `POST` / `GET` / `DELETE`
`/voice/profiles`.

!!! warning "`current_user_id` é obrigatório junto de `profiles`"
    Cadastrar ou apagar usando um id vindo do **corpo** da requisição deixaria
    qualquer um gravar biometria na conta alheia. O router recusa essa
    combinação na hora da montagem — falha em produção seria tarde demais.

As rotas de listar e apagar não são cortesia: são o direito da pessoa sobre o
próprio dado. E a listagem **não devolve o embedding** — ela precisa saber que
o perfil existe, não receber uma cópia do próprio template biométrico por HTTP.

Da linha de comando:

```bash
tempest voice models                        # baixa os modelos (faça no build)
tempest voice diarize reuniao.wav -n 2      # quem falou quando
tempest voice transcribe reuniao.wav -n 2   # quem disse o quê
```

`diarize` não carrega o Whisper — é o jeito rápido de conferir se a contagem de
falantes e o limiar estão certos antes de pagar pela transcrição.

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
