"""``IMP001`` and ``IMP002``: how a project imports its own packages (#616).

``IMP001`` is the transport service's eight ``__init__.py`` files with
absolute imports; ``IMP002`` reads re-exports with ``ast`` only, so the
project under test is written to disk and never imported.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tempest_fastapi_sdk.cli.layer_lint import RULES, LayerFinding, lint_layers

IMPORT_RULES: frozenset[str] = frozenset({"IMP001", "IMP002"})
"""The rules this file pins."""


def write_source(root: Path, relative: str, source: str = "") -> Path:
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


def import_codes(target: Path) -> list[tuple[str, int, str, str | None]]:
    """Run the import rules and reduce the findings.

    Args:
        target (Path): The check target.

    Returns:
        list[tuple[str, int, str, str | None]]: ``(path below src or
        tests, line, code, subject)`` per finding.
    """
    findings: list[LayerFinding] = lint_layers(target, rules=IMPORT_RULES)
    return [
        (
            item.path.relative_to(target).as_posix(),
            item.line,
            item.code,
            item.subject,
        )
        for item in findings
    ]


def write_services(root: Path, *, star: bool = False) -> None:
    """Write ``src.services`` re-exporting ``OrderService`` two levels down.

    Args:
        root (Path): The project root.
        star (bool): Whether the layer package re-exports with
            ``from .orders import *`` instead of naming the class.
    """
    write_source(root, "src/__init__.py")
    write_source(
        root,
        "src/services/__init__.py",
        "from .orders import *\n"
        if star
        else "from .orders import OrderService as OrderService\n",
    )
    write_source(
        root,
        "src/services/orders/__init__.py",
        "from .order import OrderService as OrderService\n",
    )
    write_source(root, "src/services/orders/order.py", "class OrderService: ...\n")


class TestSeverity:
    def test_the_new_rules_enter_as_warnings(self) -> None:
        assert {RULES[code].severity for code in IMPORT_RULES} == {"warning"}


class TestRelativeInit:
    def test_an_absolute_neighbour_import_is_reported(self, tmp_path: Path) -> None:
        write_source(
            tmp_path,
            "src/services/__init__.py",
            """
            from tempest_fastapi_sdk import BaseService
            from src.schemas import OrderSchema
            from src.services.orders.order import OrderService as OrderService
            from .billing import InvoiceService as InvoiceService
            """,
        )
        assert import_codes(tmp_path) == [
            ("src/services/__init__.py", 3, "IMP001", "src.services.orders.order")
        ]


class TestReExportedName:
    def test_a_submodule_import_names_the_top_package(self, tmp_path: Path) -> None:
        """Cenário: um controller importa o service pelo módulo onde ele mora.

        Esperado: o ``IMP002`` aponta ``src.services``, o pacote da camada,
        que reexporta o nome em dois níveis.
        """
        write_services(tmp_path)
        write_source(
            tmp_path,
            "src/controllers/order.py",
            "from src.services.orders.order import OrderService\n",
        )
        assert import_codes(tmp_path) == [
            ("src/controllers/order.py", 1, "IMP002", "OrderService")
        ]

    def test_a_star_reexport_is_followed(self, tmp_path: Path) -> None:
        write_services(tmp_path, star=True)
        write_source(
            tmp_path,
            "tests/test_order.py",
            "from src.services.orders.order import OrderService\n",
        )
        findings = lint_layers(tmp_path, rules=IMPORT_RULES)
        assert [(item.code, item.subject) for item in findings] == [
            ("IMP002", "OrderService")
        ]
        assert "`from src.services import OrderService`" in findings[0].message

    def test_inside_the_layer_the_nearest_outside_package_is_named(
        self, tmp_path: Path
    ) -> None:
        """Cenário: um service de outro contexto importa o ``OrderService``.

        Esperado: a sugestão é ``src.services.orders``, nunca o próprio
        ``src.services`` em que o módulo vive, que fecharia ciclo no
        ``__init__``.
        """
        write_services(tmp_path)
        write_source(
            tmp_path,
            "src/services/billing/invoice.py",
            "from src.services.orders.order import OrderService\n",
        )
        findings = lint_layers(tmp_path, rules=IMPORT_RULES)
        assert len(findings) == 1
        assert "`from src.services.orders import OrderService`" in (findings[0].message)

    def test_a_name_no_package_reexports_and_the_package_import_are_clean(
        self, tmp_path: Path
    ) -> None:
        write_services(tmp_path)
        write_source(tmp_path, "src/services/orders/rules.py", "MAX_ITEMS: int = 10\n")
        write_source(
            tmp_path,
            "src/controllers/order.py",
            """
            from src.services import OrderService
            from src.services.orders.rules import MAX_ITEMS
            from tempest_fastapi_sdk.cli.layer_lint import RULES
            """,
        )
        assert import_codes(tmp_path) == []

    def test_a_star_import_cycle_ends(self, tmp_path: Path) -> None:
        write_source(tmp_path, "src/a/__init__.py", "from src.b import *\n")
        write_source(tmp_path, "src/b/__init__.py", "from src.a import *\n")
        write_source(tmp_path, "src/a/x.py", "class X: ...\n")
        write_source(tmp_path, "src/c.py", "from src.a.x import X\n")
        assert import_codes(tmp_path) == []
