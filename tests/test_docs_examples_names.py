"""Guard: a documented example must not lean on a name it never defines.

This is the defect a reader hits hardest — worse than a syntax error,
because the block *looks* complete:

    agent = Agent(generator, tools=tools)   # generator? tools? from where?

Copy that into a file and it raises `NameError` on the first line. The
page had the context in a block three sections up, or in no block at all.

Every fence is checked on its own: names it uses must be defined in the
same fence (imported, assigned, bound as a parameter, or a builtin). A
fence that imports from a sibling example file (`from agent_setup import
build_agent`) counts as defined — that is the shape the fixed pages use,
and it is what makes each block a file the reader can actually save.

`KNOWN_FRAGMENTED` is the debt still to pay, and it is meant to shrink.
A page listed there is allowed to fail; a page **not** listed must be
clean. Fixing a page means deleting its line, and the test fails if a
listed page has become clean, so the list cannot drift into fiction.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

from tests.test_docs_examples_compile import DOCS_ROOT, FENCE_RE, _markdown_files

BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))

KNOWN_FRAGMENTED: frozenset[str] = frozenset(
    {
        # Delete a line when the page's examples become self-contained.
        "docs/admin-showcase.en.md",
        "docs/admin-showcase.md",
        "docs/fullstack-web.en.md",
        "docs/fullstack-web.md",
        "docs/integrated.en.md",
        "docs/integrated.md",
        "docs/learning/marketplace/flows.en.md",
        "docs/learning/marketplace/flows.md",
        "docs/marketplace-local.en.md",
        "docs/marketplace-local.md",
        "docs/migration.en.md",
        "docs/migration.md",
        "docs/recipes/admin-sql-console.en.md",
        "docs/recipes/admin-sql-console.md",
        "docs/recipes/artifact-registry.en.md",
        "docs/recipes/artifact-registry.md",
        "docs/recipes/audit-trail.en.md",
        "docs/recipes/audit-trail.md",
        "docs/recipes/auth-flow.en.md",
        "docs/recipes/auth-flow.md",
        "docs/recipes/authz.en.md",
        "docs/recipes/authz.md",
        "docs/recipes/br-helpers.en.md",
        "docs/recipes/br-helpers.md",
        "docs/recipes/cache.en.md",
        "docs/recipes/cache.md",
        "docs/recipes/chat.en.md",
        "docs/recipes/chat.md",
        "docs/recipes/deploy-safety.en.md",
        "docs/recipes/deploy-safety.md",
        "docs/recipes/downloads.en.md",
        "docs/recipes/downloads.md",
        "docs/recipes/email.en.md",
        "docs/recipes/email.md",
        "docs/recipes/feature-flags.en.md",
        "docs/recipes/feature-flags.md",
        "docs/recipes/file-store.en.md",
        "docs/recipes/file-store.md",
        "docs/recipes/geo.en.md",
        "docs/recipes/geo.md",
        "docs/recipes/index.en.md",
        "docs/recipes/index.md",
        "docs/recipes/introspection-auth.en.md",
        "docs/recipes/introspection-auth.md",
        "docs/recipes/logging.en.md",
        "docs/recipes/logging.md",
        "docs/recipes/metrics.en.md",
        "docs/recipes/metrics.md",
        "docs/recipes/oauth.en.md",
        "docs/recipes/oauth.md",
        "docs/recipes/offline-sync.en.md",
        "docs/recipes/offline-sync.md",
        "docs/recipes/openapi-client.en.md",
        "docs/recipes/openapi-client.md",
        "docs/recipes/openapi-errors.en.md",
        "docs/recipes/openapi-errors.md",
        "docs/recipes/outbox.en.md",
        "docs/recipes/outbox.md",
        "docs/recipes/reviews.en.md",
        "docs/recipes/reviews.md",
        "docs/recipes/security.en.md",
        "docs/recipes/security.md",
        "docs/recipes/sse.en.md",
        "docs/recipes/sse.md",
        "docs/recipes/storage.en.md",
        "docs/recipes/storage.md",
        "docs/recipes/stored-files.en.md",
        "docs/recipes/stored-files.md",
        "docs/recipes/tempestweb-frontend.en.md",
        "docs/recipes/tempestweb-frontend.md",
        "docs/recipes/utilities.en.md",
        "docs/recipes/utilities.md",
        "docs/recipes/webpush.en.md",
        "docs/recipes/webpush.md",
        "docs/recipes/websocket.en.md",
        "docs/recipes/websocket.md",
        "docs/ssr.en.md",
        "docs/ssr.md",
        "docs/tutorial.en.md",
        "docs/tutorial.md",
    }
)


class _NameCollector(ast.NodeVisitor):
    """Split a module's names into what it defines and what it consumes.

    Deliberately order-blind: a module-level assignment below a function
    that uses it is legal Python, so collecting every binding first and
    comparing at the end is what matches the language rather than the
    reading order.
    """

    def __init__(self) -> None:
        """Start with empty binding and usage sets."""
        self.defined: set[str] = set()
        self.used: set[str] = set()
        self.star_import: bool = False

    def visit_Import(self, node: ast.Import) -> None:
        """Bind each imported top-level module name."""
        for alias in node.names:
            self.defined.add((alias.asname or alias.name).split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Bind each imported symbol; a star import binds the unknown."""
        for alias in node.names:
            if alias.name == "*":
                self.star_import = True
                continue
            self.defined.add(alias.asname or alias.name)
        self.generic_visit(node)

    def _bind_arguments(self, args: ast.arguments) -> None:
        """Bind every parameter of a function or lambda."""
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            self.defined.add(arg.arg)
        if args.vararg is not None:
            self.defined.add(args.vararg.arg)
        if args.kwarg is not None:
            self.defined.add(args.kwarg.arg)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Bind the function name and its parameters."""
        self.defined.add(node.name)
        self._bind_arguments(node.args)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Bind the coroutine name and its parameters."""
        self.defined.add(node.name)
        self._bind_arguments(node.args)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Bind a lambda's parameters."""
        self._bind_arguments(node.args)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Bind the class name."""
        self.defined.add(node.name)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Bind the exception alias of ``except … as name``."""
        if node.name is not None:
            self.defined.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        """Treat declared globals as bound."""
        self.defined.update(node.names)
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Treat declared nonlocals as bound."""
        self.defined.update(node.names)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Record a name as bound when stored, as consumed when read."""
        if isinstance(node.ctx, ast.Store):
            self.defined.add(node.id)
        else:
            self.used.add(node.id)
        self.generic_visit(node)


