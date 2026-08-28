"""Validate the vendored Mercado Pago document against the official SDK.

Mercado Pago publishes no OpenAPI document — checked 2026-08-28,
``api.mercadopago.com/openapi{,.json}`` answer ``404`` and the
``mercadopago`` GitHub organisation has no specification repository. So
``vendor/mercadopago-openapi.yaml`` cannot be refreshed from upstream the
way the OpenPix one can, and "is it still right?" has to be answered
another way.

The provider's own SDK is that way. ``mercadopago`` on PyPI is written by
Mercado Pago and spells the URL of every operation it calls, so its
inventory is a second opinion from the same company. This script downloads
that SDK, parses the URLs out of it, and reports the two-way difference
against our document.

A difference is not automatically a defect. The SDK is a thin wrapper over
the resources most integrations use; our document covers far more, and most
of what only we carry is real. What earns attention is the other direction —
an operation the provider's own SDK calls and we do not model.

What only we carry is reported in three buckets, because "only we carry it"
is not one situation:

* **The SDK calls it.** The provider vouches for it.
* **A probe found it routed.** An unauthenticated request answers ``401``,
  ``403`` or ``400`` when the route exists and the auth or parameter gate
  replies first; ``404`` means it is not routed. That is how the two
  corrections and the three removals in :mod:`mercadopago_overlay` were
  found.
* **Nothing vouches for it.** The probe is per method *and* path, so it
  speaks only for the verb it uses, and sending a ``POST``, ``PUT`` or
  ``DELETE`` to a payment API in production to find out whether it routes
  is not an acceptable way to answer the question. Those operations carry
  the marker :data:`mercadopago_overlay.UNVERIFIED_NOTE` in their own
  generated docstring, so a consumer reading the client can tell them
  apart.

To re-probe, or to probe one suspect by hand:

.. code-block:: bash

    curl -s -o /dev/null -w "%{http_code}" https://api.mercadopago.com<path>
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tarfile
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from mercadopago_overlay import PROBE_DATE, PROBED_OPERATIONS
from mercadopago_overlay import apply as apply_overlay

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root, resolved from this file rather than the cwd."""

VENDORED_PATH: Path = REPO_ROOT / "vendor" / "mercadopago-openapi.yaml"
"""The document this repository generates the Mercado Pago client from."""

SDK_URL: str = "https://pypi.org/pypi/mercadopago/json"
"""PyPI metadata for the provider's official Python SDK.

Resolved at run time rather than pinned to a file URL so the check follows
the SDK as it is released. The version actually read is printed.
"""

USER_AGENT: str = (
    "tempest-fastapi-sdk/mercadopago_diff "
    "(+https://pypi.org/project/tempest-fastapi-sdk/)"
)
"""Identifies this script to PyPI and to the file host."""

HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
"""Keys under a path item that denote an operation."""


def _read(url: str) -> bytes:
    """Fetch a URL with an identifying ``User-Agent``.

    Args:
        url (str): The URL to read.

    Returns:
        bytes: The response body.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        payload: bytes = response.read()
    return payload


def _sdist_url() -> tuple[str, str]:
    """Locate the newest source distribution of the official SDK.

    Returns:
        tuple[str, str]: The version and the sdist URL.

    Raises:
        RuntimeError: If PyPI lists no source distribution — the wheel
            would work too, but a missing sdist means the metadata shape
            changed and the rest of this script is guessing.
    """
    import json

    metadata: dict[str, Any] = json.loads(_read(SDK_URL))
    version = str(metadata["info"]["version"])
    for entry in metadata["urls"]:
        if entry.get("packagetype") == "sdist":
            return version, str(entry["url"])
    raise RuntimeError("mercadopago published no sdist")


def _render_uri(node: ast.expr) -> str:
    """Rebuild a ``uri=`` argument as a path template.

    Args:
        node (ast.expr): The expression passed as the URI.

    Returns:
        str: The path, with every interpolated segment as ``{}``.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _render_uri(node.left) + _render_uri(node.right)
    if isinstance(node, ast.JoinedStr):
        return "".join(_render_uri(value) for value in node.values)
    return "{}"


