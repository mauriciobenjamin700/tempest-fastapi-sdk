"""Two rules ``mount_mcp`` enforces instead of documenting.

A default ``FastMCP`` sends the text of a tool's exception to the client,
so ``mount_mcp`` refuses a server that does not mask error details. And a
stateful session used to answer any authenticated caller presenting its
``Mcp-Session-Id``; it is now bound to the principal that opened it. The
hijack tests here fail on the endpoint without the binding.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytest.importorskip("fastmcp")

from fastapi import FastAPI
from fastmcp import FastMCP

from tempest_fastapi_sdk import (
    JWTUtils,
    make_bearer_token_dependency,
    register_exception_handlers,
)
from tempest_fastapi_sdk.agents import AgentContext, AgentToolError
from tempest_fastapi_sdk.mcp import MCPToolset, mcp_principal, mount_mcp
from tempest_fastapi_sdk.mcp._sessions import (
    SESSION_NOT_FOUND_BODY,
    SessionBindings,
    principal_key,
)
from tests.mcp.conftest import JWT_SECRET, serve

TOOL_SECRET: str = "postgres://admin:hunter2@db.internal/prod"

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
INITIALIZED: dict[str, Any] = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
}
MCP_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _whoami_call(request_id: int) -> dict[str, Any]:
    """Build a ``tools/call`` request for the ``whoami`` tool."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "whoami", "arguments": {}},
    }


def _headers(subject: str | None, session_id: str | None = None) -> dict[str, str]:
    """Build request headers for ``subject`` (``None`` for anonymous)."""
    headers = dict(MCP_HEADERS)
    if subject is not None:
        token = JWTUtils(JWT_SECRET).encode({"sub": subject, "typ": "access"})
        headers["Authorization"] = f"Bearer {token}"
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _stateful_app(*, allow_unauthenticated: bool = False) -> FastAPI:
    """Build an app serving a stateful endpoint with a ``whoami`` tool.

    Args:
        allow_unauthenticated (bool): Passed to ``mount_mcp``; with it the
            principal is a ``soft=True`` dependency.

    Returns:
        FastAPI: The app with ``/mcp`` mounted, ``stateless=False``.
    """
    user = make_bearer_token_dependency(
        JWTUtils(JWT_SECRET),
        soft=allow_unauthenticated,
    )
    app = FastAPI()
    register_exception_handlers(app, log_traceback=False)
    server = FastMCP("stateful", mask_error_details=True)

    @server.tool
    def whoami() -> str:
        """Return the subject of the caller, or ``anonymous``."""
        claims = mcp_principal(object)
        return str(claims["sub"]) if isinstance(claims, dict) else "anonymous"

    mount_mcp(
        app,
        server,
        principal=user,
        stateless=False,
        allow_unauthenticated=allow_unauthenticated,
    )
    return app


async def _open_session(client: httpx.AsyncClient, url: str, subject: str) -> str:
    """Initialize a session as ``subject`` and return its id."""
    opened = await client.post(url, json=INITIALIZE, headers=_headers(subject))
    assert opened.status_code == 200
    session_id = opened.headers["mcp-session-id"]
    acked = await client.post(
        url,
        json=INITIALIZED,
        headers=_headers(subject, session_id),
    )
    assert acked.status_code == 202
    return session_id


