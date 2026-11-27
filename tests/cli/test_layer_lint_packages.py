"""``INIT001``, ``SIZE001``, ``LAY003``: what a package holds, how big (#823, #824).

Each rule is fed the shape that shipped — the ``queue/__init__.py`` that
``tempest generate --src`` wrote up to 0.371.0 (a broker built and a
function defined in the package module), an ``admin/__init__.py``
composing sub-routers, an ``order_service.py`` — and the near-misses it
must leave alone: a re-export, ``__all__``, ``if TYPE_CHECKING:``, an
optional import, a module of exactly 1000 lines.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.layer_lint import (
    LAYER_FILE_SUFFIXES,
    MAX_MODULE_LINES,
    RULES,
    LayerFinding,
    lint_layers,
)

PACKAGE_RULES: frozenset[str] = frozenset({"INIT001", "SIZE001", "LAY003"})
"""The rules this file pins."""

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
"""The repository root, where the documents listing the rules live."""

SHIPPED_QUEUE_INIT: str = '''
"""FastStream (RabbitMQ) message-queue wiring for this service."""

from __future__ import annotations

import os

from faststream.rabbit import RabbitBroker
from tempest_fastapi_sdk.queue import AsyncQueueManager

RABBITMQ_URL: str = os.environ.get(
    "RABBITMQ_URL",
    "amqp://guest:guest@localhost:5672/",
)
"""AMQP URL the broker connects to (override via the ``.env`` file)."""

broker: RabbitBroker = RabbitBroker(RABBITMQ_URL)
"""Process-wide FastStream broker; subscribers register against it."""

broker_manager: AsyncQueueManager = AsyncQueueManager(broker)
"""SDK manager wrapping :data:`broker` (connect/disconnect/publish)."""


def get_broker() -> AsyncQueueManager:
    """Return the process-wide FastStream broker manager."""
    return broker_manager


__all__: list[str] = ["broker", "broker_manager", "get_broker"]
'''
"""``src/queue/__init__.py`` as ``tempest generate --src`` wrote it up to 0.371.0."""


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
    """Run the package rules and reduce the findings.

    Args:
        target (Path): The project root.

    Returns:
        list[tuple[str, int, str, str | None]]: ``(file, line, code,
        subject)`` per finding.
    """
    findings: list[LayerFinding] = lint_layers(target, rules=PACKAGE_RULES)
    return [(item.path.name, item.line, item.code, item.subject) for item in findings]


class TestSeverity:
    def test_the_new_rules_enter_as_warnings(self) -> None:
        """Cenário: as regras novas chegam a serviços que já existem.

        Esperado: as três são aviso, para o projeto migrar sem o
        ``tempest check`` cair de uma vez (o ``layers_strict`` do scaffold
        já as torna falha num projeto novo).
        """
        assert {RULES[code].severity for code in PACKAGE_RULES} == {"warning"}


class TestInitOnlyReexports:
    def test_the_shipped_queue_init_is_reported(self, tmp_path: Path) -> None:
        """Cenário: o ``queue/__init__.py`` que o gerador escrevia até a 0.371.0.

        Esperado: um ``INIT001`` por statement que faz trabalho — a
        constante lida do ambiente, as duas instâncias e a função —, com o
        nome da função como alvo; imports, docstrings e ``__all__`` passam.
        """
        _write(tmp_path, "src/queue/__init__.py", SHIPPED_QUEUE_INIT)
        assert _codes(tmp_path) == [
            ("__init__.py", 10, "INIT001", None),
            ("__init__.py", 16, "INIT001", None),
            ("__init__.py", 19, "INIT001", None),
            ("__init__.py", 23, "INIT001", "get_broker"),
        ]

    def test_router_composition_in_init_is_reported(self, tmp_path: Path) -> None:
        """Cenário: um ``admin/__init__.py`` que compõe os sub-routers.

        Esperado: o ``APIRouter(...)`` e cada ``include_router`` acusam; a
        composição vai para ``admin/router.py``.
        """
        _write(
            tmp_path,
            "src/api/routers/admin/__init__.py",
            """
            from fastapi import APIRouter

            from .orders import router as orders_router

            admin_router: APIRouter = APIRouter(prefix="/admin")
            admin_router.include_router(orders_router)
            """,
        )
        assert _codes(tmp_path) == [
            ("__init__.py", 5, "INIT001", None),
            ("__init__.py", 6, "INIT001", None),
        ]

    def test_a_class_in_init_is_reported_with_its_name(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/integrations/payment/__init__.py", "class Gateway: ...\n")
        assert _codes(tmp_path) == [("__init__.py", 1, "INIT001", "Gateway")]

    def test_a_reexporting_init_is_clean(self, tmp_path: Path) -> None:
        """Cenário: o ``__init__.py`` na forma que a regra pede.

        Esperado: docstring, ``from __future__``, re-export relativo,
        ``if TYPE_CHECKING:`` com import, import opcional em ``try``,
        ``__all__`` anotado e estendido com ``+=`` — nenhum achado.
        """
        _write(
            tmp_path,
            "src/queue/__init__.py",
            '''
            """The queue package."""

            from __future__ import annotations

            from typing import TYPE_CHECKING

            from .broker import broker as broker

            if TYPE_CHECKING:
                from .broker import RabbitBroker as RabbitBroker

            try:
                from .extras import fast as fast
            except ImportError:
                pass

            __all__: list[str] = ["broker"]
            __all__ += ["fast"]
            ''',
        )
        _write(tmp_path, "tests/unit/__init__.py")
        assert _codes(tmp_path) == []

    def test_a_branch_that_does_work_is_reported(self, tmp_path: Path) -> None:
        """Cenário: um ``if`` no ``__init__.py`` que define uma função.

        Esperado: o ``if`` inteiro acusa — só o ramo feito de import passa.
        """
        _write(
            tmp_path,
            "src/core/__init__.py",
            """
            from typing import TYPE_CHECKING

            if not TYPE_CHECKING:

                def __getattr__(name: str) -> object: ...
            """,
        )
        assert _codes(tmp_path) == [("__init__.py", 3, "INIT001", None)]

    def test_the_suite_packages_are_read_too(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/__init__.py")
        _write(tmp_path, "tests/unit/__init__.py", "VALUE: int = 1\n")
        assert _codes(tmp_path) == [("__init__.py", 1, "INIT001", None)]


class TestModuleSize:
    def test_a_module_over_the_limit_is_reported(self, tmp_path: Path) -> None:
        """Cenário: um service e um arquivo de teste com 1001 linhas.

        Esperado: um ``SIZE001`` para cada, na primeira linha além do
        teto, com a contagem na mensagem.
        """
        body = "VALUE: int = 1\n" * (MAX_MODULE_LINES + 1)
        _write(tmp_path, "src/services/orders/checkout.py", body)
        _write(tmp_path, "tests/unit/orders/test_checkout.py", body)
        findings = lint_layers(tmp_path, rules={"SIZE001"})
        assert [(item.path.name, item.line) for item in findings] == [
            ("checkout.py", MAX_MODULE_LINES + 1),
            ("test_checkout.py", MAX_MODULE_LINES + 1),
        ]
        assert "1001 lines" in findings[0].message

    def test_a_module_at_the_limit_is_clean(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/services/orders/checkout.py",
            "VALUE: int = 1\n" * MAX_MODULE_LINES,
        )
        assert _codes(tmp_path) == []

    def test_the_limit_is_a_thousand_lines(self) -> None:
        """Cenário: o teto que a regra global do autor fixa.

        Esperado: 1000, não 500 — código e teste, sem lista de exceção.
        """
        assert MAX_MODULE_LINES == 1000


class TestLayerFileName:
    @pytest.mark.parametrize(("layer", "suffix"), sorted(LAYER_FILE_SUFFIXES.items()))
    def test_a_file_named_after_its_layer_is_reported(
        self, tmp_path: Path, layer: str, suffix: str
    ) -> None:
        _write(tmp_path, f"src/{layer}/orders/checkout{suffix}.py", "class X: ...\n")
        assert _codes(tmp_path) == [(f"checkout{suffix}.py", 1, "LAY003", None)]

    def test_the_message_names_the_file_to_rename_to(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/services/orders/checkout_service.py")
        (finding,) = lint_layers(tmp_path, rules={"LAY003"})
        assert "rename it to checkout.py" in finding.message

    def test_a_file_without_the_suffix_is_clean(self, tmp_path: Path) -> None:
        """Cenário: os nomes que o scaffold usa, e um que só contém a palavra.

        Esperado: ``checkout.py``, ``service_info.py`` (a palavra no
        início, não como sufixo) e ``service.py`` passam.
        """
        _write(tmp_path, "src/services/orders/checkout.py")
        _write(tmp_path, "src/controllers/system/service_info.py")
        _write(tmp_path, "src/services/system/service.py")
        assert _codes(tmp_path) == []


class TestRuleTables:
    @pytest.mark.parametrize(
        "relative",
        [
            "docs/recipes/cli/lint-layers.md",
            "docs/recipes/cli/lint-layers.en.md",
            "tempest_fastapi_sdk/cli/_templates/CLAUDE.md.tmpl",
        ],
    )
    def test_every_rule_is_listed(self, relative: str) -> None:
        """Cenário: as tabelas que dizem o que o ``tempest check`` cobra.

        Esperado: todo código de :data:`RULES` aparece na tabela da
        receita do CLI (nas duas línguas) e na do ``CLAUDE.md`` gerado —
        no gerado, menos ``EXC001``/``EXC002``, que falam do arquivo de
        exceções e não do código do serviço. Uma regra nova sem linha falha
        aqui.
        """
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        listed = set(re.findall(r"^\| `([A-Z]+[0-9]{3})` \|", text, re.MULTILINE))
        expected = set(RULES)
        if relative.endswith(".tmpl"):
            expected -= {"EXC001", "EXC002"}
        assert expected - listed == set()
