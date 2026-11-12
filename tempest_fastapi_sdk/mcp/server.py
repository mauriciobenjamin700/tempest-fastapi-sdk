"""Serve an MCP endpoint from a FastAPI app built with this SDK.

Three pieces, each usable on its own, around a ``fastmcp.FastMCP`` server
you build and decorate yourself (``@server.tool``, ``@server.resource``,
``@server.prompt`` derive the schema from the type hints):

* :func:`mount_mcp` serves it on a path of your app, behind the same FastAPI
  dependencies your routes use (``current_user``, a token or permission
  dependency) and inside the app's lifespan.
* :func:`add_agent_tools` exposes :class:`~tempest_fastapi_sdk.agents.AgentTool`
  objects you already wrote for an agent, schema and all.
* :func:`add_fastapi_routes` exposes routes you pick by ``operation_id`` or
  tag, with the schema from the app's own OpenAPI document and the caller's
  credentials forwarded, so the route's own dependencies decide.

Everything here needs the ``[fastmcp]`` extra; the module imports without
it and each function raises an ``ImportError`` naming the extra.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Callable, Collection, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.params import Depends as DependsParam

from tempest_fastapi_sdk.mcp._dependency import require_fastmcp

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools import Tool
    from starlette.types import ASGIApp, Receive, Scope, Send

    from tempest_fastapi_sdk.agents.tools import AgentTool


PRINCIPAL_STATE_KEY: str = "tempest_mcp_principal"
"""``request.state`` attribute holding what the ``principal`` dependency
resolved for the current MCP request."""

DEFAULT_MCP_TOOL_TIMEOUT: float = 120.0
"""Seconds an exposed agent tool may run per call — the same ceiling
:class:`~tempest_fastapi_sdk.agents.AgentBudget` puts on a whole run, since
a hung tool otherwise holds the client's HTTP request open indefinitely."""

DEFAULT_FORWARDED_HEADERS: tuple[str, ...] = ("authorization", "cookie")
"""Credential headers :func:`add_fastapi_routes` copies from the MCP request
to the route it calls."""


