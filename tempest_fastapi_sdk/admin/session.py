"""Session cookie store backing the admin login flow."""

from __future__ import annotations

import json
import logging
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

logger = logging.getLogger(__name__)


def _require_itsdangerous() -> Any:
    """Import :mod:`itsdangerous` lazily.

    Returns:
        Any: The ``itsdangerous`` module.

    Raises:
        ImportError: When the ``[admin]`` extra is not installed.
    """
    try:
        import itsdangerous
    except ImportError as exc:
        raise ImportError(
            "Admin sessions require the [admin] extra. "
            "Install with `pip install tempest-fastapi-sdk[admin]`."
        ) from exc
    return itsdangerous


@dataclass(slots=True)
class AdminSession:
    """Authenticated admin session payload stored in the cookie.

    Attributes:
        principal_id (str): Identifier returned by
            :meth:`AdminAuthBackend.principal_id` at login.
        issued_at (float): Unix timestamp when the session was issued.
        csrf_token (str): Per-session CSRF token; required on all
            POST submissions inside the admin.
        mfa_pending (bool): ``True`` after the password step when the
            principal has MFA enabled but has not yet passed the TOTP
            challenge. A pending session grants NO access to admin
            pages — it only authorizes the ``/mfa`` challenge.
    """

    principal_id: str
    issued_at: float
    csrf_token: str
    mfa_pending: bool = False

    def to_payload(self) -> dict[str, Any]:
        """Serialize the session for cookie storage.

        Returns:
            dict[str, Any]: A JSON-serializable dict.
        """
        return {
            "pid": self.principal_id,
            "iat": self.issued_at,
            "csrf": self.csrf_token,
            "mfap": self.mfa_pending,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AdminSession | None:
        """Build a session from a deserialized cookie payload.

        Args:
            payload (dict[str, Any]): The decoded cookie value.

        Returns:
            AdminSession | None: The parsed session, or ``None`` when
            the payload is malformed.
        """
        try:
            return cls(
                principal_id=str(payload["pid"]),
                issued_at=float(payload["iat"]),
                csrf_token=str(payload["csrf"]),
                mfa_pending=bool(payload.get("mfap", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None


class SessionStore(ABC):
    """Abstract storage for admin sessions.

    Implementations encode/decode the session payload to/from the
    response and request. The default
    :class:`SignedCookieSessionStore` writes a signed JSON cookie.
    """

    @abstractmethod
    def load(self, request: Request) -> AdminSession | None:
        """Return the active session for ``request`` or ``None``.

        Args:
            request (Request): The inbound request.

        Returns:
            AdminSession | None: The session, or ``None`` when absent
            or invalid.
        """

    @abstractmethod
    def save(self, response: Response, session: AdminSession) -> None:
        """Attach ``session`` to ``response``.

        Args:
            response (Response): The outbound response.
            session (AdminSession): The session to persist.
        """

    @abstractmethod
    def clear(self, response: Response) -> None:
        """Remove any active session from ``response``.

        Args:
            response (Response): The outbound response.
        """


class SignedCookieSessionStore(SessionStore):
    """Default session store using a single signed JSON cookie.

    The cookie is signed with HMAC-SHA256 via :mod:`itsdangerous` so
    tampered payloads are rejected. Cookies are scoped to ``HttpOnly``
    + ``SameSite=Lax`` and may be flagged ``Secure`` for HTTPS
    deployments; signed payloads also carry their own expiration via
    the ``max_age`` argument passed to ``loads``.

    Args:
        secret_key (str): Shared secret used to sign the cookie. MUST
            be at least 32 bytes; production deployments should source
            it from a secrets manager.
        cookie_name (str): Cookie name. Defaults to ``"tempest_admin"``.
        max_age_seconds (int): Session lifetime; cookies older than
            this are rejected by :meth:`load`.
        secure (bool): Whether to flag the cookie ``Secure``. Default
            ``True`` — disable in local-only HTTP setups.
        same_site (str): ``SameSite`` policy. Defaults to ``"lax"``.
        path (str): Cookie path. Defaults to ``"/admin"``.
    """

    def __init__(
        self,
        secret_key: str,
        *,
        cookie_name: str = "tempest_admin",
        max_age_seconds: int = 60 * 60 * 8,
        secure: bool = True,
        same_site: str = "lax",
        path: str = "/admin",
    ) -> None:
        """Initialize the store.

        Args:
            secret_key (str): The signing key.
            cookie_name (str): Cookie name.
            max_age_seconds (int): Session lifetime.
            secure (bool): Whether to set ``Secure``.
            same_site (str): ``SameSite`` policy.
            path (str): Cookie path scope.

        Raises:
            ValueError: When ``secret_key`` is empty or too short.
            ImportError: When the ``[admin]`` extra is not installed.
        """
        if not secret_key or len(secret_key) < 32:
            raise ValueError(
                "secret_key must be at least 32 bytes long",
            )
        itsdangerous = _require_itsdangerous()
        self._signer = itsdangerous.TimestampSigner(
            secret_key,
            salt="tempest-admin-session",
        )
        self.cookie_name: str = cookie_name
        self.max_age_seconds: int = max_age_seconds
        self.secure: bool = secure
        self.same_site: str = same_site
        self.path: str = path

    def load(self, request: Request) -> AdminSession | None:
        """Decode and validate the inbound session cookie.

        Args:
            request (Request): The inbound request.

        Returns:
            AdminSession | None: The session, or ``None`` when the
            cookie is absent, expired or tampered with.
        """
        raw = request.cookies.get(self.cookie_name)
        if not raw:
            return None
        itsdangerous = _require_itsdangerous()
        try:
            decoded = self._signer.unsign(
                raw,
                max_age=self.max_age_seconds,
            )
        except itsdangerous.BadSignature as exc:
            logger.debug("Rejected admin session cookie: %s", exc)
            return None
        try:
            payload = json.loads(decoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return AdminSession.from_payload(payload)

    def save(self, response: Response, session: AdminSession) -> None:
        """Sign and attach the session cookie to ``response``.

        Args:
            response (Response): The outbound response.
            session (AdminSession): The session to persist.
        """
        encoded = json.dumps(session.to_payload(), separators=(",", ":"))
        signed = self._signer.sign(encoded.encode("utf-8")).decode("ascii")
        response.set_cookie(
            self.cookie_name,
            signed,
            max_age=self.max_age_seconds,
            httponly=True,
            secure=self.secure,
            samesite=cast(Literal["lax", "strict", "none"], self.same_site),
            path=self.path,
        )

    def clear(self, response: Response) -> None:
        """Expire the session cookie.

        Args:
            response (Response): The outbound response.
        """
        response.delete_cookie(
            self.cookie_name,
            path=self.path,
        )

    def issue(
        self,
        principal_id: str,
        csrf_token: str,
        *,
        mfa_pending: bool = False,
    ) -> AdminSession:
        """Build a new session ready to :meth:`save`.

        Args:
            principal_id (str): Identifier of the authenticated user.
            csrf_token (str): CSRF token to bind to the session.
            mfa_pending (bool): Whether the session still needs to pass
                the TOTP challenge before granting access.

        Returns:
            AdminSession: The fresh session.
        """
        return AdminSession(
            principal_id=principal_id,
            issued_at=utcnow().timestamp(),
            csrf_token=csrf_token,
            mfa_pending=mfa_pending,
        )


ADMIN_LOGIN_CSRF_COOKIE: str = "tempest_admin_login_csrf"
"""Cookie carrying the signed pre-login CSRF token of ``/login``."""

ADMIN_LOGIN_CSRF_MAX_AGE_SECONDS: int = 60 * 60
"""How long a login form stays submittable after it was rendered.

An hour covers a form left open in a tab while the operator looks up the
password; an older submission is refused and the form re-rendered with a
fresh token, at the cost of one retry.
"""

ADMIN_LOGIN_CSRF_SALT: str = "tempest-admin-login-csrf"
"""``itsdangerous`` salt, distinct from the session cookie's.

A signed session cookie must never verify as a login token and vice
versa, even though both are signed with the same ``secret_key``.
"""


class AdminLoginCSRF:
    """CSRF token for the login form, which runs before any session exists.

    Every other admin form checks the token stored in the session. The
    login form cannot: there is no session yet, and without a token a page
    on another site could make an operator's browser post the
    **attacker's** credentials to ``/login`` — the response sets the admin
    cookie, and the operator ends up working in the attacker's account
    (login CSRF). ``SameSite=Lax`` does not prevent it: it restricts which
    cookies a cross-site POST **sends**, not the cookie its response sets.

    The token is random, signed with a timestamp into a short-lived cookie
    scoped to the admin prefix, and repeated in a hidden form field; the
    submit compares the two in constant time. A cross-site page cannot
    read the cookie, so it cannot fill the field.

    Independent of the configured :class:`SessionStore`, so a deployment
    that swaps the store keeps the protection.
    """

    def __init__(
        self,
        secret_key: str,
        *,
        secure: bool = True,
        path: str = "/admin",
        max_age_seconds: int = ADMIN_LOGIN_CSRF_MAX_AGE_SECONDS,
    ) -> None:
        """Initialize the signer.

        Args:
            secret_key (str): The admin signing key; at least 32 bytes,
                the rule :class:`SignedCookieSessionStore` applies.
            secure (bool): Whether to flag the cookie ``Secure``.
            path (str): Cookie path scope — the admin prefix.
            max_age_seconds (int): How long a rendered form stays valid.

        Raises:
            ValueError: When ``secret_key`` is empty or too short.
            ImportError: When the ``[admin]`` extra is not installed.
        """
        if not secret_key or len(secret_key) < 32:
            raise ValueError(
                "secret_key must be at least 32 bytes long",
            )
        itsdangerous = _require_itsdangerous()
        self._signer = itsdangerous.TimestampSigner(
            secret_key,
            salt=ADMIN_LOGIN_CSRF_SALT,
        )
        self.secure: bool = secure
        self.path: str = path
        self.max_age_seconds: int = max_age_seconds

    def current(self, request: Request) -> str | None:
        """Return the token the request's cookie carries, if still valid.

        Args:
            request (Request): The inbound request.

        Returns:
            str | None: The token, or ``None`` when the cookie is absent,
            expired or tampered with.
        """
        raw = request.cookies.get(ADMIN_LOGIN_CSRF_COOKIE)
        if not raw:
            return None
        itsdangerous = _require_itsdangerous()
        try:
            return str(
                self._signer.unsign(raw, max_age=self.max_age_seconds).decode(
                    "ascii",
                ),
            )
        except (itsdangerous.BadSignature, UnicodeDecodeError) as exc:
            logger.debug("Rejected admin login CSRF cookie: %s", exc)
            return None

    def issue(self, request: Request) -> str:
        """Return the token to embed in the form being rendered.

        Reuses the request's valid token, so two tabs on the login page do
        not invalidate each other; mints a new one otherwise.

        Args:
            request (Request): The inbound request.

        Returns:
            str: The token for the hidden field.
        """
        return self.current(request) or secrets.token_urlsafe(32)

    def attach(self, response: Response, token: str) -> None:
        """Set the signed cookie carrying ``token`` on ``response``.

        Re-signing on every render restarts the expiry, so a form that
        was just re-rendered after an error is good for the full window.

        Args:
            response (Response): The outbound response.
            token (str): The token embedded in the form.
        """
        signed = self._signer.sign(token.encode("ascii")).decode("ascii")
        response.set_cookie(
            ADMIN_LOGIN_CSRF_COOKIE,
            signed,
            max_age=self.max_age_seconds,
            httponly=True,
            secure=self.secure,
            samesite="lax",
            path=self.path,
        )

    def verify(self, request: Request, submitted: str) -> bool:
        """Check the submitted field against the cookie, in constant time.

        Args:
            request (Request): The inbound request.
            submitted (str): The ``csrf_token`` form field.

        Returns:
            bool: ``True`` only when the cookie is valid and matches.
        """
        expected = self.current(request)
        if expected is None or not submitted:
            return False
        return secrets.compare_digest(expected, submitted)

    def clear(self, response: Response) -> None:
        """Drop the pre-login cookie once a session exists.

        Args:
            response (Response): The outbound response.
        """
        response.delete_cookie(ADMIN_LOGIN_CSRF_COOKIE, path=self.path)


__all__: list[str] = [
    "ADMIN_LOGIN_CSRF_COOKIE",
    "ADMIN_LOGIN_CSRF_MAX_AGE_SECONDS",
    "ADMIN_LOGIN_CSRF_SALT",
    "AdminLoginCSRF",
    "AdminSession",
    "SessionStore",
    "SignedCookieSessionStore",
]
