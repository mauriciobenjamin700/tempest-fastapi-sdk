"""Tests for the WebhookSignatureVerifier helper."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    WebhookSignatureVerifier,
    register_exception_handlers,
)


class TestWebhookSignatureVerifier:
    """Validate signature computation and FastAPI integration."""

    def test_verify_passes_with_hex_signature(self) -> None:
        verifier = WebhookSignatureVerifier("topsecret")
        body = b'{"event":"ping"}'
        signature = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
        assert verifier.verify(body, signature) is True

    def test_verify_rejects_tampered_body(self) -> None:
        verifier = WebhookSignatureVerifier("topsecret")
        body = b'{"event":"ping"}'
        signature = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
        assert verifier.verify(b'{"event":"tampered"}', signature) is False

    def test_base64_encoding_round_trip(self) -> None:
        verifier = WebhookSignatureVerifier("topsecret", encoding="base64")
        body = b"hello"
        signature = base64.b64encode(
            hmac.new(b"topsecret", body, hashlib.sha256).digest(),
        ).decode()
        assert verifier.verify(body, signature) is True

    def test_prefix_is_stripped_before_compare(self) -> None:
        verifier = WebhookSignatureVerifier("topsecret", prefix="sha256=")
        body = b"hi"
        signature = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
        assert verifier.verify(body, signature) is True

    def test_invalid_encoding_raises(self) -> None:
        with pytest.raises(ValueError):
            WebhookSignatureVerifier("s", encoding="invalid")

    def test_invalid_algorithm_raises(self) -> None:
        with pytest.raises(ValueError):
            WebhookSignatureVerifier("s", algorithm="md7")

    @pytest.mark.asyncio
    async def test_dependency_passes_valid_payload(self) -> None:
        verifier = WebhookSignatureVerifier("topsecret")
        app = FastAPI()
        register_exception_handlers(app)

        @app.post("/hook")
        async def hook(body: bytes = Depends(verifier.dependency())) -> dict[str, int]:
            return {"length": len(body)}

        payload = b'{"event":"ok"}'
        signature = hmac.new(b"topsecret", payload, hashlib.sha256).hexdigest()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/hook",
                content=payload,
                headers={"X-Signature": signature},
            )
        assert response.status_code == 200
        assert response.json() == {"length": len(payload)}

    @pytest.mark.asyncio
    async def test_dependency_rejects_missing_signature(self) -> None:
        verifier = WebhookSignatureVerifier("topsecret")
        app = FastAPI()
        register_exception_handlers(app)

        @app.post("/hook")
        async def hook(body: bytes = Depends(verifier.dependency())) -> dict[str, str]:
            return {"ok": "true"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/hook", content=b"payload")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_dependency_rejects_tampered_signature(self) -> None:
        verifier = WebhookSignatureVerifier("topsecret")
        app = FastAPI()
        register_exception_handlers(app)

        @app.post("/hook")
        async def hook(body: bytes = Depends(verifier.dependency())) -> dict[str, str]:
            return {"ok": "true"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/hook",
                content=b"payload",
                headers={"X-Signature": "deadbeef"},
            )
        assert response.status_code == 401
