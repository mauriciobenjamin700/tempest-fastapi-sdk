"""Tests for the ``services`` / ``repositories`` layer rules (#520).

Each rule is exercised with a source tree written to a temporary
directory, in the shape that shipped in the service the issue describes,
and with the near-misses it must leave alone. The scaffold is checked as
``tempest new`` writes it, and again with the ``CLAUDE.md`` examples
copied into it — the template's own advice has to pass its own rules.
"""

from __future__ import annotations

import inspect
import io
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli import layer_lint
from tempest_fastapi_sdk.cli.layer_lint import (
    BASE_REPOSITORY_METHODS,
    ENUM_NAME_SUFFIX,
    NO_BASE_SERVICE_MARKER,
    ROUTER_BUILDS_MARKER,
    LayerFinding,
    lint_layers,
    run_lint_layers,
    scan_roots,
)
from tempest_fastapi_sdk.cli.main import LINT_LAYERS_STEP
from tempest_fastapi_sdk.cli.main import app as cli_app
from tempest_fastapi_sdk.db.enums import ENUM_CLASS_SUFFIX
from tempest_fastapi_sdk.db.repository import BaseRepository

runner = CliRunner()

_BLOCK = re.compile(r"```python\n(# (src/[\w/]+\.py)\n.*?)```", re.DOTALL)


def _write(root: Path, relative: str, source: str) -> Path:
    """Write a dedented source file under ``root``.

    Args:
        root (Path): The project root.
        relative (str): Path of the file below ``root``.
        source (str): The file content (dedented before writing).

    Returns:
        Path: The written file.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def _codes(findings: list[LayerFinding]) -> list[tuple[str, int, str]]:
    """Reduce findings to ``(file name, line, code)`` triples.

    Args:
        findings (list[LayerFinding]): The checker output.

    Returns:
        list[tuple[str, int, str]]: One triple per finding, in order.
    """
    return [(finding.path.name, finding.line, finding.code) for finding in findings]


GOOD_SERVICE = """
    from tempest_fastapi_sdk import BaseService

    from src.db.repositories import ProductRepository
    from src.schemas import ProductResponseSchema


    class ProductService(BaseService[ProductRepository, ProductResponseSchema]):
        \"\"\"Business rules for products.\"\"\"

        async def taken(self, name: str) -> bool:
            \"\"\"Tell whether a name is registered.\"\"\"
            return await self.repository.exists({"name": name})
