"""Guard: ``**kwargs`` is for passthrough only, never for a consumed key.

A function that takes ``**kwargs`` / ``**options`` to forward somewhere
else may not read a key out of it for itself. The moment it does
``options.pop("x")``, ``x`` is a real parameter of the API — and hiding it
inside the catch-all costs three things at once:

1. **The type checker cannot see it.** ``**options: Any`` says nothing
   about ``x``, so nothing is checked and autocomplete offers nothing. The
   only way to discover the parameter is to read the source.
2. **The docstring drifts.** ``Extra arguments forwarded to Y`` becomes a
   lie the day a key is popped, and no docs guard can see that lie.
3. **It collides, eventually.** The day ``Y`` gains a parameter of that
   name, the function swallows it instead of forwarding it, and a
   documented upstream option silently does nothing.

This shipped five times in ``MessageBroker`` (four transport constructors
popping ``declare_topology``, plus ``rabbitmq``/``on`` popping
``prefetch``), and on three of them the key was not even in the docstring
— a supported parameter nobody could discover. It also survived a manual
audit of that exact file, which is why it is a test now rather than a
convention.

**The fix is always the same and is source compatible**: promote the key
to a named keyword-only parameter. ``**kwargs`` already made it
keyword-only, so no caller breaks.

!!! note "What this cannot see"
    The subtler form of the same defect — splatting ``**options`` into a
    callable whose own named parameters absorb some of the keys — needs
    the callee's signature resolved and is not detected here. It has the
    same fix and the same rationale.

Add ``# kwargs-guard: skip`` on the ``.pop()`` line to exempt a case that
is genuinely not this, with a docstring saying why.
"""

from __future__ import annotations

import ast
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGE = _ROOT / "tempest_fastapi_sdk"
_SKIP_MARKER = "# kwargs-guard: skip"

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _source_files() -> list[pathlib.Path]:
    """Return every Python file in the shipped package.

    Returns:
        list[pathlib.Path]: Sorted paths, so a failure lists findings in a
        stable order across runs.
    """
    return sorted(_PACKAGE.rglob("*.py"))


def _functions(tree: ast.AST) -> list[_FunctionNode]:
    """Return every function defined anywhere in ``tree``.

    Args:
        tree (ast.AST): A parsed module.

    Returns:
        list[_FunctionNode]: Both sync and async definitions, nested ones
        included — a closure that pops from its own catch-all hides the
        parameter just as well as a top-level function does.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _consumed_keys(function: _FunctionNode) -> list[tuple[int, str]]:
    """Return the keys ``function`` reads out of its own ``**kwargs``.

    Descends into nested functions, because a closure popping the outer
    catch-all is the same defect — except where the nested function
    declares a catch-all of the same name, which shadows it and makes the
    pop that function's own business.

    Args:
        function (_FunctionNode): The definition to inspect.

    Returns:
        list[tuple[int, str]]: ``(line number, key)`` per consumed key, or
        an empty list when the function takes no ``**kwargs`` or forwards
        it untouched.
    """
    catch_all = function.args.kwarg
    if catch_all is None:
        return []
    name = catch_all.arg
    found: list[tuple[int, str]] = []

    def descend(node: ast.AST) -> None:
        """Visit ``node``, stopping where the name is shadowed.

        Written as an explicit recursion rather than :func:`ast.walk`,
        which yields every descendant: skipping the shadowing definition
        alone would still visit its body and blame the outer function for
        a pop that is not its own.

        Args:
            node (ast.AST): The node to inspect.
        """
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                and child.args.kwarg is not None
                and child.args.kwarg.arg == name
            ):
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "pop"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == name
            ):
                key = child.args[0] if child.args else None
                label = key.value if isinstance(key, ast.Constant) else "<dynamic>"
                found.append((child.lineno, str(label)))
            descend(child)

    descend(function)
    return found


def _findings(path: pathlib.Path) -> list[str]:
    """Return one message per consumed key in ``path``.

    Args:
        path (pathlib.Path): The module to scan.

    Returns:
        list[str]: Human-readable findings, empty when the file is clean.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    findings: list[str] = []
    for function in _functions(tree):
        for lineno, key in _consumed_keys(function):
            if _SKIP_MARKER in lines[lineno - 1]:
                continue
            findings.append(
                f"{path.relative_to(_ROOT)}:{lineno}: {function.name}() pops "
                f"{key!r} out of **{function.args.kwarg.arg} — that is a "
                f"parameter of its API. Promote it to a named keyword-only "
                f"parameter (source compatible), or mark the line "
                f"{_SKIP_MARKER} if it is genuinely not this.",
            )
    return findings


