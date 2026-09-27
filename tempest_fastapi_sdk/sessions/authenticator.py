"""Credential verifiers that ``SessionAuth`` accepts in place of a user table.

``SessionAuth(user_model=...)`` looks the account up by e-mail and
bcrypt-verifies the hash. A service with no user table — an admin
panel guarded by one root credential from the environment — plugs an
:class:`SessionAuthenticator` instead: anything with an async
``authenticate(username, password)`` that returns the id the session
belongs to, or raises :class:`UnauthorizedException`.

:class:`StaticCredentialAuthenticator` is the bundled implementation
for a single fixed username / password pair.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Protocol, runtime_checkable
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import SecretStr

from tempest_fastapi_sdk.exceptions import UnauthorizedException

STATIC_CREDENTIAL_NAMESPACE: UUID = uuid5(
    NAMESPACE_URL,
    "https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/sessions/static-credential",
)
"""UUIDv5 namespace that derives a fixed credential's default ``user_id``.

:class:`StaticCredentialAuthenticator` without an explicit ``user_id``
uses ``uuid5(STATIC_CREDENTIAL_NAMESPACE, username)``, so the id is
stable across restarts and replicas and ``SessionAuth.revoke_all`` can
target it.
"""


@runtime_checkable
class SessionAuthenticator(Protocol):
    """Verifies a username / password pair and names the session owner.

    Implement it to back :class:`SessionAuth` with something other than
    a ``BaseUserModel`` table — an environment credential, an LDAP bind,
    an upstream API. Both parameters are positional-only, so an
    implementation may name them however it likes.
    """

    async def authenticate(self, username: str, password: str, /) -> UUID:
        """Return the id the new session belongs to.

        Args:
            username (str): The submitted username.
            password (str): The submitted plaintext password.

        Returns:
            UUID: The owner stored in :attr:`Session.user_id`.

        Raises:
            UnauthorizedException: When the credentials do not match.
        """
        ...


def _digest(value: str) -> bytes:
    """Hash ``value`` to a fixed-length digest for constant-time comparison.

    ``hmac.compare_digest`` returns early when the two inputs differ in
    length, and raises ``TypeError`` for a ``str`` with non-ASCII
    characters. Comparing SHA-256 digests of the UTF-8 bytes removes
    both: every comparison is 32 bytes against 32 bytes.

    Args:
        value (str): The text to hash.

    Returns:
        bytes: The 32-byte SHA-256 digest.
    """
    return hashlib.sha256(value.encode("utf-8")).digest()


class StaticCredentialAuthenticator:
    """Accept exactly one username / password pair, compared in constant time.

    Both halves are always compared, each with
    :func:`hmac.compare_digest` over a SHA-256 digest, and the two
    results are combined with ``&`` rather than ``and``. A wrong
    username therefore costs the same as a wrong password, and neither
    the timing nor the error message says which half failed.

    Only the digests are kept on the instance; the plaintext password
    is not stored.
    """

    def __init__(
        self,
        username: str,
        password: str | SecretStr,
        *,
        user_id: UUID | None = None,
    ) -> None:
        """Initialize the authenticator.

        Args:
            username (str): The accepted username.
            password (str | SecretStr): The accepted password. A
                ``SecretStr`` from a settings class is unwrapped here.
            user_id (UUID | None): Owner written on every session this
                credential opens. ``None`` derives
                ``uuid5(STATIC_CREDENTIAL_NAMESPACE, username)``.

        Raises:
            ValueError: When ``username`` or ``password`` is empty — an
                empty environment variable would otherwise accept an
                empty login form.
        """
        secret = (
            password.get_secret_value() if isinstance(password, SecretStr) else password
        )
        if not username or not secret:
            raise ValueError(
                "StaticCredentialAuthenticator needs a non-empty username and password"
            )
        self._username_digest: bytes = _digest(username)
        self._password_digest: bytes = _digest(secret)
        self.user_id: UUID = (
            user_id
            if user_id is not None
            else uuid5(STATIC_CREDENTIAL_NAMESPACE, username)
        )

    async def authenticate(self, username: str, password: str, /) -> UUID:
        """Verify the pair and return :attr:`user_id`.

        Args:
            username (str): The submitted username.
            password (str): The submitted plaintext password.

        Returns:
            UUID: :attr:`user_id`.

        Raises:
            UnauthorizedException: When either half differs, with the
                same message in both cases.
        """
        username_ok = hmac.compare_digest(_digest(username), self._username_digest)
        password_ok = hmac.compare_digest(_digest(password), self._password_digest)
        if not (username_ok & password_ok):
            raise UnauthorizedException(message="invalid username or password")
        return self.user_id


__all__: list[str] = [
    "STATIC_CREDENTIAL_NAMESPACE",
    "SessionAuthenticator",
    "StaticCredentialAuthenticator",
]
