# Agentes de IA (banco de dados)

A primeira ferramenta útil que alguém escreve quase nunca é uma consulta ao
tempo — é uma consulta ao **banco da própria aplicação**: "quais serviços
existem em Picos", "quantos pedidos esse cliente tem", "o que está pendente
de aprovação".

E aí aparece a pergunta que esta página responde: **de onde a ferramenta tira
a sessão?**

!!! tip "Antes desta página"
    Leia [Agentes de IA](agents.md) até *Ferramentas tipadas com Pydantic* e
    [Banco de dados](database.md) até *Conectando ao banco*. Aqui os dois se
    encontram.

## `Depends` não alcança a ferramenta

No FastAPI a sessão chega por `Depends(db.session_dependency)`, e a
dependência é resolvida pelo framework quando a requisição entra. Uma
ferramenta de agente não passa por esse caminho:

- o agente é construído **uma vez**, não a cada requisição;
- a execução pode nem vir de HTTP — uma task TaskIQ, um consumer FastStream
  ou um script de linha de comando chamam o mesmo `agent.run(...)`;
- quem invoca a ferramenta é o laço do agente, que só passa os `arguments` e
  o `AgentContext`. Não existe grafo de dependências ali para injetar nada.

Forçar a sessão da requisição até a ferramenta custa caro nos dois lados: o
agente precisaria ser reconstruído a cada requisição (e aí `make_agent_router`
deixa de servir), e a sessão ficaria aberta durante **toda** a execução — que
pode levar minutos e vários passos.

A resposta é mais simples do que parece: **a ferramenta abre a própria
sessão**. É exatamente o que os objetos de banco do próprio SDK fazem —
`DbFactStore` e `DbAgentRunSink` recebem o `AsyncDatabaseManager` e abrem uma
sessão quando precisam.

## O arquivo que roda

```python title="catalog_setup.py" hl_lines="12 32 35 56 57"
import asyncio

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseModel, BaseRepository
from tempest_fastapi_sdk.agents import Agent, AgentContext, tool
from tempest_fastapi_sdk.genai import TextGenerator, TextModel
from tempest_fastapi_sdk.schemas import BaseSchema

db = AsyncDatabaseManager("sqlite+aiosqlite:///catalog.db")


class ServiceModel(BaseModel):
    """A service published in the catalogue."""

    __tablename__ = "services"

    name: Mapped[str] = mapped_column()
    city: Mapped[str] = mapped_column()
    state: Mapped[str] = mapped_column()


class CatalogService:
    """Business logic over the service catalogue."""

    def __init__(self, repository: BaseRepository[ServiceModel]) -> None:
        """Store the repository the service delegates to."""
        self.repository = repository

    @classmethod
    def from_session(cls, session: AsyncSession) -> "CatalogService":
        """Assemble the service over a single database session."""
        return cls(BaseRepository(session, model=ServiceModel))

    async def search(self, city: str | None, limit: int) -> list[ServiceModel]:
        """Return the services published in a city."""
        filters = {"city": city} if city else {}
        page = await self.repository.paginate(filters=filters, page_size=limit)
        return list(page["items"])


class SearchArgs(BaseSchema):
    """Arguments the model may choose when searching the catalogue."""

    city: str | None = Field(
        default=None,
        description="Cidade onde procurar, por exemplo 'Picos'. Omita para não filtrar.",
    )


@tool("search_services", "Search published services by city.")
async def search_services(args: SearchArgs, _context: AgentContext) -> str:
    """Search the catalogue and describe the matches in one line each."""
    async with db.get_session_context() as session:
        catalog = CatalogService.from_session(session)
        found = await catalog.search(args.city, limit=5)

    if not found:
        return "Nenhum serviço encontrado. Tente outra cidade."
    return "\n".join(f"- {item.name} ({item.city}/{item.state})" for item in found)


def build_agent() -> Agent:
    """Build the agent the rest of this page imports."""
    return Agent(
        TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
        tools=[search_services],
        system_prompt=(
            "Você ajuda a encontrar serviços. Use sempre a ferramenta "
            "search_services e nunca invente um serviço que ela não devolveu."
        ),
    )


async def main() -> None:
    """Create the table, seed one row and ask the agent about it."""
    await db.create_tables()
    async with db.get_session_context() as session:
        session.add(ServiceModel(name="Instalação elétrica", city="Picos", state="PI"))

    run = await build_agent().run("Me indica serviços na cidade de Picos.")

    print(run.output)
    print(run.tool_calls)


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python catalog_setup.py
```

