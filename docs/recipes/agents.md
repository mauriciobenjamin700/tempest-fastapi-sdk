# Agentes de IA

Um **agente** recebe um objetivo, decide o que fazer, chama ferramentas e
conta o que fez. Essa última parte é o que o separa de um chat: a execução
volta com o **traço passo a passo** — argumentos, saídas, tempos, falhas — e
com os arquivos que ele produziu.

As ferramentas prontas embrulham os modelos que o SDK já roda localmente:
texto, imagem, áudio e RAG. Nada de API paga, nada saindo da máquina.

```bash
uv add "tempest-fastapi-sdk[genai]"   # agents não precisa de extra; o modelo, sim
```

!!! info "Submódulo, sem extra"
    `from tempest_fastapi_sdk.agents import Agent`. O módulo importa sem
    nenhum extra — o peso está nos objetos que **você** injeta, e cada um
    mantém o próprio carregamento preguiçoso.

!!! warning "O modelo é que puxa o extra"
    Um agente sem modelo não faz nada, e todo exemplo desta página injeta um
    `TextGenerator`, que vive em `[genai]`. Sem ele a primeira instanciação
    levanta `ImportError: Text generation requires the optional [genai]
    extra.` Vale o mesmo para `[genai-image]`, `[genai-audio]` e
    `[genai-rag]` nas seções seguintes.

!!! tip "Esta página é a trilha básica"
    Leia na ordem: ela constrói um agente do zero até servi-lo por HTTP.
    Quando terminar, [Agentes de IA (avançado)](agents-advanced.md) cobre
    saída estruturada, memória, skills, delegação entre agentes e laços
    autônomos.

## O primeiro agente

```python title="agent_setup.py" hl_lines="27 33 41"
import asyncio
from typing import Any

from tempest_fastapi_sdk.agents import Agent, AgentContext, text_tool
from tempest_fastapi_sdk.genai import TextGenerator, TextModel


async def get_weather(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Return the weather for a city."""
    return f"{arguments['city']}: 22 graus, céu limpo"


weather_tool = text_tool(
    "get_weather",
    "Get the current weather for a city.",
    get_weather,
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name."}},
        "required": ["city"],
    },
)


def build_agent() -> Agent:
    """Build the agent the rest of this page imports."""
    return Agent(TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT), tools=[weather_tool])


async def main() -> None:
    """Run the agent once and print the answer plus the step trace."""
    agent = build_agent()
    run = await agent.run("Qual o tempo no Recife? Use a ferramenta.")

    print(run.output)
    print(run.tool_calls)
    print([(step.kind, step.name) for step in run.steps])


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python agent_setup.py
```

```text
The weather in Recife is 22 degrees, clear sky.
['get_weather']
[('model', 'chat'), ('tool', 'get_weather'), ('model', 'chat')]
```

Três passos: o modelo pediu a ferramenta, a ferramenta rodou, o modelo leu o
resultado e respondeu. Tudo isso num modelo de 0.5B rodando em CPU.

!!! warning "`agent.run` é corrotina — precisa de contexto assíncrono"
    `await` fora de uma função `async` é `SyntaxError`. Por isso a chamada
    mora em `async def main()` e o arquivo termina em
    `asyncio.run(main())`. Num endpoint FastAPI (`async def`) você já está
    em contexto assíncrono: chame `await agent.run(...)` direto, sem
    `asyncio.run`.

!!! info "Cada exemplo desta página é um arquivo que roda"
    Salve o bloco acima como `agent_setup.py`. Os exemplos seguintes são
    arquivos completos ao lado dele e importam o que já foi construído
    (`from agent_setup import build_agent`) em vez de repetir trinta
    linhas de setup — nada de trecho com nome solto que não existe em
    lugar nenhum.

!!! tip "A descrição da ferramenta é o que importa"
    O modelo escolhe pelo `description` — é o único texto que ele lê sobre a
    ferramenta. Vale mais cuidado ali que na implementação.

## Sempre olhe o `stop_reason`

