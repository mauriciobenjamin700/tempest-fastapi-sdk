"""Every authenticated admin route reloads the principal it renders.

The session cookie is signed and short-lived, which makes it read like an
authorization decision: it proves a login happened, not that the account
still has admin access. What ends access on the next request is
:meth:`AdminAuthBackend.load_principal`, returning ``None`` for a row that was
deleted, deactivated or had ``is_admin`` revoked -- and only the routes that
call it get that. Four shipped without it (``GET /admin/logs/export``,
``GET /admin/sql``, ``POST /admin/sql`` and ``POST /admin/tasks/{job_id}/cancel``),
so a revoked administrator kept exporting tracebacks and running statements
until the cookie expired.

Every other authenticated route in this router already called
``_resolve_principal``; nothing about those four made it inconvenient, so the
rule needed no defence from design. This guard is that defence: it reads the
``APIRouter`` the factory returned -- not ``app.routes``, which the FastAPI in
this repo's floor does not flatten -- and reports every route that depends on
``_require_session`` without reloading the principal. The behavioural proof --
log in, revoke, call the route -- is
``tests/admin/test_principal_revalidation.py``; this one is what stops the next
route from being written without it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.routing import APIRoute

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
)
from tempest_fastapi_sdk.admin import SqlShellService, TaskPanelService
from tempest_fastapi_sdk.admin.router import make_admin_router
from tempest_fastapi_sdk.tasks import JobStore, make_job_model

_SESSION_GUARD = "_require_session"
"""The session dependency whose routes are the ones under the rule."""

_PRINCIPAL_RELOAD = "_resolve_principal"
"""The call that reloads the principal, so revocation ends the request."""

_MOUNTED_PATHS = {
    "/admin/",
    "/admin/logs/export",
    "/admin/sql",
    "/admin/tasks/{job_id}/cancel",
}
"""The paths this guard is expected to be reading when it reports nothing."""


class GuardUser(BaseUserModel):
    """A principal model for the routers the guard builds."""

    __tablename__ = "admin_principal_guard_users"


GuardJob = make_job_model(tablename="admin_principal_guard_jobs", class_name="GuardJob")


def _authenticated_routes(router: APIRouter) -> list[APIRoute]:
    """Return the routes that depend on the session guard.

    Only the top-level dependencies are read: the admin declares
    ``_require_session`` directly on each route, never through another
    dependency. Plain Starlette routes (the static mount) carry no
    dependant and are skipped.

    Args:
        router (APIRouter): The router to read.

    Returns:
        list[APIRoute]: The routes guarded by ``_require_session``.
    """
    return [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and any(
            getattr(dependency.call, "__name__", None) == _SESSION_GUARD
            for dependency in route.dependant.dependencies
        )
    ]


def _reloads_principal(endpoint: Any) -> bool:
    """Return whether the endpoint body calls the principal reload.

    ``_resolve_principal`` is a closure local of ``make_admin_router``, so a
    route that calls it carries the name in ``co_freevars``; ``co_names`` is
    read too so the check survives the helper being lifted to module level.

    Args:
        endpoint (Any): The route's endpoint callable.

    Returns:
        bool: ``True`` when the body references the reload.
    """
    code = getattr(endpoint, "__code__", None)
    if code is None:
        return False
    return _PRINCIPAL_RELOAD in code.co_freevars or _PRINCIPAL_RELOAD in code.co_names


def _routes_without_reload(router: APIRouter) -> list[str]:
    """Return the authenticated routes that never reload the principal.

    Args:
        router (APIRouter): The router ``make_admin_router`` returned.

    Returns:
        list[str]: ``METHOD path`` for each offender, sorted.
    """
    return sorted(
        f"{','.join(sorted(route.methods or ()))} {route.path}"
        for route in _authenticated_routes(router)
        if not _reloads_principal(route.endpoint)
    )


@pytest.fixture
async def admin_router(tmp_path: Path) -> AsyncIterator[APIRouter]:
    """Yield the router with every optional admin surface mounted.

    A router without the SQL shell or the task panel would have nothing to
    check on those routes, and the point of the guard is that they exist.

    Args:
        tmp_path (Path): Pytest temporary directory, for the log directory.

    Yields:
        APIRouter: The router the factory returned.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    store: JobStore[Any] = JobStore(db, model=GuardJob)
    router = make_admin_router(
        AdminSite(title="Guard"),
        db=db,
        auth_backend=UserModelAuthBackend(GuardUser),
        secret_key="g" * 48,
        cookie_secure=False,
        show_logs=True,
        log_dir=str(log_dir),
        sql_shell=SqlShellService(db, dialect="sqlite"),
        tasks=TaskPanelService(job_store=store),
    )
    yield router
    await db.drop_tables()
    await db.disconnect()


