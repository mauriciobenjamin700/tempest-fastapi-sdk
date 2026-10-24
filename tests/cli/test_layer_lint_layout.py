"""``LAY001``, ``LAY002`` and ``REPO002``: layer shape and repository imports (#616).

Each rule is fed the shape that shipped in the transport service the
issue measured (``models/route.py`` with five models, a repository
importing another) and the near-misses it must leave alone.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.layer_lint import (
    LAYER_DIRS,
    RULES,
    LayerFinding,
    lint_layers,
)

LAYOUT_RULES: frozenset[str] = frozenset({"LAY001", "LAY002", "REPO002"})
"""The rules this file pins."""


def _write(root: Path, relative: str, source: str = "") -> Path:
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


def _codes(target: Path) -> list[tuple[str, int, str, str | None]]:
    """Run the layout rules and reduce the findings.

    Args:
        target (Path): The project root.

    Returns:
        list[tuple[str, int, str, str | None]]: ``(file, line, code,
        subject)`` per finding.
    """
    findings: list[LayerFinding] = lint_layers(target, rules=LAYOUT_RULES)
    return [(item.path.name, item.line, item.code, item.subject) for item in findings]


class TestSeverity:
    def test_the_new_rules_enter_as_warnings(self) -> None:
        """Cenário: as regras novas chegam a serviços que já existem.

        Esperado: as três são aviso nesta release, para o projeto migrar
        sem o ``tempest check`` cair de uma vez.
        """
        assert {RULES[code].severity for code in LAYOUT_RULES} == {"warning"}


class TestContextPackage:
    @pytest.mark.parametrize("layer", LAYER_DIRS)
    def test_a_module_at_the_layer_root_is_reported(
        self, tmp_path: Path, layer: str
    ) -> None:
        _write(tmp_path, f"src/{layer}/order.py", "class Order: ...\n")
        assert _codes(tmp_path) == [("order.py", 1, "LAY002", None)]

    def test_a_module_in_a_context_package_is_clean(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/db/models/__init__.py")
        _write(tmp_path, "src/db/models/routing/__init__.py")
        _write(tmp_path, "src/db/models/routing/route.py", "class RouteModel: ...\n")
        _write(
            tmp_path,
            "src/services/billing/invoices/invoice.py",
            "class InvoiceService: ...\n",
        )
        assert _codes(tmp_path) == []


class TestOneClassPerModule:
    def test_the_transport_models_file_is_reported(self, tmp_path: Path) -> None:
        """Cenário: ``models/route.py`` com cinco models, como no transport.

        Esperado: um ``LAY001`` por classe depois da primeira, com o nome
        dela como alvo.
        """
        _write(
            tmp_path,
            "src/db/models/routing/route.py",
            """
            class RouteModel: ...
            class StopModel: ...
            class _LegModel: ...
            class ScheduleModel: ...
            class DriverModel: ...
            """,
        )
        assert _codes(tmp_path) == [
            ("route.py", 2, "LAY001", "StopModel"),
            ("route.py", 3, "LAY001", "_LegModel"),
            ("route.py", 4, "LAY001", "ScheduleModel"),
            ("route.py", 5, "LAY001", "DriverModel"),
        ]

    def test_a_module_function_is_reported(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/controllers/orders/order.py",
            """
            def build_order_controller() -> None: ...
            class OrderController: ...
            def __getattr__(name: str) -> None: ...
            """,
        )
        assert _codes(tmp_path) == [("order.py", 1, "LAY001", "build_order_controller")]

    def test_services_are_left_to_svc002(self, tmp_path: Path) -> None:
        """Cenário: duas classes num módulo de ``services/``.

        Esperado: o ``SVC002`` acusa (como erro) e o ``LAY001`` não repete.
        """
        _write(
            tmp_path,
            "src/services/news/news.py",
            "class NewsService: ...\nclass TrendService: ...\n",
        )
        assert _codes(tmp_path) == []
        assert [item.code for item in lint_layers(tmp_path)] == [
            "SVC001",
            "SVC001",
            "SVC002",
        ]


class TestRepositoryImportsRepository:
    def test_absolute_and_relative_imports_are_reported(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/db/repositories/orders/order.py",
            """
            from src.db.repositories import CustomerRepository
            from ..billing.invoice import InvoiceRepository as Invoices
            from src.db.models import OrderModel


            class OrderRepository:
                pass
            """,
        )
        assert _codes(tmp_path) == [
            ("order.py", 1, "REPO002", "CustomerRepository"),
            ("order.py", 2, "REPO002", "InvoiceRepository"),
        ]

    def test_a_project_base_class_and_the_sdk_base_are_clean(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "src/db/repositories/orders/order.py",
            """
            from tempest_fastapi_sdk import BaseRepository
            from src.db.repositories.shared.base import ProjectRepository


            class OrderRepository(ProjectRepository):
                pass
            """,
        )
        _write(
            tmp_path,
            "src/db/repositories/__init__.py",
            "from .orders.order import OrderRepository as OrderRepository\n",
        )
        assert _codes(tmp_path) == []
