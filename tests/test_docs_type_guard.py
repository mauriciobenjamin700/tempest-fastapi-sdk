"""Guard: a documented example must type-check against what it calls.

This closes the blind spot the other two docs-example guards name in their
own docstrings. `test_docs_examples_compile` resolves the *names* a block
imports; `test_docs_method_guard` resolves the *attributes* read off an
instance the block built. Neither can see an argument's **type**, and that
is how this shipped in `introspection-auth.md`:

    credentials = "eyJhbGciOiJIUzI1NiJ9.token"
    claims = await auth.get_claims(credentials)

`get_claims` takes `HTTPAuthorizationCredentials | None` and reads
`credentials.credentials`, so the block raises `AttributeError` on the line
the page is teaching. It parses, every name it imports exists, and the only
tool that could have seen it is a type checker.

So this guard runs one: every parseable Python block on the site becomes a
module in a scratch tree, and mypy checks the lot against the installed
packages, under **this repository's own** `pyproject.toml` config. Same
config, because that is the strictness the docs themselves teach a consumer
to switch on (`recipes/typing.md`), and the pydantic plugin it declares is
what types a schema constructor at all.

Only four error codes are read, and each one is a defect a reader hits by
pasting the block:

* `arg-type` — the wrong type reaches a parameter.
* `call-arg` — the wrong number or name of arguments.
* `name-defined` / `used-before-def` — a `NameError` on paste. A previous
  pass at "make every example complete" appended placeholder assignments at
  the **end** of blocks whose bodies already used them, which reads fine on
  the page and never runs.

Two categories are dropped, both measured rather than assumed:

* Any finding whose own line carries a bare `...`. That is this site's
  elision idiom — `FastAPI(...)`, `add_middleware(Mw, store=...)` — and
  mypy complains about it twice over, once as an argument of type
  `EllipsisType` and once as "too many positional arguments". Thirty such
  reports were measured, and not one was a defect.
* Blocks carrying `docs-guard: skip`, which is already how this repo marks
  a block that *is* the mistake the surrounding section describes —
  `recipes/typing.md` demonstrates rejected calls for a living.

What it does not cover, on purpose: `attr-defined`. Two legitimate shapes
produce it here and neither is a defect. Pages excerpt a class body without
its `__init__` ("add this method to your service"), so `self.repo` really is
undefined in the excerpt; and `op.replace_enum(...)` is an Alembic operation
this SDK registers at runtime with `@Operations.register_operation`, which
no static reader can see. After the sweep that shipped this guard, those two
were the only `attr-defined` left standing — 25 reports, zero defects.
`test_docs_method_guard` owns attribute existence for classes the block
actually constructs.

Cost: ~40s on a cold mypy cache, under a second warm. Both the scratch tree
and the cache live under the system temp directory, keyed by a fingerprint
of this checkout so parallel worktrees never share them.
"""

from __future__ import annotations

import ast
import hashlib
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from mypy import api as mypy_api

DOCS_ROOT: Path = Path(__file__).resolve().parent.parent

FENCE_RE: re.Pattern[str] = re.compile(
    r"^(?P<indent>[ \t]*)```(?:python|py)(?P<attrs>[^\n]*)\n"
    r"(?P<body>.*?)^(?P=indent)```",
    re.DOTALL | re.MULTILINE,
)

SKIP_MARKER: str = "docs-guard: skip"

CHECKED_CODES: frozenset[str] = frozenset(
    {"arg-type", "call-arg", "name-defined", "used-before-def"}
)
"""The mypy error codes a reader would hit by pasting the block."""

ELISION_RE: re.Pattern[str] = re.compile(r"(?<![.\w])\.\.\.(?![.\w])")
"""The `...` elision idiom, whichever way mypy chooses to complain about it."""

ERROR_RE: re.Pattern[str] = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): error: (?P<message>.*?)\s+\[(?P<code>[a-z-]+)\]$"
)

_FINGERPRINT: str = hashlib.sha256(str(DOCS_ROOT).encode("utf-8")).hexdigest()[:12]

CACHE_DIR: Path = Path(tempfile.gettempdir()) / f"tempest-docs-mypy-{_FINGERPRINT}"
"""Shared mypy cache, so only the snippets are ever re-analyzed."""