```python title="stop_reason.py" hl_lines="11 12"
import asyncio

from agent_setup import build_agent


async def main() -> None:
    """Print the answer only when the model decided it was finished."""
    agent = build_agent()
    run = await agent.run("Compare o tempo em Recife, Olinda e Jaboatão.")

    if not run.succeeded:
        print("truncado:", run.stop_reason, f"({run.seconds:.1f}s)")
        return
    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

`succeeded` só é `True` quando o **modelo** decidiu que terminou. Os outros
motivos são o agente cortando a execução:

| `stop_reason` | O que aconteceu |
| --- | --- |
| `completed` | O modelo respondeu sem pedir outra ferramenta. |
| `max_steps` | O teto de passos acabou primeiro. |
| `timeout` | O teto de tempo acabou primeiro. |
| `max_tool_calls` | O teto de chamadas acabou primeiro. |
| `error` | O backend do modelo falhou. |
| `blocked` | A moderação recusou o objetivo ou a resposta. |

!!! warning "Uma execução truncada ainda traz texto"
    O `output` de uma execução cortada é a última coisa que o modelo disse —
    trabalho parcial, não resposta final. Quem ignora o `stop_reason`
    apresenta trabalho pela metade como se estivesse pronto.

## Orçamento

```python title="budget.py" hl_lines="13"
import asyncio

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import Agent, AgentBudget
from tempest_fastapi_sdk.genai import TextGenerator, TextModel


async def main() -> None:
    """Run the same agent under an explicit ceiling."""
    agent = Agent(
        TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
        tools=[weather_tool],
        budget=AgentBudget(max_steps=8, max_seconds=90, max_tool_calls=5),
    )
    run = await agent.run("Qual o tempo no Recife?")

    print(run.stop_reason, f"{run.seconds:.1f}s", len(run.steps), "passos")


if __name__ == "__main__":
    asyncio.run(main())
```

Passos sozinhos **não** limitam uma execução: uma chamada de ferramenta pode
travar, e aí o agente fica parado sem estourar passo nenhum. Por isso o
relógio também é verificado, e por isso `max_seconds` tem default (120s) em
vez de ser opcional.

## Ferramentas tipadas com Pydantic

Escrever JSON-schema à mão ao lado do handler significa **duas descrições da
mesma coisa**, que divergem na primeira edição: o schema diz `city`, o
handler lê `arguments["town"]`, e nada acusa até um modelo chamar a
ferramenta. O decorator `@tool` elimina a duplicata.

```python title="typed_tool_agent.py" hl_lines="17 18"
import asyncio

from pydantic import Field

from tempest_fastapi_sdk.agents import Agent, AgentContext, tool
from tempest_fastapi_sdk.genai import TextGenerator, TextModel
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str = Field(description="Cidade a consultar.")
    days: int = Field(default=1, ge=1, le=7, description="Horizonte em dias.")


@tool("get_weather", "Get the current weather for a city.")
async def get_weather(args: WeatherArgs, context: AgentContext) -> str:
    """Return the forecast for the requested city."""
    return f"{args.city}: 22 graus, {args.days}d"


async def main() -> None:
    """Hand the decorated tool to an agent and run it."""
    agent = Agent(
        TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
        tools=[get_weather],
    )
    run = await agent.run("Qual a previsão de 3 dias para Olinda?")

    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

O schema que o modelo vê é **gerado** do modelo Pydantic, e o handler recebe
uma instância **validada** — `args.city` é tipado e o `mypy` confere.

!!! check "Erro de argumento vira observação, não `KeyError`"
    A validação acontece **antes** do handler rodar. Um modelo que inventa
    `town=` recebe de volta:

    ```text
    invalid arguments for get_weather: city: Field required
    ```

    Preciso o bastante para ele se corrigir no turno seguinte. Antes, isso
    explodia no meio do seu código.

Restrições declaradas no modelo valem: `ge`, `le`, `max_length`, enums. Um
modelo pedindo `days=500` é corrigido antes de você ver.

Sem decorator (handler que é lambda, método ligado, ou vem de outro lugar):

```python title="typed_tool_manual.py" hl_lines="19"
from pydantic import Field

from tempest_fastapi_sdk.agents import AgentContext, AgentTool, typed_tool
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str = Field(description="Cidade a consultar.")
    days: int = Field(default=1, ge=1, le=7, description="Horizonte em dias.")


async def get_weather_impl(args: WeatherArgs, context: AgentContext) -> str:
    """Return the forecast — a plain function, no decorator involved."""
    return f"{args.city}: 22 graus, {args.days}d"


built: AgentTool = typed_tool(
    "get_weather",
    "Get the weather.",
    WeatherArgs,
    get_weather_impl,
)
```

## Ferramentas sobre os modelos locais

Aqui é onde o módulo encosta no resto do SDK:

