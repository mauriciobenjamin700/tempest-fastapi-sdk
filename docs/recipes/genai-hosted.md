# IA generativa hospedada (Anthropic, OpenAI, DeepSeek)

A base de IA do SDK roda modelo local — `TextGenerator` (transformers) ou
`OllamaGenerator` (daemon). Às vezes a conta que importa não é a de RAM, é a
de **custo por token**, ou você precisa de um modelo que não cabe na sua
máquina. Para isso existem os backends hospedados:

| Provedor | Backend | Geração + stream | Tool calling | Saída estruturada | Embeddings |
| --- | --- | --- | --- | --- | --- |
| Anthropic | `AnthropicGenerator` | sim | sim | sim (`json_schema`) | — |
| OpenAI | `OpenAICompatGenerator` | sim | sim | sim (`json_schema`) | `OpenAICompatEmbedder` |
| DeepSeek | `OpenAICompatGenerator` | sim | sim | sim (`json_object`) | — |

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
DEEPSEEK_API_KEY=sk-...
```

Cada provedor tem `*_API_KEY`, `*_BASE_URL` e `*_MODEL`. Os defaults:

| Variável | Default |
| --- | --- |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | vazio — obrigatório |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-flash` |

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
  `deepseek_kwargs()` montam o backend por splat.
- Tool calling, saída estruturada e stream funcionam nos três; embeddings só
  na OpenAI (`OpenAICompatEmbedder`).
- Falhas viram `HostedAuthenticationError`, `HostedRateLimitError` ou
  `HostedServerError` — inclusive no meio do stream.
