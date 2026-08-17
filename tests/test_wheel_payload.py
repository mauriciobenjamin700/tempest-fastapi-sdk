"""Guard the wheel's non-Python payload against both failure directions.

The wheel ships assets a service needs at runtime — Jinja templates, the admin
stylesheet, the CLI scaffold, the bundled Alembic ``env.py`` template, the IBGE
locations table. Two ways that breaks, both silent:

* **Junk gets in.** A file that is documentation or agent instruction lands
  inside ``tempest_fastapi_sdk/`` and every consumer downloads it. This
  happened: ``tempest_fastapi_sdk/integrations/CLAUDE.md`` shipped in a wheel
  built before ``pyproject.toml`` grew its ``exclude``.
* **An asset falls out.** An ``exclude`` pattern written to drop junk matches
  more than intended, or a rename orphans a ``force-include`` entry. The
  package imports fine and fails at the first render on the user's machine.

A path allowlist only catches the first. Asserting each pattern still matches
something catches the second, which is the more expensive one.

The check runs against a wheel built in a temporary directory, so it measures
the artifact rather than the repository — the two differ exactly when the build
configuration is wrong, which is what is under guard.
"""

from __future__ import annotations

import fnmatch
import pathlib
import shutil
import subprocess
import zipfile

import pytest

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]

PAYLOAD_PATTERNS: tuple[str, ...] = (
    "tempest_fastapi_sdk/py.typed",
    "tempest_fastapi_sdk/admin/static/*.css",
    "tempest_fastapi_sdk/admin/templates/*.html",
    "tempest_fastapi_sdk/auth/templates/pt-BR/*.html",
    "tempest_fastapi_sdk/auth/templates/en-US/*.html",
    "tempest_fastapi_sdk/cli/_templates/*",
    "tempest_fastapi_sdk/cli/_templates/src/*",
    "tempest_fastapi_sdk/cli/_templates/src/*/*",
    "tempest_fastapi_sdk/db/_alembic_templates/env.py.template",
    "tempest_fastapi_sdk/pdf/templates/*.html",
    "tempest_fastapi_sdk/pdf/templates/*.css",
    "tempest_fastapi_sdk/ssr/_static/htmx.min.js",
    "tempest_fastapi_sdk/utils/data/*.json",
)
"""Every non-``.py`` file the wheel is allowed to carry, as glob patterns.

Adding a runtime asset means adding its pattern here. Adding a file that is
not runtime payload means it does not belong inside the package directory.
"""


@pytest.fixture(scope="module")
def wheel_payload(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """Build a wheel and return its non-``.py``, non-metadata member names.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Pytest's temp-dir factory.

    Returns:
        list[str]: Payload member names, sorted.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv is not available to build the wheel")
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    wheels = sorted(out.glob("*.whl"))
    assert wheels, "uv build produced no wheel"
    with zipfile.ZipFile(wheels[-1]) as zf:
        names = zf.namelist()
    return sorted(
        name
        for name in names
        if not name.endswith(".py")
        and not name.startswith("tempest_fastapi_sdk-")
        and not name.endswith("/")
    )


def test_no_unexpected_file_ships(wheel_payload: list[str]) -> None:
    """Every non-``.py`` member matches an allowlisted runtime-asset pattern."""
    unexpected = [
        name
        for name in wheel_payload
        if not any(fnmatch.fnmatch(name, pattern) for pattern in PAYLOAD_PATTERNS)
    ]
    assert not unexpected, (
        "files in the wheel that are not allowlisted runtime payload: "
        f"{unexpected} — either add the pattern to PAYLOAD_PATTERNS or keep the "
        "file out of the package directory"
    )


def test_every_asset_family_still_ships(wheel_payload: list[str]) -> None:
    """Every allowlisted pattern still matches at least one member.

    This is the direction a broad ``exclude`` breaks: the wheel keeps building,
    the package keeps importing, and a template family is simply gone.
    """
    empty = [
        pattern
        for pattern in PAYLOAD_PATTERNS
        if not any(fnmatch.fnmatch(name, pattern) for name in wheel_payload)
    ]
    assert not empty, f"allowlisted payload patterns that match nothing: {empty}"


def test_allowlist_rejects_the_file_that_actually_shipped() -> None:
    """The matcher rejects ``integrations/CLAUDE.md``, which shipped once.

    Proves the allowlist would fail rather than wave through the exact defect
    that motivated it, without paying for a second wheel build.
    """
    leaked = "tempest_fastapi_sdk/integrations/CLAUDE.md"
    assert not any(fnmatch.fnmatch(leaked, pattern) for pattern in PAYLOAD_PATTERNS)
    kept = "tempest_fastapi_sdk/cli/_templates/CLAUDE.md.tmpl"
    assert any(fnmatch.fnmatch(kept, pattern) for pattern in PAYLOAD_PATTERNS)


def test_agent_instructions_do_not_ship(wheel_payload: list[str]) -> None:
    """No ``CLAUDE.md`` ships, while the CLI scaffold template still does.

    The scaffold ships ``CLAUDE.md.tmpl`` on purpose (``tempest new`` writes it
    into the generated project), so the exclude has to distinguish the two.
    """
    instructions = [name for name in wheel_payload if name.endswith("CLAUDE.md")]
    assert not instructions, f"agent instructions in the wheel: {instructions}"
    assert "tempest_fastapi_sdk/cli/_templates/CLAUDE.md.tmpl" in wheel_payload
