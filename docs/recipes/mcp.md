# MCP (Model Context Protocol)

O [MCP](https://modelcontextprotocol.io) é o protocolo com que um cliente de
IA (Claude Desktop, um IDE, outro agente) descobre e chama ferramentas de um
servidor. O módulo `tempest_fastapi_sdk.mcp` cobre as duas direções, em cima
do [FastMCP](https://gofastmcp.com):

| Você quer… | Use |
| --- | --- |
| que um cliente MCP chame ferramentas do **seu** serviço | `mount_mcp` + `add_agent_tools` / `add_fastapi_routes` |
| que um `Agent` do SDK use ferramentas de um servidor MCP **de fora** | `MCPToolset` |

## Instalação

```bash
pip install "tempest-fastapi-sdk[fastmcp]"
```

O extra declara `fastmcp>=4.1.0`. O piso é a 4.1.0 porque ela é a primeira que
valida os segmentos de parâmetro de caminho nas ferramentas geradas de
OpenAPI — o mesmo caminho por onde `add_fastapi_routes` passa argumentos
escolhidos pelo modelo. A suíte do módulo passa na 4.1.0 (com `mcp` 2.0.0) e
na 4.1.8.

!!! note "Fora do `[all]`"
    Numa venv nova com Python 3.12, o extra somou 47 pacotes à instalação base
    do SDK (fastmcp 4.1.8), entre eles um segundo cliente HTTP (`httpx2`), e
    exige `starlette>=1.0.1`. Por isso ele é opt-in e não entra no `[all]`.
    Sem o extra, `import tempest_fastapi_sdk.mcp` funciona; cada função levanta
    `ImportError` dizendo o que instalar.

## Servir: um endpoint MCP na sua aplicação

Um serviço de pedidos que já tem uma rota `POST /orders` autenticada, e quer
que um cliente MCP possa criar pedidos, perguntar quem está logado e usar uma
ferramenta de agente que já existe:

```python title="mcp_server.py"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from tempest_fastapi_sdk import (
    JWTUtils,
    make_bearer_token_dependency,
    register_exception_handlers,
)
from tempest_fastapi_sdk.agents import AgentContext, text_tool
from tempest_fastapi_sdk.mcp import (
    add_agent_tools,
    add_fastapi_routes,
    mcp_principal,
    mount_mcp,
)

jwt = JWTUtils("troque-por-um-segredo-de-32-bytes-ou-mais")
current_user = make_bearer_token_dependency(jwt)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    print("app: abrindo recursos")
    yield
    print("app: fechando recursos")


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)


class OrderInSchema(BaseModel):
    sku: str = Field(min_length=1, description="Código do produto.")
    quantity: int = Field(ge=1, description="Quantidade.")


class OrderOutSchema(BaseModel):
    sku: str
    quantity: int
    owner: str


@app.post("/orders", operation_id="create_order", tags=["orders"])
async def create_order(
    order: OrderInSchema,
    claims: dict[str, Any] = Depends(current_user),
) -> OrderOutSchema:
    return OrderOutSchema(sku=order.sku, quantity=order.quantity, owner=claims["sub"])


async def shout(arguments: dict[str, Any], _context: AgentContext) -> str:
    return str(arguments["text"]).upper()


server = FastMCP("pedidos")


@server.tool
def whoami() -> str:
    """Return the subject of the token that called this tool."""
    return str(mcp_principal(dict)["sub"])


add_agent_tools(server, [text_tool("shout", "Return the text in upper case.", shout)])
add_fastapi_routes(server, app, operations=["create_order"])
mount_mcp(app, server, principal=current_user)
```

Rode com `uvicorn mcp_server:app`. O endpoint fica em `/mcp`.

### O servidor é seu: `FastMCP`

O SDK não embrulha o `FastMCP`. Você o constrói e registra ferramentas,
recursos e prompts com os decoradores dele (`@server.tool`, `@server.resource`,
`@server.prompt`), que derivam o schema das anotações de tipo — um parâmetro
anotado com um modelo Pydantic vira o schema daquele modelo.

### A autenticação é a das suas rotas: `mount_mcp`

`mount_mcp` registra `/mcp` como uma **rota FastAPI** comum (`GET`, `POST` e
`DELETE`, fora do OpenAPI). Por isso a dependência que você passa em
`principal=` roda exatamente como roda no `POST /orders`: o mesmo
`current_user`, a mesma resposta de recusa. Sem token, a requisição para antes
de chegar ao servidor MCP:

```text
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Authorization token is missing or invalid","code":"UNAUTHORIZED","details":{}}
```

O que `principal=` resolveu fica disponível dentro da ferramenta por
`mcp_principal(tipo)` — `dict` para as claims de
`make_bearer_token_dependency`, o seu model de usuário para
`make_jwt_user_dependency`. O `isinstance` é conferido, então o retorno já sai
tipado. Gates extras (permissão, papel) entram em `dependencies=`, como num
`APIRouter`.

!!! danger "Endpoint aberto só por escolha explícita"
    `mount_mcp` sem `principal=` e sem `dependencies=` levanta `ValueError`. Um
    endpoint MCP executa ferramentas em nome de quem chama; aberto, executa para
    qualquer um que ache a URL. Para servir sem autenticação de propósito (uma
    demo local), passe `allow_unauthenticated=True`.

Por padrão o transporte roda **sem sessão** (`stateless=True`): cada requisição
é autenticada sozinha e nenhum `Mcp-Session-Id` sobrevive ao token que o abriu
nem pode ser apresentado com o token de outro usuário. Recursos que precisam de
sessão (notificação de progresso numa requisição posterior, sampling) pedem
`stateless=False`.

### O lifespan é composto, não substituído

O transporte MCP precisa rodar dentro do lifespan da aplicação. `mount_mcp`
troca o `app.router.lifespan_context` por um que entra primeiro no **seu**
lifespan e depois no do transporte — o MCP para antes de os recursos que você
abriu fecharem. Rodando o exemplo e encerrando com `Ctrl+C`:

```text
INFO:     Waiting for application startup.
app: abrindo recursos
INFO:     Application startup complete.
...
INFO:     Waiting for application shutdown.
app: fechando recursos
INFO:     Application shutdown complete.
```

Chame `mount_mcp` depois de definir o lifespan da aplicação, e sirva com o
lifespan ligado (o uvicorn liga por padrão; num teste, `with TestClient(app)`).

### Ferramentas de agente que já existem: `add_agent_tools`

`add_agent_tools(server, tools)` publica `AgentTool`s com o `parameters` que
elas já declaram — o cliente MCP vê o mesmo schema que um `Agent` do SDK vê. O
contrato de falha é o dos agentes:

| A ferramenta… | O cliente MCP recebe |
| --- | --- |
| levanta `AgentToolError("pedido 42 já fechado")` | o erro com essa mensagem |
| levanta qualquer outra exceção | `tool failed: <Tipo>` — o texto fica no log do servidor, porque pode carregar uma DSN ou um token |
| passa de `timeout` (padrão 120 s) | `tool '<nome>' timed out after <n>s` |
| devolve `ToolResult` com artefatos | o texto, e cada artefato como conteúdo `image`/`audio` do MCP ou recurso binário embutido |

Cada chamada roda com um `AgentContext` novo: uma chamada MCP não carrega
artefatos de chamadas anteriores.

### Rotas FastAPI, sem duplicar schema: `add_fastapi_routes`

`add_fastapi_routes(server, app, operations=[...], tags=[...])` publica as
rotas que você **escolheu** — por `operation_id` ou por tag — com o schema tirado
do OpenAPI da própria aplicação. Sem seleção ela levanta `ValueError`: expor
todas as rotas (inclusive os `DELETE`) não é padrão. Um `operation_id` que não
existe, ou uma tag sem rota, também levanta — que é o que avisa quando a função
foi chamada antes de as rotas serem incluídas.

A chamada vai para a rota no mesmo processo (transporte ASGI, sem socket), e
**as credenciais do cliente MCP vão junto**: `Authorization` e `Cookie` (os
nomes em `forward_headers`) são copiados da requisição MCP. A rota autentica com
as próprias dependências, e autentica **quem chamou o MCP** — no exemplo, o
`owner` do pedido é o `sub` do token do cliente. Esses cabeçalhos também saem
do schema da ferramenta, e um valor que o modelo coloque nos argumentos é
sobrescrito (ou removido, se a requisição MCP não tinha o cabeçalho).

!!! tip "Cabeçalho de `make_token_dependency`"
    Uma rota protegida por segredo compartilhado lê outro cabeçalho
    (`X-Token` por padrão). Acrescente-o:
    `forward_headers=("authorization", "cookie", "x-token")`.

O OpenAPI é gerado uma vez, na chamada, e o cache da aplicação
(`app.openapi_schema`) é deixado como estava — a página `/docs` continua vendo
rotas incluídas depois.

## Consumir: ferramentas de outro servidor MCP num `Agent`

`MCPToolset` mantém **uma** conexão com um servidor MCP e transforma as
ferramentas que ele lista em `AgentTool`s. Contra o servidor acima, com o
modelo roteirizado pelo `ScriptedBackend` (ver
[Agentes de IA (testes)](agents-testing.md)):

```python title="mcp_client.py"
import asyncio

from tempest_fastapi_sdk import JWTUtils
from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
    tool_steps,
)
from tempest_fastapi_sdk.mcp import MCPToolset

jwt = JWTUtils("troque-por-um-segredo-de-32-bytes-ou-mais")


async def main() -> None:
    token = jwt.encode({"sub": "maria", "typ": "access"})
    backend = ScriptedBackend(
        [
            replies_with_tool("pedidos_whoami"),
            replies_with_tool("pedidos_create_order", {"sku": "CAFE", "quantity": 2}),
            replies("Pedido criado."),
        ],
    )
    async with MCPToolset.http(
        "http://127.0.0.1:8000/mcp",
        name="pedidos",
        token=token,
        prefix="pedidos_",
    ) as pedidos:
        tools = await pedidos.tools()
        print([tool.name for tool in tools])
        run = await Agent(backend, tools=tools).run("Faça um pedido de 2 cafés.")
    for step in tool_steps(run):
        print(step.name, "->", step.output)
    print(run.stop_reason, run.output)


asyncio.run(main())
```

Com o `mcp_server.py` rodando na porta 8000, ele imprime:

```text
['pedidos_whoami', 'pedidos_shout', 'pedidos_create_order']
pedidos_whoami -> maria
pedidos_create_order -> {"sku":"CAFE","quantity":2,"owner":"maria"}
completed Pedido criado.
```

`prefix=` separa ferramentas de dois servidores que usam o mesmo nome;
`include=` adapta só as que você listar.

### Servidor local, por stdio

Servidores MCP distribuídos como programa (`uvx`, `npx`, um script Python)
falam por stdio. `MCPToolset.stdio` sobe o processo no `connect` e o encerra no
`disconnect`:

```python title="stdio_client.py"
import asyncio
import sys

from tempest_fastapi_sdk.agents import AgentContext
from tempest_fastapi_sdk.mcp import MCPToolset


async def main() -> None:
    async with MCPToolset.stdio(
        sys.executable,
        ["local_server.py"],
        name="local",
    ) as local:
        tools = {tool.name: tool for tool in await local.tools()}
        print(tools["add"].parameters)
        result = await tools["add"].invoke({"a": 2, "b": 3}, AgentContext())
        print(result.text)


asyncio.run(main())
```

Com um `local_server.py` que define `add(a: int, b: int) -> int` num `FastMCP` e
termina em `server.run(transport="stdio")`, a saída é:

```text
{'type': 'object', 'additionalProperties': False, 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}
5
```

O `StdioTransport` do FastMCP, no padrão (`keep_alive=True`), deixa o
subprocesso vivo depois que o `Client` sai do `with` — medido no fastmcp 4.1.8:
o PID do servidor ainda respondia 1 s depois. O `MCPToolset.stdio` passa
`keep_alive=False`, e a suíte confere que o processo do servidor deixa de
existir depois do `disconnect()`.

### Ciclo de vida

`MCPToolset` tem `connect()` e `disconnect()`, idempotentes — é um
`LifecycleResource`, então também abre no lifespan da aplicação ou em
`TaskQueue(resources=[...])` e fica aberto a vida do processo. As ferramentas
adaptadas chamam pela conexão do toolset: depois do `disconnect` elas
respondem com `AgentToolError("... is not connected")`, que o modelo lê, em vez
de travar.

### Erros que o modelo lê

| Acontece… | A ferramenta levanta |
| --- | --- |
| o servidor marca o resultado como erro | `AgentToolError` com o texto do servidor |
| a chamada passa de `timeout` (padrão 30 s) ou do `deadline` da execução do agente | `AgentToolError("... timed out after <n>s")` |
| a execução do agente já não tem tempo | `AgentToolError("no time left ...")`, sem chamar |
| o servidor responde com erro JSON-RPC | `AgentToolError` com a mensagem do erro |
| o transporte quebra | a exceção sobe, e o agente a mostra como `tool failed: <Tipo>` — o texto pode carregar a URL do servidor |

Imagem, áudio e recurso binário que o servidor devolve viram **artefatos** do
agente (`<ferramenta>.png`, …), nunca base64 no prompt; o texto da ferramenta
diz com que nome cada um foi salvo.

## Recap

- `mount_mcp(app, server, principal=current_user)` serve um `FastMCP` em `/mcp`
  atrás das mesmas dependências das suas rotas, dentro do lifespan da app.
- `add_agent_tools` publica `AgentTool`s com o schema que elas já têm;
  `add_fastapi_routes` publica as rotas escolhidas, com o schema do OpenAPI e as
  credenciais do chamador.
- `MCPToolset.http` / `.stdio` trazem ferramentas de outro servidor para um
  `Agent`, com timeout e erros convertidos em `AgentToolError`.
