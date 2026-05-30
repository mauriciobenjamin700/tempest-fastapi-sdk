"""Tests for tempest_fastapi_sdk.api.webhooks.RSAWebhookSignatureVerifier."""

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import RSAWebhookSignatureVerifier


def _keypair() -> tuple[rsa.RSAPrivateKey, str]:
    """Generate an RSA keypair, returning (private_key, public_pem)."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private, public_pem


def _sign(private: rsa.RSAPrivateKey, body: bytes) -> str:
    signature = private.sign(body, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


class TestVerify:
    def test_valid_signature_verifies(self) -> None:
        private, public_pem = _keypair()
        verifier = RSAWebhookSignatureVerifier(public_pem)
        body = b'{"event":"OPENPIX:CHARGE_COMPLETED"}'
        assert verifier.verify(body, _sign(private, body)) is True

    def test_tampered_body_fails(self) -> None:
        private, public_pem = _keypair()
        verifier = RSAWebhookSignatureVerifier(public_pem)
        signature = _sign(private, b"original")
        assert verifier.verify(b"tampered", signature) is False

    def test_wrong_key_fails(self) -> None:
        signer, _ = _keypair()
        _, other_public = _keypair()
        verifier = RSAWebhookSignatureVerifier(other_public)
        body = b"payload"
        assert verifier.verify(body, _sign(signer, body)) is False

    def test_missing_signature_fails(self) -> None:
        _, public_pem = _keypair()
        verifier = RSAWebhookSignatureVerifier(public_pem)
        assert verifier.verify(b"x", "") is False

    def test_malformed_signature_fails(self) -> None:
        _, public_pem = _keypair()
        verifier = RSAWebhookSignatureVerifier(public_pem)
        assert verifier.verify(b"x", "!!!not-base64!!!") is False

    def test_rejects_bad_algorithm(self) -> None:
        _, public_pem = _keypair()
        with pytest.raises(ValueError):
            RSAWebhookSignatureVerifier(public_pem, algorithm="md5")


class TestDependency:
    def test_dependency_accepts_valid_and_rejects_invalid(self) -> None:
        private, public_pem = _keypair()
        verifier = RSAWebhookSignatureVerifier(
            public_pem,
            header_name="x-webhook-signature",
        )
        app = FastAPI()

        @app.post("/webhook")
        async def _hook(body: bytes = Depends(verifier.dependency())) -> dict:
            return {"len": len(body)}

        client = TestClient(app)
        body = b'{"ok":true}'
        sig = _sign(private, body)

        ok = client.post(
            "/webhook",
            content=body,
            headers={"x-webhook-signature": sig},
        )
        assert ok.status_code == 200
        assert ok.json() == {"len": len(body)}

        bad = client.post(
            "/webhook",
            content=body,
            headers={"x-webhook-signature": "wrong"},
        )
        assert bad.status_code == 401
