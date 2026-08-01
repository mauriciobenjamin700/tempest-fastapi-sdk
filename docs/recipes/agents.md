# Agentes de IA

Um **agente** recebe um objetivo, decide o que fazer, chama ferramentas e
conta o que fez. Essa última parte é o que o separa de um chat: a execução
volta com o **traço passo a passo** — argumentos, saídas, tempos, falhas — e
com os arquivos que ele produziu.

As ferramentas prontas embrulham os modelos que o SDK já roda localmente:
texto, imagem, áudio e RAG. Nada de API paga, nada saindo da máquina.

```bash
uv add "tempest-fastapi-sdk"        # o módulo em si não precisa de extra
```

!!! info "Submódulo, sem extra"
    `from tempest_fastapi_sdk.agents import Agent`. O módulo importa sem
    nenhum extra — o peso está nos objetos que **você** injeta, e cada um
    mantém o próprio carregamento preguiçoso.

## O primeiro agente

```python
from typing import Any

from tempest_fastapi_sdk.agents import Agent, AgentContext, text_tool
from tempest_fastapi_sdk.genai import TextGenerator


async def get_weather(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Return the weather for a city."""
    return f"{arguments['city']}: 22 graus, céu limpo"


tool = text_tool(
    "get_weather",
    "Get the current weather for a city.",
    get_weather,
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name."}},
        "required": ["city"],
    },
)

agent = Agent(TextGenerator("Qwen/Qwen2.5-0.5B-Instruct"), tools=[tool])
run = await agent.run("Qual o tempo no Recife? Use a ferramenta.")

print(run.output)
print(run.tool_calls)
print([(step.kind, step.name) for step in run.steps])
```

```text
The weather in Recife is 22 degrees, clear sky.
['get_weather']
[('model', 'chat'), ('tool', 'get_weather'), ('model', 'chat')]
```

Três passos: o modelo pediu a ferramenta, a ferramenta rodou, o modelo leu o
resultado e respondeu. Tudo isso num modelo de 0.5B rodando em CPU.

!!! tip "A descrição da ferramenta é o que importa"
    O modelo escolhe pelo `description` — é o único texto que ele lê sobre a
    ferramenta. Vale mais cuidado ali que na implementação.

## Sempre olhe o `stop_reason`

```python
run = await agent.run("uma tarefa longa")
if not run.succeeded:
    print("truncado:", run.stop_reason)
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

```python
from tempest_fastapi_sdk.agents import Agent, AgentBudget

agent = Agent(
    generator,
    tools=tools,
    budget=AgentBudget(max_steps=8, max_seconds=90, max_tool_calls=5),
)
```

Passos sozinhos **não** limitam uma execução: uma chamada de ferramenta pode
travar, e aí o agente fica parado sem estourar passo nenhum. Por isso o
relógio também é verificado, e por isso `max_seconds` tem default (120s) em
vez de ser opcional.

## Ferramentas sobre os modelos locais

Aqui é onde o módulo encosta no resto do SDK:

```python
from tempest_fastapi_sdk.agents import (
    Agent,
    describe_image_tool,
    generate_image_tool,
    retrieve_tool,
    speak_tool,
    transcribe_audio_tool,
    web_search_tool,
)

agent = Agent(
    generator,
    tools=[
        generate_image_tool(image_generator, default_steps=4),
        describe_image_tool(vision_generator),
        transcribe_audio_tool(speech_to_text),
        speak_tool(text_to_speech),
        retrieve_tool(retriever),
        web_search_tool(web_search),
    ],
)
```

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

```python
run = await agent.run(
    "Desenhe uma bicicleta vermelha como bike.png e depois me diga o que "
    "aparece na imagem que você criou.",
)
for step in run.steps:
    print(step.kind, step.name, step.artifacts)
print(run.artifact("bike.png").media_type)
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

```python
from tempest_fastapi_sdk.agents import AgentToolError


async def save(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Save something, or explain why it could not be saved."""
    raise AgentToolError("disco cheio")
```

O passo fica marcado com `error`, e a mensagem volta **para o modelo** como
observação. Ele costuma tentar outro caminho. Levantar a exceção para cima
jogaria fora todo o trabalho anterior da execução.

```python
failed = [step for step in run.steps if step.error]
print(failed[0].error)
```

```text
AgentToolError: disco cheio
```

Qualquer exceção do handler é tratada igual — a diferença de usar
`AgentToolError` é só deixar a intenção explícita.

## Escrever a sua própria ferramenta

```python
from typing import Any

from tempest_fastapi_sdk.agents import (
    AgentArtifact,
    AgentContext,
    AgentTool,
    ToolResult,
)


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


tool = AgentTool(
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
```

