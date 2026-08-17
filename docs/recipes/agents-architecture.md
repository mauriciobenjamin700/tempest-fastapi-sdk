# Agentes de IA (arquitetura)

Uma ferramenta e um agente cabem confortavelmente num arquivo só. O **segundo**
agente não: ele quer as mesmas ferramentas com outro prompt, e de repente o
arquivo que era claro vira o lugar onde tudo mora. Esta página é o layout que
sobrevive ao terceiro — e a razão de cada pasta existir.

!!! tip "Antes desta página"
    [Agentes de IA](agents.md) para o `@tool` e o laço, e
    [Agentes de IA (banco de dados)](agents-db.md) para a sessão dentro da
    ferramenta. Aqui montamos o serviço inteiro em volta disso.

## O layout

```text
src/
├── ai/
│   ├── runtime.py        # o modelo do processo + o orçamento das execuções
│   ├── policy.py         # quem está perguntando, e como uma ferramenta pede
│   ├── schemas/          # os argumentos que o modelo pode escolher
│   ├── views/            # o texto que o modelo lê de volta
│   ├── prompts/          # um system prompt por agente
│   ├── tools/            # por domínio, espelhando services/
│   └── agents/           # composição: modelo + ferramentas + prompt
├── controllers/          # orquestra; é quem chama agent.run
├── services/             # a regra de negócio que a ferramenta consome
└── db/
    └── manager.py        # o AsyncDatabaseManager do processo
```

`ai/` fica **ao lado** de `services` e `controllers`, não dentro. Um agente não
é um detalhe de um domínio: ele atravessa vários.

## A regra que sustenta o resto: a direção dos imports

| Camada | Pode importar | Nunca importa |
| --- | --- | --- |
| `api/routers` | `controllers`, `schemas` | `ai`, `db` |
| `controllers` | `ai`, `services`, `schemas` | `api` |
| `ai` | `services`, `schemas`, `db/manager` | `api`, `controllers`, `db/repositories` |
| `services` | `db/repositories` | `ai` |

Duas consequências valem ser ditas em voz alta:

* **O router não fala com o agente.** Quem chama `agent.run` é um controller —
  ele semeia a identidade, decide o que fazer com o `stop_reason` e traduz o
  `AgentRun` para a resposta HTTP. No dia em que a mesma pergunta vier de uma
  task ou de um consumer, o ponto de entrada já existe.
* **Uma ferramenta não conversa com repositório.** Ela passa por um service,
  como todo mundo. O que `ai` importa de `db` é só o manager — infraestrutura
  compartilhada, não a camada de dados.

## `runtime.py` — o modelo pertence ao processo

```python title="src/ai/runtime.py"
from tempest_fastapi_sdk.agents import AgentBudget
from tempest_fastapi_sdk.genai import TextGenerator

from src.core.settings import settings

generator: TextGenerator = TextGenerator(
    settings.AI_MODEL_ID,
    local_files_only=settings.AI_LOCAL_FILES_ONLY,
    idle_unload_seconds=settings.AI_IDLE_UNLOAD_SECONDS,
)


def agent_budget() -> AgentBudget:
    """Return the ceilings every agent run is held to.

    Returns:
        AgentBudget: Steps, wall-clock seconds and tool calls, from settings.
    """
    return AgentBudget(
        max_steps=settings.AI_MAX_STEPS,
        max_seconds=settings.AI_MAX_SECONDS,
        max_tool_calls=settings.AI_MAX_TOOL_CALLS,
    )
```

Um `TextGenerator` carrega pesos. Construir um dentro de `build_agent()` parece
inofensivo enquanto existe um agente — no dia do segundo, são duas cópias do
modelo na memória. Com 0.5B ninguém percebe; com um 7B quantizado são
gigabytes.

!!! tip "`idle_unload_seconds` devolve memória de graça"
    Numa API que responde muito mais HTTP comum do que pergunta de agente, o
    modelo se descarrega sozinho entre conversas e recarrega na próxima. Não
    custa uma linha de código a mais — custa um campo de settings.

!!! warning "O orçamento é o que protege a requisição"
    `max_seconds` sempre **abaixo** do timeout do proxy. Acima dele o cliente
    já desistiu e a GPU continua trabalhando para ninguém.

