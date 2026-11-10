"""``openapi-errors`` follows FastAPI dependencies, not only the handler body.

Before #718 the analyzer walked the body of each handler and nothing else.
A ``dependencies=[Depends(require_api_key)]`` on the router, or a provider in
``api/dependencies/`` answering ``404`` while a capability is off, raised
before the handler ran, and the report still said every route matched its
flow. Each class here builds the shape that shipped invisible and asserts
the exception now shows up as undocumented — and, for router-level
``responses=``, that a declaration there is honoured.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from tempest_fastapi_sdk.api.dependencies import auth as sdk_auth
from tempest_fastapi_sdk.cli.openapi_depends import SDK_DEPENDENCY_FACTORIES
from tempest_fastapi_sdk.cli.openapi_errors import RouteFinding, analyze_paths

EXCEPTIONS: str = '''"""Domain exceptions."""

from tempest_fastapi_sdk import NotFoundException, UnauthorizedException


class ItemsDisabledException(NotFoundException):
    """The items capability is off."""

    code = "ITEMS_DISABLED"


class ApiKeyMissingException(UnauthorizedException):
    """No API key was sent."""

    code = "API_KEY_MISSING"
'''

GATES: str = '''"""Dependencies that refuse."""

from fastapi import Depends, Header

from src.core.exceptions import ApiKeyMissingException, ItemsDisabledException


def require_api_key(x_api_key: str | None = Header(None)) -> None:
    """Refuse a request without an API key."""
    if x_api_key is None:
        raise ApiKeyMissingException()


def require_items_enabled() -> None:
    """Answer 404 while the capability is off."""
    raise ItemsDisabledException()


def get_items_settings(_: None = Depends(require_items_enabled)) -> dict[str, str]:
    """Settings for items, only while the capability is on."""
    return {}
'''


def _write(root: Path, relative: str, content: str) -> None:
    """Write ``content`` to ``root / relative``, creating parents.

    Args:
        root (Path): The project root.
        relative (str): The file path under the root.
        content (str): The file content.
    """
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _analyze(root: Path, files: dict[str, str]) -> list[RouteFinding]:
    """Write a project under ``root/src`` and analyze it.

    Args:
        root (Path): The project root.
        files (dict[str, str]): Path under ``src/`` to source.

    Returns:
        list[RouteFinding]: The drift found.
    """
    _write(root, "src/core/exceptions.py", EXCEPTIONS)
    _write(root, "src/api/dependencies/gates.py", GATES)
    for relative, content in files.items():
        _write(root, f"src/{relative}", content)
    return analyze_paths([root / "src"])


def _undocumented(findings: list[RouteFinding]) -> dict[str, list[str]]:
    """Index the undocumented names by ``METHOD path``.

    Args:
        findings (list[RouteFinding]): The drift found.

    Returns:
        dict[str, list[str]]: Route label to undocumented exception names.
    """
    return {
        f"{finding.route.method} {finding.route.path}": finding.undocumented
        for finding in findings
        if finding.undocumented
    }


class TestParameterDependencies:
    """``Depends`` in the handler's parameters."""

    def test_default_depends_is_followed(self, tmp_path: Path) -> None:
        """Cenário: ``_: dict = Depends(get_items_settings)``, que depende de um gate.

        Esperado: o 404 do gate, duas dependências abaixo, aparece na rota.
        """
        router = '''"""Items."""

from fastapi import APIRouter, Depends

from src.api.dependencies.gates import get_items_settings

router = APIRouter()


@router.get("/items")
async def list_items(settings: dict = Depends(get_items_settings)) -> list[str]:
    """List items."""
    return []
'''
        findings = _analyze(tmp_path, {"api/routers/items.py": router})
        assert _undocumented(findings) == {"GET /items": ["ItemsDisabledException"]}

    def test_annotated_depends_is_followed(self, tmp_path: Path) -> None:
        """Cenário: ``Annotated[None, Depends(require_api_key)]``.

        Esperado: o 401 do gate aparece na rota.
        """
        router = '''"""Items."""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies.gates import require_api_key

router = APIRouter()


@router.get("/items")
async def list_items(_: Annotated[None, Depends(require_api_key)]) -> list[str]:
    """List items."""
    return []
'''
        findings = _analyze(tmp_path, {"api/routers/items.py": router})
        assert _undocumented(findings) == {"GET /items": ["ApiKeyMissingException"]}

    def test_alias_from_another_module_is_followed(self, tmp_path: Path) -> None:
        """Cenário: alias ``ApiKeyDep = Annotated[...]`` usado na rota.

        Esperado: o alias resolve para o gate e o 401 aparece.
        """
        aliases = '''"""Aliases."""

from typing import Annotated

from fastapi import Depends

from src.api.dependencies.gates import require_api_key

ApiKeyDep = Annotated[None, Depends(require_api_key)]
'''
        router = '''"""Items."""

from fastapi import APIRouter

from src.api.dependencies.aliases import ApiKeyDep

router = APIRouter()


@router.get("/items")
async def list_items(_: ApiKeyDep) -> list[str]:
    """List items."""
    return []
'''
        findings = _analyze(
            tmp_path,
            {"api/dependencies/aliases.py": aliases, "api/routers/items.py": router},
        )
        assert _undocumented(findings) == {"GET /items": ["ApiKeyMissingException"]}

    def test_annotated_parameter_keeps_its_type(self, tmp_path: Path) -> None:
        """Cenário: ``controller: Annotated[ItemController, Depends(...)]``.

        Esperado: a chamada ``controller.get()`` resolve no tipo, e o 404
        do controller aparece (antes o tipo lido era ``Annotated``).
        """
        controller = '''"""Controller."""

from src.core.exceptions import ItemsDisabledException


class ItemController:
    """Items."""

    async def get(self) -> None:
        """Get."""
        raise ItemsDisabledException()


def get_item_controller() -> ItemController:
    """Build it."""
    return ItemController()
'''
        router = '''"""Items."""

from typing import Annotated

from fastapi import APIRouter, Depends

from src.controllers.items import ItemController, get_item_controller

router = APIRouter()


@router.get("/items/one")
async def get_item(
    controller: Annotated[ItemController, Depends(get_item_controller)],
) -> None:
    """Get one."""
    await controller.get()
'''
        findings = _analyze(
            tmp_path,
            {"controllers/items.py": controller, "api/routers/items.py": router},
        )
        assert _undocumented(findings) == {"GET /items/one": ["ItemsDisabledException"]}

    def test_bare_depends_uses_the_annotated_class(self, tmp_path: Path) -> None:
        """Cenário: ``params: Pager = Depends()``, cujo ``__init__`` recusa.

        Esperado: o construtor é a dependência, e o 404 dele aparece.
        """
        router = '''"""Items."""

from fastapi import APIRouter, Depends

from src.core.exceptions import ItemsDisabledException

router = APIRouter()


class Pager:
    """Paging parameters."""

    def __init__(self, page: int = 1) -> None:
        """Refuse while items are off."""
        raise ItemsDisabledException()


@router.get("/items")
async def list_items(pager: Pager = Depends()) -> list[str]:
    """List items."""
    return []
'''
        findings = _analyze(tmp_path, {"api/routers/items.py": router})
        assert _undocumented(findings) == {"GET /items": ["ItemsDisabledException"]}


