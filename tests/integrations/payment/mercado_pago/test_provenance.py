"""The recorded origin of the vendored Mercado Pago document stays true.

Issue #228 was open for two days on a claim the repository contradicted
itself about: `vendor/PROVENANCE.md`, `scripts/mercadopago_diff.py` and two
docstrings said the document had no upstream, while the module docstring of
`scripts/regen_mercado_pago.py` — thirty lines above one of those sentences —
named `github.com/mercadopago/openapi` correctly. The false half came from a
probe that guessed a filename:

```text
404  raw.githubusercontent.com/mercadopago/openapi/main/openapi.yaml
200  raw.githubusercontent.com/mercadopago/openapi/main/spec3.yaml
```

Nothing could catch that, because no guard reads prose. This one reads the
narrow, checkable part of it: that the URL the fetcher uses is the URL the
provenance file records, that the digest agrees, and that the refresh path
the prose promises actually exists in the Makefile.

It deliberately does **not** grep for the old wording. The correction notes
quote it on purpose, and a guard that forbids quoting a mistake makes the
mistake harder to explain. Asserting the positive facts survives any
rewording of the prose around them.

Offline by construction: nothing here goes to the network, so it runs in the
same gate as everything else. Whether the bytes still match what the provider
serves is `make mercadopago-fetch`, which is a human decision to make.
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

from regen_mercado_pago import (  # noqa: E402
    SPEC_PATH,
    SPEC_SHA256,
    SPEC_URL,
    spec_digest,
)

PROVENANCE: Path = REPO_ROOT / "vendor" / "PROVENANCE.md"
"""Where every vendored document's origin is recorded."""

MAKEFILE: Path = REPO_ROOT / "Makefile"
"""Where the refresh path the prose promises has to actually exist."""

RECIPES: tuple[Path, ...] = (
    REPO_ROOT / "docs" / "recipes" / "mercado-pago.md",
    REPO_ROOT / "docs" / "recipes" / "mercado-pago.en.md",
)
"""Both language mirrors of the page that explains the document's evidence."""

UPSTREAM_REPO: str = "github.com/mercadopago/openapi"
"""The provider's specification repository, named in the prose."""


class TestTheOriginIsRecorded:
    """What the fetcher uses and what the provenance says must agree."""

    def test_provenance_records_the_url_the_fetcher_uses(self) -> None:
        """A URL only in the code is a URL nobody reviewing can check."""
        assert SPEC_URL in PROVENANCE.read_text(encoding="utf-8"), (
            f"vendor/PROVENANCE.md does not record {SPEC_URL}, which is "
            "where scripts/regen_mercado_pago.py refreshes the document from"
        )

    def test_provenance_records_the_digest_on_disk(self) -> None:
        """The inventory table has to name the bytes actually vendored."""
        text = PROVENANCE.read_text(encoding="utf-8")
        assert SPEC_SHA256[:12] in text, (
            f"vendor/PROVENANCE.md does not carry {SPEC_SHA256[:12]}…"
        )

    def test_the_pinned_digest_is_the_file_on_disk(self) -> None:
        """Belt and braces with the drift suite, from the other direction."""
        assert spec_digest() == SPEC_SHA256

    def test_the_url_points_at_the_repository_the_prose_names(self) -> None:
        """One rename away from the prose and the code disagreeing again."""
        assert UPSTREAM_REPO in SPEC_URL.replace(
            "raw.githubusercontent.com", "github.com"
        )


class TestTheRefreshPathExists:
    """Prose promising `make mercadopago-fetch` needs the target to exist."""

    def test_the_makefile_has_the_fetch_target(self) -> None:
        """Named in PROVENANCE.md, the recipe pages and two docstrings."""
        makefile = MAKEFILE.read_text(encoding="utf-8")
        assert "mercadopago-fetch:" in makefile
        assert "regen_mercado_pago.py --fetch" in makefile

    def test_the_spec_path_is_what_the_fetch_target_names(self) -> None:
        """The target's help text must not drift off the real file."""
        makefile = MAKEFILE.read_text(encoding="utf-8")
        relative = SPEC_PATH.relative_to(REPO_ROOT).as_posix()
        assert relative in makefile


class TestTheRecipesNameTheUpstream:
    """Both languages tell the reader where the document comes from."""

    @pytest.mark.parametrize("path", RECIPES, ids=lambda p: p.name)
    def test_recipe_names_the_provider_repository(self, path: Path) -> None:
        """A reader deciding whether to trust an operation needs this."""
        assert UPSTREAM_REPO in path.read_text(encoding="utf-8"), (
            f"{path.name} does not name {UPSTREAM_REPO}"
        )

    @pytest.mark.parametrize("path", RECIPES, ids=lambda p: p.name)
    def test_recipe_names_the_refresh_command(self, path: Path) -> None:
        """Knowing the origin is only useful with the way to re-pull it."""
        assert "make mercadopago-fetch" in path.read_text(encoding="utf-8")


class TestGuardFires:
    """A guard that cannot fail is a guard nobody should trust."""

    def test_a_provenance_without_the_url_is_reported(self) -> None:
        """This is the shape `vendor/PROVENANCE.md` shipped until v0.276.0."""
        shipped = (
            "| `mercadopago-openapi.yaml` | **não existe upstream** | — |\n\n"
            "**Não existe documento upstream.**\n"
        )
        assert SPEC_URL not in shipped

    def test_a_makefile_without_the_target_is_reported(self) -> None:
        """The Makefile carried no fetch target until v0.276.0."""
        shipped = "mercadopago-regen:\n\tuv run python scripts/regen_mercado_pago.py\n"
        assert "mercadopago-fetch:" not in shipped