## `tools/` separado de `agents/`

A ferramenta é a unidade **reutilizável**; o agente é uma **composição**. Um
agente de suporte vai querer `search_services` sem herdar o prompt do agente de
catálogo — e é isso que fica impossível quando os dois moram no mesmo arquivo.

`tools/` espelha `services/`: quem conhece `services/job.py` encontra
`tools/service.py` sem procurar.

## `schemas/` — o contrato do modelo não é o contrato HTTP

O filtro de paginação do seu endpoint é um péssimo argumento de ferramenta.
Ele carrega ids que o modelo não pode adivinhar, flags mutuamente exclusivas
cuja combinação errada levanta, e pisos de tamanho que transformam um palpite
razoável em erro de validação.

```python title="src/ai/schemas/service.py"
from pydantic import Field
from tempest_fastapi_sdk import BaseSchema, CityNameField, UFField


class ServiceSearchArgsSchema(BaseSchema):
    """Arguments for searching the public service catalogue."""

    name: str | None = Field(
        default=None,
        description="Partial match on the service title. Omit to skip it.",
        max_length=255,
    )
    state: UFField | None = Field(
        default=None,
        description="Two-letter state code, e.g. 'PI'.",
    )
    city: CityNameField | None = Field(
        default=None,
        description="City the service is offered in, e.g. 'Picos'.",
        max_length=255,
    )
    page: int = Field(
        default=1,
        ge=1,
        description="1-indexed page. Use it only to see more of the same search.",
    )
```

O que o modelo **não** pode escolher simplesmente não aparece aqui — e é
travado na tradução para o filtro do domínio:

```python title="src/ai/tools/_filters.py"
from src.schemas import ServicePaginationFilterSchema

PAGE_SIZE: int = 5


def service_filters(
    *,
    name: str | None = None,
    state: str | None = None,
    city: str | None = None,
    page: int = 1,
) -> ServicePaginationFilterSchema:
    """Translate what the model chose into the domain filter.

    Returns:
        ServicePaginationFilterSchema: The filter, with the fields the model
            may not choose pinned here — active rows only, no embedded
            candidates, and a page small enough not to flood the context.
    """
    return ServicePaginationFilterSchema(
        name=name,
        state=state,
        city=city,
        page=page,
        page_size=PAGE_SIZE,
        is_active=True,
        include_candidates=False,
    )
```

A regra é curta: **campo no schema = escolha do modelo.** Segurança, privacidade
e limite de tamanho ficam na tradução, onde nenhuma frase bem construída
alcança.

## `views/` — o que o modelo lê de volta

```python title="src/ai/views/service.py"
from src.schemas import ServicePaginationSchema

EMPTY_SEARCH: str = (
    "Nenhum serviço encontrado com esses filtros. "
    "Tente relaxar um de cada vez: primeiro a cidade, depois o estado."
)


def render_services(page: ServicePaginationSchema, *, empty: str) -> str:
    """Flatten a page of services into the few lines a model needs.

    Args:
        page (ServicePaginationSchema): The page the service layer returned.
        empty (str): What to say when nothing matched — it doubles as the
            model's instruction on what to try next.

    Returns:
        str: The totals, one line per service, and an explicit next-page hint.
    """
    if not page.items:
        return empty

    lines: list[str] = [f"{page.total} serviço(s) — página {page.page} de {page.pages}."]
    lines.extend(f"- {item.name} | {item.city}/{item.state}" for item in page.items)
    if page.page < page.pages:
        lines.append(f"Há mais resultados: chame de novo com page={page.page + 1}.")
    return "\n".join(lines)
```

Três coisas ganhas por isolar isto numa função pura:

* **um lugar só decide o que sai.** Endereço, telefone, e-mail — a decisão de
  não vazar mora num arquivo, não espalhada por N ferramentas;
* **testa sem agente, sem modelo e sem banco.** Schema entra, string sai;
* **ajustar é barato.** "O modelo está confundindo cidade com estado" vira uma
  edição de formato, não uma mudança na ferramenta.

!!! warning "O retorno da ferramenta é lido pelo usuário"
    O modelo repete o que a ferramenta devolveu. Devolver o schema de resposta
    inteiro publica no chat todo campo que ele contém.

## `policy.py` — identidade nunca é argumento

