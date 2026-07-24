"""Guards that keep the docs and the public API honest.

Two cheap, low-false-positive checks that run in the gating suite:

1. **Doc code blocks parse** — every ```python fenced block in ``CLAUDE.md``,
   ``README.md`` and ``docs/**/*.md`` must be syntactically valid Python
   (after normalizing Markdown blockquote prefixes + indentation). Catches
   broken/rotted examples. Add ``# docs-guard: skip`` to a block to exempt an
   intentional non-parseable fragment.
2. **Declared exports resolve** — every name in each public module's
   ``__all__`` must actually be importable. Catches a covers-list / doc that
   references a symbol which was renamed or removed. Modules whose optional
   extra is absent in the current environment are skipped (so the check is
   strongest in CI, which installs ``--all-extras``).

These do not (and cannot) police prose backlog claims — keeping the
``CLAUDE.md`` covers / roadmap prose in sync stays a release-checklist item.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re
import textwrap

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BLOCK_RE = re.compile(r"```(?:python|py)\n(.*?)```", re.DOTALL)


def _doc_files() -> list[pathlib.Path]:
    files = [_ROOT / "CLAUDE.md", _ROOT / "README.md"]
    files.extend(sorted((_ROOT / "docs").rglob("*.md")))
    return [f for f in files if f.exists()]


def _normalize(block: str) -> str:
    """Strip a uniform Markdown blockquote prefix, then dedent."""
    lines = block.split("\n")
    non_empty = [line for line in lines if line.strip()]
    if non_empty and all(line.startswith("> ") or line == ">" for line in non_empty):
        lines = [line[2:] if line.startswith("> ") else "" for line in lines]
    return textwrap.dedent("\n".join(lines))


def test_doc_python_blocks_parse() -> None:
    """Every fenced ``python`` block in the docs is valid Python."""
    failures: list[str] = []
    for path in _doc_files():
        for index, block in enumerate(_BLOCK_RE.findall(path.read_text())):
            if "# docs-guard: skip" in block:
                continue
            try:
                ast.parse(_normalize(block))
            except SyntaxError as exc:
                rel = path.relative_to(_ROOT)
                failures.append(f"{rel} block #{index}: {str(exc).splitlines()[0]}")
    assert not failures, "unparseable doc code blocks:\n" + "\n".join(failures)


_PUBLIC_MODULES: tuple[str, ...] = (
    "tempest_fastapi_sdk",
    "tempest_fastapi_sdk.genai",
    "tempest_fastapi_sdk.genai.rag",
    "tempest_fastapi_sdk.vision",
    "tempest_fastapi_sdk.chat",
    "tempest_fastapi_sdk.reviews",
    "tempest_fastapi_sdk.geo",
    "tempest_fastapi_sdk.flags",
    "tempest_fastapi_sdk.queue",
    "tempest_fastapi_sdk.tasks",
    "tempest_fastapi_sdk.cache",
    "tempest_fastapi_sdk.admin",
    "tempest_fastapi_sdk.ssr",
    "tempest_fastapi_sdk.utils",
)


@pytest.mark.parametrize("module_name", _PUBLIC_MODULES)
def test_all_exports_resolve(module_name: str) -> None:
    """Every ``__all__`` name in the module is a real, importable attribute."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        pytest.skip(f"{module_name} needs an extra absent here: {exc}")
    names = getattr(module, "__all__", None)
    if names is None:
        pytest.skip(f"{module_name} declares no __all__")
    missing = [name for name in names if not hasattr(module, name)]
    assert not missing, f"{module_name}.__all__ names not resolvable: {missing}"
