"""FastAPI dependencies for the server-side session module."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import Request

from tempest_fastapi_sdk.exceptions import UnauthorizedException

if TYPE_CHECKING:
    from tempest_fastapi_sdk.sessions.schemas import Session


def make_session_dependency(
    *,
    required: bool = True,
) -> Callable[[Request], Session | None]:
    """Build a FastAPI dependency that returns the resolved session.

    The dependency reads ``request.state.session`` populated by
    :class:`SessionMiddleware`. Mount the middleware on the app
    BEFORE you use the dependency or it always returns ``None``
    /raises ``UnauthorizedException``.

    Args:
        required (bool): When ``True`` (default), missing sessions
            raise :class:`UnauthorizedException` so the SDK
            envelope returns ``401``. When ``False``, the
            dependency returns ``None`` and the handler decides
            what to do (typical for endpoints that work both
            anonymously and authenticated).

    Returns:
        A FastAPI dependency callable.
    """

    def _resolver(request: Request) -> Session | None:
        session: Session | None = getattr(request.state, "session", None)
        if session is None and required:
            raise UnauthorizedException(message="session required")
        return session

    return _resolver


__all__: list[str] = ["make_session_dependency"]
