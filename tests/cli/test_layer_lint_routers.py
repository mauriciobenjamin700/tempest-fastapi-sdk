"""``RTR003`` and ``RTR004``: one router per file, ``/api`` set once (#616).

``RTR004`` is the transport service's 22 ``include_router`` calls that
each repeated ``prefix="/api"``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tempest_fastapi_sdk.cli.layer_lint import RULES, LayerFinding, lint_layers

ROUTER_RULES: frozenset[str] = frozenset({"RTR003", "RTR004"})
"""The rules this file pins."""


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


def router_codes(target: Path) -> list[tuple[str, int, str, str | None]]:
    """Run the router rules and reduce the findings.

    Args:
        target (Path): The check target.

    Returns:
        list[tuple[str, int, str, str | None]]: ``(file, line, code,
        subject)`` per finding.
    """
    findings: list[LayerFinding] = lint_layers(target, rules=ROUTER_RULES)
    return [(item.path.name, item.line, item.code, item.subject) for item in findings]


class TestSeverity:
    def test_the_new_rules_enter_as_warnings(self) -> None:
        assert {RULES[code].severity for code in ROUTER_RULES} == {"warning"}


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