def _undefined_names(body: str) -> list[str]:
    """Return the names a fence uses without defining.

    Args:
        body: The fence's Python source.

    Returns:
        The sorted undefined names. A fence that does not parse is left to
        the syntax guard, and a star import makes the answer unknowable, so
        both return nothing.
    """
    try:
        tree: ast.Module = ast.parse(body)
    except SyntaxError:
        return []
    collector = _NameCollector()
    collector.visit(tree)
    if collector.star_import:
        return []
    missing = collector.used - collector.defined - BUILTIN_NAMES
    return sorted(name for name in missing if not name.startswith("_"))


def _fragmented_blocks(path: Path) -> list[str]:
    """Report every fence in a file that leans on an undefined name."""
    text: str = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for match in FENCE_RE.finditer(text):
        missing = _undefined_names(match.group("body"))
        if missing:
            line: int = text[: match.start()].count("\n") + 1
            problems.append(
                f"{path.relative_to(DOCS_ROOT)}:{line}: undefined {missing}"
            )
    return problems


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: str(p.name))
def test_examples_define_every_name_they_use(path: Path) -> None:
    """Fail when a page's examples reference names they never build."""
    relative: str = str(path.relative_to(DOCS_ROOT))
    problems: list[str] = _fragmented_blocks(path)
    if relative in KNOWN_FRAGMENTED:
        assert problems, (
            f"{relative} is listed in KNOWN_FRAGMENTED but its examples are "
            "self-contained now — delete the entry."
        )
        pytest.xfail(f"{relative}: {len(problems)} fragmented block(s)")
    assert not problems, "\n".join(problems)
