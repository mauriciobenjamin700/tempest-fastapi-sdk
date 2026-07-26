"""Tests for how the route analyzer resolves calls into a call graph.

Resolution used to be by unqualified name alone: every function sharing a name
with the callee was followed. Two things made that visibly wrong on a real
service.

The route decorator was walked as part of the handler, so
``@router.delete("/{id}")`` registered a call to ``delete`` — which matched an
unrelated ``CategoryRepository.delete`` and reported a coin-pack route as
raising ``CategoryInUseException``. ``delete`` is the name that collides in a
layered service, which is why every drifting route was a DELETE.

And a genuine ``super().delete(id)`` or ``self.repository.delete(id)`` matched
every same-named method in the project rather than the one its receiver's class
actually resolves to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.openapi_errors import analyze_paths

EXCEPTIONS: str = '''"""Domain exceptions."""

from tempest_fastapi_sdk import ConflictException, NotFoundException


class CategoryInUseException(ConflictException):
    """Category still has services."""

    code = "CATEGORY_IN_USE"


class CoinPackNotFoundException(NotFoundException):
    """Coin pack does not exist."""

    code = "COIN_PACK_NOT_FOUND"
'''

CATEGORY_REPOSITORY: str = '''"""Category data access."""

from src.core.exceptions import CategoryInUseException


class CategoryRepository:
    """The only class in the tree with a ``delete`` method."""

    async def delete(self, id: str) -> None:
        """Delete a category.

        Raises:
            CategoryInUseException: If services still reference it.
        """
        raise CategoryInUseException()
'''

COIN_CONTROLLER: str = '''"""Coin pack controller."""

from src.core.exceptions import CoinPackNotFoundException


class CoinPackController:
    """Controller whose delete reaches nothing of its own."""

    async def delete_coin_pack(self, coin_pack_id: str) -> None:
        """Delete a coin pack."""
        await self.service.remove(coin_pack_id)

    async def read_coin_pack(self, coin_pack_id: str) -> None:
        """Read a coin pack.

        Raises:
            CoinPackNotFoundException: If it does not exist.
        """
        raise CoinPackNotFoundException()
'''

COIN_ROUTER: str = '''"""Coin pack routes."""

from fastapi import APIRouter, Depends

from src.controllers.coin import CoinPackController

router = APIRouter(prefix="/api/coin-packs")


def get_coin_pack_controller() -> CoinPackController:
    """Provide the controller."""
    return CoinPackController()


@router.delete("/{coin_pack_id}", status_code=204)
async def delete_coin_pack(
    coin_pack_id: str,
    controller: CoinPackController = Depends(get_coin_pack_controller),
) -> None:
    """Delete a coin pack."""
    await controller.delete_coin_pack(coin_pack_id)


@router.get("/{coin_pack_id}")
async def read_coin_pack(
    coin_pack_id: str,
    controller: CoinPackController = Depends(get_coin_pack_controller),
) -> None:
    """Read a coin pack."""
    await controller.read_coin_pack(coin_pack_id)
'''


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Write a service where a DELETE route and a repository share a name.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: The ``src`` directory to analyze.
    """
    src = tmp_path / "src"
    (src / "core").mkdir(parents=True)
    (src / "db").mkdir(parents=True)
    (src / "controllers").mkdir(parents=True)
    (src / "api" / "routers").mkdir(parents=True)
    (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
    (src / "db" / "category.py").write_text(CATEGORY_REPOSITORY)
    (src / "controllers" / "coin.py").write_text(COIN_CONTROLLER)
    (src / "api" / "routers" / "coin.py").write_text(COIN_ROUTER)
    return src


def _undocumented(src: Path, handler: str) -> list[str]:
    """Return the undocumented exceptions reported for one handler.

    Args:
        src (Path): The tree to analyze.
        handler (str): The handler function name.

    Returns:
        list[str]: Names reported as undocumented, empty when the handler
        produced no finding at all.
    """
    for finding in analyze_paths([src]):
        if finding.function.name == handler:
            return finding.undocumented
    return []


class TestRouteDecoratorIsNotACallEdge:
    """``@router.delete(...)`` must not make ``delete`` a call-graph edge."""

    def test_delete_route_does_not_inherit_another_domain(self, tree: Path) -> None:
        """The reported bug: a coin-pack DELETE claiming a category conflict."""
        assert "CategoryInUseException" not in _undocumented(tree, "delete_coin_pack")

    def test_the_colliding_method_is_still_analyzed(self, tree: Path) -> None:
        """The repository itself is untouched — it keeps its own exception.

        Guards against fixing the false positive by dropping the class from
        the graph, which would hide real drift instead.
        """
        (tree / "api" / "routers" / "category.py").write_text(
            '"""Category routes."""\n'
            "\n"
            "from fastapi import APIRouter\n"
            "\n"
            "from src.db.category import CategoryRepository\n"
            "\n"
            'router = APIRouter(prefix="/api/categories")\n'
            "\n"
            "\n"
            '@router.delete("/{category_id}", status_code=204)\n'
            "async def delete_category(\n"
            "    category_id: str,\n"
            "    repository: CategoryRepository = CategoryRepository(),\n"
            ") -> None:\n"
            '    """Delete a category."""\n'
            "    await repository.delete(category_id)\n"
        )

        assert "CategoryInUseException" in _undocumented(tree, "delete_category")


class TestTypedReceivers:
    """A call with a typed receiver resolves inside that class only."""

    def test_annotated_parameter_reaches_its_own_class(self, tree: Path) -> None:
        """``controller.read_coin_pack()`` reaches the controller's raise."""
        assert _undocumented(tree, "read_coin_pack") == ["CoinPackNotFoundException"]

    def test_unresolved_attribute_still_follows_by_name(self, tree: Path) -> None:
        """``self.service.remove()`` has no annotation, so the name is used.

        ``CoinPackController`` never annotates ``self.service``, so the edge
        stays name-only — the over-approximating behavior is deliberately kept
        wherever the receiver cannot be typed, since missing a real exception
        is worse than reporting an extra one.
        """
        (tree / "services").mkdir()
        (tree / "services" / "coin.py").write_text(
            '"""Coin pack service."""\n'
            "\n"
            "from src.core.exceptions import CoinPackNotFoundException\n"
            "\n"
            "\n"
            "class CoinPackService:\n"
            '    """Business logic."""\n'
            "\n"
            "    async def remove(self, coin_pack_id: str) -> None:\n"
            '        """Remove a coin pack.\n'
            "\n"
            "        Raises:\n"
            "            CoinPackNotFoundException: If it does not exist.\n"
            '        """\n'
            "        raise CoinPackNotFoundException()\n"
        )

        assert "CoinPackNotFoundException" in _undocumented(tree, "delete_coin_pack")


class TestSuperAndSelf:
    """``super().m()`` and ``self.m()`` resolve through the class hierarchy."""

    def test_super_reaches_a_known_base(self, tmp_path: Path) -> None:
        """A base defined in the tree is followed."""
        src = tmp_path / "src"
        (src / "core").mkdir(parents=True)
        (src / "api" / "routers").mkdir(parents=True)
        (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
        (src / "base.py").write_text(
            '"""Base controller."""\n'
            "\n"
            "from src.core.exceptions import CoinPackNotFoundException\n"
            "\n"
            "\n"
            "class BaseController:\n"
            '    """Generic controller."""\n'
            "\n"
            "    async def delete(self, id: str) -> None:\n"
            '        """Delete a row.\n'
            "\n"
            "        Raises:\n"
            "            CoinPackNotFoundException: If it does not exist.\n"
            '        """\n'
            "        raise CoinPackNotFoundException()\n"
        )
        (src / "controller.py").write_text(
            '"""Coin pack controller."""\n'
            "\n"
            "from src.base import BaseController\n"
            "\n"
            "\n"
            "class CoinPackController(BaseController):\n"
            '    """Concrete controller."""\n'
            "\n"
            "    async def delete_coin_pack(self, coin_pack_id: str) -> None:\n"
            '        """Delete a coin pack."""\n'
            "        await super().delete(coin_pack_id)\n"
        )
        (src / "api" / "routers" / "coin.py").write_text(
            '"""Coin pack routes."""\n'
            "\n"
            "from fastapi import APIRouter\n"
            "\n"
            "from src.controller import CoinPackController\n"
            "\n"
            'router = APIRouter(prefix="/api/coin-packs")\n'
            "\n"
            "\n"
            '@router.delete("/{coin_pack_id}", status_code=204)\n'
            "async def delete_coin_pack(\n"
            "    coin_pack_id: str,\n"
            "    controller: CoinPackController = CoinPackController(),\n"
            ") -> None:\n"
            '    """Delete a coin pack."""\n'
            "    await controller.delete_coin_pack(coin_pack_id)\n"
        )

        assert _undocumented(src, "delete_coin_pack") == ["CoinPackNotFoundException"]

    def test_super_into_an_unknown_base_reaches_nothing(self, tree: Path) -> None:
        """A base outside the scanned tree yields no edge at all.

        This is the case that produced the bug in the field: the SDK's own
        ``BaseController.delete`` is not in ``src/``, so resolving ``delete``
        by name found the one unrelated project method with that name.
        """
        (tree / "controllers" / "coin.py").write_text(
            '"""Coin pack controller."""\n'
            "\n"
            "from tempest_fastapi_sdk import BaseController\n"
            "\n"
            "\n"
            "class CoinPackController(BaseController):\n"
            '    """Concrete controller."""\n'
            "\n"
            "    async def delete_coin_pack(self, coin_pack_id: str) -> None:\n"
            '        """Delete a coin pack."""\n'
            "        await super().delete(coin_pack_id)\n"
        )

        assert _undocumented(tree, "delete_coin_pack") == []

    def test_self_call_reaches_the_same_class(self, tmp_path: Path) -> None:
        """``self.m()`` resolves to the owner's own method."""
        src = tmp_path / "src"
        (src / "core").mkdir(parents=True)
        (src / "api" / "routers").mkdir(parents=True)
        (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
        (src / "controller.py").write_text(
            '"""Coin pack controller."""\n'
            "\n"
            "from src.core.exceptions import CoinPackNotFoundException\n"
            "\n"
            "\n"
            "class CoinPackController:\n"
            '    """Concrete controller."""\n'
            "\n"
            "    async def delete_coin_pack(self, coin_pack_id: str) -> None:\n"
            '        """Delete a coin pack."""\n'
            "        await self._ensure_exists(coin_pack_id)\n"
            "\n"
            "    async def _ensure_exists(self, coin_pack_id: str) -> None:\n"
            '        """Check existence.\n'
            "\n"
            "        Raises:\n"
            "            CoinPackNotFoundException: If it does not exist.\n"
            '        """\n'
            "        raise CoinPackNotFoundException()\n"
        )
        (src / "api" / "routers" / "coin.py").write_text(
            '"""Coin pack routes."""\n'
            "\n"
            "from fastapi import APIRouter\n"
            "\n"
            "from src.controller import CoinPackController\n"
            "\n"
            'router = APIRouter(prefix="/api/coin-packs")\n'
            "\n"
            "\n"
            '@router.delete("/{coin_pack_id}", status_code=204)\n'
            "async def delete_coin_pack(\n"
            "    coin_pack_id: str,\n"
            "    controller: CoinPackController = CoinPackController(),\n"
            ") -> None:\n"
            '    """Delete a coin pack."""\n'
            "    await controller.delete_coin_pack(coin_pack_id)\n"
        )

        assert _undocumented(src, "delete_coin_pack") == ["CoinPackNotFoundException"]


class TestAnnotatedAttributes:
    """``self.<attr>.m()`` resolves through the attribute's annotation."""

    @pytest.mark.parametrize(
        "init_body",
        [
            "        self.repository: CategoryRepository = repository\n",
            "        self.repository = repository\n",
        ],
        ids=["annotated-assignment", "annotated-parameter"],
    )
    def test_attribute_resolves_to_its_class(
        self, tmp_path: Path, init_body: str
    ) -> None:
        """Both annotation shapes reach the attribute's own method."""
        src = tmp_path / "src"
        (src / "core").mkdir(parents=True)
        (src / "api" / "routers").mkdir(parents=True)
        (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
        (src / "repository.py").write_text(CATEGORY_REPOSITORY)
        (src / "service.py").write_text(
            '"""Category service."""\n'
            "\n"
            "from src.repository import CategoryRepository\n"
            "\n"
            "\n"
            "class CategoryService:\n"
            '    """Business logic."""\n'
            "\n"
            "    def __init__(self, repository: CategoryRepository) -> None:\n"
            '        """Initialize.\n'
            "\n"
            "        Args:\n"
            "            repository (CategoryRepository): Data access.\n"
            '        """\n' + init_body + "\n"
            "    async def drop(self, category_id: str) -> None:\n"
            '        """Drop a category."""\n'
            "        await self.repository.delete(category_id)\n"
        )
        (src / "api" / "routers" / "category.py").write_text(
            '"""Category routes."""\n'
            "\n"
            "from fastapi import APIRouter\n"
            "\n"
            "from src.service import CategoryService\n"
            "\n"
            'router = APIRouter(prefix="/api/categories")\n'
            "\n"
            "\n"
            '@router.delete("/{category_id}", status_code=204)\n'
            "async def delete_category(\n"
            "    category_id: str,\n"
            "    service: CategoryService = CategoryService(None),\n"
            ") -> None:\n"
            '    """Delete a category."""\n'
            "    await service.drop(category_id)\n"
        )

        assert _undocumented(src, "delete_category") == ["CategoryInUseException"]
