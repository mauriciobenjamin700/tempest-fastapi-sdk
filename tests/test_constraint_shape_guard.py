"""A numeric bound on a string field raises at construction, not at validation.

``Field(le=140)`` on a ``str`` is not a strict rule rejecting bad input — it
is a shape pydantic cannot apply at all:

    ChargeRefundPayload(correlation_id="r-1", value=100, comment="obrigado")
    TypeError: Unable to apply constraint 'le' to supplied value obrigado

That reached consumers in the generated OpenPix client, on both ``comment``
fields of the refund payloads, because the spec writes ``maximum: 140``
under a description that reads "Maximum length of 140 characters" and the
generator passed it through literally. Every refund carrying a comment
failed before leaving the process.

The generator now re-reads a numeric bound on a ``type: string`` schema as a
length bound (``tempest_fastapi_sdk/openapi/parse.py``), and
``tests/openapi/test_parse.py`` pins that mapping. This guard covers the
other half of the same defect: the **emitted** models, whoever wrote them.
A regenerated package, a hand-written schema, or a spec that spells the
mismatch some new way all land here.

The reverse pair is checked too — ``max_length`` on an ``int`` is the same
category of nonsense, and pydantic answers it the same way.

Deliberately narrow: only a field whose annotation resolves to a plain
scalar is judged. ``list[str]`` with ``max_length`` is a list length and is
correct; an annotation the guard cannot resolve (a project alias like
``CentsField``, a forward reference) is skipped rather than guessed at.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk"
"""The package this guard walks."""

NUMERIC_KEYWORDS: frozenset[str] = frozenset(
    {"le", "ge", "lt", "gt", "multiple_of"},
)
"""``Field`` keywords that compare the value against a magnitude."""

LENGTH_KEYWORDS: frozenset[str] = frozenset({"max_length", "min_length"})
"""``Field`` keywords that measure ``len(value)``."""

STRING_LEAVES: frozenset[str] = frozenset({"str"})
"""Annotation leaves that carry no magnitude."""

NUMERIC_LEAVES: frozenset[str] = frozenset({"int", "float", "Decimal"})
"""Annotation leaves that have no length."""

UNION_NAMES: frozenset[str] = frozenset({"Optional", "Union"})
"""Subscripts whose arguments are alternatives, not a container's payload."""

NONE_LEAVES: frozenset[str] = frozenset({"None", "NoneType"})
"""Leaves that describe absence and belong to neither category."""


