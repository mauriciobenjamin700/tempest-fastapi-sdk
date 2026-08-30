"""Regenerate the vendored Mercado Pago schemas and client.

The generated modules are **checked in**, so a consumer gets the whole
Mercado Pago surface from ``pip install`` instead of running the generator
in every service. Checked-in generated code rots the moment someone edits
it by hand, so two things keep it honest: this script is the only way to
produce it, and
``tests/integrations/payment/mercado_pago/test_generated_drift.py`` fails
when the files on disk differ from what this script produces.

Run it after refreshing ``vendor/mercadopago-openapi.yaml``:

.. code-block:: bash

    make mercadopago-regen

The specification comes from Mercado Pago's own repository,
``github.com/mercadopago/openapi`` (Apache-2.0), pinned at commit
``73bc0e49`` of 2026-08-04. That repository also publishes per-product and
per-site slices; the **root** ``spec3.yaml`` is what is vendored here.
Measured on that commit: the root has 143 operations and 99 component
schemas, ``by-site/MLB`` has 142 and 74. The root covers a service that
sells outside Brazil, and the cost of the extra surface is small — the
generated schemas import in 0.76 s / 107 MB, against 0.67 s / 107 MB for the
OpenPix schemas already shipped.

``--name mercado_pago`` yields ``MercadoPagoClient``. Renaming the class
afterwards would break the byte-for-byte drift check, so the name goes in at
generation time.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml
from export_order import export_sort_key
from mercadopago_overlay import OverlayReport
from mercadopago_overlay import apply as apply_overlay

from tempest_fastapi_sdk.openapi import generate_integration

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root, resolved from this file rather than the cwd."""

SPEC_PATH: Path = REPO_ROOT / "vendor" / "mercadopago-openapi.yaml"
"""The pinned Mercado Pago specification.

Vendored so regeneration and the drift test both work offline. Excluded
from the wheel — nothing at runtime reads it.

Unlike the OpenPix document, this one has **no upstream to diff against**:
measured on 2026-08-28, ``api.mercadopago.com/openapi{,.json}`` answer
``404`` and the ``mercadopago`` GitHub organization publishes SDKs, carts
and samples but no specification repository. So "is it still right?"
cannot be answered by refetching. The second opinion here is the
provider's own SDK — ``mercadopago`` on PyPI — pinned in
``OFFICIAL_SDK_CALLS`` and compared by ``make mercadopago-diff``.
"""

SPEC_SHA256: str = "893ec14bfd912dd377626fa0b4a4e9896afc2fbfb8f67fd6293502d39d0f6d46"
"""Digest of the vendored bytes.

**This pins the file; it does not establish where the file came from.**
The distinction matters, and conflating the two is the trap this constant
could otherwise set. For OpenPix, ``SPEC_SHA256`` is the digest of bytes a
provider served, so it backs a claim about *them*. Here nobody knows how
the document was assembled — the header says "MercadoPago Developer
Experience" and nothing else (issue #228) — so this digest is a claim
about *us*: that the file has not changed since someone last justified it.

What it buys, exactly: a hand edit to the vendored document stops being
invisible. Before this, editing the YAML and regenerating passed green,
and the diff looked like a routine codegen refresh. Now the guard names
the file and the change has to be argued for in the overlay or in
``vendor/PROVENANCE.md``.

What it does not buy: any confidence that the 82 operations the official
SDK never calls describe a real API. Three of those answered ``404`` when
probed. That is issue #228's first option — finding the origin — and a
digest cannot substitute for it.

Refresh this value only alongside a written justification for the edit.
"""

PACKAGE_DIR: Path = (
    REPO_ROOT / "tempest_fastapi_sdk" / "integrations" / "payment" / "mercado_pago"
)
"""Where the generated modules land."""

GENERATED_FILES: tuple[str, ...] = ("schemas.py", "client.py")
"""The files this script owns. ``__init__.py`` is hand-written and kept."""

EXPORTS_START: str = "__all__: list[str] = ["
"""First line of the ``__all__`` block this script rewrites in place."""

EXPORTS_END: str = "]"
"""Line closing that block. The docstring under it is left alone."""


def spec_digest() -> str:
    """Hash the vendored specification as it sits on disk.

    Returns:
        str: Hex sha256 of the file's bytes.

    Raises:
        FileNotFoundError: If the vendored specification is missing.
    """
    return hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()