O handler recebe **dois** argumentos: `arguments` (o que o modelo passou) e
`context` (os artefatos da execução). Devolver `str` também vale, quando não
há nada binário — ele é embrulhado num `ToolResult` automaticamente.

!!! tip "Já tem ferramentas do `AIChatPipeline`?"
    `AgentTool.from_tool(tool)` adapta as ferramentas de um só argumento do
    chat pipeline, sem tocar nelas.

## Servir por HTTP

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryAgentRunSink,
    make_agent_router,
)

store = InMemoryAgentRunSink(max_runs=50)
agent = Agent(generator, tools=tools, run_sink=store)

app = FastAPI()
app.include_router(make_agent_router(agent, run_store=store))
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

## Guardar as execuções

Por padrão nada é guardado: a execução volta para quem chamou e acabou.

```python
from tempest_fastapi_sdk.agents import InMemoryAgentRunSink

store = InMemoryAgentRunSink(max_runs=100)
agent = Agent(generator, tools=tools, run_sink=store)
```

O buffer é **limitado de propósito** — execuções carregam seus artefatos, e
uma lista infinita de execuções que geram imagem é um vazamento de memória
com pavio lento.

Para persistir de verdade:

```python
from tempest_fastapi_sdk.agents import DbAgentRunSink, make_agent_run_model

model = make_agent_run_model(tablename="agent_runs")
agent = Agent(generator, tools=tools, run_sink=DbAgentRunSink(db, model))
```

!!! note "A tabela guarda o traço, não os bytes"
    Artefatos são megabytes; uma tabela de execuções não é blob store. O que
    fica são os **nomes** e os tipos, então quem lê sabe o que foi produzido
    e vai buscar onde você guardou.

Qualquer callable `async` que aceite um `AgentRun` serve como sink — mandar
para um log, uma fila ou um bucket é uma linha. Falha do sink **nunca**
derruba a execução: o trabalho já foi feito e quem chamou já tem a resposta.

## Moderação

```python
agent = Agent(generator, tools=tools, moderator=moderator)
run = await agent.run("algo proibido")
print(run.stop_reason, run.output)
```

```text
blocked blocked by moderation (toxicity)
```

O objetivo é checado **antes** de o modelo ver qualquer coisa, e a resposta
antes de voltar. Recusa vira `StopReason.BLOCKED`, não exceção.

## Acompanhar em tempo real

```python
async for step in agent.stream("Desenhe um gato e descreva"):
    print(step.index, step.kind, step.name, step.error or step.output[:60])
```

A execução só é finalizada (e mandada ao sink) quando o iterador se esgota.
Abandonar no meio não deixa registro — que é o certo para uma requisição
cancelada.

## Delegar para outro agente

Não existe objeto "time" aqui, e isso é o design: um agente já sabe escolher
uma ferramenta pelo nome e ler o que ela devolve, então o jeito mais barato
de passar trabalho a um especialista é **transformar o especialista numa
ferramenta**.

```python
from tempest_fastapi_sdk.agents import Agent, agent_tool, web_search_tool

researcher = Agent(
    generator,
    tools=[web_search_tool(web_search)],
    name="researcher",
)
writer = Agent(
    generator,
    tools=[agent_tool(researcher, description="Pesquise um tema na web.")],
    name="writer",
)

run = await writer.run("Escreva um resumo sobre PIX.")
for step in run.steps:
    print(step.kind, step.name, len(step.children))
```

```text
model chat 0
agent ask_researcher 3
model chat 0
```

O passo da delegação é `agent`, não `tool` — e o traço do filho fica
**aninhado** nele, em `children`. Uma delegação é o único passo que pode
custar tanto quanto uma execução inteira; ler um traço onde o passo caro
parece uma chamada de função é como você interpreta errado para onde foi o
tempo. `step.total_steps` conta a subárvore.

### Três guardas que a delegação precisa

| Guarda | Por quê |
| --- | --- |
| **Relógio herdado** | O filho pode terminar antes do próprio orçamento, mas **nunca** depois do relógio do pai — é o pai que está segurando uma requisição aberta. Vale o menor dos dois. |
| **Profundidade limitada** | Nada impede o modelo de fazer A delegar para B que delega para A. `max_depth` (3 por padrão) transforma isso numa recusa que o modelo lê e contorna. |
| **Artefatos com prefixo** | O que o filho produz sobe para o pai como `researcher/report.md`. Dois especialistas escrevendo `report.md` não se sobrescrevem. |

```python
run = await writer.run("...")
print([a.name for a in run.artifacts])
```

```text
['illustrator/chart.png', 'researcher/notes.md']
```

