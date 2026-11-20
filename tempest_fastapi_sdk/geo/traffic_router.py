"""Bundled router the app reads traffic (and graph tiles) from.

* ``GET <prefix>`` — the current :class:`TrafficSnapshotSchema` restricted
  to a bounding box (``min_latitude``, ``max_latitude``, ``min_longitude``,
  ``max_longitude``). Only k-anonymous edges ever reach the store, so there
  is nothing more to filter here.
* ``GET <prefix>/graph`` — with ``graph=``, the road network inside the box
  in ``tempest-react-sdk``'s ``createRoadGraph`` input shape, so the app
  matches its live marker against the same edges the traffic is keyed by.

``authorize`` is mandatory, like every geo router: who may read traffic is
the service's decision, never the SDK's. The update notice goes over SSE
from :meth:`TrafficAggregator.flush`; the client re-reads its box on it.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Final

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.geo.road_graph import RoadGraph
from tempest_fastapi_sdk.geo.schemas import BoundingBox, Coordinate
from tempest_fastapi_sdk.geo.traffic import (
    TrafficSnapshotSchema,
    TrafficSnapshotStore,
)
from tempest_fastapi_sdk.schemas.base import BaseSchema

TRAFFIC_INVALID_BBOX_ERROR: Final[str] = (
    "min_latitude must not exceed max_latitude, nor min_longitude max_longitude"
)
"""Message of the ``422`` answered for an inverted bounding box."""


class RoadGraphTileSchema(BaseSchema):
    """A tile of the road network, in ``createRoadGraph`` input shape.

    Attributes:
        nodes (list[Coordinate]): Vertices; edges refer to them by index.
        edges (list[dict[str, Any]]): ``{"from", "to", "twoWay"?, "id"?,
            ...}`` per edge.
        two_way (bool): Default for edges without ``twoWay`` (``"twoWay"``
            on the wire).
    """

    nodes: list[Coordinate] = Field(
        default_factory=list, title="Nodes", description="Road vertices."
    )
    edges: list[dict[str, Any]] = Field(
        default_factory=list,
        title="Edges",
        description='Edges as {"from", "to", "twoWay"?, "id"?, ...}.',
    )
    two_way: bool = Field(
        default=False,
        validation_alias="twoWay",
        serialization_alias="twoWay",
        title="Two-way default",
        description="Default for edges without twoWay.",
    )


def make_traffic_router(
    store: TrafficSnapshotStore | Callable[[Request], TrafficSnapshotStore],
    *,
    authorize: Callable[..., Coroutine[Any, Any, Any]],
    graph: RoadGraph | None = None,
    prefix: str = "/traffic",
    tag: str = "Traffic",
    docs_locale: str | None = None,
) -> APIRouter:
    """Build the router that serves traffic from ``store``.

    Args:
        store (TrafficSnapshotStore | Callable[[Request], TrafficSnapshotStore]):
            The store :meth:`TrafficAggregator.flush` saves to, or a factory
            called with each request.
        authorize (Callable[..., Coroutine[Any, Any, Any]]): Dependency that
            raises unless the caller may read traffic. Mandatory.
        graph (RoadGraph | None): When given, also mounts
            ``GET <prefix>/graph`` serving tiles of it.
        prefix (str): URL prefix. Defaults to ``"/traffic"``.
        tag (str): OpenAPI tag.
        docs_locale (str | None): Language of the OpenAPI text, ``"en-US"``
            or ``"pt-BR"``; ``None`` reads ``API_DOCS_LOCALE``.

    Returns:
        APIRouter: The router, ready for ``app.include_router``.

    Raises:
        ValueError: When ``authorize`` is ``None``.
    """
    if authorize is None:
        raise ValueError("make_traffic_router needs an authorize dependency")

    def resolve_store(request: Request) -> TrafficSnapshotStore:
        """Return the store for this request.

        Args:
            request (Request): The request being served.

        Returns:
            TrafficSnapshotStore: ``store``, or what its factory returns.
        """
        if isinstance(store, TrafficSnapshotStore):
            return store
        return store(request)

    def bbox_of(
        min_latitude: float = Query(ge=-90, le=90),
        max_latitude: float = Query(ge=-90, le=90),
        min_longitude: float = Query(ge=-180, le=180),
        max_longitude: float = Query(ge=-180, le=180),
    ) -> BoundingBox:
        """Read and check the bounding box of the query string.

        Args:
            min_latitude (float): Southern edge.
            max_latitude (float): Northern edge.
            min_longitude (float): Western edge.
            max_longitude (float): Eastern edge.

        Returns:
            BoundingBox: The box.

        Raises:
            ValidationException: When a minimum exceeds its maximum.
        """
        if min_latitude > max_latitude or min_longitude > max_longitude:
            raise ValidationException(message=TRAFFIC_INVALID_BBOX_ERROR)
        return BoundingBox(
            min_latitude=min_latitude,
            max_latitude=max_latitude,
            min_longitude=min_longitude,
            max_longitude=max_longitude,
        )

    route_docs: RouteDocs = RouteDocs(docs_locale)
    router = APIRouter(prefix=prefix, tags=[tag], dependencies=[Depends(authorize)])

    @router.get(
        "",
        response_model=TrafficSnapshotSchema,
        summary=route_docs.summary("geo_traffic.read_traffic"),
        description=route_docs.description("geo_traffic.read_traffic"),
    )
    async def read_traffic(
        request: Request,
        bbox: BoundingBox = Depends(bbox_of),
    ) -> TrafficSnapshotSchema:
        """Answer the published edges inside the box.

        Args:
            request (Request): The request being served.
            bbox (BoundingBox): The box.

        Returns:
            TrafficSnapshotSchema: The snapshot restricted to the box; with
            no snapshot yet (or an expired one), an empty one whose
            ``generated_at`` is ``None``.
        """
        snapshot = await resolve_store(request).load()
        if snapshot is None:
            return TrafficSnapshotSchema()
        return snapshot.within(bbox)

    if graph is not None:
        road_graph: RoadGraph = graph

        @router.get(
            "/graph",
            response_model=RoadGraphTileSchema,
            summary=route_docs.summary("geo_traffic.read_graph_tile"),
            description=route_docs.description("geo_traffic.read_graph_tile"),
        )
        async def read_graph_tile(
            bbox: BoundingBox = Depends(bbox_of),
        ) -> RoadGraphTileSchema:
            """Answer the edges with an end inside the box.

            Args:
                bbox (BoundingBox): The box.

            Returns:
                RoadGraphTileSchema: The tile.
            """
            tile = road_graph.extract(bbox).to_dict()
            return RoadGraphTileSchema.model_validate(tile)

    return router


__all__: list[str] = [
    "TRAFFIC_INVALID_BBOX_ERROR",
    "RoadGraphTileSchema",
    "make_traffic_router",
]
