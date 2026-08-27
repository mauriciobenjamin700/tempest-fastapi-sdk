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

Five error codes are read, and each one is a defect a reader hits by
pasting the block:

* `arg-type` — the wrong type reaches a parameter.
* `call-arg` — the wrong number or name of arguments.
* `name-defined` / `used-before-def` — a `NameError` on paste. A previous
  pass at "make every example complete" appended placeholder assignments at
  the **end** of blocks whose bodies already used them, which reads fine on
  the page and never runs.
* `attr-defined`, **only** when the owner is a class the block imported from
  the family. `"Coordinate" has no attribute "location"` is a real call
  against a class the SDK ships; `"UserService" has no attribute "repo"` is a
  page excerpting a service without its `__init__`, and is dropped.

Two categories are dropped, both measured rather than assumed:

* Any finding whose own line carries a bare `...`. That is this site's
  elision idiom — `FastAPI(...)`, `add_middleware(Mw, store=...)` — and
  mypy complains about it twice over, once as an argument of type
  `EllipsisType` and once as "too many positional arguments". Thirty such
  reports were measured, and not one was a defect.
* Blocks carrying `docs-guard: skip`, which is already how this repo marks
  a block that *is* the mistake the surrounding section describes —
  `recipes/typing.md` demonstrates rejected calls for a living.

What the `attr-defined` scoping drops, and why it has to: a page excerpting
a class body without its `__init__` ("add this method to your service") has
no `self.repo` and never will, and `op.replace_enum(...)` is an Alembic
operation this SDK registers at runtime with `@Operations.register_operation`,
which no static reader can see. Both were measured — 25 reports, zero
defects — and neither imports its owner from the family, so the import gate
removes them without naming them.

`test_docs_method_guard` still owns the narrower case it was built for: an
attribute read off an instance the block constructs, including one the class
only ever assigns on `self`, which is invisible to a type checker.

Cost: ~40s on a cold mypy cache, a second or two warm. That is one mypy run
more than `make check` already pays in `make type`; they cover disjoint
ground (the package versus the documented calls into it) and share the
cache, so the second one is only ever paying for the snippets. Both the
scratch tree and the cache live under the system temp directory, keyed by a
fingerprint of this checkout so parallel worktrees never share them, and the
tree is scoped by process id so two suites in one checkout do not either.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
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
    {"arg-type", "call-arg", "name-defined", "used-before-def", "attr-defined"}
)
"""The mypy error codes a reader would hit by pasting the block.

`attr-defined` is read only for a class the block **imports from the Tempest
family** — see :func:`_family_imports`. Scoped that way it is precise; scoped
wider it is noise, because a page excerpting a class body without its
`__init__` genuinely has no `self.repo`.
"""

TEMPEST_PACKAGES: tuple[str, ...] = (
    "tempest_fastapi_sdk",
    "tempest_core",
    "tempestweb",
)
"""Packages whose classes this guard is willing to judge attributes on."""

ATTR_OWNER_RE: re.Pattern[str] = re.compile(
    r'^"?(?:type\[)?(?P<owner>[A-Za-z_][A-Za-z0-9_]*)\]?"? has no attribute'
)
"""Pulls the owner out of `"Coordinate" has no attribute "location"`."""

ELISION_RE: re.Pattern[str] = re.compile(r"(?<![.\w])\.\.\.(?![.\w])")
"""The `...` elision idiom, whichever way mypy chooses to complain about it."""

ERROR_RE: re.Pattern[str] = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): error: (?P<message>.*?)\s+\[(?P<code>[a-z-]+)\]$"
)

_FINGERPRINT: str = hashlib.sha256(str(DOCS_ROOT).encode("utf-8")).hexdigest()[:12]

CACHE_DIR: Path = Path(tempfile.gettempdir()) / f"tempest-docs-mypy-{_FINGERPRINT}"
"""Shared mypy cache, so only the snippets are ever re-analyzed."""


