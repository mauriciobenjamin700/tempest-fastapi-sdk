"""Bundled router that receives live positions for a :class:`LivePositionStore`.

One route, ``POST <prefix>/{channel_id}/position``. The two rules a service
would otherwise write slightly differently each time are parameters with no
default:

* ``authorize`` — who may publish on that channel (the driver of that trip).
  The factory refuses to build the router without it.
* ``channel_active`` — whether the channel is accepting positions now (the
  trip started and has not ended). ``False`` answers ``409`` and stores
  nothing.

A successful response says only whether the position was accepted; the
position reaches viewers through the store's audience, nowhere else. The
only answer that repeats part of the body is FastAPI's ``422`` validation
error, sent to the caller after ``authorize`` passed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Final

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.geo.live import LivePositionSchema, LivePositionStore
from tempest_fastapi_sdk.geo.traffic import (
    TrafficAggregator,
    TrafficDimensionsResolver,
)
from tempest_fastapi_sdk.schemas.base import BaseSchema

logger: logging.Logger = logging.getLogger(__name__)

LIVE_POSITION_INACTIVE_ERROR: Final[str] = "Channel is not accepting positions"
"""Message of the ``409`` answered when ``channel_active`` says ``False``."""


class LivePositionPublishResponseSchema(BaseSchema):
    """What ``POST <prefix>/{channel_id}/position`` answers.

    Attributes:
        accepted (bool): Whether the store kept the position. ``False`` is
            not an error: the position was throttled, too close to the last
            one, or older than it.
    """

    accepted: bool = Field(
        ...,
        title="Accepted",
        description="Whether the store kept the position",
    )


def make_live_position_router(
    store: LivePositionStore | Callable[[Request], LivePositionStore],
    *,
    authorize: Callable[..., Coroutine[Any, Any, Any]],
    channel_active: Callable[..., Coroutine[Any, Any, bool]],
    traffic: TrafficAggregator | None = None,
    traffic_dimensions: TrafficDimensionsResolver | None = None,
    prefix: str = "/live",
    tag: str = "Live position",
    docs_locale: str | None = None,
) -> APIRouter:
    """Build the router that receives positions for ``store``.

    Mounts ``POST <prefix>/{channel_id}/position``, body
    :class:`~tempest_fastapi_sdk.geo.LivePositionSchema`, answering
    :class:`LivePositionPublishResponseSchema`.

    Both dependencies are ordinary FastAPI dependencies: they can declare
    ``channel_id: str`` to receive the path parameter and depend on the
    service's current-user dependency. ``authorize`` runs first and refuses
    by raising (``ForbiddenException`` for a user who is not the channel's
    publisher); its return value is ignored. ``channel_active`` then answers
    ``True`` or ``False``.

    Args:
        store (LivePositionStore | Callable[[Request], LivePositionStore]):
            The store, or a factory called with each request that returns it
            (``lambda request: request.app.state.live_positions``).
        authorize (Callable[..., Coroutine[Any, Any, Any]]): Dependency that
            raises unless the caller may publish on ``channel_id``.
            Mandatory.
        channel_active (Callable[..., Coroutine[Any, Any, bool]]): Dependency
            answering whether ``channel_id`` accepts positions now.
            Mandatory.
        traffic (TrafficAggregator | None): Also hands every authorized
            position of an active channel to this aggregator, the channel id
            as the device — throttled or not, since the matcher wants every
            fix. The aggregator hashes the id at once and publishes only
            k-anonymous traffic.
        traffic_dimensions (TrafficDimensionsResolver | None): Dependency
            answering the coarse quality dimensions of the trip
            (``{"os": "android"}``, read from a header the app sends, for
            instance), handed to ``traffic.add_position``; needs ``traffic``
            built with ``quality=``. Values the aggregator refuses (an
            undeclared name, a value too long) are dropped with a warning
            and the position is still aggregated.
        prefix (str): URL prefix. Defaults to ``"/live"``.
        tag (str): OpenAPI tag. Defaults to ``"Live position"``.
        docs_locale (str | None): Language of the OpenAPI ``summary`` and
            ``description`` — ``"en-US"`` or ``"pt-BR"``. ``None`` reads
            ``API_DOCS_LOCALE``.

    Returns:
        APIRouter: The router, ready for ``app.include_router``.

    Raises:
        ValueError: When ``authorize`` or ``channel_active`` is ``None``,
            or ``traffic_dimensions`` comes without ``traffic``.
    """
    if authorize is None:
        raise ValueError(
            "make_live_position_router needs an authorize dependency: "
            "without it anyone could move anyone's marker",
        )
    if channel_active is None:
        raise ValueError("make_live_position_router needs a channel_active dependency")
    if traffic_dimensions is not None and traffic is None:
        raise ValueError("traffic_dimensions needs traffic=")

    async def no_dimensions() -> Mapping[str, str] | None:
        """Answer no dimensions when the service gives no resolver.

        Returns:
            Mapping[str, str] | None: Always ``None``.
        """
        return None

    dimensions_of = traffic_dimensions or no_dimensions

    def resolve_store(request: Request) -> LivePositionStore:
        """Return the store for this request.

        Args:
            request (Request): The request being served.

        Returns:
            LivePositionStore: ``store`` itself, or what its factory returns.
        """
        if isinstance(store, LivePositionStore):
            return store
        return store(request)

    route_docs: RouteDocs = RouteDocs(docs_locale)
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.post(
        "/{channel_id}/position",
        response_model=LivePositionPublishResponseSchema,
        summary=route_docs.summary("geo_live.publish_position"),
        description=route_docs.description("geo_live.publish_position"),
        dependencies=[Depends(authorize)],
    )
    async def publish_position(
        channel_id: str,
        position: LivePositionSchema,
        request: Request,
        active: bool = Depends(channel_active),
        dimensions: Mapping[str, str] | None = Depends(dimensions_of),
    ) -> LivePositionPublishResponseSchema:
        """Hand an authorized position to the store.

        Args:
            channel_id (str): The channel the position belongs to.
            position (LivePositionSchema): The reported position.
            request (Request): The request being served.
            active (bool): What ``channel_active`` answered.
            dimensions (Mapping[str, str] | None): What
                ``traffic_dimensions`` answered.

        Returns:
            LivePositionPublishResponseSchema: Whether it was accepted.

        Raises:
            ConflictException: When the channel is not active; nothing is
                stored.
        """
        if not active:
            raise ConflictException(message=LIVE_POSITION_INACTIVE_ERROR)
        if traffic is not None:
            try:
                traffic.add_position(channel_id, position, dimensions=dimensions)
            except ValueError:
                logger.warning("live position: quality dimensions refused, dropped")
                traffic.add_position(channel_id, position)
        accepted = await resolve_store(request).publish(channel_id, position)
        return LivePositionPublishResponseSchema(accepted=accepted)

    return router


__all__: list[str] = [
    "LIVE_POSITION_INACTIVE_ERROR",
    "LivePositionPublishResponseSchema",
    "make_live_position_router",
]
