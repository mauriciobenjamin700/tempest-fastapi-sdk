"""Shared fixtures: a FastAPI app with an MCP endpoint, served by uvicorn."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

pytest.importorskip("fastmcp")

import uvicorn
from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel, Field

from tempest_fastapi_sdk import (
    JWTUtils,
    make_bearer_token_dependency,
    register_exception_handlers,
)
from tempest_fastapi_sdk.mcp import add_fastapi_routes, mcp_principal, mount_mcp
from tests.mcp.mcp_fixture_server import build_server

JWT_SECRET: str = "mcp-test-secret-with-enough-bytes-for-hs256"
ROUTE_SECRET: str = "route-shared-secret"


class OrderInSchema(BaseModel):
    """Body of the exposed route."""

    sku: str = Field(min_length=1, description="Product code.")
    quantity: int = Field(ge=1, description="Units wanted.")


class OrderOutSchema(BaseModel):
    """Response of the exposed route."""

    sku: str
    quantity: int
    owner: str


@dataclass
class LiveApp:
    """A running app and what the tests read back from it.

    Attributes:
        url (str): The MCP endpoint.
        base (str): The server's base URL.
        jwt (JWTUtils): The helper that signs valid tokens.
        events (list[str]): Lifespan events, in order.
        route_secrets (list[str]): ``X-Route-Secret`` values the route saw.
    """

    url: str
    base: str
    jwt: JWTUtils
    events: list[str] = field(default_factory=list)
    route_secrets: list[str] = field(default_factory=list)

    def token(self, subject: str) -> str:
        """Sign an access token for ``subject``."""
        return self.jwt.encode({"sub": subject, "typ": "access"})


def build_app(live: LiveApp, *, tool_timeout: float | None = 120.0) -> FastAPI:
    """Build the app under test.

    Args:
        live (LiveApp): Where lifespan events and route inputs are recorded.
        tool_timeout (float | None): Ceiling for the exposed agent tools.

    Returns:
        FastAPI: The app with ``/orders`` routes and ``/mcp`` mounted.
    """
    current_user = make_bearer_token_dependency(live.jwt)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        live.events.append("app-start")
        yield
        live.events.append("app-stop")

    app = FastAPI(lifespan=lifespan)
    register_exception_handlers(app, log_traceback=False)

    @app.post("/orders", operation_id="create_order", tags=["orders"])
    async def create_order(
        order: OrderInSchema,
        claims: dict[str, Any] = Depends(current_user),
        x_route_secret: str = Header(default=""),
    ) -> OrderOutSchema:
        live.route_secrets.append(x_route_secret)
        return OrderOutSchema(
            sku=order.sku,
            quantity=order.quantity,
            owner=str(claims["sub"]),
        )

    @app.delete("/orders/{order_id}", operation_id="delete_order", tags=["admin"])
    async def delete_order(order_id: int) -> dict[str, int]:
        return {"deleted": order_id}

    server = build_server(tool_timeout=tool_timeout)

    @server.tool
    def whoami() -> str:
        """Return the authenticated subject."""
        return str(mcp_principal(dict)["sub"])

    add_fastapi_routes(
        server,
        app,
        operations=["create_order"],
        forward_headers=("authorization", "x-route-secret"),
    )
    mount_mcp(app, server, principal=current_user)
    return app


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[str]:
    """Run ``app`` under uvicorn on a free loopback port.

    Args:
        app (FastAPI): The app to serve, lifespan on.

    Yields:
        str: The base URL.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    task: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        sock.close()


@pytest.fixture
def make_live() -> Callable[..., Any]:
    """Return a factory that serves a fresh app.

    Returns:
        Callable[..., Any]: ``make_live(**build_app_kwargs)`` as an async
        context manager yielding a :class:`LiveApp`.
    """

    @asynccontextmanager
    async def factory(**kwargs: Any) -> AsyncIterator[LiveApp]:
        live = LiveApp(url="", base="", jwt=JWTUtils(JWT_SECRET))
        async with serve(build_app(live, **kwargs)) as base:
            live.base = base
            live.url = f"{base}/mcp"
            yield live

    return factory


@pytest.fixture
async def live(make_live: Callable[..., Any]) -> AsyncIterator[LiveApp]:
    """Serve the default app for one test.

    Yields:
        LiveApp: The running app.
    """
    async with make_live() as running:
        yield running
