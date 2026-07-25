"""Tests for tempest_fastapi_sdk.openapi.loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk.openapi.loader import (
    SpecError,
    deref,
    load_spec,
    parse_header_options,
    resolve_ref,
)

MINIMAL: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Tiny", "version": "1.0"},
    "paths": {},
    "components": {"schemas": {"Thing": {"type": "object"}}},
}


class TestLoadSpecFromFile:
    """A specification on disk loads as JSON or YAML."""

    def test_json_file(self, tmp_path: Path) -> None:
        """A ``.json`` document loads with no extra dependency."""
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(MINIMAL), encoding="utf-8")
        assert load_spec(str(path))["info"]["title"] == "Tiny"

    def test_yaml_file(self, tmp_path: Path) -> None:
        """A YAML document loads through PyYAML."""
        pytest.importorskip("yaml")
        path = tmp_path / "spec.yaml"
        path.write_text(
            'openapi: "3.0.3"\ninfo:\n  title: Tiny\n  version: "1.0"\npaths: {}\n',
            encoding="utf-8",
        )
        assert load_spec(str(path))["info"]["title"] == "Tiny"

    def test_missing_file_is_reported(self, tmp_path: Path) -> None:
        """A typo in the path fails with the path in the message."""
        with pytest.raises(SpecError, match="No such specification file"):
            load_spec(str(tmp_path / "nope.json"))

    def test_garbage_is_reported(self, tmp_path: Path) -> None:
        """Text that is neither JSON nor YAML fails clearly."""
        path = tmp_path / "spec.json"
        path.write_text("{[not json: and: not: yaml", encoding="utf-8")
        with pytest.raises(SpecError, match="neither valid JSON nor valid YAML"):
            load_spec(str(path))

    def test_non_mapping_document_is_reported(self, tmp_path: Path) -> None:
        """A JSON list is valid JSON but not an OpenAPI document."""
        path = tmp_path / "spec.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(SpecError, match="expected a mapping"):
            load_spec(str(path))


class TestVersionGate:
    """Document versions the generator cannot read are refused."""

    def test_swagger_2_is_refused(self, tmp_path: Path) -> None:
        """Swagger 2.0 is a different shape, not a 3.x dialect.

        Treating it as 3.x would silently yield zero schemas and zero
        operations, which reads like the specification was empty.
        """
        path = tmp_path / "spec.json"
        path.write_text(json.dumps({"swagger": "2.0", "info": {}}), encoding="utf-8")
        with pytest.raises(SpecError, match=r"Swagger 2.0"):
            load_spec(str(path))

    def test_missing_version_is_refused(self, tmp_path: Path) -> None:
        """A document without ``openapi`` is not an OpenAPI document."""
        path = tmp_path / "spec.json"
        path.write_text(json.dumps({"info": {}}), encoding="utf-8")
        with pytest.raises(SpecError, match="no `openapi` version field"):
            load_spec(str(path))

    def test_future_major_is_refused(self, tmp_path: Path) -> None:
        """Only 3.x is read."""
        path = tmp_path / "spec.json"
        path.write_text(json.dumps({"openapi": "4.0.0"}), encoding="utf-8")
        with pytest.raises(SpecError, match=r"only 3.0 and 3.1"):
            load_spec(str(path))

    @pytest.mark.parametrize("version", ["3.0.0", "3.0.3", "3.1.0"])
    def test_supported_versions_load(self, tmp_path: Path, version: str) -> None:
        """Every 3.x patch level is accepted."""
        path = tmp_path / "spec.json"
        path.write_text(
            json.dumps({**MINIMAL, "openapi": version}),
            encoding="utf-8",
        )
        assert load_spec(str(path))["openapi"] == version


class TestLoadSpecFromUrl:
    """A specification behind HTTP loads, with headers when needed."""

    def test_url_is_fetched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The document is downloaded and parsed."""
        captured: dict[str, Any] = {}

        def fake_get(url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            return httpx.Response(200, json=MINIMAL, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", fake_get)
        document = load_spec(
            "https://api.example.com/openapi.json",
            headers={"Authorization": "Bearer t"},
        )
        assert document["info"]["title"] == "Tiny"
        assert captured["url"] == "https://api.example.com/openapi.json"
        assert captured["headers"] == {"Authorization": "Bearer t"}

    def test_http_error_names_the_header_option(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 401 points the user at ``--header`` instead of a traceback."""

        def fake_get(url: str, **kwargs: Any) -> httpx.Response:
            request = httpx.Request("GET", url)
            return httpx.Response(401, request=request)

        monkeypatch.setattr(httpx, "get", fake_get)
        with pytest.raises(SpecError, match=r"HTTP 401.*--header"):
            load_spec("https://api.example.com/openapi.json")

    def test_transport_error_is_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A connection failure surfaces as a SpecError."""

        def fake_get(url: str, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr(httpx, "get", fake_get)
        with pytest.raises(SpecError, match="Could not fetch"):
            load_spec("https://api.example.com/openapi.json")


class TestResolveRef:
    """Internal pointers resolve; external ones are refused."""

    def test_internal_ref_resolves(self) -> None:
        """A pointer into ``components.schemas`` resolves."""
        assert resolve_ref(MINIMAL, "#/components/schemas/Thing") == {"type": "object"}

    def test_escaped_tokens_resolve(self) -> None:
        """``~1`` and ``~0`` are un-escaped per RFC 6901."""
        document = {"paths": {"/a/b": {"get": {}}}}
        assert resolve_ref(document, "#/paths/~1a~1b") == {"get": {}}

    def test_external_ref_is_refused(self) -> None:
        """An external reference is refused, never skipped.

        Skipping it would produce a schema missing fields without saying
        so — worse than an error naming the bundling fix.
        """
        with pytest.raises(SpecError, match="External \\$ref is not supported"):
            resolve_ref(MINIMAL, "common.yaml#/Thing")

    def test_unresolvable_ref_is_reported(self) -> None:
        """A pointer at a missing key fails with the pointer in the message."""
        with pytest.raises(SpecError, match="does not resolve"):
            resolve_ref(MINIMAL, "#/components/schemas/Missing")

    def test_ref_at_non_schema_is_reported(self) -> None:
        """A pointer landing on a scalar is not a schema."""
        with pytest.raises(SpecError, match="points at a str"):
            resolve_ref(MINIMAL, "#/openapi")


class TestDeref:
    """``deref`` follows a chain of references."""

    def test_plain_schema_passes_through(self) -> None:
        """A schema with no ``$ref`` is returned unchanged."""
        assert deref(MINIMAL, {"type": "string"}) == {"type": "string"}

    def test_chain_is_followed(self) -> None:
        """A ``$ref`` pointing at another ``$ref`` resolves fully."""
        document = {
            "components": {
                "schemas": {
                    "A": {"$ref": "#/components/schemas/B"},
                    "B": {"type": "integer"},
                }
            }
        }
        assert deref(document, {"$ref": "#/components/schemas/A"}) == {
            "type": "integer"
        }

    def test_ref_loop_terminates(self) -> None:
        """A ``$ref`` cycle with no schema in it fails instead of hanging."""
        document = {
            "components": {
                "schemas": {
                    "A": {"$ref": "#/components/schemas/B"},
                    "B": {"$ref": "#/components/schemas/A"},
                }
            }
        }
        with pytest.raises(SpecError, match="longer than 100 hops"):
            deref(document, {"$ref": "#/components/schemas/A"})


class TestParseHeaderOptions:
    """``--header`` values become a mapping."""

    def test_pairs_are_parsed(self) -> None:
        """Name and value are split on the first colon and trimmed."""
        assert parse_header_options(
            ["Authorization: Bearer abc", "X-Tenant:  acme "]
        ) == {"Authorization": "Bearer abc", "X-Tenant": "acme"}

    def test_value_may_contain_a_colon(self) -> None:
        """Only the first colon separates; URLs in values survive."""
        assert parse_header_options(["Referer: https://x.example/a"]) == {
            "Referer": "https://x.example/a"
        }

    def test_missing_separator_is_reported(self) -> None:
        """A typo fails loudly instead of dropping the header."""
        with pytest.raises(SpecError, match="Malformed --header"):
            parse_header_options(["Authorization Bearer abc"])

    def test_empty_list_is_empty_mapping(self) -> None:
        """No ``--header`` yields no headers."""
        assert parse_header_options([]) == {}