class TestErrorMasking:
    def test_a_server_that_does_not_mask_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"mask_error_details=True.*") as caught:
            mount_mcp(FastAPI(), FastMCP("plain"), allow_unauthenticated=True)
        assert "'plain'" in str(caught.value)

    def test_an_unmasking_mounted_server_is_refused(self) -> None:
        parent = FastMCP("parent", mask_error_details=True)
        parent.mount(FastMCP("child"), namespace="c")
        with pytest.raises(ValueError, match="'child'"):
            mount_mcp(FastAPI(), parent, allow_unauthenticated=True)

    def test_the_fastmcp_setting_counts_as_masking(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import fastmcp

        monkeypatch.setattr(fastmcp.settings, "mask_error_details", True)
        mount_mcp(FastAPI(), FastMCP("by-setting"), allow_unauthenticated=True)

    async def test_a_tool_exception_reaches_the_client_masked(self) -> None:
        app = FastAPI()
        server = FastMCP("masked", mask_error_details=True)

        @server.tool
        def boom() -> str:
            """Fail with a message that must stay on the server."""
            raise RuntimeError(TOOL_SECRET)

        mount_mcp(app, server, allow_unauthenticated=True)
        async with serve(app) as base, MCPToolset.http(f"{base}/mcp", name="m") as mcp:
            tools = {tool.name: tool for tool in await mcp.tools()}
            with pytest.raises(AgentToolError) as caught:
                await tools["boom"].invoke({}, AgentContext())
        assert TOOL_SECRET not in str(caught.value)
        assert "Error calling tool 'boom'" in str(caught.value)


class TestSessionOwner:
    async def test_the_owner_reuses_its_session(self) -> None:
        async with serve(_stateful_app()) as base, httpx.AsyncClient() as client:
            url = f"{base}/mcp"
            session_id = await _open_session(client, url, "ana")
            answer = await client.post(
                url,
                json=_whoami_call(2),
                headers=_headers("ana", session_id),
            )
        assert answer.status_code == 200
        assert '"text":"ana"' in answer.text

    async def test_another_principal_cannot_use_the_session(self) -> None:
        async with serve(_stateful_app()) as base, httpx.AsyncClient() as client:
            url = f"{base}/mcp"
            session_id = await _open_session(client, url, "ana")
            hijack = await client.post(
                url,
                json=_whoami_call(2),
                headers=_headers("bob", session_id),
            )
            still_ana = await client.post(
                url,
                json=_whoami_call(3),
                headers=_headers("ana", session_id),
            )
        assert hijack.status_code == 404
        assert hijack.content == SESSION_NOT_FOUND_BODY
        assert still_ana.status_code == 200

    async def test_the_refusal_matches_an_unknown_session(self) -> None:
        async with serve(_stateful_app()) as base, httpx.AsyncClient() as client:
            url = f"{base}/mcp"
            await _open_session(client, url, "ana")
            unknown = await client.post(
                url,
                json=_whoami_call(2),
                headers=_headers("bob", "0" * 32),
            )
        assert unknown.status_code == 404
        assert unknown.content == SESSION_NOT_FOUND_BODY

    async def test_an_anonymous_caller_cannot_use_a_users_session(self) -> None:
        app = _stateful_app(allow_unauthenticated=True)
        async with serve(app) as base, httpx.AsyncClient() as client:
            url = f"{base}/mcp"
            session_id = await _open_session(client, url, "ana")
            hijack = await client.post(
                url,
                json=_whoami_call(2),
                headers=_headers(None, session_id),
            )
        assert hijack.status_code == 404

    async def test_delete_releases_the_binding(self) -> None:
        app = _stateful_app()
        async with serve(app) as base, httpx.AsyncClient() as client:
            url = f"{base}/mcp"
            session_id = await _open_session(client, url, "ana")
            ended = await client.delete(url, headers=_headers("ana", session_id))
            after = await client.post(
                url,
                json=_whoami_call(2),
                headers=_headers("ana", session_id),
            )
        assert ended.status_code == 200
        assert after.status_code == 404

    async def test_the_stateless_default_issues_no_session(self) -> None:
        app = FastAPI()
        mount_mcp(
            app,
            FastMCP("stateless", mask_error_details=True),
            allow_unauthenticated=True,
        )
        async with serve(app) as base, httpx.AsyncClient() as client:
            opened = await client.post(
                f"{base}/mcp",
                json=INITIALIZE,
                headers=MCP_HEADERS,
            )
        assert opened.status_code == 200
        assert "mcp-session-id" not in opened.headers


class TestSessionBindings:
    async def test_an_idle_binding_is_pruned_on_the_next_open(self) -> None:
        now = [0.0]
        bindings = SessionBindings(idle_seconds=10.0, clock=lambda: now[0])
        issued = iter(["s1", "s2"])

        async def transport(scope: Any, receive: Any, send: Any) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"mcp-session-id", next(issued).encode())],
                },
            )
            await send({"type": "http.response.body", "body": b""})

        async def sink(_message: Any) -> None:
            return None

        scope: dict[str, Any] = {"type": "http", "method": "POST", "headers": []}
        await bindings.serve(transport, "sub:ana", scope, sink, sink)
        assert len(bindings) == 1
        now[0] = 11.0
        await bindings.serve(transport, "sub:ana", scope, sink, sink)
        assert len(bindings) == 1


class TestPrincipalKey:
    def test_claims_are_keyed_by_subject(self) -> None:
        first = principal_key({"sub": "ana", "exp": 1})
        refreshed = principal_key({"sub": "ana", "exp": 2})
        assert first == refreshed == "sub:ana"

    def test_an_object_is_keyed_by_type_and_id(self) -> None:
        class UserModel:
            def __init__(self, user_id: int) -> None:
                self.id = user_id

        assert principal_key(UserModel(7)) == principal_key(UserModel(7))
        assert principal_key(UserModel(7)) != principal_key(UserModel(8))

    def test_anonymous_is_none(self) -> None:
        assert principal_key(None) is None

    @pytest.mark.parametrize("principal", [{"role": "x"}, object()])
    def test_a_principal_without_identity_is_refused(self, principal: Any) -> None:
        with pytest.raises(TypeError, match="stateless=False"):
            principal_key(principal)