def _markdown_files() -> list[Path]:
    """Collect every Markdown file whose examples ship to readers.

    Returns:
        list[Path]: The docs tree plus the repository README, sorted for
        stable test ids.
    """
    files: list[Path] = sorted((DOCS_ROOT / "docs").rglob("*.md"))
    files.append(DOCS_ROOT / "README.md")
    return files


def _label(path: Path) -> str:
    """Return the repo-relative name of ``path``, or its own name off-tree.

    Args:
        path (Path): The Markdown file being reported.

    Returns:
        str: A stable label for the message. The fallback exists so the
        guard can be pointed at a temporary file, which is how the
        regression tests below feed it the block that shipped.
    """
    try:
        return str(path.relative_to(DOCS_ROOT))
    except ValueError:
        return path.name


def _workspace(tag: str) -> Path:
    """Return an empty scratch tree for one mypy run.

    Args:
        tag (str): Name distinguishing this run's tree from the others, so
            a regression test never clobbers the docs-wide run.

    Returns:
        Path: The freshly emptied directory. Paths stay stable between
        runs, which is what lets :data:`CACHE_DIR` stay warm.
    """
    trees: Path = Path(tempfile.gettempdir()) / f"tempest-docs-snips-{_FINGERPRINT}"
    root: Path = trees / tag
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_blocks(
    paths: list[Path], workspace: Path
) -> dict[str, tuple[str, int, list[str]]]:
    """Write every checkable Python block as a module under ``workspace``.

    Blocks that do not parse are skipped: `test_docs_examples_compile` owns
    syntax, and a deliberately partial snippet would otherwise report every
    name it elides.

    Args:
        paths (list[Path]): The Markdown files to extract from.
        workspace (Path): The scratch tree to write into.

    Returns:
        dict[str, tuple[str, int, list[str]]]: ``{module file name: (page
        label, line of the block's first code line, the block's lines)}``,
        for mapping mypy's report back to the page a reader is looking at
        and for reading the offending line back.
    """
    index: dict[str, tuple[str, int, list[str]]] = {}
    for path in paths:
        text: str = path.read_text(encoding="utf-8")
        for position, match in enumerate(FENCE_RE.finditer(text)):
            body: str = match.group("body")
            if SKIP_MARKER in body:
                continue
            try:
                ast.parse(body)
            except SyntaxError:
                continue
            stem: str = _label(path).replace("/", "_").replace(".", "_")
            name: str = f"{stem}_{position}.py"
            (workspace / name).write_text(body, encoding="utf-8")
            index[name] = (
                _label(path),
                text[: match.start()].count("\n") + 2,
                body.splitlines(),
            )
    return index


def _run_mypy(workspace: Path) -> str:
    """Type-check every module in ``workspace`` under the repo's config.

    ``--ignore-missing-imports`` is the one relaxation: examples import
    from the service they are teaching you to build (`src.core.settings`),
    which exists on the reader's machine and never here.

    Args:
        workspace (Path): The scratch tree written by :func:`_write_blocks`.

    Returns:
        str: mypy's stdout, one finding per line.
    """
    stdout, _stderr, _status = mypy_api.run(
        [
            "--config-file",
            str(DOCS_ROOT / "pyproject.toml"),
            "--ignore-missing-imports",
            "--no-error-summary",
            "--cache-dir",
            str(CACHE_DIR),
            str(workspace),
        ]
    )
    return stdout


def _findings(paths: list[Path], tag: str) -> list[str]:
    """Report every type error a reader would hit in these pages.

    Args:
        paths (list[Path]): The Markdown files to check.
        tag (str): Scratch-tree name for this run.

    Returns:
        list[str]: One ``page:line: message`` per finding, filtered to
        :data:`CHECKED_CODES` and stripped of the elision idiom.
    """
    workspace: Path = _workspace(tag)
    index: dict[str, tuple[str, int, list[str]]] = _write_blocks(paths, workspace)
    problems: list[str] = []
    for line in _run_mypy(workspace).splitlines():
        match: re.Match[str] | None = ERROR_RE.match(line.strip())
        if match is None:
            continue
        if match.group("code") not in CHECKED_CODES:
            continue
        located: tuple[str, int, list[str]] | None = index.get(
            Path(match.group("file")).name
        )
        if located is None:
            continue
        page, offset, lines = located
        block_line: int = int(match.group("line"))
        source: str = lines[block_line - 1] if block_line <= len(lines) else ""
        if ELISION_RE.search(source):
            continue
        problems.append(f"{page}:{offset + block_line - 1}: {match.group('message')}")
    return sorted(problems)


