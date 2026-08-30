"""Password hashing helpers backed by bcrypt.

:class:`PasswordUtils` requires the ``[auth]`` extra. The dependency
is imported lazily so ``import tempest_fastapi_sdk`` keeps working when
the extra is not installed — the class raises :class:`ImportError` on
first instantiation instead. :func:`generate_password` needs nothing
but the standard library and works either way.
"""

import secrets
import string
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

        bcrypt refuses inputs over **72 UTF-8 bytes** — note bytes, not
        characters, so an emoji costs four and an accented Latin letter
        two. Validate the length before calling this from a request
        handler, or the ``ValueError`` becomes an HTTP 500 instead of a
        422; :class:`~tempest_fastapi_sdk.UserAuthService` does that via
        ``AUTH_PASSWORD_MAX_BYTES``.

        Args:
            plain (str): The plaintext password.

        Returns:
            str: The bcrypt hash encoded as a UTF-8 string, ready to
            persist in a database column.

        Raises:
            ValueError: When ``plain`` exceeds 72 UTF-8 bytes.
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


DEFAULT_GENERATED_PASSWORD_LENGTH: int = 24
"""Length :func:`generate_password` targets when the policy allows it.

Well above every default floor, and well under the 72-byte bcrypt
ceiling, so the common case needs no clamping in either direction.
"""

_CHARACTER_CLASSES: tuple[str, ...] = (
    string.ascii_lowercase,
    string.ascii_uppercase,
    string.digits,
    "!#$%&*+-=?@^_",
)
"""The four classes ``AUTH_PASSWORD_REQUIRE_COMPLEXITY`` asks for.

The special-character group is deliberately ASCII and free of quotes,
backslashes and spaces: the value travels through ``.env`` files, shell
history and log redaction on its way to a bug report, and every one of
those has a story about the quoting. Every character here is
``not c.isalnum()``, which is exactly the test
``UserAuthService._enforce_password_policy`` applies.
"""


def generate_password(
    *,
    min_length: int = 12,
    max_bytes: int = 72,
    require_complexity: bool = True,
) -> str:
    """Mint a random password that satisfies the configured policy.

    For flows where nobody types a password — the OAuth callback that
    creates an account, an admin provisioning a user — but the column
    is still ``NOT NULL``. The account owner reaches a password of
    their own through the ordinary reset flow; this one only has to be
    unguessable and *accepted*.

    **Compliance is by construction, not by retry.** Drawing from one
    flat alphabet and hoping is the defect this function exists to
    avoid: fed to ``UserAuthService._enforce_password_policy`` with
    complexity on and the default 12-character floor, 200 000 samples
    each, ``secrets.token_urlsafe(16)`` was rejected 53.01% of the
    time, ``secrets.token_urlsafe(32)`` 26.54%, and
    ``secrets.token_hex(32)`` 100% (it has neither uppercase nor a
    special character, on every single draw). A generator that passes
    three times in four fails intermittently, in production, inside a
    callback, about a password the user never typed. So one character
    is drawn from each required class first, the rest fills from their
    union, and the result is shuffled with ``secrets.SystemRandom``.

    Every character is ASCII, so the returned length is also the UTF-8
    byte count bcrypt measures — ``max_bytes`` needs no separate
    encoding check.

    Args:
        min_length (int): The policy's ``AUTH_PASSWORD_MIN_LENGTH``.
            Raised to 8 internally when ``require_complexity`` is set,
            mirroring the floor the policy itself applies.
        max_bytes (int): The policy's ``AUTH_PASSWORD_MAX_BYTES``
            (72 by default — the bcrypt limit).
        require_complexity (bool): The policy's
            ``AUTH_PASSWORD_REQUIRE_COMPLEXITY``. Only affects the
            length floor: all four classes are always represented when
            the target length has room for them, since a stronger
            password is never rejected by a laxer policy.

    Returns:
        str: A fresh random password of at least ``min_length``
        characters and at most ``max_bytes`` bytes.

    Raises:
        ValueError: When the policy cannot be satisfied at all —
            ``max_bytes`` below the effective length floor. That is a
            misconfiguration no password could survive, so it surfaces
            here rather than as a validation error later.
    """
    floor = max(min_length, 8) if require_complexity else min_length
    if max_bytes < floor:
        raise ValueError(
            f"password policy is unsatisfiable: max_bytes={max_bytes} is "
            f"below the effective minimum length of {floor}"
        )
    length = min(max(floor, DEFAULT_GENERATED_PASSWORD_LENGTH), max_bytes)
    alphabet = "".join(_CHARACTER_CLASSES)
    chars: list[str] = []
    if length >= len(_CHARACTER_CLASSES):
        chars.extend(secrets.choice(group) for group in _CHARACTER_CLASSES)
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


__all__: list[str] = [
    "DEFAULT_GENERATED_PASSWORD_LENGTH",
    "PasswordUtils",
    "generate_password",
]
