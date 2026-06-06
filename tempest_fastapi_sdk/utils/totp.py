"""TOTP (RFC 6238) helper backed by ``pyotp``.

Wraps the small surface of ``pyotp`` the SDK actually needs —
generating a secret, building the ``otpauth://`` provisioning URI
(scanned as a QR code by Authenticator apps), and verifying a
6-digit code with a ±N-step clock-drift window. The import of
``pyotp`` is deferred to first use so the rest of the SDK keeps
working when the ``[mfa]`` extra is not installed; the
``ImportError`` is raised with a clear hint the moment a
:class:`TOTPHelper` method is called.
"""

from __future__ import annotations


class TOTPHelper:
    """Stateless TOTP issuer + verifier.

    Construct one per app/process. Every method is pure — the
    helper holds only the ``issuer`` string shown in Authenticator
    apps next to the account name. Secrets and verification codes
    are passed in by the caller (typically loaded from the user
    row).

    Requires the ``[mfa]`` extra (``pyotp``).

    Example:

        >>> totp = TOTPHelper(issuer="My App")
        >>> secret = totp.generate_secret()                   # base32
        >>> uri = totp.provisioning_uri(secret, "ana@example.com")
        >>> # render `uri` as a QR code; user scans it
        >>> totp.verify(secret, "123456")                     # True / False
    """

    def __init__(self, *, issuer: str) -> None:
        """Initialize.

        Args:
            issuer (str): Label rendered by the Authenticator app
                alongside the account name. Use the human-readable
                product name (``"My App"``) — pyotp URL-encodes it
                for you.
        """
        self.issuer: str = issuer

    def generate_secret(self) -> str:
        """Return a fresh URL-safe base32 secret (16 chars, 80 bits).

        Returns:
            str: Base32-encoded TOTP secret ready to feed
            :meth:`provisioning_uri` and :meth:`verify`. Persist it
            on the user row (encrypted at rest is preferable —
            consider Postgres ``pgcrypto`` for the column).

        Raises:
            ImportError: When the ``[mfa]`` extra is not installed.
        """
        try:
            import pyotp
        except ImportError as exc:  # pragma: no cover - guarded by extra
            raise ImportError(
                "TOTPHelper requires the [mfa] extra. "
                "Install with `pip install tempest-fastapi-sdk[mfa]`."
            ) from exc
        return str(pyotp.random_base32())

    def provisioning_uri(self, secret: str, account_name: str) -> str:
        """Build the ``otpauth://`` URI for ``account_name``.

        The returned URI encodes the secret + issuer + account
        name; render it as a QR code (most apps support it
        directly via a `<img>` referencing a QR generator route on
        the backend).

        Args:
            secret (str): Base32 secret from :meth:`generate_secret`.
            account_name (str): Identifier shown next to ``issuer``
                in the user's Authenticator — typically the email.

        Returns:
            str: ``otpauth://totp/<issuer>:<account>?secret=…&issuer=…``.

        Raises:
            ImportError: When the ``[mfa]`` extra is not installed.
        """
        try:
            import pyotp
        except ImportError as exc:  # pragma: no cover - guarded by extra
            raise ImportError(
                "TOTPHelper requires the [mfa] extra. "
                "Install with `pip install tempest-fastapi-sdk[mfa]`."
            ) from exc
        return str(
            pyotp.TOTP(secret).provisioning_uri(
                name=account_name,
                issuer_name=self.issuer,
            )
        )

    def verify(self, secret: str, code: str, *, window: int = 1) -> bool:
        """Constant-time check that ``code`` matches ``secret`` now.

        Args:
            secret (str): Base32 secret persisted on the user row.
            code (str): 6-digit code submitted by the user.
            window (int): Tolerance in 30-second steps to absorb
                clock drift between client and server. ``1``
                (default) accepts the previous + current + next
                step (90s window total). Setting it to ``0`` is
                strict; values above ``2`` weaken security
                noticeably.

        Returns:
            bool: ``True`` when the code is valid for the current
            window, ``False`` otherwise (also when ``code`` is
            non-numeric or wrong length).

        Raises:
            ImportError: When the ``[mfa]`` extra is not installed.
        """
        try:
            import pyotp
        except ImportError as exc:  # pragma: no cover - guarded by extra
            raise ImportError(
                "TOTPHelper requires the [mfa] extra. "
                "Install with `pip install tempest-fastapi-sdk[mfa]`."
            ) from exc
        cleaned = code.strip().replace(" ", "").replace("-", "")
        if not cleaned.isdigit() or len(cleaned) != 6:
            return False
        return bool(pyotp.TOTP(secret).verify(cleaned, valid_window=window))


__all__: list[str] = ["TOTPHelper"]
