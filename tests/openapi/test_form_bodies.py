"""The generator reads the request body's media type instead of assuming JSON.

Both behaviours here were found by pointing the generator at Stripe's
specification, where every one of the 588 write operations declares
``application/x-www-form-urlencoded`` and nothing declares JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.openapi import generate_integration
from tempest_fastapi_sdk.openapi.parse import parse_spec


def _document() -> dict[str, Any]:
    """Build a specification with one form write and one JSON write.

    Returns:
        dict[str, Any]: The OpenAPI document.
    """
    ok_response = {
        "description": "ok",
        "content": {
            "application/json": {
                "schema": {"type": "object", "properties": {"id": {"type": "string"}}}
            }
        },
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Mixed API", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/v1/charges": {
                "post": {
                    "operationId": "createCharge",
                    "summary": "Create a charge.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "amount": {"type": "integer"},
                                        "metadata": {
                                            "type": "object",
                                            "additionalProperties": {"type": "string"},
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": ok_response},
                }
            },
            "/v1/notes": {
                "post": {
                    "operationId": "createNote",
                    "summary": "Create a note.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "responses": {"200": ok_response},
                }
            },
        },
    }


@pytest.fixture(scope="module")
def generated_client(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Generate the mixed specification once and return ``client.py``.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Session temp factory.

    Returns:
        str: The generated client source.
    """
    import json

    directory = tmp_path_factory.mktemp("mixed")
    spec_path = directory / "spec.json"
    spec_path.write_text(json.dumps(_document()))
    generate_integration(
        str(spec_path),
        target=directory,
        name="mixed",
        out=directory / "generated",
        force=True,
        run_format=False,
    )
    return (directory / "generated" / "client.py").read_text()


class TestEncodingSelection:
    def test_parser_records_the_media_type(self) -> None:
        """``body_encoding`` carries what the specification declared."""
        spec = parse_spec(_document(), client_name="mixed")
        encodings = {
            operation.path: operation.body_encoding
            for operation in spec.client.operations
        }

        assert encodings == {"/v1/charges": "form", "/v1/notes": "json"}

    def test_form_operations_send_data(self, generated_client: str) -> None:
        """A form body reaches the wire flattened, not as JSON."""
        assert "data=form_encode(payload)" in generated_client

    def test_json_operations_are_untouched(self, generated_client: str) -> None:
        """Teaching the generator form encoding did not change the JSON path."""
        assert "json=payload" in generated_client

    def test_helper_is_imported(self, generated_client: str) -> None:
        """Without the import the generated module fails on an undefined name.

        The import cannot be decided from the rendered annotations —
        ``form_encode`` appears only in call sites — so this pins that it is
        decided from the operations instead.
        """
        assert "from tempest_fastapi_sdk import HTTPClient, form_encode" in (
            generated_client
        )

    def test_json_only_spec_does_not_import_the_helper(self, tmp_path: Path) -> None:
        """A JSON-only API keeps the import block it had before."""
        import json

        document = _document()
        del document["paths"]["/v1/charges"]
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(document))
        generate_integration(
            str(spec_path),
            target=tmp_path,
            name="jsononly",
            out=tmp_path / "generated",
            force=True,
            run_format=False,
        )

        source = (tmp_path / "generated" / "client.py").read_text()

        assert "form_encode" not in source


class TestUnsupportedMediaTypes:
    def test_multipart_is_still_reported_as_a_gap(self) -> None:
        """File uploads need a different call shape, so they stay unmodelled."""
        document = _document()
        document["paths"]["/v1/charges"]["post"]["requestBody"]["content"] = {
            "multipart/form-data": {"schema": {"type": "object"}}
        }

        spec = parse_spec(document, client_name="mixed")

        assert any("multipart/form-data" in note for note in spec.unsupported)
