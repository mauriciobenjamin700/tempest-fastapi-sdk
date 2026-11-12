"""``mount_mcp``, ``add_agent_tools`` and ``add_fastapi_routes`` over real HTTP.

Every test here runs the app under uvicorn on a loopback port and talks to
it with the SDK's own :class:`MCPToolset` (or a raw ``httpx`` request when
the status code is the assertion), so the transport, the auth gate and the
lifespan are the ones a deployed service runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

pytest.importorskip("fastmcp")

from fastapi import FastAPI
from fastmcp import FastMCP

from tempest_fastapi_sdk.agents import AgentContext, AgentToolError
from tempest_fastapi_sdk.mcp import (
    MCPToolset,
    add_agent_tools,
    add_fastapi_routes,
    mount_mcp,
)
from tests.mcp.conftest import LiveApp, OrderInSchema, serve
from tests.mcp.mcp_fixture_server import (
    AGENT_TOOLS,
    LEAKED_SECRET,
    PDF_BYTES,
    PNG_BYTES,
    QuoteRequestSchema,
)

INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "raw", "version": "0"},
    },
}
MCP_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _without_titles(schema: Any) -> Any:
    """Drop ``title`` keys, which the MCP side strips from the schema."""
    if isinstance(schema, dict):
        return {k: _without_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_without_titles(item) for item in schema]
    return schema


@asynccontextmanager
async def _toolset(live: LiveApp, subject: str = "alice") -> AsyncIterator[MCPToolset]:
    async with MCPToolset.http(
        live.url,
        name="app",
        token=live.token(subject),
        headers={"X-Route-Secret": "from-client"},
    ) as toolset:
        yield toolset


async def _call(toolset: MCPToolset, name: str, **arguments: Any) -> Any:
    tools = {tool.name: tool for tool in await toolset.tools()}
    return await tools[name].invoke(arguments, AgentContext())


class TestAuthentication:
    async def test_request_without_token_is_refused_with_the_sdk_401(
        self,
        live: LiveApp,
    ) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.post(live.url, json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"

    async def test_request_with_a_forged_token_is_refused(self, live: LiveApp) -> None:
        headers = {**MCP_HEADERS, "Authorization": "Bearer not-a-jwt"}
        async with httpx.AsyncClient() as client:
            response = await client.post(live.url, json=INITIALIZE, headers=headers)
        assert response.status_code == 401

    async def test_request_with_a_valid_token_reaches_the_server(
        self,
        live: LiveApp,
    ) -> None:
        headers = {**MCP_HEADERS, "Authorization": f"Bearer {live.token('alice')}"}
        async with httpx.AsyncClient() as client:
            response = await client.post(live.url, json=INITIALIZE, headers=headers)
        assert response.status_code == 200
        assert "fixture" in response.text

    async def test_the_sdk_client_without_a_token_cannot_connect(
        self,
        live: LiveApp,
    ) -> None:
        toolset = MCPToolset.http(live.url, name="app")
        with pytest.raises(Exception, match=r"401|Unauthorized|error response"):
            await toolset.connect()
        assert not toolset.connected

    async def test_each_request_sees_its_own_principal(self, live: LiveApp) -> None:
        async with _toolset(live, "alice") as alice, _toolset(live, "bob") as bob:
            results = await asyncio.gather(
                _call(alice, "whoami"),
                _call(bob, "whoami"),
            )
        assert [result.text for result in results] == ["alice", "bob"]

    def test_mounting_without_any_gate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="allow_unauthenticated"):
            mount_mcp(FastAPI(), FastMCP("open"))

    async def test_an_explicitly_open_endpoint_serves_anyone(self) -> None:
        app = FastAPI()
        server = FastMCP("open")
        add_agent_tools(server, AGENT_TOOLS[:1])
        mount_mcp(app, server, allow_unauthenticated=True)
        async with serve(app) as base, MCPToolset.http(f"{base}/mcp", name="o") as mcp:
            result = await _call(mcp, "echo", text="hi")
        assert result.text == "echo: hi"


class TestLifespan:
    async def test_the_app_lifespan_wraps_the_mcp_transport(self) -> None:
        events: list[str] = []

        @asynccontextmanager
        async def app_lifespan(_app: FastAPI) -> AsyncIterator[None]:
            events.append("app-start")
            yield
            events.append("app-stop")

        app = FastAPI(lifespan=app_lifespan)
        server = FastMCP("ordered")

        @server.tool
        def ping() -> str:
            """Record that the MCP transport served a call."""
            events.append("mcp-call")
            return "pong"

        mount_mcp(app, server, allow_unauthenticated=True)
        async with serve(app) as base, MCPToolset.http(f"{base}/mcp", name="o") as mcp:
            await _call(mcp, "ping")
        assert events == ["app-start", "mcp-call", "app-stop"]


class TestAgentTools:
    async def test_round_trip_through_the_sdk_client(self, live: LiveApp) -> None:
        async with _toolset(live) as toolset:
            result = await _call(toolset, "echo", text="over http")
        assert result.text == "echo: over http"

    async def test_the_schema_is_the_agent_tools_own(self, live: LiveApp) -> None:
        async with _toolset(live) as toolset:
            remote = {tool.name: tool.parameters for tool in await toolset.tools()}
        for tool in AGENT_TOOLS:
            assert remote[tool.name] == tool.parameters

    async def test_artifacts_cross_as_bytes(self, live: LiveApp) -> None:
        async with _toolset(live) as toolset:
            result = await _call(toolset, "draw")
        by_type = {artifact.media_type: artifact for artifact in result.artifacts}
        assert by_type["image/png"].data == PNG_BYTES
        assert by_type["application/pdf"].data == PDF_BYTES
        assert by_type["application/pdf"].name == "report.pdf"
        assert "Drew chart.png" in result.text

    async def test_agent_tool_error_reaches_the_client_verbatim(
        self,
        live: LiveApp,
    ) -> None:
        async with _toolset(live) as toolset:
            with pytest.raises(AgentToolError, match="order 42 is already closed"):
                await _call(toolset, "refuse")

    async def test_unexpected_exception_text_stays_on_the_server(
        self,
        live: LiveApp,
    ) -> None:
        async with _toolset(live) as toolset:
            with pytest.raises(AgentToolError) as caught:
                await _call(toolset, "explode")
        assert str(caught.value) == "tool failed: RuntimeError"
        assert LEAKED_SECRET not in str(caught.value)

    async def test_server_side_timeout_answers_the_client(
        self,
        make_live: Callable[..., Any],
    ) -> None:
        async with make_live(tool_timeout=0.2) as live, _toolset(live) as toolset:
            with pytest.raises(AgentToolError, match=r"'nap' timed out after 0.2s"):
                await _call(toolset, "nap", seconds=5)

    async def test_native_tool_schema_is_derived_from_pydantic(
        self,
        live: LiveApp,
    ) -> None:
        async with _toolset(live) as toolset:
            remote = {tool.name: tool.parameters for tool in await toolset.tools()}
            result = await _call(
                toolset,
                "quote",
                request={"sku": "A1", "quantity": 3},
            )
        expected = _without_titles(QuoteRequestSchema.model_json_schema())
        assert _without_titles(remote["quote"]["properties"]["request"]) == expected
        assert result.text == "3 x A1 = 30"


class TestFastAPIRoutes:
    async def test_only_the_selected_route_is_exposed(self, live: LiveApp) -> None:
        async with _toolset(live) as toolset:
            names = {tool.name for tool in await toolset.tools()}
        assert "create_order" in names
        assert "delete_order" not in names

    async def test_the_route_schema_is_the_pydantic_body(self, live: LiveApp) -> None:
        async with _toolset(live) as toolset:
            tools = {tool.name: tool for tool in await toolset.tools()}
        schema = tools["create_order"].parameters
        expected = _without_titles(OrderInSchema.model_json_schema())
        for name, prop in expected["properties"].items():
            assert _without_titles(schema["properties"][name]) == prop
        assert set(schema["required"]) == set(expected["required"])

    async def test_forwarded_headers_are_not_tool_arguments(
        self,
        live: LiveApp,
    ) -> None:
        async with _toolset(live) as toolset:
            tools = {tool.name: tool for tool in await toolset.tools()}
        properties = {p.lower() for p in tools["create_order"].parameters["properties"]}
        assert not properties & {"authorization", "x-route-secret", "x_route_secret"}

    async def test_the_route_authenticates_the_mcp_caller(self, live: LiveApp) -> None:
        async with _toolset(live, "carol") as toolset:
            result = await _call(toolset, "create_order", sku="B2", quantity=2)
        assert '"owner":"carol"' in result.text.replace(" ", "")
        assert live.route_secrets == ["from-client"]

    async def test_a_model_supplied_header_does_not_override_the_caller(
        self,
        live: LiveApp,
    ) -> None:
        async with _toolset(live, "carol") as toolset:
            result = await _call(
                toolset,
                "create_order",
                sku="B2",
                quantity=2,
                x_route_secret="from-model",
                authorization=f"Bearer {live.token('mallory')}",
            )
        assert '"owner":"carol"' in result.text.replace(" ", "")
        assert live.route_secrets == ["from-client"]

    def test_selection_is_required(self) -> None:
        with pytest.raises(ValueError, match="operations= or tags="):
            add_fastapi_routes(FastMCP("x"), FastAPI())

    def test_unknown_operation_and_tag_are_refused(self) -> None:
        app = FastAPI()

        @app.get("/a", operation_id="read_a", tags=["a"])
        async def read_a() -> str:
            return "a"

        with pytest.raises(ValueError, match="operation_id nope"):
            add_fastapi_routes(FastMCP("x"), app, operations=["nope"])
        with pytest.raises(ValueError, match="tagged missing"):
            add_fastapi_routes(FastMCP("x"), app, tags=["missing"])

    def test_tags_select_and_the_openapi_cache_is_left_alone(self) -> None:
        app = FastAPI()

        @app.get("/a", operation_id="read_a", tags=["a"])
        async def read_a() -> str:
            return "a"

        selected = add_fastapi_routes(FastMCP("x"), app, tags=["a"])

        @app.get("/b", operation_id="read_b")
        async def read_b() -> str:
            return "b"

        assert selected == ["read_a"]
        assert "/b" in app.openapi()["paths"]
