"""A software authenticator, so the WebAuthn tests exercise real crypto.

Every alternative to this file is worse. Mocking ``fido2`` would test
that the SDK calls a mock; hand-writing the expected verification
outcome would test the test. This builds the bytes a real authenticator
produces — attestation object, authenticator data, ES256 signature — so
``Fido2Server`` verifies genuine artifacts and a mistake in how the SDK
serializes, stores or re-parses a credential shows up as a failure.

Nothing here is a security primitive for the SDK: it is a *client*, and
it deliberately supports being wrong (replaying a counter, signing for
the wrong origin) so the tests can assert that the server rejects it.
"""

from __future__ import annotations

import json
import os
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2 import cbor
from fido2.cose import ES256

USER_PRESENT: int = 0x01
"""``UP`` flag — the user interacted with the authenticator."""

USER_VERIFIED: int = 0x04
"""``UV`` flag — the authenticator verified *who* the user is."""

BACKUP_ELIGIBLE: int = 0x08
"""``BE`` flag — the credential may be synced."""

BACKUP_STATE: int = 0x10
"""``BS`` flag — the credential currently is synced."""

ATTESTED_DATA: int = 0x40
"""``AT`` flag — authenticator data carries attested credential data."""


def b64url(data: bytes) -> str:
    """Encode bytes the way WebAuthn JSON does — base64url, unpadded.

    Args:
        data (bytes): Raw bytes.

    Returns:
        str: Unpadded base64url text.
    """
    return urlsafe_b64encode(data).decode().rstrip("=")


@dataclass
class SoftwareAuthenticator:
    """An in-memory authenticator holding one ES256 credential.

    Attributes:
        rp_id (str): Relying-party ID the credential is bound to. The
            signature covers its SHA-256, which is what makes a
            credential useless on another domain.
        aaguid (bytes): 16-byte model identifier.
        credential_id (bytes): Identifier the server stores.
        counter (int): Signature counter, advanced on every assertion.
        backed_up (bool): Whether to report the credential as synced.
        user_verified (bool): Whether to set the ``UV`` flag.
    """

    rp_id: str
    aaguid: bytes = field(default_factory=lambda: bytes(range(16)))
    credential_id: bytes = field(default_factory=lambda: os.urandom(32))
    counter: int = 0
    backed_up: bool = False
    user_verified: bool = True
    _key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1()),
        repr=False,
    )

    @property
    def rp_id_hash(self) -> bytes:
        """SHA-256 of the relying-party ID.

        Returns:
            bytes: The 32-byte hash embedded in authenticator data.
        """
        return sha256(self.rp_id.encode()).digest()

    def _flags(self, *, attested: bool) -> int:
        """Assemble the authenticator-data flag byte.

        Args:
            attested (bool): Whether attested credential data follows.

        Returns:
            int: The flag byte.
        """
        flags = USER_PRESENT
        if self.user_verified:
            flags |= USER_VERIFIED
        if self.backed_up:
            flags |= BACKUP_ELIGIBLE | BACKUP_STATE
        if attested:
            flags |= ATTESTED_DATA
        return flags

    def _client_data(self, *, ceremony: str, challenge: str, origin: str) -> bytes:
        """Build the client data the browser would have produced.

        Args:
            ceremony (str): ``webauthn.create`` or ``webauthn.get``.
            challenge (str): Base64url challenge from the options.
            origin (str): Origin the ceremony ran on.

        Returns:
            bytes: The serialized client data JSON.
        """
        return json.dumps(
            {
                "type": ceremony,
                "challenge": challenge,
                "origin": origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode()

    def register(self, options: dict[str, Any], *, origin: str) -> dict[str, Any]:
        """Produce the registration response for ``options``.

        Args:
            options (dict[str, Any]): The object returned by the begin
                endpoint (with its ``publicKey`` member).
            origin (str): Origin to claim in the client data.

        Returns:
            dict[str, Any]: The WebAuthn JSON a browser would post.
        """
        public_key = options["publicKey"]
        client_data = self._client_data(
            ceremony="webauthn.create",
            challenge=public_key["challenge"],
            origin=origin,
        )
        cose = ES256.from_cryptography_key(self._key.public_key())
        attested = (
            self.aaguid
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + cbor.encode(dict(cose))
        )
        auth_data = (
            self.rp_id_hash
            + bytes([self._flags(attested=True)])
            + self.counter.to_bytes(4, "big")
            + attested
        )
        attestation = cbor.encode(
            {"fmt": "none", "attStmt": {}, "authData": auth_data},
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["usb"],
            },
            "clientExtensionResults": {},
        }

    def authenticate(
        self,
        options: dict[str, Any],
        *,
        origin: str,
        user_handle: bytes | None = None,
        advance_counter: bool = True,
    ) -> dict[str, Any]:
        """Produce the assertion for ``options``.

        Args:
            options (dict[str, Any]): The object returned by the begin
                endpoint (with its ``publicKey`` member).
            origin (str): Origin to claim in the client data.
            user_handle (bytes | None): Value to report as the user
                handle, for the discoverable flow.
            advance_counter (bool): Whether to increment the signature
                counter. ``False`` reproduces a cloned authenticator,
                which the server must reject.

        Returns:
            dict[str, Any]: The WebAuthn JSON a browser would post.
        """
        public_key = options["publicKey"]
        client_data = self._client_data(
            ceremony="webauthn.get",
            challenge=public_key["challenge"],
            origin=origin,
        )
        if advance_counter:
            self.counter += 1
        auth_data = (
            self.rp_id_hash
            + bytes([self._flags(attested=False)])
            + self.counter.to_bytes(4, "big")
        )
        signature = self._key.sign(
            auth_data + sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        response: dict[str, Any] = {
            "clientDataJSON": b64url(client_data),
            "authenticatorData": b64url(auth_data),
            "signature": b64url(signature),
        }
        if user_handle is not None:
            response["userHandle"] = b64url(user_handle)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
        }
