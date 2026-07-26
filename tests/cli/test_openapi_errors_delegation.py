"""Tests for delegation the analyzer can only see through generic parameters.

A pass-through layer overrides nothing, so it has no ``__init__`` to read:

    class CategoryService(BaseService[CategoryRepository, CategoryResponseSchema]):
        \"\"\"Business logic for categories.\"\"\"

The subscript is the only statement of what it delegates to. Without reading it,
the chain from a route down to the repository breaks at exactly the classes the
layering encourages — and a route that genuinely raises its repository's
exceptions was reported as declaring *unreachable* ones, which is worse than
silence: it invites deleting a correct declaration.

The second half covers the other half of that bug: a layer further down the
chain that **overrides** the method must be walked as a normal target, not just
mined for constructor configuration.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.openapi_errors import (
    _generic_delegates,
    analyze_paths,
)

EXCEPTIONS: str = '''"""Domain exceptions."""

from tempest_fastapi_sdk import ConflictException, NotFoundException


class CategoryInUseException(ConflictException):
    """Category still has services."""

    code = "CATEGORY_IN_USE"


class CategoryNotFoundException(NotFoundException):
    """Category does not exist."""

    code = "CATEGORY_NOT_FOUND"
'''

MODELS: str = '''"""ORM models."""

from tempest_fastapi_sdk import BaseModel


class CategoryModel(BaseModel):
    """A service category."""

    __tablename__ = "categories"
'''

SCHEMAS: str = '''"""DTOs."""

from tempest_fastapi_sdk import BaseSchema


class CategoryResponseSchema(BaseSchema):
    """A category as returned by the API."""
'''

REPOSITORY: str = '''"""Category data access."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import BaseRepository

from src.core.exceptions import CategoryInUseException, CategoryNotFoundException
from src.db.models import CategoryModel


class CategoryRepository(BaseRepository[CategoryModel]):
    """Repository translating the FK restriction into a domain error."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository.

        Args:
            session (AsyncSession): The async database session.
        """
        super().__init__(
            session,
            model=CategoryModel,
            not_found_exception=CategoryNotFoundException,
        )

    async def delete(self, id: UUID) -> None:
        """Delete a category.

        Args:
            id (UUID): The category to delete.

        Raises:
            CategoryInUseException: If services still reference it.
        """
        raise CategoryInUseException()
'''

PASSTHROUGH_SERVICE: str = '''"""Category business logic."""

from tempest_fastapi_sdk import BaseService

from src.db.repositories import CategoryRepository
from src.schemas import CategoryResponseSchema


class CategoryService(BaseService[CategoryRepository, CategoryResponseSchema]):
    """Pure pass-through: no __init__, no overrides."""
'''

PASSTHROUGH_CONTROLLER: str = '''"""Category orchestration."""

from uuid import UUID

from tempest_fastapi_sdk import BaseController

from src.schemas import CategoryResponseSchema
from src.services import CategoryService


class CategoryController(BaseController[CategoryService, CategoryResponseSchema]):
    """Controller declaring only the domain method."""

    async def delete_category(self, category_id: UUID) -> None:
        """Delete a category.

        Args:
            category_id (UUID): The category to delete.
        """
        await self.service.delete(category_id)
'''

ROUTER: str = '''"""Category routes."""

from uuid import UUID

from fastapi import APIRouter, Depends

from src.controllers import CategoryController

router = APIRouter(prefix="/api/categories")


def get_controller() -> CategoryController:
    """Provide the controller."""
    raise NotImplementedError


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: UUID,
    controller: CategoryController = Depends(get_controller),
) -> None:
    """Delete a category."""
    await controller.delete_category(category_id)
