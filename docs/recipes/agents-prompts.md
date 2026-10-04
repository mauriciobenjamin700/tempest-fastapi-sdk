# Agentes de IA (prompts)

O system prompt é o único texto que o modelo lê em **toda** volta do laço, e é
o que menos aparece no código: uma string solta no `Agent(...)` do primeiro
exemplo, e depois um `SERVICE_AGENT_PROMPT` importado de uma pasta `prompts/`
que [a página de arquitetura](agents-architecture.md) cita sem mostrar.

Esta página mostra a pasta, o que vai dentro de cada arquivo, como compor um
prompt que depende de quem pergunta, e como testar que ele chegou ao modelo.

!!! tip "Antes desta página"
    [Agentes de IA](agents.md) até *Ferramentas tipadas com Pydantic*, e
    [Agentes de IA (arquitetura)](agents-architecture.md) para o layout `src/ai/`
    onde a pasta `prompts/` mora.

Nenhum exemplo daqui carrega modelo: todos rodam com o `ScriptedBackend` de
[Agentes de IA (testes)](agents-testing.md), que guarda o que o agente mandou
em cada chamada.

## O que o modelo recebe

Antes de escrever prompt, veja o que de fato chega ao backend. São três
coisas, e só uma delas é o seu `system_prompt`:

```python title="what_the_model_sees.py" hl_lines="35 36 37 42 44 46"
import asyncio
import json

from pydantic import Field

from tempest_fastapi_sdk.agents import Agent, AgentContext, Skill, tool
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies
from tempest_fastapi_sdk.schemas import BaseSchema


class SearchArgs(BaseSchema):
    """Arguments for the catalogue search."""

    name: str = Field(description="Partial match on the service title.")


@tool("search_services", "Search the public service catalogue by name.")
async def search_services(args: SearchArgs, context: AgentContext) -> str:
    """Return a canned catalogue line."""
    return f"1 serviço: {args.name} | Picos/PI"


invoicing = Skill(
    name="invoicing",
    description="Ler e validar notas fiscais (NF-e).",
    instructions="O guia completo da NF-e.",
)


async def main() -> None:
    """Run once against a scripted backend and print what it received."""
    backend = ScriptedBackend([replies("ok")])
    agent = Agent(
        backend,
        tools=[search_services],
        skills=[invoicing],
        system_prompt="Você é o assistente do catálogo de serviços.",
    )
    await agent.run("Tem eletricista em Picos?")

    print("--- system")
    print(backend.system_prompts[0])
    print("--- user")
    print(backend.prompts[0])
    print("--- tools")
    print(backend.specs_seen[0])
    print(json.dumps(search_services.to_spec(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
```

```text
--- system
Você é o assistente do catálogo de serviços.

You have skills available. Each is a set of instructions and tools you can load with the 'load_skill' tool when the task needs it:
- invoicing: Ler e validar notas fiscais (NF-e).
Load a skill before doing work that falls under it. Do not load skills you do not need.
--- user
Tem eletricista em Picos?
--- tools
['search_services', 'load_skill']
{
  "type": "function",
  "function": {
    "name": "search_services",
    "description": "Search the public service catalogue by name.",
    "parameters": {
      "description": "Arguments for the catalogue search.",
      "properties": {
        "name": {
          "description": "Partial match on the service title.",
          "title": "Name",
          "type": "string"
        }
      },
      "required": [
        "name"
      ],
      "type": "object"
    }
  }
}
```

Pedaço por pedaço:

* **`system`** é o seu `system_prompt`, mais o bloco que o SDK **anexa** quando
  o agente tem skills. Você não escreve esse bloco, mas ele está lá — e é por
  isso que vale olhar a saída real em vez de presumir.
* **`user`** é o objetivo passado a `run()`. Muda a cada execução; o prompt
  não.
* **As ferramentas** viajam **fora** do prompt, como specs: nome, a
  `description` do `@tool` e o JSON-schema dos argumentos. Repare que até a
  docstring de `SearchArgs` chegou ao modelo, como `description` dos
  parâmetros. `load_skill` aparece porque o agente tem uma skill.

