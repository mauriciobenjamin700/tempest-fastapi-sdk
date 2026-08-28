"""Compare the vendored OpenPix specification against a published one.

The overlay in :mod:`openpix_overlay` rests on the vendored document being
what the provider publishes. Nothing enforced that, and nothing measured
the distance once it drifted: on 2026-08-28 the vendored file was a whole
OpenAPI minor behind, missing 24 operations, and shipping two client
methods whose paths the provider had already fixed.

This script is how that distance is measured. It reports rather than
asserts, because the answer to a divergence is a judgement call — refresh,
or record why not — not a red build.

Run it against the published document:

.. code-block:: bash

    curl -s https://api.woovi.com/api/openapi.json -o /tmp/live.json
    uv run python scripts/openpix_diff.py /tmp/live.json

A URL works too, at the cost of needing the network.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from openpix_overlay import apply as apply_overlay

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root, resolved from this file rather than the cwd."""

VENDORED_PATH: Path = REPO_ROOT / "vendor" / "openpix-openapi.json"
"""The pinned document this repository generates from."""

PUBLISHED_URL: str = "https://api.woovi.com/api/openapi.json"
"""Where the provider publishes the current document.

Found on the "Download OpenAPI specification" link of the Redoc page at
https://developers.woovi.com/en/api-redoc. The human-facing documentation
pages render this same file and are not comparable by scraping — they are
single-page apps, and reading their HTML yields paths that do not exist.
"""

HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
"""Keys under a path item that denote an operation."""


def _load(source: str) -> dict[str, Any]:
    """Read a specification from a path or a URL.

    Args:
        source (str): Local path, or an ``http(s)`` URL.

    Returns:
        dict[str, Any]: The parsed document.
    """
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source) as response:
            text = response.read().decode("utf-8")
    else:
        text = Path(source).read_text(encoding="utf-8")
    try:
        return dict(json.loads(text))
    except json.JSONDecodeError:
        return dict(yaml.safe_load(text))


def _operations(document: dict[str, Any]) -> set[tuple[str, str]]:
    """Collect every ``(METHOD, path)`` the document declares.

    Args:
        document (dict[str, Any]): A loaded specification.

    Returns:
        set[tuple[str, str]]: One entry per operation.
    """
    found: set[tuple[str, str]] = set()
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in item:
            if method in HTTP_METHODS:
                found.add((method.upper(), str(path)))
    return found


def _numeric_names(document: dict[str, Any], name: str) -> dict[str, int]:
    """Count how a property name is typed across the whole document.

    Args:
        document (dict[str, Any]): A loaded specification.
        name (str): The property name to tally.

    Returns:
        dict[str, int]: Declared type to occurrence count.

    Walks the same constructs :func:`openpix_overlay._walk` does, so the
    numbers here and the overlay's report describe the same set.
    """
    tally: dict[str, int] = {}

    def walk(node: Any, scope: str | None) -> None:
        if isinstance(node, list):
            for entry in node:
                walk(entry, scope)
            return
        if not isinstance(node, dict):
            return
        if scope == name and node.get("type"):
            key = str(node["type"])
            tally[key] = tally.get(key, 0) + 1
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for property_name, property_schema in value.items():
                    walk(property_schema, str(property_name))
            elif key == "parameters" and isinstance(value, list):
                for parameter in value:
                    if isinstance(parameter, dict):
                        walk(
                            parameter.get("schema"),
                            str(parameter.get("name") or "") or None,
                        )
            elif key in {"items", "allOf", "anyOf", "oneOf", "not"}:
                walk(value, scope)
            elif key in {"example", "examples", "enum", "default"}:
                continue
            else:
                walk(value, None)

    walk(document, None)
    return tally


def _property_drift(
    vendored: dict[str, Any],
    published: dict[str, Any],
) -> Iterator[tuple[str, list[str], list[str]]]:
    """Yield component schemas whose property sets differ.

    Args:
        vendored (dict[str, Any]): The pinned document.
        published (dict[str, Any]): The provider's current document.

    Yields:
        tuple[str, list[str], list[str]]: Schema name, properties only the
        published document has, properties only the vendored one has.
    """
    ours = vendored.get("components", {}).get("schemas", {})
    theirs = published.get("components", {}).get("schemas", {})
    for schema_name in sorted(set(ours) & set(theirs)):
        mine = set((ours[schema_name] or {}).get("properties", {}))
        yours = set((theirs[schema_name] or {}).get("properties", {}))
        if mine != yours:
            yield schema_name, sorted(yours - mine), sorted(mine - yours)


def _header(document: dict[str, Any], label: str) -> str:
    """Render the one-line identity of a document.

    Args:
        document (dict[str, Any]): A loaded specification.
        label (str): Prefix naming which document this is.

    Returns:
        str: The rendered line.
    """
    info = document.get("info") or {}
    return (
        f"{label:10} openapi {document.get('openapi')} | "
        f"{info.get('title')!r} {info.get('version')} | "
        f"{len(_operations(document))} operations | "
        f"{len(document.get('components', {}).get('schemas', {}))} schemas"
    )


def main(argv: list[str]) -> int:
    """Report the distance between the vendored and published documents.

    Args:
        argv (list[str]): Command-line arguments; the first is an optional
            path or URL for the published document.

    Returns:
        int: Always ``0`` — this reports, it does not gate.
    """
    source = argv[0] if argv else PUBLISHED_URL
    vendored = _load(str(VENDORED_PATH))
    published = _load(source)

    print(_header(vendored, "VENDORED"))
    print(_header(published, "PUBLISHED"))
    print(f"{'source':10} {source}")

    ours, theirs = _operations(vendored), _operations(published)
    print(f"\ncommon operations: {len(ours & theirs)}")

    missing = sorted(theirs - ours, key=lambda entry: (entry[1], entry[0]))
    print(f"\npublished, absent from the vendored document ({len(missing)}):")
    for method, path in missing:
        print(f"  {method:7} {path}")

    extra = sorted(ours - theirs, key=lambda entry: (entry[1], entry[0]))
    print(f"\nvendored, absent from the published document ({len(extra)}):")
    for method, path in extra:
        print(f"  {method:7} {path}")

    drift = list(_property_drift(vendored, published))
    print(f"\ncommon schemas whose properties differ: {len(drift)}")
    for schema_name, published_only, vendored_only in drift:
        print(f"  {schema_name}: +{published_only} -{vendored_only}")

    print("\nnumeric typing, vendored vs published:")
    for name in ("value", "balance", "skip", "limit"):
        print(
            f"  {name:8} vendored={_numeric_names(vendored, name)} "
            f"published={_numeric_names(published, name)}"
        )

    _, report = apply_overlay(vendored)
    print(
        f"\noverlay on the vendored document: {report.integer_fields} retyped, "
        f"{len(report.added_properties)} properties added"
    )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main(sys.argv[1:]))
