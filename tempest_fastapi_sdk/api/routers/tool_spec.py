"""Reusable ``/tool-spec`` router.

Services often expose a machine-readable manifest at the root prefix
so callers can discover capabilities without parsing the full OpenAPI
document. This router gives that endpoint a single canonical shape
without forcing a specific schema — the caller passes either a static
mapping or a (potentially async) provider.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter

SpecProvider = (
    dict[str, Any]
    | Callable[[], dict[str, Any]]
    | Callable[[], Awaitable[dict[str, Any]]]
)


def make_tool_spec_router(
    spec: SpecProvider,
    *,
    path: str = "/tool-spec",
    tag: str = "meta",
    response_model: type[Any] | None = None,
) -> APIRouter:
    """Build a router that exposes a tool-spec / capabilities manifest.

    The endpoint sits at the root prefix by default so it joins
    ``/health`` as a meta endpoint outside of ``/api/<domain>``.

    Args:
        spec (SpecProvider): The manifest. Pass either a dict (served
            verbatim), a sync callable returning a dict (called on
            every request — useful for content that depends on
            settings or runtime state), or an async callable.
        path (str): Endpoint path. Defaults to ``"/tool-spec"``.
        tag (str): OpenAPI tag. Defaults to ``"meta"``.
        response_model (type[Any] | None): Optional Pydantic model used
            as the response schema for OpenAPI. The provider must
            return a value compatible with the model.

    Returns:
        APIRouter: A FastAPI router with a single ``GET`` route.
    """
    router = APIRouter(tags=[tag])

    if callable(spec):
        provider_callable: Callable[..., Any] = spec
        is_coro = inspect.iscoroutinefunction(provider_callable)

        @router.get(path, response_model=response_model)
        async def tool_spec() -> Any:
            result = provider_callable()
            if is_coro or inspect.isawaitable(result):
                return await result
            return result
    else:
        static_spec: dict[str, Any] = dict(spec)

        @router.get(path, response_model=response_model)
        async def tool_spec_static() -> dict[str, Any]:
            return static_spec

    return router


__all__: list[str] = [
    "make_tool_spec_router",
]
