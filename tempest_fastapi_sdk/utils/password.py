"""Password hashing helpers backed by bcrypt.

Requires the ``[auth]`` extra. The dependency is imported lazily so
``import tempest_fastapi_sdk`` keeps working when the extra is not
installed — :class:`PasswordUtils` raises :class:`ImportError` on
first instantiation instead.
"""

from typing import Any

try:
    import bcrypt as _bcrypt
except ImportError:  # pragma: no cover - guarded by extras
    _bcrypt: Any = None  # type: ignore[no-redef]


class PasswordUtils:
    """Hash and verify passwords using bcrypt.

    Stateless utility — instantiate once and reuse across the
    application. The cost factor (``rounds``) controls how slow
    hashing is; 12 is a sensible 2026 default. Raise it when CPU
    budget allows to keep up with hardware.

    Attributes:
        rounds (int): The bcrypt cost factor.
    """

    def __init__(self, *, rounds: int = 12) -> None:
        """Initialize.

        Args:
            rounds (int): The bcrypt cost factor. Higher values make
                hashing slower and brute-force attacks harder.
                Defaults to ``12``.

        Raises:
            ImportError: When the ``[auth]`` extra is not installed.
        """
        if _bcrypt is None:
            raise ImportError(
                "PasswordUtils requires the [auth] extra. "
                "Install with `pip install tempest-fastapi-sdk[auth]`."
            )
        self.rounds: int = rounds

    def hash(self, plain: str) -> str:
        """Hash a plaintext password.

        Args:
            plain (str): The plaintext password.

        Returns:
            str: The bcrypt hash encoded as a UTF-8 string, ready to
            persist in a database column.
        """
        salt = _bcrypt.gensalt(rounds=self.rounds)
        return _bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")

    def verify(self, plain: str, hashed: str) -> bool:
        """Verify a plaintext password against an existing hash.

        Catches malformed hashes and returns ``False`` rather than
        raising, so callers can branch on the boolean without
        bcrypt-specific error handling.

        Args:
            plain (str): The plaintext password to verify.
            hashed (str): The previously stored bcrypt hash.

        Returns:
            bool: ``True`` if the password matches.
        """
        try:
            return bool(
                _bcrypt.checkpw(
                    plain.encode("utf-8"),
                    hashed.encode("utf-8"),
                )
            )
        except (ValueError, TypeError):
            return False


__all__: list[str] = [
    "PasswordUtils",
]
