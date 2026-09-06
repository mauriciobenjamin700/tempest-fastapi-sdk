"""The checked-in zap modules match what the generator produces.

Shipping generated code means it can be edited by hand, and hand edits to
generated code are invisible until the next regeneration silently reverts
them. This suite makes that loud: if ``schemas.py`` or ``client.py``
differs by one byte from what ``scripts/regen_zap.py`` produces out of the
vendored specification, it fails here rather than in someone's service.

**The vendored file matters more here than for the other providers.**
OpenPix and Mercado Pago publish their documents, so a lost copy is one
download away. zap-api is ours and serves its document from wherever it is
deployed — there is no canonical URL to re-fetch from, which is why
``SPEC_SHA256`` records which bytes this checkout generated from rather
than which bytes a provider serves.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Locate the repository root from this file.

    Returns:
        Path: The first ancestor directory holding ``pyproject.toml``.

    Raises:
        RuntimeError: When no ancestor carries ``pyproject.toml``.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("repository root not found")


REPO_ROOT: Path = _repo_root()
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from regen_zap import (  # noqa: E402
    GENERATED_FILES,
    PACKAGE_DIR,
    SPEC_PATH,
    SPEC_SHA256,
    expected_exports,
    regenerate,
    spec_digest,
)


class TestVendoredSpec:
    """The pinned specification is present and usable."""

    def test_specification_is_vendored(self) -> None:
        """Regeneration and this suite both have to work offline."""
        assert SPEC_PATH.exists(), f"missing {SPEC_PATH}"
        assert SPEC_PATH.stat().st_size > 10_000

    def test_specification_is_the_bytes_that_were_generated_from(self) -> None:
        """A refresh has to update the digest in the same commit.

        Refresh with ``make zap-fetch`` — which needs the gateway running,
        or ``ZAP_OPENAPI_URL`` pointing at one — and paste the digest it
        prints into ``SPEC_SHA256``.
        """
        assert spec_digest() == SPEC_SHA256

    def test_specification_is_not_in_the_wheel(self) -> None:
        """It is build-time input, not something a service loads."""
        package_root = REPO_ROOT / "tempest_fastapi_sdk"
        assert package_root not in SPEC_PATH.parents


@pytest.fixture(scope="module")
def freshly_generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Regenerate the zap modules into a temporary directory.

    Args:
        tmp_path_factory (pytest.TempPathFactory): pytest's factory.

    Returns:
        Path: Directory holding the freshly generated modules.
    """
    destination = tmp_path_factory.mktemp("zap_regen")
    regenerate(destination)
    return destination


class TestGeneratedFilesAreNotStale:
    """The files on disk are what the generator produces today."""

    @pytest.mark.parametrize("filename", GENERATED_FILES)
    def test_matches_byte_for_byte(
        self, freshly_generated: Path, filename: str
    ) -> None:
        """A hand edit here is reverted by the next regeneration.

        Args:
            freshly_generated (Path): The regenerated output.
            filename (str): The generated module under test.
        """
        checked_in = (PACKAGE_DIR / filename).read_text(encoding="utf-8")
        produced = (freshly_generated / filename).read_text(encoding="utf-8")
        assert checked_in == produced, (
            f"{filename} differs from `make zap-regen` output — regenerate "
            f"rather than editing it by hand"
        )


class TestExportsFollowTheGeneratedModules:
    """``__all__`` lists every generated name, and stays that way.

    A name missing from ``__all__`` still imports at runtime — the lazy
    ``__getattr__`` finds it — so nothing fails until a consumer runs a
    strict type-checker and gets *"X is not exported from module"*. That is
    how it shipped on OpenPix, and it is why the list is generated rather
    than curated.
    """

    def test_all_matches_the_generated_modules(self, freshly_generated: Path) -> None:
        """The checked-in list is what regeneration would produce.

        Args:
            freshly_generated (Path): The regenerated output.
        """
        from tempest_fastapi_sdk.integrations.messaging import zap

        assert zap.__all__ == expected_exports(
            freshly_generated, PACKAGE_DIR / "__init__.py"
        )

    def test_every_exported_name_resolves(self) -> None:
        """``__all__`` promising a name the lazy hook cannot find is a lie."""
        from tempest_fastapi_sdk.integrations.messaging import zap

        for name in zap.__all__:
            assert getattr(zap, name) is not None

    def test_the_client_is_exported(self) -> None:
        """The one name every consumer needs."""
        from tempest_fastapi_sdk.integrations.messaging import zap

        assert "ZapClient" in zap.__all__


class TestImportingIsCheap:
    """Reaching the namespace must not build every model."""

    def test_importing_the_namespace_leaves_schemas_unloaded(self) -> None:
        """The lazy hook is the whole reason the namespace is safe to import.

        Run in a subprocess because ``sys.modules`` is global and another
        test in this session will already have imported the schemas.
        """
        import subprocess

        code = (
            "import sys;"
            "import tempest_fastapi_sdk.integrations.messaging as m;"
            "assert m is not None;"
            "loaded = ["
            "  name for name in sys.modules"
            "  if name.endswith('messaging.zap.schemas')"
            "];"
            "print(loaded)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        )
        assert result.stdout.strip() == "[]"