def regenerate(destination: Path) -> tuple[list[Path], OverlayReport]:
    """Generate the Mercado Pago modules into ``destination``.

    Args:
        destination (Path): Directory to write ``schemas.py`` and
            ``client.py`` into. Created if missing.

    Returns:
        tuple[list[Path], OverlayReport]: The written files, and what the
        overlay corrected on the way in.

    Raises:
        FileNotFoundError: If the vendored specification is missing.

    Generation runs into a temporary directory first, because
    ``generate_integration`` also writes an ``__init__.py`` — and this
    package's ``__init__.py`` is hand-written, carrying the thin layer and
    the lazy re-exports. Copying only the two generated files keeps it.

    The specification is corrected by :mod:`mercadopago_overlay` before it
    reaches the generator, and the corrected copy is staged as JSON beside
    the output. The vendored YAML is never rewritten — it is what it always
    was, and every disagreement with it stays reviewable in one file.
    """
    if not SPEC_PATH.exists():
        raise FileNotFoundError(f"vendored specification missing: {SPEC_PATH}")

    destination.mkdir(parents=True, exist_ok=True)
    document, report = apply_overlay(
        yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    )
    with tempfile.TemporaryDirectory() as staging:
        staging_path = Path(staging)
        corrected = staging_path / "mercadopago-corrected.json"
        corrected.write_text(json.dumps(document), encoding="utf-8")
        generate_integration(
            str(corrected),
            target=staging_path,
            name="mercado_pago",
            out=staging_path / "generated",
            force=True,
        )
        written: list[Path] = []
        for filename in GENERATED_FILES:
            source = staging_path / "generated" / filename
            target = destination / filename
            shutil.copyfile(source, target)
            written.append(target)
    return written, report


def _module_exports(path: Path) -> list[str]:
    """Read a module's ``__all__`` without importing it.

    Args:
        path (Path): The module to read.

    Returns:
        list[str]: The exported names, in the order written.

    Parsed rather than imported: importing ``schemas`` builds every model,
    and this runs while the file may not even be in place yet.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != "__all__":
            continue
        if isinstance(node.value, ast.List):
            return [
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
    return []


def _hand_written_names(init_path: Path) -> list[str]:
    """Read the ``_HAND_WRITTEN`` tuple from the package's ``__init__``.

    Args:
        init_path (Path): The package's ``__init__.py``.

    Returns:
        list[str]: The names the thin layer defines itself.
    """
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if not isinstance(target, ast.Name) or target.id != "_HAND_WRITTEN":
            continue
        if isinstance(node.value, ast.Tuple):
            return [
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
    return []


def expected_exports(generated_dir: Path, init_path: Path) -> list[str]:
    """Compute the ``__all__`` the package should publish.

    Args:
        generated_dir (Path): Directory holding the generated modules.
        init_path (Path): The package's ``__init__.py``, read for the
            hand-written names.

    Returns:
        list[str]: Every hand-written name plus every generated one, in the
        order ruff's ``RUF022`` demands: constants, then classes, then
        functions, each alphabetically. Plain ``sorted()`` fails that lint,
        and the generated file has to pass the same gate as the rest.

    The generated names are listed because a type-checker will not accept
    them otherwise. Measured with basedpyright against the installed wheel:
    a consumer writing
    ``from tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload``
    got ``"ChargePayload" is not exported from module`` — the
    ``TYPE_CHECKING`` wildcard makes the symbol visible but does not mark it
    re-exported. Listing the name clears it. (mypy accepted the wildcard
    either way, which is why this survived a release.)
    """
    names = set(_hand_written_names(init_path))
    for filename in GENERATED_FILES:
        names |= set(_module_exports(generated_dir / filename))
    return sorted(names, key=export_sort_key)


def apply_exports(package_dir: Path, generated_dir: Path) -> Path:
    """Rewrite the package's ``__all__`` from the generated modules.

    Args:
        package_dir (Path): The package directory holding ``__init__.py``.
        generated_dir (Path): Directory holding the generated modules the
            names come from.

    Returns:
        Path: The rewritten ``__init__.py``.

    Raises:
        RuntimeError: If the ``__all__`` block cannot be located, which
            means the hand-written file drifted from what this script
            expects and a silent no-op would ship a stale export list.
    """
    init_path = package_dir / "__init__.py"
    lines = init_path.read_text(encoding="utf-8").split("\n")
    try:
        start = lines.index(EXPORTS_START)
        end = lines.index(EXPORTS_END, start)
    except ValueError as error:
        raise RuntimeError(
            f"could not find the `__all__` block in {init_path}"
        ) from error

    block = [
        EXPORTS_START,
        *(f'    "{name}",' for name in expected_exports(generated_dir, init_path)),
        EXPORTS_END,
    ]
    init_path.write_text(
        "\n".join([*lines[:start], *block, *lines[end + 1 :]]),
        encoding="utf-8",
    )
    return init_path


def main() -> int:
    """Regenerate in place and report what was written.

    Returns:
        int: Process exit code — ``0`` on success.
    """
    written, report = regenerate(PACKAGE_DIR)
    for path in written:
        print(f"  + {path.relative_to(REPO_ROOT)}")
    updated = apply_exports(PACKAGE_DIR, PACKAGE_DIR)
    print(f"  ~ {updated.relative_to(REPO_ROOT)} (__all__)")
    for entry in report.moved_paths:
        print(f"  overlay: moved   {entry}")
    for entry in report.added_operations:
        print(f"  overlay: added   {entry}")
    for entry in report.removed_operations:
        print(f"  overlay: removed {entry}")
    if report.unverified_operations:
        print(
            f"  overlay: marked {len(report.unverified_operations)} operations "
            f"as unverified (no SDK call, no probe)"
        )
    for entry in report.collisions:
        print(f"  overlay: skipped — {entry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
