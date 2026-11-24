"""The router rules: one router per file, ``/api`` set once (#616), and
``TempestAPIRouter`` + ``EndpointDoc`` in every domain router (#825, #827).

``RTR004`` is the transport service's 22 ``include_router`` calls that
each repeated ``prefix="/api"``. ``RTR005`` and ``RTR006`` are the shape
agents kept writing while the scaffold already showed the other one: a
plain ``fastapi.APIRouter`` (where ``@raises`` is inert) and loose
``SUMMARY_*`` constants passed as ``summary=``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tempest_fastapi_sdk.cli.layer_lint import RULES, LayerFinding, lint_layers

ROUTER_RULES: frozenset[str] = frozenset({"RTR003", "RTR004"})
"""The rules of #616."""

DOMAIN_ROUTER_RULES: frozenset[str] = frozenset({"RTR005", "RTR006"})
"""The rules of #825 and #827."""


def write_source(root: Path, relative: str, source: str) -> Path:
    """Write a dedented source file under ``root``.

    Args:
        root (Path): The project root.
        relative (str): Path of the file below ``root``.
        source (str): The file content.

    Returns:
        Path: The written file.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def router_codes(
    target: Path, rules: frozenset[str] = ROUTER_RULES
) -> list[tuple[str, int, str, str | None]]:
    """Run the router rules and reduce the findings.

    Args:
        target (Path): The check target.
        rules (frozenset[str]): The rule codes to run.

    Returns:
        list[tuple[str, int, str, str | None]]: ``(file, line, code,
        subject)`` per finding.
    """
    findings: list[LayerFinding] = lint_layers(target, rules=rules)
    return [(item.path.name, item.line, item.code, item.subject) for item in findings]


class TestSeverity:
    def test_the_new_rules_enter_as_warnings(self) -> None:
        rules = ROUTER_RULES | DOMAIN_ROUTER_RULES
        assert {RULES[code].severity for code in rules} == {"warning"}


class TestOneRouterPerModule:
    def test_every_router_after_the_first_is_reported(self, tmp_path: Path) -> None:
        write_source(
            tmp_path,
            "src/api/routers/orders.py",
            """
            from fastapi import APIRouter

            router: APIRouter = APIRouter(prefix="/orders")
            admin_router = APIRouter(prefix="/admin/orders")


            def build() -> APIRouter:
                return APIRouter()
            """,
        )
        assert router_codes(tmp_path) == [
            ("orders.py", 4, "RTR003", "admin_router"),
            ("orders.py", 8, "RTR003", None),
        ]

    def test_a_router_outside_routers_is_not_counted(self, tmp_path: Path) -> None:
        write_source(
            tmp_path,
            "src/api/app.py",
            "from fastapi import APIRouter\n\na = APIRouter()\nb = APIRouter()\n",
        )
        assert router_codes(tmp_path) == []


class TestApiPrefixSetOnce:
    def test_the_transport_include_router_calls(self, tmp_path: Path) -> None:
        """Cenário: cada domínio repete ``/api`` no prefixo ou no include.

        Esperado: um ``RTR004`` por chamada, com a função como subject.
        """
        write_source(
            tmp_path,
            "src/api/routers/orders.py",
            """
            from fastapi import APIRouter

            router = APIRouter(prefix="/api/orders")
            """,
        )
        write_source(
            tmp_path,
            "src/api/app.py",
            """
            from fastapi import FastAPI


            def create_app(router: object) -> FastAPI:
                app = FastAPI()
                app.include_router(router, prefix="/api/")
                app.include_router(router, prefix="/apiv2")
                app.include_router(router, prefix="/health")
                return app
            """,
        )
        assert router_codes(tmp_path) == [
            ("app.py", 6, "RTR004", "create_app"),
            ("orders.py", 3, "RTR004", None),
        ]

    def test_build_api_router_may_set_it(self, tmp_path: Path) -> None:
        write_source(
            tmp_path,
            "src/api/routers/api.py",
            """
            from fastapi import APIRouter

            from src.api.routers.orders import router as orders_router


            def build_api_router() -> APIRouter:
                api = APIRouter(prefix="/api")
                api.include_router(orders_router)
                return api
            """,
        )
        assert router_codes(tmp_path) == []


class TestEveryRouterClassCounts:
    def test_tempest_api_router_and_a_local_subclass_count(
        self, tmp_path: Path
    ) -> None:
        """Cenário: o módulo constrói ``TempestAPIRouter`` e uma subclasse local.

        Esperado: ``RTR003`` conta as duas classes, e ``RTR004`` lê o
        prefixo da subclasse — antes do #825 só ``APIRouter`` contava.
        """
        write_source(
            tmp_path,
            "src/api/routers/base.py",
            """
            from tempest_fastapi_sdk import TempestAPIRouter


            class VersionedRouter(TempestAPIRouter):
                pass
            """,
        )
        write_source(
            tmp_path,
            "src/api/routers/orders.py",
            """
            from tempest_fastapi_sdk import TempestAPIRouter

            from src.api.routers.base import VersionedRouter

            router = TempestAPIRouter(prefix="/orders")
            admin_router = VersionedRouter(prefix="/api/admin/orders")
            """,
        )
        assert router_codes(tmp_path) == [
            ("orders.py", 6, "RTR003", "admin_router"),
            ("orders.py", 6, "RTR004", None),
        ]


class TestDomainRouterIsTempest:
    def test_the_plain_router_the_scaffold_shipped(self, tmp_path: Path) -> None:
        """Cenário: o ``service_info.py`` que o ``tempest new`` gerava até o #825.

        Esperado: um ``RTR005`` na construção do ``APIRouter``, com o nome
        atribuído como subject.
        """
        write_source(
            tmp_path,
            "src/api/routers/service_info.py",
            """
            from fastapi import APIRouter, Depends

            from src.api.docs import service_info

            router: APIRouter = APIRouter(tags=["root"])


            @router.get("/", **service_info.INDEX)
            async def index() -> dict[str, str]:
                return {}
            """,
        )
        assert router_codes(tmp_path, DOMAIN_ROUTER_RULES) == [
            ("service_info.py", 5, "RTR005", "router"),
        ]

    def test_fastapi_attribute_form_is_reported(self, tmp_path: Path) -> None:
        """Cenário: ``fastapi.APIRouter(...)`` pelo módulo, sem import do nome.

        Esperado: o mesmo ``RTR005``.
        """
        write_source(
            tmp_path,
            "src/api/routers/orders.py",
            "import fastapi\n\nrouter = fastapi.APIRouter()\n",
        )
        assert router_codes(tmp_path, DOMAIN_ROUTER_RULES) == [
            ("orders.py", 3, "RTR005", "router"),
        ]

    def test_the_aggregator_and_tempest_routers_pass(self, tmp_path: Path) -> None:
        """Cenário: ``build_api_router`` agrega com ``APIRouter`` e o domínio é Tempest.

        Esperado: nenhum achado; o agregador é isento e fora de
        ``routers/`` a regra não olha.
        """
        write_source(
            tmp_path,
            "src/api/routers/api.py",
            """
            from fastapi import APIRouter


            def build_api_router() -> APIRouter:
                return APIRouter(prefix="/api")
            """,
        )
        write_source(
            tmp_path,
            "src/api/routers/orders.py",
            """
            from tempest_fastapi_sdk import TempestAPIRouter

            router = TempestAPIRouter(prefix="/orders")
            """,
        )
        write_source(
            tmp_path,
            "src/api/app.py",
            "from fastapi import APIRouter\n\nhealth = APIRouter()\n",
        )
        assert router_codes(tmp_path, DOMAIN_ROUTER_RULES) == []


class TestRouteTextLivesInApiDocs:
    def test_the_summary_constants_agents_wrote(self, tmp_path: Path) -> None:
        """Cenário: constantes ``SUMMARY_*`` no topo do router e um literal.

        Esperado: um ``RTR006`` por palavra-chave, na linha do valor, com
        o handler como subject; o espalhamento de ``EndpointDoc`` passa.
        """
        write_source(
            tmp_path,
            "src/api/routers/addresses.py",
            """
            from tempest_fastapi_sdk import TempestAPIRouter

            from src.api.docs import addresses as docs

            SUMMARY_LIST_ADDRESSES: str = "Lista endereços de entrega do cliente"

            router = TempestAPIRouter(prefix="/addresses")


            @router.get("", summary=SUMMARY_LIST_ADDRESSES)
            async def list_addresses() -> list[str]:
                return []


            @router.post(
                "",
                summary="Cadastra novo endereço",
                description="Cria o endereço.",
            )
            async def create_address() -> str:
                return ""


            @router.delete("/{address_id}", **docs.DELETE_ADDRESS)
            async def delete_address(address_id: int) -> None:
                return None
            """,
        )
        assert router_codes(tmp_path, DOMAIN_ROUTER_RULES) == [
            ("addresses.py", 10, "RTR006", "list_addresses"),
            ("addresses.py", 17, "RTR006", "create_address"),
            ("addresses.py", 18, "RTR006", "create_address"),
        ]
