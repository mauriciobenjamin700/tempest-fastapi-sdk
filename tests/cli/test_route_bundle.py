"""Tests for ``tempest bundle-route``.

The fixture is a two-domain service (``users`` and ``orders``) laid out
the way ``tempest new`` lays one out: every layer is a package whose
``__init__.py`` re-exports both domains, and the controller providers of
both domains share ``api/dependencies/controllers.py``. That shape is the
point — following whole modules would pull ``orders`` into a ``users``
bundle through those re-exports.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

PROJECT_FILES: dict[str, str] = {
    "src/__init__.py": "",
    "src/server.py": """
        from platform import python_version

        from fastapi import FastAPI

        from src.api.routers import orders_router, users_router

        app = FastAPI()
        app.include_router(users_router, prefix="/api")
        app.include_router(orders_router, prefix="/api")
        app.add_api_route("/version", python_version)
    """,
    "src/api/__init__.py": "",
    "src/api/routers/__init__.py": """
        from .orders import router as orders_router
        from .users import router as users_router

        __all__: list[str] = ["orders_router", "users_router"]
    """,
    "src/api/routers/users.py": """
        from fastapi import APIRouter, Depends

        from src.api.dependencies import get_user_controller, require_token
        from src.controllers import UserController
        from src.schemas import UserResponseSchema

        router = APIRouter(prefix="/users", dependencies=[Depends(require_token)])


        @router.get("/{user_id}")
        async def get_user(
            user_id: int,
            controller: UserController = Depends(get_user_controller),
        ) -> UserResponseSchema:
            return await controller.get(user_id)


        @router.delete("/{user_id}")
        async def delete_user(user_id: int) -> None:
            return None
    """,
    "src/api/routers/orders.py": """
        from fastapi import APIRouter, Depends

        from src.api.dependencies import get_order_controller
        from src.controllers import OrderController

        router = APIRouter(prefix="/orders")


        @router.get("/")
        async def list_orders(
            controller: OrderController = Depends(get_order_controller),
        ) -> list[int]:
            return await controller.list()
    """,
    "src/api/dependencies/__init__.py": """
        from .auth import require_token as require_token
        from .controllers import get_order_controller as get_order_controller
        from .controllers import get_user_controller as get_user_controller
    """,
    "src/api/dependencies/auth.py": """
        from fastapi import Header


        def require_token(x_token: str = Header(default="")) -> None:
            return None
    """,
    "src/api/dependencies/controllers.py": """
        from src.controllers import OrderController, UserController
        from src.services import OrderService, UserService
        from src.db.repositories import OrderRepository, UserRepository


        def get_user_controller() -> UserController:
            return UserController(UserService(UserRepository()))


        def get_order_controller() -> OrderController:
            return OrderController(OrderService(OrderRepository()))
    """,
    "src/controllers/__init__.py": """
        from .orders import OrderController as OrderController
        from .users import UserController as UserController
    """,
    "src/controllers/users.py": """
        from src.schemas import UserResponseSchema
        from src.services import UserService


        class UserController:
            def __init__(self, service: UserService) -> None:
                self.service = service

            async def get(self, user_id: int) -> UserResponseSchema:
                return await self.service.get(user_id)
    """,
    "src/controllers/orders.py": """
        from src.services import OrderService


        class OrderController:
            def __init__(self, service: OrderService) -> None:
                self.service = service

            async def list(self) -> list[int]:
                return await self.service.list()
    """,
    "src/services/__init__.py": """
        from .orders import OrderService as OrderService
        from .users import UserService as UserService
    """,
    "src/services/users.py": """
        from src.core.exceptions import UserNotFoundError
        from src.db.repositories import UserRepository
        from src.schemas import UserResponseSchema


        class UserService:
            def __init__(self, repository: UserRepository) -> None:
                self.repository = repository

            async def get(self, user_id: int) -> UserResponseSchema:
                row = await self.repository.get(user_id)
                if row is None:
                    raise UserNotFoundError()
                return UserResponseSchema(id=row.id, name=row.name)
    """,
    "src/services/orders.py": """
        from src.db.repositories import OrderRepository


        class OrderService:
            def __init__(self, repository: OrderRepository) -> None:
                self.repository = repository

            async def list(self) -> list[int]:
                return []
    """,
    "src/db/__init__.py": "",
    "src/db/repositories/__init__.py": """
        from .orders import OrderRepository as OrderRepository
        from .users import UserRepository as UserRepository
    """,
    "src/db/repositories/users.py": """
        from src.db.models import UserModel


        class UserRepository:
            async def get(self, user_id: int) -> UserModel | None:
                return UserModel(id=user_id, name="ana")
    """,
    "src/db/repositories/orders.py": """
        from src.db.models import OrderModel


        class OrderRepository:
            model = OrderModel
    """,
    "src/db/models/__init__.py": """
        from .orders import OrderModel as OrderModel
        from .users import UserModel as UserModel
    """,
    "src/db/models/users.py": """
        from dataclasses import dataclass


        @dataclass
        class UserModel:
            id: int
            name: str
    """,
    "src/db/models/orders.py": """
        from dataclasses import dataclass


        @dataclass
        class OrderModel:
            id: int
    """,
    "src/schemas/__init__.py": """
        from .users import UserResponseSchema as UserResponseSchema
    """,
    "src/schemas/users.py": """
        from pydantic import BaseModel


        class UserResponseSchema(BaseModel):
            id: int
            name: str
    """,
    "src/core/__init__.py": "",
    "src/core/exceptions.py": """
        from tempest_fastapi_sdk import NotFoundException


        class UserNotFoundError(NotFoundException):
            code = "USER_NOT_FOUND"
    """,
    ".env": "JWT_SECRET=do-not-leak-me\n",
}
"""The two-domain fixture service, path to source."""

USERS_FILES: tuple[str, ...] = (
    "src/api/routers/users.py",
    "src/api/dependencies/auth.py",
    "src/api/dependencies/controllers.py",
    "src/controllers/users.py",
    "src/services/users.py",
    "src/db/repositories/users.py",
    "src/db/models/users.py",
    "src/schemas/users.py",
    "src/core/exceptions.py",
)
"""Every file ``GET /api/users/{user_id}`` reaches, in bundle order."""


@pytest.fixture
def service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_project_imports: None,
) -> Iterator[Path]:
    """Write the fixture service and stand inside it.

    Args:
        tmp_path (Path): Where the service is written.
        monkeypatch (pytest.MonkeyPatch): Changes the working directory.
        isolated_project_imports (None): Forgets other projects' ``src``.

    Yields:
        Path: The service root.
    """
    for relative, source in PROJECT_FILES.items():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _bundled_paths(markdown: str) -> list[str]:
    """Paths of the file sections of a bundle, in order.

    Args:
        markdown (str): The bundle.

    Returns:
        list[str]: The ``## `path``` headings, without the markup.
    """
    return [
        line.removeprefix("## `").removesuffix("`")
        for line in markdown.splitlines()
        if line.startswith("## `")
    ]


class TestResolution:
    def test_bundles_the_users_route_and_nothing_of_orders(
        self,
        service: Path,
    ) -> None:
        """Every layer of the domain is in, in layer order; ``orders`` is out.

        ``api/dependencies/controllers.py`` holds both providers, so it is
        bundled whole, but the ``orders`` controller, service, repository
        and model it mentions are not followed: only the names
        ``get_user_controller`` reads are.
        """
        result = runner.invoke(app, ["bundle-route", "GET /api/users/{user_id}"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert _bundled_paths(result.stdout) == list(USERS_FILES)
        assert "orders" not in "\n".join(_bundled_paths(result.stdout))

    def test_header_names_the_route_endpoint_and_dependencies(
        self,
        service: Path,
    ) -> None:
        result = runner.invoke(app, ["bundle-route", "GET /api/users/{user_id}"])
        assert result.stdout.startswith("# Route bundle: `GET /api/users/{user_id}`\n")
        assert "- **Endpoint:** `get_user`" in result.stdout
        assert "`require_token`" in result.stdout
        assert "`get_user_controller`" in result.stdout
        assert "| router | `src/api/routers/users.py` |" in result.stdout

    def test_each_file_is_a_complete_python_block(self, service: Path) -> None:
        result = runner.invoke(app, ["bundle-route", "GET /api/users/{user_id}"])
        source = (service / "src" / "services" / "users.py").read_text(encoding="utf-8")
        assert f"```python\n{source.rstrip()}\n```" in result.stdout

    def test_never_reads_the_env_file_nor_installed_packages(
        self,
        service: Path,
    ) -> None:
        """The SDK, pydantic and fastapi are imported, and never bundled."""
        result = runner.invoke(app, ["bundle-route", "GET /api/users/{user_id}"])
        assert "do-not-leak-me" not in result.stdout
        assert all(path.startswith("src/") for path in _bundled_paths(result.stdout))
        assert "site-packages" not in result.stdout

    def test_a_path_with_one_method_needs_no_method(self, service: Path) -> None:
        result = runner.invoke(app, ["bundle-route", "/api/orders/"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "src/controllers/orders.py" in _bundled_paths(result.stdout)
        assert "src/controllers/users.py" not in _bundled_paths(result.stdout)

    def test_the_method_is_case_insensitive(self, service: Path) -> None:
        result = runner.invoke(app, ["bundle-route", "get /api/orders/"])
        assert result.exit_code == 0, result.stdout + result.stderr

    def test_max_depth_zero_keeps_only_the_defining_files(
        self,
        service: Path,
    ) -> None:
        result = runner.invoke(
            app,
            ["bundle-route", "GET /api/users/{user_id}", "--max-depth", "0"],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert _bundled_paths(result.stdout) == [
            "src/api/routers/users.py",
            "src/api/dependencies/auth.py",
            "src/api/dependencies/controllers.py",
        ]


class TestFailures:
    def test_unknown_route_exits_one_with_a_suggestion(self, service: Path) -> None:
        result = runner.invoke(app, ["bundle-route", "GET /api/user/{user_id}"])
        assert result.exit_code == 1
        assert "no route matches 'GET /api/user/{user_id}'" in result.stderr
        assert "did you mean: GET /api/users/{user_id}" in result.stderr
        assert result.stdout == ""

    def test_wrong_method_lists_the_methods_the_path_has(
        self,
        service: Path,
    ) -> None:
        result = runner.invoke(app, ["bundle-route", "POST /api/users/{user_id}"])
        assert result.exit_code == 1
        assert "/api/users/{user_id} is served by: DELETE, GET" in result.stderr

    def test_ambiguous_path_exits_two_listing_the_methods(
        self,
        service: Path,
    ) -> None:
        result = runner.invoke(app, ["bundle-route", "/api/users/{user_id}"])
        assert result.exit_code == 2
        assert "served by several methods: DELETE, GET" in result.stderr
        assert result.stdout == ""

    def test_route_served_by_installed_code_exits_one(self, service: Path) -> None:
        """An endpoint from the standard library reaches no project file."""
        result = runner.invoke(app, ["bundle-route", "GET /version"])
        assert result.exit_code == 1
        assert "reaches no file of this project" in result.stderr
        assert result.stdout == ""


class TestOutput:
    def test_output_writes_the_file_and_keeps_stdout_empty(
        self,
        service: Path,
    ) -> None:
        destination = service / "bundle.md"
        result = runner.invoke(
            app,
            ["bundle-route", "GET /api/users/{user_id}", "--output", str(destination)],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert result.stdout == ""
        written = destination.read_text(encoding="utf-8")
        assert _bundled_paths(written) == list(USERS_FILES)
        assert "9 file(s)" in result.stderr

    def test_a_backtick_fence_in_the_source_gets_a_longer_fence(
        self,
        service: Path,
    ) -> None:
        """A docstring holding ```` ``` ```` must not close the block early."""
        models = service / "src" / "db" / "models" / "users.py"
        models.write_text(
            '"""Example:\n\n```python\nUserModel(1, "a")\n```\n"""\n'
            + models.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        result = runner.invoke(app, ["bundle-route", "GET /api/users/{user_id}"])
        assert '````python\n"""Example:' in result.stdout