async def test_no_authenticated_admin_route_skips_the_reload(
    admin_router: APIRouter,
) -> None:
    """Every route behind ``_require_session`` re-reads the principal."""
    assert _routes_without_reload(admin_router) == []


async def test_the_check_covers_the_four_routes_the_defect_shipped(
    admin_router: APIRouter,
) -> None:
    """A router built without them has nothing to hide behind.

    The guard can only report what is mounted, so it says which paths it saw
    -- otherwise a change that dropped the surfaces would empty the report
    and pass by vacuity, the way the old ``app.routes`` assertions did.

    Args:
        admin_router (APIRouter): The router the factory returned.
    """
    authenticated = {route.path for route in _authenticated_routes(admin_router)}
    assert authenticated >= _MOUNTED_PATHS


class TestTheGuardFires:
    """The check has to fail on the shape that actually shipped."""

    async def test_a_route_guarded_only_by_the_cookie_is_reported(self) -> None:
        """A route with ``_require_session`` and no reload is reported.

        This is the shape of the four routes before the fix, built here so
        the guard's verdict is measured rather than assumed.
        """
        router = APIRouter()

        async def _require_session(request: Request) -> str:
            """Stand in for the cookie check, under the name the guard reads.

            Args:
                request (Request): The inbound request.

            Returns:
                str: A placeholder session.
            """
            return "session"

        @router.get("/only-the-cookie")
        async def only_the_cookie(
            session: str = Depends(_require_session),
        ) -> str:
            """Serve on the cookie alone, the shape the four routes had.

            Args:
                session (str): The placeholder session.

            Returns:
                str: The session, untouched.
            """
            return session

        assert _routes_without_reload(router) == ["GET /only-the-cookie"]

    async def test_the_same_route_is_accepted_once_it_reloads(self) -> None:
        """Adding the reload call is what clears the report."""
        router = APIRouter()

        async def _require_session(request: Request) -> str:
            """Stand in for the cookie check, under the name the guard reads.

            Args:
                request (Request): The inbound request.

            Returns:
                str: A placeholder session.
            """
            return "session"

        async def _resolve_principal(request: Request, session: str) -> str:
            """Stand in for the reload, under the name the guard reads.

            Args:
                request (Request): The inbound request.
                session (str): The placeholder session.

            Returns:
                str: A placeholder principal.
            """
            return f"principal of {session}"

        @router.get("/reloaded")
        async def reloaded(
            request: Request,
            session: str = Depends(_require_session),
        ) -> str:
            """Reload the principal before answering.

            Args:
                request (Request): The inbound request.
                session (str): The placeholder session.

            Returns:
                str: The reloaded principal.
            """
            return await _resolve_principal(request, session)

        assert _routes_without_reload(router) == []

    async def test_a_route_without_the_session_guard_is_left_alone(self) -> None:
        """The login and logout paths opt out of the rule by design."""
        router = APIRouter()

        @router.get("/login")
        async def login() -> str:
            """Serve without a session dependency, like the login form.

            Returns:
                str: A placeholder page.
            """
            return "login"

        assert _routes_without_reload(router) == []