```text
Encontrei um serviço em Picos: Instalação elétrica (Picos/PI).
['search_services']
```

!!! info "Cada exemplo desta página é um arquivo que roda"
    Salve o bloco acima como `catalog_setup.py`. Os próximos blocos são
    arquivos completos ao lado dele e importam o que já existe
    (`from catalog_setup import db`), em vez de repetir o setup.

## Bloco a bloco

**O manager mora no módulo.** `db = AsyncDatabaseManager(...)` é criado uma
vez, na importação, e é o mesmo objeto para todas as execuções do agente. Ele
não abre conexão nesse momento — a engine sobe na primeira sessão pedida, ou
no `connect()` do lifespan.

**`from_session` é da sua aplicação, não do SDK.** O classmethod destacado é o
único lugar que sabe de quais repositórios aquele service precisa. No exemplo é
um só; num service real são vários, e é justamente por isso que o SDK não
consegue oferecer um `from_session` genérico — só você conhece a lista.

Vale a pena adotar isso como convenção do projeto: **todo service ganha um
`from_session`**, e passa a ser montável por qualquer consumidor que não tenha
requisição — agente, task, consumer, script, seed.

**O `async with` fica dentro da ferramenta.** Essa é a linha que define o
tempo de vida da sessão, e a próxima seção é inteira sobre ela.

**O que a ferramenta devolve é texto para o modelo ler.** Não é a resposta da
API. Voltamos nisso no final.

## Uma sessão por chamada de ferramenta

Repare onde o `async with` **não** está: nem em volta do `agent.run(...)`, nem
guardado num atributo do agente. Ele abre quando a ferramenta é chamada e
fecha quando ela retorna.

| | Sessão por execução | Sessão por chamada de ferramenta |
| --- | --- | --- |
| Onde fica o `async with` | em volta do `agent.run(...)` | dentro da ferramenta |
| Tempo com a conexão presa | a execução inteira | uma consulta |
| Execução de 40s com 4 passos | 40s de conexão ocupada | ~4 × alguns ms |
| Duas ferramentas na mesma execução | compartilham a sessão | uma sessão cada |

O motivo é o relógio. Uma execução de agente é lenta por natureza: cada passo
espera o modelo gerar tokens. Segurar uma conexão do pool durante toda essa
espera é desperdiçá-la — pior ainda em produção, onde várias execuções
acontecem ao mesmo tempo e o pool tem tamanho fixo.

!!! check "As chamadas de ferramenta são sequenciais"
    O laço do agente executa uma chamada por vez, então uma sessão por
    chamada nunca é usada por duas tarefas concorrentes — que é o que
    quebraria uma `AsyncSession` compartilhada.

## O commit é do context manager

`get_session_context()` **faz commit na saída** e rollback se você levantar
uma exceção. Isso é diferente da sessão da requisição:

| Forma | Commit no sucesso | Para que serve |
| --- | --- | --- |
| `db.session_dependency` | não — o commit é da camada de service | uma requisição HTTP |
| `db.get_session_context()` | **sim**, ao sair do `async with` | ferramenta de agente, task, script |
| `db.get_session()` | não — você fecha na mão | casos fora do comum |

Numa ferramenta de **leitura** isso é indiferente: não há nada para gravar.

!!! warning "Ferramenta que escreve: uma transação por chamada"
    Se a ferramenta grava, cada chamada vira sua própria transação, que
    confirma sozinha ao terminar. Um agente que chama `criar_pedido` e depois
    `debitar_saldo` **não** tem atomicidade entre as duas: a primeira já
    confirmou quando a segunda falha.

    Quando as duas escritas precisam cair juntas, elas pertencem à mesma
    ferramenta — abra uma sessão só e faça as duas lá dentro, deixando o
    service decidir. O agente escolhe *o quê* fazer; a transação continua
    sendo desenhada por você.

## Um manager por processo

O `AsyncDatabaseManager` carrega engine e pool. Dois deles no mesmo processo
são dois pools contra o mesmo banco, e nenhum dos dois sabe do outro:

```python title="wrong_second_manager.py"
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager("postgresql+asyncpg://app:secret@localhost/app")
agent_db = AsyncDatabaseManager("postgresql+asyncpg://app:secret@localhost/app")
```

Na prática isso acontece por acidente: a aplicação já tem o manager em
`resources.py`, e o módulo do agente cria outro "só para as ferramentas". O
consumo de conexões dobra, e o limite do Postgres não foi combinado para
isso. Importe o que já existe:

