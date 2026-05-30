"""Single-use opaque tokens hashed at rest.

For flows where a secret is emailed/SMS'd to a user and later presented
back — password reset, email verification, magic links, invitations,
raw API keys. The plaintext is shown to the user exactly once; only its
SHA-256 digest is persisted, so a database leak does not expose usable
tokens. Lookups hash the incoming plaintext and compare digests in
constant time.

Pure standard library — no extras required.

Example:
    plaintext, token_hash = generate_opaque_token()
    # store token_hash + an expiry; email `plaintext` to the user
    # ...later, on redemption:
    if verify_opaque_token(submitted, stored_hash):
        ...  # consume (mark used) and proceed
"""

import hashlib
import hmac
import secrets

_DEFAULT_NBYTES: int = 32


def hash_opaque_token(plaintext: str) -> str:
    """Return the SHA-256 hex digest of a token's plaintext.

    Args:
        plaintext (str): The token value to hash.

    Returns:
        str: 64-character lowercase hex digest.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_opaque_token(nbytes: int = _DEFAULT_NBYTES) -> tuple[str, str]:
    """Generate a cryptographically random token and its digest.

    Args:
        nbytes (int): Entropy in bytes for the URL-safe token. Defaults
            to 32 (~43 characters), well above brute-force reach.

    Returns:
        tuple[str, str]: ``(plaintext, token_hash)``. Show ``plaintext``
            to the user once; persist only ``token_hash``.
    """
    plaintext = secrets.token_urlsafe(nbytes)
    return plaintext, hash_opaque_token(plaintext)


def verify_opaque_token(plaintext: str, token_hash: str) -> bool:
    """Constant-time check that ``plaintext`` hashes to ``token_hash``.

    Args:
        plaintext (str): The token submitted by the caller.
        token_hash (str): The stored SHA-256 hex digest.

    Returns:
        bool: ``True`` when the hashes match, ``False`` otherwise.
    """
    return hmac.compare_digest(hash_opaque_token(plaintext), token_hash)


__all__: list[str] = [
    "generate_opaque_token",
    "hash_opaque_token",
    "verify_opaque_token",
]
