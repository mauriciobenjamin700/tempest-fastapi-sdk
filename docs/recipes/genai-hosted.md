# IA generativa hospedada (Anthropic, OpenAI, DeepSeek, Google)

A base de IA do SDK roda modelo local — `TextGenerator` (transformers) ou
`OllamaGenerator` (daemon). Às vezes a conta que importa não é a de RAM, é a
de **custo por token**, ou você precisa de um modelo que não cabe na sua
máquina. Para isso existem os backends hospedados:

| Provedor | Backend | Geração + stream | Tool calling | Saída estruturada | Embeddings |
| --- | --- | --- | --- | --- | --- |
| Anthropic | `AnthropicGenerator` | sim | sim | sim (`json_schema`) | — |
| OpenAI | `OpenAICompatGenerator` | sim | sim | sim (`json_schema`) | `OpenAICompatEmbedder` |
| DeepSeek | `OpenAICompatGenerator` | sim | sim | sim (`json_object`) | — |
| Google (Gemini) | `OpenAICompatGenerator` | sim, pelo endpoint compatível (não testado) | sim, idem | sim (`json_schema`) | — |

A ideia é uma só: eles cumprem **os mesmos protocolos** que o Ollama — o
`TextBackend` (`generate` / `chat` / `stream`), o `StructuredTextBackend`
(`chat_structured`), o `ToolCallingBackend` do agente (`chat_with_tools`) e o
`SupportsEmbed` do RAG. Você troca a linha que constrói o backend e o resto do
serviço não muda.

!!! info "Instalação"

    ```bash
    uv add "tempest-fastapi-sdk[genai-hosted]"
    ```

    O extra só puxa `httpx`, que já é dependência base — ele existe para o
    serviço declarar a capacidade que usa. Nenhum SDK de fornecedor
    (`anthropic`, `openai`) é instalado.

## Primeiro passo: as chaves nas settings

Componha o mixin `HostedGenAISettings` nas settings do serviço:

```python
from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings


class Settings(HostedGenAISettings, BaseAppSettings):
    """Settings do serviço."""


settings: Settings = Settings()
```

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
OPENAI_MODEL=<id do modelo OpenAI>
OPENAI_STT_MODEL=<id do modelo de transcrição>
DEEPSEEK_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
GOOGLE_MODEL=<id do modelo Gemini>
```

Cada provedor tem `*_API_KEY`, `*_BASE_URL` e `*_MODEL`. Os defaults:

| Variável | Default |
| --- | --- |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | vazio — obrigatório |
| `OPENAI_STT_MODEL` | vazio — obrigatório em `openai_speech_kwargs()` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-flash` |
| `GOOGLE_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `GOOGLE_MODEL` | vazio — obrigatório em `gemini_kwargs()` |

!!! note "De onde vêm os defaults"

    `claude-opus-5-5` é o modelo que a tabela de modelos da Anthropic
    recomenda como ponto de partida
    (<https://platform.claude.com/docs/en/models/overview>). `deepseek-flash`
    é um dos dois valores que a referência do DeepSeek aceita no campo
    `model` (<https://api-docs.deepseek.com/api/create-chat-completion>).
    Para a OpenAI o SDK não fixa nenhum id: `openai_kwargs()` levanta
    `ValueError` enquanto `OPENAI_MODEL` estiver vazio.

## Gerar texto

Os `*_kwargs()` devolvem exatamente o que o construtor recebe, então montar o
backend é um splat:

```python
import asyncio

from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings
from tempest_fastapi_sdk.genai import AnthropicGenerator, OpenAICompatGenerator


class Settings(HostedGenAISettings, BaseAppSettings):
    """Settings do serviço."""


settings: Settings = Settings(ANTHROPIC_API_KEY="sk-ant-...", DEEPSEEK_API_KEY="sk-...")

claude: AnthropicGenerator = AnthropicGenerator(**settings.anthropic_kwargs())
deepseek: OpenAICompatGenerator = OpenAICompatGenerator(**settings.deepseek_kwargs())


async def main() -> None:
    """Pergunta a mesma coisa aos dois provedores."""
    print(await claude.generate("Explique PIX em uma frase."))
    print(await deepseek.generate("Explique PIX em uma frase."))
    async for pedaco in claude.stream("Conte até cinco."):
        print(pedaco, end="", flush=True)