!!! warning "O filho truncado é sinalizado, não escondido"
    Se o sub-agente parar por orçamento, o texto que volta ao pai começa com
    `[stopped: timeout]`. Um pai que recebesse só a resposta parcial a
    apresentaria como completa.

Vários especialistas de uma vez:

```python
from tempest_fastapi_sdk.agents import Agent, team_tools

coordinator = Agent(
    generator,
    tools=team_tools({
        researcher: "Pesquise fatos na web.",
        illustrator: "Desenhe imagens a partir de uma descrição.",
    }),
    name="coordinator",
)
```

!!! tip "A descrição é o que o coordenador usa para escolher"
    Passar a descrição junto de cada agente é a forma que mantém as
    descrições legíveis lado a lado — e é só nelas que o coordenador se
    baseia para decidir a quem entregar.

## Loop: insistir até passar num critério

Uma execução para quando o **modelo** diz que terminou. Isso costuma
significar "sem mais ideias", não "está bom".

```python
from tempest_fastapi_sdk.agents import AgentRun, run_until

def parses(run: AgentRun) -> bool:
    """Accept only output that is valid JSON."""
    import json
    try:
        json.loads(run.output)
    except ValueError:
        return False
    return run.succeeded

result = await run_until(
    agent,
    "Devolva os dados como JSON.",
    until=parses,
    max_rounds=4,
    max_seconds=120,
)
print(result.accepted, result.rounds, result.output)
```

O predicado é onde está o valor: um teste que **executa** a saída — faz o
parse, importa o módulo, chama o endpoint — é uma barreira muito mais dura
que perguntar ao modelo se ele está satisfeito. É por isso que esse laço
consegue melhorar sobre uma execução única.

Cada rodada seguinte vê a tentativa rejeitada:

```text
Devolva os dados como JSON.

Your previous attempt was rejected. It was:

Aqui estão os dados: nome=João, idade=30

Produce a different and better answer.
```

Um modelo que não enxerga a própria tentativa anterior tende a reproduzi-la.
Passe `feedback=` para escrever esse texto do seu jeito.

!!! danger "`accepted=False` significa que nada passou"
    Ficar sem rodadas não é aprovação. `result.output` é a melhor tentativa,
    não uma resposta validada — cheque `result.accepted`, não só o texto.

## Loop: gerar, criticar, revisar

Um segundo agente lendo a saída do primeiro pega o que o autor não pega —
pelo mesmo motivo que code review funciona com gente.

```python
from tempest_fastapi_sdk.agents import refine

result = await refine(writer, reviewer, "Escreva as notas de release.")

print(result.accepted, result.rounds)
for iteration in result.iterations:
    print(iteration.index, iteration.accepted, iteration.critique)
```

```text
True 2
0 False Vago demais sobre a mudança incompatível; cite a versão.
1 True None
```

O crítico aprova respondendo exatamente `APPROVED`. Um crítico solto hesita
— "está bom, embora você pudesse considerar..." é impossível de ramificar.
Uma palavra reservada torna a decisão legível por máquina e deixa a
**rejeição** livre, que é a metade que precisa ser expressiva.

!!! note "O crítico não reescreve"
    Obrigá-lo a descrever a correção mantém o trabalho (e a
    responsabilidade por ele) com o worker. Um crítico que reescreve acaba
    contrabandeando os próprios erros sem revisão.

!!! warning "Custo multiplica"
    `rounds` execuções de um agente com N passos são `rounds * N` chamadas
    de modelo. É exatamente o ponto desses laços — e por isso todos exigem
    um teto duro.

## Recapitulando

- **`Agent.run(goal)`** devolve `AgentRun`: resposta, traço, artefatos e
  **por que parou**.
- **`AgentBudget`** limita passos, tempo e chamadas; o tempo é o que de fato
  protege uma requisição.
- **Ferramentas prontas** cobrem imagem, visão, áudio, RAG e web sobre os
  modelos que você já hospeda.
- **Artefatos nomeados** encadeiam multimodal sem disco e sem base64.
- **Erro de ferramenta vira observação** para o modelo, não exceção.
- **Persistência é opt-in** — memória por padrão, ORM quando quiser.
- **`agent_tool`** transforma um agente em ferramenta de outro; o relógio é
  herdado, a profundidade é limitada e o traço do filho fica aninhado.
- **`run_until`** insiste até o seu predicado aceitar; **`refine`** usa um
  crítico. Nos dois, `accepted=False` quer dizer que nada passou.

Onde continuar: [IA generativa self-hosted](genai.md) para os modelos em si,
[Geração de imagem](image-generation.md) e
[Pesos de modelos](model-weights.md) para fixar o que o agente usa.
