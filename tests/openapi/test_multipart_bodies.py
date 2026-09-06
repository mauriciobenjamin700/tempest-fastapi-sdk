"""A ``multipart/form-data`` body becomes arguments, not a dropped body.

The generator modelled JSON and ``application/x-www-form-urlencoded``, and
reported multipart as unsupported. Reporting it was not enough: the method
was still emitted, with **no** body argument at all. It type-checked, it
showed up in autocomplete, and every call it could make was a `400` — the
zap gateway's four upload routes require `to` and `file`, and the generated
`upload_image` could send neither.

Multipart is a different call shape rather than a different body object:
the parts the specification marks ``format: binary`` leave on ``files``,
the scalars on ``data``. So the fields are flattened into keyword
arguments instead of being wrapped in a model that the call site would
have to take apart again.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk.openapi.generate import generate_integration

FILE_BYTES: bytes = b"\x89PNG\r\n\x1a\n\x00not-utf8\xff"
"""A part that is not valid UTF-8, so a text-only path cannot carry it."""


def _multipart_document() -> dict[str, Any]:
    """Build a specification with one multipart operation and one JSON one.

    Returns:
        dict[str, Any]: The OpenAPI document.
    """
    return {
        "openapi": "3.0.3",
        "info": {"title": "Upload API", "version": "1.0.0"},
        "servers": [{"url": "https://upload.example.com"}],
        "paths": {
            "/upload": {
                "post": {
                    "operationId": "uploadImage",
                    "summary": "Upload an image.",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {"$ref": "#/components/schemas/UploadForm"}
                            }
                        }
                    },
                    "responses": {
                        "202": {
                            "description": "queued",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/send": {
                "post": {
                    "operationId": "sendJson",
                    "summary": "Send JSON.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"to": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {"204": {"description": "done"}},
                }
            },
        },
        "components": {
            "schemas": {
                "UploadForm": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient"},
                        "file": {"type": "string", "format": "binary"},
                        "caption": {"type": "string"},
                    },
                    "required": ["to", "file"],
                }
            }
        },
    }


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate the package from :func:`_multipart_document`.

    Args:
        tmp_path_factory (pytest.TempPathFactory): pytest's factory.

    Returns:
        Path: The generated package directory.
    """
    root = tmp_path_factory.mktemp("multipart")
    spec = root / "upload.json"
    spec.write_text(json.dumps(_multipart_document()), encoding="utf-8")
    result = generate_integration(
        str(spec),
        target=root,
        name="upload",
        out=root / "pkg" / "upload_gen",
        run_format=False,
    )
    return result.written[0].parent


@pytest.fixture(scope="module")
def client_module(generated: Path) -> ModuleType:
    """Import the generated ``client`` module.

    Args:
        generated (Path): The generated package directory.

    Returns:
        ModuleType: The imported module.
    """
    root = str(generated.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    for stale in [name for name in sys.modules if name.startswith("upload_gen")]:
        del sys.modules[stale]
    return importlib.import_module("upload_gen.client")


def _arguments(source: str, method: str) -> dict[str, str]:
    """Map argument name to rendered annotation for one generated method.

    Args:
        source (str): The generated ``client.py`` source.
        method (str): The method name to look up.

    Returns:
        dict[str, str]: Keyword-only arguments and their annotations.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method:
            return {
                argument.arg: ast.unparse(argument.annotation)
                for argument in node.args.kwonlyargs
                if argument.annotation is not None
            }
    raise AssertionError(f"{method} not generated")


class TestTheFieldsBecomeArguments:
    """The form's shape is visible in the signature."""

    @pytest.fixture(scope="class")
    @classmethod
    def source(cls, generated: Path) -> str:
        """Read the generated client source.

        Args:
            generated (Path): The generated package directory.

        Returns:
            str: The source of ``client.py``.
        """
        return (generated / "client.py").read_text(encoding="utf-8")

    def test_every_field_is_an_argument(self, source: str) -> None:
        """Regression: the method took none of them.

        Args:
            source (str): The generated client source.
        """
        assert _arguments(source, "upload_image") == {
            "file": "bytes",
            "to": "str",
            "caption": "str | None",
        }

    def test_the_binary_part_is_bytes_not_a_stream(self, source: str) -> None:
        """A stream cannot survive the client's retry.

        Args:
            source (str): The generated client source.
        """
        assert _arguments(source, "upload_image")["file"] == "bytes"

    def test_no_body_argument_is_emitted(self, source: str) -> None:
        """The form is the arguments; a `body=` too would be a second spelling.

        Args:
            source (str): The generated client source.
        """
        assert "body" not in _arguments(source, "upload_image")

    def test_json_operations_keep_their_body(self, source: str) -> None:
        """The JSON path is untouched.

        Args:
            source (str): The generated client source.
        """
        assert "body" in _arguments(source, "send_json")

    def test_multipart_is_no_longer_reported_unsupported(self, source: str) -> None:
        """What the generator now models is not reported as a gap.

        Args:
            source (str): The generated client source.
        """
        assert "multipart/form-data" not in source

    def test_the_docstring_names_the_form_body(self, source: str) -> None:
        """An optional field says where it is omitted from, correctly.

        It used to say "Omitted from the query" for every location that
        was not a header. The comment wraps, so the assertion picks a
        phrase that cannot straddle a line break.

        Args:
            source (str): The generated client source.
        """
        assert "form body" in source
        assert "Omitted from the query" not in source


class TestTheRequestIsMultipart:
    """The signature is only worth what reaches the wire."""

    def test_parts_and_scalars_are_split(self, client_module: ModuleType) -> None:
        """The binary part rides in ``files``, the scalars in ``data``.

        Args:
            client_module (ModuleType): The generated client module.
        """
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["type"] = request.headers.get("content-type", "")
            seen["body"] = request.content
            return httpx.Response(202, json={"id": "abc"})

        async def run() -> Any:
            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=client_module.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                return await client_module.UploadClient(http).upload_image(
                    file=FILE_BYTES,
                    to="5511999999999",
                    caption="ola",
                )

        result = asyncio.run(run())

        assert str(seen["type"]).startswith("multipart/form-data; boundary=")
        assert FILE_BYTES in seen["body"]
        assert b"5511999999999" in seen["body"]
        assert b"ola" in seen["body"]
        assert result.id == "abc"

    def test_an_omitted_optional_field_is_absent(
        self, client_module: ModuleType
    ) -> None:
        """A `None` caption is not sent as the string "None".

        Args:
            client_module (ModuleType): The generated client module.
        """
        seen: dict[str, bytes] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return httpx.Response(202, json={"id": "abc"})

        async def run() -> None:
            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=client_module.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                await client_module.UploadClient(http).upload_image(
                    file=FILE_BYTES, to="5511999999999"
                )

        asyncio.run(run())
        assert b"caption" not in seen["body"]
