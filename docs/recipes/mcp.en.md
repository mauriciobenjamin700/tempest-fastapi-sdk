# MCP (Model Context Protocol)

[MCP](https://modelcontextprotocol.io) is the protocol an AI client (Claude
Desktop, an IDE, another agent) uses to discover and call a server's tools.
The `tempest_fastapi_sdk.mcp` module covers both directions, on top of
[FastMCP](https://gofastmcp.com):

| You want… | Use |
| --- | --- |
| an MCP client to call tools of **your** service | `mount_mcp` + `add_agent_tools` / `add_fastapi_routes` |
| an SDK `Agent` to use tools of an **external** MCP server | `MCPToolset` |

## Installation

```bash
pip install "tempest-fastapi-sdk[fastmcp]"
```

The extra declares `fastmcp>=4.1.0`. The floor is 4.1.0 because it is the
first release that validates path-parameter segments in tools generated from
OpenAPI — the same code path through which `add_fastapi_routes` passes
model-chosen arguments. The module's suite passes on 4.1.0 (with `mcp` 2.0.0)
and on 4.1.8.

!!! note "Out of `[all]`"
    In a fresh Python 3.12 venv, the extra added 47 packages to the SDK's base
    install (fastmcp 4.1.8), a second HTTP client (`httpx2`) among them, and it
    requires `starlette>=1.0.1`. That is why it is opt-in and stays out of
    `[all]`. Without the extra, `import tempest_fastapi_sdk.mcp` works; each
    function raises an `ImportError` saying what to install.

## Serve: an MCP endpoint in your application

An orders service that already has an authenticated `POST /orders` route, and
wants an MCP client to be able to create orders, ask who is logged in, and use
an agent tool that already exists:

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

jwt = JWTUtils("replace-with-a-secret-of-32-bytes-or-more")
current_user = make_bearer_token_dependency(jwt)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    print("app: opening resources")
    yield
    print("app: closing resources")


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)


class OrderInSchema(BaseModel):
    sku: str = Field(min_length=1, description="Product code.")
    quantity: int = Field(ge=1, description="Quantity.")


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


server = FastMCP("orders")


@server.tool
def whoami() -> str:
    """Return the subject of the token that called this tool."""
    return str(mcp_principal(dict)["sub"])


add_agent_tools(server, [text_tool("shout", "Return the text in upper case.", shout)])
add_fastapi_routes(server, app, operations=["create_order"])
mount_mcp(app, server, principal=current_user)
```

Run it with `uvicorn mcp_server:app`. The endpoint lives at `/mcp`.

### The server is yours: `FastMCP`

The SDK does not wrap `FastMCP`. You build it and register tools, resources
and prompts with its decorators (`@server.tool`, `@server.resource`,
`@server.prompt`), which derive the schema from the type hints — a parameter
annotated with a Pydantic model gets that model's schema.

### Authentication is your routes': `mount_mcp`

`mount_mcp` registers `/mcp` as an ordinary **FastAPI route** (`GET`, `POST`
and `DELETE`, kept out of OpenAPI). So the dependency you pass as
`principal=` runs exactly as it runs on `POST /orders`: the same
`current_user`, the same refusal. Without a token, the request stops before it
reaches the MCP server:

```text
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Authorization token is missing or invalid","code":"UNAUTHORIZED","details":{}}
```

Whatever `principal=` resolved is available inside a tool through
`mcp_principal(type)` — `dict` for the claims of
`make_bearer_token_dependency`, your user model for
`make_jwt_user_dependency`. The `isinstance` is checked, so the return value is
already typed. Extra gates (permission, role) go in `dependencies=`, as on an
`APIRouter`.

A `soft=True` dependency (the kind that returns `None` for a request without a
token instead of refusing it) does not open the endpoint: when `principal=`
resolves `None`, `mount_mcp` answers the same `401` as above. To accept
anonymous callers on purpose, pass `allow_unauthenticated=True` — then the
anonymous caller reaches the tool, and `mcp_principal(dict)` raises `TypeError`
for it (`None` is not a `dict`).

!!! danger "An open endpoint only by explicit choice"
    `mount_mcp` with neither `principal=` nor `dependencies=` raises
    `ValueError`. An MCP endpoint runs tools on behalf of whoever calls it;
    left open, it runs them for anyone who finds the URL. To serve without
    authentication on purpose (a local demo), pass
    `allow_unauthenticated=True`.

By default the transport runs **without a session** (`stateless=True`): each
request is authenticated on its own, and no `Mcp-Session-Id` outlives the token
that opened it or can be presented with another user's token. Features that
need a session (a progress notification on a later request, sampling) need
`stateless=False`.

### The lifespan is composed, not replaced

The MCP transport has to run inside the application's lifespan. `mount_mcp`
replaces `app.router.lifespan_context` with one that enters **your** lifespan
first and the transport's inside it — MCP stops before the resources you opened
are closed. Running the example and stopping it with `Ctrl+C`:

```text
INFO:     Waiting for application startup.
app: opening resources
INFO:     Application startup complete.
...
INFO:     Waiting for application shutdown.
app: closing resources
INFO:     Application shutdown complete.
```

Call `mount_mcp` after setting the app's lifespan, and serve with the lifespan
on (uvicorn does by default; in a test, `with TestClient(app)`).

### Agent tools you already have: `add_agent_tools`

`add_agent_tools(server, tools)` publishes `AgentTool`s with the `parameters`
they already declare — the MCP client sees the same schema an SDK `Agent` sees.
The failure contract is the agents':

| The tool… | The MCP client gets |
| --- | --- |
| raises `AgentToolError("order 42 is already closed")` | the error with that message |
| raises any other exception | `tool failed: <Type>` — the text stays in the server log, because it can carry a DSN or a token |
| runs past `timeout` (default 120 s) | `tool '<name>' timed out after <n>s` |
| returns a `ToolResult` with artifacts | the text, and each artifact as MCP `image`/`audio` content or an embedded binary resource |

Each call runs under a fresh `AgentContext`: an MCP call carries no artifacts
from earlier calls.

### FastAPI routes, without duplicating schemas: `add_fastapi_routes`

`add_fastapi_routes(server, app, operations=[...], tags=[...])` publishes the
routes you **picked** — by `operation_id` or by tag — with the schema taken from
the application's own OpenAPI document. Without a selection it raises
`ValueError`: exposing every route (`DELETE`s included) is not a default. An
`operation_id` that does not exist, or a tag with no route, also raises — which
is what tells you the function ran before the routes were included.

The call goes to the route in the same process (ASGI transport, no socket),
and **the MCP client's credentials go with it**: `Authorization` and `Cookie`
(the names in `forward_headers`) are copied from the MCP request. The route
authenticates with its own dependencies, and it authenticates **whoever called
MCP** — in the example, the order's `owner` is the `sub` of the client's token.
Those headers are also taken out of the tool's schema, and a value the model
puts in the arguments is overwritten (or removed, when the MCP request had no
such header).

The route sees the call as it would see the MCP request: the `Host` and the
client address are the MCP request's (a `TrustedHostMiddleware` accepts it, and
a per-IP limit counts each client in its own bucket), and an unhandled
exception in the route reaches the MCP client as the `500` the app answers over
HTTP — never with the exception's text.

!!! tip "The `make_token_dependency` header"
    A route protected by a shared secret reads another header (`X-Token` by
    default). Add it:
    `forward_headers=("authorization", "cookie", "x-token")`.

The OpenAPI document is generated once, during the call, and the application's
cache (`app.openapi_schema`) is left as it was — the `/docs` page still sees
routes included later.

## Consume: another MCP server's tools in an `Agent`

`MCPToolset` holds **one** connection to an MCP server and turns the tools it
lists into `AgentTool`s. Against the server above, with the model scripted by
`ScriptedBackend` (see [AI agents (testing)](agents-testing.md)):

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

jwt = JWTUtils("replace-with-a-secret-of-32-bytes-or-more")


async def main() -> None:
    token = jwt.encode({"sub": "maria", "typ": "access"})
    backend = ScriptedBackend(
        [
            replies_with_tool("orders_whoami"),
            replies_with_tool("orders_create_order", {"sku": "COFFEE", "quantity": 2}),
            replies("Order placed."),
        ],
    )
    async with MCPToolset.http(
        "http://127.0.0.1:8000/mcp",
        name="orders",
        token=token,
        prefix="orders_",
    ) as orders:
        tools = await orders.tools()
        print([tool.name for tool in tools])
        run = await Agent(backend, tools=tools).run("Order 2 coffees.")
    for step in tool_steps(run):
        print(step.name, "->", step.output)
    print(run.stop_reason, run.output)


asyncio.run(main())
```