@dataclass(frozen=True)
class Block:
    """One documented example, and what is known about it.

    Attributes:
        page (str): Repo-relative page the block came from.
        offset (int): Line of the block's first code line inside the page.
        lines (list[str]): The block's source lines.
        family (frozenset[str]): Names the block imported from the Tempest
            family, which is what makes an `attr-defined` report judgeable.
    """

    page: str
    offset: int
    lines: list[str]
    family: frozenset[str]

    def owns(self, message: str) -> bool:
        """Report whether an `attr-defined` message is about a shipped class.

        Args:
            message (str): The mypy message, e.g. ``"Coordinate" has no
                attribute "location"``.

        Returns:
            bool: ``True`` when the owner is a name this block imported from
            the family. `"UserService" has no attribute "repo"` (a class the
            page defines without its `__init__`), `"str" has no attribute
            "send"` and `Module has no attribute "replace_enum"` (an Alembic
            operation registered at runtime) all answer ``False``.
        """
        owner: re.Match[str] | None = ATTR_OWNER_RE.match(message)
        return owner is not None and owner.group("owner") in self.family


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

    The tree is scoped by process id as well as by tag, because two runs of
    the suite in one checkout would otherwise write the same 1999 files at
    each other — which is not hypothetical, it happened while this guard was
    being written, and the loser silently checks a half-written tree. That
    costs nothing: mypy keys its cache on module name, not on the path the
    module was read from, so a never-before-seen directory still resolves
    warm (measured: 1.31s against 1.57s for the directory it had just used).

    Stale trees from earlier runs are pruned on the way in, so the temp
    directory does not grow one tree per invocation forever.

    Args:
        tag (str): Name distinguishing this run's tree from the others, so
            a regression test never clobbers the docs-wide run.

    Returns:
        Path: The freshly emptied directory.
    """
    trees: Path = Path(tempfile.gettempdir()) / f"tempest-docs-snips-{_FINGERPRINT}"
    _prune(trees)
    root: Path = trees / f"{os.getpid()}-{tag}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prune(trees: Path, *, max_age_seconds: float = 86_400.0) -> None:
    """Delete scratch trees older than a day, best effort.

    Args:
        trees (Path): The per-checkout parent directory.
        max_age_seconds (float): Age past which a tree is removed.
    """
    if not trees.is_dir():
        return
    cutoff: float = time.time() - max_age_seconds
    for child in trees.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def _write_blocks(paths: list[Path], workspace: Path) -> dict[str, Block]:
    """Write every checkable Python block as a module under ``workspace``.

    Blocks that do not parse are skipped: `test_docs_examples_compile` owns
    syntax, and a deliberately partial snippet would otherwise report every
    name it elides.

    Args:
        paths (list[Path]): The Markdown files to extract from.
        workspace (Path): The scratch tree to write into.

    Returns:
        dict[str, Block]: ``{module file name: block}``, for mapping mypy's
        report back to the page a reader is looking at, for reading the
        offending line back, and for judging which `attr-defined` reports
        are about a class the SDK actually ships.
    """
    index: dict[str, Block] = {}
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
            index[name] = Block(
                page=_label(path),
                offset=text[: match.start()].count("\n") + 2,
                lines=body.splitlines(),
                family=_family_imports(body),
            )
    return index


def _family_imports(body: str) -> frozenset[str]:
    """Collect the names a block imports from the Tempest family.

    This is what makes reading `attr-defined` safe. `"UserService" has no
    attribute "repo"` is a page excerpting a service without its `__init__`;
    `"Coordinate" has no attribute "location"` is a real call against a class
    the SDK ships. The two are told apart by whether the block imported the
    name from the family at all.

    Args:
        body (str): The fence's Python source.

    Returns:
        frozenset[str]: Locally bound names that came from a family package.
    """
    try:
        tree: ast.Module = ast.parse(body)
    except SyntaxError:
        return frozenset()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if (node.module or "").split(".", 1)[0] not in TEMPEST_PACKAGES:
            continue
        names.update(alias.asname or alias.name for alias in node.names)
    return frozenset(names)


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
    index: dict[str, Block] = _write_blocks(paths, workspace)
    problems: list[str] = []
    for line in _run_mypy(workspace).splitlines():
        match: re.Match[str] | None = ERROR_RE.match(line.strip())
        if match is None:
            continue
        code: str = match.group("code")
        if code not in CHECKED_CODES:
            continue
        block: Block | None = index.get(Path(match.group("file")).name)
        if block is None:
            continue
        message: str = match.group("message")
        if code == "attr-defined" and not block.owns(message):
            continue
        block_line: int = int(match.group("line"))
        source: str = (
            block.lines[block_line - 1] if block_line <= len(block.lines) else ""
        )
        if ELISION_RE.search(source):
            continue
        problems.append(f"{block.page}:{block.offset + block_line - 1}: {message}")
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


SHIPPED_ATTRIBUTE_DEFECT: str = """```python
from tempest_fastapi_sdk.geo import Coordinate, nearest, within_radius

store_a = Coordinate(latitude=-8.0476, longitude=-34.8770)
store_b = Coordinate(latitude=-7.9899, longitude=-34.8386)

center = Coordinate(latitude=-23.55, longitude=-46.63)
stores = [store_a, store_b]  # objects with .location: Coordinate

near = within_radius(center, stores, 5.0, key=lambda s: s.location)
top2 = nearest(center, stores, k=2, key=lambda s: s.location)
```
"""

EXCERPTED_CLASS: str = """```python
from uuid import UUID

from tempest_fastapi_sdk import UploadUtils

from src.schemas import UserResponseSchema

avatar_storage = UploadUtils(source="./uploads/avatars")


class UserService:
    async def set_avatar(self, user_id: UUID, path: str) -> UserResponseSchema:
        user = await self.repo.get_by_id(user_id)
        return self.repo.map_to_response(user)
```
"""


def test_guard_fires_on_the_attribute_of_a_shipped_class(tmp_path: Path) -> None:
    """Pin the third shape: an attribute the SDK's own class does not have.

    `geo.md` built bare `Coordinate`s, called them "objects with `.location`"
    in the comment, and then read `s.location` off each — `AttributeError` on
    the line the section teaches. The sibling method guard cannot see it: the
    read happens on a lambda parameter, not on the local the block built.
    """
    page: Path = tmp_path / "attribute.md"
    page.write_text(SHIPPED_ATTRIBUTE_DEFECT, encoding="utf-8")
    problems: list[str] = _findings([page], tag="attribute")
    assert problems, problems
    assert all('"Coordinate" has no attribute "location"' in p for p in problems)


def test_guard_stays_quiet_on_a_class_the_page_only_excerpts(tmp_path: Path) -> None:
    """The false positive the import gate exists to remove.

    Pages routinely show one method of a service without its `__init__` —
    "add this to your service" — so `self.repo` is undefined by design.
    Twenty-one such reports were measured across the site. None of them names
    a class the block imported from the family, which is exactly what tells
    them apart from the case above.
    """
    page: Path = tmp_path / "excerpt.md"
    page.write_text(EXCERPTED_CLASS, encoding="utf-8")
    assert _findings([page], tag="excerpt") == []


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
