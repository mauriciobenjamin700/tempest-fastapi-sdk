"""The MCP server the round-trip tests talk to, over HTTP and over stdio.

Imported by the tests to mount it on a FastAPI app, and run as a script
(``python mcp_fixture_server.py``) to serve it over stdio in a subprocess.
Only the SDK and ``fastmcp`` are imported, so the subprocess needs nothing
from the test tree.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from tempest_fastapi_sdk.agents import (
    AgentArtifact,
    AgentContext,
    AgentTool,
    AgentToolError,
    ToolResult,
    text_tool,
)
from tempest_fastapi_sdk.mcp import add_agent_tools

PNG_BYTES: bytes = b"\x89PNG\r\n\x1a\nfixture"
PDF_BYTES: bytes = b"%PDF-1.7 fixture"
LEAKED_SECRET: str = "postgres://app:hunter2@db/prod"


class QuoteRequestSchema(BaseModel):
    """A typed argument, to compare the advertised schema with Pydantic's."""

    sku: str = Field(min_length=1, description="Product code.")
    quantity: int = Field(ge=1, le=100, description="Units wanted.")


async def _echo(arguments: dict[str, Any], _context: AgentContext) -> str:
    return f"echo: {arguments['text']}"


async def _draw(_arguments: dict[str, Any], _context: AgentContext) -> ToolResult:
    return ToolResult(
        text="Drew chart.png and report.pdf.",
        artifacts=[
            AgentArtifact(name="chart.png", media_type="image/png", data=PNG_BYTES),
            AgentArtifact(
                name="report.pdf",
                media_type="application/pdf",
                data=PDF_BYTES,
            ),
        ],
    )


async def _refuse(_arguments: dict[str, Any], _context: AgentContext) -> str:
    raise AgentToolError("order 42 is already closed")


async def _explode(_arguments: dict[str, Any], _context: AgentContext) -> str:
    raise RuntimeError(f"could not reach {LEAKED_SECRET}")


async def _nap(arguments: dict[str, Any], _context: AgentContext) -> str:
    await asyncio.sleep(float(arguments["seconds"]))
    return "rested"


async def _pid(_arguments: dict[str, Any], _context: AgentContext) -> str:
    return str(os.getpid())


EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}

AGENT_TOOLS: list[AgentTool] = [
    text_tool("echo", "Repeat the text back.", _echo),
    AgentTool("draw", "Draw a chart and a report.", EMPTY_SCHEMA, _draw),
    AgentTool("refuse", "Always refuses.", EMPTY_SCHEMA, _refuse),
    AgentTool("explode", "Always crashes.", EMPTY_SCHEMA, _explode),
    AgentTool(
        "nap",
        "Sleep for a while.",
        {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
        _nap,
    ),
    AgentTool("pid", "Report the server process id.", EMPTY_SCHEMA, _pid),
]


def build_server(*, tool_timeout: float | None = 120.0) -> FastMCP:
    """Build the fixture server.

    Args:
        tool_timeout (float | None): The ceiling passed to ``add_agent_tools``.

    Returns:
        FastMCP: The server with the agent tools and one native tool.
    """
    server = FastMCP("fixture")
    add_agent_tools(server, AGENT_TOOLS, timeout=tool_timeout)

    @server.tool
    def quote(request: QuoteRequestSchema) -> str:
        """Price a quantity of a product."""
        return f"{request.quantity} x {request.sku} = {request.quantity * 10}"

    return server


if __name__ == "__main__":
    build_server().run(transport="stdio", show_banner=False)