def _calls_in(tree: ast.AST) -> Iterator[tuple[str, ast.expr | None]]:
    """Yield every ``self._<verb>(...)`` call with the expression it targets.

    Args:
        tree (ast.AST): A parsed module, or one function of it.

    Yields:
        tuple[str, ast.expr | None]: The verb, and the ``uri`` expression —
        ``None`` when the call names no URI at all.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute):
            continue
        if function.attr not in ("_get", "_post", "_put", "_delete"):
            continue
        if not (isinstance(function.value, ast.Name) and function.value.id == "self"):
            continue
        uri: ast.expr | None = None
        for keyword in node.keywords:
            if keyword.arg == "uri":
                uri = keyword.value
        if uri is None and node.args:
            uri = node.args[0]
        yield function.attr[1:].upper(), uri


def official_inventory(archive: bytes) -> tuple[set[tuple[str, str]], list[str]]:
    """Extract every ``(METHOD, path)`` the official SDK calls.

    Args:
        archive (bytes): The SDK source distribution, as downloaded.

    Returns:
        tuple[set[tuple[str, str]], list[str]]: One entry per call site with
        interpolated segments normalised to ``{}``, and the call sites whose
        URI this reader could not resolve.

    Parsed with ``ast`` rather than matched with a regular expression: the
    SDK wraps its calls across lines, and a regular expression that reads to
    the first closing parenthesis attributes the verb of one call to the URL
    of the next. That produced three phantom operations on the first pass of
    this comparison.

    A call whose ``uri`` is a local name is resolved from the assignment in
    the same function — ``disbursement_refund.py`` builds three URLs that
    way, and reading only literal arguments hid two real operations. What
    still cannot be resolved is **returned rather than dropped**: an
    inventory that silently omits a call would understate the very authority
    this comparison rests on.
    """
    found: set[tuple[str, str]] = set()
    unresolved: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            if "/mercadopago/resources/" not in member.name:
                continue
            if not member.name.endswith(".py"):
                continue
            handle = bundle.extractfile(member)
            if handle is None:
                continue
            module = ast.parse(handle.read().decode("utf-8"))
            filename = Path(member.name).name
            for scope in ast.walk(module):
                if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                assigned: dict[str, ast.expr] = {}
                for statement in ast.walk(scope):
                    if not isinstance(statement, ast.Assign):
                        continue
                    for target in statement.targets:
                        if isinstance(target, ast.Name):
                            assigned[target.id] = statement.value
                for verb, uri in _calls_in(scope):
                    resolved = (
                        assigned.get(uri.id) if isinstance(uri, ast.Name) else uri
                    )
                    path = _render_uri(resolved) if resolved is not None else "{}"
                    path = re.sub(r"\{[^}]*\}", "{}", path)
                    if path == "{}":
                        unresolved.append(f"{filename}:{scope.name} ({verb})")
                        continue
                    found.add((verb, path.rstrip("/") or "/"))
    return found, unresolved


def vendored_inventory(document: dict[str, Any]) -> set[tuple[str, str]]:
    """Collect every ``(METHOD, path)`` our document declares.

    Args:
        document (dict[str, Any]): The vendored specification.

    Returns:
        set[tuple[str, str]]: One entry per operation, normalised the same
        way as :func:`official_inventory`.
    """
    found: set[tuple[str, str]] = set()
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in item:
            if method in HTTP_METHODS:
                normalised = re.sub(r"\{[^}]*\}", "{}", str(path))
                found.add((method.upper(), normalised.rstrip("/") or "/"))
    return found


def main() -> int:
    """Report the two-way difference and how to check a suspicious path.

    Returns:
        int: Always ``0`` — this reports, it does not gate. Whether a
        difference is a defect is a judgement about one endpoint.
    """
    version, url = _sdist_url()
    official, unresolved = official_inventory(_read(url))
    document = yaml.safe_load(VENDORED_PATH.read_text(encoding="utf-8"))
    patched, report = apply_overlay(document)
    ours = vendored_inventory(patched)

    print(f"official SDK   mercadopago {version} | {len(official)} call sites")
    if unresolved:
        print(f"  unresolved URIs, not compared ({len(unresolved)}):")
        for entry in unresolved:
            print(f"    {entry}")
    print(f"vendored spec  {VENDORED_PATH.name} | {len(ours)} operations")
    for moved in report.moved_paths:
        print(f"  overlay: {moved}")

    missing = sorted(official - ours, key=lambda entry: (entry[1], entry[0]))
    print(
        f"\nthe provider's own SDK calls these, we do not model them ({len(missing)}):"
    )
    for method, path in missing:
        print(f"  {method:6} {path}")

    extra = sorted(ours - official, key=lambda entry: (entry[1], entry[0]))
    probed = [entry for entry in extra if entry in PROBED_OPERATIONS]
    unverified = [entry for entry in extra if entry not in PROBED_OPERATIONS]

    print(f"\nonly we carry these ({len(extra)}), by what vouches for them:")
    print(f"\n  probed live on {PROBE_DATE} ({len(probed)}):")
    for method, path in probed:
        print(f"    {method:6} {path:56} {PROBED_OPERATIONS[method, path]}")

    print(f"\n  nothing vouches for these ({len(unverified)}):")
    for method, path in unverified:
        print(f"    {method:6} {path}")
    print(
        "\n  The probe is per method and path, so it speaks only for the verb"
        "\n  it uses. Sending a POST, PUT or DELETE to a payment API in"
        "\n  production to find out whether it routes is not an acceptable way"
        "\n  to answer the question — these stay unverified, and each one is"
        "\n  marked in its own generated docstring."
    )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
