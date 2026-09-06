"""Regenerate the zap-api WebSocket client from its AsyncAPI document.

The sibling of :mod:`scripts.regen_zap`, for the half OpenAPI cannot
describe. The gateway serves two documents — ``/openapi.json`` for the HTTP
routes and ``/asyncapi.json`` for the socket — because no single format
covers a connection that stays open and speaks both ways.

The document is vendored for the same reason the OpenAPI one is: there is no
canonical public URL for it, it is ours and served from wherever the gateway
runs, so the checked-in file is the authority and ``SPEC_SHA256`` records
which bytes this checkout generated from.

Usage::

    make zap-ws-fetch    # refresh the vendored document from a gateway
    make zap-ws-regen    # regenerate from the vendored document, offline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
"""Repository root, resolved from this file."""

SPEC_PATH: Path = REPO_ROOT / "vendor" / "zap-asyncapi.yaml"
"""Vendored document. Committed, and the authority for a regeneration."""

DEFAULT_SPEC_URL: str = "http://127.0.0.1:3000/asyncapi.json"
"""Where a locally running gateway serves it."""

SPEC_URL_ENV: str = "ZAP_ASYNCAPI_URL"
"""Environment variable overriding :data:`DEFAULT_SPEC_URL`."""

SPEC_SHA256: str = "78b106e3ccba764f04fd7ec71ec21b073a8d4bfc1007543e24d904206b2c643d"
"""Hex sha256 of the vendored file, printed by ``--fetch`` to be pasted here."""

READ_TIMEOUT_SECONDS: float = 10.0
"""Timeout for the fetch."""

PACKAGE_DIR: Path = (
    REPO_ROOT / "tempest_fastapi_sdk" / "integrations" / "messaging" / "zap_ws"
)
"""Where the generated package lands."""

GENERATED_FILES: tuple[str, ...] = ("schemas.py", "stream.py", "__init__.py")
"""What the generator owns here."""


def spec_url() -> str:
    """Return the URL to fetch the document from.

    Returns:
        str: :data:`SPEC_URL_ENV` when set, otherwise
        :data:`DEFAULT_SPEC_URL`.
    """
    return os.environ.get(SPEC_URL_ENV) or DEFAULT_SPEC_URL


def spec_digest() -> str:
    """Hash the vendored document as it sits on disk.

    Returns:
        str: Hex sha256 of the file's bytes.

    Raises:
        FileNotFoundError: If the vendored document is missing.
    """
    return hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()


def fetch_spec() -> tuple[int, str]:
    """Read the running gateway's document over the vendored one.

    Returns:
        tuple[int, str]: Bytes written and their hex sha256, printed so it
        can be pasted into :data:`SPEC_SHA256` in the same commit.

    Raises:
        SystemExit: If the gateway cannot be reached, answers something
            that is not an AsyncAPI 3 document, or serves one that does not
            declare whose point of view its actions record. Vendoring any
            of those would fail later and further from the cause.
    """
    url = spec_url()
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
            payload: bytes = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(
            f"could not read {url}: {exc}. Start the gateway with "
            f"DOCS_ENABLED=true, or point {SPEC_URL_ENV} at a running one."
        ) from exc
    try:
        document: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{url} did not serve JSON") from exc
    if not isinstance(document, dict) or not str(
        document.get("asyncapi", "")
    ).startswith("3."):
        raise SystemExit(f"{url} is not an AsyncAPI 3 document")
    if document.get("x-tempest-perspective") != "server":
        raise SystemExit(
            f"{url} does not declare `x-tempest-perspective: server`, so its "
            f"`action` fields cannot be inverted with confidence."
        )

    rendered = yaml.safe_dump(
        document, sort_keys=False, allow_unicode=True, width=100
    ).encode("utf-8")
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_bytes(rendered)
    return len(rendered), hashlib.sha256(rendered).hexdigest()


def regenerate(destination: Path) -> list[Path]:
    """Generate the zap WebSocket modules into ``destination``.

    Args:
        destination (Path): Directory to write :data:`GENERATED_FILES`
            into. Created if missing.

    Returns:
        list[Path]: The files written.
    """
    from tempest_fastapi_sdk.asyncapi.generate import generate_stream

    result = generate_stream(
        str(SPEC_PATH), out=destination, name="zap", run_format=True
    )
    return list(result.written)


def main() -> int:
    """Run the script.

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
        written, digest = fetch_spec()
        print(f"fetched {spec_url()} -> {SPEC_PATH.name} ({written} bytes)")
        print(f"  sha256 {digest}")
        if SPEC_SHA256 and digest != SPEC_SHA256:
            print(f"  changed from the pinned {SPEC_SHA256}")
        print("  paste it into SPEC_SHA256 in the same commit")

    print(f"spec    {SPEC_PATH.name} sha256 {spec_digest()}")
    for path in regenerate(PACKAGE_DIR):
        print(f"wrote   {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
