"""Use an external MCP server's tools from the SDK's agents.

:class:`MCPToolset` holds one connection to an MCP server — a subprocess
spoken to over stdio, or a streamable HTTP endpoint — and turns the tools it
lists into :class:`~tempest_fastapi_sdk.agents.AgentTool` objects, so an
:class:`~tempest_fastapi_sdk.agents.Agent` calls them like any other tool::

    async with MCPToolset.stdio("uvx", ["mcp-server-time"], name="time") as mcp:
        agent = Agent(backend, tools=await mcp.tools())
        run = await agent.run("What time is it in Tokyo?")

It satisfies :class:`~tempest_fastapi_sdk.tasks.LifecycleResource`
(``connect``/``disconnect``), so it can also be opened by an app lifespan or
by ``TaskQueue(resources=[...])`` and shared for the life of the process.

Needs the ``[fastmcp]`` extra; the module imports without it and the
constructors raise an ``ImportError`` naming the extra.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Collection, Mapping, Sequence
from contextlib import AsyncExitStack
from pathlib import PurePosixPath
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import unquote, urlparse

from tempest_fastapi_sdk.agents.schemas import AgentArtifact, ToolResult
from tempest_fastapi_sdk.agents.tools import (
    AgentContext,
    AgentTool,
    AgentToolError,
    ToolReturn,
)
from tempest_fastapi_sdk.mcp._dependency import require_fastmcp

if TYPE_CHECKING:
    from fastmcp import Client
    from fastmcp.client.client import CallToolResult

DEFAULT_MCP_CALL_TIMEOUT: float = 30.0
"""Seconds one remote tool call may take before it becomes a tool error."""

_SESSION_REQUEST_TIMEOUT: float = 30.0
"""Seconds the handshake and ``tools/list`` may take. Tool calls carry their
own ``timeout``, so a short call ceiling does not also cut listing short."""

_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "application/pdf": "pdf",
    "application/json": "json",
    "text/plain": "txt",
}


def _decode(data: str) -> bytes:
    """Decode a base64 payload the server sent.

    Args:
        data (str): The base64 text.

    Returns:
        bytes: The decoded bytes.

    Raises:
        AgentToolError: When the payload is not valid base64.
    """
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AgentToolError("the MCP server sent invalid base64 content") from exc


class MCPToolset:
    """One MCP server connection, its tools adapted for the agents.

    Build it with :meth:`stdio` or :meth:`http` (or hand it a configured
    ``fastmcp.Client``), open it with ``async with`` or :meth:`connect`, and
    read :meth:`tools`. The adapted tools call through **this** connection,
    so they work while the toolset is open and fail with an
    :class:`~tempest_fastapi_sdk.agents.AgentToolError` the model can read
    once it is closed.

    Every failure the model can act on becomes an ``AgentToolError``: a
    result the server flagged as an error (its text), a call that ran past
    ``timeout`` or past the agent's own deadline, and a JSON-RPC error. A
    broken transport is left to the agent, which reports it as
    ``"tool failed: <Type>"`` — its text can carry the server URL.

    Attributes:
        name (str): The server's name, used in error messages.
        prefix (str): Prepended to every remote tool name, to keep two
            servers' ``search`` tools apart.
        timeout (float): Seconds one call may take.
        include (frozenset[str] | None): Remote tool names to adapt; ``None``
            adapts every tool the server lists.
    """

    def __init__(
        self,
        client: Client[Any],
        *,
        name: str,
        prefix: str = "",
        timeout: float = DEFAULT_MCP_CALL_TIMEOUT,
        include: Collection[str] | None = None,
    ) -> None:
        """Wrap a configured, not yet connected ``fastmcp.Client``.

        Args:
            client (Client[Any]): The client; this toolset enters and exits it.
            name (str): The server's name, for error messages.
            prefix (str): Prepended to each remote tool name.
            timeout (float): Seconds one call may take.
            include (Collection[str] | None): Remote tool names to adapt;
                ``None`` adapts all of them.

        Raises:
            ImportError: When the ``[fastmcp]`` extra is not installed.
        """
        require_fastmcp()
        self.name: str = name
        self.prefix: str = prefix
        self.timeout: float = timeout
        self.include: frozenset[str] | None = (
            None if include is None else frozenset(include)
        )
        self._client: Client[Any] = client
        self._stack: AsyncExitStack | None = None

    @classmethod
    def stdio(
        cls,
        command: str,
        args: Sequence[str] = (),
        *,
        name: str,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        prefix: str = "",
        timeout: float = DEFAULT_MCP_CALL_TIMEOUT,
        include: Collection[str] | None = None,
    ) -> MCPToolset:
        """Connect to a server started as a subprocess, over stdio.

        The subprocess starts on :meth:`connect` and is stopped on
        :meth:`disconnect` — the transport's ``keep_alive`` is turned off,
        so closing the toolset does not leave the process running.

        Args:
            command (str): The executable (``"uvx"``, ``"npx"``, a Python).
            args (Sequence[str]): Its arguments.
            name (str): The server's name, for error messages.
            env (Mapping[str, str] | None): Environment for the subprocess.
            cwd (str | None): Its working directory.
            prefix (str): Prepended to each remote tool name.
            timeout (float): Seconds one call may take.
            include (Collection[str] | None): Remote tool names to adapt.

        Returns:
            MCPToolset: The toolset, not yet connected.

        Raises:
            ImportError: When the ``[fastmcp]`` extra is not installed.
        """
        require_fastmcp()
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport

        transport = StdioTransport(
            command,
            list(args),
            env=None if env is None else dict(env),
            cwd=cwd,
            keep_alive=False,
        )
        return cls(
            Client(transport, timeout=_SESSION_REQUEST_TIMEOUT),
            name=name,
            prefix=prefix,
            timeout=timeout,
            include=include,
        )

    @classmethod
    def http(
        cls,
        url: str,
        *,
        name: str,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        prefix: str = "",
        timeout: float = DEFAULT_MCP_CALL_TIMEOUT,
        include: Collection[str] | None = None,
    ) -> MCPToolset:
        """Connect to a server over streamable HTTP.

        Args:
            url (str): The MCP endpoint, e.g. ``"https://api.example/mcp"``.
            name (str): The server's name, for error messages.
            token (str | None): Sent as ``Authorization: Bearer <token>``.
            headers (Mapping[str, str] | None): Further headers on every
                request, e.g. the shared secret of a ``make_token_dependency``.
            prefix (str): Prepended to each remote tool name.
            timeout (float): Seconds one call may take.
            include (Collection[str] | None): Remote tool names to adapt.

        Returns:
            MCPToolset: The toolset, not yet connected.

        Raises:
            ImportError: When the ``[fastmcp]`` extra is not installed.
        """
        require_fastmcp()
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        transport = StreamableHttpTransport(
            url,
            headers=None if headers is None else dict(headers),
            auth=token,
        )
        return cls(
            Client(transport, timeout=_SESSION_REQUEST_TIMEOUT),
            name=name,
            prefix=prefix,
            timeout=timeout,
            include=include,
        )

    @property
    def connected(self) -> bool:
        """Whether :meth:`connect` ran and :meth:`disconnect` has not."""
        return self._stack is not None

    async def connect(self) -> None:
        """Open the connection (and start the subprocess, for stdio).

        Calling it on an open toolset does nothing.
        """
        if self._stack is not None:
            return
        stack = AsyncExitStack()
        await stack.enter_async_context(self._client)
        self._stack = stack

    async def disconnect(self) -> None:
        """Close the connection (and stop the subprocess, for stdio).

        Calling it on a closed toolset does nothing.
        """
        stack, self._stack = self._stack, None
        if stack is not None:
            await stack.aclose()

    async def __aenter__(self) -> Self:
        """Connect, for ``async with``.

        Returns:
            Self: This toolset, connected.
        """
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Disconnect, whatever happened inside the block.

        Args:
            exc_type (type[BaseException] | None): The exception type, if any.
            exc (BaseException | None): The exception, if any.
            traceback (TracebackType | None): Its traceback, if any.
        """
        await self.disconnect()

    async def tools(self) -> list[AgentTool]:
        """List the server's tools and adapt them for an agent.

        Each tool keeps the server's description and input schema; its name
        gets :attr:`prefix`.

        Returns:
            list[AgentTool]: One agent tool per remote tool, in server order.

        Raises:
            RuntimeError: When the toolset is not connected.
        """
        if self._stack is None:
            raise RuntimeError(f"MCP toolset {self.name!r} is not connected")
        listed = await self._client.list_tools()
        return [
            self._adapt(tool.name, tool.description or "", tool.input_schema)
            for tool in listed
            if self.include is None or tool.name in self.include
        ]

    def _adapt(
        self,
        remote: str,
        description: str,
        schema: dict[str, Any],
    ) -> AgentTool:
        """Build the agent tool that calls ``remote`` on this connection.

        Args:
            remote (str): The tool's name on the server.
            description (str): Its description.
            schema (dict[str, Any]): Its input JSON schema.

        Returns:
            AgentTool: The adapted tool.
        """

        async def handler(
            arguments: dict[str, Any],
            context: AgentContext,
        ) -> ToolReturn:
            """Call the remote tool and translate the result."""
            return await self._call(remote, arguments, context)

        return AgentTool(
            name=f"{self.prefix}{remote}",
            description=description,
            parameters=schema,
            handler=handler,
        )

    def _budget(self, context: AgentContext) -> float:
        """Seconds this call may take: the toolset's ceiling or the run's clock.

        Args:
            context (AgentContext): The calling run's context.

        Returns:
            float: The smaller of :attr:`timeout` and the time left before the
            run's deadline, never negative. ``0`` means the run is out of
            time; it is never passed on, because the client reads a zero
            timeout as "no timeout".
        """
        if context.deadline is None:
            return self.timeout
        return max(0.0, min(self.timeout, context.deadline - time.monotonic()))

    async def _call(
        self,
        remote: str,
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> ToolResult:
        """Call ``remote`` and map every model-actionable failure.

        Args:
            remote (str): The tool's name on the server.
            arguments (dict[str, Any]): The arguments the model chose.
            context (AgentContext): The calling run's context.

        Returns:
            ToolResult: The text and artifacts the server returned.

        Raises:
            AgentToolError: When the toolset is closed, the call timed out,
                the server answered with a JSON-RPC error, or the tool result
                is flagged as an error.
        """
        from mcp import MCPError
        from mcp_types import REQUEST_TIMEOUT

        if self._stack is None:
            raise AgentToolError(f"the MCP server {self.name!r} is not connected")
        budget = self._budget(context)
        if budget <= 0:
            raise AgentToolError(
                f"no time left in this run to call the MCP tool {remote!r}",
            )
        try:
            result = await self._client.call_tool(
                remote,
                arguments,
                timeout=budget,
                raise_on_error=False,
            )
        except MCPError as exc:
            if exc.error.code == REQUEST_TIMEOUT:
                raise AgentToolError(
                    f"the MCP tool {remote!r} on {self.name!r} timed out "
                    f"after {budget:g}s",
                ) from exc
            raise AgentToolError(
                f"the MCP server {self.name!r} refused {remote!r}: {exc.error.message}",
            ) from exc
        except TimeoutError as exc:
            raise AgentToolError(
                f"the MCP tool {remote!r} on {self.name!r} timed out after {budget:g}s",
            ) from exc
        return self._translate(remote, result, context)

    def _translate(
        self,
        remote: str,
        result: CallToolResult,
        context: AgentContext,
    ) -> ToolResult:
        """Turn MCP content into the agent's text plus artifacts.

        Text blocks and text resources become the text the model reads.
        Images, audio and binary resources become artifacts — never prompt
        text — named after the tool (``<tool>.png``, made unique on the run),
        and the text says which names they got.

        Args:
            remote (str): The tool's name on the server.
            result (CallToolResult): What the server returned.
            context (AgentContext): The run context, for unique names.

        Returns:
            ToolResult: The translated result.

        Raises:
            AgentToolError: When the server flagged the result as an error.
        """
        from mcp_types import (
            AudioContent,
            EmbeddedResource,
            ImageContent,
            ResourceLink,
            TextContent,
            TextResourceContents,
        )

        lines: list[str] = []
        artifacts: list[AgentArtifact] = []

        def keep(stem: str, media_type: str, data: str, description: str) -> str:
            path = PurePosixPath(stem)
            suffix = path.suffix or f".{_EXTENSIONS.get(media_type, 'bin')}"
            base = path.stem if path.suffix else stem
            name, counter = f"{base}{suffix}", 0
            while name in context.artifacts or any(a.name == name for a in artifacts):
                counter += 1
                name = f"{base}-{counter}{suffix}"
            artifacts.append(
                AgentArtifact(
                    name=name,
                    media_type=media_type,
                    data=_decode(data),
                    description=description,
                ),
            )
            return name

        for block in result.content:
            if isinstance(block, TextContent):
                lines.append(block.text)
            elif isinstance(block, ImageContent | AudioContent):
                stored = keep(
                    remote,
                    block.mime_type,
                    block.data,
                    f"{block.type} from MCP tool {remote}",
                )
                lines.append(f"Saved {block.type} as {stored}.")
            elif isinstance(block, EmbeddedResource):
                resource = block.resource
                if isinstance(resource, TextResourceContents):
                    lines.append(resource.text)
                    continue
                path = unquote(urlparse(str(resource.uri)).path)
                stored = keep(
                    PurePosixPath(path).name or remote,
                    resource.mime_type or "application/octet-stream",
                    resource.blob,
                    f"resource {resource.uri} from MCP tool {remote}",
                )
                lines.append(f"Saved {resource.uri} as {stored}.")
            elif isinstance(block, ResourceLink):
                lines.append(f"Resource: {block.uri}")
        if not lines and result.structured_content is not None:
            lines.append(json.dumps(result.structured_content, ensure_ascii=False))
        text = "\n".join(lines)
        if result.is_error:
            raise AgentToolError(text or f"the MCP tool {remote!r} failed")
        return ToolResult(text=text, artifacts=artifacts)


__all__: list[str] = ["DEFAULT_MCP_CALL_TIMEOUT", "MCPToolset"]