With `mcp_server.py` running on port 8000, it prints:

```text
['orders_whoami', 'orders_shout', 'orders_create_order']
orders_whoami -> maria
orders_create_order -> {"sku":"COFFEE","quantity":2,"owner":"maria"}
completed Order placed.
```

`prefix=` keeps apart the tools of two servers that use the same name;
`include=` adapts only the ones you list.

### A local server, over stdio

MCP servers distributed as a program (`uvx`, `npx`, a Python script) speak
stdio. `MCPToolset.stdio` starts the process on `connect` and stops it on
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

With a `local_server.py` that defines `add(a: int, b: int) -> int` on a
`FastMCP` and ends in `server.run(transport="stdio")`, the output is:

```text
{'type': 'object', 'additionalProperties': False, 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}
5
```

FastMCP's `StdioTransport`, by default (`keep_alive=True`), leaves the
subprocess running after the `Client` leaves its `with` — measured on fastmcp
4.1.8: the server's PID still answered 1 s later. `MCPToolset.stdio` passes
`keep_alive=False`, and the suite checks that the server process is gone after
`disconnect()`.

### Lifecycle

`MCPToolset` has idempotent `connect()` and `disconnect()` — it is a
`LifecycleResource`, so it also opens in the application lifespan or in
`TaskQueue(resources=[...])` and stays open for the life of the process. The
adapted tools call through the toolset's connection: after `disconnect` they
answer with `AgentToolError("... is not connected")`, which the model reads,
instead of hanging.

### Errors the model reads

| When… | The tool raises |
| --- | --- |
| the server flags the result as an error | `AgentToolError` with the server's text |
| the call runs past `timeout` (default 30 s) or past the agent run's `deadline` | `AgentToolError("... timed out after <n>s")` |
| the agent run has no time left | `AgentToolError("no time left ...")`, without calling |
| the server answers with a JSON-RPC error | `AgentToolError` with the error's message |
| the transport breaks | the exception propagates, and the agent shows it as `tool failed: <Type>` — its text can carry the server URL |

Images, audio and binary resources the server returns become agent
**artifacts** (`<tool>.png`, …), never base64 in the prompt; the tool's text
says the name each one was saved under.

## Recap

- `mount_mcp(app, server, principal=current_user)` serves a `FastMCP` at `/mcp`
  behind the same dependencies as your routes, inside the app's lifespan.
- `add_agent_tools` publishes `AgentTool`s with the schema they already have;
  `add_fastapi_routes` publishes the routes you picked, with the OpenAPI schema
  and the caller's credentials.
- `MCPToolset.http` / `.stdio` bring another server's tools into an `Agent`,
  with timeouts and errors turned into `AgentToolError`.
