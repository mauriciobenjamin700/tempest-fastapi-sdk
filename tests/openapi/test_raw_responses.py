"""A success body that is not JSON is handed over, not dropped.

The generator models ``application/json`` and nothing else. For a long
time everything else took the same exit as an empty body: the method was
typed ``-> None``, issued the request, checked the status and returned
nothing. That is not a gap in typing — it is a method that spends a round
trip and throws the payload away, and it shipped in three integrations at
once (an invoice PDF and an invoice XML in OpenPix, four report
downloads in Mercado Pago, the QR image and the Prometheus scrape in
zap).

It stopped being survivable when the zap gateway grew
``GET /message/{messageId}/media``. The earlier cases each had another
route to the same bytes, which is what the generator's own docstring
leaned on; media has none, because the gateway downloads it while the
message is in memory and WhatsApp will not serve it again.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk.openapi.generate import generate_integration

PNG_BYTES: bytes = b"\x89PNG\r\n\x1a\n\x00rawbytes\xff\xfe"
"""A payload that is not valid UTF-8, so a decoding client cannot pass."""


def _raw_document() -> dict[str, Any]:
    """Build a specification with one operation per response shape.

    Returns:
        dict[str, Any]: An OpenAPI document declaring a binary body, a
        text body, a JSON body and a bodiless ``204``.
    """
    error = {"description": "nope"}
    return {
        "openapi": "3.0.3",
        "info": {"title": "Raw API", "version": "1.0.0"},
        "servers": [{"url": "https://raw.example.com"}],
        "paths": {
            "/media/{mediaId}": {
                "get": {
                    "operationId": "getMedia",
                    "summary": "Download media.",
                    "parameters": [
                        {
                            "name": "mediaId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "the bytes",
                            "content": {
                                "application/octet-stream": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        },
                        "404": error,
                    },
                }
            },
            "/report": {
                "get": {
                    "operationId": "getReport",
                    "summary": "Download a report.",
                    "responses": {
                        "200": {
                            "description": "the rows",
                            "content": {"text/csv": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/thing": {
                "get": {
                    "operationId": "getThing",
                    "summary": "Read a thing.",
                    "responses": {
                        "200": {
                            "description": "the thing",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/ping": {
                "post": {
                    "operationId": "ping",
                    "summary": "Ping.",
                    "responses": {"204": {"description": "done"}},
                }
            },
        },
    }


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate the package from :func:`_raw_document`.

    Args:
        tmp_path_factory (pytest.TempPathFactory): pytest's factory.

    Returns:
        Path: The generated package directory.
    """
    root = tmp_path_factory.mktemp("raw")
    spec = root / "raw.json"
    spec.write_text(json.dumps(_raw_document()), encoding="utf-8")
    result = generate_integration(
        str(spec),
        target=root,
        name="raw",
        out=root / "pkg" / "raw_gen",
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
    for stale in [name for name in sys.modules if name.startswith("raw_gen")]:
        del sys.modules[stale]
    return importlib.import_module("raw_gen.client")


def _return_annotation(source: str, method: str) -> str:
    """Read one method's return annotation out of the generated source.

    Args:
        source (str): The generated ``client.py`` source.
        method (str): The method name to look up.

    Returns:
        str: The annotation as written.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method:
            assert node.returns is not None
            return ast.unparse(node.returns)
    raise AssertionError(f"{method} not generated")


class TestAnnotations:
    """The signature says what the caller actually gets back."""

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

    @pytest.mark.parametrize("method", ["get_media", "get_report"])
    def test_non_json_body_is_bytes(self, source: str, method: str) -> None:
        """Binary and text bodies alike are typed ``bytes``.

        Args:
            source (str): The generated client source.
            method (str): The method under test.
        """
        assert _return_annotation(source, method) == "bytes"

    def test_json_body_keeps_its_model(self, source: str) -> None:
        """The JSON path is untouched by the raw path.

        Args:
            source (str): The generated client source.
        """
        assert _return_annotation(source, "get_thing") == "GetThingResponse"

    def test_bodiless_success_stays_none(self, source: str) -> None:
        """A ``204`` has nothing to hand over.

        Args:
            source (str): The generated client source.
        """
        assert _return_annotation(source, "ping") == "None"

    def test_bodiless_docstring_does_not_say_json(self, source: str) -> None:
        """``204`` answers no body at all, not "no JSON body".

        The old wording was emitted for both cases at once, so the PNG
        endpoint's docstring claimed there was nothing to read while the
        prose above it named the image type.

        Args:
            source (str): The generated client source.
        """
        assert "with no JSON body" not in source
        assert "Nothing — the operation answers 204 with no body." in source

    def test_raw_docstring_names_the_media_type(self, source: str) -> None:
        """The caller is told what the bytes are.

        Args:
            source (str): The generated client source.
        """
        assert "application/octet-stream" in source
        assert "text/csv" in source

    def test_no_unsupported_marker_for_a_modelled_response(self, source: str) -> None:
        """What the generator now models is not reported as a gap.

        Args:
            source (str): The generated client source.
        """
        assert "openapi: unsupported — response of" not in source


class TestTheBytesArrive:
    """The annotation is only worth what the request returns."""

    def _call(self, client_module: ModuleType, handler: Any, method: str) -> Any:
        """Drive one generated method against a mock transport.

        Args:
            client_module (ModuleType): The generated client module.
            handler (Any): The ``httpx.MockTransport`` handler.
            method (str): The method to call.

        Returns:
            Any: Whatever the generated method returned.
        """

        async def run() -> Any:
            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=client_module.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                client = client_module.RawClient(http)
                if method == "get_media":
                    return await client.get_media("abc")
                return await getattr(client, method)()

        return asyncio.run(run())

    def test_binary_body_reaches_the_caller(self, client_module: ModuleType) -> None:
        """The exact bytes come back, undecoded.

        Regression: this returned ``None`` and the payload was discarded
        inside the generated method.

        Args:
            client_module (ModuleType): The generated client module.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=PNG_BYTES,
                headers={"Content-Type": "application/octet-stream"},
            )

        assert self._call(client_module, handler, "get_media") == PNG_BYTES

    def test_text_body_is_not_decoded(self, client_module: ModuleType) -> None:
        """A text media type is handed over as bytes too.

        Decoding would need a charset the specification does not carry,
        and a wrong guess corrupts silently. ``bytes`` is lossless — the
        caller decodes with what it knows.

        Args:
            client_module (ModuleType): The generated client module.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"id,total\n1,9.90\n",
                headers={"Content-Type": "text/csv; charset=utf-8"},
            )

        body = self._call(client_module, handler, "get_report")
        assert isinstance(body, bytes)
        assert body.decode() == "id,total\n1,9.90\n"

    def test_error_status_still_raises(self, client_module: ModuleType) -> None:
        """A raw method checks the status before handing bytes over.

        Args:
            client_module (ModuleType): The generated client module.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "gone"})

        with pytest.raises(httpx.HTTPStatusError):
            self._call(client_module, handler, "get_media")

    def test_bodiless_method_returns_none(self, client_module: ModuleType) -> None:
        """The ``204`` path did not become bytes by accident.

        Args:
            client_module (ModuleType): The generated client module.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        assert self._call(client_module, handler, "ping") is None
