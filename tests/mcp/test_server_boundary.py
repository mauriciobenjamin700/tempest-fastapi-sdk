"""What the MCP endpoint lets through, and what a route call carries.

Each test here was written against a defect the endpoint shipped with: a
``soft=True`` principal that resolved ``None`` opened the endpoint, an
unhandled route exception reached the MCP client with its text, and a route
call presented itself as ``127.0.0.1:123`` on host ``fastapi`` whoever the
caller was. The last class pins the client side: a redirect off the
configured origin is not followed with the toolset's credentials.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytest.importorskip("fastmcp")

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastmcp import Client, FastMCP

from tempest_fastapi_sdk import (
    JWTUtils,
    make_bearer_token_dependency,
    register_exception_handlers,
)
from tempest_fastapi_sdk.agents import AgentContext, AgentToolError
from tempest_fastapi_sdk.mcp import (
    MCPToolset,
    add_agent_tools,
    add_fastapi_routes,
    mcp_principal,
    mount_mcp,
)
from tests.mcp.conftest import JWT_SECRET, serve
from tests.mcp.mcp_fixture_server import AGENT_TOOLS

ROUTE_SECRET_TEXT: str = "postgres://admin:hunter2@db.internal/prod"

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


def _soft_app(*, allow_unauthenticated: bool = False) -> FastAPI:
    """Build an app whose principal is an optional-user dependency.

    Args:
        allow_unauthenticated (bool): Passed to ``mount_mcp``.

    Returns:
        FastAPI: The app with ``/mcp`` mounted behind the soft principal.
    """
    soft_user = make_bearer_token_dependency(JWTUtils(JWT_SECRET), soft=True)
    app = FastAPI()
    register_exception_handlers(app, log_traceback=False)
    server = FastMCP("soft", mask_error_details=True)
    add_agent_tools(server, AGENT_TOOLS[:1])

    @server.tool
    def caller() -> str:
        """Return the principal the endpoint resolved."""
        return repr(mcp_principal(object))

    @server.tool
    def claims() -> str:
        """Return the principal as the claims dictionary."""
        return str(mcp_principal(dict)["sub"])

    mount_mcp(
        app,
        server,
        principal=soft_user,
        allow_unauthenticated=allow_unauthenticated,
    )
    return app


def _route_app() -> FastAPI:
    """Build an app exposing an introspection route.

    Returns:
        FastAPI: The app, ``/mcp`` open so the route call is the subject.
    """
    app = FastAPI()
    register_exception_handlers(app, log_traceback=False)

    @app.get("/peer", operation_id="peer")
    async def peer(request: Request) -> dict[str, Any]:
        client = request.client
        return {
            "host": request.headers.get("host"),
            "port": client.port if client is not None else None,
        }

    server = FastMCP("routes", mask_error_details=True)
    add_fastapi_routes(server, app, operations=["peer"])
    mount_mcp(app, server, allow_unauthenticated=True)
    return app


async def _call(toolset: MCPToolset, name: str) -> Any:
    tools = {tool.name: tool for tool in await toolset.tools()}
    return await tools[name].invoke({}, AgentContext())


class TestSoftPrincipal:
    async def test_an_anonymous_request_is_refused(self) -> None:
        async with serve(_soft_app()) as base, httpx.AsyncClient() as client:
            response = await client.post(
                f"{base}/mcp",
                json=INITIALIZE,
                headers=MCP_HEADERS,
            )
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"

    async def test_a_valid_token_still_reaches_the_server(self) -> None:
        token = JWTUtils(JWT_SECRET).encode({"sub": "ana", "typ": "access"})
        async with (
            serve(_soft_app()) as base,
            MCPToolset.http(f"{base}/mcp", name="s", token=token) as mcp,
        ):
            result = await _call(mcp, "caller")
        assert "'sub': 'ana'" in result.text

    async def test_the_explicit_opt_in_serves_anonymous_callers(self) -> None:
        app = _soft_app(allow_unauthenticated=True)
        async with serve(app) as base, MCPToolset.http(f"{base}/mcp", name="s") as mcp:
            result = await _call(mcp, "caller")
            with pytest.raises(AgentToolError, match="Error calling tool 'claims'"):
                await _call(mcp, "claims")
        assert result.text == "None"


class TestRouteCall:
    async def test_an_unhandled_route_exception_stays_on_the_server(self) -> None:
        """The route's own ``500`` reaches the tool, not its exception.

        Checked on a server that does **not** mask error details, called in
        memory: ``mount_mcp`` would refuse it, and on a masking server every
        failure reads ``Error calling tool 'boom'``, which hides whether the
        exception text ever left the route call.
        """
        app = FastAPI()
        register_exception_handlers(app, log_traceback=False)

        @app.get("/boom", operation_id="boom")
        async def boom() -> dict[str, str]:
            raise RuntimeError(ROUTE_SECRET_TEXT)

        server = FastMCP("unmasked")
        add_fastapi_routes(server, app, operations=["boom"])
        async with Client(server) as client:
            result = await client.call_tool("boom", {}, raise_on_error=False)
        text = str(result.content)
        assert result.is_error
        assert ROUTE_SECRET_TEXT not in text
        assert "500" in text

    async def test_the_route_sees_the_callers_host_and_address(self) -> None:
        async with (
            serve(_route_app()) as base,
            MCPToolset.http(f"{base}/mcp", name="r") as mcp,
        ):
            result = await _call(mcp, "peer")
        compact = result.text.replace(" ", "")
        assert f'"host":"{base.removeprefix("http://")}"' in compact
        assert '"port":123' not in compact


class TestClientRedirect:
    async def test_a_cross_origin_redirect_does_not_carry_the_credentials(
        self,
    ) -> None:
        seen: list[dict[str, str]] = []
        elsewhere = FastAPI()

        @elsewhere.api_route("/mcp", methods=["GET", "POST", "DELETE"])
        async def record(request: Request) -> dict[str, str]:
            seen.append(dict(request.headers))
            return {}

        async with serve(elsewhere) as target:
            bouncer = FastAPI()

            @bouncer.api_route("/mcp", methods=["GET", "POST", "DELETE"])
            async def bounce() -> RedirectResponse:
                return RedirectResponse(f"{target}/mcp", status_code=307)

            async with serve(bouncer) as base:
                toolset = MCPToolset.http(
                    f"{base}/mcp",
                    name="b",
                    token="bearer-secret",
                    headers={"X-Token": "shared-secret"},
                )
                with pytest.raises(Exception):  # noqa: B017
                    await toolset.connect()
        assert seen == []
