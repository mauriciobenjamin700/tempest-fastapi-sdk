"""Regenerate the zap-api client from the vendored specification.

Two steps, deliberately separate:

``--fetch``
    Read the document the running gateway serves and write it over
    ``vendor/zap-openapi.yaml``. Needs the gateway up.

default
    Read the vendored specification and rewrite ``schemas.py`` and
    ``client.py`` under ``integrations/messaging/zap/``. Offline,
    deterministic, and what
    ``tests/integrations/messaging/zap/test_generated_drift.py`` re-runs to
    prove the checked-in files were not hand-edited.

**This provider has no published specification URL.** OpenPix and Mercado
Pago are fetched from a vendor-controlled host; zap-api is ours, and it
serves its document from wherever it happens to be deployed. So the URL is
configuration, not a constant: :data:`SPEC_URL_ENV` overrides
:data:`DEFAULT_SPEC_URL`, and a refresh from a machine where the gateway is
not running fails loudly rather than vendoring an error page.

That makes the **vendored file the authority**, more so than for a provider
whose document is public: nobody else can re-derive it. It is stored as
YAML for the same reason the others are — a readable diff is the whole
point of checking it in.

Three operations answer a success body that is not JSON:

* ``GET /metrics`` answers ``text/plain`` (Prometheus exposition).
* ``GET /session/qr/image`` answers ``image/png``.
* ``GET /message/{messageId}/media`` answers ``application/octet-stream``.

Each is typed ``-> bytes`` and handed over undecoded. They used to be
typed ``-> None``, on the reasoning that the payload was reachable some
other way — the QR image duplicates ``GET /session/qr``, and metrics are
a scrape target. That reasoning never covered the third: the gateway
fetches media while the message is still in memory and WhatsApp will not
serve it again, so the endpoint is the only route to those bytes and the
generated client dropped them on the floor.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_order import export_sort_key

from tempest_fastapi_sdk.openapi import generate_integration

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root, resolved from this file rather than the cwd."""

SPEC_PATH: Path = REPO_ROOT / "vendor" / "zap-openapi.yaml"
"""The vendored specification. The authority — nobody else publishes it."""

DEFAULT_SPEC_URL: str = "http://127.0.0.1:3000/openapi.json"
"""Where a locally running gateway serves its document."""

SPEC_URL_ENV: str = "ZAP_OPENAPI_URL"
"""Environment variable overriding :data:`DEFAULT_SPEC_URL`."""

SPEC_SHA256: str = "4da5062eee171704913305a54095e5a0192e2631e72a2934a62f83e40e0e0637"
"""Hex sha256 of the vendored file, printed by ``--fetch`` to be pasted here.

Empty means unpinned. Unlike the OpenPix digest this cannot be checked
against a public URL, so it records *which bytes this checkout generated
from*, not *which bytes the provider serves*.
"""

READ_TIMEOUT_SECONDS: float = 30.0
"""Deadline for the fetch, so an unreachable gateway names itself."""

PACKAGE_DIR: Path = (
    REPO_ROOT / "tempest_fastapi_sdk" / "integrations" / "messaging" / "zap"
)
"""Where the generated modules live."""

GENERATED_FILES: tuple[str, ...] = ("schemas.py", "client.py")
"""What the generator owns here. ``__init__.py`` is hand-written."""

EXPORTS_START: str = "__all__: list[str] = ["
"""Line opening the export block this script rewrites."""

EXPORTS_END: str = "]"
"""Line closing that block."""


def spec_url() -> str:
    """Return the URL to fetch the specification from.

    Returns:
        str: :data:`SPEC_URL_ENV` when set, otherwise
        :data:`DEFAULT_SPEC_URL`.
    """
    return os.environ.get(SPEC_URL_ENV) or DEFAULT_SPEC_URL


def spec_digest() -> str:
    """Hash the vendored specification as it sits on disk.

    Returns:
        str: Hex sha256 of the file's bytes.

    Raises:
        FileNotFoundError: If the vendored specification is missing.
    """
    return hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()


