"""The ``en-US`` tables of the route-docs catalog."""

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc
from tempest_fastapi_sdk.api.route_docs.en_us.ai import (
    AI_ROUTES as AI_ROUTES,
)
from tempest_fastapi_sdk.api.route_docs.en_us.apps import (
    APPS_ROUTES as APPS_ROUTES,
)
from tempest_fastapi_sdk.api.route_docs.en_us.auth import (
    AUTH_ROUTES as AUTH_ROUTES,
)
from tempest_fastapi_sdk.api.route_docs.en_us.auth import (
    AUTH_TEXTS as AUTH_TEXTS,
)
from tempest_fastapi_sdk.api.route_docs.en_us.platform import (
    PLATFORM_ROUTES as PLATFORM_ROUTES,
)
from tempest_fastapi_sdk.api.route_docs.en_us.platform import (
    PLATFORM_TEXTS as PLATFORM_TEXTS,
)

ROUTES: dict[str, RouteDoc] = {
    **AI_ROUTES,
    **APPS_ROUTES,
    **AUTH_ROUTES,
    **PLATFORM_ROUTES,
}
"""Every operation entry of this locale."""

TEXTS: dict[str, str] = {**AUTH_TEXTS, **PLATFORM_TEXTS}
"""Every parameter and response description of this locale."""

__all__: list[str] = [
    "AI_ROUTES",
    "APPS_ROUTES",
    "AUTH_ROUTES",
    "AUTH_TEXTS",
    "PLATFORM_ROUTES",
    "PLATFORM_TEXTS",
    "ROUTES",
    "TEXTS",
]
