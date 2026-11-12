"""The FastMCP provider behind :func:`~tempest_fastapi_sdk.mcp.add_fastapi_routes`.

Private, and imported only from inside that function: it needs the
``[fastmcp]`` extra at module level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx2
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers.openapi import MCPType, OpenAPIProvider

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastmcp.server.providers.openapi import (
        OpenAPIResource,
        OpenAPIResourceTemplate,
        OpenAPITool,
    )
    from fastmcp.utilities.openapi import HTTPRoute

_CREDENTIAL_LOCATIONS: frozenset[str] = frozenset({"header", "cookie"})


def _selected_operations(
    spec: dict[str, Any],
    operations: set[str],
    tags: set[str],
) -> list[str]:
    """Resolve the selection against the document and refuse what misses.

    Args:
        spec (dict[str, Any]): The app's OpenAPI document.
        operations (set[str]): Requested ``operation_id`` values.
        tags (set[str]): Requested tags.

    Returns:
        list[str]: Every selected ``operation_id``, sorted.

    Raises:
        ValueError: When an operation id is unknown or a tag matches nothing.
    """
    by_tag: dict[str, set[str]] = {}
    known: set[str] = set()
    for item in spec.get("paths", {}).values():
        for operation in item.values():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            operation_id = operation["operationId"]
            known.add(operation_id)
            for tag in operation.get("tags", []):
                by_tag.setdefault(tag, set()).add(operation_id)
    unknown = sorted(operations - known)
    if unknown:
        raise ValueError(f"no route has operation_id {', '.join(unknown)}")
    empty = sorted(tag for tag in tags if tag not in by_tag)
    if empty:
        raise ValueError(f"no route is tagged {', '.join(empty)}")
    selected = set(operations)
    for tag in tags:
        selected |= by_tag[tag]
    return sorted(selected)


def _forwarding_hook(forward_headers: set[str]) -> Any:
    """Build the request hook that carries the MCP caller's credentials.

    The ``Host`` of the MCP request is copied too, so a
    ``TrustedHostMiddleware`` accepts the call and URLs the route builds
    (``request.url_for``) name the host the caller used, not the internal
    base URL.

    Args:
        forward_headers (set[str]): Lower-case header names to forward.

    Returns:
        Any: An async ``httpx2`` request event hook.
    """

    async def forward(request: httpx2.Request) -> None:
        try:
            incoming = get_http_request().headers
        except RuntimeError:
            incoming = None
        if incoming is not None and "host" in incoming:
            request.headers["host"] = incoming["host"]
        for header in forward_headers:
            value = incoming.get(header) if incoming is not None else None
            if value is None:
                request.headers.pop(header, None)
            else:
                request.headers[header] = value

    return forward


class _CallerASGITransport(httpx2.ASGITransport):
    """An in-process transport that presents the MCP caller's address.

    ``httpx2.ASGITransport`` puts a fixed ``("127.0.0.1", 123)`` in every
    scope, so every route call made for any MCP client would share one
    loopback address: one per-IP rate-limit bucket for all of them, and
    access logs that name nobody. The ``client`` read here is the address
    of the MCP request being served, falling back to the fixed one outside
    an HTTP request (a stdio server).
    """

    _fallback_client: tuple[str, int] = ("127.0.0.1", 123)

    @property
    def client(self) -> tuple[str, int]:
        """The peer address put in the route call's ASGI scope.

        Returns:
            tuple[str, int]: The MCP request's client address, or the
            fallback outside an HTTP request.
        """
        try:
            peer = get_http_request().client
        except RuntimeError:
            peer = None
        if peer is None:
            return self._fallback_client
        return (peer.host, peer.port)

    @client.setter
    def client(self, value: tuple[str, int]) -> None:
        """Keep the address used outside an HTTP request.

        Args:
            value (tuple[str, int]): The fallback address.
        """
        self._fallback_client = value


def _strip_forwarded_parameters(forward_headers: set[str]) -> Any:
    """Build the component hook that hides forwarded headers from the model.

    Args:
        forward_headers (set[str]): Lower-case header names that are forwarded.

    Returns:
        Any: A ``mcp_component_fn`` for :class:`OpenAPIProvider`.
    """

    def strip(
        route: HTTPRoute,
        component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
    ) -> None:
        hidden: set[str] = set()
        for parameter in route.parameters:
            if parameter.location not in _CREDENTIAL_LOCATIONS:
                continue
            if parameter.name.lower() in forward_headers or (
                parameter.location == "cookie" and "cookie" in forward_headers
            ):
                hidden |= {parameter.name, f"{parameter.name}__{parameter.location}"}
        schema = getattr(component, "parameters", None)
        if not hidden or not isinstance(schema, dict):
            return
        properties = schema.get("properties", {})
        for name in hidden:
            properties.pop(name, None)
        if "required" in schema:
            schema["required"] = [n for n in schema["required"] if n not in hidden]

    return strip


def build_route_provider(
    spec: dict[str, Any],
    app: FastAPI,
    *,
    operations: set[str],
    tags: set[str],
    forward_headers: set[str],
    timeout: float,
) -> tuple[OpenAPIProvider, list[str]]:
    """Build the provider that turns the selected routes into tools.

    The in-process client runs with ``raise_app_exceptions=False``: an
    unhandled exception in a route reaches the tool as the ``500`` the app
    answers over HTTP, never as the exception itself, whose text FastMCP
    would otherwise hand to the MCP client (``Error calling tool 'x':
    <message>``) — a DSN or a token included.

    Args:
        spec (dict[str, Any]): The app's OpenAPI document (a private copy).
        app (FastAPI): The app the tools call, in process.
        operations (set[str]): ``operation_id`` values to expose.
        tags (set[str]): Tags whose routes are exposed.
        forward_headers (set[str]): Lower-case credential headers to forward.
        timeout (float): Seconds a route call may take.

    Returns:
        tuple[OpenAPIProvider, list[str]]: The provider, and the sorted
        ``operation_id`` of every exposed route.
    """
    selected = _selected_operations(spec, operations, tags)
    chosen = set(selected)
    client = httpx2.AsyncClient(
        transport=_CallerASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://fastapi",
        timeout=timeout,
        event_hooks={"request": [_forwarding_hook(forward_headers)]},
    )

    def route_type(route: HTTPRoute, _default: MCPType) -> MCPType:
        return MCPType.TOOL if route.operation_id in chosen else MCPType.EXCLUDE

    provider = OpenAPIProvider(
        spec,
        client,
        route_map_fn=route_type,
        mcp_component_fn=_strip_forwarded_parameters(forward_headers),
    )
    return provider, selected


__all__: list[str] = ["build_route_provider"]
