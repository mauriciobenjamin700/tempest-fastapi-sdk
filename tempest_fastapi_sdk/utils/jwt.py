"""JWT encode/decode helpers backed by PyJWT.

Requires the ``[auth]`` extra. The dependency is imported lazily so
``import tempest_fastapi_sdk`` keeps working when the extra is not
installed — :class:`JWTUtils` raises :class:`ImportError` on first
instantiation instead.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

try:
    import jwt as _jwt
except ImportError:  # pragma: no cover - guarded by extras
    _jwt: Any = None  # type: ignore[no-redef]

from tempest_fastapi_sdk.exceptions.jwt import (
    ExpiredTokenException,
    InvalidTokenException,
)


class JWTUtils:
    """Encode and decode JWTs using a shared secret.

    Every token gets an ``iat`` (issued-at) and ``exp`` (expiry)
    claim populated automatically; the caller is responsible for the
    rest (``sub``, custom claims, etc.). When the helper is created
    with ``issuer=``, the ``iss`` claim is also added on encode and
    verified on decode.

    Attributes:
        algorithm (str): The JWT signing algorithm.
        default_ttl (timedelta): Default expiration applied on
            :meth:`encode` when ``ttl`` is not provided.
    """

    def __init__(
        self,
        secret: str,
        *,
        algorithm: str = "HS256",
        default_ttl: timedelta = timedelta(hours=1),
        issuer: str | None = None,
    ) -> None:
        """Initialize.

        Args:
            secret (str): The signing key (HMAC) or private key (RSA/EC).
            algorithm (str): JWT algorithm. Defaults to ``"HS256"``.
                Use ``"RS256"`` / ``"ES256"`` for asymmetric setups.
            default_ttl (timedelta): TTL applied by :meth:`encode` when
                the caller doesn't pass one. Defaults to 1 hour.
            issuer (str | None): Value for the ``iss`` claim. When set,
                :meth:`decode` rejects tokens whose ``iss`` doesn't
                match (i.e. domain-level isolation).

        Raises:
            ImportError: When the ``[auth]`` extra is not installed.
        """
        if _jwt is None:
            raise ImportError(
                "JWTUtils requires the [auth] extra. "
                "Install with `pip install tempest-fastapi-sdk[auth]`."
            )
        self._secret: str = secret
        self.algorithm: str = algorithm
        self.default_ttl: timedelta = default_ttl
        self._issuer: str | None = issuer

    def encode(
        self,
        payload: dict[str, Any],
        *,
        ttl: timedelta | None = None,
    ) -> str:
        """Encode ``payload`` as a signed JWT.

        Args:
            payload (dict[str, Any]): Claims to include. Typically
                contains a stable subject (``"sub": "<user-id>"``).
            ttl (timedelta | None): Override :attr:`default_ttl` for
                this call (e.g. shorter for password-reset tokens).

        Returns:
            str: The compact-serialized JWT.
        """
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            **payload,
            "iat": int(now.timestamp()),
            "exp": int((now + (ttl or self.default_ttl)).timestamp()),
        }
        if self._issuer is not None:
            claims.setdefault("iss", self._issuer)
        return _jwt.encode(claims, self._secret, algorithm=self.algorithm)

    def decode(self, token: str) -> dict[str, Any]:
        """Decode and verify a JWT.

        Args:
            token (str): The token to decode.

        Returns:
            dict[str, Any]: The decoded claims.

        Raises:
            ExpiredTokenException: When the ``exp`` claim is past.
            InvalidTokenException: For every other validation
                failure (bad signature, wrong issuer, missing claim,
                malformed payload, etc.).
        """
        try:
            decoded: dict[str, Any] = _jwt.decode(
                token,
                self._secret,
                algorithms=[self.algorithm],
                issuer=self._issuer,
            )
            return decoded
        except _jwt.ExpiredSignatureError as exc:
            raise ExpiredTokenException() from exc
        except _jwt.InvalidTokenError as exc:
            raise InvalidTokenException() from exc

    def decode_or_none(self, token: str) -> dict[str, Any] | None:
        """Decode and verify a JWT, returning ``None`` on failure.

        Convenience wrapper for opportunistic decoding (e.g. soft
        auth that downgrades the user to anonymous when the token
        is missing/bad).

        Args:
            token (str): The token to decode.

        Returns:
            dict[str, Any] | None: The decoded claims, or ``None``
            when the token is invalid or expired.
        """
        try:
            return self.decode(token)
        except (InvalidTokenException, ExpiredTokenException):
            return None


__all__: list[str] = [
    "JWTUtils",
]