def fetch_spec() -> tuple[int, str]:
    """Read the running gateway's document over the vendored one.

    Returns:
        tuple[int, str]: Bytes written and their hex sha256. The digest is
        printed so it can be pasted into :data:`SPEC_SHA256` in the same
        commit as the refreshed file.

    Raises:
        SystemExit: If the gateway cannot be reached, or answers something
            that is not an OpenAPI 3 document. Vendoring an error page
            would be worse than failing here.

    The document is served as JSON and vendored as YAML, so a refresh shows
    a readable diff instead of one long line.
    """
    url = spec_url()
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
            payload: bytes = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(
            f"could not read {url}: {exc}. Start the gateway, or point "
            f"{SPEC_URL_ENV} at a running one."
        ) from exc
    try:
        document: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{url} did not serve JSON") from exc
    if not isinstance(document, dict) or not str(
        document.get("openapi", "")
    ).startswith("3."):
        raise SystemExit(f"{url} is not an OpenAPI 3 document")

    rendered = yaml.safe_dump(
        document, sort_keys=False, allow_unicode=True, width=100
    ).encode("utf-8")
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_bytes(rendered)
    return len(rendered), hashlib.sha256(rendered).hexdigest()


def regenerate(destination: Path) -> list[Path]:
    """Generate the zap modules into ``destination``.

    Args:
        destination (Path): Directory to write :data:`GENERATED_FILES`
            into. Created if missing.

    Returns:
        list[Path]: The written files.

    Raises:
        FileNotFoundError: If the vendored specification is missing.

    Generation runs into a temporary directory first, because
    ``generate_integration`` also writes an ``__init__.py`` — and this
    package's ``__init__.py`` is hand-written, carrying the lazy
    re-exports. Copying only the generated files keeps it.
    """
    if not SPEC_PATH.exists():
        raise FileNotFoundError(f"vendored specification missing: {SPEC_PATH}")

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging:
        staging_path = Path(staging)
        generate_integration(
            str(SPEC_PATH),
            target=staging_path,
            name="zap",
            out=staging_path / "generated",
            force=True,
        )
        written: list[Path] = []
        for filename in GENERATED_FILES:
            shutil.copyfile(
                staging_path / "generated" / filename, destination / filename
            )
            written.append(destination / filename)
    return written


def _module_exports(path: Path) -> list[str]:
    """Read a module's ``__all__`` without importing it.

    Args:
        path (Path): The module to read.

    Returns:
        list[str]: The string entries of its ``__all__``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = node.target if isinstance(node, ast.AnnAssign) else None
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
        order ruff's ``RUF022`` demands.

    The generated names are listed rather than left to the ``TYPE_CHECKING``
    wildcard because a wildcard is not a re-export: basedpyright refuses
    ``from ...zap import SendTextRequest`` without the name in ``__all__``.
    """
    names = set(_hand_written_names(init_path))
    for filename in GENERATED_FILES:
        names |= set(_module_exports(generated_dir / filename))
    return sorted(names, key=export_sort_key)


def apply_exports(package_dir: Path) -> Path:
    """Rewrite the package's ``__all__`` from the generated modules.

    Args:
        package_dir (Path): The package directory holding ``__init__.py``
            and the generated modules.

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
        *(f'    "{name}",' for name in expected_exports(package_dir, init_path)),
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
        help=f"refresh {SPEC_PATH.name} from {SPEC_URL_ENV} (default: "
        f"{DEFAULT_SPEC_URL}) before regenerating",
    )
    arguments = parser.parse_args()

    if arguments.fetch:
        size, digest = fetch_spec()
        print(f"fetched {spec_url()} -> {SPEC_PATH.name} ({size} bytes)")
        print(f"  sha256 {digest}")
        if SPEC_SHA256 and digest != SPEC_SHA256:
            print(f"  changed from the pinned {SPEC_SHA256}")
        print("  paste it into SPEC_SHA256 in the same commit")

    written = regenerate(PACKAGE_DIR)
    init_path = apply_exports(PACKAGE_DIR)
    subprocess.run(
        ["ruff", "format", *(str(path) for path in [*written, init_path])],
        check=False,
        capture_output=True,
    )
    print(f"spec    {SPEC_PATH.name} sha256 {spec_digest()}")
    for path in [*written, init_path]:
        print(f"wrote   {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
