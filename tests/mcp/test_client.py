"""``MCPToolset`` against the SDK's own server in a stdio subprocess.

The server is ``mcp_fixture_server.py`` started with this interpreter, so
every call crosses a process boundary over the stdio transport a desktop
MCP host uses.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastmcp")

from tempest_fastapi_sdk.agents import Agent, AgentContext, AgentToolError
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    assert_used_tools,
    replies,
    replies_with_tool,
)
from tempest_fastapi_sdk.mcp import MCPToolset
from tests.mcp.mcp_fixture_server import AGENT_TOOLS, PNG_BYTES

FIXTURE_SERVER: Path = Path(__file__).with_name("mcp_fixture_server.py")


def _stdio(**kwargs: Any) -> MCPToolset:
    return MCPToolset.stdio(
        sys.executable,
        [str(FIXTURE_SERVER)],
        name="fixture",
        cwd=str(FIXTURE_SERVER.parent),
        **kwargs,
    )


async def _call(toolset: MCPToolset, name: str, **arguments: Any) -> Any:
    tools = {tool.name: tool for tool in await toolset.tools()}
    return await tools[name].invoke(arguments, AgentContext())


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class TestStdioRoundTrip:
    async def test_tools_cross_the_process_boundary(self) -> None:
        async with _stdio() as toolset:
            result = await _call(toolset, "echo", text="over stdio")
            server_pid = int((await _call(toolset, "pid")).text)
        assert result.text == "echo: over stdio"
        assert server_pid != os.getpid()

    async def test_the_listed_schemas_are_the_agent_tools_own(self) -> None:
        async with _stdio() as toolset:
            remote = {tool.name: tool for tool in await toolset.tools()}
        for tool in AGENT_TOOLS:
            assert remote[tool.name].parameters == tool.parameters
            assert remote[tool.name].description == tool.description

    async def test_an_agent_runs_the_remote_tool(self) -> None:
        backend = ScriptedBackend(
            [replies_with_tool("fixture_echo", {"text": "ping"}), replies("done")],
        )
        async with _stdio(prefix="fixture_", include=["echo"]) as toolset:
            tools = await toolset.tools()
            run = await Agent(backend, tools=tools).run("echo ping")
        assert [tool.name for tool in tools] == ["fixture_echo"]
        assert_used_tools(run, "fixture_echo")
        assert run.steps[1].output == "echo: ping"

    async def test_image_content_becomes_an_artifact_not_prompt_text(self) -> None:
        context = AgentContext()
        async with _stdio() as toolset:
            draw = {tool.name: tool for tool in await toolset.tools()}["draw"]
            result = await draw.invoke({}, context)
        image = next(a for a in result.artifacts if a.media_type == "image/png")
        assert image.data == PNG_BYTES
        assert image.name == "draw.png"
        assert "iVBOR" not in result.text
        assert "Saved image as draw.png." in result.text


class TestErrorMapping:
    async def test_tool_error_result_becomes_agent_tool_error(self) -> None:
        async with _stdio() as toolset:
            with pytest.raises(AgentToolError, match="order 42 is already closed"):
                await _call(toolset, "refuse")

    async def test_client_timeout_becomes_agent_tool_error(self) -> None:
        async with _stdio(timeout=0.5) as toolset:
            started = time.monotonic()
            with pytest.raises(AgentToolError, match=r"'nap' on 'fixture' timed out"):
                await _call(toolset, "nap", seconds=10)
        assert time.monotonic() - started < 5

    async def test_the_run_deadline_shortens_the_call(self) -> None:
        async with _stdio(timeout=30) as toolset:
            nap = {tool.name: tool for tool in await toolset.tools()}["nap"]
            context = AgentContext(deadline=time.monotonic() + 0.5)
            with pytest.raises(AgentToolError, match=r"timed out after 0\.[0-9]+s"):
                await nap.invoke({"seconds": 10}, context)

    async def test_an_expired_run_does_not_call_without_a_timeout(self) -> None:
        async with _stdio(timeout=30) as toolset:
            nap = {tool.name: tool for tool in await toolset.tools()}["nap"]
            context = AgentContext(deadline=time.monotonic() - 1)
            with pytest.raises(AgentToolError, match="no time left"):
                await nap.invoke({"seconds": 10}, context)

    async def test_unknown_remote_tool_becomes_agent_tool_error(self) -> None:
        async with _stdio(include=["echo"]) as toolset:
            ghost = toolset._adapt("ghost", "", {"type": "object"})
            with pytest.raises(AgentToolError):
                await ghost.invoke({}, AgentContext())


class TestLifecycle:
    async def test_disconnect_stops_the_subprocess(self) -> None:
        toolset = _stdio()
        await toolset.connect()
        pid = int((await _call(toolset, "pid")).text)
        assert _alive(pid)
        await toolset.disconnect()
        deadline = time.monotonic() + 10
        while _alive(pid) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not _alive(pid), f"server process {pid} outlived disconnect()"

    async def test_tools_need_a_connection(self) -> None:
        with pytest.raises(RuntimeError, match="not connected"):
            await _stdio().tools()

    async def test_a_tool_kept_after_disconnect_fails_readably(self) -> None:
        async with _stdio() as toolset:
            echo = {tool.name: tool for tool in await toolset.tools()}["echo"]
        assert not toolset.connected
        with pytest.raises(AgentToolError, match="not connected"):
            await echo.invoke({"text": "late"}, AgentContext())

    async def test_connect_and_disconnect_are_idempotent(self) -> None:
        toolset = _stdio()
        await toolset.connect()
        await toolset.connect()
        await toolset.disconnect()
        await toolset.disconnect()
        assert not toolset.connected

    def test_it_is_a_lifecycle_resource(self) -> None:
        from tempest_fastapi_sdk.tasks import LifecycleResource

        assert isinstance(_stdio(), LifecycleResource)


def test_the_package_imports_without_the_extra() -> None:
    code = textwrap.dedent(
        """
        import sys
        sys.modules["fastmcp"] = None
        import tempest_fastapi_sdk.mcp as mcp
        try:
            mcp.MCPToolset.http("http://x/mcp", name="x")
        except ImportError as exc:
            print(exc)
        """,
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert 'pip install "tempest-fastapi-sdk[fastmcp]"' in completed.stdout
