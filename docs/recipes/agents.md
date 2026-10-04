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

!!! abstract "Quer entender o mecanismo antes do código?"
    [Agentes: como funcionam por dentro](agents-concepts.md) mostra o laço com
    a transcrição literal que o modelo recebe em cada volta, o vocabulário
    (passo, observação, artefato, orçamento) e o critério para escolher entre
    ferramenta, skill, delegação e laço. Esta página assume o mecanismo; aquela
    o explica.


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

!!! info "O peso baixa uma vez — depois é cache em disco"
    A primeira chamada escreve os GB em `$HF_HOME/hub` (ou
    `~/.cache/huggingface/hub`); as execuções seguintes leem de lá, sem rede.
    Num **container sem volume** isso se perde a cada restart. Como apontar o
    cache, fixar a revisão, pré-baixar no deploy e rodar offline está em
    **[Pesos de modelos »](model-weights.md#onde-os-pesos-ficam-e-por-que-a-2a-execucao-e-instantanea)**.

Três passos: o modelo pediu a ferramenta, a ferramenta rodou, o modelo leu o
resultado e respondeu. Tudo isso num modelo de 0.5B rodando em CPU.

O que aconteceu por baixo: o agente mandou ao modelo o `system_prompt` mais o
seu objetivo, junto da lista de ferramentas. O modelo **não executou nada** —
devolveu um pedido (`get_weather`, `{"city": "Recife"}`). O agente rodou o
handler, anexou a saída à conversa como mensagem de papel `tool` e perguntou de
novo; dessa vez o modelo respondeu sem pedir mais nada, e o laço fechou em
`completed`. A transcrição literal dessas duas chamadas está em
[como funcionam por dentro](agents-concepts.md#o-que-o-modelo-ve-em-cada-volta).

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
travar, e aí o agente fica parado sem estourar passo nenhum. Por isso toda
chamada de modelo e de ferramenta roda sob o tempo que **resta** no relógio e
é cancelada quando ele acaba, e por isso `max_seconds` tem default (120s) em
vez de ser opcional. Medido com uma ferramenta que dorme 3 s e
`max_seconds=0.5`: a execução termina em 0,75 s com `stop_reason=timeout` —
os 0,5 s do orçamento mais a folga de 0,25 s que uma ferramenta de topo tem
para um sub-agente conseguir parar sozinho e devolver o traço dele.

Os tetos de passo e de chamada também valem **dentro** de uma volta: um
modelo que pede 50 ferramentas de uma vez não executa as 50. Medido com
`max_steps=5, max_tool_calls=2` e uma volta pedindo 50 chamadas: rodaram 2, e
a execução parou em `max_tool_calls`.

O orçamento existe porque o critério de parada natural do laço — "o modelo
decidiu que acabou" — é justamente o que um modelo confuso não cumpre. Medido
com um modelo que nunca para de pedir ferramenta e `max_steps=4`: a execução
termina em `max_steps`, com `succeeded=False` e `output` **vazio**, porque ele
nunca chegou a escrever texto. Detalhes em
[por que existe orçamento](agents-concepts.md#por-que-existe-orcamento).

## O modelo roda com os defaults do gerador

O laço do agente chama o modelo com as mensagens e as ferramentas, e só: não
passa `config`, nem `max_new_tokens`, nem tamanho de contexto. Então o que
vale é o que **o gerador** traz de fábrica — e é no gerador que você ajusta:

```python title="defaults.py" hl_lines="12-13"
import asyncio

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.genai import GenerationConfig, OllamaGenerator


async def main() -> None:
    """Run an agent whose model has a bounded window and greedy decoding."""
    model = OllamaGenerator(
        "ministral-3:14b",
        num_ctx=32768,
        config=GenerationConfig(max_new_tokens=512, do_sample=False),
    )
    agent = Agent(model, tools=[weather_tool])
    run = await agent.run("Qual o tempo no Recife?")

    print(run.stop_reason, run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

`config=` vale em toda chamada do gerador, e uma chamada que traz o próprio
`config` ganha campo a campo. O `TextGenerator` aceita o mesmo `config=`.

!!! warning "No Ollama, `num_ctx` não é opcional para agente"
    O prompt de um agente é o turno de sistema, a especificação de toda
    ferramenta e **todo** resultado de ferramenta até ali — ele cresce a
    cada passo. Quando passa da janela de contexto, o Ollama corta o
    prompt **sem erro**. Medido no Ollama 0.30.11 com `ministral-3:14b`,
    numa ferramenta que devolvia ~11,5 mil tokens: sem `num_ctx`, o daemon
    processou 2 051 tokens, o modelo perdeu a pergunta e a execução terminou
    `completed` com a resposta errada (3 de 3 execuções); com
    `num_ctx=32768`, processou os 11 551 e respondeu certo (3 de 3).

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

Qualquer exceção do handler também vira observação, com uma diferença que
importa: a mensagem de um `AgentToolError` é tratada como **escrita para ser
mostrada** e vai inteira para o traço, enquanto de uma exceção qualquer o
traço guarda só o tipo. O traço é o que o router HTTP, o stream SSE e os
sinks expõem, e uma exceção arbitrária carrega DSN, token ou caminho de
arquivo. Medido com um handler levantando
`RuntimeError("could not connect to postgresql://admin:hunter2@db:5432/app")`:
o modelo lê o texto inteiro, e o passo registra
`RuntimeError: the tool failed (details withheld)` — a senha não aparece no
`run.model_dump_json()`. A exceção completa vai para o log
(`tempest_fastapi_sdk.agents.agent`). Em desenvolvimento,
`Agent(..., expose_tool_errors=True)` grava o texto inteiro no traço.

!!! tip "Argumento como string JSON"
    Servidores no formato da OpenAI (vLLM, TGI, APIs hospedadas) mandam os
    `arguments` de uma chamada como **string** JSON; o agente faz o parse.
    JSON inválido, ou algo que não é objeto, vira erro de ferramenta que o
    modelo lê — não uma chamada com `{}`. Quando a chamada traz `id`, a
    mensagem `tool` de volta leva `tool_call_id` e `name`.

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
`context` (os artefatos da execução, mais o que a sua aplicação semear nele).
Devolver `str` também vale, quando não há nada binário — ele é embrulhado num
`ToolResult` automaticamente.

!!! tip "Ferramenta que consulta o banco de dados?"
    É o que quase todo mundo escreve primeiro, e a sessão **não** chega por
    `Depends` — o agente não vive dentro de uma requisição.
    [Agentes de IA (banco de dados) »](agents-db.md) mostra o padrão inteiro,
    e é lá que o parâmetro `context` é destrinchado.

!!! tip "Já tem ferramentas do `AIChatPipeline`?"
    `AgentTool.from_tool(tool)` adapta as ferramentas de um só argumento do
    chat pipeline, sem tocar nelas.

## Servir por HTTP

```python title="app.py" hl_lines="11 14 23 30"
from fastapi import FastAPI, Header

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryAgentRunSink,
    make_agent_router,
)
from tempest_fastapi_sdk.genai import TextGenerator, TextModel

store = InMemoryAgentRunSink(max_runs=50)


def current_user(x_user_id: str = Header()) -> str:
    """Return who is calling.

    A stand-in for your real authentication dependency: trusting a header
    is only acceptable behind a gateway that sets it.
    """
    return x_user_id


agent = Agent(
    TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
    tools=[weather_tool],
    run_sink=store,
)

app = FastAPI()
app.include_router(make_agent_router(agent, run_store=store, owner=current_user))
```

```bash
uvicorn app:app --reload
```

| Rota | O que faz |
| --- | --- |
| `POST /api/agent/run` | Executa até o fim e devolve o registro, com `run_id` |
| `POST /api/agent/run/stream` | Cada passo como evento SSE, e um `done` com `{"run_id": ...}` no fim |
| `GET /api/agent/runs` | Execuções recentes de **quem chama** (só com `run_store`) |
| `GET /api/agent/runs/{run_id}/artifacts/{nome}` | Baixa um artefato (só com `run_store`); `nome` pode ter `/` (`illustrator/bike.png`) |

Uma execução guardada é endereçada pelo `run_id` dela, um id estável — nunca
pela posição no histórico, que muda a cada execução nova e faria um link de
um segundo atrás servir a execução de outra pessoa.

`owner=` é a dependência FastAPI que devolve **quem chama**. Com ela, cada
execução é marcada com esse id (`AgentRun.owner`, e `AgentContext.owner` para
as ferramentas lerem), `GET /runs` lista só as execuções de quem chama, e o
artefato da execução de outra pessoa responde `404`, igual a uma execução que
não existe. Sem `owner=`, todo mundo que alcança o router vê toda execução
guardada — aceitável só quando um único principal chega nele.

O JSON traz os artefatos como **metadados** (nome, tipo, tamanho), nunca os
bytes: uma imagem gerada tem megabytes, e base64 no corpo infla isso em um
terço. Os bytes vêm numa segunda requisição, com o media type certo — o que
também faz um `<img src>` funcionar direto.

!!! tip "Várias execuções ao mesmo tempo num modelo em CPU"
    Cada execução que chega no router chama o mesmo `TextGenerator`, e em
    CPU uma decodificação já ocupa todos os núcleos: sem limite, quatro
    execuções simultâneas dividem a máquina e todas terminam tarde. Crie o
    gerador com `max_concurrent=1` —
    `TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT, max_concurrent=1)` — e
    os pedidos excedentes esperam numa fila. Medido com esse modelo, quatro
    chamadas simultâneas: latência mediana de 22,73 s para 11,91 s, vazão de
    22,4 para 26,9 tokens/s. O orçamento de tempo do agente conta a espera
    na fila, então dimensione `max_seconds` com isso em mente. Os números, o
    hardware e por que é um pool de threads e não um semáforo estão em
    [IA generativa self-hosted](genai.md#varios-pedidos-ao-mesmo-tempo-em-cpu-max_concurrent).

## Agente em CPU: backend, tamanho e orçamento

Sem GPU, cada volta do laço é uma geração inteira na CPU, e três decisões
mudam o tempo de uma execução por um fator de cinco ou mais: qual backend
roda o modelo, de que tamanho é o modelo, e quanto texto as ferramentas
devolvem. O orçamento e o timeout precisam caber nesses números, não o
contrário.

```python title="cpu_agent.py" hl_lines="11-16 20"
import asyncio

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import Agent, AgentBudget
from tempest_fastapi_sdk.genai import GenerationConfig, OllamaGenerator


def build_cpu_agent() -> Agent:
    """Build an agent sized for a CPU-only host."""
    model = OllamaGenerator(
        "qwen2.5:3b",
        num_ctx=8192,
        options={"num_gpu": 0},
        timeout=120.0,
        config=GenerationConfig(max_new_tokens=256),
    )
    return Agent(
        model,
        tools=[weather_tool],
        budget=AgentBudget(max_steps=12, max_seconds=120),
    )


async def main() -> None:
    """Run once and print how long each step took."""
    agent = build_cpu_agent()
    run = await agent.run("Qual o tempo no Recife? Use a ferramenta.")

    print(run.stop_reason, f"{run.seconds:.1f}s")
    for step in run.steps:
        print(step.kind, step.name, f"{step.seconds:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
```

```text
completed 1.9s
model chat 0.86s
tool get_weather 0.00s
model chat 1.05s
```

(Uma execução com o modelo já carregado no daemon; os números de onde ela
sai estão logo abaixo.)

Pedaço por pedaço:

- **`OllamaGenerator("qwen2.5:3b")`** — um GGUF quantizado em 4 bits servido
  pelo Ollama, e não o `TextGenerator` em `float32`. O porquê está nos
  números abaixo; o dimensionamento de memória (e por que o bitsandbytes em
  CPU não é a saída) está em
  [IA generativa self-hosted › Em CPU](genai.md#em-cpu).
- **`num_ctx=8192`** — o prompt do agente cresce a cada passo, e o Ollama
  corta o que passa da janela sem erro. Ver
  [O modelo roda com os defaults do gerador](#o-modelo-roda-com-os-defaults-do-gerador).
- **`options={"num_gpu": 0}`** — força a CPU numa máquina que tem GPU (é
  como as medições abaixo foram feitas). Numa máquina sem GPU, dispense.
- **`config=GenerationConfig(max_new_tokens=256)`** — teto de tokens por
  volta. Em CPU a geração é a parte cara, e o laço não passa teto nenhum.
- **`timeout=120.0` e `max_seconds=120`** — são os defaults, escritos aqui
  porque andam juntos; a seção [O timeout do Ollama](#o-timeout-do-ollama-e-o-orcamento)
  explica por quê.

### Quanto custa cada passo

Medido num i9-13900F sob WSL2 (12 CPUs lógicas visíveis = 6 núcleos × 2,
62 GB de RAM, GPU escondida), torch 2.14 + transformers 4.57.6 para o
`TextGenerator` em `float32`, Ollama 0.30.11 com `num_gpu: 0` para os GGUF
Q4_K_M (`size_vram` = 0 no `/api/ps`). O agente é o desta seção, com os
defaults de geração de cada backend, e a meta pede **uma** chamada de
ferramenta e a resposta (três passos). "Frio" é a primeira execução de um
processo novo, com o load dos pesos dentro do primeiro passo (pesos já no
cache de disco do sistema); N=3. "Quente" são as cinco execuções seguintes
de cada processo; N=15. Mediana; parte das rodadas teve outro processo
ocupando uma CPU lógica.

Com a ferramenta devolvendo uma linha (prompt de ~230 tokens):

| Backend e modelo | Execução fria | Execução quente | Passo de modelo quente | Geração |
| --- | --- | --- | --- | --- |
| `TextGenerator` Qwen2.5-0.5B `float32` | 4,39 s | 2,12 s | 1,02 s | 25,6 tokens/s |
| `TextGenerator` Qwen2.5-3B `float32` | 13,87 s | 11,59 s | 5,50 s | 5,2 tokens/s |
| `OllamaGenerator` `qwen2.5:0.5b` | 1,52 s | 0,64 s | 0,29 s | 130 tokens/s |
| `OllamaGenerator` `qwen2.5:3b` | 4,83 s | 1,94 s | 0,84 s | 30,6 tokens/s |

Com a ferramenta devolvendo ~4 100 tokens, diferentes a cada execução
(prompt do último passo: ~4 400 tokens). O `TextGenerator` relê o prompt
inteiro em todo passo; no Ollama a observação muda de execução para
execução para o cache de prefixo não esconder o custo:

| Backend e modelo | Execução quente | Último passo | Prefill do último passo |
| --- | --- | --- | --- |
| `TextGenerator` Qwen2.5-0.5B `float32` | 7,60 s | 6,56 s | 819 tokens/s |
| `TextGenerator` Qwen2.5-3B `float32` | 43,08 s | 37,51 s | 134 tokens/s |
| `OllamaGenerator` `qwen2.5:0.5b` | 9,65 s | 9,34 s | 509 tokens/s |
| `OllamaGenerator` `qwen2.5:3b` | 35,91 s | 35,06 s | 132 tokens/s |

Todas as 180 execuções terminaram dentro do `AgentBudget` default
(`max_steps=12`, `max_seconds=120`): a mais lenta levou 51,3 s, e o passo
mais lento 43,0 s (3B `float32`, frio, observação longa).

O que os números dizem:

- **O GGUF ganha na geração, não na leitura do prompt.** No 3B, o Ollama
  gerou 30,6 tokens/s contra 5,2 do `float32` — 6× — e a execução curta
  caiu de 11,59 s para 1,94 s. Mas o prefill ficou igual (132 contra 134
  tokens/s): com uma observação de 4 mil tokens, o passo é quase todo
  prefill, e a diferença encolhe para 35,91 s contra 43,08 s.
- **O Ollama reaproveita o prefixo; o `TextGenerator` relê tudo.** Com a
  mesma observação longa repetida idêntica de uma execução para a outra, o
  prefill do último passo caiu para 0,05 s no `qwen2.5:3b` e continuou em
  32,66 s no 3B `float32`. O system prompt e a lista de ferramentas são o
  mesmo prefixo em toda execução, por isso o prefill do primeiro passo
  quente custa 0,04 s no `qwen2.5:3b` e 1,65 s no 3B `float32`.
- **Observação é o que custa.** Num 3B nesta CPU, cada mil tokens que uma
  ferramenta devolve custam ~7,6 s de prefill (1 000 / 132), em qualquer
  dos dois backends. Ferramenta que devolve o resumo e não o dump é a
  alavanca de tempo, não só de contexto.
- **0,5B no Ollama falhou em silêncio.** Em 12 de 54 execuções o
  `qwen2.5:0.5b` terminou `completed` sem chamar a ferramenta e com
  `output` vazio: o Ollama devolveu a mensagem sem texto e sem
  `tool_calls`. O mesmo modelo pelo `TextGenerator` chamou a ferramenta nas
  36 execuções, e o `qwen2.5:3b` nas 54. Em CPU, o 3B pelo Ollama foi o
  menor que completou todas.
- **O primeiro passo paga o load.** Frio contra quente, o primeiro passo
  custou +2,2 s no 0,5B e +2,6 s no 3B em `float32`, e o Ollama reportou
  1,4 s a 2,2 s de `load_duration` no 3B — com os pesos já no cache de disco.
  Chame `load()` do `TextGenerator` no startup do serviço (via
  `asyncio.to_thread`) para o primeiro usuário não pagar isso.

### Dimensionar o orçamento

O tempo de uma execução é a soma, passo a passo, de **tokens novos no
prompt ÷ taxa de prefill** mais **tokens gerados ÷ taxa de geração**. Com
as taxas medidas acima, a conta fecha com a medição: no 3B `float32` com a
observação longa, 229/140 + 21/5,4 + 4 381/134 + 23/4,6 ≈ 43,2 s, contra
43,08 s medidos.

Use essa conta, com as taxas da **sua** máquina, para escolher o teto:

- **`max_new_tokens` pesa mais que parece.** O default do `TextGenerator` é
  256 tokens por volta; a 5,2 tokens/s, uma volta que usa o teto inteiro
  são ~49 s de geração no 3B `float32`, e duas delas já passam de 120 s. A
  30,6 tokens/s, no GGUF, a mesma volta são ~8 s.
- **`max_seconds` é o teto de uma requisição HTTP**, então ele vem do que o
  seu cliente aceita esperar; o que você ajusta para caber nele é o
  modelo, o tamanho das observações e o `max_new_tokens`. A 132 tokens/s, os
  120 s default comportam ~15 mil tokens de observação somados na execução
  inteira, sem contar geração.
- **A fila conta no relógio.** Execuções simultâneas num `TextGenerator`
  com `max_concurrent=1` esperam umas pelas outras, e a espera sai do mesmo
  `max_seconds` — ver
  [Várias execuções ao mesmo tempo num modelo em CPU](#servir-por-http). A
  concorrência dentro do Ollama não foi medida aqui.

### O timeout do Ollama e o orçamento

`timeout=` do `OllamaGenerator` (default 120 s) é **por requisição**, e o
cliente HTTP do gerador **não** refaz uma requisição que estoura o tempo:
quando o cliente desiste, o daemon aborta a geração, então uma nova
tentativa recomeçaria do zero e estouraria de novo. O `ReadTimeout` chega
depois de um timeout. Medido com o `qwen2.5:3b` em CPU e `timeout=10.0`: a
chamada levantou `ReadTimeout` em 10,01 s (mediana, N=5), com um
`POST /api/chat` de 10,0 s no log do Ollama. Erro de conexão e `429`/`5xx`
continuam sendo refeitos.

??? note "Até a v0.303.1, o timeout era refeito três vezes"
    O cliente fazia três tentativas, com 0,5 s e 1 s de espera entre elas.
    No mesmo cenário, com um prompt de ~4 400 tokens (~33 s de prefill), a
    chamada levantava `ReadTimeout` depois de 31,53 s, o log do Ollama
    mostrava três `POST /api/chat` de 10,0 s, e dentro de um agente a
    execução terminava em `error` com 33,73 s. Nessas versões, passe
    `retry_policy=RetryPolicy(max_attempts=1)` (com
    `from tempest_fastapi_sdk import RetryPolicy`): com ela, a execução do
    agente terminou em `error` com 10,84 s — uma tentativa, que é o
    comportamento default a partir da versão seguinte.

Duas configurações que se comportam bem:

- **`timeout` maior ou igual a `max_seconds`** (o caso dos defaults): o
  orçamento corta antes do timeout. Com `max_seconds=20`, a execução
  terminou em `timeout` com 20,02 s.
- **Timeout menor que `max_seconds`**: a volta que estoura o timeout falha
  depois de um timeout, e a execução termina em `error`. Para o agente
  tratar isso como fim de orçamento, prefira a configuração anterior.

!!! tip "Resumo para começar em CPU"
    `OllamaGenerator` com um modelo de 3B em GGUF, `num_ctx` explícito,
    `max_new_tokens` explícito, ferramentas que devolvem pouco texto e os
    defaults do `AgentBudget`. Depois meça um `run.steps` na sua máquina e
    recalcule: as taxas desta página são de um i9-13900F, e a sua CPU muda
    todas elas.

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
- **Erro de ferramenta vira observação** para o modelo, não exceção — e só o
  texto de `AgentToolError` vai inteiro para o traço.
- **`make_agent_router`** publica `/run`, `/run/stream` e download de
  artefato por `run_id`; `owner=` separa as execuções por quem chama.
- **Em CPU**, o GGUF pelo `OllamaGenerator` gera 6× mais rápido que o
  `float32` no 3B, mas o prefill é igual: o tamanho das observações e o
  `max_new_tokens` é que fazem a execução caber no `max_seconds`.

Próximo passo: [Agentes de IA (avançado)](agents-advanced.md) — saída
estruturada tipada, as três camadas de memória, skills carregadas sob
demanda, delegação entre agentes e laços que insistem até passar num
critério.

Veja também: [Agentes de IA (arquitetura)](agents-architecture.md) para onde
cada peça mora num serviço de verdade, [IA generativa self-hosted](genai.md)
para os modelos em si, [Geração de imagem](image-generation.md) e
[Pesos de modelos](model-weights.md) para fixar o que o agente usa.