```python title="multimodal_setup.py" hl_lines="40"
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.agents import (
    Agent,
    describe_image_tool,
    generate_image_tool,
    retrieve_tool,
    speak_tool,
    transcribe_audio_tool,
    web_search_tool,
)
from tempest_fastapi_sdk.genai import (
    Embedder,
    EmbeddingModel,
    ImageGenerator,
    ImageModel,
    TextGenerator,
    TextModel,
    VisionModel,
    VisionTextGenerator,
)
from tempest_fastapi_sdk.genai.audio import SpeechToText, TextToSpeech
from tempest_fastapi_sdk.genai.rag import (
    InMemoryVectorStore,
    Retriever,
    SearxngBackend,
    WebSearch,
)


def build_multimodal_agent() -> Agent:
    """Wire one agent over every local model the SDK can run."""
    retriever = Retriever(
        Embedder(EmbeddingModel.ALL_MINILM_L6_V2),
        InMemoryVectorStore(),
    )
    web_search = WebSearch(
        SearxngBackend("http://localhost:8080", http_client=HTTPClient()),
    )
    return Agent(
        TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT),
        tools=[
            generate_image_tool(ImageGenerator(ImageModel.SDXL_TURBO), default_steps=4),
            describe_image_tool(VisionTextGenerator(VisionModel.QWEN2_VL_2B_INSTRUCT)),
            transcribe_audio_tool(SpeechToText("base")),
            speak_tool(TextToSpeech()),
            retrieve_tool(retriever),
            web_search_tool(web_search),
        ],
    )
```

!!! warning "Cada ferramenta puxa o seu extra"
    `[genai]` (texto), `[genai-image]` (imagem), `[genai-vlm]` (visão),
    `[genai-audio]` (STT/TTS) e `[genai-rag]` (retriever + busca web).
    Instale só as que você vai usar — os pesos só são baixados na primeira
    chamada de cada modelo, não na instanciação acima.

| Ferramenta | Modelo por trás | O que faz |
| --- | --- | --- |
| `generate_image_tool` | `ImageGenerator` | Desenha e guarda como artefato |
| `describe_image_tool` | `VisionTextGenerator` | Olha uma imagem e responde sobre ela |
| `transcribe_audio_tool` | `SpeechToText` | Áudio → texto |
| `speak_tool` | `TextToSpeech` | Texto → áudio (artefato WAV) |
| `retrieve_tool` | `Retriever` | Busca no corpus indexado |
| `web_search_tool` | `WebSearch` | Busca na web via SearXNG |
| `save_artifact_tool` | — | Salva texto como arquivo entregável |

!!! note "`default_steps` não é detalhe"
    Um modelo turbo quer ~4 passos de difusão e um completo quer ~30. Se o
    LLM escolher às cegas, uma renderização demora dez vezes mais que o
    necessário. Fixe o valor do seu checkpoint na ferramenta.

## Encadear multimodal: desenhar e depois olhar

É aqui que os **artefatos nomeados** ganham sentido:

```python title="draw_then_look.py" hl_lines="9 17"
import asyncio

from multimodal_setup import build_multimodal_agent


async def main() -> None:
    """Draw an image, then ask the vision model what it drew."""
    agent = build_multimodal_agent()
    run = await agent.run(
        "Desenhe uma bicicleta vermelha como bike.png e depois me diga o que "
        "aparece na imagem que você criou.",
    )

    for step in run.steps:
        print(step.kind, step.name, step.artifacts)

    bike = run.artifact("bike.png")
    if bike is not None:
        print(bike.media_type)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
model chat []
tool generate_image ['bike.png']
model chat []
tool describe_image []
model chat []
image/png
```

O `generate_image` registra `bike.png` na execução; o `describe_image` aceita
esse mesmo nome e lê os bytes de volta do contexto. **A imagem nunca toca o
disco e o modelo nunca carrega base64 no prompt** — ele só passa um nome
adiante.

Se o modelo inventar um nome que não existe, a ferramenta erra dizendo quais
existem:

```text
no artifact named 'chart.png'; available: bike.png
```

Isso é de propósito: um "não encontrado" seco não dá ao modelo nada com que
se corrigir.

## Falha de ferramenta não derruba a execução

```python title="failing_tool.py" hl_lines="10 30"
import asyncio
from typing import Any

from tempest_fastapi_sdk.agents import Agent, AgentContext, AgentToolError, text_tool
from tempest_fastapi_sdk.genai import TextGenerator, TextModel


async def save(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Save something, or explain why it could not be saved."""
    raise AgentToolError("disco cheio")


save_tool = text_tool(
    "save_note",
    "Save a note to disk.",
    save,
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)


async def main() -> None:
    """Show that a raising tool becomes an observation, not a crash."""
    agent = Agent(TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT), tools=[save_tool])
    run = await agent.run("Salve a nota 'comprar pão'.")

    failed = [step for step in run.steps if step.error]
    print(failed[0].error)
    print(run.stop_reason, run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

O passo fica marcado com `error`, e a mensagem volta **para o modelo** como
observação. Ele costuma tentar outro caminho. Levantar a exceção para cima
jogaria fora todo o trabalho anterior da execução.

```text
AgentToolError: disco cheio
completed Não consegui salvar a nota: o disco está cheio.
```

Qualquer exceção do handler é tratada igual — a diferença de usar
`AgentToolError` é só deixar a intenção explícita.

## Escrever a sua própria ferramenta

```python title="report_tool.py" hl_lines="15 33"
import asyncio
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentArtifact,
    AgentContext,
    AgentTool,
    ToolResult,
)
from tempest_fastapi_sdk.genai import TextGenerator, TextModel


