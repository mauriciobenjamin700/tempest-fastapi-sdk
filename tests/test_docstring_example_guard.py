"""Guard: a ``>>>`` example in a docstring never teaches a private attribute.

The blind spot this closes is narrow and has already cost two releases.
``test_docs_type_guard`` runs mypy over every fenced block in the
Markdown — 1999 of them when it first ran — and v0.257.0 used it to fix,
among 162 findings, an example that passed ``emb._embed_many`` to
``BatchScheduler``. The same call survived in two places that guard does
not read: the docstring of ``BatchScheduler`` itself, and a prose bullet
written with backticks instead of a fence.

Both were still wrong three releases later, and the docstring one is the
worse of the two: ``_embed_many`` is synchronous while the scheduler
awaits its handler, so the documented snippet raises ``TypeError`` on the
first call. A reader copying from the API reference had no way to know.

Scope is deliberately one rule — a private attribute being *called* in a
doctest line. It is cheap, has no false positives in this tree, and it
catches the shape that shipped: an example reaching past the public API.
Type-checking docstring examples is a different, much larger job; these
snippets name free variables (``embedder``) that only the prose defines.

Add ``docstring-guard: skip`` to the line to exempt one deliberately.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

PACKAGE_ROOT: pathlib.Path = (
    pathlib.Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk"
)

SKIP_MARKER: str = "docstring-guard: skip"
"""Line marker for an example that means to show a private attribute."""

_PRIVATE_CALL = re.compile(r"\.\_[a-z][a-z0-9_]*\s*\(")
"""A call on an attribute whose name starts with a single underscore."""

_DOC_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _private_calls_in_examples(path: pathlib.Path) -> list[str]:
    """Find doctest lines in ``path`` that call a private attribute.

    Args:
        path (pathlib.Path): The module to inspect.

    Returns:
        list[str]: One ``file: line`` entry per offending example line.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - the suite never has one
        return []

    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOC_NODES):
            continue
        doc = ast.get_docstring(node)
        if not doc or ">>>" not in doc:
            continue
        for line in doc.splitlines():
            stripped = line.strip()
            if not (stripped.startswith(">>>") or stripped.startswith("...")):
                continue
            if SKIP_MARKER in stripped:
                continue
            if _PRIVATE_CALL.search(stripped):
                problems.append(f"{_label(path)}: {stripped}")
    return problems


def _label(path: pathlib.Path) -> str:
    """Render a path for the failure message.

    Args:
        path (pathlib.Path): The inspected file.

    Returns:
        str: A repo-relative path when possible, else the absolute one.
    """
    try:
        return str(path.relative_to(PACKAGE_ROOT.parent))
    except ValueError:
        return str(path)


def _modules() -> list[pathlib.Path]:
    """Collect every module in the package.

    Returns:
        list[pathlib.Path]: The files, sorted for a stable test id.
    """
    return sorted(PACKAGE_ROOT.rglob("*.py"))


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_docstring_examples_use_the_public_api(path: pathlib.Path) -> None:
    """No ``>>>`` example calls a private attribute.

    Args:
        path (pathlib.Path): The module under check.
    """
    problems = _private_calls_in_examples(path)
    assert not problems, (
        "docstring examples must teach the public API:\n  " + "\n  ".join(problems)
    )


class TestTheGuardFires:
    """The check must fail on the shape it exists to catch."""

    def test_the_shape_that_shipped_is_reported(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Reproduces the ``BatchScheduler`` docstring as it shipped.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        module = tmp_path / "batching.py"
        module.write_text(
            '"""Coalesce concurrent calls.\n\n'
            "    Example:\n\n"
            "        >>> async def embed_batch(texts: list[str])"
            " -> list[list[float]]:\n"
            "        ...     return await embedder._embed_many(texts)\n"
            '"""\n',
            encoding="utf-8",
        )
        assert _private_calls_in_examples(module), (
            "the guard missed the private call that shipped"
        )

    def test_the_public_form_passes(self, tmp_path: pathlib.Path) -> None:
        """The corrected example is accepted.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        module = tmp_path / "batching.py"
        module.write_text(
            '"""Coalesce concurrent calls.\n\n'
            "    Example:\n\n"
            "        >>> async def embed_batch(texts: list[str])"
            " -> list[list[float]]:\n"
            "        ...     return await embedder.embed(texts)\n"
            '"""\n',
            encoding="utf-8",
        )
        assert _private_calls_in_examples(module) == []

    def test_prose_mentioning_a_private_name_is_left_alone(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Only doctest lines are read, so prose can name the attribute.

        The corrected docstring says why ``_embed_many`` is wrong, which
        means the guard must not fire on the explanation itself.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        module = tmp_path / "batching.py"
        module.write_text(
            '"""Coalesce concurrent calls.\n\n'
            "    Its private ``_embed_many(texts)`` is synchronous, so awaiting\n"
            "    it raises TypeError.\n\n"
            "    Example:\n\n"
            "        >>> vec = await sched.submit('hello')\n"
            '"""\n',
            encoding="utf-8",
        )
        assert _private_calls_in_examples(module) == []

    def test_the_skip_marker_exempts_a_line(self, tmp_path: pathlib.Path) -> None:
        """A deliberate private example can opt out.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        module = tmp_path / "internals.py"
        module.write_text(
            '"""Internal helper.\n\n'
            "    Example:\n\n"
            "        >>> obj._drain(batch)  # docstring-guard: skip\n"
            '"""\n',
            encoding="utf-8",
        )
        assert _private_calls_in_examples(module) == []
