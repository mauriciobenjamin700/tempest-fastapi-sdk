"""Guard: no public function or method in the package returns bare ``Any``.

An explicit ``-> Any`` return tells every type checker to stop checking the
caller: attributes that do not exist, wrong types passed downstream, and
``None`` checks skipped silently all pass. Every public return must name a
real type (``TypeVar`` / ``Self`` / ``ParamSpec``, an existing type imported
under ``TYPE_CHECKING``, a named alias like ``JsonValue``, a ``Protocol``
describing the slice the SDK and caller use, or a dataclass / schema).

Read from source with ``ast`` so every module is scanned without triggering
optional extras. Generated modules (module docstring containing
``Do not edit by hand``) are exempt, as are private helpers (leading ``_``)
and non-bare container types like ``dict[str, Any]``.

Only upstream overrides whose base class declares ``-> Any`` belong in
:data:`ALLOWED_ANY_RETURNS`, each with its reason. Stale allowlist entries fail
the test so exceptions do not outlive the override that needed them.

Blind spot: return types that resolve to ``Any`` indirectly (through an
untyped third-party import or a missing annotation) are outside AST scope.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE: Path = Path(__file__).resolve().parents[1] / "tempest_fastapi_sdk"
"""The package the guard scans."""

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
"""Repository root, used for relative paths and mypy config."""

GENERATED_MARKER: str = "Do not edit by hand"
"""Phrase in the module docstring of every generated module."""

ALLOWED_ANY_RETURNS: dict[str, str] = {
    (
        "tempest_fastapi_sdk/queue/dedup.py::"
        "make_dedup_middleware._DedupMiddleware.consume_scope"
    ): (
        "override de faststream.BaseMiddleware.consume_scope, que declara -> Any "
        "upstream (faststream 0.7.5)"
    ),
    (
        "tempest_fastapi_sdk/queue/reliability.py::"
        "make_dead_letter_middleware._DeadLetterMiddleware.consume_scope"
    ): (
        "override de faststream.BaseMiddleware.consume_scope, que declara -> Any "
        "upstream (faststream 0.7.5)"
    ),
    (
        "tempest_fastapi_sdk/queue/reliability.py::"
        "QueueMetrics.middleware._MetricsMiddleware.consume_scope"
    ): (
        "override de faststream.BaseMiddleware.consume_scope, que declara -> Any "
        "upstream (faststream 0.7.5)"
    ),
    (
        "tempest_fastapi_sdk/queue/tracing.py::"
        "make_tracing_middleware._TracingMiddleware.consume_scope"
    ): (
        "override de faststream.BaseMiddleware.consume_scope, que declara -> Any "
        "upstream (faststream 0.7.5)"
    ),
}
"""Explicitly permitted ``-> Any`` returns, keyed by ``path::qualname``."""


def _is_bare_any(node: ast.expr | None) -> bool:
    """Return ``True`` when ``node`` is a bare ``Any`` return annotation.

    Args:
        node (ast.expr | None): The AST node of ``FunctionDef.returns``.

    Returns:
        bool: ``True`` for ``Any``, ``typing.Any`` (or any ``*.Any``), or a
        stringified forward reference to either.
    """
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == "Any"
    if isinstance(node, ast.Attribute):
        return node.attr == "Any"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip() in {"Any", "typing.Any"}
    return False


def _is_generated(tree: ast.Module) -> bool:
    """Whether a module is generated code.

    Args:
        tree (ast.Module): The parsed module.

    Returns:
        bool: ``True`` when its docstring carries :data:`GENERATED_MARKER`.
    """
    docstring = ast.get_docstring(tree) or ""
    return GENERATED_MARKER in docstring


class _PublicAnyVisitor(ast.NodeVisitor):
    """Collect public ``def`` / ``async def`` nodes returning bare ``Any``."""

    def __init__(self, rel_path: str) -> None:
        """Initialize the visitor for one module.

        Args:
            rel_path (str): Repository-relative POSIX path of the module.
        """
        self._rel_path: str = rel_path
        self._scope: list[str] = []
        self.findings: list[str] = []

    def _visit_fn(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Record ``node`` if its name is public and its return is ``Any``.

        Args:
            node (ast.FunctionDef | ast.AsyncFunctionDef): Function node.
        """
        if not node.name.startswith("_") and _is_bare_any(node.returns):
            qualname = ".".join([*self._scope, node.name])
            self.findings.append(f"{self._rel_path}::{qualname}")
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Track enclosing class names while walking the AST.

        Args:
            node (ast.ClassDef): Class definition node.
        """
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Inspect a synchronous function definition.

        Args:
            node (ast.FunctionDef): Function definition node.
        """
        self._visit_fn(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Inspect an asynchronous function definition.

        Args:
            node (ast.AsyncFunctionDef): Async function definition node.
        """
        self._visit_fn(node)


def find_public_any_returns(sources: dict[str, str]) -> list[str]:
    """Return every ``path::qualname`` with a public ``-> Any`` return.

    Args:
        sources (dict[str, str]): Repository-relative POSIX path to source.

    Returns:
        list[str]: Sorted identifiers of offending functions and methods.
    """
    hits: list[str] = []
    for path, text in sources.items():
        tree = ast.parse(text, filename=path)
        if _is_generated(tree):
            continue
        visitor = _PublicAnyVisitor(path)
        visitor.visit(tree)
        hits.extend(visitor.findings)
    return sorted(hits)


def _package_sources() -> dict[str, str]:
    """Read every Python source file under :data:`PACKAGE`.

    Returns:
        dict[str, str]: Repository-relative POSIX path to source text.
    """
    return {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE.rglob("*.py"))
    }


def test_no_public_any_return_outside_allowlist() -> None:
    """Every public function or method outside the allowlist has a concrete return."""
    found = set(find_public_any_returns(_package_sources()))
    unexpected = sorted(found - set(ALLOWED_ANY_RETURNS))
    assert not unexpected, (
        "Public functions/methods returning bare Any outside ALLOWED_ANY_RETURNS:\n"
        + "\n".join(f"  - {item}" for item in unexpected)
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every allowlist entry must still exist and carry a non-empty reason."""
    found = set(find_public_any_returns(_package_sources()))
    stale = sorted(set(ALLOWED_ANY_RETURNS) - found)
    assert not stale, (
        "Stale entries in ALLOWED_ANY_RETURNS (remove once resolved):\n"
        + "\n".join(f"  - {item}" for item in stale)
    )
    blank_reasons = [
        key for key, reason in ALLOWED_ANY_RETURNS.items() if not reason.strip()
    ]
    assert not blank_reasons, f"Allowlist entries missing a reason: {blank_reasons}"


def test_guard_fires_on_historical_violations() -> None:
    """Prove the guard fires on the exact shapes that shipped before #839."""
    sources: dict[str, str] = {
        "tempest_fastapi_sdk/core/enums.py": (
            "from typing import Any\n"
            "class _EnumHelpers:\n"
            "    @classmethod\n"
            "    def from_value(cls, value: Any) -> Any:\n"
            "        return value\n"
        ),
        "tempest_fastapi_sdk/agents/agent.py": (
            "import typing\n"
            "class Agent:\n"
            "    async def run_structured(self, goal: str) -> typing.Any:\n"
            "        return None\n"
        ),
        "tempest_fastapi_sdk/pdf/assets.py": (
            'def build_url_fetcher() -> "Any":\n'
            "    return None\n"
            "def _private_helper() -> Any:\n"
            "    return None\n"
            "def mapping_return() -> dict[str, Any]:\n"
            "    return {}\n"
        ),
        "tempest_fastapi_sdk/integrations/generated.py": (
            '"""Generated client. Do not edit by hand."""\n'
            "from typing import Any\n"
            "def fetch() -> Any:\n"
            "    return None\n"
        ),
    }
    assert find_public_any_returns(sources) == [
        "tempest_fastapi_sdk/agents/agent.py::Agent.run_structured",
        "tempest_fastapi_sdk/core/enums.py::_EnumHelpers.from_value",
        "tempest_fastapi_sdk/pdf/assets.py::build_url_fetcher",
    ]


def test_consumer_surfaces_infer_concrete_types() -> None:
    """Verify mypy infers concrete types on consumer surfaces via reveal_type."""
    snippet = """
from pydantic import BaseModel
from starlette.types import Scope

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.api.static import HardenedStaticFiles
from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.tasks.jobs import BaseJobModel


class ColorEnum(BaseStrEnum):
    RED = "red"
    BLUE = "blue"


class AnswerSchema(BaseModel):
    value: int


class ResizeJobModel(BaseJobModel):
    __tablename__ = "resize_jobs"


c1 = ColorEnum.from_value("red")
c2 = ColorEnum.from_value("red", default=None)
c3 = ColorEnum.from_value("red", default=0)
reveal_type(c1)
reveal_type(c2)
reveal_type(c3)


async def check_agent(agent: Agent) -> None:
    run = await agent.run_structured("answer", AnswerSchema)
    reveal_type(run)
    reveal_type(run.data)


job = ResizeJobModel.new_job("resize")
reveal_type(job)


async def check_static(scope: Scope) -> None:
    files = HardenedStaticFiles(directory=".")
    reveal_type(files)
    resp = await files.get_response("index.html", scope)
    reveal_type(resp)
"""
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "consumer_probe.py"
        probe.write_text(snippet, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                str(REPO_ROOT / "pyproject.toml"),
                "--no-error-summary",
                str(probe),
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    output = proc.stdout + proc.stderr
    assert "error:" not in output, f"Unexpected mypy error:\n{output}"
    revealed = [
        line.split('Revealed type is "', 1)[1].rsplit('"', 1)[0]
        for line in output.splitlines()
        if 'Revealed type is "' in line
    ]
    assert len(revealed) == 8, f"Expected 8 reveal_type lines, got:\n{output}"
    assert revealed[0] == "consumer_probe.ColorEnum"
    assert revealed[1] == "consumer_probe.ColorEnum | None"
    assert revealed[2] == "consumer_probe.ColorEnum | int"
    expected_run = (
        "tempest_fastapi_sdk.agents.structured.StructuredRun"
        "[consumer_probe.AnswerSchema]"
    )
    assert revealed[3] == expected_run
    assert revealed[4] == "consumer_probe.AnswerSchema | None"
    assert revealed[5] == "consumer_probe.ResizeJobModel"
    assert revealed[6] == "tempest_fastapi_sdk.api.static.HardenedStaticFiles"
    assert revealed[7] == "starlette.responses.Response"