def _leaves(node: ast.expr) -> set[str] | None:
    """Reduce an annotation to the set of scalar names it can be.

    Args:
        node (ast.expr): The annotation expression.

    Returns:
        set[str] | None: The leaf names, or ``None`` when the annotation is
        not a plain scalar — a container, a parameterized generic, or
        anything this guard would have to guess at. ``Annotated[X, ...]``
        is unwrapped to ``X``, and ``Optional``/``Union``/``|`` are read as
        alternatives.
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Constant):
        return {"None"} if node.value is None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _leaves(node.left)
        right = _leaves(node.right)
        return None if left is None or right is None else left | right
    if isinstance(node, ast.Subscript):
        return _subscript_leaves(node)
    return None


def _subscript_leaves(node: ast.Subscript) -> set[str] | None:
    """Reduce a subscripted annotation, when its shape is one we read.

    Args:
        node (ast.Subscript): The subscripted annotation.

    Returns:
        set[str] | None: Leaves for ``Annotated``/``Optional``/``Union``,
        ``None`` for every other subscript — ``list[str]`` is a container,
        and its length bound is legitimate.
    """
    head = node.value
    name = head.id if isinstance(head, ast.Name) else getattr(head, "attr", None)
    if name == "Annotated":
        payload = node.slice
        if isinstance(payload, ast.Tuple) and payload.elts:
            return _leaves(payload.elts[0])
        return None
    if name not in UNION_NAMES:
        return None
    members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
    collected: set[str] = set()
    for member in members:
        found = _leaves(member)
        if found is None:
            return None
        collected |= found
    return collected


def _field_calls(node: ast.AnnAssign) -> list[ast.Call]:
    """Collect the ``Field(...)`` calls attached to one annotated assignment.

    Args:
        node (ast.AnnAssign): The annotated assignment to inspect.

    Returns:
        list[ast.Call]: Calls to ``Field``, from the assigned value and from
        inside an ``Annotated[...]`` annotation alike.
    """
    calls: list[ast.Call] = []
    for candidate in (node.annotation, node.value):
        if candidate is None:
            continue
        for inner in ast.walk(candidate):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = (
                func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            )
            if name == "Field":
                calls.append(inner)
    return calls


def _mismatches(path: Path) -> list[str]:
    """Find constraints whose shape contradicts the field's type.

    Args:
        path (Path): The module to inspect.

    Returns:
        list[str]: One ``file:line: detail`` entry per violation.
    """
    source = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AnnAssign):
            continue
        leaves = _leaves(node.annotation)
        if leaves is None:
            continue
        concrete = leaves - NONE_LEAVES
        if concrete <= STRING_LEAVES and concrete:
            forbidden, shape = NUMERIC_KEYWORDS, "a string has no magnitude"
        elif concrete <= NUMERIC_LEAVES and concrete:
            forbidden, shape = LENGTH_KEYWORDS, "a number has no length"
        else:
            continue
        for call in _field_calls(node):
            for keyword in call.keywords:
                if keyword.arg in forbidden:
                    problems.append(
                        f"{_label(path)}:{keyword.value.lineno}: "
                        f"`{keyword.arg}=` on `{ast.unparse(node.annotation)}` "
                        f"— {shape}"
                    )
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


def test_no_constraint_contradicts_its_field_type() -> None:
    """Every bound is the kind its field can actually carry."""
    problems: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        problems.extend(_mismatches(path))
    assert not problems, (
        "pydantic raises `TypeError: Unable to apply constraint ...` at "
        "construction for these, so the field is unreachable, not merely "
        "strict:\n  " + "\n  ".join(problems)
    )


def test_the_guard_fires_on_the_shape_that_shipped(tmp_path: Path) -> None:
    """The exact two fields that reached the published OpenPix client."""
    module = tmp_path / "shipped.py"
    module.write_text(
        "from pydantic import BaseModel, Field\n\n\n"
        "class ChargeRefundPayload(BaseModel):\n"
        "    comment: str | None = Field(\n"
        '        description="Maximum length of 140 characters.",\n'
        "        le=140,\n"
        "        default=None,\n"
        "    )\n",
        encoding="utf-8",
    )

    problems = _mismatches(module)

    assert len(problems) == 1
    assert "`le=` on `str | None`" in problems[0]


def test_the_length_spelling_passes(tmp_path: Path) -> None:
    """The replacement the generator now emits is not flagged."""
    module = tmp_path / "fixed.py"
    module.write_text(
        "from pydantic import BaseModel, Field\n\n\n"
        "class ChargeRefundPayload(BaseModel):\n"
        "    comment: str | None = Field(max_length=140, default=None)\n",
        encoding="utf-8",
    )

    assert not _mismatches(module)


def test_a_length_bound_on_a_number_is_the_same_defect(tmp_path: Path) -> None:
    """The reverse pair fails at construction the same way."""
    module = tmp_path / "reversed.py"
    module.write_text(
        "from pydantic import BaseModel, Field\n\n\n"
        "class Charge(BaseModel):\n"
        "    value: int = Field(max_length=140)\n",
        encoding="utf-8",
    )

    assert _mismatches(module)


def test_a_length_bound_on_a_list_is_left_alone(tmp_path: Path) -> None:
    """`max_length` on `list[str]` bounds the list, which is legitimate."""
    module = tmp_path / "container.py"
    module.write_text(
        "from pydantic import BaseModel, Field\n\n\n"
        "class Charge(BaseModel):\n"
        "    tags: list[str] = Field(max_length=10, default_factory=list)\n",
        encoding="utf-8",
    )

    assert not _mismatches(module)


def test_an_unresolvable_annotation_is_skipped(tmp_path: Path) -> None:
    """A project alias is not decoded, so it is not judged."""
    module = tmp_path / "aliased.py"
    module.write_text(
        "from pydantic import BaseModel, Field\n\n"
        "from tempest_fastapi_sdk.utils import CentsField\n\n\n"
        "class Charge(BaseModel):\n"
        "    value: CentsField = Field(max_length=10)\n",
        encoding="utf-8",
    )

    assert not _mismatches(module)


def test_the_annotated_spelling_is_read_through(tmp_path: Path) -> None:
    """`Annotated[str, Field(le=...)]` hides the same defect one level in."""
    module = tmp_path / "annotated.py"
    module.write_text(
        "from typing import Annotated\n\n"
        "from pydantic import BaseModel, Field\n\n\n"
        "class ChargeRefundPayload(BaseModel):\n"
        "    comment: Annotated[str, Field(le=140)] = ''\n",
        encoding="utf-8",
    )

    assert _mismatches(module)
