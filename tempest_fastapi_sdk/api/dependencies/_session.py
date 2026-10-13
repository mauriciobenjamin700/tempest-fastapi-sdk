"""Turn a router's ``session_factory`` into something FastAPI can resolve.

Every ``make_*_router`` that takes a ``session_factory`` registers it
through :func:`session_provider_for`, so the rule lives in one place: a
``get_session`` with sub-dependencies (``Request``,
``Depends(get_database)``) is resolved by FastAPI like any other
dependency, and ``app.dependency_overrides[get_session]`` reaches the
router's routes. ``tests/test_session_factory_guard.py`` fails when a
router iterates ``session_factory()`` by hand instead.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def session_provider_for(
    session_factory: Callable[..., AsyncIterator[AsyncSession]],
) -> Callable[..., Any]:
    """Return the dependable a router passes to ``Depends`` for its session.

    An (async) generator function — the project's ``get_session``, or
    ``db.session_dependency`` of an
    :class:`~tempest_fastapi_sdk.AsyncDatabaseManager` — is returned
    unchanged, so FastAPI resolves its parameters and an
    ``app.dependency_overrides[session_factory]`` applies. Any other
    callable is treated as the zero-argument factory the routers accepted
    before, and wrapped in a generator that iterates it.

    Args:
        session_factory (Callable[..., AsyncIterator[AsyncSession]]): The
            ``session_factory`` the router was built with.

    Returns:
        Callable[..., Any]: The callable to register as
        ``Depends(...)``.
    """
    if inspect.isasyncgenfunction(session_factory) or inspect.isgeneratorfunction(
        session_factory
    ):
        return session_factory

    async def _session() -> AsyncIterator[AsyncSession]:
        """Iterate a session factory FastAPI cannot resolve itself.

        Yields:
            AsyncSession: The request's session.
        """
        async for session in session_factory():
            yield session

    return _session


__all__: list[str] = ["session_provider_for"]
