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

## Ferramentas tipadas com Pydantic

Escrever JSON-schema à mão ao lado do handler significa **duas descrições da
mesma coisa**, que divergem na primeira edição: o schema diz `city`, o
handler lê `arguments["town"]`, e nada acusa até um modelo chamar a
ferramenta. O decorator `@tool` elimina a duplicata.

```python
from pydantic import Field

from tempest_fastapi_sdk.agents import AgentContext, tool
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str = Field(description="Cidade a consultar.")
    days: int = Field(default=1, ge=1, le=7, description="Horizonte em dias.")


@tool("get_weather", "Get the current weather for a city.")
async def get_weather(args: WeatherArgs, context: AgentContext) -> str:
    """Return the forecast for the requested city."""
    return f"{args.city}: 22 graus, {args.days}d"
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

```python
from tempest_fastapi_sdk.agents import typed_tool

built = typed_tool("get_weather", "Get the weather.", WeatherArgs, get_weather_impl)
```

## Saída estruturada: um objeto, não um parágrafo

Um agente que termina em prosa serve para chat e não serve para pipeline —
algo lá na frente tem que transformar "a nota totaliza R$ 1.240,50 e vence
dia 15" de volta em campos, e isso quebra no dia em que o modelo escrever
diferente.

```python
from pydantic import Field

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherReport(BaseSchema):
    """The structured answer."""

    city: str = Field(description="Cidade reportada.")
    celsius: int = Field(description="Temperatura em celsius.")
    sky: str = Field(description="Condição do céu, uma palavra.")


run = await agent.run_structured("Consulte o tempo no Recife e reporte.", WeatherReport)

if run.has_data:
    print(run.data.city, run.data.celsius)
else:
    print("sem dados:", run.parse_error)
```

```text
Recife 22
```

`run.data` é uma instância do **seu** modelo — `run.data.celsius` é `int`, e
o type-checker sabe disso.

### Por que não pedir JSON e dar parse

O agente ganha uma ferramenta temporária `final_answer` com o formato do seu
modelo, e **chamar essa ferramenta é como o modelo termina**. Os argumentos
*são* a resposta estruturada, já validados, carregados pela mesma máquina de
tool-calling que o resto do agente usa — sem um segundo formato para o modelo
errar.

!!! tip "Modelo pequeno responde em prosa mesmo assim"
    Modelos locais pequenos frequentemente resolvem a tarefa certo e depois
    respondem em texto, ignorando a instrução. Por isso existe uma **passada
    de extração**: quando a prosa não traz JSON, o SDK faz mais uma chamada
    cuja **única** ferramenta é a de resposta, pedindo para reescrever o que
    já foi dito naquele formato. Sem nada mais para chamar e nada mais para
    raciocinar, até um modelo de 0.5B preenche os campos.

    Custa uma chamada extra de modelo — troca certa quando a alternativa é
    perder a execução inteira. Desligue com `extraction_retry=False`.

!!! warning "Sempre cheque `has_data`"
    Uma execução pode ser `succeeded` e ainda assim vir com `data=None` — o
    orçamento acabou, ou nem a extração conseguiu. `run.parse_error` diz
    qual foi o caso. E modelos pequenos às vezes deixam um campo vazio em vez
    de omitir: valide os valores, não só a presença do objeto.

Para insistir até a forma chegar, componha com o laço:

```python
from tempest_fastapi_sdk.agents import run_until, structured_verdict
```

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

## Memória: três camadas, e qual escolher

"O agente precisa lembrar" esconde **três necessidades diferentes**, e
escolher a errada é a razão pela qual memória costuma decepcionar. As três
estão aqui, todas opt-in.

| Camada | Dura | Use quando |
| --- | --- | --- |
| **Scratchpad** | uma execução | Uma execução longa precisa guardar um achado para um passo posterior usar, sem re-derivar nem re-ler. |
| **Fatos** | para sempre, editável | Algo é **verdade** e deve continuar sendo: uma preferência, um id de conta, uma política. Você quer ler e corrigir isso fora do modelo. |
| **Recall** | para sempre, difuso | Conversas passadas *podem* ser relevantes e você não sabe de antemão quais. A busca semântica decide. |

!!! danger "A distinção que mais importa: fato vs recall"
    Um **fato** é afirmado e exato — você lista, edita, apaga, e mostra ao
    usuário o que o sistema acredita sobre ele. **Recall** é recuperado e
    aproximado — traz texto que *parece* relacionado, o que é poderoso e
    **não auditável**.

    Guardar "o plano do usuário é enterprise" no recall significa que
    ninguém consegue corrigir. Guardar uma conversa inteira como fato
    significa que nada de útil volta.

### Scratchpad — dentro da execução

```python
from tempest_fastapi_sdk.agents import Agent, AgentContext, scratchpad, scratchpad_tools

agent = Agent(generator, tools=scratchpad_tools())

context = AgentContext()
run = await agent.run("Some os itens da nota e depois aplique o desconto.", context=context)
print(scratchpad(context))
```

```text
{'subtotal': '1240.50'}
```

O modelo ganha `note_write` / `note_read` / `note_list`. As notas vivem no
`AgentContext.state` e **somem quando a execução termina** — isso é a
feature, não a limitação: uma nota de outra execução aparecendo no meio da
tarefa é pior que nota nenhuma.

Use quando a execução é longa e deriva algo vários passos antes de precisar.
Sem isso o modelo re-deriva (lento, e a segunda resposta pode divergir) ou
carrega tudo na conversa, disputando atenção com o resto.

### Fatos — duráveis e editáveis

```python
from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryFactStore,
    fact_tools,
    facts_prompt,
)