```python title="src/ai/policy.py"
from uuid import UUID

from tempest_fastapi_sdk.agents import AgentContext, AgentToolError

USER_ID_KEY: str = "user_id"


def context_for(user_id: UUID) -> AgentContext:
    """Build the context a run started by an authenticated user carries.

    Args:
        user_id (UUID): The caller the authentication dependency resolved.

    Returns:
        AgentContext: A context whose state pins the caller for every tool.
    """
    return AgentContext(state={USER_ID_KEY: user_id})


def require_user_id(context: AgentContext) -> UUID:
    """Return the caller pinned to this run, or fail with a readable message.

    Args:
        context (AgentContext): The run context handed to the tool.

    Returns:
        UUID: The authenticated caller.

    Raises:
        AgentToolError: When the run carries no identity — raised rather than
            returned so the loop records a failed step instead of the tool
            quietly answering about nobody.
    """
    user_id = context.state.get(USER_ID_KEY)
    if not isinstance(user_id, UUID):
        raise AgentToolError("this tool needs an authenticated user")
    return user_id
```

Isso divide as ferramentas em duas famílias, e a diferença fica visível na
primeira linha de cada uma:

| Família | Identidade | Exemplo |
| --- | --- | --- |
| Catálogo | nenhuma | `search_services` |
| Do dono | `require_user_id(context)` | `get_my_services`, `cancel_my_application` |

Se cada ferramenta reimplementasse a leitura, uma delas erraria a chave — e um
erro de digitação aqui **falha aberto**: devolve os dados de outra pessoa em vez
de levantar.

## `agents/` — só composição

```python title="src/ai/agents/service.py"
from tempest_fastapi_sdk.agents import Agent, AgentRunSink

from src.ai.prompts import SERVICE_AGENT_PROMPT
from src.ai.runtime import agent_budget, generator
from src.ai.tools import get_my_services, search_services


def build_service_agent(*, run_sink: AgentRunSink | None = None) -> Agent:
    """Build the agent that answers about services.

    Args:
        run_sink (AgentRunSink | None): Where finished runs are recorded.
            Injected rather than built here, so this module never reaches the
            database layer: the API wires the persistent sink and a test passes
            an in-memory one, or none.

    Returns:
        Agent: The configured agent, sharing the process-wide generator.
    """
    return Agent(
        generator,
        tools=[search_services, get_my_services],
        system_prompt=SERVICE_AGENT_PROMPT,
        budget=agent_budget(),
        run_sink=run_sink,
        name="service-agent",
    )
```

Tudo o que este módulo usa mora em outro lugar. O que sobra é a **escolha** de
quais peças andam juntas — e é isso que faz do segundo agente um segundo
arquivo pequeno, em vez de uma cópia deste.

!!! info "Recursos entram pelas bordas"
    O `run_sink` chega por parâmetro porque persistir é assunto da camada de
    infraestrutura. Se ele fosse construído aqui, `ai` passaria a importar
    `db/models`, e a seta da tabela lá em cima estaria invertida.

O agente é construído **uma vez** e compartilhado. É seguro porque ele não
guarda nada por requisição: a identidade viaja no contexto da execução e cada
ferramenta abre a própria sessão.

## O endpoint: por que não `make_agent_router`

```python title="src/controllers/ai.py"
from tempest_fastapi_sdk.agents import Agent

from src.ai import context_for
from src.db.models import UserModel
from src.schemas import AgentAnswerResponseSchema, AgentAskRequestSchema


class AIController:
    """Controller for the agent-backed endpoints."""

    def __init__(self, agent: Agent) -> None:
        """Store the shared agent.

        Args:
            agent (Agent): The process-wide agent; it holds no request state.
        """
        self.agent: Agent = agent

    async def ask(
        self,
        user: UserModel,
        data: AgentAskRequestSchema,
    ) -> AgentAnswerResponseSchema:
        """Answer a question with the caller pinned to the run.

        Args:
            user (UserModel): The authenticated caller.
            data (AgentAskRequestSchema): The question.

        Returns:
            AgentAnswerResponseSchema: The answer plus what the run did.
                ``succeeded`` is False when a budget truncated it, and the
                answer is partial work rather than a conclusion.
        """
        run = await self.agent.run(data.question, context=context_for(user.id))
        return AgentAnswerResponseSchema(
            output=run.output,
            succeeded=run.succeeded,
            stop_reason=str(run.stop_reason),
            tool_calls=run.tool_calls,
            seconds=run.seconds,
        )
```

