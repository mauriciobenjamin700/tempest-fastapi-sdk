"""The loader refuses what it cannot read, instead of generating nonsense.

Two refusals matter. A version this generator does not understand yields an
empty client, which reads like the document had nothing in it. And a document
that does not say whose point of view its `action` fields record cannot be
turned into a client at all — the failure of guessing is silent, because the
wrong guess still compiles and still type-checks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.asyncapi import (
    PERSPECTIVE_EXTENSION,
    load_asyncapi_spec,
)
from tempest_fastapi_sdk.openapi.loader import SpecError


def _write(tmp_path: Path, document: dict[str, Any]) -> str:
    """Write a document to disk.

    Args:
        tmp_path (Path): pytest's temporary directory.
        document (dict[str, Any]): The document.

    Returns:
        str: The path, as the loader takes it.
    """
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


class TestVersion:
    """Only AsyncAPI 3.x is read."""

    def test_a_valid_document_loads(
        self, tmp_path: Path, document: dict[str, Any]
    ) -> None:
        """The happy path, so the refusals below mean something.

        Args:
            tmp_path (Path): pytest's temporary directory.
            document (dict[str, Any]): A valid document.
        """
        assert load_asyncapi_spec(_write(tmp_path, document))["asyncapi"] == "3.0.0"

    def test_openapi_is_refused_by_name(
        self, tmp_path: Path, document: dict[str, Any]
    ) -> None:
        """An OpenAPI document points the reader at the other package.

        Args:
            tmp_path (Path): pytest's temporary directory.
            document (dict[str, Any]): A valid document to mangle.
        """
        del document["asyncapi"]
        document["openapi"] = "3.1.0"
        with pytest.raises(SpecError, match=re.escape("tempest_fastapi_sdk.openapi")):
            load_asyncapi_spec(_write(tmp_path, document))

    def test_asyncapi_2_says_why_it_is_not_a_dialect(
        self, tmp_path: Path, document: dict[str, Any]
    ) -> None:
        """2.x nests operations in channels — a different document shape.

        Reading it as 3.x would find no root `operations` and emit a client
        with no methods, which looks like an empty specification.

        Args:
            tmp_path (Path): pytest's temporary directory.
            document (dict[str, Any]): A valid document to mangle.
        """
        document["asyncapi"] = "2.6.0"
        with pytest.raises(SpecError, match="publish"):
            load_asyncapi_spec(_write(tmp_path, document))

    def test_a_missing_file_is_named(self, tmp_path: Path) -> None:
        """The path is in the message.

        Args:
            tmp_path (Path): pytest's temporary directory.
        """
        with pytest.raises(SpecError, match="No such specification file"):
            load_asyncapi_spec(str(tmp_path / "absent.json"))


class TestPerspective:
    """A document that does not say whose `action` it records is refused."""

    def test_absent_perspective_is_refused(
        self, tmp_path: Path, document: dict[str, Any]
    ) -> None:
        """Regression: guessing would invert half the client silently.

        Args:
            tmp_path (Path): pytest's temporary directory.
            document (dict[str, Any]): A valid document to mangle.
        """
        del document[PERSPECTIVE_EXTENSION]
        with pytest.raises(SpecError, match=PERSPECTIVE_EXTENSION):
            load_asyncapi_spec(_write(tmp_path, document))

    def test_the_refusal_says_how_to_fix_it(
        self, tmp_path: Path, document: dict[str, Any]
    ) -> None:
        """A hand-written document needs one line, and the error names it.

        Args:
            tmp_path (Path): pytest's temporary directory.
            document (dict[str, Any]): A valid document to mangle.
        """
        del document[PERSPECTIVE_EXTENSION]
        with pytest.raises(SpecError, match='"server"'):
            load_asyncapi_spec(_write(tmp_path, document))

    def test_a_client_authored_document_is_refused(
        self, tmp_path: Path, document: dict[str, Any]
    ) -> None:
        """Its actions would need reading straight through, not inverting.

        Args:
            tmp_path (Path): pytest's temporary directory.
            document (dict[str, Any]): A valid document to mangle.
        """
        document[PERSPECTIVE_EXTENSION] = "client"
        with pytest.raises(SpecError, match=re.escape("only 'server' is understood")):
            load_asyncapi_spec(_write(tmp_path, document))
