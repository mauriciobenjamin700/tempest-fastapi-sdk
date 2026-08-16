"""``Field(alias=...)`` renames the parameter a type-checker sees.

Pydantic treats ``alias``, ``validation_alias`` and ``serialization_alias``
the same way at runtime once ``populate_by_name=True`` is set. A static
checker does not: ``alias`` is the field specifier that renames the
parameter in the synthesized ``__init__``, so pyright rejects

    ChargePayload(correlation_id="order-1")

with *No parameter named "correlation_id"* and demands ``correlationID``
instead. Measured with basedpyright against the published wheel — and
measured again with ``validate_by_name`` in ``model_config``, which does
not change it. mypy accepts both spellings either way, which is how this
reached consumers.

The fix is to write the wire name twice, as ``validation_alias`` (reading)
and ``serialization_alias`` (writing). This guard keeps ``alias`` from
coming back: it is one keyword, it looks equivalent, and nothing else in
the gate would notice.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk"
"""The package this guard walks."""

SKIP_MARKER: str = "alias-guard: skip"
"""Comment opting one line out, for a case that is genuinely not this."""


def _field_aliases(path: Path) -> list[str]:
    """Find ``Field(..., alias=...)`` calls in one module.

    Args:
        path (Path): The module to inspect.

    Returns:
        list[str]: One ``file:line`` entry per violation. Only calls to
        ``Field`` are considered — FastAPI's ``Query(alias=...)`` and
        ``Header(alias=...)`` name a request parameter, not a model field,
        and synthesize no ``__init__``.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.split("\n")
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name != "Field":
            continue
        for keyword in node.keywords:
            if keyword.arg != "alias":
                continue
            if SKIP_MARKER in lines[keyword.value.lineno - 1]:
                continue
            problems.append(f"{_label(path)}:{keyword.value.lineno}")
    return problems


def _label(path: Path) -> str:
    """Render a path for the failure message.

    Args:
        path (Path): The inspected file.

    Returns:
        str: A repo-relative path when possible, the absolute one otherwise
        — this guard's own tests point it at a temporary directory.
    """
    try:
        return str(path.relative_to(PACKAGE_ROOT.parent))
    except ValueError:
        return str(path)


def test_no_field_uses_the_plain_alias() -> None:
    """Every wire name is split into validation and serialization aliases."""
    problems: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        problems.extend(_field_aliases(path))
    assert not problems, (
        "`Field(alias=...)` renames the parameter a type-checker sees; use "
        "`validation_alias=` + `serialization_alias=` instead:\n  "
        + "\n  ".join(problems)
    )


def test_the_guard_fires_on_the_shape_that_shipped(tmp_path: Path) -> None:
    """A guard that cannot fail is one nobody should trust."""
    module = tmp_path / "shipped.py"
    module.write_text(
        "from pydantic import BaseModel, Field\n\n\n"
        "class Charge(BaseModel):\n"
        '    correlation_id: str = Field(alias="correlationID")\n',
        encoding="utf-8",
    )

    assert _field_aliases(module)


def test_the_split_form_passes(tmp_path: Path) -> None:
    """The replacement is not flagged."""
    module = tmp_path / "fixed.py"
    module.write_text(
        "from pydantic import BaseModel, Field\n\n\n"
        "class Charge(BaseModel):\n"
        "    correlation_id: str = Field(\n"
        '        validation_alias="correlationID",\n'
        '        serialization_alias="correlationID",\n'
        "    )\n",
        encoding="utf-8",
    )

    assert not _field_aliases(module)


@pytest.mark.parametrize("caller", ["Query", "Header", "Cookie", "Path"])
def test_request_parameter_helpers_are_left_alone(tmp_path: Path, caller: str) -> None:
    """``Query(alias=...)`` names a query string key, not a field.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
        caller (str): The FastAPI helper under test.
    """
    module = tmp_path / f"{caller.lower()}_param.py"
    module.write_text(
        f"from fastapi import {caller}\n\n\n"
        f'def read(page: int = {caller}(1, alias="pageNumber")) -> int:\n'
        "    return page\n",
        encoding="utf-8",
    )

    assert not _field_aliases(module)
