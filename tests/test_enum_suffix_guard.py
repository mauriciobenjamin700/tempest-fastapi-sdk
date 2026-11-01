"""Guard: every enum the SDK writes by hand is named ``<Something>Enum``.

The rule existed and was violated silently: 80 public enums shipped without
the suffix (``PdfExtractor``, ``JobStatus``, ``UF``...) beside seven that had
it, because nothing checked. They were renamed in one breaking change, and
this guard keeps the next one from slipping in.

Read from source with ``ast``. A class is an enum when one of its bases is a
stdlib enum class (``Enum``, ``StrEnum``, ``IntEnum``, ``Flag``,
``IntFlag``) or, transitively, another enum class defined in the package
(``BaseStrEnum``). Generated modules are exempt: their names follow the
provider's OpenAPI specification, and they are recognized by the
``Do not edit by hand`` line every generated module docstring carries.

Blind spot: an enum built with the functional API
(``Enum("Name", [...])``) is not a ``class`` statement and is not seen.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE: Path = Path(__file__).resolve().parents[1] / "tempest_fastapi_sdk"
"""The package the guard scans."""

SUFFIX: str = "Enum"
"""The suffix every hand-written enum carries."""

STDLIB_ENUMS: frozenset[str] = frozenset(
    {"Enum", "StrEnum", "IntEnum", "Flag", "IntFlag", "ReprEnum"},
)
"""Base names that make a class an enum."""

GENERATED_MARKER: str = "Do not edit by hand"
"""Phrase in the module docstring of every generated module."""


def _base_name(node: ast.expr) -> str:
    """Return the last dotted component of a base expression.

    Args:
        node (ast.expr): One entry of ``ClassDef.bases``.

    Returns:
        str: ``StrEnum`` for ``StrEnum`` and ``enum.StrEnum``; ``""`` for
        anything that is not a name or attribute.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_generated(tree: ast.Module) -> bool:
    """Whether a module is generated code.

    Args:
        tree (ast.Module): The parsed module.

    Returns:
        bool: ``True`` when its docstring carries :data:`GENERATED_MARKER`.
    """
    docstring = ast.get_docstring(tree) or ""
    return GENERATED_MARKER in docstring


def find_unsuffixed_enums(sources: dict[str, str]) -> list[str]:
    """List hand-written enum classes whose name lacks :data:`SUFFIX`.

    Args:
        sources (dict[str, str]): Module path to source text.

    Returns:
        list[str]: ``"<path>: <ClassName>"`` per offending class, sorted.
    """
    classes: dict[str, tuple[list[str], str, bool]] = {}
    for path, text in sources.items():
        tree = ast.parse(text)
        generated = _is_generated(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [_base_name(base) for base in node.bases]
                classes.setdefault(node.name, (bases, path, generated))

    def is_enum(name: str, seen: frozenset[str] = frozenset()) -> bool:
        """Resolve enum-ness through the package's own base classes.

        Args:
            name (str): A class name.
            seen (frozenset[str]): Names already on the resolution path.

        Returns:
            bool: ``True`` when ``name`` derives from a stdlib enum.
        """
        if name in STDLIB_ENUMS:
            return True
        if name not in classes or name in seen:
            return False
        return any(is_enum(base, seen | {name}) for base in classes[name][0])

    return sorted(
        f"{path}: {name}"
        for name, (_bases, path, generated) in classes.items()
        if not generated
        and name not in STDLIB_ENUMS
        and is_enum(name)
        and not name.endswith(SUFFIX)
    )


def _package_sources() -> dict[str, str]:
    """Read every module of the package.

    Returns:
        dict[str, str]: Repository-relative path to source text.
    """
    return {
        str(path.relative_to(PACKAGE.parent)): path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE.rglob("*.py"))
    }


def test_every_hand_written_enum_ends_in_enum() -> None:
    assert find_unsuffixed_enums(_package_sources()) == []


def test_the_scan_sees_the_package_enums() -> None:
    """A clean result is not vacuous: the scan finds the renamed enums."""
    sources = _package_sources()
    renamed = (
        "class PdfExtractorEnum(BaseStrEnum)",
        "class JobStatusEnum(",
        "class UFEnum(",
    )
    joined = "\n".join(sources.values())
    assert all(marker in joined for marker in renamed)


def test_it_fires_on_the_shape_that_shipped() -> None:
    """``PdfExtractor`` derived from ``BaseStrEnum`` without the suffix."""
    base = (
        '"""Enums."""\n'
        "from enum import StrEnum\n\n\n"
        "class BaseStrEnum(StrEnum):\n"
        '    """Base."""\n'
    )
    shipped = '"""Pages."""\nclass PdfExtractor(BaseStrEnum):\n    TEXT = "text"\n'
    assert find_unsuffixed_enums(
        {"core/enums.py": base, "pdf/pages.py": shipped},
    ) == ["pdf/pages.py: PdfExtractor"]


def test_it_fires_on_a_direct_stdlib_enum() -> None:
    source = '"""M."""\nimport enum\n\n\nclass Color(enum.Enum):\n    RED = 1\n'
    assert find_unsuffixed_enums({"m.py": source}) == ["m.py: Color"]


def test_generated_modules_are_exempt() -> None:
    source = (
        '"""Pydantic schemas generated from a spec.\n\n'
        'Do not edit by hand — rerun `tempest openapi-client` to refresh.\n"""\n'
        "from enum import StrEnum\n\n\n"
        "class ChargeStatus(StrEnum):\n"
        '    ACTIVE = "ACTIVE"\n'
    )
    assert find_unsuffixed_enums({"integrations/x/schemas.py": source}) == []
