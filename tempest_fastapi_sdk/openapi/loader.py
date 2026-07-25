"""Load an OpenAPI document from a URL or a local file, and resolve ``$ref``.

Kept separate from parsing so the transport concerns (HTTP, JSON vs YAML,
authentication headers) never leak into the schema logic, and so tests can
feed a plain ``dict`` straight to the parser.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

_MAX_REF_DEPTH: int = 100
"""Guard against a ``$ref`` chain that never terminates."""


class SpecError(ValueError):
    """Raised when a document cannot be loaded or is not usable OpenAPI.

    A dedicated type so the CLI can turn it into a clean message instead
    of a traceback, while library callers can still catch it as the
    ``ValueError`` it is.
    """


def _parse_text(text: str, *, origin: str) -> dict[str, Any]:
    """Parse a specification document from text, JSON or YAML.

    JSON is attempted first because it needs no dependency and because a
    ``.json`` specification served without a content type is the common
    case. YAML is only reached when the JSON parse fails.

    Args:
        text (str): The raw document.
        origin (str): URL or path, used in error messages.

    Returns:
        dict[str, Any]: The parsed document.

    Raises:
        SpecError: When the text is neither JSON nor YAML, when the
            document is not a mapping at the top level, or when it is YAML
            and PyYAML is absent (the message names the ``[openapi]``
            extra).
    """
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as json_error:
        try:
            import yaml
        except ImportError as import_error:  # pragma: no cover - env-dependent
            raise SpecError(
                f"{origin} is not valid JSON and YAML support is unavailable. "
                f"Install the extra: pip install tempest-fastapi-sdk[openapi]. "
                f"(JSON parse error: {json_error})"
            ) from import_error
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as yaml_error:
            raise SpecError(
                f"{origin} is neither valid JSON nor valid YAML: {yaml_error}"
            ) from yaml_error
    if not isinstance(parsed, dict):
        raise SpecError(
            f"{origin} does not contain an OpenAPI document — expected a "
            f"mapping at the top level, got {type(parsed).__name__}."
        )
    return parsed


def _check_version(document: Mapping[str, Any], *, origin: str) -> None:
    """Reject document versions the generator cannot represent.

    Args:
        document (Mapping[str, Any]): The parsed document.
        origin (str): URL or path, used in error messages.

    Raises:
        SpecError: For Swagger 2.0 (a different document shape, not a
            dialect of 3.x) and for a missing/unknown ``openapi`` version.
            Failing here is deliberate: silently treating a 2.0 document
            as 3.x yields empty schemas and an empty client, which reads
            like the specification had nothing in it.
    """
    if "swagger" in document:
        raise SpecError(
            f"{origin} is Swagger 2.0, which this generator does not read. "
            f"Convert it to OpenAPI 3 first (for example with "
            f"`swagger2openapi`) and re-run."
        )
    version = document.get("openapi")
    if not isinstance(version, str):
        raise SpecError(
            f"{origin} has no `openapi` version field, so it is not an "
            f"OpenAPI 3 document."
        )
    if not version.startswith("3."):
        raise SpecError(
            f"{origin} declares OpenAPI {version}; only 3.0 and 3.1 are read."
        )


def fetch_spec_text(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> str:
    """Download a specification document over HTTP.

    Args:
        url (str): Absolute ``http://`` or ``https://`` URL.
        headers (Mapping[str, str] | None): Extra request headers — a
            specification behind authentication needs them.
        timeout (float): Request timeout in seconds.

    Returns:
        str: The response body.

    Raises:
        SpecError: On a non-2xx status or a transport failure, with the
            status and URL in the message.
    """
    try:
        response = httpx.get(
            url,
            headers=dict(headers or {}),
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SpecError(
            f"{url} returned HTTP {exc.response.status_code}. "
            f"Pass --header when the specification needs authentication."
        ) from exc
    except httpx.HTTPError as exc:
        raise SpecError(f"Could not fetch {url}: {exc}") from exc
    return response.text


def load_spec(
    source: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Load and validate a specification from a URL or a local path.

    Args:
        source (str): An ``http(s)://`` URL or a filesystem path.
        headers (Mapping[str, str] | None): Extra headers, used only for
            the URL form.
        timeout (float): HTTP timeout in seconds, used only for the URL
            form.

    Returns:
        dict[str, Any]: The parsed document, with internal ``$ref``
        pointers left intact — :func:`resolve_ref` walks them on demand so
        a cyclic schema stays representable.

    Raises:
        SpecError: When the file does not exist, the document does not
            parse, or the version is not OpenAPI 3.x.
    """
    if source.startswith(("http://", "https://")):
        text = fetch_spec_text(source, headers=headers, timeout=timeout)
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise SpecError(f"No such specification file: {path}")
        text = path.read_text(encoding="utf-8")
    document = _parse_text(text, origin=source)
    _check_version(document, origin=source)
    return document


def resolve_ref(document: Mapping[str, Any], ref: str) -> dict[str, Any]:
    """Resolve one internal JSON pointer against the document.

    Args:
        document (Mapping[str, Any]): The whole specification.
        ref (str): A ``$ref`` value, e.g.
            ``"#/components/schemas/User"``.

    Returns:
        dict[str, Any]: The referenced fragment.

    Raises:
        SpecError: For an external reference (anything not starting with
            ``#/``) and for a pointer that does not resolve. External
            refs are refused rather than skipped, because skipping one
            produces a schema that is missing fields without saying so.
    """
    if not ref.startswith("#/"):
        raise SpecError(
            f"External $ref is not supported: {ref!r}. Bundle the "
            f"specification into a single document first (for example with "
            f"`redocly bundle`) and re-run."
        )
    node: Any = document
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            raise SpecError(f"$ref does not resolve: {ref!r}")
        node = node[token]
    if not isinstance(node, dict):
        raise SpecError(
            f"$ref {ref!r} points at a {type(node).__name__}, not a schema."
        )
    return node


def deref(
    document: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Follow a chain of ``$ref`` until a concrete schema is reached.

    Args:
        document (Mapping[str, Any]): The whole specification.
        schema (Mapping[str, Any]): A schema that may be a ``$ref``.

    Returns:
        dict[str, Any]: The first fragment in the chain that is not a bare
        ``$ref``.

    Raises:
        SpecError: When the chain exceeds :data:`_MAX_REF_DEPTH`, which
            means the document contains a ``$ref`` loop with no schema in
            it.
    """
    current: dict[str, Any] = dict(schema)
    for _ in range(_MAX_REF_DEPTH):
        ref = current.get("$ref")
        if not isinstance(ref, str):
            return current
        current = dict(resolve_ref(document, ref))
    raise SpecError("$ref chain is longer than 100 hops — the document has a loop.")


def parse_header_options(raw_headers: list[str]) -> dict[str, str]:
    """Turn repeated ``--header "Name: value"`` options into a mapping.

    Args:
        raw_headers (list[str]): Values as typed on the command line.

    Returns:
        dict[str, str]: Header name to value, whitespace trimmed.

    Raises:
        SpecError: When an entry has no ``:`` separator, so a typo is
            reported instead of the header being silently dropped.
    """
    headers: dict[str, str] = {}
    for raw in raw_headers:
        name, separator, value = raw.partition(":")
        if not separator:
            raise SpecError(
                f"Malformed --header {raw!r}; expected the form 'Name: value'."
            )
        headers[name.strip()] = value.strip()
    return headers


__all__: list[str] = [
    "SpecError",
    "deref",
    "fetch_spec_text",
    "load_spec",
    "parse_header_options",
    "resolve_ref",
]
