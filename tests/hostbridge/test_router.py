"""The router is opt-in, authenticated by construction, and read-only by default."""

from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk.hostbridge import (
    HostBridge,
    HostBridgeConfig,
    make_hostbridge_router,
)


def _noop_dependency() -> None:
    """Stand in for a service's real auth dependency."""
    return None


def _paths(app_router: APIRouter) -> set[tuple[str, str]]:
    """Collect ``(path, method)`` pairs a router declares.

    Args:
        app_router: The router to inspect.

    Returns:
        set[tuple[str, str]]: One entry per declared path and method.
    """
    return {
        (route.path, method) for route in app_router.routes for method in route.methods
    }


class TestAuthIsRequired:
    """Publishing a shell without auth is refused, not defaulted."""

    def test_empty_dependencies_raise(self) -> None:
        """A router with no auth never gets built.

        Making this a default rather than an error is how an internal tool
        ends up reachable: the omission is silent and the surface is the
        whole machine.
        """
        with pytest.raises(ValueError, match="at least one dependency"):
            make_hostbridge_router(HostBridge(), dependencies=[])

    def test_dependencies_are_applied_to_every_route(self) -> None:
        """Auth is on the router, so a new route cannot be added without it."""
        router = make_hostbridge_router(
            HostBridge(), dependencies=[Depends(_noop_dependency)], destructive=True
        )
        assert len(router.dependencies) == 1
        assert router.routes


class TestDestructiveIsOptIn:
    """The write side exists only when a service asks for it."""

    def test_read_only_by_default(self) -> None:
        """Commands, writes, deletes and power actions are absent."""
        declared = _paths(
            make_hostbridge_router(
                HostBridge(), dependencies=[Depends(_noop_dependency)]
            )
        )
        assert ("/system/info", "GET") in declared
        assert ("/system/files", "GET") in declared
        assert ("/system/exec", "POST") not in declared
        assert ("/system/files", "POST") not in declared
        assert ("/system/files", "DELETE") not in declared
        assert ("/system/shutdown", "POST") not in declared

    def test_destructive_adds_the_write_side(self) -> None:
        """With the flag on, the full surface is mounted."""
        declared = _paths(
            make_hostbridge_router(
                HostBridge(),
                dependencies=[Depends(_noop_dependency)],
                destructive=True,
            )
        )
        for path, method in [
            ("/system/exec", "POST"),
            ("/system/files", "POST"),
            ("/system/files", "DELETE"),
            ("/system/shutdown", "POST"),
            ("/system/restart", "POST"),
            ("/system/abort", "POST"),
            ("/system/lock", "POST"),
            ("/system/logoff", "POST"),
        ]:
            assert (path, method) in declared

    def test_the_prefix_is_configurable(self) -> None:
        """A service that already owns ``/system`` can mount it elsewhere."""
        declared = _paths(
            make_hostbridge_router(
                HostBridge(), dependencies=[Depends(_noop_dependency)], prefix="/host"
            )
        )
        assert ("/host/info", "GET") in declared


class TestServedResponses:
    """Driven through ASGI, the routes answer with the bridge's own schemas."""

    async def test_reading_a_file_answers_with_its_content(
        self, tmp_path: Path
    ) -> None:
        """A GET on an allowed path returns the schema, not an ORM shape."""
        target = tmp_path / "notes.txt"
        target.write_text("hello")
        app = FastAPI()
        app.include_router(
            make_hostbridge_router(
                HostBridge(HostBridgeConfig(allowed_base_paths=(str(tmp_path),))),
                dependencies=[Depends(_noop_dependency)],
            )
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/system/files", params={"path": str(target)})
        assert response.status_code == 200
        assert response.json()["content"] == "hello"

    async def test_a_refused_path_answers_with_the_envelope(
        self, tmp_path: Path
    ) -> None:
        """A path outside the bases is a 400 carrying the code, not a 500."""
        from tempest_fastapi_sdk.api.handlers import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_hostbridge_router(
                HostBridge(HostBridgeConfig(allowed_base_paths=(str(tmp_path),))),
                dependencies=[Depends(_noop_dependency)],
            )
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/system/files", params={"path": "/etc/passwd"})
        assert response.status_code == 400
        assert response.json()["code"] == "HOST_INVALID_PATH"
