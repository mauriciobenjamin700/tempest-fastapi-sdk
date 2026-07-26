"""Tests for how a call with no receiver is resolved.

``f()`` and ``obj.f()`` used to be the same kind of edge, and they are not: a
bare call can never reach an instance method, while a call on an unannotated
receiver genuinely might.

Conflating them broke on the most ordinary code there is. A repository that
imports SQLAlchemy's ``update``::

    from sqlalchemy import update

    await self.session.execute(update(UserModel).where(...))

registers a call to ``update``, which then matched an unrelated
``CoinPackService.update`` — putting a coin pack's 404 on a category route. The
same happens with ``delete``, ``insert`` and ``select``, so any project using
SQLAlchemy's expression API alongside a service method of the same name was
affected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.openapi_errors import analyze_paths

EXCEPTIONS: str = '''"""Domain exceptions."""

from tempest_fastapi_sdk import ConflictException, NotFoundException


class UnrelatedException(ConflictException):
    """Belongs to a different domain entirely."""

    code = "UNRELATED"


class ExpectedException(NotFoundException):
    """The one the route really can produce."""

    code = "EXPECTED"
'''

UNRELATED_SERVICE: str = '''"""A service that happens to define ``update``."""

from src.core.exceptions import UnrelatedException


class UnrelatedService:
    """Owns a method whose name collides with SQLAlchemy's ``update``."""

    async def update(self, id: str) -> None:
        """Update something unrelated.

        Raises:
            UnrelatedException: Always.
        """
        raise UnrelatedException()
'''

REPOSITORY_WITH_BARE_CALLS: str = '''"""Data access via the expression API."""

from sqlalchemy import delete, update

from src.core.exceptions import ExpectedException
from src.db.models import Thing


class ThingRepository:
    """Calls the module-level ``update``/``delete`` helpers, not a method."""

    def __init__(self, session: object) -> None:
        """Initialize.

        Args:
            session (object): The database session.
        """
        self.session = session

    async def replace(self, id: str) -> None:
        """Swap a row's children.

        Raises:
            ExpectedException: If the new children do not exist.
        """
        await self.session.execute(delete(Thing).where(Thing.id == id))
        await self.session.execute(update(Thing).values(name="x"))
        raise ExpectedException()
'''

MODELS: str = '''"""ORM models."""

from tempest_fastapi_sdk import BaseModel


class Thing(BaseModel):
    """A thing."""

    __tablename__ = "things"
'''

ROUTER: str = '''"""Thing routes."""

from fastapi import APIRouter

from src.db.repositories import ThingRepository

router = APIRouter(prefix="/api/things")


@router.put("/{thing_id}")
async def replace_thing(
    thing_id: str,
    repository: ThingRepository = ThingRepository(None),
) -> None:
    """Replace a thing's children."""
    await repository.replace(thing_id)
