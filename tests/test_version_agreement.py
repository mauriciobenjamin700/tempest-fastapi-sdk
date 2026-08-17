"""The two recorded versions must agree on every commit, not at release time.

``pyproject.toml`` carries the distribution version and
``tempest_fastapi_sdk.__version__`` carries the one the CLI prints and services
log. They are written by hand (or by ``make release``), and nothing before this
guard compared them inside ``make check``: the release workflow checks the pair
only when the tag is pushed, which is *after* the irreversible step. A wheel
whose ``__version__`` disagrees with its metadata has to be re-cut.
"""

from __future__ import annotations

import pathlib
import tomllib

import tempest_fastapi_sdk

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]


def test_pyproject_and_dunder_version_agree() -> None:
    """``pyproject.toml``'s version equals ``tempest_fastapi_sdk.__version__``."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject_version: str = tomllib.load(handle)["project"]["version"]
    assert pyproject_version == tempest_fastapi_sdk.__version__, (
        f"pyproject.toml says {pyproject_version}, "
        f"__init__.py says {tempest_fastapi_sdk.__version__}"
    )
