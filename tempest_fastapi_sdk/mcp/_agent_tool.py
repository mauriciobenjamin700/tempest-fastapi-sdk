"""The FastMCP tool that runs an :class:`~tempest_fastapi_sdk.agents.AgentTool`.

Private, and imported only from inside the public helpers: it subclasses
``fastmcp.tools.Tool`` at module level, so importing it requires the
``[fastmcp]`` extra.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any
from urllib.parse import quote

from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from fastmcp.tools.base import ToolResult as MCPToolResult
from mcp_types import (
    AudioContent,
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
)
from pydantic import PrivateAttr

from tempest_fastapi_sdk.agents.schemas import AgentArtifact, ToolResult
from tempest_fastapi_sdk.agents.tools import AgentContext, AgentTool, AgentToolError

logger = logging.getLogger(__name__)


def artifact_content(artifact: AgentArtifact) -> ContentBlock:
    """Render one agent artifact as an MCP content block.

    Images and audio become the protocol's own ``image`` / ``audio`` blocks,
    which a client can show or play; anything else travels as an embedded
    binary resource under ``artifact:///<name>``, so the client still gets the
    bytes and the media type.

    Args:
        artifact (AgentArtifact): The artifact a tool produced.

    Returns:
        ContentBlock: The block carrying the artifact's bytes, base64-encoded.
    """
    data = base64.b64encode(artifact.data).decode("ascii")
    if artifact.media_type.startswith("image/"):
        return ImageContent(type="image", data=data, mime_type=artifact.media_type)
    if artifact.media_type.startswith("audio/"):
        return AudioContent(type="audio", data=data, mime_type=artifact.media_type)
    return EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri=f"artifact:///{quote(artifact.name)}",
            mime_type=artifact.media_type,
            blob=data,
        ),
    )


def result_content(result: ToolResult) -> list[ContentBlock]:
    """Render an agent tool's result as MCP content.

    Args:
        result (ToolResult): What the agent tool returned.

    Returns:
        list[ContentBlock]: The text first, then one block per artifact.
    """
    blocks: list[ContentBlock] = [TextContent(type="text", text=result.text)]
    blocks.extend(artifact_content(artifact) for artifact in result.artifacts)
    return blocks


class AgentToolAdapter(Tool):
    """A FastMCP tool whose body is an existing agent tool.

    The input schema is the agent tool's own ``parameters``, so the model on
    the other side of MCP sees exactly what an in-process agent sees.

    Failures follow the agent contract: an :class:`AgentToolError` carries
    its message to the client, any other exception reaches it as
    ``"tool failed: <Type>"`` and is logged here in full, because an
    arbitrary exception can carry a DSN or a token.
    """

    _source: AgentTool | None = PrivateAttr(default=None)

    @classmethod
    def wrap(cls, source: AgentTool, *, timeout: float | None) -> AgentToolAdapter:
        """Build the MCP tool for ``source``.

        Args:
            source (AgentTool): The agent tool to expose.
            timeout (float | None): Seconds one call may take, or ``None``
                for no ceiling.

        Returns:
            AgentToolAdapter: The tool, ready for ``FastMCP.add_tool``.
        """
        tool = cls(
            name=source.name,
            description=source.description,
            parameters=source.parameters,
            timeout=timeout,
        )
        tool._source = source
        return tool

    async def run(self, arguments: dict[str, Any]) -> MCPToolResult:
        """Run the wrapped agent tool under a fresh context.

        Args:
            arguments (dict[str, Any]): The arguments the client sent.

        Returns:
            MCPToolResult: The text and artifacts as MCP content.

        Raises:
            ToolError: When the agent tool failed, timed out, or the adapter
                was built without one.
        """
        source = self._source
        if source is None:
            raise ToolError(f"tool {self.name!r} has no implementation")
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        context = AgentContext(goal=f"MCP call to {self.name}", deadline=deadline)
        try:
            async with asyncio.timeout(self.timeout):
                result = await source.invoke(arguments, context)
        except AgentToolError as exc:
            raise ToolError(str(exc)) from exc
        except TimeoutError as exc:
            if self.timeout is None or time.monotonic() < (deadline or 0.0):
                raise self._masked(exc) from exc
            raise ToolError(
                f"tool {self.name!r} timed out after {self.timeout:g}s",
            ) from exc
        except Exception as exc:
            raise self._masked(exc) from exc
        return MCPToolResult(content=result_content(result))

    def _masked(self, exc: Exception) -> ToolError:
        """Log an unexpected failure in full and return what the client reads.

        Args:
            exc (Exception): What the agent tool raised.

        Returns:
            ToolError: ``"tool failed: <Type>"`` — the type only, since the
            text of an arbitrary exception is not written to be shown.
        """
        kind = type(exc).__name__
        logger.warning("MCP tool %r raised %s", self.name, kind, exc_info=exc)
        return ToolError(f"tool failed: {kind}")


__all__: list[str] = ["AgentToolAdapter", "artifact_content", "result_content"]