async def render_report(
    arguments: dict[str, Any],
    context: AgentContext,
) -> ToolResult:
    """Render a report and return it as a downloadable artifact."""
    body = f"# {arguments['title']}\n\n{arguments['body']}"
    return ToolResult(
        text=f"Relatório '{arguments['title']}' gerado.",
        artifacts=[
            AgentArtifact(
                name="report.md",
                media_type="text/markdown",
                data=body.encode("utf-8"),
            ),
        ],
    )


report_tool = AgentTool(
    name="render_report",
    description="Render a titled report the user can download.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["title", "body"],
    },
    handler=render_report,
)


async def main() -> None:
    """Run the agent and write the artifact it produced to disk."""
    agent = Agent(TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT), tools=[report_tool])
    run = await agent.run("Gere um relatório 'Vendas' resumindo o trimestre.")

    report = run.artifact("report.md")
    if report is not None:
        Path("report.md").write_bytes(report.data)
        print("escrito:", report.media_type, len(report.data), "bytes")


if __name__ == "__main__":
    asyncio.run(main())
```

O handler recebe **dois** argumentos: `arguments` (o que o modelo passou) e
`context` (os artefatos da execução). Devolver `str` também vale, quando não
há nada binário — ele é embrulhado num `ToolResult` automaticamente.

!!! tip "Já tem ferramentas do `AIChatPipeline`?"
    `AgentTool.from_tool(tool)` adapta as ferramentas de um só argumento do
    chat pipeline, sem tocar nelas.

## Servir por HTTP

```python title="app.py" hl_lines="11 15 19"
from fastapi import FastAPI

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryAgentRunSink,
    make_agent_router,
)
from tempest_fastapi_sdk.genai import TextGenerator, TextModel

store = InMemoryAgentRunSink(max_runs=50)
agent = Agent(
    TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
    tools=[weather_tool],
    run_sink=store,
)

app = FastAPI()
app.include_router(make_agent_router(agent, run_store=store))
```

```bash
uvicorn app:app --reload
```

| Rota | O que faz |
| --- | --- |
| `POST /api/agent/run` | Executa até o fim e devolve o registro |
| `POST /api/agent/run/stream` | Cada passo como evento SSE, e um `done` no fim |
| `GET /api/agent/runs` | Execuções recentes (só com `run_store`) |
| `GET /api/agent/runs/{i}/artifacts/{nome}` | Baixa um artefato |

O JSON traz os artefatos como **metadados** (nome, tipo, tamanho), nunca os
bytes: uma imagem gerada tem megabytes, e base64 no corpo infla isso em um
terço. Os bytes vêm numa segunda requisição, com o media type certo — o que
também faz um `<img src>` funcionar direto.

## Recapitulando

- **`Agent.run(goal)`** devolve `AgentRun`: resposta, traço, artefatos e
  **por que parou**.
- **`AgentBudget`** limita passos, tempo e chamadas; o tempo é o que de fato
  protege uma requisição.
- **`@tool`** deriva o schema de um modelo Pydantic — uma descrição só, e
  argumento errado vira observação corrigível.
- **Ferramentas prontas** cobrem imagem, visão, áudio, RAG e web sobre os
  modelos que você já hospeda.
- **Artefatos nomeados** encadeiam multimodal sem disco e sem base64.
- **Erro de ferramenta vira observação** para o modelo, não exceção.
- **`make_agent_router`** publica `/run`, `/run/stream` e download de
  artefato.

Próximo passo: [Agentes de IA (avançado)](agents-advanced.md) — saída
estruturada tipada, as três camadas de memória, skills carregadas sob
demanda, delegação entre agentes e laços que insistem até passar num
critério.

Veja também: [IA generativa self-hosted](genai.md) para os modelos em si,
[Geração de imagem](image-generation.md) e
[Pesos de modelos](model-weights.md) para fixar o que o agente usa.
