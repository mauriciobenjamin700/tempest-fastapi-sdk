# Agentes de IA (avançado)

Esta página continua de onde [Agentes de IA](agents.md) parou. Lá o agente
recebe um objetivo, chama ferramentas e devolve um traço. Aqui ele passa a
devolver **objetos tipados**, a **lembrar** entre execuções, a carregar
**capacidades sob demanda**, a **delegar** para especialistas e a **insistir**
até um critério passar.

Cada seção é independente — leia a que resolve o seu caso.

!!! abstract "O mecanismo por trás destas peças"
    Skills, delegação e memória são todas respostas ao mesmo fato: **cada volta
    do laço reenvia o histórico inteiro ao modelo**, então tudo que está no
    prompt custa em toda chamada. [Agentes: como funcionam por
    dentro](agents-concepts.md) mede esse crescimento e dá a tabela de quando
    usar ferramenta, skill, delegação ou laço.

| Quero que o agente… | Vá para |
| --- | --- |
| devolva um objeto, não um parágrafo | [Saída estruturada](#saida-estruturada-um-objeto-nao-um-paragrafo) |
| lembre de algo | [Memória](#memoria-tres-camadas-e-qual-escolher) |
| tenha muitas capacidades sem inchar o prompt | [Skills](#skills-capacidades-carregadas-sob-demanda) |
| passe trabalho a um especialista | [Delegação](#delegar-para-outro-agente) |
| insista até acertar | [Laços](#loop-insistir-ate-passar-num-criterio) |
| guarde o histórico de execuções | [Guardar as execuções](#guardar-as-execucoes) |

## O setup que as seções reusam

Cada exemplo desta página é um arquivo completo, e todos partem deste:

```python title="advanced_setup.py"
"""Shared setup every example on this page imports."""

from tempest_fastapi_sdk.genai import TextGenerator, TextModel

BASE_PROMPT = (
    "You are a careful assistant. Use the tools when they help, "
    "and say plainly when you cannot answer."
)


def build_generator() -> TextGenerator:
    """The text backend the examples inject into their agents."""
    return TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)
```

## Saída estruturada: um objeto, não um parágrafo

Um agente que termina em prosa serve para chat e não serve para pipeline —
algo lá na frente tem que transformar "a nota totaliza R$ 1.240,50 e vence
dia 15" de volta em campos, e isso quebra no dia em que o modelo escrever
diferente.

```python title="structured_report.py" hl_lines="22"
import asyncio

from advanced_setup import build_generator
from pydantic import Field

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherReport(BaseSchema):
    """The structured answer."""

    city: str = Field(description="Cidade reportada.")
    celsius: int = Field(description="Temperatura em celsius.")
    sky: str = Field(description="Condição do céu, uma palavra.")


async def main() -> None:
    """Ask for a typed object instead of a paragraph."""
    agent = Agent(build_generator())
    run = await agent.run_structured(
        "Consulte o tempo no Recife e reporte.",
        WeatherReport,
    )

    if run.has_data:
        print(run.data.city, run.data.celsius)
    else:
        print("sem dados:", run.parse_error)


if __name__ == "__main__":
    asyncio.run(main())
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

Para insistir até a forma chegar, repita a execução e julgue com
`structured_verdict` — ele aceita só a execução que completou **e** trouxe
dados:

```python title="structured_retry.py" hl_lines="14"
import asyncio

from advanced_setup import build_generator
from structured_report import WeatherReport

from tempest_fastapi_sdk.agents import Agent, structured_verdict


async def main() -> None:
    """Retry until the model produces the shape we asked for."""
    agent = Agent(build_generator())
    for attempt in range(3):
        run = await agent.run_structured(
            "Consulte o tempo no Recife e reporte.",
            WeatherReport,
        )
        if structured_verdict(run):
            print(attempt, run.data)
            return

    print("nenhuma tentativa produziu o objeto")


if __name__ == "__main__":
    asyncio.run(main())
```

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

```python title="scratchpad_run.py" hl_lines="16"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    scratchpad,
    scratchpad_tools,
)


async def main() -> None:
    """Let the run keep a note for a later step to read."""
    agent = Agent(build_generator(), tools=scratchpad_tools())
    context = AgentContext()

    await agent.run(
        "Some os itens da nota e depois aplique o desconto.",
        context=context,
    )
    print(scratchpad(context))


if __name__ == "__main__":
    asyncio.run(main())
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

```python title="facts_run.py" hl_lines="20 21"
import asyncio

from advanced_setup import BASE_PROMPT, build_generator

from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryFactStore,
    fact_tools,
    facts_prompt,
)


async def main() -> None:
    """Seed a durable fact and hand it to the agent through the prompt."""
    store = InMemoryFactStore()
    await store.put("timezone", "America/Recife", subject="user-42")

    agent = Agent(
        build_generator(),
        tools=fact_tools(store, subject="user-42"),
        system_prompt=BASE_PROMPT + await facts_prompt(store, subject="user-42"),
    )
    run = await agent.run("Que horas são boas para uma call amanhã?")

    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
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
    Que é exatamente o que memória durável não deveria fazer. Use-o para
    testes e para começar, e troque por um dos dois abaixo antes que importe.

#### Fatos numa tabela

```python title="facts_db.py" hl_lines="11"
from advanced_setup import build_generator

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.agents import Agent, DbFactStore, fact_tools, make_fact_model

db = AsyncDatabaseManager("postgresql+asyncpg://user:pass@localhost/app")
model = make_fact_model(tablename="agent_facts")
store = DbFactStore(db, model)

user_id = "user-42"
agent = Agent(build_generator(), tools=fact_tools(store, subject=user_id))
```

Escolha esta quando os fatos fazem parte do seu domínio: você quer eles no
backup, no admin, cruzados com o usuário, e legíveis por algo que não é o
agente. Em produção, herde de `BaseFactModel` à mão para a migration
enxergar a tabela estaticamente.

!!! danger "Declare o índice único na migration"
    Um fato é identificado por `(subject, key)`, mas o SDK não pode declarar
    a constraint por você — não sabe se a sua tabela é particionada ou
    compartilhada. Adicione na migration:

    ```sql
    UNIQUE (subject, key)
    ```

    Sem ela, uma corrida entre duas escritas deixa duas linhas, e as leituras
    passam a devolver a que o banco preferir.

#### Fatos no Redis

```python title="facts_redis.py" hl_lines="8"
from redis.asyncio import Redis

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, RedisFactStore, fact_tools

redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
store = RedisFactStore(redis, prefix="agent:facts")

agent = Agent(build_generator(), tools=fact_tools(store, subject="user-42"))
```

Um hash por subject — listar os fatos de alguém é um `HGETALL`, e toda
operação é O(1). Escolha esta quando os fatos são preferências
compartilhadas entre réplicas e uma migration é mais cerimônia do que o dado
merece. Requer o extra `[cache]`.

Os três implementam o mesmo protocolo de quatro métodos, então trocar é
mudança de construtor.

### Recall — semântico, entre execuções

```python title="recall_run.py" hl_lines="19"
import asyncio

from advanced_setup import BASE_PROMPT, build_generator

from tempest_fastapi_sdk.agents import Agent, recall_prompt
from tempest_fastapi_sdk.genai import Embedder, EmbeddingModel
from tempest_fastapi_sdk.genai.rag import ChatMemory


async def main() -> None:
    """Blend possibly-relevant past conversations into the prompt."""
    chat_memory = ChatMemory(Embedder(EmbeddingModel.ALL_MINILM_L6_V2))
    goal = "Agende uma call com o cliente."

    agent = Agent(
        build_generator(),
        system_prompt=(
            BASE_PROMPT
            + await recall_prompt(chat_memory, goal, user_id="u1")
        ),
    )
    run = await agent.run(goal)

    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
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

```python title="memory_combined.py" hl_lines="22 23 24"
import asyncio

from advanced_setup import BASE_PROMPT, build_generator

from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryFactStore,
    fact_tools,
    facts_prompt,
    recall_prompt,
    scratchpad_tools,
)
from tempest_fastapi_sdk.genai import Embedder, EmbeddingModel
from tempest_fastapi_sdk.genai.rag import ChatMemory


async def main() -> None:
    """Use all three layers at once — they answer different questions."""
    store = InMemoryFactStore()
    chat_memory = ChatMemory(Embedder(EmbeddingModel.ALL_MINILM_L6_V2))
    user_id = "user-42"
    goal = "Agende uma call com o cliente."

    agent = Agent(
        build_generator(),
        tools=[*scratchpad_tools(), *fact_tools(store, subject=user_id)],
        system_prompt=(
            BASE_PROMPT
            + await facts_prompt(store, subject=user_id)
            + await recall_prompt(chat_memory, goal, user_id=user_id)
        ),
    )
    run = await agent.run(goal)

    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

## Skills: capacidades carregadas sob demanda

Toda ferramenta que o agente pode chamar ocupa espaço no prompt, e cada linha
ali custa contexto e dilui atenção. Dez capacidades bem documentadas — cada
uma com suas convenções, seus casos de borda, seu exemplo — é mais instrução
do que um modelo local pequeno consegue segurar, e a qualidade cai **nas
dez**.

Uma **skill** separa o que o modelo precisa para *escolher* do que ele precisa
para *fazer*:

```python title="skills_setup.py" hl_lines="34 39"
from typing import Any

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, AgentContext, Skill, text_tool

INVOICE_GUIDE = """
Uma NF-e válida tem chave de 44 dígitos, CNPJ do emitente e valor total.
Rejeite a nota quando a chave não bater com o CNPJ do emitente.
"""


async def parse_nfe_handler(arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Parse an NF-e XML into a readable summary."""
    return f"nota {arguments['key']}: R$ 1.240,50"


async def validate_cnpj_handler(arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Say whether a CNPJ is well-formed."""
    return f"{arguments['cnpj']}: válido"


parse_nfe = text_tool(
    "parse_nfe",
    "Parse an NF-e XML and summarize it.",
    parse_nfe_handler,
    parameters={
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    },
)
validate_cnpj = text_tool(
    "validate_cnpj",
    "Check whether a CNPJ is well-formed.",
    validate_cnpj_handler,
    parameters={
        "type": "object",
        "properties": {"cnpj": {"type": "string"}},
        "required": ["cnpj"],
    },
)

invoicing = Skill(
    name="invoicing",
    description="Ler e validar notas fiscais brasileiras (NF-e).",
    instructions=INVOICE_GUIDE,          # tão longo quanto precisar
    tools=[parse_nfe, validate_cnpj],
)


def build_skilled_agent() -> Agent:
    """An agent that carries the skill without carrying its prompt."""
    return Agent(build_generator(), skills=[invoicing])
```

No prompt fica só isto:

```text
- invoicing: Ler e validar notas fiscais brasileiras (NF-e).
```

Quando o modelo decide que a skill se aplica, ele chama `load_skill` e **aí**
recebe as instruções completas — e as ferramentas dela passam a existir.

```python title="skills_run.py" hl_lines="9"
import asyncio

from skills_setup import build_skilled_agent


async def main() -> None:
    """Watch the model load the skill before using its tools."""
    agent = build_skilled_agent()
    run = await agent.run("Valide a nota em anexo.")

    print(run.tool_calls)


if __name__ == "__main__":
    asyncio.run(main())
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

```python title="skills_from_disk.py" hl_lines="5"
from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, discover_skills

agent = Agent(build_generator(), skills=discover_skills("skills/"))
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

```python title="skills_attach_tool.py" hl_lines="6"
from skills_setup import parse_nfe

from tempest_fastapi_sdk.agents import discover_skills

for skill in discover_skills("skills/"):
    skill.tools.append(parse_nfe)
```

!!! note "Diretório ausente não é erro"
    `discover_skills` devolve `[]` quando o diretório não existe, para o
    serviço subir normalmente sem ele.

Para saber o que o agente carregou numa execução:

```python title="skills_loaded.py" hl_lines="13"
import asyncio

from skills_setup import build_skilled_agent

from tempest_fastapi_sdk.agents import AgentContext, loaded_skills


async def main() -> None:
    """Report which skills the run ended up loading."""
    agent = build_skilled_agent()
    context = AgentContext()

    await agent.run("Valide a nota em anexo.", context=context)
    print(loaded_skills(context))


if __name__ == "__main__":
    asyncio.run(main())
```

## Delegar para outro agente

Não existe objeto "time" aqui, e isso é o design: um agente já sabe escolher
uma ferramenta pelo nome e ler o que ela devolve, então o jeito mais barato
de passar trabalho a um especialista é **transformar o especialista numa
ferramenta**.

```python title="delegation.py" hl_lines="27"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.agents import Agent, agent_tool, web_search_tool
from tempest_fastapi_sdk.genai.rag import SearxngBackend, WebSearch


def build_writer() -> Agent:
    """A writer that can hand research off to a specialist."""
    web_search = WebSearch(
        SearxngBackend("http://localhost:8080", http_client=HTTPClient()),
    )
    researcher = Agent(
        build_generator(),
        tools=[web_search_tool(web_search)],
        name="researcher",
    )
    return Agent(
        build_generator(),
        tools=[agent_tool(researcher, description="Pesquise um tema na web.")],
        name="writer",
    )


async def main() -> None:
    """Delegate, then read the nested trace."""
    run = await build_writer().run("Escreva um resumo sobre PIX.")

    for step in run.steps:
        print(step.kind, step.name, len(step.children))


if __name__ == "__main__":
    asyncio.run(main())
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

```python title="delegation_artifacts.py" hl_lines="9"
import asyncio

from delegation import build_writer


async def main() -> None:
    """Child artifacts arrive prefixed with the child's name."""
    run = await build_writer().run("Escreva um resumo ilustrado sobre PIX.")

    print([artifact.name for artifact in run.artifacts])


if __name__ == "__main__":
    asyncio.run(main())
```

```text
['illustrator/chart.png', 'researcher/notes.md']
```

!!! warning "O filho truncado é sinalizado, não escondido"
    Se o sub-agente parar por orçamento, o texto que volta ao pai começa com
    `[stopped: timeout]`. Um pai que recebesse só a resposta parcial a
    apresentaria como completa.

Vários especialistas de uma vez:

```python title="team.py" hl_lines="21"
from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, generate_image_tool, team_tools
from tempest_fastapi_sdk.genai import ImageGenerator, ImageModel

researcher = Agent(build_generator(), name="researcher")
illustrator = Agent(
    build_generator(),
    tools=[
        generate_image_tool(
            ImageGenerator(ImageModel.SDXL_TURBO),
            default_steps=4,
        ),
    ],
    name="illustrator",
)

coordinator = Agent(
    build_generator(),
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

```python title="run_until_json.py" hl_lines="9 24"
import asyncio
import json

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, AgentRun, run_until


def parses(run: AgentRun) -> bool:
    """Accept only output that is valid JSON."""
    try:
        json.loads(run.output)
    except ValueError:
        return False
    return run.succeeded


async def main() -> None:
    """Keep trying until the output actually parses."""
    agent = Agent(build_generator())
    result = await run_until(
        agent,
        "Devolva os dados como JSON.",
        until=parses,
        max_rounds=4,
        max_seconds=120,
    )

    print(result.accepted, result.rounds, result.output)


if __name__ == "__main__":
    asyncio.run(main())
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

```python title="refine_release_notes.py" hl_lines="18"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, refine


async def main() -> None:
    """Generate, critique, revise — until the reviewer approves."""
    writer = Agent(build_generator(), name="writer")
    reviewer = Agent(
        build_generator(),
        system_prompt=(
            "You review release notes. Reply exactly APPROVED when they are "
            "good enough; otherwise say what is missing."
        ),
        name="reviewer",
    )
    result = await refine(writer, reviewer, "Escreva as notas de release.")

    print(result.accepted, result.rounds)
    for iteration in result.iterations:
        print(iteration.index, iteration.accepted, iteration.critique)


if __name__ == "__main__":
    asyncio.run(main())
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

## Guardar as execuções

Por padrão nada é guardado: a execução volta para quem chamou e acabou.

```python title="run_sink_memory.py" hl_lines="7"
from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, InMemoryAgentRunSink, scratchpad_tools

store = InMemoryAgentRunSink(max_runs=100)
agent = Agent(build_generator(), tools=scratchpad_tools(), run_sink=store)
```

O buffer é **limitado de propósito** — execuções carregam seus artefatos, e
uma lista infinita de execuções que geram imagem é um vazamento de memória
com pavio lento.

Para persistir de verdade:

```python title="run_sink_db.py" hl_lines="12"
from advanced_setup import build_generator

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.agents import (
    Agent,
    DbAgentRunSink,
    make_agent_run_model,
    scratchpad_tools,
)

db = AsyncDatabaseManager("postgresql+asyncpg://user:pass@localhost/app")
model = make_agent_run_model(tablename="agent_runs")
agent = Agent(
    build_generator(),
    tools=scratchpad_tools(),
    run_sink=DbAgentRunSink(db, model),
)
```

!!! note "A tabela guarda o traço, não os bytes"
    Artefatos são megabytes; uma tabela de execuções não é blob store. O que
    fica são os **nomes** e os tipos, então quem lê sabe o que foi produzido
    e vai buscar onde você guardou.

Qualquer callable `async` que aceite um `AgentRun` serve como sink — mandar
para um log, uma fila ou um bucket é uma linha. Falha do sink **nunca**
derruba a execução: o trabalho já foi feito e quem chamou já tem a resposta.

## Moderação

```python title="moderation.py" hl_lines="11"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.genai import RuleModerator


async def main() -> None:
    """A rejected goal stops the run instead of raising."""
    moderator = RuleModerator(["bomba", "veneno"], category="toxicity")
    agent = Agent(build_generator(), moderator=moderator)
    run = await agent.run("Como fabricar uma bomba?")

    print(run.stop_reason, run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
blocked blocked by moderation (toxicity)
```

O objetivo é checado **antes** de o modelo ver qualquer coisa, e a resposta
antes de voltar. Recusa vira `StopReason.BLOCKED`, não exceção.

## Acompanhar em tempo real

```python title="stream_steps.py" hl_lines="10"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, scratchpad_tools


async def main() -> None:
    """Read each step as it lands, instead of waiting for the run."""
    agent = Agent(build_generator(), tools=scratchpad_tools())

    async for step in agent.stream("Some 12 e 30, guarde e depois explique"):
        print(step.index, step.kind, step.name, step.error or step.output[:60])


if __name__ == "__main__":
    asyncio.run(main())
```

A execução só é finalizada (e mandada ao sink) quando o iterador se esgota.
Abandonar no meio não deixa registro — que é o certo para uma requisição
cancelada.

## Recapitulando

- **`run_structured(goal, output=Model)`** devolve uma instância do seu
  modelo; cheque `has_data`, e lembre da passada de extração para modelos
  pequenos.
- **Memória** tem três camadas: scratchpad (uma execução), fatos (duráveis e
  editáveis) e recall (duráveis e difusos). Fato é auditável; recall não.
- **Skills** mantêm o prompt pequeno: só nome e descrição ficam nele.
- **`agent_tool`** faz de um especialista uma ferramenta; relógio herdado,
  profundidade limitada, traço aninhado.
- **`run_until` / `refine`** insistem até o seu predicado — ou um crítico —
  aceitar. `accepted=False` significa que nada passou.
- **Persistência é opt-in**: nada, buffer em memória, ou tabela.

Voltar para a [trilha básica](agents.md).
