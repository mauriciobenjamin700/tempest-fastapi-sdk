"""Tests for exceptions a route reaches only through an inherited SDK method.

A repository names its 404 once, in its constructor
(``not_found_exception=CoinPackNotFoundException``), and never again: the
``raise`` lives in :class:`~tempest_fastapi_sdk.BaseRepository`, outside the
scanned tree. Every route deleting or reading a coin pack therefore produces a
404 that no ``raise`` statement in the project can show, and the analyzer used
to report none of them.

The last class here is the important one: it asserts the analyzer's
kwarg-to-method table against ``BaseRepository`` itself, so adding a raise site
to the repository without updating the table fails the suite instead of silently
under-reporting.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tempest_fastapi_sdk
from tempest_fastapi_sdk.cli.openapi_errors import (
    CONFIGURED_RAISERS,
    CONFLICT_FALLBACK_KWARG,
    analyze_paths,
)

EXCEPTIONS: str = '''"""Domain exceptions."""

from tempest_fastapi_sdk import ConflictException, NotFoundException


class CoinPackNotFoundException(NotFoundException):
    """Coin pack does not exist."""

    code = "COIN_PACK_NOT_FOUND"


class CoinPackAlreadyExistsException(ConflictException):
    """Coin pack name is taken."""

    code = "COIN_PACK_ALREADY_EXISTS"
'''

REPOSITORY: str = '''"""Coin pack data access."""

from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import BaseRepository

from src.core.exceptions import (
    CoinPackAlreadyExistsException,
    CoinPackNotFoundException,
)
from src.db.models import CoinPackModel


class CoinPackRepository(BaseRepository[CoinPackModel]):
    """Repository configuring both of its domain failures."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository.

        Args:
            session (AsyncSession): The async database session.
        """
        super().__init__(
            session,
            model=CoinPackModel,
            not_found_exception=CoinPackNotFoundException,
            create_conflict_exception=CoinPackAlreadyExistsException,
        )
'''

MODELS: str = '''"""ORM models."""

from tempest_fastapi_sdk import BaseModel


class CoinPackModel(BaseModel):
    """A purchasable pack of coins."""

    __tablename__ = "coin_packs"
'''

SERVICE: str = '''"""Coin pack business logic."""

from tempest_fastapi_sdk import BaseService

from src.db.repositories import CoinPackRepository
from src.schemas import CoinPackResponseSchema


class CoinPackService(
    BaseService[CoinPackRepository, CoinPackResponseSchema, CoinPackResponseSchema]
):
    """Service inheriting every CRUD pass-through."""

    def __init__(self, repository: CoinPackRepository) -> None:
        """Initialize the service.

        Args:
            repository (CoinPackRepository): Data access.
        """
        super().__init__(repository=repository)

    async def create(self, data: CoinPackResponseSchema) -> None:
        """Persist a new coin pack.

        Args:
            data (CoinPackResponseSchema): The pack to create.
        """
        await self.repository.add(data)
'''

CONTROLLER: str = '''"""Coin pack orchestration."""

from uuid import UUID

from tempest_fastapi_sdk import BaseController

from src.schemas import CoinPackResponseSchema
from src.services import CoinPackService


class CoinPackController(
    BaseController[CoinPackService, CoinPackResponseSchema, CoinPackResponseSchema]
):
    """Controller that only forwards."""

    def __init__(self, service: CoinPackService) -> None:
        """Initialize the controller.

        Args:
            service (CoinPackService): Business logic.
        """
        super().__init__(service)

    async def delete_coin_pack(self, coin_pack_id: UUID) -> None:
        """Delete a coin pack.

        Args:
            coin_pack_id (UUID): The pack to delete.
        """
        await super().delete(coin_pack_id)

    async def create_coin_pack(self, data: CoinPackResponseSchema) -> None:
        """Create a coin pack.

        Args:
            data (CoinPackResponseSchema): The pack to create.
        """
        await self.service.create(data)
'''

ROUTER: str = '''"""Coin pack routes."""

from uuid import UUID

from fastapi import APIRouter, Depends

from src.controllers import CoinPackController

router = APIRouter(prefix="/api/coin-packs")


def get_controller() -> CoinPackController:
    """Provide the controller."""
    raise NotImplementedError


@router.delete("/{coin_pack_id}", status_code=204)
async def delete_coin_pack(
    coin_pack_id: UUID,
    controller: CoinPackController = Depends(get_controller),
) -> None:
    """Delete a coin pack."""
    await controller.delete_coin_pack(coin_pack_id)


@router.post("/", status_code=201)
async def create_coin_pack(
    controller: CoinPackController = Depends(get_controller),
) -> None:
    """Create a coin pack."""
    await controller.create_coin_pack(None)
'''

SCHEMAS: str = '''"""DTOs."""

from tempest_fastapi_sdk import BaseSchema


class CoinPackResponseSchema(BaseSchema):
    """A coin pack as returned by the API."""
'''


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Write a layered service whose failures are configured, never raised.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: The ``src`` directory to analyze.
    """
    src = tmp_path / "src"
    for package in ("core", "db", "services", "controllers", "api/routers"):
        (src / package).mkdir(parents=True)
    (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
    (src / "db" / "models.py").write_text(MODELS)
    (src / "db" / "repositories.py").write_text(REPOSITORY)
    (src / "schemas.py").write_text(SCHEMAS)
    (src / "services.py").write_text(SERVICE)
    (src / "controllers.py").write_text(CONTROLLER)
    (src / "api" / "routers" / "coin.py").write_text(ROUTER)
    return src


def _undocumented(src: Path, handler: str) -> list[str]:
    """Return the undocumented exceptions reported for one handler.

    Args:
        src (Path): The tree to analyze.
        handler (str): The handler function name.

    Returns:
        list[str]: Names reported as undocumented, empty when the handler
        produced no finding.
    """
    for finding in analyze_paths([src]):
        if finding.function.name == handler:
            return finding.undocumented
    return []


class TestConfiguredExceptionsAreReached:
    """A class handed to the base constructor counts as raised."""

    def test_delete_reaches_the_configured_not_found(self, tree: Path) -> None:
        """The 404 of a DELETE route is reported even though no code raises it.

        The chain is route → ``CoinPackController.delete_coin_pack`` →
        ``super().delete`` (the SDK's) → ``self.service`` → ``self.repository``
        → the ``not_found_exception`` that repository configured.
        """
        assert _undocumented(tree, "delete_coin_pack") == ["CoinPackNotFoundException"]

    def test_create_reaches_the_configured_conflict(self, tree: Path) -> None:
        """``repository.add`` reaches ``create_conflict_exception``."""
        assert _undocumented(tree, "create_coin_pack") == [
            "CoinPackAlreadyExistsException"
        ]

    def test_the_kinds_do_not_leak_into_each_other(self, tree: Path) -> None:
        """A create conflict is not attributed to a delete, nor a 404 to a create.

        Attributing every configured class to every inherited method would
        trade the old false negative for a false positive, so the mapping is
        per-method.
        """
        assert "CoinPackAlreadyExistsException" not in _undocumented(
            tree, "delete_coin_pack"
        )
        assert "CoinPackNotFoundException" not in _undocumented(
            tree, "create_coin_pack"
        )

    def test_a_sibling_repository_does_not_donate_its_exceptions(
        self, tree: Path
    ) -> None:
        """Only ``service`` / ``repository`` are followed as delegation links.

        A service holding extra repositories used to hand their configured
        404s to every route that reached it — one missing exception became
        several wrong ones.
        """
        (tree / "db" / "other.py").write_text(
            '"""Unrelated data access."""\n'
            "\n"
            "from sqlalchemy.ext.asyncio import AsyncSession\n"
            "from tempest_fastapi_sdk import BaseRepository, NotFoundException\n"
            "\n"
            "from src.db.models import CoinPackModel\n"
            "\n"
            "\n"
            "class OtherNotFoundException(NotFoundException):\n"
            '    """Unrelated 404."""\n'
            "\n"
            '    code = "OTHER_NOT_FOUND"\n'
            "\n"
            "\n"
            "class OtherRepository(BaseRepository[CoinPackModel]):\n"
            '    """Unrelated repository."""\n'
            "\n"
            "    def __init__(self, session: AsyncSession) -> None:\n"
            '        """Initialize.\n'
            "\n"
            "        Args:\n"
            "            session (AsyncSession): The session.\n"
            '        """\n'
            "        super().__init__(\n"
            "            session,\n"
            "            model=CoinPackModel,\n"
            "            not_found_exception=OtherNotFoundException,\n"
            "        )\n"
        )
        service = (
            (tree / "services.py")
            .read_text()
            .replace(
                "        super().__init__(repository=repository)",
                "        super().__init__(repository=repository)\n"
                "        self.other: OtherRepository = OtherRepository(None)",
            )
        )
        (tree / "services.py").write_text(
            service.replace(
                "from src.db.repositories import CoinPackRepository",
                "from src.db.other import OtherRepository\n"
                "from src.db.repositories import CoinPackRepository",
            )
        )

        assert "OtherNotFoundException" not in _undocumented(tree, "delete_coin_pack")

    def test_an_unconfigured_repository_reaches_nothing(self, tmp_path: Path) -> None:
        """Without a configured class there is nothing to attribute."""
        src = tmp_path / "src"
        (src / "core").mkdir(parents=True)
        (src / "api" / "routers").mkdir(parents=True)
        (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
        (src / "repository.py").write_text(
            '"""Data access."""\n'
            "\n"
            "from tempest_fastapi_sdk import BaseRepository\n"
            "\n"
            "\n"
            "class PlainRepository(BaseRepository):\n"
            '    """Repository configuring nothing."""\n'
        )
        (src / "api" / "routers" / "plain.py").write_text(
            '"""Routes."""\n'
            "\n"
            "from fastapi import APIRouter\n"
            "\n"
            "from src.repository import PlainRepository\n"
            "\n"
            'router = APIRouter(prefix="/api/plain")\n'
            "\n"
            "\n"
            '@router.delete("/{id}", status_code=204)\n'
            "async def drop(\n"
            "    id: str,\n"
            "    repository: PlainRepository = PlainRepository(),\n"
            ") -> None:\n"
            '    """Drop a row."""\n'
            "    await repository.delete(id)\n"
        )

        assert _undocumented(src, "drop") == []


class TestTableMatchesTheRepository:
    """``CONFIGURED_RAISERS`` must describe ``BaseRepository`` as it is."""

    @staticmethod
    def _raise_sites() -> dict[str, set[str]]:
        """Read which repository method raises which configured attribute.

        Transitive over ``self.*`` calls, because most of the raising is
        indirect: ``get_by_id`` and ``soft_delete`` never touch
        ``_raise_not_found`` themselves, they call ``self.get(...)``, which
        does. Counting only direct raises would call four correct table
        entries stale.

        Returns:
            dict[str, set[str]]: Constructor kwarg to the public method names
            that reach ``self.<kwarg>``, directly or through another method
            of the same class.
        """
        source = Path(tempest_fastapi_sdk.__file__).parent / "db" / "repository.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        direct: dict[str, set[str]] = {}
        internal: dict[str, set[str]] = {}
        for class_node in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for method in class_node.body:
                if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                raises: set[str] = set()
                calls: set[str] = set()
                for child in ast.walk(method):
                    if not isinstance(child, ast.Call):
                        continue
                    func = child.func
                    if not isinstance(func, ast.Attribute) or not _is_self_attribute(
                        func
                    ):
                        continue
                    if func.attr in CONFIGURED_RAISERS:
                        raises.add(func.attr)
                    elif func.attr == "_raise_not_found":
                        raises.add("not_found_exception")
                    else:
                        calls.add(func.attr)
                direct[method.name] = raises
                internal[method.name] = calls

        def reached(method: str, seen: frozenset[str] = frozenset()) -> set[str]:
            """Union the kwargs ``method`` reaches, following ``self`` calls.

            Args:
                method (str): The method to walk from.
                seen (frozenset[str]): Methods already visited, breaking
                    recursion.

            Returns:
                set[str]: Kwarg names reached.
            """
            if method in seen:
                return set()
            found = set(direct.get(method, set()))
            for callee in internal.get(method, set()):
                found |= reached(callee, seen | {method})
            return found

        sites: dict[str, set[str]] = {kwarg: set() for kwarg in CONFIGURED_RAISERS}
        for method in direct:
            if method.startswith("_"):
                continue
            for kwarg in reached(method):
                sites[kwarg].add(method)
        return sites

    @pytest.mark.parametrize("kwarg", sorted(CONFIGURED_RAISERS))
    def test_every_raise_site_is_listed(self, kwarg: str) -> None:
        """No method raises a configured class without the table saying so.

        A missing entry means the analyzer silently under-reports that route's
        failure, which is the whole defect this table exists to close.
        """
        actual = self._raise_sites()[kwarg]

        assert actual <= CONFIGURED_RAISERS[kwarg], (
            f"{kwarg} is raised by {sorted(actual - CONFIGURED_RAISERS[kwarg])}, "
            f"which CONFIGURED_RAISERS does not list"
        )

    @pytest.mark.parametrize("kwarg", sorted(CONFIGURED_RAISERS))
    def test_no_entry_is_stale(self, kwarg: str) -> None:
        """No listed method has stopped raising that class."""
        actual = self._raise_sites()[kwarg]

        assert CONFIGURED_RAISERS[kwarg] <= actual, (
            f"CONFIGURED_RAISERS lists "
            f"{sorted(CONFIGURED_RAISERS[kwarg] - actual)} for {kwarg}, "
            f"which no longer raise it"
        )

    def test_the_conflict_fallback_is_not_a_key(self) -> None:
        """The blanket kwarg is expanded, never mapped to methods directly."""
        assert CONFLICT_FALLBACK_KWARG not in CONFIGURED_RAISERS


def _is_self_attribute(node: ast.Attribute) -> bool:
    """Return whether an attribute access is rooted at ``self``.

    Args:
        node (ast.Attribute): The attribute expression.

    Returns:
        bool: True for ``self.x``, False otherwise.
    """
    return isinstance(node.value, ast.Name) and node.value.id == "self"
