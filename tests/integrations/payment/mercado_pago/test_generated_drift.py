"""The checked-in Mercado Pago modules match what the generator produces.

Shipping generated code means it can be edited by hand, and hand edits to
generated code are invisible until the next regeneration silently reverts
them. This suite makes that loud: if ``schemas.py`` or ``client.py`` differs
by one byte from what ``scripts/regen_mercado_pago.py`` produces out of the
vendored specification, it fails here rather than in someone's service.

It is affordable because regeneration is deterministic — the generator
already guarantees an unchanged spec yields byte-identical output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Locate the repository root from this file.

    Returns:
        Path: The first ancestor directory holding ``pyproject.toml``.

    Walked rather than counted with ``parents[n]``: the OpenPix suite this
    mirrors moved once and a hard-coded depth broke on the move, as a
    collection error.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("repository root not found")


REPO_ROOT: Path = _repo_root()
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from regen_mercado_pago import (  # noqa: E402
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
        assert SPEC_PATH.stat().st_size > 100_000

    def test_the_vendored_document_is_the_one_the_provider_serves(self) -> None:
        """A hand edit to the vendored YAML stops being invisible.

        The same claim the OpenPix guard makes, for the same reason: the
        digest is of bytes the provider served, so it backs a statement
        about *them*. Measured 2026-08-30, this file is byte-for-byte
        `spec3.yaml` from `github.com/mercadopago/openapi` at commit
        `73bc0e49`, which is still `main`.

        Until v0.276.0 this test claimed the narrower thing — that the file
        had no upstream and nobody knew how it was assembled (issue #228).
        That was wrong: the probe behind it guessed `openapi.yaml` in a
        repository that publishes `spec3.yaml`, and the module docstring of
        `scripts/regen_mercado_pago.py` had named the origin correctly the
        whole time. `vendor/mercadopago-evidence.md` section 1 carries the
        correction.

        A failure here means the document changed. Run `make
        mercadopago-fetch` to see whether the change is theirs; if it is
        not, justify it in the overlay or in `vendor/PROVENANCE.md`, and
        update `SPEC_SHA256` in the same commit either way.
        """
        assert spec_digest() == SPEC_SHA256, (
            "vendor/mercadopago-openapi.yaml is not the document "
            f"{SPEC_SHA256[:12]}… names. Run `make mercadopago-fetch` to "
            "see whether the provider moved; a change that is not theirs "
            "is an edit of ours and needs a written justification."
        )

    def test_specification_is_not_in_the_wheel(self) -> None:
        """It is build-time input, not something a service loads.

        ``vendor/`` sits outside the package directory, so hatchling leaves
        it out of the wheel by construction. Asserted anyway: moving it
        inside the package to be "tidy" would quietly add ~255 KB of YAML
        to every install.
        """
        package_root = REPO_ROOT / "tempest_fastapi_sdk"
        assert package_root not in SPEC_PATH.parents


@pytest.fixture(scope="module")
def freshly_generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Regenerate the Mercado Pago modules into a temporary directory.

    Args:
        tmp_path_factory (pytest.TempPathFactory): pytest's factory.

    Returns:
        Path: Directory holding the freshly generated modules.

    Module-scoped so the ~1 s generation runs once for every file it is
    compared against.
    """
    destination = tmp_path_factory.mktemp("mercado_pago_regen")
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
            f"{filename} differs from `make mercadopago-regen` output — "
            f"regenerate rather than editing it by hand"
        )


