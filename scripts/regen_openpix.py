"""Regenerate the vendored OpenPix schemas and client.

The generated modules are **checked in**, so a consumer gets the whole
OpenPix surface from ``pip install`` instead of running the generator in
every service. Checked-in generated code rots the moment someone edits it
by hand, so two things keep it honest: this script is the only way to
produce it, and ``tests/openpix/test_generated_drift.py`` fails when the
files on disk differ from what this script produces.

Run it after refreshing ``vendor/openpix-openapi.yaml``:

.. code-block:: bash

    make openpix-regen

``--name open_pix`` is not a typo: the generator derives the client class
from it, and that spelling is what yields ``OpenPixClient`` rather than
``OpenpixClient``. Renaming the class afterwards would break the byte-for-byte
drift check, so the name goes in at generation time.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from tempest_fastapi_sdk.openapi import generate_integration

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root, resolved from this file rather than the cwd."""

SPEC_PATH: Path = REPO_ROOT / "vendor" / "openpix-openapi.yaml"
"""The pinned OpenPix specification.

Vendored so regeneration and the drift test both work offline, and so the
diff of an upstream change is reviewable. Excluded from the wheel — nothing
at runtime reads it.
"""

PACKAGE_DIR: Path = (
    REPO_ROOT / "tempest_fastapi_sdk" / "integrations" / "payment" / "openpix"
)
"""Where the generated modules land."""

GENERATED_FILES: tuple[str, ...] = ("schemas.py", "client.py")
"""The files this script owns. ``__init__.py`` is hand-written and kept."""


def regenerate(destination: Path) -> list[Path]:
    """Generate the OpenPix modules into ``destination``.

    Args:
        destination (Path): Directory to write ``schemas.py`` and
            ``client.py`` into. Created if missing.

    Returns:
        list[Path]: The written files.

    Raises:
        FileNotFoundError: If the vendored specification is missing.

    Generation runs into a temporary directory first, because
    ``generate_integration`` also writes an ``__init__.py`` — and this
    package's ``__init__.py`` is hand-written, carrying the thin layer and
    the lazy re-exports. Copying only the two generated files keeps it.
    """
    if not SPEC_PATH.exists():
        raise FileNotFoundError(f"vendored specification missing: {SPEC_PATH}")

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging:
        staging_path = Path(staging)
        generate_integration(
            str(SPEC_PATH),
            target=staging_path,
            name="open_pix",
            out=staging_path / "generated",
            force=True,
        )
        written: list[Path] = []
        for filename in GENERATED_FILES:
            source = staging_path / "generated" / filename
            target = destination / filename
            shutil.copyfile(source, target)
            written.append(target)
    return written


def main() -> int:
    """Regenerate in place and report what was written.

    Returns:
        int: Process exit code — ``0`` on success.
    """
    for path in regenerate(PACKAGE_DIR):
        print(f"  + {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
