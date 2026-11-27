# Pipeline de chat com IA

Use para montar o chat completo (histórico, memória, ferramentas) com `AIChatPipeline`, o endpoint pronto `make_ai_chat_router` e streaming.

Parte da receita [IA generativa self-hosted](../genai.md).

Aqui as fatias anteriores se encaixam. Montar um chatbot "de verdade" —
memória, RAG por web, tool-calling, TTS opcional — normalmente significa
escrever (e manter) um microserviço de inferência inteiro. `AIChatPipeline`
faz isso **dentro do seu processo**: você injeta as peças que já viu
(`OllamaGenerator`, `ChatMemory`, `WebSearch`, `Tool`s) e chama `respond`.

!!! tip "Escolhendo o modelo de voz do chat"
    A licença dos pesos de TTS varia mais do que parece (F5-TTS e AudioLDM 2
    são não comerciais). Tabela medida e fontes em
    [Escolher modelo de voz e áudio](audio.md#escolher-modelo-de-voz-e-audio-licenca-e-onde-roda).

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
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
    web_search=WebSearch(SearxngBackend("http://localhost:8080", http_client=HTTPClient())),
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
    `RuleModerator`/`ClassifierModerator`) filtra o input **e cada turno do
    `history`** antes de gerar e a resposta depois — em `respond` e em
    `stream`; turno flagueado responde `blocked_message` (input
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

## Endpoint pronto: `make_ai_chat_router`

Um router, um backend de chat inteiro no processo:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    TextGenerator,
    TextModelEnum,
    make_ai_chat_router,
)

pipeline = AIChatPipeline(generator=TextGenerator(TextModelEnum.QWEN2_5_7B_INSTRUCT))


app = FastAPI()
app.include_router(make_ai_chat_router(pipeline))   # prefixo /api/ai-chat
```

Ele monta `POST /api/ai-chat/chat` (devolve `AIChatResult`) e
`POST /api/ai-chat/chat/stream` (tokens via SSE). O corpo é
`{"chat_id": ..., "content": ..., "history": [...]}` — sem `user_id`.

!!! note "O router é stateless"
    O histórico vive no corpo do request, não no servidor — cada chamada
    manda o `history`. Isso mantém o backend sem sessão (escala horizontal
    de graça) e a memória de longo prazo cuida do "lembrar" via
    `ChatMemory`.

### Com memória: o dono da conversa vem da sessão

Com `memory=` no pipeline, cada turno busca e indexa lembranças **do
usuário**. Quem é o usuário sai da sua dependência de autenticação, nunca do
corpo:

```python
# src/api/app.py

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    OllamaEmbedder,
    OllamaGenerator,
    make_ai_chat_router,
)
from tempest_fastapi_sdk.genai.rag import ChatMemory

from src.api.dependencies.auth import current_user_id

pipeline = AIChatPipeline(
    OllamaGenerator("llama3.2"),
    memory=ChatMemory(OllamaEmbedder("nomic-embed-text")),
)


def create_app() -> FastAPI:
    """Mount the AI chat behind the service's own auth."""
    app = FastAPI()
    app.include_router(
        make_ai_chat_router(
            pipeline,
            current_user_id=current_user_id,
            dependencies=[Depends(current_user_id)],
        ),
    )
    return app
```

!!! warning "`current_user_id` é obrigatório junto de `memory`"
    Buscar memória com um `user_id` vindo do **corpo** entregaria as
    conversas passadas de qualquer pessoa a quem soubesse o id dela — e o
    `AIChatResult` devolve os `memory_hits`. Por isso o router recusa a
    combinação na montagem (`ValueError`). Sem memória, `current_user_id` é
    opcional e o turno roda sem dono.

!!! info "O corpo não escolhe papel"
    `history[].role` aceita só `"user"` e `"assistant"`. Um turno
    `"system"` vindo do cliente sentaria no prompt com a autoridade do seu
    `base_system_prompt` — o corpo volta `422`. E, com `moderator=`, o
    conteúdo de cada turno do `history` passa pelo mesmo filtro da
    mensagem nova.

??? note "Migrando de uma versão que lia `user_id` do corpo"
    Até a 0.299.0 o `AIChatRequestSchema` tinha `user_id` e o router o
    repassava para `memory.search`. Agora:

    - o campo saiu do schema; cliente antigo que ainda manda `user_id` não
      quebra — o valor é ignorado;
    - pipeline **com** `memory=` precisa de `current_user_id=` em
      `make_ai_chat_router`, senão a montagem levanta `ValueError`;
    - `history` com `role` diferente de `user`/`assistant` passa a
      responder `422`;
    - chamando o pipeline direto, `user_id=None` pula a memória do turno
      (nem busca, nem indexa).

## Streaming

`stream` devolve os tokens conforme saem (modo prompt; resolve qualquer
tool-call **antes** de começar a emitir):

```python
import asyncio

from tempest_fastapi_sdk.genai import AIChatPipeline, TextGenerator, TextModelEnum

pipeline = AIChatPipeline(generator=TextGenerator(TextModelEnum.QWEN2_5_7B_INSTRUCT))


async def stream_demo() -> None:
    async for token in pipeline.stream(
        user_id="u1", chat_id="c1", content="Explique RAG em uma frase.",
    ):
        print(token, end="", flush=True)


asyncio.run(stream_demo())
```

!!! warning "Moderação no streaming: o que já saiu não volta"
    Com `moderator=`, o `stream` também filtra a resposta, e
    `stream_moderation=` no construtor escolhe como:

    - `"incremental"` (default) — antes de mandar cada pedaço, o texto
      gerado até ali (incluindo o pedaço) passa pelo moderador. Na primeira
      flag o stream para e o último pedaço é o `blocked_message`. Os pedaços
      anteriores **já foram entregues** e não há como recolhê-los; a
      garantia é que o pedaço que torna o texto flagueável nunca sai. Com
      `RuleModerator` e o block-list `["secret"]`, a resposta
      `"here is the SECRET plan"` emitida um caractere por pedaço sai como
      `"here is the SECRE"` seguido do
      `blocked_message` — o termo nunca sai inteiro, um prefixo dele pode
      sair. Custa uma chamada ao moderador por pedaço.
    - `"buffered"` — gera a resposta inteira, modera uma vez, e só então
      manda: ou a resposta inteira num pedaço só, ou o `blocked_message`.
      Nada vaza, mas o cliente espera a geração toda. É a escolha para um
      `ClassifierModerator`, que custaria uma inferência por pedaço no modo
      incremental.

    Resposta bloqueada não é indexada na memória, igual ao `respond`.

!!! tip "O microserviço de inferência vira uma escolha, não um requisito"
    Com o pipeline in-process, ter um serviço separado só pra LLM passa a
    ser uma decisão de organização (isolar a GPU, escalar à parte) — não
    uma obrigação arquitetural. O mesmo `TextBackend` deixa você trocar
    Ollama por vLLM/TGI depois sem mudar o call site.