'''


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Write a layered service whose middle layer is a pure pass-through.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: The ``src`` directory to analyze.
    """
    src = tmp_path / "src"
    for package in ("core", "db", "api/routers"):
        (src / package).mkdir(parents=True)
    (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
    (src / "db" / "models.py").write_text(MODELS)
    (src / "db" / "repositories.py").write_text(REPOSITORY)
    (src / "schemas.py").write_text(SCHEMAS)
    (src / "services.py").write_text(PASSTHROUGH_SERVICE)
    (src / "controllers.py").write_text(PASSTHROUGH_CONTROLLER)
    (src / "api" / "routers" / "category.py").write_text(ROUTER)
    return src


def _finding(src: Path, handler: str) -> tuple[list[str], list[str]]:
    """Return the drift reported for one handler.

    Args:
        src (Path): The tree to analyze.
        handler (str): The handler function name.

    Returns:
        tuple[list[str], list[str]]: Its undocumented and unreachable lists,
        both empty when the handler produced no finding.
    """
    for found in analyze_paths([src]):
        if found.function.name == handler:
            return found.undocumented, found.unreachable
    return [], []


class TestGenericDelegates:
    """A base's generic parameters name what the class delegates to."""

    def test_chain_survives_a_service_without_init(self, tree: Path) -> None:
        """The repository's own raise is reached through the pass-through.

        ``CategoryService`` declares nothing but its bases, so
        ``BaseService[CategoryRepository, ...]`` is the only place its
        repository is named.
        """
        undocumented, _ = _finding(tree, "delete_category")

        assert "CategoryInUseException" in undocumented

    def test_chain_reaches_the_configured_not_found(self, tree: Path) -> None:
        """The configured 404 travels the same chain."""
        undocumented, _ = _finding(tree, "delete_category")

        assert "CategoryNotFoundException" in undocumented

    def test_a_correct_declaration_is_not_called_unreachable(self, tree: Path) -> None:
        """Declaring both leaves the route clean.

        The regression this guards: the route was reported as declaring two
        *unreachable* exceptions it actually raises, which invites deleting a
        correct declaration.
        """
        router = tree / "api" / "routers" / "category.py"
        router.write_text(
            router.read_text()
            .replace(
                "from fastapi import APIRouter, Depends",
                "from fastapi import APIRouter, Depends\n"
                "from tempest_fastapi_sdk import error_responses\n"
                "\n"
                "from src.core.exceptions import (\n"
                "    CategoryInUseException,\n"
                "    CategoryNotFoundException,\n"
                ")",
            )
            .replace(
                '@router.delete("/{category_id}", status_code=204)',
                "@router.delete(\n"
                '    "/{category_id}",\n'
                "    status_code=204,\n"
                "    responses=error_responses(\n"
                "        CategoryInUseException,\n"
                "        CategoryNotFoundException,\n"
                "    ),\n"
                ")",
            )
        )

        assert _finding(tree, "delete_category") == ([], [])

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "class CategoryService(BaseService[CategoryRepository, Resp]):\n"
                "    pass\n",
                {"repository": "CategoryRepository"},
            ),
            (
                "class CategoryController(BaseController[CategoryService, Resp]):\n"
                "    pass\n",
                {"service": "CategoryService"},
            ),
            (
                "class CoinPackRepository(BaseRepository[CoinPackModel]):\n    pass\n",
                {},
            ),
            ("class Holder(Generic[T]):\n    pass\n", {}),
            ("class Concrete(Holder[Raiser]):\n    pass\n", {}),
            ("class Plain(BaseService):\n    pass\n", {}),
        ],
        ids=[
            "base-service",
            "base-controller",
            "base-repository-is-not-delegation",
            "unrelated-generic",
            "unrelated-generic-parameterized",
            "unsubscripted-base",
        ],
    )
    def test_only_known_sdk_bases_are_interpreted(
        self, source: str, expected: dict[str, str]
    ) -> None:
        """A project's own generic says nothing about delegation.

        ``BaseRepository[Model]``'s parameter is the ORM model, not something
        to forward calls to, so reading positions blindly would invent a
        delegation link to a table class.
        """
        node = ast.parse(source).body[0]
        assert isinstance(node, ast.ClassDef)

        assert _generic_delegates(node) == expected


class TestOverridesDownTheChain:
    """A layer that overrides the method is walked, not only mined."""

    def test_the_override_is_followed_not_just_configured(self, tree: Path) -> None:
        """``CategoryRepository.delete`` raises explicitly, not via a kwarg.

        Collecting only constructor configuration along the chain would find
        the 404 and miss this 409, since nothing configures it.
        """
        undocumented, _ = _finding(tree, "delete_category")

        assert "CategoryInUseException" in undocumented

    def test_a_layer_without_the_method_is_skipped(self, tree: Path) -> None:
        """A middle layer that does not define the name contributes nothing.

        ``CategoryService`` has no ``delete``; the walk must pass through it
        to the repository rather than stopping.
        """
        service = tree / "services.py"
        service.write_text(
            service.read_text()
            + "\n"
            + "    async def unrelated(self) -> None:\n"
            + '        """Do something else."""\n'
        )

        undocumented, _ = _finding(tree, "delete_category")

        assert "CategoryInUseException" in undocumented