"""


class TestServiceInheritsBaseService:
    """``SVC001``: every public class in ``services/`` is a ``BaseService``."""

    def test_flags_the_hand_rolled_service(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/news.py",
            """
            class NewsService:
                \"\"\"News, rebuilt by hand.\"\"\"

                async def list(self) -> list[int]:
                    \"\"\"Every item.\"\"\"
                    return []
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [("news.py", 1, "SVC001")]
        assert "BaseService[Repository, ResponseSchema]" in findings[0].message
        assert NO_BASE_SERVICE_MARKER in findings[0].message

    def test_accepts_base_service_subscripted_or_qualified(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "src/services/product.py", GOOD_SERVICE)
        _write(
            tmp_path,
            "src/services/order.py",
            """
            import tempest_fastapi_sdk as sdk


            class OrderService(sdk.BaseService):
                \"\"\"Orders.\"\"\"
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_accepts_a_subclass_of_a_project_service(self, tmp_path: Path) -> None:
        """Resolution reaches a fixed point whatever the file order."""
        _write(
            tmp_path,
            "src/services/a_admin_product.py",
            """
            from src.services.product import ProductService


            class AdminProductService(ProductService):
                \"\"\"Products, with the admin rules.\"\"\"
            """,
        )
        _write(tmp_path, "src/services/product.py", GOOD_SERVICE)
        assert lint_layers(tmp_path) == []

    def test_accepts_an_sdk_service_base(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/auth.py",
            """
            from tempest_fastapi_sdk.auth import UserAuthService as SdkAuth


            class AuthService(SdkAuth):
                \"\"\"Signup and login, extended.\"\"\"
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_a_service_named_class_from_elsewhere_is_not_accepted(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "src/services/news.py",
            """
            from somewhere import RemoteService


            class NewsService(RemoteService):
                \"\"\"News.\"\"\"
            """,
        )
        assert _codes(lint_layers(tmp_path)) == [("news.py", 4, "SVC001")]

    def test_marker_opts_out(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalogue.py",
            f"""
            class CatalogueService:  {NO_BASE_SERVICE_MARKER}
                \"\"\"Static catalogue read from a file; there is no repository.\"\"\"

                def __init__(self, path: str) -> None:
                    \"\"\"Remember the file.\"\"\"
                    self.path = path
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_marker_on_a_split_header(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalogue.py",
            f"""
            class CatalogueService(
                object,
            ):  {NO_BASE_SERVICE_MARKER}
                \"\"\"Static catalogue.\"\"\"
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_marker_inside_the_body_does_not_count(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalogue.py",
            f"""
            class CatalogueService:
                \"\"\"Static catalogue.\"\"\"

                path = "x"  {NO_BASE_SERVICE_MARKER}
            """,
        )
        assert _codes(lint_layers(tmp_path)) == [("catalogue.py", 1, "SVC001")]

    def test_private_class_is_not_checked(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/product.py",
            GOOD_SERVICE
            + """

    class _Row:
        \"\"\"Internal row shape.\"\"\"
""",
        )
        assert lint_layers(tmp_path) == []


class TestOneServicePerModule:
    """``SVC002``: one public class and no module-level function."""

    def test_flags_each_extra_class_and_each_function(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/feeds.py",
            """
            import re

            from tempest_fastapi_sdk import BaseService


            def page_envelope(items: list[int]) -> dict[str, list[int]]:
                \"\"\"Wrap items.\"\"\"
                return {"items": items}


            def word_pattern(word: str) -> re.Pattern[str]:
                \"\"\"Compile a word regex.\"\"\"
                return re.compile(word)


            async def alignment_of(score: float) -> str:
                \"\"\"Name a score.\"\"\"
                return "left"


            class NewsService(BaseService):
                \"\"\"News.\"\"\"


            class TrendService(BaseService):
                \"\"\"Trends.\"\"\"


            class FactCheckService(BaseService):
                \"\"\"Fact checks.\"\"\"
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [
            ("feeds.py", 6, "SVC002"),
            ("feeds.py", 11, "SVC002"),
            ("feeds.py", 16, "SVC002"),
            ("feeds.py", 25, "SVC002"),
            ("feeds.py", 29, "SVC002"),
        ]
        assert "utils/" in findings[0].message
        assert "`TrendService` shares this module with `NewsService`" in (
            findings[3].message
        )

    def test_private_function_is_still_a_module_function(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/product.py",
            GOOD_SERVICE
            + """

    def _slug(name: str) -> str:
        \"\"\"Slugify.\"\"\"
        return name.lower()
""",
        )
        assert [finding.code for finding in lint_layers(tmp_path)] == ["SVC002"]

    def test_module_dunder_getattr_is_allowed(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/__init__.py",
            """
            from typing import Any


            def __getattr__(name: str) -> Any:
                \"\"\"Lazy re-export.\"\"\"
                raise AttributeError(name)
            """,
        )
        assert lint_layers(tmp_path) == []


class TestRedundantInit:
    """``SVC003``: an ``__init__`` that only does what the base does."""

    @pytest.mark.parametrize(
        "body",
        [
            "self.repository = repository",
            "self.repository: ProductRepository = repository",
            "super().__init__(repository)",
            "super().__init__(repository=repository)",
            "BaseService.__init__(self, repository)",
            "super().__init__(repository)\n"
            + " " * 20
            + "self.repository = repository",
        ],
    )
    def test_flags_each_redundant_shape(self, tmp_path: Path, body: str) -> None:
        _write(
            tmp_path,
            "src/services/product.py",
            f"""
            from tempest_fastapi_sdk import BaseService


            class ProductService(BaseService):
                \"\"\"Products.\"\"\"

                def __init__(self, repository: ProductRepository) -> None:
                    \"\"\"Store the repository.\"\"\"
                    {body}
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [("product.py", 7, "SVC003")]
        assert "delete it" in findings[0].message

    @pytest.mark.parametrize(
        "signature, body",
        [
            ("session: AsyncSession", "super().__init__(ProductRepository(session))"),
            (
                "repository: ProductRepository, mailer: Mailer",
                "super().__init__(repository)\n" + " " * 20 + "self.mailer = mailer",
            ),
            ("repository: ProductRepository", "self.repository = build(repository)"),
        ],
    )
    def test_leaves_an_init_with_its_own_work(
        self, tmp_path: Path, signature: str, body: str
    ) -> None:
        _write(
            tmp_path,
            "src/services/product.py",
            f"""
            from tempest_fastapi_sdk import BaseService


            class ProductService(BaseService):
                \"\"\"Products.\"\"\"

                def __init__(self, {signature}) -> None:
                    \"\"\"Build the service.\"\"\"
                    {body}
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_opted_out_service_keeps_its_init(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalogue.py",
            f"""
            class CatalogueService:  {NO_BASE_SERVICE_MARKER}
                \"\"\"Static catalogue.\"\"\"

                def __init__(self, repository: object) -> None:
                    \"\"\"Store the source.\"\"\"
                    self.repository = repository
            """,
        )
        assert lint_layers(tmp_path) == []


class TestRepositoryWrapper:
    """``REPO001``: a method that only forwards to ``BaseRepository``."""

    def test_flags_the_three_wrappers_of_the_issue(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/db/repositories/news.py",
            """
            from typing import Any

            from tempest_fastapi_sdk import BaseRepository


            class NewsRepository(BaseRepository[NewsModel]):
                \"\"\"News rows.\"\"\"

                async def list_all(self) -> list[NewsModel]:
                    \"\"\"Every row.\"\"\"
                    return await self.list()

                async def list_ordered(self) -> list[NewsModel]:
                    return await self.list(order_by="-created_at")

                def list_filtered(self, filters: dict[str, Any]) -> Any:
                    \"\"\"Rows matching the filters, not awaited.\"\"\"
                    return self.list(filters)
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [
            ("news.py", 9, "REPO001"),
            ("news.py", 13, "REPO001"),
            ("news.py", 16, "REPO001"),
        ]
        assert "BaseRepository.list" in findings[0].message
        assert "`repository.list(...)`" in findings[0].message

    def test_leaves_real_queries_and_overrides(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/db/repositories/news.py",
            """
            from sqlalchemy import select
            from tempest_fastapi_sdk import BaseRepository


            class NewsRepository(BaseRepository[NewsModel]):
                \"\"\"News rows.\"\"\"

                async def delete(self, id: int) -> None:
                    \"\"\"Deleting news hides it.\"\"\"
                    return await self.soft_delete(id)

                async def trending(self) -> list[NewsModel]:
                    \"\"\"A query the base cannot express.\"\"\"
                    result = await self.session.execute(select(NewsModel))
                    return list(result.scalars())

                async def recent(self) -> list[NewsModel]:
                    \"\"\"Forwards to the class's own method, not the base.\"\"\"
                    return await self.paginate()

                async def paginate(self) -> list[NewsModel]:
                    \"\"\"Overridden here.\"\"\"
                    return []

                async def via_other(self) -> list[NewsModel]:
                    \"\"\"Calls another object.\"\"\"
                    return await self.session.list()
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_base_method_list_matches_base_repository(self) -> None:
        """A method added to the base must reach the rule, not slip past it."""
        public = {
            name
            for name, value in vars(BaseRepository).items()
            if not name.startswith("_")
            and (
                inspect.isfunction(value)
                or isinstance(value, classmethod | staticmethod)
            )
        }
        assert public == BASE_REPOSITORY_METHODS


class TestEnumSuffix:
    """``ENUM001``: a ``BaseStrEnum`` subclass name ends in ``Enum`` (#518)."""

    def test_flags_a_direct_subclass_with_the_expected_name(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "src/core/enums.py",
            """
            from tempest_fastapi_sdk import BaseStrEnum


            class OrderStatus(BaseStrEnum):
                \"\"\"Order lifecycle.\"\"\"

                OPEN = "open"


            class PartyEnum(BaseStrEnum):
                \"\"\"Already suffixed.\"\"\"

                LEFT = "left"
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [("enums.py", 4, "ENUM001")]
        assert "rename it to `OrderStatusEnum`" in findings[0].message
        assert "no migration is needed" in findings[0].message

    def test_follows_an_alias_and_a_project_base(self, tmp_path: Path) -> None:
        """Inheritance resolves to a fixed point across files and aliases."""
        _write(
            tmp_path,
            "src/schemas/a_status.py",
            """
            from src.core.base import DomainEnum


            class Status(DomainEnum):
                \"\"\"Two levels below BaseStrEnum.\"\"\"

                ON = "on"
            """,
        )
        _write(
            tmp_path,
            "src/core/base.py",
            """
            from tempest_fastapi_sdk.core.enums import BaseStrEnum as StrBase


            class DomainEnum(StrBase):
                \"\"\"Project base without members.\"\"\"


            class _Hidden(StrBase):
                \"\"\"Private: names no database type.\"\"\"
            """,
        )
        assert _codes(lint_layers(tmp_path)) == [("a_status.py", 4, "ENUM001")]

    def test_leaves_other_enums_alone(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app/core/enums.py",
            """
            from enum import Enum, StrEnum

            from tempest_fastapi_sdk import BaseIntEnum


            class Color(StrEnum):
                RED = "red"


            class Shape(Enum):
                CIRCLE = 1


            class Priority(BaseIntEnum):
                LOW = 1
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_suffix_matches_enum_type_name(self) -> None:
        """The checker's suffix is the one ``enum_type_name`` strips."""
        assert ENUM_NAME_SUFFIX == ENUM_CLASS_SUFFIX


INLINE_ROUTER = """
    from fastapi import APIRouter, Depends
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.api.dependencies.resources import get_session
    from src.controllers import ProductController
    from src.db.repositories import ProductRepository
    from src.schemas import ProductResponseSchema
    from src.services import ProductService

    router: APIRouter = APIRouter(prefix="/products")


    @router.get("/{product_id}")
    async def get_product(
        product_id: int,
        session: AsyncSession = Depends(get_session),
    ) -> ProductResponseSchema:
        \"\"\"Read a product.\"\"\"
        controller = ProductController(ProductService(ProductRepository(session)))
        return await controller.get_by_id(product_id)
"""


class TestRouterBuildsController:
    """``RTR001``: a controller or service constructed inside ``routers/``."""

    def test_flags_the_controller_built_in_the_route(self, tmp_path: Path) -> None:
        """Both classes of the nested construction are reported, once each."""
        _write(tmp_path, "src/api/routers/product.py", INLINE_ROUTER)
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [
            ("product.py", 19, "RTR001"),
            ("product.py", 19, "RTR001"),
        ]
        messages = sorted(finding.message for finding in findings)
        assert messages[0].startswith("`ProductController(...)` is built inside")
        assert "api/dependencies/controllers.py" in messages[0]
        assert messages[1].startswith("`ProductService(...)` is built inside")
        assert "api/dependencies/services.py" in messages[1]

    def test_flags_a_provider_and_a_module_level_instance(self, tmp_path: Path) -> None:
        """The provider the HTTP recipe used to keep in the router module."""
        _write(
            tmp_path,
            "src/api/routers/auth.py",
            """
            from fastapi import APIRouter, Depends
            from sqlalchemy.ext.asyncio import AsyncSession

            from src.services import auth

            router = APIRouter(prefix="/auth")
            shared = auth.AuthService()


            def get_auth_service(
                session: AsyncSession = Depends(get_session),
            ) -> auth.AuthService:
                return auth.AuthService(session)
            """,
        )
        assert _codes(lint_layers(tmp_path)) == [
            ("auth.py", 7, "RTR001"),
            ("auth.py", 13, "RTR001"),
        ]

    def test_depends_on_a_provider_is_clean(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/api/routers/product.py",
            """
            from fastapi import APIRouter, Depends, HTTPException

            from src.api.dependencies.controllers import get_product_controller
            from src.controllers import ProductController
            from src.schemas import ProductResponseSchema

            router: APIRouter = APIRouter(prefix="/products")


            @router.get("/{product_id}")
            async def get_product(
                product_id: int,
                controller: ProductController = Depends(get_product_controller),
            ) -> ProductResponseSchema:
                if product_id < 0:
                    raise HTTPException(status_code=404)
                return await controller.get_by_id(product_id)


            def build_product_service() -> None:
                get_product_service()
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_construction_outside_routers_is_not_read(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/api/dependencies/controllers.py",
            """
            def get_product_controller() -> ProductController:
                return ProductController(ProductService(ProductRepository()))
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_marker_opts_out_one_call(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/api/routers/push.py",
            f"""
            from tempest_fastapi_sdk import make_push_router

            router = make_push_router(
                service=DeviceService(transports),  {ROUTER_BUILDS_MARKER}
            )
            other = ReportService()
            """,
        )
        assert _codes(lint_layers(tmp_path)) == [("push.py", 6, "RTR001")]


class TestRouterReturnsModel:
    """``RTR002``: a route whose declared response is an ORM model."""

    def test_flags_the_return_annotation(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/api/routers/product.py",
            """
            from fastapi import APIRouter

            from src.db.models import ProductModel

            router = APIRouter()


            @router.get("/{product_id}")
            async def get_product(product_id: int) -> ProductModel:
                ...
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [("product.py", 9, "RTR002")]
        assert findings[0].message.startswith(
            "route `get_product` returns the ORM model `ProductModel` "
            "(return annotation)"
        )

    def test_flags_nested_union_string_and_response_model(self, tmp_path: Path) -> None:
        """The model is found at any depth and under either declaration."""
        _write(tmp_path, "src/db/models/order.py", "class Order: ...\n")
        _write(
            tmp_path,
            "src/api/routers/order.py",
            """
            from typing import Annotated

            from fastapi import APIRouter

            from src import db
            from src.db import Order
            from src.db.models.user import UserModel as Account

            router = APIRouter()


            @router.get("/")
            async def list_orders() -> list[Annotated[Order, "x"]] | None:
                ...


            @router.post("/", response_model=db.Order)
            async def create_order() -> "Account":
                ...


            @router.api_route("/raw", methods=["GET"], response_model=Account)
            def raw() -> dict[str, Account]:
                ...
            """,
        )
        findings = lint_layers(tmp_path)
        assert _codes(findings) == [
            ("order.py", 13, "RTR002"),
            ("order.py", 18, "RTR002"),
            ("order.py", 18, "RTR002"),
            ("order.py", 23, "RTR002"),
        ]
        assert "`Order` (response_model=)" in findings[2].message

    def test_leaves_schemas_and_non_routes_alone(self, tmp_path: Path) -> None:
        """A schema of the same name, a helper and a route with no model."""
        _write(tmp_path, "src/db/models/product.py", "class Product: ...\n")
        _write(
            tmp_path,
            "src/api/routers/product.py",
            """
            from fastapi import APIRouter

            from src.db.models import ProductModel
            from src.schemas import Product, ProductResponseSchema

            router = APIRouter()


            def to_model(data: ProductResponseSchema) -> ProductModel:
                ...


            @router.get("/{product_id}")
            async def get_product(product_id: int) -> Product:
                ...


            @router.get("/", response_model=list[ProductResponseSchema])
            async def list_products():
                ...


            @staticmethod
            def unrelated() -> ProductModel:
                ...
            """,
        )
        assert lint_layers(tmp_path) == []

    def test_models_outside_routers_are_not_read(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/api/dependencies/auth.py",
            """
            from src.db.models import UserModel


            @router.get("/me")
            async def me() -> UserModel: ...
            """,
        )
        assert lint_layers(tmp_path) == []


class TestDiscovery:
    """Where the checker looks, and what it never reads."""

    def test_app_root_and_nested_layers(self, tmp_path: Path) -> None:
        _write(
            tmp_path, "app/billing/services/invoice.py", "class InvoiceService: ...\n"
        )
        assert _codes(lint_layers(tmp_path)) == [("invoice.py", 1, "SVC001")]

    def test_target_may_be_the_source_root(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/services/invoice.py", "class InvoiceService: ...\n")
        assert scan_roots(tmp_path / "src") == [tmp_path / "src"]
        assert len(lint_layers(tmp_path / "src")) == 1

    def test_virtualenv_and_tests_are_never_read(self, tmp_path: Path) -> None:
        for relative in (
            ".venv/lib/pkg/services/x.py",
            "tests/services/x.py",
            "node_modules/a/services/x.py",
        ):
            _write(tmp_path, relative, "class XService: ...\n")
        assert lint_layers(tmp_path) == []

    def test_a_file_that_does_not_parse_is_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/services/broken.py", "class (:\n")
        assert lint_layers(tmp_path) == []

    def test_no_layers_is_clean(self, tmp_path: Path) -> None:
        assert lint_layers(tmp_path) == []
        assert lint_layers(tmp_path / "missing") == []


class TestRunner:
    """``run_lint_layers``: the callable ``tempest check`` runs."""

    def test_prints_one_line_per_finding_and_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write(tmp_path, "src/services/news.py", "class NewsService: ...\n")
        monkeypatch.chdir(tmp_path)
        stream = io.StringIO()
        assert run_lint_layers(".", stream=stream) == 1
        lines = stream.getvalue().splitlines()
        assert lines[0].startswith("src/services/news.py:1: SVC001 `NewsService`")
        assert lines[-1] == "1 layer finding(s)."

    def test_clean_project_passes(self, tmp_path: Path) -> None:
        stream = io.StringIO()
        assert run_lint_layers(str(tmp_path), stream=stream) == 0
        assert stream.getvalue() == "Layer rules: no findings.\n"

    def test_command_exit_codes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        clean = runner.invoke(cli_app, ["lint-layers", str(tmp_path)])
        assert clean.exit_code == 0, clean.stdout
        _write(tmp_path, "src/services/news.py", "class NewsService: ...\n")
        dirty = runner.invoke(cli_app, ["lint-layers"])
        assert dirty.exit_code == 1
        assert "src/services/news.py:1: SVC001" in dirty.stdout

    def test_check_runs_the_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``tempest check`` hands the layer step to the shared gate."""
        import tempest_cli.lint

        seen: dict[str, Any] = {}

        def fake_full_check(target: str, **options: Any) -> int:
            seen["target"] = target
            seen["steps"] = list(options["extra_steps"])
            return 0

        monkeypatch.setattr(tempest_cli.lint, "run_full_check", fake_full_check)
        result = runner.invoke(cli_app, ["check", str(tmp_path)])
        assert result.exit_code == 0, result.stdout
        assert seen["steps"] == [LINT_LAYERS_STEP]
        assert LINT_LAYERS_STEP.name == "lint-layers"
        assert LINT_LAYERS_STEP.run is layer_lint.run_lint_layers


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Scaffold a project with the extras that add layers.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Pytest's temporary
            directory factory.

    Returns:
        Path: The generated project root.
    """
    parent = tmp_path_factory.mktemp("layers")
    result = runner.invoke(
        cli_app,
        ["new", "demo", "--path", str(parent), "--extras", "auth,queue,tasks,ssr"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    return parent / "demo"


class TestScaffold:
    """The project ``tempest new`` writes passes its own rules."""

    def test_scaffold_is_clean(self, project: Path) -> None:
        assert (project / "src" / "services").is_dir()
        assert (project / "src" / "db" / "repositories").is_dir()
        assert lint_layers(project) == []

    def test_claude_md_examples_are_clean(self, project: Path) -> None:
        """Every ``# src/...`` block of the template, written to disk, passes."""
        written = []
        document = (project / "CLAUDE.md").read_text(encoding="utf-8")
        for source, relative in _BLOCK.findall(document):
            written.append(_write(project, relative, source))
        assert project / "src/services/product.py" in written
        assert project / "src/db/repositories/product.py" in written
        assert project / "src/core/enums.py" in written
        assert project / "src/api/routers/product.py" in written
        assert lint_layers(project) == []

    def test_claude_md_router_built_inline_is_flagged(self, project: Path) -> None:
        """The template's router, rewritten to build the controller, fails.

        Proves ``RTR001`` reads the router the template teaches: the same
        block with the ``Depends`` provider swapped for the construction
        the provider holds.
        """
        document = (project / "CLAUDE.md").read_text(encoding="utf-8")
        blocks = {relative: source for source, relative in _BLOCK.findall(document)}
        router = blocks["src/api/routers/product.py"]
        broken = router.replace(
            "Depends(get_product_controller)",
            "ProductController(ProductService(ProductRepository(session)))",
        )
        assert broken != router
        written = _write(project / "broken", "src/api/routers/product.py", broken)
        codes = [finding.code for finding in lint_layers(written.parents[3])]
        assert codes == ["RTR001", "RTR001"]

    def test_claude_md_documents_the_gate(self, project: Path) -> None:
        """The template names the step, every code and the opt-out marker."""
        document = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "does not enforce these yet" not in document
        for needle in (
            "tempest lint-layers",
            NO_BASE_SERVICE_MARKER,
            "SVC001",
            "SVC002",
            "SVC003",
            "REPO001",
            "ENUM001",
            "RTR001",
            "RTR002",
            ROUTER_BUILDS_MARKER,
        ):
            assert needle in document
