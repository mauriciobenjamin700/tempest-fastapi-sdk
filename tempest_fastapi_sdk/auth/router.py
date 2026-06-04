"""``make_auth_router`` — FastAPI router for the bundled auth flow.

Wraps the five endpoints every signup-with-email project ends up
implementing the same way:

* ``POST /auth/signup`` — create user + maybe send activation
* ``POST /auth/activate/{token}`` — consume activation + log in
* ``POST /auth/login`` — email + password → JWT pair
* ``POST /auth/password-reset/request`` — issue reset token
* ``POST /auth/password-reset/confirm`` — consume reset token

The router is generic over the service so the consuming
application keeps full control over the underlying user /
token models and the email rendering pipeline.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, status

from tempest_fastapi_sdk.auth.schemas import (
    ActivationResponseSchema,
    LoginResponseSchema,
    LoginSchema,
    PasswordResetConfirmSchema,
    PasswordResetRequestSchema,
    PasswordResetResponseSchema,
    SignupResponseSchema,
    SignupSchema,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.auth.service import UserAuthService


def make_auth_router(
    service: UserAuthService,
    *,
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    prefix: str = "/auth",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the bundled auth router.

    Args:
        service (UserAuthService): The configured service handling
            signup / activation / reset.
        session_factory (Callable[[], AsyncIterator[AsyncSession]]):
            FastAPI dependency yielding an async session. Typically
            wired as ``lambda: db.get_session()`` where ``db`` is
            an :class:`AsyncDatabaseManager`. Used inside each
            handler to scope the transaction to the request.
        prefix (str): URL prefix; defaults to ``"/auth"``.
        tags (list[str] | None): OpenAPI tags. Defaults to
            ``["auth"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    from fastapi import Depends

    router = APIRouter(
        prefix=prefix,
        tags=list(tags or ["auth"]),
    )

    async def _session() -> AsyncIterator[AsyncSession]:
        async for s in session_factory():
            yield s

    session_dep = Depends(_session)

    @router.post(
        "/signup",
        response_model=SignupResponseSchema,
        status_code=status.HTTP_201_CREATED,
        summary="Create a new account",
        description=(
            "Creates a user with email + password. When "
            "``AUTH_AUTO_ACTIVATE`` is set the response carries JWT "
            "tokens directly; otherwise the user must confirm via "
            "the activation link before logging in."
        ),
    )
    async def signup(
        payload: SignupSchema,
        session: AsyncSession = session_dep,
    ) -> SignupResponseSchema:
        user, activation = await service.signup(
            session,
            email=payload.email,
            password=payload.password,
            name=payload.name,
        )
        await session.commit()
        if activation is None:
            access, refresh = service.issue_jwt_pair(user)
            return SignupResponseSchema(
                user_id=user.id,
                activation_required=False,
                activation_url=None,
                access_token=access,
                refresh_token=refresh,
            )
        return_url = (
            activation.url
            if service.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE
            or service.email is None
            else None
        )
        return SignupResponseSchema(
            user_id=user.id,
            activation_required=True,
            activation_url=return_url,
        )

    @router.post(
        "/activate/{token}",
        response_model=ActivationResponseSchema,
        summary="Activate the account using the emailed token",
    )
    async def activate(
        token: str,
        session: AsyncSession = session_dep,
    ) -> ActivationResponseSchema:
        user = await service.activate(session, token=token)
        access, refresh = service.issue_jwt_pair(user)
        await session.commit()
        return ActivationResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    @router.post(
        "/login",
        response_model=LoginResponseSchema,
        summary="Log in with email + password",
    )
    async def login(
        payload: LoginSchema,
        session: AsyncSession = session_dep,
    ) -> LoginResponseSchema:
        user = await service.login(
            session,
            email=payload.email,
            password=payload.password,
        )
        access, refresh = service.issue_jwt_pair(user)
        await session.commit()
        return LoginResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    @router.post(
        "/password-reset/request",
        response_model=PasswordResetResponseSchema,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Request a password-reset link",
        description=(
            "Always returns 202 so attackers can't enumerate accounts "
            "by probing emails. The link is mailed when ``EmailUtils`` "
            "is wired; otherwise (or when "
            "``AUTH_RETURN_TOKEN_IN_RESPONSE`` is on) it ships in the "
            "response body."
        ),
    )
    async def password_reset_request(
        payload: PasswordResetRequestSchema,
        session: AsyncSession = session_dep,
    ) -> PasswordResetResponseSchema:
        token = await service.request_password_reset(session, email=payload.email)
        await session.commit()
        message = "If the email matches an account, a reset link was sent."
        if token is None:
            return PasswordResetResponseSchema(message=message, reset_url=None)
        return PasswordResetResponseSchema(message=message, reset_url=token.url)

    @router.post(
        "/password-reset/confirm",
        response_model=LoginResponseSchema,
        summary="Confirm a password reset with the issued token",
    )
    async def password_reset_confirm(
        payload: PasswordResetConfirmSchema,
        session: AsyncSession = session_dep,
    ) -> LoginResponseSchema:
        user = await service.confirm_password_reset(
            session,
            token=payload.token,
            new_password=payload.new_password,
        )
        access, refresh = service.issue_jwt_pair(user)
        await session.commit()
        return LoginResponseSchema(
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    return router


__all__: list[str] = [
    "make_auth_router",
]
