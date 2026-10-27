"""Localized OpenAPI text for the routers the SDK builds (#643).

See :mod:`tempest_fastapi_sdk.api.route_docs.catalog`.
"""

from tempest_fastapi_sdk.api.route_docs.catalog import (
    ROUTE_DOCS as ROUTE_DOCS,
)
from tempest_fastapi_sdk.api.route_docs.catalog import (
    ROUTE_TEXTS as ROUTE_TEXTS,
)
from tempest_fastapi_sdk.api.route_docs.catalog import (
    RouteDocs as RouteDocs,
)
from tempest_fastapi_sdk.api.route_docs.catalog import (
    resolve_docs_locale as resolve_docs_locale,
)
from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc as RouteDoc

__all__: list[str] = [
    "ROUTE_DOCS",
    "ROUTE_TEXTS",
    "RouteDoc",
    "RouteDocs",
    "resolve_docs_locale",
]