'''


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Write a project whose repository bare-calls ``update`` and ``delete``.

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
    (src / "db" / "repositories.py").write_text(REPOSITORY_WITH_BARE_CALLS)
    (src / "services.py").write_text(UNRELATED_SERVICE)
    (src / "api" / "routers" / "thing.py").write_text(ROUTER)
    return src


def _undocumented(src: Path, handler: str) -> list[str]:
    """Return the undocumented exceptions reported for one handler.

    Args:
        src (Path): The tree to analyze.
        handler (str): The handler function name.

    Returns:
        list[str]: Names reported as undocumented.
    """
    for finding in analyze_paths([src]):
        if finding.function.name == handler:
            return finding.undocumented
    return []


class TestBareCallsSkipMethods:
    """``f()`` resolves to module-level functions only."""

    def test_sqlalchemy_helper_does_not_reach_a_same_named_method(
        self, tree: Path
    ) -> None:
        """The reported bug, in miniature.

        ``update(Thing)`` is SQLAlchemy's helper; it must not drag in
        ``UnrelatedService.update``'s exception.
        """
        assert "UnrelatedException" not in _undocumented(tree, "replace_thing")

    def test_the_real_exception_is_still_reported(self, tree: Path) -> None:
        """Narrowing the edge must not cost the finding that matters."""
        assert _undocumented(tree, "replace_thing") == ["ExpectedException"]

    def test_a_module_level_function_is_still_followed(self, tree: Path) -> None:
        """A bare call to a free function keeps its edge.

        Helpers are the normal use of a bare call, so restricting to
        module-level functions must not mean restricting to nothing.
        """
        (tree / "helpers.py").write_text(
            '"""Validation helpers."""\n'
            "\n"
            "from src.core.exceptions import UnrelatedException\n"
            "\n"
            "\n"
            "def ensure_allowed(id: str) -> None:\n"
            '    """Check access.\n'
            "\n"
            "    Raises:\n"
            "        UnrelatedException: If not allowed.\n"
            '    """\n'
            "    raise UnrelatedException()\n"
        )
        router = tree / "api" / "routers" / "thing.py"
        router.write_text(
            router.read_text()
            .replace(
                "from src.db.repositories import ThingRepository",
                "from src.db.repositories import ThingRepository\n"
                "from src.helpers import ensure_allowed",
            )
            .replace(
                "    await repository.replace(thing_id)",
                "    ensure_allowed(thing_id)\n    await repository.replace(thing_id)",
            )
        )

        assert "UnrelatedException" in _undocumented(tree, "replace_thing")


class TestUntypedReceiversStayBroad:
    """``obj.f()`` with an unknown receiver keeps the wide resolution."""

    def test_unannotated_receiver_still_reaches_a_method(self, tmp_path: Path) -> None:
        """Recall is preserved where the target is genuinely unknown.

        ``service`` comes from a factory with no annotation, so nothing states
        its class. Dropping this edge would hide a real exception, which is
        worse than reporting an extra one.
        """
        src = tmp_path / "src"
        (src / "core").mkdir(parents=True)
        (src / "api" / "routers").mkdir(parents=True)
        (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
        (src / "services.py").write_text(UNRELATED_SERVICE)
        (src / "api" / "routers" / "thing.py").write_text(
            '"""Thing routes."""\n'
            "\n"
            "from fastapi import APIRouter\n"
            "\n"
            "from src.services import UnrelatedService\n"
            "\n"
            'router = APIRouter(prefix="/api/things")\n'
            "\n"
            "\n"
            "def build_service():\n"
            '    """Build a service without annotating the return."""\n'
            "    return UnrelatedService()\n"
            "\n"
            "\n"
            '@router.put("/{thing_id}")\n'
            "async def touch_thing(thing_id: str) -> None:\n"
            '    """Touch a thing."""\n'
            "    service = build_service()\n"
            "    await service.update(thing_id)\n"
        )

        assert "UnrelatedException" in _undocumented(src, "touch_thing")


class TestGuardsStillResolve:
    """``@requires(...)`` guards are recorded as bare calls."""

    def test_a_guard_function_is_followed(self, tmp_path: Path) -> None:
        """A guard's exceptions stay reachable from the route it protects.

        Guards are module-level functions, so they survive the narrowing — but
        they reach the graph through the same field, which makes this worth
        pinning.
        """
        src = tmp_path / "src"
        (src / "core").mkdir(parents=True)
        (src / "api" / "routers").mkdir(parents=True)
        (src / "core" / "exceptions.py").write_text(EXCEPTIONS)
        (src / "guards.py").write_text(
            '"""Permission guards."""\n'
            "\n"
            "from src.core.exceptions import UnrelatedException\n"
            "\n"
            "\n"
            "def require_thing(user):\n"
            '    """Deny unless allowed.\n'
            "\n"
            "    Raises:\n"
            "        UnrelatedException: When denied.\n"
            '    """\n'
            "    raise UnrelatedException()\n"
        )
        (src / "api" / "routers" / "thing.py").write_text(
            '"""Thing routes."""\n'
            "\n"
            "from fastapi import APIRouter\n"
            "from tempest_fastapi_sdk import requires\n"
            "\n"
            "from src.guards import require_thing\n"
            "\n"
            'router = APIRouter(prefix="/api/things")\n'
            "\n"
            "\n"
            '@router.get("/{thing_id}")\n'
            "@requires(require_thing)\n"
            "async def read_thing(thing_id: str, user=None) -> None:\n"
            '    """Read a thing."""\n'
            "    return None\n"
        )

        assert "UnrelatedException" in _undocumented(src, "read_thing")
