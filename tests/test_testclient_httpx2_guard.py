"""``StarletteDeprecationWarning`` is a ``UserWarning``, not a deprecation.

Starlette 1.x binds ``starlette.testclient`` to ``httpx2`` when it is
installed and falls back to ``httpx`` with a warning when it is not. The
warning reads like cosmetic noise and is not:

    >>> from starlette.exceptions import StarletteDeprecationWarning as W
    >>> [c.__name__ for c in W.__mro__]
    ['StarletteDeprecationWarning', 'UserWarning', 'Warning', 'Exception',
     'BaseException', 'object']

Measured on 2026-08-30 with fastapi 0.141.1 / starlette 1.6.0 / httpx
0.28.1 / httpx2 2.12.0: a project whose ``pytest`` config turns warnings
into errors fails **at import**, before collecting a test, and the obvious
silencer (``ignore::DeprecationWarning``) matches nothing because the class
is not in that tree. With ``httpx2`` installed the import is silent and
``TestClient`` answers ``200`` over it.

Every service ``tempest new`` scaffolds opens its smoke test with ``from
fastapi.testclient import TestClient``, so the template's dev group is what
decides whether a freshly created project starts out warning. This guard
keeps ``httpx2`` pinned there and here.

The behavioural half is enforced separately, by
``filterwarnings = ["error::starlette.exceptions.StarletteDeprecationWarning"]``
in this repo's ``[tool.pytest.ini_options]``: drop the pin and the suite
stops collecting.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""The repository root."""

TEMPLATE: Path = REPO_ROOT / "tempest_fastapi_sdk/cli/_templates/pyproject.toml.tmpl"
"""The ``pyproject.toml`` every ``tempest new`` project is born with."""

CONFIGS: tuple[Path, ...] = (REPO_ROOT / "pyproject.toml", TEMPLATE)
"""Both TOMLs whose dev group runs ``fastapi.testclient``."""


def _dev_group(text: str) -> list[str]:
    """Read the ``dev`` dependency group out of a TOML document.

    Args:
        text (str): The document contents. The scaffold template parses as
            TOML as-is — its placeholders live inside string values.

    Returns:
        list[str]: The requirement strings, empty when there is no group.
    """
    groups = tomllib.loads(text).get("dependency-groups", {})
    return list(groups.get("dev", []))


def _violation(text: str) -> str | None:
    """Check one document for a dev group that would warn on import.

    Args:
        text (str): The document contents.

    Returns:
        str | None: The problem, or ``None`` when the group pins
        ``httpx2``.
    """
    dev = _dev_group(text)
    if any(req.startswith("httpx2") for req in dev):
        return None
    return "dev group runs `fastapi.testclient` without pinning `httpx2`"


class TestTestClientPinsHttpx2:
    """Neither this package nor the scaffold may warn on import."""

    @pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.name)
    def test_dev_group_pins_httpx2(self, config: Path) -> None:
        """Both dev groups carry the pin that keeps the import quiet."""
        problem = _violation(config.read_text(encoding="utf-8"))
        assert problem is None, f"{config}: {problem}"

    def test_template_no_longer_pins_bare_httpx_for_the_test_client(
        self,
    ) -> None:
        """The scaffold's only use of ``httpx`` was ``TestClient``."""
        dev = _dev_group(TEMPLATE.read_text(encoding="utf-8"))
        assert not [req for req in dev if req.startswith("httpx>")]


class TestGuardFires:
    """A guard that cannot fail is a guard nobody should trust."""

    def test_the_shipped_defect_is_reported(self) -> None:
        """This is the exact dev group the template shipped until now."""
        shipped = "\n".join(
            (
                "[dependency-groups]",
                "dev = [",
                '    "pytest>=8.3.3",',
                '    "httpx>=0.28.1",',
                "]",
            )
        )
        assert _violation(shipped) is not None


class TestMeasuredBehaviour:
    """The claims in this module's docstring, re-run rather than trusted."""

    def test_the_warning_is_a_user_warning(self) -> None:
        """Filtering on ``DeprecationWarning`` would catch nothing."""
        from starlette.exceptions import StarletteDeprecationWarning

        assert issubclass(StarletteDeprecationWarning, UserWarning)
        assert not issubclass(StarletteDeprecationWarning, DeprecationWarning)

    def test_the_test_client_is_bound_to_httpx2(self) -> None:
        """With the pin honoured, Starlette never reaches the fallback."""
        import starlette.testclient

        assert starlette.testclient.httpx.__name__ == "httpx2"
