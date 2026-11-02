"""Every secured operation the SDK routers mount documents 401 (#691).

``make_auth_router``'s authenticated routes answered ``401`` without a token
and ``403`` for an inactive account, but declared neither: the OpenAPI showed
``security`` and no ``401``, so generated clients did not type the error and
a service guarding "every secured operation documents 401" failed only for
mounting the router. ``make_hostbridge_router`` had the same gap.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tempest_fastapi_sdk.hostbridge import make_hostbridge_router
from tests.api.route_docs_apps import ROUTER_BUILDERS

BEARER: HTTPBearer = HTTPBearer()


async def _bearer_guard(
    credentials: HTTPAuthorizationCredentials = Depends(BEARER),
) -> None:
    """Stand in for a service's bearer auth, which puts ``security`` on a route.

    Args:
        credentials (HTTPAuthorizationCredentials): The parsed header.
    """


def _secured_operations(app: FastAPI) -> list[tuple[str, str, dict[str, Any]]]:
    """List every operation whose OpenAPI entry carries ``security``.

    Args:
        app (FastAPI): The application to inspect.

    Returns:
        list[tuple[str, str, dict[str, Any]]]: Method, path and responses.
    """
    return [
        (method.upper(), path, operation["responses"])
        for path, operations in app.openapi()["paths"].items()
        for method, operation in operations.items()
        if operation.get("security")
    ]


@pytest.mark.parametrize("name", sorted(ROUTER_BUILDERS))
def test_every_secured_operation_documents_401(name: str) -> None:
    app = FastAPI()
    app.include_router(ROUTER_BUILDERS[name]({}))

    missing = [
        f"{method} {path}"
        for method, path, responses in _secured_operations(app)
        if "401" not in responses
    ]

    assert missing == []


def test_auth_router_secured_routes_document_inactive_account() -> None:
    """The routes behind the user dependency also answer ``403`` inactive."""
    app = FastAPI()
    app.include_router(ROUTER_BUILDERS["auth"]({}))

    secured = _secured_operations(app)

    assert len(secured) == 13
    for method, path, responses in secured:
        assert "ACCOUNT_INACTIVE" in responses["403"]["description"], (method, path)


def test_auth_router_keeps_signup_free_of_401() -> None:
    """Router-wide ``responses=`` would document a 401 ``signup`` never sends."""
    app = FastAPI()
    app.include_router(ROUTER_BUILDERS["auth"]({}))

    signup = app.openapi()["paths"]["/auth/signup"]["post"]

    assert "401" not in signup["responses"]


def test_hostbridge_with_bearer_auth_documents_401_and_403() -> None:
    app = FastAPI()
    app.include_router(
        make_hostbridge_router(
            object(),  # type: ignore[arg-type]
            dependencies=[Depends(_bearer_guard)],
        )
    )

    secured = _secured_operations(app)

    assert secured
    for method, path, responses in secured:
        assert {"401", "403"} <= set(responses), (method, path)