class TestRouteAndRouterDependencies:
    """``dependencies=`` on the decorator, the router and ``include_router``."""

    def test_route_decorator_dependencies(self, tmp_path: Path) -> None:
        """Cenário: ``@router.get(..., dependencies=[Depends(require_items_enabled)])``.

        Esperado: o 404 aparece na rota.
        """
        router = '''"""Items."""

from fastapi import APIRouter, Depends

from src.api.dependencies.gates import require_items_enabled

router = APIRouter()


@router.get("/items", dependencies=[Depends(require_items_enabled)])
async def list_items() -> list[str]:
    """List items."""
    return []
'''
        findings = _analyze(tmp_path, {"api/routers/items.py": router})
        assert _undocumented(findings) == {"GET /items": ["ItemsDisabledException"]}

    def test_router_dependencies_reach_every_route(self, tmp_path: Path) -> None:
        """Cenário: ``APIRouter(dependencies=[Depends(require_api_key)])``, duas rotas.

        Esperado: o 401 aparece nas duas.
        """
        router = '''"""Items."""

from fastapi import APIRouter, Depends

from src.api.dependencies.gates import require_api_key

router: APIRouter = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/items")
async def list_items() -> list[str]:
    """List items."""
    return []


@router.delete("/items")
async def clear_items() -> None:
    """Clear items."""
'''
        findings = _analyze(tmp_path, {"api/routers/items.py": router})
        assert _undocumented(findings) == {
            "GET /items": ["ApiKeyMissingException"],
            "DELETE /items": ["ApiKeyMissingException"],
        }

    def test_include_router_dependencies_are_transitive(self, tmp_path: Path) -> None:
        """Cenário: o formato do ``tempest new`` — ``build_api_router`` inclui o
        router de itens por import relativo, com ``dependencies=`` no
        ``include_router``, e o ``api`` é incluído com outro gate.

        Esperado: os dois gates aparecem na rota do router de itens.
        """
        items = '''"""Items."""

from fastapi import APIRouter

router: APIRouter = APIRouter(prefix="/items")


@router.get("/")
async def list_items() -> list[str]:
    """List items."""
    return []
'''
        api = '''"""The API router."""

from fastapi import APIRouter, Depends

from src.api.dependencies.gates import require_api_key, require_items_enabled

from .items import router as items_router

api: APIRouter = APIRouter(prefix="/api")
api.include_router(items_router, dependencies=[Depends(require_items_enabled)])

root: APIRouter = APIRouter()
root.include_router(api, dependencies=[Depends(require_api_key)])
'''
        findings = _analyze(
            tmp_path, {"api/routers/items.py": items, "api/routers/api.py": api}
        )
        assert _undocumented(findings) == {
            "GET /": ["ApiKeyMissingException", "ItemsDisabledException"]
        }

    def test_include_through_package_reexport(self, tmp_path: Path) -> None:
        """Cenário: ``from src.api.routers import items_router``, re-exportado
        pelo ``__init__`` do pacote.

        Esperado: o re-export é seguido até o router e o 401 aparece.
        """
        items = '''"""Items."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/items")
async def list_items() -> list[str]:
    """List items."""
    return []
'''
        package = '''"""Routers."""

from .items import router as items_router

__all__: list[str] = ["items_router"]
'''
        app = '''"""App."""

from fastapi import Depends, FastAPI

from src.api.dependencies.gates import require_api_key
from src.api.routers import items_router

app = FastAPI()
app.include_router(items_router, dependencies=[Depends(require_api_key)])
'''
        findings = _analyze(
            tmp_path,
            {
                "api/routers/items.py": items,
                "api/routers/__init__.py": package,
                "api/app.py": app,
            },
        )
        assert _undocumented(findings) == {"GET /items": ["ApiKeyMissingException"]}

    def test_router_responses_count_as_declared(self, tmp_path: Path) -> None:
        """Cenário: o 401 do router declarado em ``APIRouter(responses=...)``,
        e uma rota que não alcança o gate da outra.

        Esperado: nenhuma deriva — a declaração do router cobre as rotas e
        não vira ``unreachable`` em nenhuma delas.
        """
        router = '''"""Items."""

from fastapi import APIRouter, Depends
from tempest_fastapi_sdk import error_responses

from src.api.dependencies.gates import require_api_key, require_items_enabled
from src.core.exceptions import ApiKeyMissingException, ItemsDisabledException

router = APIRouter(
    dependencies=[Depends(require_api_key)],
    responses=error_responses(ApiKeyMissingException),
)


@router.get(
    "/items",
    dependencies=[Depends(require_items_enabled)],
    responses=error_responses(ItemsDisabledException),
)
async def list_items() -> list[str]:
    """List items."""
    return []


@router.get("/ping")
async def ping() -> None:
    """Ping."""
'''
        findings = _analyze(tmp_path, {"api/routers/items.py": router})
        assert findings == []