```python title="app_agent.py"
from fastapi import FastAPI

from catalog_setup import build_agent
from tempest_fastapi_sdk.agents import make_agent_router

app = FastAPI()
app.include_router(make_agent_router(build_agent()))
```

!!! danger "Feche a engine no shutdown"
    `get_session_context()` conecta sozinho na primeira chamada, então uma
    ferramenta funciona mesmo sem `connect()`. O que ela não faz é fechar: sem
    `await db.disconnect()` no lifespan, o processo termina com o pool aberto.
    O ciclo de vida completo está em
    [Banco de dados » Ciclo de vida no lifespan](database.md#ciclo-de-vida-no-lifespan).

## O parâmetro `context`

Toda ferramenta recebe dois argumentos: os `arguments` já validados e um
`AgentContext`. Na busca acima ele não é usado — e tudo bem. A assinatura é
fixa porque o laço chama todas as ferramentas do mesmo jeito.

!!! tip "Convenção"
    Nomeie `_context` quando não usar, como no `catalog_setup.py`. Quem lê
    entende na hora que não há nada escondido ali.

Ele deixa de ser decorativo no minuto em que a ferramenta precisa saber algo
que o **modelo não pode escolher**. É essa a divisão: `arguments` é o que o
modelo decidiu; `context` é o que a sua aplicação sabe.

### Quem está perguntando

O caso mais importante de todos. Se `user_id` fosse um campo do `SearchArgs`,
bastaria o usuário escrever "veja os pedidos do fulano" para o modelo
obedecer. Identidade nunca é argumento de ferramenta.

Você semeia o contexto no endpoint, com o usuário que a autenticação já
resolveu:

```python title="app_ask.py" hl_lines="20"
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI

from catalog_setup import build_agent
from tempest_fastapi_sdk.agents import AgentContext


async def get_current_user_id() -> UUID:
    """Stand-in for the project's real authentication dependency."""
    return UUID("11111111-1111-1111-1111-111111111111")


app = FastAPI()
agent = build_agent()


@app.post("/api/ask")
async def ask(question: str, user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    """Answer a question with the caller's identity pinned to the run."""
    run = await agent.run(question, context=AgentContext(state={"user_id": user_id}))
    return {"output": run.output, "stop_reason": run.stop_reason}
```

E a ferramenta lê de lá, nunca dos argumentos:

```python title="owner_tool.py" hl_lines="16"
from pydantic import Field

from catalog_setup import CatalogService, db
from tempest_fastapi_sdk.agents import AgentContext, AgentToolError, tool
from tempest_fastapi_sdk.schemas import BaseSchema


class MyServicesArgs(BaseSchema):
    """Arguments for listing the caller's own services."""

    city: str | None = Field(default=None, description="Filtro opcional por cidade.")


@tool("get_my_services", "List the services owned by the current user.")
async def get_my_services(args: MyServicesArgs, context: AgentContext) -> str:
    """List the caller's own services, never anyone else's."""
    user_id = context.state.get("user_id")
    if user_id is None:
        raise AgentToolError("no authenticated user in this run")

    async with db.get_session_context() as session:
        catalog = CatalogService.from_session(session)
        found = await catalog.search(args.city, limit=5)

    return "\n".join(f"- {item.name} ({item.city})" for item in found) or "Nada seu por aqui."
```

Lendo de cima para baixo: o endpoint põe `user_id` no `state`; o `state`
acompanha a execução inteira; a ferramenta pega de lá e entrega ao service. O
modelo nunca vê esse valor e não tem como trocá-lo.

!!! warning "`make_agent_router` não semeia contexto"
    O router pronto chama `agent.run(goal)` sem contexto — ele não conhece a
    sua autenticação. Quando a ferramenta precisa saber quem pergunta,
    escreva o endpoint você mesmo, como acima.

### Rascunho compartilhado entre as ferramentas

`state` é um dicionário livre que vive **uma execução** e some no fim. Serve
para uma ferramenta deixar algo para a próxima sem devolver aquilo ao modelo —
um id já resolvido, uma decisão tomada, uma contagem:

```python title="share_state.py"
from uuid import UUID

from tempest_fastapi_sdk.agents import AgentContext


def remember_category(context: AgentContext, category_id: UUID) -> None:
    """Leave the resolved category id for the next tool of this run."""
    context.state["resolved_category_id"] = category_id
```

O próprio SDK usa esse espaço: o bloco de notas
([`scratchpad_tools`](agents-advanced.md#scratchpad-dentro-da-execucao)), as
skills carregadas e a saída estruturada guardam tudo lá. Prefixe suas chaves
com algo do seu domínio para não colidir com as delas.

### Arquivos que uma ferramenta passa para outra

`context.artifacts` é o que permite um agente gerar uma imagem e depois olhar
para ela, sem disco e sem base64 no prompt. Uma ferramenta registra o
`AgentArtifact` no `ToolResult` e a outra recupera pelo nome:

```python title="read_artifact.py"
from tempest_fastapi_sdk.agents import AgentArtifact, AgentContext


def load_report(context: AgentContext) -> AgentArtifact:
    """Read back an artifact an earlier tool registered on this run."""
    return context.require_artifact("relatorio.pdf")
```

`require_artifact` falha com uma mensagem que o modelo consegue ler e
corrigir, em vez de um `KeyError`. O encadeamento completo está em
[Agentes de IA » Encadear multimodal](agents.md#encadear-multimodal-desenhar-e-depois-olhar).

### O relógio da execução

`context.deadline` é o instante (`time.monotonic()`) em que esta execução
precisa parar, já considerando o orçamento deste agente **e** o de quem o
delegou. Uma ferramenta cara pergunta antes de começar:

```python
import time

from tempest_fastapi_sdk.agents import AgentContext, AgentToolError


def guard_deadline(context: AgentContext) -> None:
    """Refuse to start expensive work when the run is out of time."""
    if context.deadline is not None and time.monotonic() >= context.deadline:
        raise AgentToolError("no time left for this search")
```

Sem isso, uma ferramenta lenta estoura o tempo que um sub-agente herdou do
pai — e o pai é quem está segurando a requisição aberta.

### Onde esta execução está na árvore

`depth` e `parent` só importam em multi-agente: `depth` é quantas delegações
abaixo você está (`0` é o topo) e é o que impede A delegar para B delegar de
volta para A eternamente. Detalhes em
[Agentes de IA (avançado) » Delegar para outro agente](agents-advanced.md#delegar-para-outro-agente).

## O que a ferramenta devolve ao modelo

O retorno da ferramenta é lido pelo modelo e, quase sempre, repetido para o
usuário. Ele não é a resposta da sua API — e tratar os dois como a mesma coisa
custa nos três pontos abaixo.

**Devolva pouco.** Serializar a página inteira gasta a janela de contexto com
UUID, carimbo de tempo e relacionamento aninhado que o modelo não usa para
raciocinar. Uma linha por registro é o suficiente, e é o que o exemplo faz.

**Trave o que é regra sua.** Os filtros que existem por segurança ou por
domínio — "só ativos", "só do dono", "sem os dados de contato" — ficam fixos
no código da ferramenta, fora do schema de argumentos. O que estiver no schema
é escolha do modelo, e escolha do modelo é escolha de quem escreve o prompt.

**Cuidado com campo privado.** Se a resposta do seu service carrega endereço,
telefone ou e-mail, e a ferramenta devolve o objeto inteiro, o agente publica
aquilo na resposta. Monte a string com os campos que podem ser lidos.

## Recapitulando

- **`Depends` não alcança a ferramenta** — o agente não vive dentro de uma
  requisição, então a ferramenta abre a própria sessão.
- **`async with db.get_session_context()` dentro da ferramenta**, uma sessão
  por chamada: a conexão não fica presa durante a espera pelo modelo.
- **`get_session_context` faz commit** ao sair; numa ferramenta de escrita
  isso é uma transação por chamada, e escritas que precisam cair juntas
  pertencem à mesma ferramenta.
- **`from_session` é convenção da sua aplicação** — só ela sabe montar o
  service, e isso serve a todo consumidor sem requisição.
- **Um `AsyncDatabaseManager` por processo**, com `disconnect()` no lifespan.
- **`context` carrega o que o modelo não pode escolher** — antes de tudo, quem
  está perguntando. `make_agent_router` não semeia contexto.
- **O retorno é texto para o modelo**: curto, com os filtros de segurança
  travados no código e sem campo privado.

Próximo passo: [Agentes de IA (avançado)](agents-advanced.md) para memória
durável, skills e delegação; [Agentes de IA (testes)](agents-testing.md) para
testar a ferramenta sem subir modelo nenhum.

Veja também: [Banco de dados](database.md) para repositórios, paginação e
migrações; [Tarefas em background](queue-tasks.md) para o outro consumidor que
não tem requisição.