class _ASGIPassthrough(Response):
    """A response that hands the request to another ASGI app.

    Returned from the route that guards the MCP endpoint: by the time it is
    sent, FastAPI has already run the route's dependencies, so the inner app
    only ever sees requests that passed them. The request body is still
    unread at that point, which is what lets the inner app consume it.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Hold the app to delegate to.

        Args:
            app (ASGIApp): The MCP transport app.
        """
        super().__init__()
        self._app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Let the inner app answer the request.

        Args:
            scope (Scope): The ASGI scope of the guarded request.
            receive (Receive): Its receive channel, body unread.
            send (Send): Its send channel.
        """
        await self._app(scope, receive, send)


def mount_mcp(
    app: FastAPI,
    server: FastMCP,
    *,
    path: str = "/mcp",
    principal: Callable[..., Any] | None = None,
    dependencies: Sequence[DependsParam] = (),
    stateless: bool = True,
    allow_unauthenticated: bool = False,
) -> None:
    """Serve ``server`` over streamable HTTP at ``path`` of ``app``.

    The endpoint is an ordinary FastAPI route (``GET``, ``POST`` and
    ``DELETE`` on ``path``, kept out of the OpenAPI document), so the
    dependencies you pass run exactly as they do on your other routes: the
    SDK's ``current_user`` built by ``make_jwt_user_dependency``, a
    ``make_token_dependency`` shared secret, a ``make_permission_dependency``
    scope check. A request they refuse gets the same ``401``/``403`` your API
    answers and never reaches the MCP server.

    The MCP transport has to run inside the app's lifespan (it owns a task
    group for its sessions), so ``app.router.lifespan_context`` is replaced by
    one that enters your existing lifespan first and the transport inside it:
    the transport stops before the resources your lifespan opened are closed.
    Serve the app with its lifespan on (uvicorn does by default; a test client
    needs ``with TestClient(app)`` or an equivalent).

    Args:
        app (FastAPI): The application to mount on. Call this after setting
            the app's own lifespan.
        server (FastMCP): The MCP server, already holding its tools.
        path (str): Where the endpoint lives. Defaults to ``"/mcp"``.
        principal (Callable[..., Any] | None): A dependency that resolves the
            caller — typically your ``current_user``. It runs on every MCP
            request, refuses the ones it would refuse on a route, and its
            result is what :func:`mcp_principal` returns inside a tool.
        dependencies (Sequence[DependsParam]): Further gates, as on
            ``APIRouter(dependencies=...)`` — a permission or role check.
        stateless (bool): Serve each request on its own, without an
            ``Mcp-Session-Id``. Defaults to ``True``: every request is
            authenticated on its own anyway, and a session that outlives the
            token that opened it, or is presented with another user's token,
            is exactly what this avoids. Set ``False`` for server-to-client
            features that need a session (progress notifications on a later
            request, sampling).
        allow_unauthenticated (bool): Serve without ``principal`` and without
            ``dependencies``. Refused otherwise, because an MCP endpoint
            runs tools on the caller's behalf and an open one runs them for
            anyone who finds the URL.

    Raises:
        ValueError: When neither ``principal`` nor ``dependencies`` is given
            and ``allow_unauthenticated`` is ``False``.
        ImportError: When the ``[fastmcp]`` extra is not installed.
    """
    require_fastmcp()
    if principal is None and not dependencies and not allow_unauthenticated:
        raise ValueError(
            "mount_mcp needs principal= or dependencies= to authenticate MCP "
            "clients; pass allow_unauthenticated=True to serve it open.",
        )
    transport = server.http_app(path=path, stateless_http=stateless)

    if principal is None:

        async def _mcp_endpoint(request: Request) -> Response:
            return _ASGIPassthrough(transport)

    else:

        async def _mcp_endpoint(  # type: ignore[misc]
            request: Request,
            caller: Any = Depends(principal),
        ) -> Response:
            setattr(request.state, PRINCIPAL_STATE_KEY, caller)
            return _ASGIPassthrough(transport)

    _mcp_endpoint.__doc__ = "Serve the MCP streamable HTTP transport."
    app.add_api_route(
        path,
        _mcp_endpoint,
        methods=["GET", "POST", "DELETE"],
        dependencies=list(dependencies),
        include_in_schema=False,
        name="mcp",
    )

    outer = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan(lifespan_app: Any) -> AsyncIterator[Any]:
        async with outer(lifespan_app) as state, transport.lifespan(transport):
            yield state

    app.router.lifespan_context = _lifespan


def mcp_principal[T](kind: type[T]) -> T:
    """Return the caller ``mount_mcp(principal=...)`` resolved, inside a tool.

    Example:

        >>> @server.tool
        ... async def my_orders() -> list[str]:
        ...     user = mcp_principal(User)
        ...     return await orders_of(user.id)

    Args:
        kind (type[T]): The type the principal dependency returns — your user
            model, or ``dict`` for the claims of
            ``make_bearer_token_dependency``. Checked with ``isinstance``, so
            the return value is typed for the caller.

    Returns:
        T: The resolved principal of the request being served.

    Raises:
        LookupError: Outside an MCP HTTP request, or when the endpoint was
            mounted without ``principal=``.
        TypeError: When the principal is not a ``kind``.
        ImportError: When the ``[fastmcp]`` extra is not installed.
    """
    require_fastmcp()
    from fastmcp.server.dependencies import get_http_request

    try:
        request = get_http_request()
    except RuntimeError as exc:
        raise LookupError("mcp_principal() called outside an MCP request") from exc
    missing = object()
    found = getattr(request.state, PRINCIPAL_STATE_KEY, missing)
    if found is missing:
        raise LookupError("the MCP endpoint was mounted without principal=")
    if not isinstance(found, kind):
        raise TypeError(
            f"the MCP principal is a {type(found).__name__}, not a {kind.__name__}",
        )
    return found


def add_agent_tools(
    server: FastMCP,
    tools: Iterable[AgentTool],
    *,
    timeout: float | None = DEFAULT_MCP_TOOL_TIMEOUT,
) -> list[Tool]:
    """Expose agent tools as MCP tools, with the schema they already declare.

    Each call runs the tool's handler under a fresh
    :class:`~tempest_fastapi_sdk.agents.AgentContext` — an MCP call carries
    no earlier artifacts — and returns its text followed by its artifacts:
    images and audio as MCP ``image``/``audio`` content, anything else as an
    embedded binary resource. The failure contract is the agent's: an
    :class:`~tempest_fastapi_sdk.agents.AgentToolError` reaches the client
    with its message, any other exception as ``"tool failed: <Type>"``.

    Args:
        server (FastMCP): The server to register on.
        tools (Iterable[AgentTool]): The agent tools to expose.
        timeout (float | None): Seconds one call may take before the client
            gets ``"tool <name> timed out"``. Defaults to
            :data:`DEFAULT_MCP_TOOL_TIMEOUT`; ``None`` removes the ceiling.

    Returns:
        list[Tool]: The registered FastMCP tools, in order.

    Raises:
        ImportError: When the ``[fastmcp]`` extra is not installed.
    """
    require_fastmcp()
    from tempest_fastapi_sdk.mcp._agent_tool import AgentToolAdapter

    return [
        server.add_tool(AgentToolAdapter.wrap(tool, timeout=timeout)) for tool in tools
    ]


def add_fastapi_routes(
    server: FastMCP,
    app: FastAPI,
    *,
    operations: Collection[str] = (),
    tags: Collection[str] = (),
    forward_headers: Collection[str] = DEFAULT_FORWARDED_HEADERS,
    timeout: float = 30.0,
) -> list[str]:
    """Expose selected routes of ``app`` as MCP tools.

    The tool schema comes from the app's OpenAPI document — the Pydantic
    models the routes already declare — and each call goes to the route
    in process (an ASGI transport, no socket). Only what you name is exposed:
    a route is picked when its ``operation_id`` is in ``operations`` or it
    carries one of ``tags``.

    The headers in ``forward_headers`` are copied from the MCP request onto
    the route call, and removed from the call when the MCP request has none,
    so the route's own dependencies authenticate the **MCP caller** — never
    an anonymous internal call, and never a value the model put in the
    arguments. Header and cookie parameters with those names are taken out
    of the tool's input schema for the same reason.

    Call it after the routes are included. The OpenAPI document is generated
    once, here, and the app's cached copy is left as it was.

    Args:
        server (FastMCP): The server to register on.
        app (FastAPI): The application whose routes are exposed.
        operations (Collection[str]): ``operation_id`` values to expose.
        tags (Collection[str]): OpenAPI tags whose routes are exposed.
        forward_headers (Collection[str]): Headers copied from the MCP
            request, case-insensitive. Defaults to ``Authorization`` and
            ``Cookie``; add the header a ``make_token_dependency`` reads.
        timeout (float): Seconds a route call may take.

    Returns:
        list[str]: The ``operation_id`` of every exposed route, sorted.

    Raises:
        ValueError: When neither ``operations`` nor ``tags`` is given, when an
            operation id does not exist, or when a tag matches no route.
        ImportError: When the ``[fastmcp]`` extra is not installed.
    """
    require_fastmcp()
    from tempest_fastapi_sdk.mcp._routes import build_route_provider

    if not operations and not tags:
        raise ValueError(
            "add_fastapi_routes needs operations= or tags=; exposing every "
            "route of the app is not a default",
        )
    cached = app.openapi_schema
    spec = copy.deepcopy(app.openapi())
    app.openapi_schema = cached
    provider, selected = build_route_provider(
        spec,
        app,
        operations=set(operations),
        tags=set(tags),
        forward_headers={header.lower() for header in forward_headers},
        timeout=timeout,
    )
    server.add_provider(provider)
    return selected


__all__: list[str] = [
    "DEFAULT_FORWARDED_HEADERS",
    "DEFAULT_MCP_TOOL_TIMEOUT",
    "PRINCIPAL_STATE_KEY",
    "add_agent_tools",
    "add_fastapi_routes",
    "mcp_principal",
    "mount_mcp",
]