O router pronto do SDK chama `agent.run(goal)` **sem contexto** — ele não
conhece a sua autenticação. Um serviço com ferramentas de dono precisa do
endpoint próprio; `make_agent_router` continua ótimo para um agente sem
identidade (um assistente de documentação, um agente interno de suporte).

!!! check "Sempre traduza `stop_reason`"
    Uma execução truncada por orçamento devolve texto — o último que o modelo
    disse. Publicar isso como resposta sem dizer que foi cortada é como
    devolver metade de uma query sem avisar.

## Auditoria: uma linha por execução

```python title="src/api/dependencies/resources.py"
from tempest_fastapi_sdk.agents import Agent, DbAgentRunSink

from src.ai import build_service_agent
from src.db.manager import db
from src.db.models import AgentRunModel

_service_agent = build_service_agent(run_sink=DbAgentRunSink(db, AgentRunModel))


def get_service_agent() -> Agent:
    """Return the process-wide service agent.

    Returns:
        Agent: The shared agent, recording each run to the database.
    """
    return _service_agent
```

Num produto em que o agente fala com usuário final, isso é o que responde "por
que ele disse aquilo?" três dias depois. Adicionar cedo custa uma migration;
adicionar tarde custa a migration **mais** os dados do período em que ninguém
estava gravando.

!!! tip "No painel, somente leitura"
    Registre a tabela no admin com `can_create=False` e `can_edit=False`, e
    todos os campos em `readonly_fields`. É um log: ler é o caso de uso,
    escrever nunca é.

## Latência: onde isso quebra primeiro

Um gerador local é **serializado** — uma geração por vez, por GPU. Um agente de
três passos são três gerações. Dez pessoas perguntando ao mesmo tempo formam
fila, e a décima espera trinta gerações.

Na ordem, do mais barato para o mais caro:

1. **`AgentBudget` amarrado ao timeout** — já está em `runtime.py`.
2. **Um semáforo na frente do agente**, respondendo "ocupado" em vez de
   enfileirar sem limite.
3. **O agente vira uma task** e a resposta volta por SSE. Se o serviço já tem
   fila e SSE, isso é recombinação, não infraestrutura nova.

## Quando crescer: skills antes de multi-agente

1. **Um agente, N ferramentas** — enquanto as descrições couberem no prompt sem
   o modelo se perder. Num modelo pequeno esse teto chega cedo: 5 a 8.
2. **[Skills](agents-advanced.md#skills-capacidades-carregadas-sob-demanda)** —
   as capacidades carregam sob demanda e só o nome fica no prompt. É o passo
   certo quando o teto acima chega.
3. **[Delegação](agents-advanced.md#delegar-para-outro-agente)** — só quando
   houver domínios cujos prompts se contradizem. Custa profundidade, orçamento
   herdado e traço aninhado.

Pular direto para o 3 é o erro comum. A maioria dos serviços vive muito tempo
no 1.

## Recapitulando

- **`ai/` fica ao lado de `services` e `controllers`**, e a direção dos imports
  é o que impede a camada de virar um novelo.
- **`runtime.py` guarda o modelo do processo** — um `TextGenerator` por serviço,
  não por agente.
- **`tools/` separado de `agents/`**: ferramenta é unidade reutilizável, agente
  é composição.
- **`schemas/` é o contrato do modelo**, e o que ele não pode escolher fica na
  tradução para o filtro do domínio.
- **`views/` decide o que o modelo lê** — e, por tabela, o que o usuário final
  vai ler junto.
- **`policy.py` guarda a identidade**, que nunca é argumento.
- **O controller chama `agent.run`**, semeia o contexto e traduz o
  `stop_reason`.
- **O `run_sink` entra pelas bordas** e responde "por que ele disse aquilo?".

Veja também: [Agentes de IA (banco de dados)](agents-db.md) para a sessão dentro
da ferramenta, [Agentes de IA (testes)](agents-testing.md) para exercitar tudo
isso sem carregar modelo, e [Banco de dados](database.md) para o manager
compartilhado.
