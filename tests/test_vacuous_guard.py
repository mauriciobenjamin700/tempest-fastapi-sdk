"""Guard against tests that assert a property they cannot observe.

The v0.218.0 PDF module shipped documenting byte-identical output "across
processes", backed by a test that rendered the same payload **twice in
one process** and compared. Inside one process those bytes match for
reasons that have nothing to do with the claim; three runs of the same
container produced three different hashes.

The shape is easy to write and reads as thorough:

    first = render(payload)
    second = render(payload)
    assert first == second

That is a fine test of *purity*. What it cannot see is anything that
outlives the process — and the docstring on the original said, in as many
words, "byte-identical **across processes**".

So the trigger is the boundary claim itself, not the vocabulary of
guarantees around it. A test whose name or docstring says it crossed a
process, a replica or a machine has to show it: a subprocess, a
container, a second engine. Saying so while staying put is wrong every
time, which is what makes this checkable at all.

Marking a false positive is one line, and the marker is the place to say
why.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_ROOT: Path = Path(__file__).resolve().parent

SKIP_MARKER: str = "vacuous-guard: skip"
"""Marker for a test whose same-process comparison is the real subject."""

CLAIM_WORDS: frozenset[str] = frozenset(
    {
        "across processes",
        "across replicas",
        "across machines",
        "across containers",
        "between processes",
        "across runs of",
        "outlives the process",
        "survives a restart",
    },
)
"""Phrases that name a boundary the test therefore has to cross.

Deliberately narrow. The first draft policed the *vocabulary of the
guarantee* — ``deterministic``, ``reproducible``, ``idempotent`` — and
flagged 22 tests, of which roughly twenty were correct as written:
idempotence is ``f(f(x)) == f(x)``, an in-process property by definition,
and one flagged test asserts bcrypt is **non**-deterministic.

A guard whose hits are mostly noise teaches people to add skip markers,
which is worse than no guard. So the trigger is not "does this test talk
about determinism" but "does it claim to have crossed a boundary" —
because that claim is unambiguous, and a test making it while staying in
one process is wrong every time.
"""

BOUNDARY_MARKERS: frozenset[str] = frozenset(
    {
        "subprocess",
        "docker",
        "Popen",
        "SOURCE_DATE_EPOCH",
        "spawn",
        "fork",
        "new_event_loop",
    },
)
"""Evidence that the test leaves the current process or engine behind."""


def _test_files() -> list[Path]:
    """Collect the test modules to inspect.

    Returns:
        list[Path]: Every ``test_*.py`` under ``tests/``, sorted.
    """
    return sorted(TESTS_ROOT.rglob("test_*.py"))


def _claims_persistence(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the claim word a test's name or docstring uses.

    Args:
        node (ast.FunctionDef | ast.AsyncFunctionDef): The test function.

    Returns:
        str | None: The matched word, or ``None`` when the test makes no
        such claim.
    """
    haystack = node.name.replace("_", " ").lower()
    docstring = ast.get_docstring(node) or ""
    haystack = f"{haystack}\n{docstring.lower()}"
    for word in CLAIM_WORDS:
        if word in haystack:
            return word
    return None


def _crosses_a_boundary(node: ast.AST, source_lines: list[str]) -> bool:
    """Whether the test body shows evidence of leaving the process.

    Args:
        node (ast.AST): The test function.
        source_lines (list[str]): The module's lines, for the raw text.

    Returns:
        bool: ``True`` when a boundary marker appears in the body.
    """
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", start + 1)
    body = "\n".join(source_lines[start:end])
    return any(marker in body for marker in BOUNDARY_MARKERS)


def _vacuous_tests(path: Path) -> list[str]:
    """Find tests claiming persistence without crossing a boundary.

    Args:
        path (Path): The test module.

    Returns:
        list[str]: One ``file:line: name — word`` entry per suspect.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.split("\n")
    tree = ast.parse(source)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        word = _claims_persistence(node)
        if word is None:
            continue
        start = node.lineno - 1
        end = node.end_lineno or start + 1
        if SKIP_MARKER in "\n".join(lines[start:end]):
            continue
        if _crosses_a_boundary(node, lines):
            continue
        try:
            label = str(path.relative_to(TESTS_ROOT.parent))
        except ValueError:  # pragma: no cover - only the guard's own fixtures
            label = str(path)
        problems.append(f"{label}:{node.lineno}: {node.name} — claims {word!r}")
    return problems


@pytest.mark.parametrize("path", _test_files(), ids=lambda p: p.name)
def test_persistence_claims_cross_a_process_boundary(path: Path) -> None:
    """A test claiming determinism must leave the process to see it."""
    problems = _vacuous_tests(path)
    assert not problems, (
        "these tests promise a property that outlives the process but only "
        "compare within it — run them in a subprocess (or mark them "
        f"`# {SKIP_MARKER}` with the reason):\n  " + "\n  ".join(problems)
    )


def test_the_guard_fires_on_the_shape_that_shipped(tmp_path: Path) -> None:
    """Reproduces the v0.218.0 test verbatim in shape.

    A guard that cannot fail is one nobody should trust — and this one
    exists because the original read as a real assertion.
    """
    offender = tmp_path / "test_offender.py"
    offender.write_text(
        "def test_same_payload_gives_the_same_bytes() -> None:\n"
        '    """This is what makes a document hashable and cacheable.\n'
        "\n"
        "    WeasyPrint writes no creation date and no document identifier\n"
        "    unless asked, so two renders of one payload are byte-identical\n"
        "    across processes.\n"
        '    """\n'
        "    first = render()\n"
        "    second = render()\n"
        "    assert first == second\n",
        encoding="utf-8",
    )
    assert _vacuous_tests(offender), "the guard missed the shape that shipped"


def test_a_subprocess_version_passes(tmp_path: Path) -> None:
    """Crossing the boundary is what makes the claim testable."""
    accepted = tmp_path / "test_accepted.py"
    accepted.write_text(
        "def test_output_is_reproducible() -> None:\n"
        '    """Byte-identical across processes."""\n'
        "    import subprocess\n"
        "    first = subprocess.run(cmd, capture_output=True).stdout\n"
        "    second = subprocess.run(cmd, capture_output=True).stdout\n"
        "    assert first == second\n",
        encoding="utf-8",
    )
    assert not _vacuous_tests(accepted)


def test_the_marker_silences_a_false_positive(tmp_path: Path) -> None:
    """Some same-process comparisons genuinely are the subject."""
    marked = tmp_path / "test_marked.py"
    unmarked = tmp_path / "test_unmarked.py"
    body = (
        "def test_state_is_shared_across_replicas() -> None:\n"
        '    """The store is the boundary here, not the process."""\n'
        "{marker}"
        "    assert store_a.get('k') == store_b.get('k')\n"
    )
    unmarked.write_text(body.format(marker=""), encoding="utf-8")
    assert _vacuous_tests(unmarked), "the fixture must trip the guard unmarked"

    marked.write_text(
        body.format(marker=f"    # {SKIP_MARKER}: two stores, one process\n"),
        encoding="utf-8",
    )
    assert not _vacuous_tests(marked)