class TestSdkDependencyFactories:
    """Dependencies built by the SDK's own factories."""

    def test_make_token_dependency_raises_unauthorized(self, tmp_path: Path) -> None:
        """Cenário: o ``require_token`` do ``tempest new`` no router.

        Esperado: o ``UnauthorizedException`` levantado dentro do SDK aparece.
        """
        auth = '''"""Auth."""

from tempest_fastapi_sdk import make_token_dependency

require_token = make_token_dependency("secret")
'''
        router = '''"""Items."""

from fastapi import APIRouter, Depends

from src.api.dependencies.auth import require_token

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/items")
async def list_items() -> list[str]:
    """List items."""
    return []
'''
        findings = _analyze(
            tmp_path,
            {"api/dependencies/auth.py": auth, "api/routers/items.py": router},
        )
        assert _undocumented(findings) == {"GET /items": ["UnauthorizedException"]}

    def test_inline_factory_call(self, tmp_path: Path) -> None:
        """Cenário: ``Depends(make_role_dependency(...))`` direto na rota.

        Esperado: 401 e 403 da factory aparecem.
        """
        router = '''"""Items."""

from fastapi import APIRouter, Depends
from tempest_fastapi_sdk import make_role_dependency

router = APIRouter()


@router.get("/items", dependencies=[Depends(make_role_dependency("admin"))])
async def list_items() -> list[str]:
    """List items."""
    return []
'''
        findings = _analyze(tmp_path, {"api/routers/items.py": router})
        assert _undocumented(findings) == {
            "GET /items": ["ForbiddenException", "UnauthorizedException"]
        }

    def test_table_matches_the_sdk_source(self) -> None:
        """Cenário: cada factory da tabela, lida da fonte do SDK.

        Esperado: as exceções levantadas dentro da dependência aninhada são
        exatamente as da tabela, para a tabela não driftar.
        """
        tree = ast.parse(inspect.getsource(sdk_auth))
        factories = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in SDK_DEPENDENCY_FACTORIES
        }
        assert set(factories) == set(SDK_DEPENDENCY_FACTORIES)
        for name, factory in factories.items():
            raised: set[str] = set()
            for nested in ast.walk(factory):
                if nested is factory or not isinstance(
                    nested, ast.FunctionDef | ast.AsyncFunctionDef
                ):
                    continue
                for node in ast.walk(nested):
                    if isinstance(node, ast.Raise) and node.exc is not None:
                        exc = (
                            node.exc.func
                            if isinstance(node.exc, ast.Call)
                            else node.exc
                        )
                        if isinstance(exc, ast.Name):
                            raised.add(exc.id)
            assert raised == SDK_DEPENDENCY_FACTORIES[name], name


