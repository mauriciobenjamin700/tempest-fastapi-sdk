"""Load an AsyncAPI 3.0 document, and refuse the ones that cannot be read.

The sibling of :mod:`tempest_fastapi_sdk.openapi.loader`. Fetching and
parsing are shared with it — the transport and the YAML/JSON handling do not
care which specification the bytes describe — and only the two checks that
are specific to AsyncAPI live here.

Both checks fail loudly rather than degrading:

* A version this generator does not read produces an empty client, which
  reads like the document had nothing in it.
* A document that does not say **whose** point of view its ``action`` fields
  are written from cannot be turned into a client at all, and the failure
  mode of guessing is a client that compiles and does the opposite.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.openapi.loader import (
    SpecError as SpecError,
)
from tempest_fastapi_sdk.openapi.loader import (
    _parse_text,
    fetch_spec_text,
)

SUPPORTED_MAJOR: str = "3."
"""Version prefix this generator reads."""

PERSPECTIVE_EXTENSION: str = "x-tempest-perspective"
"""Root key naming whose point of view ``action`` is written from."""

SERVER_PERSPECTIVE: str = "server"
"""The only perspective this generator knows how to invert."""


def check_version(document: Mapping[str, Any], *, origin: str) -> None:
    """Reject document versions the generator cannot represent.

    Args:
        document (Mapping[str, Any]): The parsed document.
        origin (str): URL or path, used in error messages.

    Raises:
        SpecError: For a missing ``asyncapi`` field, for AsyncAPI 2.x —
            which nests operations inside channels and spells the
            directions ``publish``/``subscribe``, a different document
            shape rather than a dialect — and for any other major.
    """
    version = document.get("asyncapi")
    if not isinstance(version, str):
        raise SpecError(
            f"{origin} has no `asyncapi` version field, so it is not an "
            f"AsyncAPI document. An OpenAPI document goes through "
            f"`tempest_fastapi_sdk.openapi` instead."
        )
    if version.startswith("2."):
        raise SpecError(
            f"{origin} declares AsyncAPI {version}. 2.x nests operations "
            f"inside channels and spells the directions `publish` and "
            f"`subscribe`, which is a different document shape rather than "
            f"a dialect of 3.x. Convert it first."
        )
    if not version.startswith(SUPPORTED_MAJOR):
        raise SpecError(f"{origin} declares AsyncAPI {version}; only 3.x is read.")


def check_perspective(document: Mapping[str, Any], *, origin: str) -> None:
    """Reject a document that does not say whose ``action`` it records.

    Args:
        document (Mapping[str, Any]): The parsed document.
        origin (str): URL or path, used in error messages.

    Raises:
        SpecError: When ``x-tempest-perspective`` is absent, or is not
            ``"server"``.

    AsyncAPI's ``action`` is relative to the application that published the
    document: ``receive`` means *that* application receives. A generated
    client is the other end, so it inverts every one of them — and a wrong
    sign is invisible, because the client still compiles and still
    type-checks. It just sends what it should listen for.

    Nothing in the specification records which end wrote the document, so
    this generator requires the extension rather than assuming the common
    case. A document without it is a document whose directions cannot be
    read with confidence, and refusing is cheaper than a silent inversion.
    """
    perspective = document.get(PERSPECTIVE_EXTENSION)
    if perspective is None:
        raise SpecError(
            f"{origin} does not declare `{PERSPECTIVE_EXTENSION}`. AsyncAPI's "
            f"`action` is relative to whoever published the document, and a "
            f"client has to invert it — so a document that does not say which "
            f"end wrote it cannot be generated from. Documents produced by "
            f"`tempest-express-sdk` carry it; add "
            f'`"{PERSPECTIVE_EXTENSION}": "{SERVER_PERSPECTIVE}"` at the root '
            f"of a hand-written one served by the application it describes."
        )
    if perspective != SERVER_PERSPECTIVE:
        raise SpecError(
            f"{origin} declares `{PERSPECTIVE_EXTENSION}: {perspective!r}`, and "
            f"only {SERVER_PERSPECTIVE!r} is understood. A document written "
            f"from the client's point of view would need its actions read "
            f"straight through rather than inverted, which this generator "
            f"does not do."
        )


def load_asyncapi_spec(
    source: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Load and validate an AsyncAPI document from a URL or a local path.

    Args:
        source (str): ``http(s)://`` URL, or a filesystem path.
        headers (Mapping[str, str] | None): Extra request headers, for a
            document behind authentication.
        timeout (float): Per-request timeout in seconds.

    Returns:
        dict[str, Any]: The parsed document, with internal ``$ref``
        pointers left intact.

    Raises:
        SpecError: When the file does not exist, the document does not
            parse, the version is not AsyncAPI 3.x, or the document does
            not declare whose point of view its actions are written from.
    """
    if source.startswith(("http://", "https://")):
        text = fetch_spec_text(source, headers=headers, timeout=timeout)
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise SpecError(f"No such specification file: {path}")
        text = path.read_text(encoding="utf-8")
    document = _parse_text(text, origin=source)
    check_version(document, origin=source)
    check_perspective(document, origin=source)
    return document


__all__: list[str] = [
    "PERSPECTIVE_EXTENSION",
    "SERVER_PERSPECTIVE",
    "SUPPORTED_MAJOR",
    "SpecError",
    "check_perspective",
    "check_version",
    "load_asyncapi_spec",
]