store = InMemoryFactStore()
await store.put("timezone", "America/Recife", subject="user-42")

agent = Agent(
    generator,
    tools=fact_tools(store, subject="user-42"),
    system_prompt=BASE_PROMPT + await facts_prompt(store, subject="user-42"),
)
```

O bloco injetado no prompt:

```text
What you already know:
- timezone: America/Recife
```

!!! tip "Injetar vence obrigar o modelo a consultar"
    Quando o modelo percebe que precisa saber o fuso, ele normalmente já
    respondeu no fuso errado. Fatos são curtos e poucos — o custo de prompt
    é pequeno e o modelo não consegue deixar de consultar.

`fact_tools` dá `fact_remember` / `fact_recall` / `fact_list` /
`fact_forget`. Passe `allow_forget=False` quando os fatos são curados em
outro lugar: **um modelo que pode apagar o que discorda, apaga**.

`subject=` isola por usuário ou tenant. Deixar `None` dá um namespace
compartilhado — certo para um agente de propósito único, errado para
qualquer coisa por usuário.

!!! warning "O `InMemoryFactStore` some no restart"
    Que é exatamente o que memória durável não deveria fazer. Ele serve para
    testes e para começar; implemente o `FactStore` sobre a sua tabela antes
    que isso importe. São quatro métodos.

### Recall — semântico, entre execuções

```python
from tempest_fastapi_sdk.agents import Agent, recall_prompt

goal = "Agende uma call com o cliente."
agent = Agent(
    generator,
    system_prompt=BASE_PROMPT + await recall_prompt(chat_memory, goal, user_id="u1"),
)
```

```text
Possibly relevant from earlier conversations:
- eles preferem reuniões de manhã
```

Reaproveita o `ChatMemory` (Chroma/pgvector) que o SDK já tem. Repare no
cabeçalho: **"possivelmente relevante"**. Recall traz o que *parece*
relacionado, e apresentar isso como fato é como um agente passa a afirmar
com convicção algo que ninguém escreveu.

!!! check "Falha de recall não derruba o agente"
    Se o vector store estiver fora, `recall_prompt` devolve string vazia e a
    execução segue. Recall é melhoria, não requisito.

### Combinando

Nada impede usar as três — elas não competem, respondem perguntas
diferentes:

```python
agent = Agent(
    generator,
    tools=[*scratchpad_tools(), *fact_tools(store, subject=user_id)],
    system_prompt=(
        BASE_PROMPT
        + await facts_prompt(store, subject=user_id)
        + await recall_prompt(chat_memory, goal, user_id=user_id)
    ),
)
```

## Skills: capacidades carregadas sob demanda

Toda ferramenta que o agente pode chamar ocupa espaço no prompt, e cada linha
ali custa contexto e dilui atenção. Dez capacidades bem documentadas — cada
uma com suas convenções, seus casos de borda, seu exemplo — é mais instrução
do que um modelo local pequeno consegue segurar, e a qualidade cai **nas
dez**.

Uma **skill** separa o que o modelo precisa para *escolher* do que ele precisa
para *fazer*:

```python
from tempest_fastapi_sdk.agents import Agent, Skill

invoicing = Skill(
    name="invoicing",
    description="Ler e validar notas fiscais brasileiras (NF-e).",
    instructions=INVOICE_GUIDE,          # tão longo quanto precisar
    tools=[parse_nfe, validate_cnpj],
)

agent = Agent(generator, skills=[invoicing])
```

No prompt fica só isto:

```text
- invoicing: Ler e validar notas fiscais brasileiras (NF-e).
```

Quando o modelo decide que a skill se aplica, ele chama `load_skill` e **aí**
recebe as instruções completas — e as ferramentas dela passam a existir.

```python
run = await agent.run("Valide a nota em anexo.")
print(run.tool_calls)
```

```text
['load_skill', 'parse_nfe', 'validate_cnpj']
```

!!! check "As ferramentas da skill ficam escondidas até o load"
    Antes do `load_skill`, `parse_nfe` não aparece na lista de ferramentas
    que o modelo vê — nome e schema não custam nada enquanto não são usados.
    Cem capacidades custam cem linhas curtas, e a que está em uso ganha a
    página inteira.

!!! tip "A descrição é o que decide"
    É o **único** texto que o modelo vê antes de carregar. Diga para que a
    skill serve, não como ela funciona: "ler e validar NF-e" faz o modelo
    escolher certo; "utilitários fiscais diversos" não.

### Skills em arquivo

Para adicionar capacidade sem mexer em código:

```python
from tempest_fastapi_sdk.agents import Agent, discover_skills

agent = Agent(generator, skills=discover_skills("skills/"))
```

Cada `skills/<nome>/SKILL.md`:

```markdown
---
name: invoicing
description: Ler e validar notas fiscais brasileiras (NF-e).
---

O guia completo vai aqui, do tamanho que precisar.
```

É o mesmo formato das skills do Claude Code, então o arquivo serve nos dois
lugares. Ferramentas não vêm de arquivo — são Python — então anexe depois:

```python
skill.tools.append(parse_nfe)
```

!!! note "Diretório ausente não é erro"
    `discover_skills` devolve `[]` quando o diretório não existe, para o
    serviço subir normalmente sem ele.

Para saber o que o agente carregou numa execução:

```python
from tempest_fastapi_sdk.agents import AgentContext, loaded_skills

context = AgentContext()
run = await agent.run("...", context=context)
print(loaded_skills(context))
```

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