def test_no_function_consumes_a_key_from_its_own_kwargs() -> None:
    """Every ``**kwargs`` in the package forwards, and consumes nothing."""
    problems: list[str] = []
    for path in _source_files():
        problems.extend(_findings(path))
    assert not problems, "\n".join(problems)


def test_the_guard_detects_the_defect_it_exists_for(
    tmp_path: pathlib.Path,
) -> None:
    """A guard that cannot fire is a guard nobody should trust.

    Rebuilds the shape that actually shipped — the pre-fix
    ``MessageBroker.rabbitmq`` — and asserts the scan reports it. Without
    this, a refactor that quietly stopped matching would leave the suite
    green and the package unguarded.
    """
    module = tmp_path / "sample.py"
    module.write_text(
        "from typing import Any\n"
        "\n"
        "\n"
        "def rabbitmq(url: str, **options: Any) -> object:\n"
        '    """Build a broker."""\n'
        '    declare_topology = bool(options.pop("declare_topology", True))\n'
        "    return (url, declare_topology, options)\n",
        encoding="utf-8",
    )
    lines = module.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    consumed = [
        key for function in _functions(tree) for _, key in _consumed_keys(function)
    ]
    assert consumed == ["declare_topology"]


def test_forwarding_untouched_is_not_a_finding(tmp_path: pathlib.Path) -> None:
    """The clean shape must stay silent, or the guard gets suppressed.

    ``TaskQueue.rabbitmq`` is exactly this: it forwards every keyword to
    the third-party broker and consumes none, which is what its docstring
    says. A guard that flagged it would be noise, and noise is how a guard
    ends up disabled.
    """
    module = tmp_path / "clean.py"
    module.write_text(
        "from typing import Any\n"
        "\n"
        "\n"
        "def rabbitmq(url: str, **options: Any) -> object:\n"
        '    """Build a broker."""\n'
        "    return (url, options)\n"
        "\n"
        "\n"
        "def local(**options: Any) -> object:\n"
        '    """Pop from a mapping this function built itself."""\n'
        '    own = {"a": 1}\n'
        '    own.pop("a", None)\n'
        "    return (own, options)\n",
        encoding="utf-8",
    )
    tree = ast.parse(module.read_text(encoding="utf-8"))
    consumed = [
        key for function in _functions(tree) for _, key in _consumed_keys(function)
    ]
    assert consumed == []


def test_a_shadowing_nested_catch_all_belongs_to_the_inner_function(
    tmp_path: pathlib.Path,
) -> None:
    """An inner ``**options`` is the inner function's, not the outer's."""
    module = tmp_path / "nested.py"
    module.write_text(
        "from typing import Any\n"
        "\n"
        "\n"
        "def outer(**options: Any) -> object:\n"
        '    """Forward untouched, and define an unrelated inner."""\n'
        "\n"
        "    def inner(**options: Any) -> object:\n"
        '        """Its own catch-all, shadowing the outer name."""\n'
        '        return options.pop("x", None)\n'
        "\n"
        "    return (inner, options)\n",
        encoding="utf-8",
    )
    tree = ast.parse(module.read_text(encoding="utf-8"))
    outer = next(f for f in _functions(tree) if f.name == "outer")
    inner = next(f for f in _functions(tree) if f.name == "inner")
    assert _consumed_keys(outer) == []
    assert [key for _, key in _consumed_keys(inner)] == ["x"]
