"""No file `tempest new` writes may still carry a template placeholder.

The templates for ``Dockerfile`` and ``.dockerignore`` are shared with
``tempest generate dockerfile``, which fills in ``__SPA_STAGE__``,
``__SPA_COPY__``, ``__SPA_HEADER__``, ``__SPA_IGNORE__`` and
``__SYSTEM_DEPS__``. The scaffold's context did not, so a fresh project
shipped those markers verbatim and ``docker build`` died on the first line
that carried one:

```text
ERROR: dockerfile parse error on line 8: unknown instruction: __SPA_HEADER__#
```

Measured against the real ``docker build --check`` on a scaffolded project,
before and after.

The guard is on the *shape*, not on the five known names: any
``__UPPER_CASE__`` left in any generated file fails. A new placeholder in a
new template inherits the check without anyone remembering to extend it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

PLACEHOLDER: re.Pattern[str] = re.compile(r"__[A-Z][A-Z_]{2,}__")
"""An unrendered marker: at least four upper-case characters between ``__``.

Upper-case only, so Python's own dunders (``__init__``, ``__all__``) in the
scaffolded source are not false positives.
"""


def _scaffold(tmp_path: Path, extras: str) -> Path:
    """Run ``tempest new`` and return the project root.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
        extras (str): The ``--extras`` value under test.

    Returns:
        Path: The scaffolded project root.
    """
    result = CliRunner().invoke(
        app,
        ["new", "demo", "--path", str(tmp_path), "--extras", extras],
    )
    assert result.exit_code == 0, result.output
    return tmp_path / "demo"


def _offenders(root: Path) -> dict[str, list[str]]:
    """Collect every generated file still holding a placeholder.

    Args:
        root (Path): The scaffolded project root.

    Returns:
        dict[str, list[str]]: Relative path to the markers found in it.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits = PLACEHOLDER.findall(text)
        if hits:
            found[str(path.relative_to(root))] = sorted(set(hits))
    return found


@pytest.mark.parametrize(
    "extras",
    ["", "auth,admin", "auth,admin,postgres", "auth,admin,postgres,ssr", "pdf"],
)
def test_scaffold_leaves_no_placeholder(tmp_path: Path, extras: str) -> None:
    """Every generated file is fully rendered, for every extras combination.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
        extras (str): The ``--extras`` value under test.
    """
    assert _offenders(_scaffold(tmp_path, extras)) == {}


def test_the_dockerfile_starts_with_a_comment_not_a_marker(
    tmp_path: Path,
) -> None:
    """Pins the exact line ``docker build`` refused to parse.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    dockerfile = (_scaffold(tmp_path, "auth,admin") / "Dockerfile").read_text()
    header = dockerfile.splitlines()[:12]
    assert all(
        line.startswith("#") or line.startswith("FROM") or not line.strip()
        for line in header
    ), header


def test_the_pdf_extra_brings_its_system_packages(tmp_path: Path) -> None:
    """``__SYSTEM_DEPS__`` was never rendered, so the stanza never shipped.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    The same missing context hid a second defect: a ``--extras pdf``
    scaffold produced an image without Pango or fontconfig, and WeasyPrint
    raises ``OSError`` from cffi at the **first render** rather than at
    build time — so the image looked fine until a user asked for a PDF.
    """
    dockerfile = (_scaffold(tmp_path, "pdf") / "Dockerfile").read_text()
    assert "libpango-1.0-0" in dockerfile
    assert "fonts-dejavu-core" in dockerfile


def test_a_project_without_pdf_stays_slim(tmp_path: Path) -> None:
    """The stanza is conditional, not unconditional.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    dockerfile = (_scaffold(tmp_path, "auth,admin") / "Dockerfile").read_text()
    assert "libpango-1.0-0" not in dockerfile
    assert "apt-get" not in dockerfile


def test_the_placeholder_pattern_matches_the_markers_that_shipped() -> None:
    """A guard that cannot fire is one nobody should trust.

    The five markers a scaffolded project used to carry, fed to the same
    pattern the test above uses.
    """
    shipped = (
        "__SPA_HEADER__# Run:    docker run --rm -p 8000:8000 demo\n"
        "__SPA_STAGE__# ---- builder ----\n"
        "__SYSTEM_DEPS__# Run as an unprivileged user.\n"
        '__SPA_COPY__ENV PATH="/app/.venv/bin:$PATH"\n'
        "__SPA_IGNORE__\n"
    )
    assert sorted(set(PLACEHOLDER.findall(shipped))) == [
        "__SPA_COPY__",
        "__SPA_HEADER__",
        "__SPA_IGNORE__",
        "__SPA_STAGE__",
        "__SYSTEM_DEPS__",
    ]


def test_python_dunders_are_not_flagged() -> None:
    """The scaffolded source is full of them, and none is a placeholder."""
    source = "__init__\n__all__: list[str] = []\n__version__ = '1'\n__main__\n"
    assert PLACEHOLDER.findall(source) == []
