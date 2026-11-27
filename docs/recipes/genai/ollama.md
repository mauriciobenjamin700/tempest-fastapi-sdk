# Backend Ollama

Use para rodar LLM e embeddings pelo Ollama, sem torch: geração, RAG, busca híbrida, reranking e o mesmo router trocando torch por Ollama.

Parte da receita [IA generativa self-hosted](../genai.md).

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

!!! info "O Ollama também fala e transcreve"
    Desde a v0.40.11 o Ollama gera voz (`/v1/audio/speech`) e o `lfm3-audio`
    transcreve; o `OllamaSpeech` embrulha os dois. O que foi medido, os limites
    e as fontes estão em [Voz pelo Ollama](audio.md#voz-pelo-ollama).

## Gerar texto via Ollama

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

!!! warning "Timeout de leitura não é refeito"
    O `HTTPClient` que o gerador cria refaz erro de conexão e status
    retentável (`429`, `5xx`), mas **não** refaz `ReadTimeout`. Quando o
    cliente desiste de esperar, o daemon aborta a geração — no log do Ollama
    0.30.11 cada tentativa aparece como um `500` de exatamente o timeout —,
    então a nova tentativa recomeça do zero e estoura de novo. Medido com
    `qwen2.5:3b` em CPU (`options={"num_gpu": 0}`) e `timeout=10`: até a
    v0.303.1 a falha chegava em 31,5 s, depois de três abortos; agora chega
    em 10,0 s, com um (mediana de N=3 antes e N=5 depois). O mesmo vale para
    o `OllamaEmbedder` e para o `OpenAICompatGenerator`. Quem quer o retry de
    volta passa a política explícita, que é usada como veio:

    ```python
    from tempest_fastapi_sdk import RetryPolicy
    from tempest_fastapi_sdk.genai import OllamaGenerator

    gen: OllamaGenerator = OllamaGenerator(
        "llama3.2",
        retry_policy=RetryPolicy(retry_on_read_timeout=True),
    )
    ```

!!! info "`GenerationConfig` vira opções do Ollama"
    O mesmo `GenerationConfig` tipado funciona aqui — os campos são
    traduzidos pras opções do Ollama: `max_new_tokens`→`num_predict`,
    `repetition_penalty`→`repeat_penalty`, e `temperature`/`top_p`/`top_k`/
    `seed`/`stop` passam direto. `do_sample=False` vira `temperature=0`
    (geração greedy).

!!! warning "Erro do daemon levanta `OllamaError`"
    O Ollama reporta algumas falhas (modelo não baixado, runner que caiu no
    meio) como um corpo `{"error": "..."}` — num `200` ou como uma linha do
    stream NDJSON. `generate`, `chat`, `embed` e as variantes estruturadas
    levantam `OllamaError` (com `.model` e `.detail`) em vez de devolver
    `""`, e `stream` levanta na linha de erro em vez de terminar como se
    tivesse acabado — os pedaços já entregues continuam entregues.

`chat` segue o `generate` em cache e métricas: com `generation_cache=`, uma
chamada determinística repetida não vai ao daemon, e com `metrics=` ela é
registrada sob a operação `chat` com os tokens do Ollama. A chave do `chat`
é separada da do `generate`, então um prompt igual às mensagens serializadas
nunca colide.

### Janela de contexto: `num_ctx`

O Ollama processa no máximo `num_ctx` tokens de prompt, e o que passa disso
o daemon **corta sem avisar**: a resposta vem `200`, sem erro, gerada sobre
um pedaço do que você mandou (desde a 0.374.0 o SDK detecta o corte e loga um
`WARNING` — ver abaixo). Medido no Ollama 0.30.11 (`lfm2.5-thinking:1.2b`,
numa RTX 4070 Ti SUPER de 16 GB): um chat de 35 018 tokens voltou com
`prompt_eval_count` de 2 051 no default, e de 35 018 com `num_ctx=65536`.
Fixe a janela no gerador, e ela vai em toda chamada:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenerationConfig, OllamaGenerator

gen = OllamaGenerator(
    "llama3.2",
    num_ctx=32768,
    options={"num_thread": 8},
    config=GenerationConfig(max_new_tokens=512),
)


async def main() -> None:
    """Run this example."""
    resposta: str = await gen.chat([{"role": "user", "content": "Oi"}])
    print(resposta)


asyncio.run(main())
```

- **`num_ctx`** vira a opção `num_ctx` de toda requisição.
- **`options`** são opções cruas do Ollama (`num_thread`, `num_gpu`, …),
  também em toda requisição. `num_ctx` nos dois lugares levanta
  `ValueError`.
- **`config`** é o `GenerationConfig` padrão. O de cada chamada ganha campo a
  campo, e os `**kwargs` da chamada ganham de todos.

Isso importa sobretudo para quem **não** passa opção nenhuma por chamada — o
[laço do agente](../agents.md#o-modelo-roda-com-os-defaults-do-gerador) é
assim. A janela e o config padrão entram na chave do `generation_cache`, então
dois geradores com defaults diferentes não respondem um pelo cache do outro.

!!! warning "O corte agora deixa um `WARNING` no log"
    O daemon não avisa que cortou, mas deixa um rastro: o prompt truncado
    chega com `prompt_eval_count` em **metade da janela mais 2 ou 3
    tokens**, seja qual for o tamanho do que você mandou. Medido no Ollama
    0.30.11 em CPU (`qwen2.5:0.5b`, `num_gpu=0`, prompts de 1 000 a
    30 000 palavras, todos maiores que a janela): 1026 com `num_ctx` 2048,
    2050 no default de 4096, 4098 com 8192 e 8194 com 16384; a pesquisa de 2026-11-26/27 viu 2050
    (`qwen2.5:3b`, `qwen2.5-coder:14b`) e 2051 (`lfm2.5-thinking:1.2b`,
    `ministral-3:14b`) no default. Um prompt que cabe volta com o próprio
    tamanho (1243 com `num_ctx` 2048).

    Por isso o `OllamaGenerator` loga um `WARNING` no logger
    `tempest_fastapi_sdk.genai.ollama` quando o `prompt_eval_count` cai
    entre metade da janela e metade mais 16 (ou chega na janela inteira),
    com o modelo, a contagem e a janela no texto e em `extra`:

    ```text
    WARNING tempest_fastapi_sdk.genai.ollama: Ollama model 'qwen2.5:0.5b'
    evaluated 2050 prompt tokens with a 4096-token context window: an
    overflowing prompt is cut to about half the window without an error, so
    this prompt was likely truncated. Raise num_ctx on the generator.
    ```

    - A janela é o `num_ctx` da requisição quando houver (do construtor ou
      da chamada). Sem ele, é o `context_length` que o `/api/ps` do daemon
      informa para o modelo carregado — consultado uma vez por gerador, e
      só depois de uma resposta que traga `prompt_eval_count`. Se a consulta
      falhar, a checagem é pulada; a chamada nunca falha por causa dela.
    - Vale para `generate`, `chat`, `chat_with_tools` (o laço do `Agent`),
      `stream` e as duas chamadas estruturadas.
    - O default de `num_ctx` **não** mudou: continua `None`, herdando a
      janela do daemon (4096 sem `OLLAMA_CONTEXT_LENGTH`). Subir a janela
      custa VRAM, e isso é decisão de quem sobe o serviço; o aviso é o que
      torna o corte visível. Um prompt que caia exatamente na faixa sem ter
      sido cortado gera um aviso falso — raro, e o remédio é o mesmo.

### Falhar em vez de cortar: `truncate`

O aviso torna o corte visível, mas a chamada segue com o prompt cortado. Em
extração de documento, em agente com histórico longo ou em RAG com muitos
trechos, responder sobre metade do contexto é pior que um erro. Para esses
casos, passe `truncate=False`: o daemon recusa o prompt que não cabe, e o
`OllamaGenerator` levanta `OllamaContextExceededError`.

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaContextExceededError, OllamaGenerator

gen = OllamaGenerator("qwen2.5:0.5b", num_ctx=2048, truncate=False)


async def main() -> None:
    """Run this example."""
    documento: str = " ".join(f"palavra{i}" for i in range(4000))
    try:
        await gen.chat([{"role": "user", "content": documento}])
    except OllamaContextExceededError as exc:
        print(exc.prompt_tokens, exc.context_window)


asyncio.run(main())
```

O que o daemon responde foi medido no Ollama 0.30.11 em CPU
(`qwen2.5:0.5b`, `num_gpu=0`, `num_ctx` 2048, um prompt de 4 000 palavras),
no `/api/chat` e no `/api/generate`, com e sem `stream`:

| `truncate` no corpo | status | resultado |
| --- | --- | --- |
| ausente (o default) | `200` | prompt cortado: `prompt_eval_count` 1026 |
| `true` | `200` | igual ao ausente: `prompt_eval_count` 1026 |
| `false` | `400` | `exceed_context_size_error`, antes de qualquer linha do stream |

O corpo do `400` traz os dois tamanhos, e eles vão para a exceção:

```text
OllamaContextExceededError: Ollama model 'qwen2.5:0.5b' refused a prompt
longer than its context window (the prompt has 18923 tokens and the context
window 2048) because truncate=False; raise num_ctx or shorten the prompt
```

- **`truncate=None`** (o padrão) não manda o campo: o daemon corta, como
  antes, e o `WARNING` acima continua valendo.
- O `truncate` da chamada ganha do construtor, e vale nos mesmos seis
  métodos que o `think` (`generate`, `chat`, `chat_with_tools`, `stream`,
  `generate_structured`, `chat_structured`). Ele entra na chave do
  `generation_cache`: uma resposta guardada de um prompt cortado não
  responde uma chamada com `truncate=False`.
- `prompt_tokens` e `context_window` ficam `None` quando o corpo do erro não
  trouxer os tamanhos; a exceção continua sendo `OllamaContextExceededError`.
- No `stream`, a exceção sai na primeira iteração: o daemon recusa antes de
  mandar qualquer pedaço.
- Num prompt que cabe, `truncate=False` não muda nada (medido: `200`, com
  `prompt_eval_count` 32, nos dois endpoints).

### Raciocínio: `think`

Modelo de raciocínio (o `gpt-oss`, o `ornith`) aceita o campo `think` no
**topo** do corpo do `/api/chat` e do `/api/generate`: `True`/`False` liga
ou desliga o raciocínio, e `"low"`/`"medium"`/`"high"` escolhe o nível nos
modelos que têm níveis. Dentro de `options` o daemon ignora o campo — e era
para lá que ia um `think=` passado como keyword antes desta mudança. Agora ele
é um parâmetro do gerador (`ThinkLevel`) e de cada chamada:

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaGenerator

gen = OllamaGenerator("gpt-oss:20b", think="low", num_ctx=16384)


async def main() -> None:
    """Run this example."""
    rapida: str = await gen.chat([{"role": "user", "content": "Oi"}])
    pensada: str = await gen.chat(
        [{"role": "user", "content": "Quanto é 17 x 23?"}],
        think="high",
    )
    print(rapida, pensada)


asyncio.run(main())
```

- **`think=None`** (o padrão) não manda o campo: o modelo usa o default
  dele, como antes.
- O `think` da chamada ganha do construtor, e entra na chave do
  `generation_cache`.
- `generate`, `chat`, `chat_with_tools`, `stream`, `generate_structured` e
  `chat_structured` aceitam o parâmetro; o laço do `Agent` usa o do
  construtor.

Medido na pesquisa de LLMs locais de 2026-11-26/27 (Ollama 0.30.11, RTX
4070 Ti SUPER, temperatura 0):

| modelo / uso | `think` | acerto | p50 |
| --- | --- | --- | --- |
| `gpt-oss:20b`, 34 tarefas de tools | `"low"` | 97,1% | **1,44 s** |
| `gpt-oss:20b`, 34 tarefas de tools | default (medium) | 94,1% | 1,68 s |
| `gpt-oss:20b`, 34 tarefas de tools | `"high"` | 97,1% | 3,59 s |
| `gpt-oss:20b`, RAG com 27 perguntas | `"low"` | 20/21 fatos, 0/6 alucinações | **0,99 s** |
| `ornith-1.5:9b`, 33 extrações estruturadas | `False` | 93,3 por campo | **1,34 s** |
| `ornith-1.5:9b`, 33 extrações estruturadas | `True` | 95,0 por campo | 5,60 s |

Com N=34 e N=33, um ponto e pouco é um item: não leia ordem fina entre
`"low"` e `"high"`. O que a tabela mostra é o tempo — `"low"` empatou com
os outros níveis e, no p50 do relatório, levou de 25% a 40% do tempo do
`"high"` e de 47% a 86% do `"medium"` (o padrão), conforme o uso.

!!! warning "Modelo sem raciocínio recusa `think`"
    Modelo sem a capacidade `thinking` (no `/api/show`) responde `400` com
    `"<modelo>" does not support thinking` — reproduzido no Ollama 0.30.11
    com o `ministral-3:14b` e `think=True`, no `/api/chat` e no
    `/api/generate`, com e sem stream, sem carregar o modelo. O gerador
    levanta `OllamaThinkingUnsupportedError` (subclasse de `OllamaError`,
    com `.model` e `.detail`) em vez do `httpx.HTTPStatusError` cru. Para
    esse modelo, deixe `think=None`.

!!! danger "`think=False` faz alguns modelos ignorarem o `format`"
    Com `think=False`, o `ornith:35b` respondeu a um `chat_structured` em
    markdown (`**Nome:** Ana Costa ...`), com `200`, em 3 de 3 chamadas; com
    `think` omitido ou `True`, devolveu o objeto JSON em 3 de 3. O
    `ornith-1.5:9b`, o `gpt-oss:20b` e o `ministral-3:14b` respeitaram o
    `format` com `think=False` (3 de 3 cada). Quando a resposta estruturada
    não traz objeto JSON nenhum, `generate_structured` e `chat_structured`
    levantam `OllamaFormatIgnoredError` — subclasse de `OllamaError` **e**
    de `ValueError`, então o `except ValueError` de antes continua pegando —
    com `.content` (o começo da resposta) e `.think`, e a mensagem aponta o
    `think=False` quando foi ele. Antes, o mesmo caso virava um
    `ValueError("no JSON object ...")` sem pista da causa.

## Embeddings via Ollama + RAG

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

### Prefixos de tarefa do `nomic-embed-text`

O `nomic-embed-text` foi treinado com `search_query: ` na frente da pergunta e
`search_document: ` na frente do trecho. Declare os dois no `OllamaEmbedder` —
ou espalhe o preset `NOMIC_EMBED_TEXT_PREFIXES` — e o `Retriever` aplica cada
um do lado certo:

```python title="nomic_rag.py" hl_lines="11"
import asyncio

from tempest_fastapi_sdk.genai import NOMIC_EMBED_TEXT_PREFIXES, OllamaEmbedder
from tempest_fastapi_sdk.genai.rag import Chunk, InMemoryVectorStore, Retriever


async def main() -> None:
    """Index two passages and search them with nomic's task prefixes."""
    embedder = OllamaEmbedder(
        "nomic-embed-text",
        **NOMIC_EMBED_TEXT_PREFIXES,
    )
    rag = Retriever(embedder, InMemoryVectorStore())
    await rag.index(
        [
            Chunk(text="O reembolso cai em até 7 dias úteis.", source="faq", index=0),
            Chunk(text="A entrega expressa chega amanhã.", source="faq", index=1),
        ],
    )
    hits = await rag.search("Quanto tempo demora o reembolso?", top_k=1)
    print(hits[0].text)
    await embedder.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

`Retriever.index` chama `embed_passages`, que envia `search_document: <trecho>`
ao daemon; `Retriever.search` chama `embed_query`, que envia
`search_query: <pergunta>`. O `embed()` puro continua sem prefixo, e o default
dos dois parâmetros é `""` — quem não declara nada mantém os vetores de antes.

O preset é escolhido por você, não adivinhado pelo nome do modelo: a mesma
família sai com várias tags (`nomic-embed-text`, `nomic-embed-text:v1.5`, um
fine-tune local), e um palpite errado mudaria todo vetor gravado em silêncio.
Para a família E5 existe `MULTILINGUAL_E5_PREFIXES` (`query: ` / `passage: `),
que serve também no `Embedder` do torch. Outro modelo: passe
`query_prefix=` / `passage_prefix=` com as strings do model card dele.

Quanto vale: numa medição de 21 perguntas em PT-BR sobre um manual fictício
(`nomic-embed-text` via Ollama, um trecho de 2000 caracteres por documento),
o documento certo apareceu entre os 4 primeiros em **18/21** sem prefixo e
**20/21** com prefixo; em k=1 e k=2 não mudou (15/21 e 17/21). Com trechos de
300 caracteres o prefixo não mudou nada (18/21 nos dois). N=21 é pouco: é um
sinal, não uma taxa.

!!! warning "Trocar o prefixo exige reindexar"
    O prefixo entra no texto que vira vetor, então ligar, desligar ou trocar
    `query_prefix` / `passage_prefix` muda **todos** os vetores. Um índice
    gravado sem prefixo e consultado com prefixo (ou o contrário) compara
    vetores de espaços diferentes e devolve resultado pior sem erro nenhum.
    Ao mudar os prefixos, apague o índice e rode `Retriever.index` de novo
    sobre o corpus inteiro.

## Busca híbrida (BM25 + denso)

A busca densa capta significado mas erra termos exatos — nomes próprios,
códigos, siglas que a query compartilha literalmente com o chunk. O BM25
(esparso) acerta esses e ignora semântica. O `HybridRetriever` roda os
dois sobre os mesmos chunks indexados e funde os rankings com **Reciprocal
Rank Fusion** — então "o que o BACEN faz?" acha o chunk que diz "BACEN"
mesmo com score denso morno. BM25 vem do `rank-bm25` (extra `[genai-rag]`).

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import (
    HybridRetriever,
    InMemoryVectorStore,
    chunk_text,
)

rag = HybridRetriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
)


