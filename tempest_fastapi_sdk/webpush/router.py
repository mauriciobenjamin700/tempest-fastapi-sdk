"""Opt-in FastAPI router exposing Web Push subscribe / unsubscribe.

Mirrors :func:`tempest_fastapi_sdk.make_auth_router`: a factory that
wires the two endpoints ``tempest-react-sdk``'s ``WebPushClient`` calls
(``onSubscribe`` / ``onUnsubscribe``) straight onto a
:class:`WebPushSubscriptionService`, so a project gets a working push
loop without hand-writing the routes. The caller supplies the
dependencies (how a request-scoped service and the current user are
resolved); the router owns only the HTTP surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.webpush.schemas import WebPushSubscriptionSchema
from tempest_fastapi_sdk.webpush.service import WebPushSubscriptionService


def make_web_push_router(
    *,
    service_factory: Callable[[AsyncSession], WebPushSubscriptionService[Any]],
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    current_user_id: Callable[..., Any],
    prefix: str = "/api/push",
    tags: list[str] | None = None,
    store_user_agent: bool = True,
) -> APIRouter:
    """Build a router with ``POST /subscribe`` and ``POST /unsubscribe``.

    Both endpoints accept the raw ``PushSubscription.toJSON()`` body the
    browser produces (a :class:`WebPushSubscriptionSchema`), so the
    ``tempest-react-sdk`` ``WebPushClient`` callbacks hit them with no
    payload mangling:

    * ``POST {prefix}/subscribe`` -> ``service.subscribe`` (idempotent by
      endpoint). Stores the ``User-Agent`` as the device label when
      ``store_user_agent`` is set.
    * ``POST {prefix}/unsubscribe`` -> ``service.unsubscribe`` (delete by
      endpoint, no-op when absent).

    Both require authentication via ``current_user_id``.

    Args:
        service_factory (Callable[[AsyncSession], WebPushSubscriptionService]):
            Builds a request-scoped service from the yielded session
            (typically ``lambda s: WebPushSubscriptionService(
            BaseRepository(s, model=Model), dispatcher)``).
        session_factory (Callable[[], AsyncIterator[AsyncSession]]): Yields
            a request-scoped DB session (the project's ``get_session``).
        current_user_id (Callable[..., Any]): A FastAPI dependency that
            resolves the authenticated user's :class:`~uuid.UUID`.
        prefix (str): URL prefix. Defaults to ``"/api/push"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["push"]``.
        store_user_agent (bool): When ``True`` (default), persist the
            request ``User-Agent`` header as the device label.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["push"]))

    async def _session() -> AsyncIterator[AsyncSession]:
        async for session in session_factory():
            yield session

    def _service(
        session: AsyncSession = Depends(_session),
    ) -> WebPushSubscriptionService[Any]:
        return service_factory(session)

    @router.post("/subscribe", status_code=status.HTTP_201_CREATED)
    async def subscribe(
        subscription: WebPushSubscriptionSchema,
        request: Request,
        user_id: UUID = Depends(current_user_id),
        service: WebPushSubscriptionService[Any] = Depends(_service),
    ) -> dict[str, str]:
        """Persist the device subscription sent by the browser.

        Args:
            subscription (WebPushSubscriptionSchema): The browser
                ``PushSubscription.toJSON()`` payload.
            request (Request): Used to read the ``User-Agent`` label.
            user_id (UUID): The authenticated user (FK owner).
            service (WebPushSubscriptionService): Request-scoped service.

        Returns:
            dict[str, str]: ``{"status": "subscribed"}``.
        """
        user_agent = request.headers.get("user-agent") if store_user_agent else None
        await service.subscribe(user_id, subscription, user_agent=user_agent)
        return {"status": "subscribed"}

    @router.post("/unsubscribe", status_code=status.HTTP_200_OK)
    async def unsubscribe(
        subscription: WebPushSubscriptionSchema,
        user_id: UUID = Depends(current_user_id),
        service: WebPushSubscriptionService[Any] = Depends(_service),
    ) -> dict[str, str]:
        """Remove the device subscription (idempotent).

        Args:
            subscription (WebPushSubscriptionSchema): The subscription to
                drop; only its ``endpoint`` is used.
            user_id (UUID): The authenticated user (auth gate).
            service (WebPushSubscriptionService): Request-scoped service.

        Returns:
            dict[str, str]: ``{"status": "unsubscribed"}``.
        """
        await service.unsubscribe(subscription.endpoint)
        return {"status": "unsubscribed"}

    return router


__all__: list[str] = [
    "make_web_push_router",
]