class TestRouterSubclasses:
    """#735: ``TempestAPIRouter`` and project subclasses are routers too."""

    TEMPEST_ROUTER: str = '''"""Items."""

from fastapi import Depends
from tempest_fastapi_sdk import TempestAPIRouter, error_responses

from src.api.dependencies.gates import require_api_key, require_items_enabled
from src.core.exceptions import ApiKeyMissingException, ItemsDisabledException

router: TempestAPIRouter = TempestAPIRouter(
    dependencies=[Depends(require_api_key)],
    responses=error_responses(RESPONSES),
)


@router.get("/items", dependencies=[Depends(require_items_enabled)])
async def list_items() -> list[str]:
    """List items."""
    return []
'''

    def test_tempest_router_responses_count_as_declared(self, tmp_path: Path) -> None:
        """Cenário: ``TempestAPIRouter(responses=...)`` cobre o 401 do router e
        o 404 que a dependency da rota levanta.

        Esperado: nenhuma deriva — o falso positivo do tempest-ai.
        """
        router = self.TEMPEST_ROUTER.replace(
            "RESPONSES", "ApiKeyMissingException, ItemsDisabledException"
        )
        assert _analyze(tmp_path, {"api/routers/items.py": router}) == []

    def test_tempest_router_dependency_is_reported_when_undeclared(
        self, tmp_path: Path
    ) -> None:
        """Cenário: o 401 sai do ``responses=`` do ``TempestAPIRouter``.

        Esperado: o ``require_api_key`` do ``dependencies=`` entra no grafo e o
        401 aparece como não documentado — o falso negativo da 0.349.0.
        """
        router = self.TEMPEST_ROUTER.replace("RESPONSES", "ItemsDisabledException")
        findings = _analyze(tmp_path, {"api/routers/items.py": router})
        assert _undocumented(findings) == {"GET /items": ["ApiKeyMissingException"]}

    def test_a_project_subclass_of_a_subclass_is_a_router(self, tmp_path: Path) -> None:
        """Cenário: ``class AppRouter(TempestAPIRouter)`` e
        ``class AdminRouter(AppRouter)``, definidos em outro módulo.

        Esperado: o ``dependencies=`` de um ``AdminRouter(...)`` alcança a rota.
        """
        base = '''"""Routers."""

from tempest_fastapi_sdk import TempestAPIRouter


class AdminRouter(AppRouter):
    """Admin routes."""


class AppRouter(TempestAPIRouter):
    """The project's router."""
'''
        router = '''"""Admin."""

from fastapi import Depends

from src.api.routers.base import AdminRouter
from src.api.dependencies.gates import require_api_key

router = AdminRouter(dependencies=[Depends(require_api_key)])


@router.get("/admin")
async def admin() -> None:
    """Admin."""
'''
        findings = _analyze(
            tmp_path,
            {"api/routers/base.py": base, "api/routers/admin.py": router},
        )
        assert _undocumented(findings) == {"GET /admin": ["ApiKeyMissingException"]}