async def main() -> None:
    """Run this example."""
    chunks = chunk_text(
        "O CNPJ identifica a pessoa jurídica na Receita Federal.",
        source="faq.md",
    )
    await rag.index(chunks)                            # indexa denso + BM25
    hits = await rag.search("o que é CNPJ?", top_k=5)  # funde denso + esparso
    print(hits)


asyncio.run(main())
```

`search(query, top_k, candidates)` pega `candidates` de cada lado e funde
pra `top_k`. `reciprocal_rank_fusion(rankings, k=60)` está exposto avulso
pra fundir rankings arbitrários. O índice BM25 é in-memory (reconstruído a
cada `index`) — bom até dezenas de milhares de chunks. Tem também
`retrieve(query, top_k)` (busca híbrida → bloco de contexto), então o
`HybridRetriever` satisfaz `SupportsRetrieve` e entra no
`make_genai_router(retriever=...)` no lugar do `Retriever`.

!!! info "Cada `index` substitui as fontes que ele cita"
    Um chunk é identificado por `(source, index, text)` — não só por
    `(source, index)`, que o `chunk_text` repete a cada chamada (ele
    recomeça o `index` em 0). E cada `index(chunks)` **substitui** tudo que
    já estava indexado para as `source`s do lote: reindexar um documento
    editado não deixa a versão antiga, nem uma cauda dela, respondendo
    busca. Por isso, passe **todos** os chunks de uma fonte na mesma
    chamada. `InMemoryVectorStore`, `PgVectorStore` e `ChromaVectorStore`
    aplicam a mesma troca do lado denso; um `VectorStore` seu que só
    acrescenta continua devolvendo as linhas antigas, e o `HybridRetriever`
    as descarta antes da fusão (elas só gastam vagas de `candidates`).

    Do lado esparso, só entram na fusão os chunks que compartilham ao menos
    um termo com a query. Sem sobreposição o BM25 dá 0 para todos, e uma
    lista de zeros "ordenada por score" é só a ordem de inserção — somada
    no RRF, invertia o ranking denso.

## Reranking (cross-encoder)

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

## Mesmo router, torch OU Ollama

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
