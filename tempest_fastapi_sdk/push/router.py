"""Opt-in FastAPI router exposing device registration.

The unified counterpart of
:func:`tempest_fastapi_sdk.make_web_push_router`: two endpoints a browser
*and* a mobile app hit with the same body shape, plus the VAPID public
key the web client needs before it can subscribe at all. The caller
supplies the dependencies (session, current user, service); the router
owns only the HTTP surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.api.dependencies._session import session_provider_for
from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.push.schemas import DeviceRegistrationSchema
from tempest_fastapi_sdk.push.service import DeviceService
from tempest_fastapi_sdk.utils._signature import accepts_keyword


def make_push_router(
    *,
    service_factory: Callable[[AsyncSession], DeviceService[Any]],
    session_factory: Callable[..., AsyncIterator[AsyncSession]],
    current_user_id: Callable[..., Any],
    prefix: str = "/api/push",
    tags: list[str] | None = None,
    vapid_public_key: str | Callable[[], str] | None = None,
    transfer_ownership: bool = False,
    docs_locale: str | None = None,
) -> APIRouter:
    """Build a router with ``POST /register`` and ``POST /unregister``.

    * ``POST {prefix}/register`` -> ``service.register`` (idempotent by
      token). A browser posts ``platform="web"`` with the endpoint and the
      two key fields; a mobile app posts ``platform="ios"`` / ``"android"``
      with its FCM registration token.
    * ``POST {prefix}/unregister`` -> ``service.unregister`` (delete by
      token, scoped to the authenticated user; no-op when absent or owned
      by another account).
    * ``GET {prefix}/vapid-public-key`` -> the key the browser subscribes
      with, mounted only when ``vapid_public_key`` is given.

    Both write endpoints require authentication via ``current_user_id``.

    Args:
        service_factory (Callable[[AsyncSession], DeviceService]): Builds a
            request-scoped service from the yielded session.
        session_factory (Callable[..., AsyncIterator[AsyncSession]]): Yields
            a request-scoped DB session (the project's ``get_session``).
        current_user_id (Callable[..., Any]): FastAPI dependency resolving
            the authenticated user's :class:`~uuid.UUID`.
        prefix (str): URL prefix. Defaults to ``"/api/push"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["push"]``.
        vapid_public_key (str | Callable[[], str] | None): When set, mount
            the public ``GET {prefix}/vapid-public-key`` returning
            ``{"public_key": ...}``, so the key is not baked into the
            frontend build. Accepts a string or a zero-arg callable
            (resolved per request). ``None`` omits the route — the right
            choice for a mobile-only service.
        transfer_ownership (bool): Let ``POST {prefix}/register`` move
            a token registered to another account to the caller. Off by
            default (#632): the stored row is left untouched and the
            caller gets the same answer as a new registration. Turn on only
            where moving devices between accounts is a real case; the
            service override must then accept ``transfer_ownership=``.
        docs_locale (str | None): Language of the OpenAPI ``summary``
            and ``description`` text — ``"en-US"`` or ``"pt-BR"``.
            ``None`` (default) reads ``API_DOCS_LOCALE`` through
            :class:`~tempest_fastapi_sdk.settings.ServerSettings`,
            whose default is ``"en-US"``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    route_docs: RouteDocs = RouteDocs(docs_locale)
    router = APIRouter(prefix=prefix, tags=list(tags or ["push"]))

    if vapid_public_key is not None:

        @router.get(
            "/vapid-public-key",
            summary=route_docs.summary("push.get_vapid_public_key"),
            description=route_docs.description("push.get_vapid_public_key"),
            status_code=status.HTTP_200_OK,
        )
        async def get_vapid_public_key() -> dict[str, str]:
            """Return the VAPID public key the browser subscribes with.

            Public and unauthenticated. Answers ``""`` when the key is
            unset, which lets the frontend hide the opt-in instead of
            failing.

            Returns:
                dict[str, str]: ``{"public_key": <url-safe base64 or "">}``.
            """
            key = vapid_public_key() if callable(vapid_public_key) else vapid_public_key
            return {"public_key": key or ""}

    _session = session_provider_for(session_factory)

    def _service(session: AsyncSession = Depends(_session)) -> DeviceService[Any]:
        return service_factory(session)

    @router.post(
        "/register",
        summary=route_docs.summary("push.register"),
        description=route_docs.description("push.register"),
        status_code=status.HTTP_201_CREATED,
    )
    async def register(
        registration: DeviceRegistrationSchema,
        user_id: UUID = Depends(current_user_id),
        service: DeviceService[Any] = Depends(_service),
    ) -> dict[str, str]:
        """Persist the device that will receive notifications.

        Args:
            registration (DeviceRegistrationSchema): Token, platform and
                the web encryption material when applicable.
            user_id (UUID): The authenticated user (FK owner).
            service (DeviceService): Request-scoped service.

        Returns:
            dict[str, str]: ``{"status": "registered"}``.
        """
        if transfer_ownership:
            await service.register(user_id, registration, transfer_ownership=True)
        else:
            await service.register(user_id, registration)
        return {"status": "registered"}

    @router.post(
        "/unregister",
        summary=route_docs.summary("push.unregister"),
        description=route_docs.description("push.unregister"),
        status_code=status.HTTP_200_OK,
    )
    async def unregister(
        registration: DeviceRegistrationSchema,
        user_id: UUID = Depends(current_user_id),
        service: DeviceService[Any] = Depends(_service),
    ) -> dict[str, str]:
        """Remove the caller's own device (idempotent).

        The delete is scoped to ``user_id``: a token registered to another
        account is left in place and the answer is the same ``200`` as for
        an unknown token, so the route never reveals who owns a token. A
        ``DeviceService`` subclass whose ``unregister`` override predates
        the ``user_id`` keyword still gets the scoping — the router checks
        ownership through ``list_for_user`` and calls the override only for
        the caller's own device, instead of failing with ``TypeError``.

        Args:
            registration (DeviceRegistrationSchema): The device to drop;
                only its ``token`` is used.
            user_id (UUID): The authenticated user; only their devices can
                be removed.
            service (DeviceService): Request-scoped service.

        Returns:
            dict[str, str]: ``{"status": "unregistered"}``.
        """
        token = registration.token
        if accepts_keyword(service.unregister, "user_id"):
            await service.unregister(token, user_id=user_id)
        elif any(row.token == token for row in await service.list_for_user(user_id)):
            await service.unregister(token)
        return {"status": "unregistered"}

    return router


__all__: list[str] = [
    "make_push_router",
]
