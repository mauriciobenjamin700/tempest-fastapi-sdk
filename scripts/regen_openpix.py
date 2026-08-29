"""Regenerate the vendored OpenPix schemas and client.

The generated modules are **checked in**, so a consumer gets the whole
OpenPix surface from ``pip install`` instead of running the generator in
every service. Checked-in generated code rots the moment someone edits it
by hand, so two things keep it honest: this script is the only way to
produce it, and ``tests/integrations/payment/openpix/test_generated_drift.py``
fails when the files on disk differ from what this script produces.

Run it after refreshing ``vendor/openpix-openapi.json``:

.. code-block:: bash

    make openpix-regen

``--name open_pix`` is not a typo: the generator derives the client class
from it, and that spelling is what yields ``OpenPixClient`` rather than
``OpenpixClient``. Renaming the class afterwards would break the byte-for-byte
drift check, so the name goes in at generation time.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from export_order import export_sort_key
from openpix_overlay import OverlayReport
from openpix_overlay import apply as apply_overlay

from tempest_fastapi_sdk.openapi import generate_integration
from tempest_fastapi_sdk.openapi.loader import load_spec

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root, resolved from this file rather than the cwd."""

SPEC_PATH: Path = REPO_ROOT / "vendor" / "openpix-openapi.json"
"""The pinned OpenPix specification.

Vendored so regeneration and the drift test both work offline, and so the
diff of an upstream change is reviewable. Excluded from the wheel — nothing
at runtime reads it.

Stored as the provider serves it, JSON and unreformatted, so
:data:`SPEC_SHA256` is the digest of *their* bytes. Re-serialising it to
YAML would make the digest describe our formatter instead, and the
byte-for-byte claim unverifiable.
"""

SPEC_URL: str = "https://api.woovi.com/api/openapi.json"
"""Where the provider publishes the current document.

Linked from the "Download OpenAPI specification" control of the Redoc page
at https://developers.woovi.com/en/api-redoc. The human-facing pages
(https://developers.woovi.com/api and the API Explorer) render this same
document and are not comparable by scraping: they are single-page apps, and
reading their HTML yields paths that do not exist in the document.
"""

USER_AGENT: str = (
    "tempest-fastapi-sdk/regen_openpix (+https://pypi.org/project/tempest-fastapi-sdk/)"
)
"""Identifies this script to the provider's origin.

Not decoration: the origin answers ``HTTP 403`` to urllib's default
``Python-urllib/3.11``.
"""

SPEC_SHA256: str = "9b14fb33627f68424fd220298019703b897233c5b301c44b81cd4f0a3f83eb5e"
"""Digest of the vendored bytes, refreshed by ``--fetch``.

The overlay exists so the vendored document stays what the provider
publishes and every correction we make lives outside it. Until v0.260.0
nothing recorded or checked that: editing the vendored file by hand and
regenerating passed green, and nobody could say which publication the file
came from. ``tests/integrations/payment/openpix/test_generated_drift.py``
compares this value, so a hand edit fails loudly.

Refreshed 2026-08-28 from :data:`SPEC_URL`.
"""

PACKAGE_DIR: Path = (
    REPO_ROOT / "tempest_fastapi_sdk" / "integrations" / "payment" / "openpix"
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


def fetch_spec() -> tuple[int, str]:
    """Download the published specification over the vendored one.

    Returns:
        tuple[int, str]: Bytes written and their hex sha256. The digest is
        printed so it can be pasted into :data:`SPEC_SHA256` in the same
        commit as the refreshed file.

    Raises:
        RuntimeError: If the response is not a usable OpenAPI document —
            better a loud failure than vendoring an error page.

    The bytes are written exactly as served. The provider sends neither
    ``Last-Modified`` nor ``ETag``, so the digest and the commit date are
    the only provenance there is.

    A ``User-Agent`` is sent because the origin refuses urllib's default:
    measured 2026-08-28, ``Python-urllib/3.11`` answers ``HTTP 403``
    while the same URL under curl answers ``200``.
    """
    request = urllib.request.Request(
        SPEC_URL,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request) as response:
        payload: bytes = response.read()
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{SPEC_URL} did not serve JSON") from error
    if not str(document.get("openapi", "")).startswith("3."):
        raise RuntimeError(f"{SPEC_URL} is not an OpenAPI 3 document")
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_bytes(payload)
    return len(payload), hashlib.sha256(payload).hexdigest()


def regenerate(destination: Path) -> tuple[list[Path], OverlayReport]:
    """Generate the OpenPix modules into ``destination``.

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

    The specification is corrected by :mod:`openpix_overlay` before it
    reaches the generator, and the corrected copy is staged as JSON beside
    the output. The vendored YAML is never rewritten: it stays the
    provider's document, so refreshing it shows only *their* diff.
    """
    if not SPEC_PATH.exists():
        raise FileNotFoundError(f"vendored specification missing: {SPEC_PATH}")

    destination.mkdir(parents=True, exist_ok=True)
    document, report = apply_overlay(load_spec(str(SPEC_PATH)))
    with tempfile.TemporaryDirectory() as staging:
        staging_path = Path(staging)
        corrected = staging_path / "openpix-corrected.json"
        corrected.write_text(json.dumps(document), encoding="utf-8")
        generate_integration(
            str(corrected),
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
        order ruff's ``RUF022`` demands — see :mod:`export_order`. Plain
        ``sorted()`` fails that lint, and the generated file has to pass the
        same gate as the rest.

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="download the specification and refresh vendor/openpix-openapi.json",
    )
    arguments = parser.parse_args()

    if arguments.fetch:
        size, digest = fetch_spec()
        print(f"  + {SPEC_PATH.relative_to(REPO_ROOT)} ({size} bytes)")
        print(f"    sha256: {digest}")
        if digest != SPEC_SHA256:
            print("    SPEC_SHA256 is stale — update it and PROVENANCE.md")

    written, report = regenerate(PACKAGE_DIR)
    for path in written:
        print(f"  + {path.relative_to(REPO_ROOT)}")
    updated = apply_exports(PACKAGE_DIR, PACKAGE_DIR)
    print(f"  ~ {updated.relative_to(REPO_ROOT)} (__all__)")
    print(f"  overlay: {report.integer_fields} numeric fields retyped as integer")
    for entry in report.added_properties:
        print(f"  overlay: + {entry}")
    for entry in report.retyped_properties:
        print(f"  overlay: ! {entry} (type corrected)")
    for entry in report.retyped_pointers:
        print(f"  overlay: ! {entry} (type corrected, by pointer)")
    for entry in report.lifted_enums:
        print(f"  overlay: ~ {entry} (enum lifted to its own component)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