A consequência prática da terceira linha: **o prompt não precisa repetir o
schema**. O modelo já recebe nome, tipo e descrição de cada argumento. O que o
prompt acrescenta é o que o schema não diz — *quando* usar cada ferramenta.

!!! info "O system é o mesmo em todas as voltas"
    A mensagem `system` é montada uma vez por execução e reenviada sem mudança
    em cada chamada ao modelo. Medido com uma execução de três voltas: os três
    `backend.system_prompts` eram idênticos. Por isso dado que muda no meio da
    execução não pertence ao prompt (mais sobre isso
    [abaixo](#o-que-nao-por-no-prompt)).

## A pasta `prompts/`

Um prompt por agente, e um arquivo com as regras que todos compartilham:

```text
src/ai/prompts/
├── __init__.py   # re-exporta as constantes
├── base.py       # regras comuns a todo agente
├── service.py    # SERVICE_AGENT_PROMPT
└── support.py    # SUPPORT_AGENT_PROMPT
```

```python title="src/ai/prompts/base.py"
"""Text blocks every agent in this service shares."""

TOOL_FAILURE_RULE: str = (
    "Quando uma ferramenta falhar, leia o erro e mude a chamada; "
    "não repita a mesma chamada."
)
NO_GUESSING_RULE: str = (
    "Responda só com o que as ferramentas devolveram. "
    "Se elas não trouxerem a resposta, diga que não encontrou."
)
ANSWER_FORMAT_RULE: str = "Responda em português, em até três frases, sem markdown."

BASE_RULES: str = "\n".join(
    [TOOL_FAILURE_RULE, NO_GUESSING_RULE, ANSWER_FORMAT_RULE],
)
```

```python title="src/ai/prompts/service.py"
"""System prompt of the service-catalogue agent."""

from src.ai.prompts.base import BASE_RULES

SERVICE_AGENT_PROMPT: str = f"""\
Você é o assistente do catálogo de serviços da plataforma.

Escopo: o catálogo público de serviços e os serviços que o próprio usuário publicou.
Para qualquer outro assunto, diga que não pode ajudar com isso.

Ferramentas:
- search_services: perguntas sobre o catálogo público ("tem eletricista em Picos?").
- get_my_services: perguntas sobre o que o próprio usuário publicou.

{BASE_RULES}"""
```

```python title="src/ai/prompts/support.py"
"""System prompt of the support agent."""

from src.ai.prompts.base import BASE_RULES

SUPPORT_AGENT_PROMPT: str = f"""\
Você é o atendimento da plataforma.

Escopo: dúvidas de conta, pagamento e uso do site.
Para dúvidas sobre o catálogo de serviços, diga que o assistente do catálogo responde isso.

Ferramentas:
- search_help: procure no FAQ antes de responder qualquer dúvida de uso.
- open_ticket: só quando o FAQ não resolver e o usuário pedir atendimento humano.

{BASE_RULES}"""
```

```python title="src/ai/prompts/__init__.py"
from src.ai.prompts.base import (
    ANSWER_FORMAT_RULE,
    BASE_RULES,
    NO_GUESSING_RULE,
    TOOL_FAILURE_RULE,
)
from src.ai.prompts.service import SERVICE_AGENT_PROMPT
from src.ai.prompts.support import SUPPORT_AGENT_PROMPT

__all__: list[str] = [
    "ANSWER_FORMAT_RULE",
    "BASE_RULES",
    "NO_GUESSING_RULE",
    "SERVICE_AGENT_PROMPT",
    "SUPPORT_AGENT_PROMPT",
    "TOOL_FAILURE_RULE",
]
```

O que cada escolha compra:

* **Constante de módulo, `UPPER_SNAKE_CASE`.** O prompt é configuração, não
  lógica: importar `SERVICE_AGENT_PROMPT` é o que `src/ai/agents/service.py`
  faz, e um teste importa a mesma constante para afirmar sobre ela.
* **`base.py` separado.** A regra de falha de ferramenta escrita uma vez vale
  para os dois agentes; corrigida uma vez, corrige os dois. O
  [teste](#testar-o-prompt) garante que nenhum prompt esqueceu de incluí-la.
* **`__init__.py` re-exporta.** O resto do serviço importa
  `from src.ai.prompts import SERVICE_AGENT_PROMPT`, nunca do submódulo — a
  mesma regra de import de todo pacote do serviço.
* **Texto do prompt no arquivo, não no banco.** Mudar prompt muda
  comportamento; no arquivo, a mudança passa por revisão e por teste como
  qualquer outra.

Para ver o resultado montado, rode ao lado de `src/`:

```python title="show_prompt.py"
import asyncio

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

from src.ai.prompts import SERVICE_AGENT_PROMPT


async def main() -> None:
    """Print the system message exactly as the backend receives it."""
    backend = ScriptedBackend([replies("ok")])
    await Agent(backend, system_prompt=SERVICE_AGENT_PROMPT).run("oi")

    print(backend.system_prompts[0])


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Você é o assistente do catálogo de serviços da plataforma.

Escopo: o catálogo público de serviços e os serviços que o próprio usuário publicou.
Para qualquer outro assunto, diga que não pode ajudar com isso.

Ferramentas:
- search_services: perguntas sobre o catálogo público ("tem eletricista em Picos?").
- get_my_services: perguntas sobre o que o próprio usuário publicou.

Quando uma ferramenta falhar, leia o erro e mude a chamada; não repita a mesma chamada.
Responda só com o que as ferramentas devolveram. Se elas não trouxerem a resposta, diga que não encontrou.
Responda em português, em até três frases, sem markdown.
```

## Anatomia de um prompt

O `SERVICE_AGENT_PROMPT` acima tem cinco partes. A ordem e a redação são
**julgamento de redação**, não resultado medido: esta página não comparou a
taxa de acerto de um prompt contra outro com modelo de verdade, e você deve
desconfiar de quem afirma isso sem dizer o modelo e o N.

| Parte | Responde | No exemplo |
| --- | --- | --- |
| Papel | quem o modelo é nesta conversa | "assistente do catálogo de serviços" |
| Escopo | o que está dentro e o que fazer com o resto | "Para qualquer outro assunto, diga que não pode ajudar" |
| Quando usar cada ferramenta | a decisão que o schema não carrega | `search_services` para o catálogo público, `get_my_services` para o que é do usuário |
| Falha de ferramenta | o que fazer quando a observação é um erro | `TOOL_FAILURE_RULE` |
| Formato | o que a resposta final deve parecer | `ANSWER_FORMAT_RULE` |

Três coisas aqui **não** são julgamento, são mecânica do SDK:

* **O nome da ferramenta no prompt tem que ser o nome real.** O modelo chama
  pelo nome; um prompt que cita `search_catalog` quando a ferramenta é
  `search_services` ensina o modelo a pedir uma ferramenta que não existe, e a
  observação volta como `unknown tool 'search_catalog'; available: ...`.
* **O erro que o modelo lê é o que a ferramenta escreveu.** A regra de falha
  só funciona se a mensagem do `AgentToolError` disser o que mudar —
  [Agentes de IA (ferramentas)](agents-tools.md#ferramenta-que-chama-uma-api)
  mostra mensagens escritas para isso.
* **Descrição de ferramenta mora no `@tool`, não aqui.** O prompt diz *quando*;
  a `description` e o schema dizem *o quê*. Escrever o *o quê* duas vezes é
  duas descrições que divergem na primeira edição.

## O que o SDK já põe no prompt

### `DEFAULT_SYSTEM_PROMPT`

`Agent(...)` sem `system_prompt=` usa a constante `DEFAULT_SYSTEM_PROMPT`:

```text
You are a capable assistant working towards the user's goal. Use the available tools when they help and answer directly when they do not. When a tool fails, read the error and try a different approach rather than repeating the same call. When you have the answer, reply with it and stop calling tools.
```

Passar `system_prompt=` **substitui** esse texto — não anexa. Medido: com
`system_prompt="X"`, o backend recebe exatamente `"X"`. Se você quer manter
as regras do default e acrescentar as suas, componha:

```python title="extend_default.py"
from tempest_fastapi_sdk.agents import DEFAULT_SYSTEM_PROMPT, Agent
from tempest_fastapi_sdk.agents.testing import ScriptedBackend

SHOP_PROMPT: str = DEFAULT_SYSTEM_PROMPT + "\n\nResponda sempre em português."


def build_agent() -> Agent:
    """Build an agent that keeps the default rules and adds one of its own.

    Returns:
        Agent: The agent, on a scripted backend for the example.
    """
    return Agent(ScriptedBackend([]), system_prompt=SHOP_PROMPT)
```

O `BASE_RULES` da pasta `prompts/` é a mesma ideia com as regras do seu
serviço no lugar das do SDK.

### `skills_prompt`

Com `skills=`, o agente anexa ao seu prompt o bloco de `skills_prompt(skills)`
— o que você viu na primeira saída desta página, começando em
`You have skills available.` O bloco vem **depois** do seu texto, e cada skill
ocupa uma linha: `- nome: descrição`. A instrução inteira da skill só chega
quando o modelo chama `load_skill`
([Skills](agents-advanced.md#skills-capacidades-carregadas-sob-demanda)).

Dois efeitos disso no seu prompt:

* **Não liste as skills de novo.** O bloco já lista; repetir é a mesma
  informação duas vezes no contexto, em toda volta.
* **Uma frase sua sobre *quando* carregar cada skill ainda vale**, pela mesma
  razão do "quando usar cada ferramenta" acima.

### `DEFAULT_CRITIC_PROMPT`

`refine(worker, critic, goal)` usa `DEFAULT_CRITIC_PROMPT` (ou o que você
passar em `critic_prompt=`). Ele **não** vai no system do crítico: entra no
começo da mensagem `user`, seguido de `GOAL:` e `WORK TO REVIEW:`. Medido com
um crítico construído com `system_prompt="C"`: o system recebido foi `C`, e a
mensagem `user` começava com o texto de `DEFAULT_CRITIC_PROMPT`. Um prompt
próprio para o crítico precisa continuar pedindo a palavra exata `APPROVED`
— é por ela que `refine` decide
([Laço gerar, criticar, revisar](agents-advanced.md#loop-gerar-criticar-revisar)).

## Prompt que depende da requisição

`facts_prompt` e `recall_prompt` devolvem blocos de texto para anexar ao
prompt — os fatos do usuário, o que conversas anteriores podem contribuir. Os
dois dependem de **quem** pergunta, e o `Agent` recebe o prompt no construtor:
`run()` não aceita outro. Logo, prompt por requisição é **agente por
requisição**, montado por uma função:

```python title="prompt_per_request.py" hl_lines="25 38 49 50"
import asyncio

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBackend,
    FactStore,
    InMemoryFactStore,
    facts_prompt,
)
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

from src.ai.prompts import SERVICE_AGENT_PROMPT


def build_agent(generator: AgentBackend, prompt: str) -> Agent:
    """Build the agent of one request around the process-wide generator.

    Args:
        generator (AgentBackend): The shared backend; it is reused, never copied.
        prompt (str): The system prompt composed for this request.

    Returns:
        Agent: An agent whose prompt belongs to this request only.
    """
    return Agent(generator, system_prompt=prompt, name="service-agent")


async def prompt_for(user_id: str, facts: FactStore) -> str:
    """Compose the fixed prompt with what is known about this user.

    Args:
        user_id (str): The authenticated caller.
        facts (FactStore): Where durable facts live.

    Returns:
        str: The agent prompt plus the user's facts block.
    """
    return SERVICE_AGENT_PROMPT + await facts_prompt(facts, subject=user_id)


async def main() -> None:
    """Serve two users with one generator and show each prompt's tail."""
    facts = InMemoryFactStore()
    await facts.put("cidade", "Picos/PI", subject="ana")
    await facts.put("cidade", "Teresina/PI", subject="bruno")
    generator = ScriptedBackend([replies("ok"), replies("ok")])

    for user_id in ("ana", "bruno"):
        agent = build_agent(generator, await prompt_for(user_id, facts))
        await agent.run("Tem eletricista perto de mim?")

    for prompt in generator.system_prompts:
        print(prompt.splitlines()[-2:])


if __name__ == "__main__":
    asyncio.run(main())
```

```text
['What you already know:', '- cidade: Picos/PI']
['What you already know:', '- cidade: Teresina/PI']
```

Um gerador, dois agentes, dois prompts. O que é caro — os pesos do modelo —
continua em `runtime.py`, um por processo; o que é construído por requisição
é só o objeto `Agent`, que guarda referências para o gerador e para as
ferramentas que já existiam.

### Quanto custa construir o agente

Medido no mesmo i9-13900F sob WSL2 de
[Quanto custa cada passo](agents.md#quanto-custa-cada-passo), Python 3.11.12,
mediana de 5 rodadas:

```text
8 ferramentas + 3 skills: 3.30 µs (N=100000 x 5)
8 ferramentas, sem skills: 1.39 µs (N=100000 x 5)
facts_prompt + construção: 12.11 µs (N=20000 x 5)
```

O passo de modelo mais barato medido naquela página foi 0,29 s (Ollama,
`qwen2.5:0.5b`, quente). Construir o agente com oito ferramentas tipadas e três
skills custa ~3,3 µs — cinco ordens de grandeza abaixo de uma única volta do
modelo. As skills dobram o custo porque o construtor monta a ferramenta
`load_skill` e concatena o bloco de `skills_prompt`; as ferramentas não são
reconstruídas, só a lista que as referencia.

??? info "O script da medição"

    ```python title="measure_build.py"
    import asyncio
    import statistics
    import timeit
    from collections.abc import Callable

    from pydantic import Field

    from tempest_fastapi_sdk.agents import (
        Agent,
        AgentBudget,
        AgentContext,
        AgentTool,
        InMemoryFactStore,
        Skill,
        facts_prompt,
        tool,
    )
    from tempest_fastapi_sdk.agents.testing import ScriptedBackend
    from tempest_fastapi_sdk.schemas import BaseSchema

    ROUNDS: int = 5


    class SearchArgs(BaseSchema):
        """Arguments shared by the measured tools."""

        query: str = Field(description="What to search for.", max_length=255)
        city: str | None = Field(default=None, description="City name.")
        page: int = Field(default=1, ge=1)


    def make_tool(index: int) -> AgentTool:
        """Build one typed tool, the way a service builds them at import.

        Args:
            index (int): Suffix that keeps the tool names unique.

        Returns:
            AgentTool: The tool.
        """

        @tool(f"tool_{index}", f"Tool {index}: search one domain.")
        async def handler(args: SearchArgs, context: AgentContext) -> str:
            """Echo the query."""
            return args.query

        return handler


    TOOLS: list[AgentTool] = [make_tool(index) for index in range(8)]
    SKILLS: list[Skill] = [
        Skill(
            name=f"skill_{index}",
            description=f"Domain {index}.",
            instructions="x" * 2000,
            tools=[make_tool(100 + index)],
        )
        for index in range(3)
    ]
    GENERATOR: ScriptedBackend = ScriptedBackend([])
    PROMPT: str = "You are the service assistant. " * 20


    def build_agent(prompt: str, *, skills: list[Skill]) -> Agent:
        """Build the per-request agent around the shared generator.

        Args:
            prompt (str): The request's system prompt.
            skills (list[Skill]): The skills to attach.

        Returns:
            Agent: A fresh agent; the generator is shared, not copied.
        """
        return Agent(
            GENERATOR,
            tools=TOOLS,
            skills=skills,
            system_prompt=prompt,
            budget=AgentBudget(max_steps=8, max_seconds=30.0),
        )


    def median_us(call: Callable[[], object], number: int) -> float:
        """Return the median cost of ``call`` in microseconds over ROUNDS rounds.

        Args:
            call (Callable[[], object]): What to time.
            number (int): Calls per round.

        Returns:
            float: Median microseconds per call.
        """
        rounds = [timeit.timeit(call, number=number) / number * 1e6 for _ in range(ROUNDS)]
        return statistics.median(rounds)


    def main() -> None:
        """Time the construction with and without skills, and with facts."""
        loop = asyncio.new_event_loop()
        facts = InMemoryFactStore()
        loop.run_until_complete(facts.put("cidade", "Picos/PI", subject="ana"))

        async def with_facts() -> Agent:
            """Compose the prompt from the store, then build."""
            block = await facts_prompt(facts, subject="ana")
            return build_agent(PROMPT + block, skills=SKILLS)

        with_skills = median_us(lambda: build_agent(PROMPT, skills=SKILLS), 100_000)
        without = median_us(lambda: build_agent(PROMPT, skills=[]), 100_000)
        composed = median_us(lambda: loop.run_until_complete(with_facts()), 20_000)
        print(f"8 ferramentas + 3 skills: {with_skills:.2f} µs (N=100000 x {ROUNDS})")
        print(f"8 ferramentas, sem skills: {without:.2f} µs (N=100000 x {ROUNDS})")
        print(f"facts_prompt + construção: {composed:.2f} µs (N=20000 x {ROUNDS})")
        loop.close()


    if __name__ == "__main__":
        main()
    ```

    Uma segunda execução deu 3,37 / 1,40 / 12,17 µs.

### No serviço

No layout de [arquitetura](agents-architecture.md), a fábrica mora em
`src/ai/agents/` e o controller a chama a cada requisição. O `run_sink` e o
`FactStore` entram por parâmetro, como lá:

```python title="src/ai/agents/service.py"
from tempest_fastapi_sdk.agents import Agent, AgentRunSink, FactStore, facts_prompt

from src.ai.prompts import SERVICE_AGENT_PROMPT
from src.ai.runtime import agent_budget, generator
from src.ai.tools import get_my_services, search_services


async def build_service_agent(
    user_id: str,
    *,
    facts: FactStore,
    run_sink: AgentRunSink | None = None,
) -> Agent:
    """Build the service agent for one caller.

    Args:
        user_id (str): The authenticated caller; their facts join the prompt.
        facts (FactStore): Where durable facts live.
        run_sink (AgentRunSink | None): Where finished runs are recorded.

    Returns:
        Agent: A fresh agent around the process-wide generator and tools.
    """
    prompt = SERVICE_AGENT_PROMPT + await facts_prompt(facts, subject=user_id)
    return Agent(
        generator,
        tools=[search_services, get_my_services],
        system_prompt=prompt,
        budget=agent_budget(),
        run_sink=run_sink,
        name="service-agent",
    )
```

```python title="src/controllers/ai.py"
from tempest_fastapi_sdk.agents import AgentRunSink, FactStore

from src.ai import build_service_agent, context_for
from src.db.models import UserModel
from src.schemas import AgentAnswerResponseSchema, AgentAskRequestSchema


class AIController:
    """Controller for the agent-backed endpoints."""

    def __init__(self, facts: FactStore, run_sink: AgentRunSink | None) -> None:
        """Store the process-wide collaborators.

        Args:
            facts (FactStore): Where durable facts live.
            run_sink (AgentRunSink | None): Where finished runs are recorded.
        """
        self.facts: FactStore = facts
        self.run_sink: AgentRunSink | None = run_sink

    async def ask(
        self,
        user: UserModel,
        data: AgentAskRequestSchema,
    ) -> AgentAnswerResponseSchema:
        """Answer a question with a prompt built for this caller.

        Args:
            user (UserModel): The authenticated caller.
            data (AgentAskRequestSchema): The question.

        Returns:
            AgentAnswerResponseSchema: The answer plus what the run did.
        """
        agent = await build_service_agent(
            str(user.id),
            facts=self.facts,
            run_sink=self.run_sink,
        )
        run = await agent.run(data.question, context=context_for(user.id))
        return AgentAnswerResponseSchema(
            output=run.output,
            succeeded=run.succeeded,
            stop_reason=str(run.stop_reason),
            tool_calls=run.tool_calls,
            seconds=run.seconds,
        )
```

!!! note "`make_agent_router` recebe um agente pronto"
    `make_agent_router(agent, ...)` serve sempre o mesmo `Agent`, logo o mesmo
    prompt. Prompt por requisição pede endpoint próprio, como o controller
    acima — que é o que a [arquitetura](agents-architecture.md#o-endpoint-por-que-nao-make_agent_router)
    já recomenda quando a ferramenta precisa de mais que um id.

## O que não pôr no prompt

**Identidade e permissão.** "O usuário é o 42, só mostre os serviços dele" é
uma frase, e o modelo pode ignorar uma frase. Quem pergunta viaja no
`AgentContext` e a ferramenta o lê com `require_user_id`
([`policy.py`](agents-architecture.md#policypy-identidade-nunca-e-argumento)).
Um fato no prompt (`cidade: Picos/PI`) ajuda o modelo a responder; ele não
**restringe** o que a ferramenta devolve.

**Dado que muda dentro da execução.** O system é o mesmo em todas as voltas de
uma execução ([medido acima](#o-que-o-modelo-recebe)). Estoque, saldo, status
de um pedido: o que pode mudar enquanto o agente trabalha é resposta de
ferramenta, lida no momento em que o modelo precisa.

**O schema das ferramentas.** Já vai nas specs, como a primeira saída desta
página mostrou.

**Segredo.** O prompt é texto que o modelo lê, e nada no SDK impede o modelo de
repeti-lo na resposta. Chave de API e credencial ficam no código da ferramenta.

## Testar o prompt

O prompt é uma constante, então o teste dele é rápido — e dá para afirmar o
que chegou ao modelo pelo `ScriptedBackend`:

```python title="test_prompts.py" hl_lines="12 13 26 27"
import pytest

from tempest_fastapi_sdk.agents import InMemoryFactStore
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

from prompt_per_request import build_agent, prompt_for
from src.ai.prompts import BASE_RULES, SERVICE_AGENT_PROMPT, SUPPORT_AGENT_PROMPT


def test_every_agent_prompt_carries_the_base_rules() -> None:
    """A prompt that forgot the shared rules is a drift, caught here."""
    assert BASE_RULES in SERVICE_AGENT_PROMPT
    assert BASE_RULES in SUPPORT_AGENT_PROMPT


@pytest.mark.asyncio
async def test_each_request_sees_only_its_own_facts() -> None:
    """Ana's facts must not reach the run started for Bruno."""
    facts = InMemoryFactStore()
    await facts.put("cidade", "Picos/PI", subject="ana")
    generator = ScriptedBackend([replies("ok"), replies("ok")])

    await build_agent(generator, await prompt_for("ana", facts)).run("oi")
    await build_agent(generator, await prompt_for("bruno", facts)).run("oi")

    assert "Picos/PI" in generator.system_prompts[0]
    assert generator.system_prompts[1] == SERVICE_AGENT_PROMPT
```

```bash
pytest test_prompts.py -q
```

```text
..                                                                       [100%]
2 passed in 0.51s
```

O segundo teste afirma uma coisa que só a saída real mostra: sem fato
nenhum, `facts_prompt` devolve string vazia e o prompt de Bruno é
**exatamente** o `SERVICE_AGENT_PROMPT`, sem cabeçalho órfão.

!!! tip "O que esse teste não responde"
    Se o modelo **segue** o prompt — escolhe a ferramenta que o prompt manda,
    respeita o formato — é pergunta para o modelo de verdade, numa suíte
    separada e marcada:
    [E o modelo de verdade?](agents-testing.md#e-o-modelo-de-verdade)

## Recapitulando

- **O modelo recebe três coisas**: o system (seu prompt mais o que o SDK
  anexa), o objetivo e as specs das ferramentas. `ScriptedBackend` mostra as
  três sem modelo.
- **`src/ai/prompts/`** tem um arquivo por agente e um `base.py` com as regras
  comuns, em constantes `UPPER_SNAKE_CASE` re-exportadas no `__init__.py`.
- **O prompt diz *quando*; o `@tool` diz *o quê*.** Nome de ferramenta citado
  no prompt tem que ser o nome real.
- **`system_prompt=` substitui o `DEFAULT_SYSTEM_PROMPT`**; componha com ele
  se quiser manter as regras do SDK. Skills anexam o próprio bloco depois do
  seu texto.
- **Prompt por requisição é agente por requisição**: uma função monta o
  `Agent` em volta do gerador do processo. Medido, custa ~3,3 µs com oito
  ferramentas e três skills.
- **Identidade, permissão, dado volátil e segredo ficam fora do prompt.**
- **Teste a constante e o que chegou ao modelo**; se o modelo obedece é outra
  suíte.

Veja também: [Agentes de IA (ferramentas)](agents-tools.md) para escrever a
mensagem de erro que a regra de falha manda o modelo ler, e
[Agentes de IA (avançado)](agents-advanced.md#memoria-tres-camadas-e-qual-escolher)
para os blocos de `facts_prompt` e `recall_prompt`.
