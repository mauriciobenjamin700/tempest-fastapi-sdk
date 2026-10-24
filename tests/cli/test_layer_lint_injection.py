"""``DI001`` to ``DI003``: what services and controllers receive (#616).

The shapes are the ones the transport service measured: services taking
the ``AsyncSession`` (14 of them), a service taking another service
(``SettlementService`` with ``WalletService``) and ``from_session`` on
every class (about 400 call sites).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tempest_fastapi_sdk.cli.layer_lint import RULES, LayerFinding, lint_layers

INJECTION_RULES: frozenset[str] = frozenset({"DI001", "DI002", "DI003"})
"""The rules this file pins."""


def _write(root: Path, relative: str, source: str) -> Path:
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
    """Run the injection rules and reduce the findings.

    Args:
        target (Path): The project root.

    Returns:
        list[tuple[str, int, str, str | None]]: ``(file, line, code,
        subject)`` per finding.
    """
    findings: list[LayerFinding] = lint_layers(target, rules=INJECTION_RULES)
    return [(item.path.name, item.line, item.code, item.subject) for item in findings]


class TestSeverity:
    def test_the_new_rules_enter_as_warnings(self) -> None:
        assert {RULES[code].severity for code in INJECTION_RULES} == {"warning"}


class TestSessionInjected:
    def test_a_service_and_a_controller_taking_the_session(
        self, tmp_path: Path
    ) -> None:
        """Cenário: service e controller recebendo ``AsyncSession`` no ``__init__``.

        Esperado: um ``DI001`` por parâmetro, inclusive dentro de
        ``Optional``/união e em anotação string.
        """
        _write(
            tmp_path,
            "src/services/billing/invoice.py",
            """
            from sqlalchemy.ext.asyncio import AsyncSession


            class InvoiceService:
                def __init__(self, session: AsyncSession | None) -> None:
                    self.session = session
            """,
        )
        _write(
            tmp_path,
            "src/controllers/billing/invoice.py",
            """
            class InvoiceController:
                def __init__(self, *, db: "AsyncSession") -> None:
                    self.db = db
            """,
        )
        assert _codes(tmp_path) == [
            ("invoice.py", 2, "DI001", "InvoiceController.db"),
            ("invoice.py", 5, "DI001", "InvoiceService.session"),
        ]

    def test_a_repository_taking_the_session_is_clean(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/db/repositories/billing/invoice.py",
            """
            from sqlalchemy.ext.asyncio import AsyncSession


            class InvoiceRepository:
                def __init__(self, session: AsyncSession) -> None:
                    self.session = session
            """,
        )
        assert _codes(tmp_path) == []


class TestSameLayerCollaborator:
    def test_the_transport_settlement_service(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/wallet/settlement.py",
            """
            class SettlementService:
                def __init__(
                    self,
                    repository: "SettlementRepository",
                    wallet_service: "WalletService",
                ) -> None:
                    self.wallet_service = wallet_service
            """,
        )
        assert _codes(tmp_path) == [
            ("settlement.py", 5, "DI002", "SettlementService.wallet_service")
        ]

    def test_a_controller_taking_services_is_clean_and_another_is_not(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "src/controllers/orders/order.py",
            """
            class OrderController:
                def __init__(
                    self,
                    order_service: "OrderService",
                    audit: "AuditController",
                ) -> None:
                    self.order_service = order_service
            """,
        )
        assert _codes(tmp_path) == [("order.py", 5, "DI002", "OrderController.audit")]

    def test_a_declared_exception_keeps_the_settlement_case(
        self, tmp_path: Path
    ) -> None:
        """Cenário: o caso real do transport, declarado com motivo.

        Esperado: a exceção desculpa só aquele parâmetro.
        """
        _write(
            tmp_path,
            "src/services/wallet/settle.py",
            """
            class SettlementService:
                def __init__(self, wallet: "WalletService", fx: "FxService") -> None:
                    self.wallet = wallet
            """,
        )
        _write(
            tmp_path,
            "tests/architecture_exceptions.py",
            """
            ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
                "DI002 src/services/wallet/settle.py::SettlementService.wallet": (
                    "mesma transação do crédito"
                ),
            }
            """,
        )
        assert _codes(tmp_path) == [("settle.py", 2, "DI002", "SettlementService.fx")]


class TestSelfAssembly:
    def test_every_from_session_is_reported(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/catalog/catalog.py",
            """
            class CatalogService:
                @classmethod
                def from_session(cls, session: object) -> "CatalogService":
                    return cls()
            """,
        )
        _write(
            tmp_path,
            "src/utils/assembly.py",
            """
            async def from_session(session: object) -> None:
                return None
            """,
        )
        assert _codes(tmp_path) == [
            ("catalog.py", 3, "DI003", "CatalogService.from_session"),
            ("assembly.py", 1, "DI003", "from_session"),
        ]

    def test_a_build_function_is_clean(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/scripts/composition.py",
            """
            def build_catalog_service(session: object) -> object:
                return session
            """,
        )
        assert _codes(tmp_path) == []