@pytest.fixture(scope="session")
def documented_type_errors() -> dict[str, list[str]]:
    """Type-check the whole site once and group the findings by page.

    Returns:
        dict[str, list[str]]: ``{page label: findings}``. One mypy run
        serves every parametrized case below — per-page runs would pay the
        import graph 114 times.
    """
    grouped: dict[str, list[str]] = {}
    for problem in _findings(_markdown_files(), tag="docs"):
        grouped.setdefault(problem.split(":", 1)[0], []).append(problem)
    return grouped


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: str(p.name))
def test_examples_type_check(
    path: Path, documented_type_errors: dict[str, list[str]]
) -> None:
    """Fail when an example passes an argument its callee cannot take."""
    problems: list[str] = documented_type_errors.get(_label(path), [])
    assert not problems, "\n".join(problems)


SHIPPED_ARGUMENT_DEFECT: str = '''```python
import asyncio
from typing import Any

from tempest_fastapi_sdk import IntrospectionAuth

from src.core.settings import settings

auth = IntrospectionAuth(userinfo_url=settings.IAGRO_USERINFO_URL)

credentials = "eyJhbGciOiJIUzI1NiJ9.token"


async def main() -> None:
    """The exact block that shipped in introspection-auth.md."""
    claims: dict[str, Any] = await auth.get_claims(credentials)
    print(claims)


asyncio.run(main())
```
'''

SHIPPED_ORDERING_DEFECT: str = """```python
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

repository = BaseRepository(session, model=UserModel)
session = None  # provided by db.get_session_context() in your code
```
"""

ELISION_IDIOM: str = """```python
from fastapi import FastAPI

from tempest_fastapi_sdk import IdempotencyMiddleware

app = FastAPI()
app.add_middleware(IdempotencyMiddleware, store=...)
```
"""


def test_guard_fires_on_the_argument_type_that_shipped(tmp_path: Path) -> None:
    """Feed it the block both sibling guards walked past, and assert it fails.

    `introspection-auth.md` handed a raw token string to a parameter typed
    `HTTPAuthorizationCredentials`. It imports real names and reads no
    attribute off a constructed instance, so only this guard can see it.
    """
    page: Path = tmp_path / "argument.md"
    page.write_text(SHIPPED_ARGUMENT_DEFECT, encoding="utf-8")
    problems: list[str] = _findings([page], tag="argument")
    assert len(problems) == 1, problems
    assert 'Argument 1 to "get_claims"' in problems[0]
    assert "HTTPAuthorizationCredentials" in problems[0]


def test_guard_fires_on_the_placeholder_defined_after_use(tmp_path: Path) -> None:
    """Pin the second shape: a name bound below the line that reads it.

    Eighty-nine of these shipped, all from the same well-meaning pass that
    appended `session = None` to the bottom of blocks that used `session`
    at the top. `NameError` the moment a reader pastes it.
    """
    page: Path = tmp_path / "ordering.md"
    page.write_text(SHIPPED_ORDERING_DEFECT, encoding="utf-8")
    problems: list[str] = _findings([page], tag="ordering")
    assert any("used before definition" in problem for problem in problems), problems


def test_guard_stays_quiet_on_the_elision_idiom(tmp_path: Path) -> None:
    """The false positive that would have made it noise.

    `store=...` is how every middleware recipe elides a value the section
    fills in two paragraphs later. mypy types `...` as `EllipsisType` and
    reports it as a wrong argument; seventeen such reports were measured,
    and not one was a defect.
    """
    page: Path = tmp_path / "elision.md"
    page.write_text(ELISION_IDIOM, encoding="utf-8")
    assert _findings([page], tag="elision") == []
