"""Model Context Protocol, both directions, over FastMCP.

**Serve.** Build a ``fastmcp.FastMCP`` server, register tools on it, and
mount it on your app behind the dependencies your routes already use:

    >>> server = FastMCP("orders")
    >>> add_agent_tools(server, [lookup_order_tool])
    >>> add_fastapi_routes(server, app, operations=["create_order"])
    >>> mount_mcp(app, server, principal=current_user)

**Consume.** Connect to someone else's MCP server and hand its tools to an
:class:`~tempest_fastapi_sdk.agents.Agent`:

    >>> async with MCPToolset.http(url, name="crm", token=token) as crm:
    ...     agent = Agent(backend, tools=await crm.tools())

Submodule import, like ``agents``:
``from tempest_fastapi_sdk.mcp import mount_mcp``. Needs the ``[fastmcp]``
extra; the import itself works without it, and each helper raises an
``ImportError`` naming the extra.
"""

from tempest_fastapi_sdk.mcp.client import (
    DEFAULT_MCP_CALL_TIMEOUT as DEFAULT_MCP_CALL_TIMEOUT,
)
from tempest_fastapi_sdk.mcp.client import MCPToolset as MCPToolset
from tempest_fastapi_sdk.mcp.server import (
    DEFAULT_FORWARDED_HEADERS as DEFAULT_FORWARDED_HEADERS,
)
from tempest_fastapi_sdk.mcp.server import (
    DEFAULT_MCP_TOOL_TIMEOUT as DEFAULT_MCP_TOOL_TIMEOUT,
)
from tempest_fastapi_sdk.mcp.server import (
    PRINCIPAL_STATE_KEY as PRINCIPAL_STATE_KEY,
)
from tempest_fastapi_sdk.mcp.server import add_agent_tools as add_agent_tools
from tempest_fastapi_sdk.mcp.server import add_fastapi_routes as add_fastapi_routes
from tempest_fastapi_sdk.mcp.server import mcp_principal as mcp_principal
from tempest_fastapi_sdk.mcp.server import mount_mcp as mount_mcp

__all__: list[str] = [
    "DEFAULT_FORWARDED_HEADERS",
    "DEFAULT_MCP_CALL_TIMEOUT",
    "DEFAULT_MCP_TOOL_TIMEOUT",
    "PRINCIPAL_STATE_KEY",
    "MCPToolset",
    "add_agent_tools",
    "add_fastapi_routes",
    "mcp_principal",
    "mount_mcp",
]