class TestExportsFollowTheGeneratedModules:
    """``__all__`` lists every generated name, and stays that way.

    A name missing from ``__all__`` still imports at runtime — the lazy
    ``__getattr__`` finds it — so nothing fails until a consumer runs a
    strict type-checker and gets *"X is not exported from module"*. That is
    exactly how it shipped, and it is why the list is generated rather than
    curated.
    """

    def test_all_matches_the_generated_modules(self, freshly_generated: Path) -> None:
        """The checked-in list is what regeneration would produce.

        Args:
            freshly_generated (Path): The regenerated output.

        Order is compared too, not just membership: the block is written in
        the order ruff's ``RUF022`` demands, and a plain alphabetical sort
        fails that lint on a file the generator owns.
        """
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            __all__ as exported,
        )

        expected = expected_exports(freshly_generated, PACKAGE_DIR / "__init__.py")
        assert list(exported) == expected

    def test_every_generated_schema_is_exported(self) -> None:
        """Nothing generated is reachable only through the submodule."""
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            __all__ as exported,
        )
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import schemas

        assert not set(schemas.__all__) - set(exported)


class TestGeneratedSurface:
    """What the generated half actually carries, pinned."""

    def test_client_class_is_capitalized_the_way_we_ship_it(self) -> None:
        """``--name open_pix`` is what yields ``MercadoPagoClient``.

        Generating with ``openpix`` gives ``OpenpixClient``. Renaming after
        the fact would break the byte-for-byte check above, so the spelling
        is an input to generation, not a patch over it.
        """
        from tempest_fastapi_sdk.integrations.payment.mercado_pago.client import (
            MercadoPagoClient,
        )

        assert MercadoPagoClient.__name__ == "MercadoPagoClient"

    def test_carries_the_whole_specification(self) -> None:
        """323 schemas and 147 operations — the point of embedding it.

        The counts moved in v0.260.0, when the provider's own SDK became
        the authority on this API: seven operations it calls were added,
        three the API does not route were removed, and the three removed
        ones took a response schema each with them.
        """
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            client,
            schemas,
        )

        operations = [
            name
            for name in dir(client.MercadoPagoClient)
            if not name.startswith("_")
            and callable(getattr(client.MercadoPagoClient, name))
        ]
        assert len(schemas.__all__) == 323
        assert len(operations) == 147

    def test_there_is_only_one_server(self) -> None:
        """The spec declares a single host, so there is nothing to switch.

        Unlike OpenPix, where sandbox is a different domain, what separates
        a test charge from a real one here is the access token. This test
        pins that: the day the specification grows a second server, the
        hand-written ``DEFAULT_BASE_URL`` stops being the whole story and
        this fails.
        """
        import yaml

        document = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
        servers = [entry.get("url") for entry in document.get("servers", [])]

        assert servers == ["https://api.mercadopago.com"]


class TestLazyLoading:
    """The 323 models load on first use, not on import."""

    def test_importing_the_package_does_not_load_the_schemas(self) -> None:
        """Someone importing this for ``to_cents`` should not pay for them.

        Run in a subprocess because the schemas are almost certainly already
        imported by the rest of this suite, which would make an in-process
        check pass for the wrong reason.
        """
        import subprocess

        package = "tempest_fastapi_sdk.integrations.payment.mercado_pago"
        source = (
            f"import sys; import {package} as op; "
            f"print('{package}.schemas' in sys.modules); "
            "print(op.to_cents(19.9))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        )
        loaded, cents = completed.stdout.split()
        assert loaded == "False", "importing the package loaded the generated schemas"
        assert cents == "1990"

    def test_generated_names_resolve_from_the_package(self) -> None:
        """The whole point: no submodule path, no generator run."""
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            MercadoPagoClient,
            Payment,
        )

        assert Payment.__name__ == "Payment"
        assert MercadoPagoClient.__name__ == "MercadoPagoClient"

    def test_unknown_attribute_still_raises(self) -> None:
        """A typo must not come back as something truthy."""
        import tempest_fastapi_sdk.integrations.payment.mercado_pago as package

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = package.NoSuchSchema

    def test_dir_lists_both_halves(self) -> None:
        """Autocompletion should see the generated surface too."""
        import tempest_fastapi_sdk.integrations.payment.mercado_pago as package

        names = dir(package)
        assert "to_cents" in names
        assert "Payment" in names
        assert len(names) > 300