asyncio.run(main())
```

Pedaço por pedaço:

- `AnthropicGenerator` fala a Messages API: `POST {base_url}/v1/messages` com
  os headers `x-api-key` e `anthropic-version`
  (<https://platform.claude.com/docs/en/api/overview>). Turnos `system`
  viram o campo `system` de topo, que é onde essa API os lê.
- `OpenAICompatGenerator` fala `/chat/completions`. O `deepseek_kwargs()`
  aponta para `https://api.deepseek.com`; o formato é o mesmo, segundo
  <https://api-docs.deepseek.com/>.
- `stream()` lê os eventos SSE de cada provedor e devolve só o texto.

Chave vazia levanta `ValueError` **na construção**, não um 401 lá dentro do
primeiro job em background.

!!! warning "`max_tokens` na Anthropic"

    A Messages API exige `max_tokens` em toda chamada
    (<https://platform.claude.com/docs/en/api/messages>). O
    `AnthropicGenerator` manda `16000` quando a chamada não define
    `max_new_tokens`; ajuste com `max_tokens=` no construtor ou com
    `GenerationConfig(max_new_tokens=...)`.

## O mesmo código, qualquer backend

É aqui que a troca fica de graça. Escreva o consumidor contra o protocolo, não
contra o provedor:

```python
import asyncio

from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    OllamaGenerator,
    OpenAICompatGenerator,
    TextBackend,
)


async def resumir(backend: TextBackend, texto: str) -> str:
    """Resume um texto com qualquer backend."""
    return await backend.chat(
        [
            {"role": "system", "content": "Resuma em uma frase."},
            {"role": "user", "content": texto},
        ],
    )


async def main() -> None:
    """Roda o mesmo resumo em três backends."""
    backends: list[TextBackend] = [
        OllamaGenerator("llama3.2"),
        AnthropicGenerator(api_key="sk-ant-..."),
        OpenAICompatGenerator(
            "deepseek-flash",
            api_key="sk-...",
            base_url="https://api.deepseek.com",
        ),
    ]
    for backend in backends:
        print(await resumir(backend, "O PIX é o pagamento instantâneo do BC."))


asyncio.run(main())
```

O `make_genai_router`, o `AIChatPipeline` e o `Agent` recebem o backend do
mesmo jeito — passe o hospedado onde passaria o `OllamaGenerator`.

## Agente com ferramentas

O `Agent` chama `chat_with_tools(messages, tools)` e lê `tool_calls` no
formato OpenAI/Ollama. O `AnthropicGenerator` traduz nos dois sentidos: as
ferramentas saem como `{name, description, input_schema}`, os blocos
`tool_use` voltam como `tool_calls` (com o `id` preservado), e os resultados
`role: tool` voltam para a API como blocos `tool_result` num único turno
`user`, como o guia de tool use pede
(<https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>).

```python
import asyncio
from typing import Any

from tempest_fastapi_sdk.agents import Agent, AgentContext, AgentTool
from tempest_fastapi_sdk.genai import AnthropicGenerator


async def somar(arguments: dict[str, Any], context: AgentContext) -> str:
    """Soma dois inteiros."""
    return str(int(arguments["a"]) + int(arguments["b"]))


somar_tool: AgentTool = AgentTool(
    name="somar",
    description="Soma dois inteiros.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
    handler=somar,
)


async def main() -> None:
    """Roda um agente sobre o Claude."""
    agent: Agent = Agent(AnthropicGenerator(api_key="sk-ant-..."), tools=[somar_tool])
    run = await agent.run("Quanto é 2 + 3?")
    print(run.stop_reason, run.output)


asyncio.run(main())
```

Trocar `AnthropicGenerator(...)` por `OpenAICompatGenerator(...)` ou
`OllamaGenerator(...)` não muda mais nada no exemplo.

## Saída estruturada

`chat_structured(messages, schema)` devolve uma instância validada do seu
modelo Pydantic:

```python
import asyncio

from pydantic import BaseModel

from tempest_fastapi_sdk.genai import AnthropicGenerator, StructuredTextBackend


class Fatura(BaseModel):
    """Campos que queremos extrair."""

    numero: str
    total_centavos: int


async def ler(backend: StructuredTextBackend, documento: str) -> Fatura:
    """Extrai a fatura de um documento."""
    return await backend.chat_structured(
        [
            {"role": "system", "content": "Extraia os campos da fatura."},
            {"role": "user", "content": documento},
        ],
        Fatura,
    )


async def main() -> None:
    """Extrai com o Claude."""
    print(await ler(AnthropicGenerator(api_key="sk-ant-..."), "Fatura A-1, R$ 5,00"))


asyncio.run(main())
```

Cada provedor pede JSON de um jeito, e o backend cuida disso:

- **Anthropic** — `output_config.format` com `type: json_schema`. A API exige
  `additionalProperties: false` em todo objeto e recusa limites numéricos e
  de tamanho de string
  (<https://platform.claude.com/docs/en/build-with-claude/structured-outputs>);
  o schema enviado é adaptado, e a resposta é validada contra o **seu**
  modelo, com os limites originais.
- **OpenAI** — `response_format` com `type: json_schema`.
- **DeepSeek** — `response_format` com `type: json_object`, o único modo JSON
  documentado (<https://api-docs.deepseek.com/guides/json_mode>); o schema
  vai num turno `system` no fim da conversa, que também cumpre a exigência
  da palavra "json" no prompt.

## Resultado completo, imagem e provedor pelo id do modelo

`chat_structured` responde uma pergunta só — "me dê a instância" — e levanta
em qualquer outro desfecho. Quem paga cada chamada precisa de mais três
respostas, **mesmo quando não há instância**: quanto a chamada custou, por que
o modelo parou e o que ele escreveu. Uma recusa, uma resposta cortada em
`max_tokens` ou um JSON fora do schema também foram cobrados.

`complete_structured` devolve tudo isso num `StructuredCompletion`, e
`hosted_generator` escolhe o provedor pelo id do modelo:

```python
import asyncio
from pathlib import Path

from pydantic import BaseModel

from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings
from tempest_fastapi_sdk.genai import (
    StructuredCompletion,
    StructuredCompletionBackend,
    hosted_generator,
    image_part,
)


class Settings(HostedGenAISettings, BaseAppSettings):
    """Settings do serviço."""

    AI_MODEL: str = "claude-haiku-5-5"


class Transcricao(BaseModel):
    """O texto lido da foto."""

    legivel: bool
    texto: str


async def transcrever(
    backend: StructuredCompletionBackend,
    foto: bytes,
) -> StructuredCompletion[Transcricao]:
    """Lê o texto de uma foto de caderno."""
    return await backend.complete_structured(
        [
            {"role": "system", "content": "Transcreva o texto da foto."},
            {
                "role": "user",
                "content": [
                    image_part(foto, "image/jpeg"),
                    {"type": "text", "text": "Transcreva."},
                ],
            },
        ],
        Transcricao,
        effort="low",
        cache_system=True,
        max_tokens=2048,
    )


async def main() -> None:
    """Transcreve com o modelo configurado."""
    settings = Settings()
    backend = hosted_generator(**settings.hosted_kwargs(settings.AI_MODEL))
    resultado = await transcrever(backend, Path("caderno.jpg").read_bytes())
    if resultado.output is not None:
        print(resultado.output.texto)
    else:
        print("sem resultado:", resultado.stop_reason, resultado.error)
    print("tokens:", resultado.usage)


asyncio.run(main())
```

Pedaço por pedaço:

- **`hosted_generator(model, api_key=...)`** lê o provedor do prefixo do id
  (`claude-` → Anthropic, `gpt-`/`o1`/`o3`/`o4`/`chatgpt-` → OpenAI,
  `gemini-` → Google, `deepseek-` → DeepSeek; a tabela é `MODEL_PREFIXES`) e
  monta o backend com o que cada provedor documenta: o campo do orçamento de
  saída (`max_completion_tokens` na OpenAI), o modo JSON (`json_object` no
  DeepSeek) e os valores de esforço. Id fora da tabela (fine-tune, servidor
  próprio) levanta `ValueError`: passe `provider=` e, se for o caso,
  `base_url=`.
- **`settings.hosted_kwargs(model)`** devolve exatamente os argumentos de
  `hosted_generator` — o modelo, e a chave e a URL **do provedor resolvido**.
  Trocar de provedor vira trocar `AI_MODEL` no `.env` (e ter a chave daquele
  provedor lá).
- **`image_part(dados, media_type)`** monta a parte de imagem no formato
  OpenAI, que é o formato de mensagem do SDK. O `AnthropicGenerator` a traduz
  para o bloco `image` da Messages API; o `OpenAICompatGenerator` a repassa
  como está.
- **`effort`** vai como `output_config.effort` na Anthropic e como
  `reasoning_effort` no formato OpenAI. O Gemini documenta `minimal`, `low`,
  `medium` e `high` (<https://ai.google.dev/gemini-api/docs/openai>), então
  `xhigh` e `max` viram `high` (`GOOGLE_EFFORT_VALUES`). O DeepSeek não
  documenta o campo, e nem o `hosted_generator` nem o `deepseek_kwargs()` o
  enviam (`effort_field=None`).
- **`cache_system=True`** marca o turno `system` com
  `cache_control: {"type": "ephemeral"}` na Anthropic. No formato OpenAI não
  há marcador por mensagem, e o parâmetro é aceito e ignorado.

O `StructuredCompletion` que volta:

| Campo | O que é |
| --- | --- |
| `output` | a instância validada, ou `None` |
| `text` | a resposta como chegou |
| `usage` | o `TokenUsage` informado pelo provedor, **em qualquer desfecho**; `None` quando a resposta não traz `usage` |
| `stop_reason` | `end`, `max_tokens`, `refusal`, `tool_use` ou `other` |
| `provider_stop_reason` | o valor original (`end_turn`, `length`...) |
| `error` | por que `output` é `None` depois de uma parada natural |

!!! tip "Só a parada natural é parseada"

    Uma resposta cortada em `max_tokens` pode validar por acaso quando os
    últimos campos do schema têm default. Por isso o parse só acontece com
    `stop_reason == "end"`; nos outros casos `output` é `None` e `error`
    fica vazio — o `stop_reason` já diz o motivo.

!!! note "Cache gravado e cache lido"

    `TokenUsage.cache_write_tokens` é a parte da entrada gravada no cache
    (`cache_creation_input_tokens` da Anthropic), que a Anthropic cobra acima
    do preço base; `cache_hit_tokens` é a parte lida. As duas já estão
    dentro de `input_tokens`. O formato OpenAI não informa gravação, e o
    campo fica `0`.

Os testes desta superfície rodam sobre `httpx.MockTransport`: eles fixam o
request que cada backend monta e a leitura da resposta documentada, não o
comportamento de um provedor ao vivo.

## Um provedor escolhido no `.env`

Ler o provedor do prefixo do id é cômodo, mas deixa uma decisão de negócio
escondida no nome de um modelo, e cada provedor pede a sua variável de chave.
Quando o serviço manda **todos** os modelos para **um** provedor, diga isso
no `.env`, com provedor, chave e (se quiser) a URL:

```bash
# .env
GENAI_PROVIDER=openai
GENAI_API_KEY=sk-...
# GENAI_BASE_URL=https://gateway.interno/v1   # vazio usa a URL do provedor
AI_MODEL_FREE=gpt-5-mini
AI_MODEL_FAMILY=gpt-5
```

```python
from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings
from tempest_fastapi_sdk.genai import TextBackend, hosted_generator


class Settings(HostedGenAISettings, BaseAppSettings):
    """Settings do serviço: os dois modelos vêm do mesmo provedor."""

    AI_MODEL_FREE: str = "gpt-5-mini"
    AI_MODEL_FAMILY: str = "gpt-5"


def backend_do_plano(settings: Settings, familia: bool) -> TextBackend:
    """Monta o gerador do plano com o provedor do `.env`."""
    modelo = settings.AI_MODEL_FAMILY if familia else settings.AI_MODEL_FREE
    return hosted_generator(**settings.hosted_kwargs(modelo))


settings = Settings(GENAI_PROVIDER="openai", GENAI_API_KEY="sk-...")
print(settings.hosted_kwargs(settings.AI_MODEL_FREE)["provider"])  # openai
```

Com `GENAI_PROVIDER` preenchido, o `hosted_kwargs` muda de regra:

- **O provedor é o do `.env`**, para todo modelo. Id com prefixo de outro
  provedor (`claude-...` com `GENAI_PROVIDER=openai`) levanta `ValueError`
  em vez de trocar de provedor em silêncio; id sem prefixo conhecido
  (fine-tune, modelo atrás de um servidor compatível com OpenAI) vai para o
  provedor escolhido.
- **A chave é `GENAI_API_KEY`**; vazia, cai na chave própria do provedor
  (`OPENAI_API_KEY`...). A URL é `GENAI_BASE_URL` ou, vazia, a do provedor.
- **`provider=`** que discorda do `.env` levanta `ValueError`: um serviço
  conversa com um provedor.
- **Sem `GENAI_PROVIDER`** (ausente ou vazio), nada muda: o provedor sai do
  prefixo, como na seção acima.

!!! check "O boot de produção cobra a chave"

    Com `EnvironmentSettings` composto e `ENV=production`, `GENAI_PROVIDER`
    preenchido sem chave efetiva (nem `GENAI_API_KEY`, nem a do provedor)
    derruba o boot listando `GENAI_API_KEY`. Serviço que não define
    `GENAI_PROVIDER` não é cobrado: as chaves por provedor continuam
    opcionais.

## PDF: `document_part` e `supports_documents`

Imagem você já manda com `image_part`. PDF tem o mesmo problema, com um
agravante: cada provedor escreve a parte de documento de um jeito, e nem todo
provedor aceita uma. Escrever o bloco à mão (o `document` da Anthropic, por
exemplo) prende o serviço a um provedor — trocar `GENAI_PROVIDER` passa a
quebrar só o fluxo de PDF, e só em produção.

`document_part` monta a parte uma vez, e cada gerador a traduz ou recusa:

```python
import asyncio
from pathlib import Path

from pydantic import BaseModel

from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings
from tempest_fastapi_sdk.genai import (
    StructuredCompletion,
    StructuredCompletionBackend,
    document_part,
    hosted_generator,
    supports_documents,
)


class Settings(HostedGenAISettings, BaseAppSettings):
    """Settings do serviço."""

    AI_MODEL: str = "claude-haiku-5-5"


class Agenda(BaseModel):
    """Os eventos lidos do comunicado."""

    eventos: list[str]


async def ler_agenda(
    backend: StructuredCompletionBackend,
    pdf: bytes,
) -> StructuredCompletion[Agenda]:
    """Extrai os eventos de um comunicado em PDF."""
    return await backend.complete_structured(
        [
            {
                "role": "user",
                "content": [
                    document_part(pdf, filename="comunicado.pdf"),
                    {"type": "text", "text": "Liste os eventos."},
                ],
            },
        ],
        Agenda,
        max_tokens=2048,
    )


async def main() -> None:
    """Confere o provedor no boot e lê o PDF."""
    settings = Settings()
    kwargs = settings.hosted_kwargs(settings.AI_MODEL)
    if not supports_documents(kwargs["provider"]):
        raise SystemExit(f"{kwargs['provider']} não documenta entrada de PDF")
    backend = hosted_generator(**kwargs)
    resultado = await ler_agenda(backend, Path("comunicado.pdf").read_bytes())
    print(resultado.output)


asyncio.run(main())
```

Pedaço por pedaço:

- **`document_part(dados, media_type="application/pdf", *, filename=None)`**
  monta a parte `file` do Chat Completions da OpenAI: `file_data` é uma data
  URL base64 e `filename` vai junto (`document.pdf` quando você não passa um,
  porque todo exemplo base64 do guia da OpenAI manda um nome). Só
  `application/pdf` é aceito; outro tipo, ou bytes vazios, levanta
  `ValueError` na hora. Texto puro vai numa parte `text`.
- **Anthropic:** o `AnthropicGenerator` traduz a parte para um bloco
  `document` com `source` base64, e o `filename` vira o `title` do bloco.
- **OpenAI:** o `OpenAICompatGenerator` repassa a parte como está.
- **Google e DeepSeek:** o gerador que o `hosted_generator` monta para eles
  (e o que `gemini_kwargs()` / `deepseek_kwargs()` montam) levanta
  `ValueError` **antes** de abrir a conexão quando uma mensagem carrega a
  parte, com uma mensagem que nomeia o provedor. Texto e imagem continuam
  saindo normalmente.
- **`supports_documents(provider)`** responde a mesma pergunta sem chamar
  nada: `True` para `anthropic` e `openai`, `False` para `google`,
  `deepseek`, `None` e nome desconhecido. É o que o serviço checa no boot, ou
  antes de oferecer a rota de PDF. A lista está em `DOCUMENT_PROVIDERS`.

De onde saiu cada linha da tabela, lida em 2026-11-04:

| Provedor | O que a documentação diz | Resultado no SDK |
| --- | --- | --- |
| Anthropic | bloco `document` com `source` base64 `application/pdf`, segundo a documentação da Anthropic (<https://platform.claude.com/docs/en/build-with-claude/pdf-support>) | traduz |
| OpenAI | "Chat Completions accepts only PDF files as `file` content parts", com `file_data` em data URL, segundo a documentação da OpenAI (<https://developers.openai.com/api/docs/guides/pdf-files>) | repassa |
| Google | a página do endpoint compatível (<https://ai.google.dev/gemini-api/docs/openai>) documenta imagem e áudio, e nenhuma parte `file` | `ValueError` |
| DeepSeek | a referência do chat (<https://api-docs.deepseek.com/api/create-chat-completion>) lista uma parte `file`, mas descreve `file_data` como data URL "of the image" e não cita PDF | `ValueError` |

!!! warning "Testado o request, não a resposta"

    Os testes desta parte rodam sobre `httpx.MockTransport`: fixam o corpo
    que cada gerador monta e o `ValueError` sem nenhum request. Nenhum
    provedor foi chamado de verdade — que a Anthropic e a OpenAI leiam o PDF
    é o que a documentação delas diz, não algo que o SDK mediu. Se o Google
    ou o DeepSeek passarem a documentar PDF, a mudança é uma linha em
    `DOCUMENT_PROVIDERS`.

!!! tip "Servidor compatível genérico"

    `OpenAICompatGenerator` construído à mão (vLLM, um gateway próprio)
    repassa a parte por padrão (`accepts_documents=True`): o SDK não sabe o
    que aquele servidor aceita, e quem responde é ele. Passe
    `accepts_documents=False` para recusar antes da chamada.

## Embeddings (OpenAI)

`OpenAICompatEmbedder` cumpre o `SupportsEmbed`, então entra no `Retriever`
no lugar do `OllamaEmbedder`:

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatEmbedder


async def main() -> None:
    """Gera dois vetores."""
    embedder: OpenAICompatEmbedder = OpenAICompatEmbedder(
        "text-embedding-3-small",
        api_key="sk-...",
    )
    vetores: list[list[float]] = await embedder.embed(["olá", "mundo"])
    print(len(vetores))


asyncio.run(main())
```

Os vetores voltam na ordem da entrada, ordenados pelo `index` da resposta.

!!! note "Anthropic e DeepSeek não têm embedding"

    A Anthropic não oferece endpoint de embedding, e a navegação da
    referência do DeepSeek lista Chat Completions, Responses, FIM, modelos,
    saldo e arquivos — nenhum embedding. Para RAG com esses provedores, gere
    os vetores com `OpenAICompatEmbedder`, `OllamaEmbedder` ou `Embedder`.

## Transcrição remota (speech-to-text)

O `SpeechToText` roda o Whisper **no seu processo**: baixa peso, ocupa RAM
(ou GPU) e disputa CPU com o resto do serviço. Quando já existe um servidor
que transcreve — o `tempest-ai`, a própria OpenAI, um `vLLM` com Whisper —, o
`OpenAICompatSpeech` manda o áudio para ele pelo endpoint OpenAI
`POST {base_url}/audio/transcriptions` e devolve a mesma `Transcription`.

Primeiro, as settings. A chave e a raiz são as mesmas do `openai_kwargs()`
(`OPENAI_API_KEY`, `OPENAI_BASE_URL`); só o modelo muda, em
`OPENAI_STT_MODEL`:

```bash
# .env
OPENAI_API_KEY=<chave de API do tempest-ai>
OPENAI_BASE_URL=https://ai.buscar.app.br/api/openai/v1
OPENAI_STT_MODEL=<id que GET /models lista>
```

Depois, um serviço que recebe o upload e transcreve:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile

from tempest_fastapi_sdk import (
    BaseAppSettings,
    HostedGenAISettings,
    register_exception_handlers,
)
from tempest_fastapi_sdk.genai import OpenAICompatSpeech
from tempest_fastapi_sdk.genai.audio import Transcription


class Settings(HostedGenAISettings, BaseAppSettings):
    """Settings do serviço."""


settings: Settings = Settings()
speech: OpenAICompatSpeech = OpenAICompatSpeech(**settings.openai_speech_kwargs())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fecha o cliente HTTP do backend no desligamento."""
    yield
    await speech.aclose()


app: FastAPI = FastAPI(lifespan=lifespan)
register_exception_handlers(app)


@app.post("/transcrever")
async def transcrever(arquivo: UploadFile) -> Transcription:
    """Transcreve o áudio enviado em português."""
    audio: bytes = await arquivo.read()
    return await speech.transcribe(audio, language="pt", with_segments=False)
```

Pedaço por pedaço:

- `openai_speech_kwargs()` devolve `model`, `api_key`, `base_url` e
  `provider="openai"` — o splat monta o backend. Com `OPENAI_STT_MODEL`
  vazio ele levanta `ValueError` no boot, como o `openai_kwargs()` faz com o
  `OPENAI_MODEL`.
- `base_url` **inclui** a versão (`.../v1`), igual ao `OpenAICompatGenerator`;
  o backend acrescenta `/audio/transcriptions`. Para o `tempest-ai`, passe
  `https://ai.buscar.app.br/api/openai/v1`.
- O pedido é multipart com `file`, `model`, `language` (só quando você passa
  um) e `response_format=verbose_json`, o formato que traz `duration`,
  `language` e `segments`. A chave vai em `Authorization: Bearer`.
- `language` aceita o código ISO-639-1 (`"pt"`) ou um `LanguageEnum`
  (`LanguageEnum.PT_BR` vira `"pt"`); `None` deixa o servidor detectar.
- `with_segments=False` devolve `segments=[]`; `duration` e `language`
  continuam preenchidos.
- `audio` aceita `bytes` ou um caminho local (`str` ou `Path`). String é
  sempre caminho, nunca URL: o backend não busca nada fora da máquina.

!!! tip "Troca direta pelo `SpeechToText`"

    `OpenAICompatSpeech.transcribe` aceita os mesmos `language` e
    `with_segments` do `SpeechToText` e cumpre o protocolo
    `SpeechTranscriber`. Código que faz
    `await transcriber.transcribe(audio, language="pt", with_segments=False)`
    não muda; e `make_genai_router(speech_to_text=speech)` serve o
    `/transcribe` com ele.

Os erros do servidor viram exceções que o `register_exception_handlers`
já sabe responder:

| Status do servidor | Exceção | O que o seu cliente recebe |
| --- | --- | --- |
| 401, 403 | `HostedAuthenticationError` | 500 — chave errada é defeito de configuração |
| 413 | `FileTooLargeException` (mensagem do servidor) | 413 |
| 400, 422 | `ValidationException` (mensagem do servidor) | 422 |
| 429 | `HostedRateLimitError` | 500 |
| 5xx | `HostedServerError` | 500 |

Em `413`/`400`/`422`, `details` traz `backend`, `model`, `status` e o
`server_code` que o servidor mandou (`AUDIO_TOO_LARGE`, `INVALID_AUDIO`).

!!! warning "Síntese de voz fica de fora"

    O `/audio/speech` usa outro modelo (o de TTS), e o `OpenAICompatSpeech`
    guarda um modelo só. Para voz, use `TextToSpeech` ou `OllamaSpeech`.

## Quando o provedor falha

Os dois backends levantam a mesma família de exceções:

| Exceção | Status | O que fazer |
| --- | --- | --- |
| `HostedAuthenticationError` | 401, 403 | Corrigir a chave — repetir não adianta |
| `HostedRateLimitError` | 429 | Esperar `retry_after` segundos (quando o header vem) |
| `HostedServerError` | 5xx, incluindo o 529 da Anthropic | Tentar depois, ou cair para outro backend |
| `HostedAPIError` | qualquer outro (400, 402, 422...) | Corrigir a requisição |

Todas herdam de `HostedAPIError`, que herda de `httpx.HTTPStatusError` — quem
já capturava essa exceção do `OpenAICompatGenerator` continua capturando.

```python
import asyncio

from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    HostedRateLimitError,
    HostedServerError,
    OllamaGenerator,
)


async def responder(pergunta: str) -> str:
    """Usa o Claude e cai para o Ollama local se ele estiver fora."""
    claude: AnthropicGenerator = AnthropicGenerator(api_key="sk-ant-...")
    try:
        return await claude.generate(pergunta)
    except HostedRateLimitError as erro:
        print(f"limite atingido; tente de novo em {erro.retry_after}s")
        raise
    except HostedServerError:
        return await OllamaGenerator("llama3.2").generate(pergunta)


print(asyncio.run(responder("Explique PIX.")))
```

Cada exceção traz `provider`, `status_code`, `error_type` e `detail` (do corpo
de erro do provedor) e `request_id` (header `request-id` da Anthropic). Na
Anthropic, um erro também pode chegar **depois** do `200`, no meio do stream
(`event: error`, <https://platform.claude.com/docs/en/build-with-claude/streaming>):
o `stream()` levanta a mesma exceção tipada — um `overloaded_error` vira
`HostedServerError` com `status_code` 529 — e o texto já entregue continua
entregue.

!!! tip "Retry já vem embutido"

    O cliente HTTP do SDK repete `429`, `500`, `502`, `503` e `504` com backoff exponencial — no
    `AnthropicGenerator`, o `529` também. O que ele **não** repete é timeout
    de leitura: o provedor aborta a geração quando o cliente desiste, e a
    repetição recomeçaria do zero.

## Por que não os SDKs oficiais

`anthropic` e `openai` são wrappers de HTTP. Na versão atual dos dois
(`anthropic` 1.15.0, `openai` 3.31.0), ambos exigem `httpx2` — uma segunda
pilha HTTP, que o `HTTPClient` do SDK e o `httpx.MockTransport` dos testes
não alcançam — mais `jiter`, `anyio` e `sniffio` com teto de versão. O que o
SDK usa deles é um POST e um leitor de SSE. Implementar sobre o `httpx` que já
é dependência mantém retry, circuit-breaker e `X-Request-ID` iguais aos do
Ollama, e não leva nenhum teto para o lock de quem consome.

## Recap

- `AnthropicGenerator` e `OpenAICompatGenerator` cumprem os mesmos protocolos
  que o `OllamaGenerator`: troque a construção, mantenha o resto.
- `HostedGenAISettings` + `anthropic_kwargs()` / `openai_kwargs()` /
  `deepseek_kwargs()` / `gemini_kwargs()` montam o backend por splat.
- `GENAI_PROVIDER` + `GENAI_API_KEY` (+ `GENAI_BASE_URL`) fixam um provedor
  para todos os modelos, pelo `.env`; modelo de outro provedor levanta.
- `hosted_generator(**settings.hosted_kwargs(modelo))` escolhe o provedor
  pelo id do modelo; `complete_structured` devolve saída, uso e motivo de
  parada em qualquer desfecho (o uso, quando o provedor o informa), com imagem
  por `image_part`.
- PDF vai por `document_part`: bloco `document` na Anthropic, parte `file` na
  OpenAI, `ValueError` antes da chamada no Google e no DeepSeek;
  `supports_documents(provider)` responde no boot.
- Tool calling, saída estruturada e stream funcionam nos quatro (no Gemini,
  tool calling e stream vão pelo endpoint compatível sem teste próprio);
  embeddings só na OpenAI (`OpenAICompatEmbedder`).
- `OpenAICompatSpeech(**settings.openai_speech_kwargs())` transcreve num
  servidor remoto (`/audio/transcriptions`) e substitui o `SpeechToText` sem
  mudar a chamada; `413` vira `FileTooLargeException` e `400`/`422` viram
  `ValidationException`.
- Falhas viram `HostedAuthenticationError`, `HostedRateLimitError` ou
  `HostedServerError` — inclusive no meio do stream.
