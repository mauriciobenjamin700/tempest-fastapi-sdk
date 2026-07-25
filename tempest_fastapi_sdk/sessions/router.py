"""``make_session_router`` — bundled session login / logout / list endpoints.

Mounts five HTTP endpoints that cover the entire server-side
session lifecycle the project would otherwise hand-roll. Every
endpoint speaks JSON; the cookie is set / cleared by the SDK at
the right moment so callers never touch ``Set-Cookie`` directly.

* ``POST /auth/session/login`` — verify credentials, mint a
  session, set the cookie.
* ``POST /auth/session/logout`` — invalidate the current session,
  clear the cookie. Idempotent.
* ``GET /auth/session/me`` — return the live session resolving
  the current cookie.
* ``GET /auth/session/list`` — list every session the current
  user owns (active devices UI).
* ``DELETE /auth/session/{id}`` — revoke a specific session by
  its public id. The current session can revoke itself this way
  too — the cookie is cleared when it does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, Response, status

from tempest_fastapi_sdk.api.cookies import clear_cookie, set_cookie
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.sessions.dependencies import make_session_dependency
from tempest_fastapi_sdk.sessions.schemas import (
    Session,
    SessionLoginSchema,
    SessionResponseSchema,
    SessionSummarySchema,
)
from tempest_fastapi_sdk.utils.client_ip import get_client_ip

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.api.cookies import SameSite
    from tempest_fastapi_sdk.sessions.service import SessionAuth


def make_session_router(
    service: SessionAuth,
    *,
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    prefix: str = "/auth/session",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the bundled session router.

    Args:
        service (SessionAuth): Configured auth service that talks
            to the store + verifies passwords against the user
            model.
        session_factory (Callable[[], AsyncIterator[AsyncSession]]):
            FastAPI dependency yielding an async SQLAlchemy session
            — typically ``db.session_dependency``.
        prefix (str): URL prefix. Defaults to ``"/auth/session"``.
        tags (list[str] | None): OpenAPI tags. Defaults to
            ``["session"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    settings = service.settings
    router = APIRouter(prefix=prefix, tags=list(tags or ["session"]))

    async def _session_dep() -> AsyncIterator[AsyncSession]:
        async for s in session_factory():
            yield s

    db_dep = Depends(_session_dep)
    current_session_required = Depends(make_session_dependency(required=True))

    def _set_session_cookie(response: Response, plaintext: str) -> None:
        set_cookie(
            response,
            settings.SESSION_COOKIE_NAME,
            plaintext,
            max_age=settings.SESSION_TTL_SECONDS,
            path=settings.SESSION_COOKIE_PATH,
            domain=settings.SESSION_COOKIE_DOMAIN,
            secure=settings.SESSION_COOKIE_SECURE,
            http_only=settings.SESSION_COOKIE_HTTPONLY,
            samesite=_samesite(settings.SESSION_COOKIE_SAMESITE),
        )

    def _clear_session_cookie(response: Response) -> None:
        clear_cookie(
            response,
            settings.SESSION_COOKIE_NAME,
            path=settings.SESSION_COOKIE_PATH,
            domain=settings.SESSION_COOKIE_DOMAIN,
            samesite=_samesite(settings.SESSION_COOKIE_SAMESITE),
        )

    @router.post(
        "/login",
        response_model=SessionResponseSchema,
        status_code=status.HTTP_200_OK,
        summary="Authenticate and start a session",
    )
    async def login(
        payload: SessionLoginSchema,
        request: Request,
        response: Response,
        session: AsyncSession = db_dep,
    ) -> SessionResponseSchema:
        """Authenticate and open a server-side session.

        Args:
            payload (SessionLoginSchema): Email and password.
            request (Request): The inbound request, read for client metadata.
            response (Response): The outgoing response, used to set the cookie.
            session (AsyncSession): The request-scoped DB session.

        Returns:
            Response: The session cookie plus the login payload.
        """
        user = await service.authenticate(
            session,
            email=payload.email,
            password=payload.password,
        )
        await session.commit()
        previous = request.cookies.get(settings.SESSION_COOKIE_NAME)
        new_session, plaintext = await service.login(
            user_id=user.id,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            previous_session_id=previous,
        )
        _set_session_cookie(response, plaintext)
        return SessionResponseSchema(
            user_id=user.id,
            expires_at=new_session.expires_at,
        )

    @router.post(
        "/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Revoke the current session",
    )
    async def logout(
        request: Request,
        response: Response,
    ) -> Response:
        cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
        if cookie:
            await service.revoke(cookie)
        _clear_session_cookie(response)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.get(
        "/me",
        response_model=Session,
        summary="Return the live session for the current cookie",
    )
    async def me(
        session: Session = current_session_required,
    ) -> Session:
        return session

    @router.get(
        "/list",
        response_model=list[SessionSummarySchema],
        summary="List every session the current user owns",
    )
    async def list_sessions(
        request: Request,
        session: Session = current_session_required,
    ) -> list[SessionSummarySchema]:
        """List the caller's active sessions.

        Args:
            request (Request): The inbound request, read for client metadata.
            session (Session): The caller's current server-side session.

        Returns:
            list[SessionInfoSchema]: One entry per active session.
        """
        current_plain: str | None = getattr(request.state, "session_id_plaintext", None)
        return await service.list_sessions(
            session.user_id,
            current_session_id_plaintext=current_plain,
        )

    @router.delete(
        "/{public_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Revoke one specific session by its public id",
    )
    async def revoke_one(
        public_id: str,
        request: Request,
        response: Response,
        session: Session = current_session_required,
    ) -> Response:
        """Revoke one of the caller's sessions by its public id.

        Revoking your own session also clears the cookie, so the browser
        stops presenting an identifier that no longer resolves.

        Args:
            public_id (str): Public id of the session to revoke.
            request (Request): The inbound request.
            response (Response): The outgoing response, used to clear the
                cookie when the caller revoked themselves.
            session (Session): The caller's current session.

        Returns:
            Response: An empty ``204``.
        """
        await service.revoke_by_public_id(session.user_id, public_id)
        if session.session_id.startswith(public_id):
            _clear_session_cookie(response)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    return router


def _samesite(value: str) -> SameSite:
    """Narrow ``str`` from settings to the ``SameSite`` literal alias."""
    if value not in {"lax", "strict", "none"}:
        raise UnauthorizedException(message="invalid SameSite policy")
    return value  # type: ignore[return-value]


__all__: list[str] = ["make_session_router"]
